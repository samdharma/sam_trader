"""RejectionMonitorActor — per-instrument rejection circuit breaker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.identifiers import InstrumentId, StrategyId


@dataclass(frozen=True)
class StrategyHaltRequest:
    """Event emitted when a strategy should halt trading for an instrument.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument that triggered the halt.
    strategy_id : StrategyId
        The strategy that should halt.
    reason : str
        The rejection reason that caused the halt.
    count : int
        Number of consecutive rejections observed.

    """

    instrument_id: InstrumentId
    strategy_id: StrategyId
    reason: str
    count: int


class RejectionMonitorActorConfig(ActorConfig, frozen=True):
    """Configuration for the RejectionMonitorActor.

    Parameters
    ----------
    max_consecutive : int, default 3
        Number of identical consecutive rejections before emitting a halt.
    cooldown_seconds : int, default 900
        Seconds before a rejection streak resets and the strategy may retry.

    """

    max_consecutive: int = 3
    cooldown_seconds: int = 900


class RejectionMonitorActor(Actor):
    """Actor that subscribes to ``OrderRejected`` events and emits
    ``StrategyHaltRequest`` when a per-instrument rejection streak exceeds the
    configured threshold.

    Tracks rejections per ``(instrument_id, strategy_id, reason)``.  After
    ``max_consecutive`` identical rejections a ``StrategyHaltRequest`` is
    published on the message bus.  A cooldown period allows periodic retry.

    Parameters
    ----------
    config : RejectionMonitorActorConfig
        Actor configuration.

    """

    def __init__(self, config: RejectionMonitorActorConfig):
        super().__init__(config)
        self._counters: dict[
            tuple[InstrumentId, StrategyId, str],
            dict[str, Any],
        ] = {}
        self._topic = "events.order.*"
        self._halt_topic = "StrategyHaltRequest"

    def on_start(self) -> None:
        """Subscribe to all order events on the message bus."""
        self.msgbus.subscribe(topic=self._topic, handler=self._handle_order_event)
        self.log.info("RejectionMonitorActor: subscribed to order events")

    def _handle_order_event(self, event: Any) -> None:
        """Filter for ``OrderRejected`` and process streak counting."""
        if isinstance(event, OrderRejected):
            self._process_rejection(event)

    def _now(self) -> datetime:
        """Return the current UTC time (overrideable for testing)."""
        return self.clock.utc_now()  # type: ignore[no-any-return]

    def _process_rejection(self, event: OrderRejected) -> None:
        """Increment the rejection counter and emit a halt if threshold is met."""
        key = (event.instrument_id, event.strategy_id, event.reason)
        now = self._now()
        cooldown = timedelta(seconds=self.config.cooldown_seconds)

        record = self._counters.get(key)
        if record is not None:
            last_rejected = record["last_rejected"]
            if now - last_rejected > cooldown:
                # Cooldown expired — start a fresh streak.
                record = {"count": 1, "last_rejected": now, "halted": False}
            else:
                record["count"] += 1
                record["last_rejected"] = now
        else:
            record = {"count": 1, "last_rejected": now, "halted": False}

        self._counters[key] = record

        if record["count"] >= self.config.max_consecutive and not record["halted"]:
            request = StrategyHaltRequest(
                instrument_id=event.instrument_id,
                strategy_id=event.strategy_id,
                reason=event.reason,
                count=record["count"],
            )
            self.msgbus.publish(topic=self._halt_topic, msg=request)
            record["halted"] = True
            self.log.error(
                f"RejectionMonitorActor: HALT emitted for {event.strategy_id} "
                f"on {event.instrument_id} after {record['count']} rejections "
                f"(reason={event.reason})"
            )
        else:
            self.log.warning(
                f"RejectionMonitorActor: rejection {record['count']}/"
                f"{self.config.max_consecutive} for {event.strategy_id} "
                f"on {event.instrument_id} (reason={event.reason})"
            )

    def on_stop(self) -> None:
        """Unsubscribe from order events."""
        self.msgbus.unsubscribe(topic=self._topic, handler=self._handle_order_event)
        self.log.info("RejectionMonitorActor: stopped")
