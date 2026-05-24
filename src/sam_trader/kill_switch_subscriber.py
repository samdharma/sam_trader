"""Background Redis subscriber for kill-switch commands.

When the ops CLI publishes ``sam:kill_switch HALTED`` (or ``CLOSE_ONLY`` /
``RUNNING``), this subscriber updates the Nautilus ``LiveRiskEngine``
``trading_state`` and, for ``HALTED``, initiates an emergency market exit on
all strategies.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import redis.asyncio as aioredis
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import TradingState

from sam_trader.config import SamTraderConfig

logger = logging.getLogger(__name__)

KILL_SWITCH_CHANNEL = "sam:kill_switch"

# Mapping from our safety states to Nautilus TradingState.
_STATE_MAP: dict[str, TradingState] = {
    "HALTED": TradingState.HALTED,
    "CLOSE_ONLY": TradingState.REDUCING,
    "RUNNING": TradingState.ACTIVE,
}


class KillSwitchSubscriber:
    """Subscribes to Redis kill-switch commands and updates the risk engine.

    Parameters
    ----------
    node : TradingNode
        The running Nautilus trading node.
    cfg : SamTraderConfig
        Configuration (Redis connection).

    """

    def __init__(self, node: TradingNode, cfg: SamTraderConfig) -> None:
        self._node = node
        self._cfg = cfg
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background subscriber thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("KillSwitchSubscriber started on %s", KILL_SWITCH_CHANNEL)

    def stop(self) -> None:
        """Signal the subscriber to stop and wait for thread exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("KillSwitchSubscriber stopped")

    def _run(self) -> None:
        """Thread entry point — runs the async listener."""
        try:
            asyncio.run(self._listen())
        except Exception as exc:  # noqa: BLE001
            logger.warning("KillSwitchSubscriber loop exited with error: %s", exc)

    async def _listen(self) -> None:
        """Async Redis pub/sub listener."""
        try:
            redis = aioredis.Redis(
                host=self._cfg.redis_host,
                port=self._cfg.redis_port,
                password=self._cfg.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            pubsub = redis.pubsub()
            await pubsub.subscribe(KILL_SWITCH_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KillSwitchSubscriber: Redis connection failed: %s", exc)
            return

        try:
            async for message in pubsub.listen():
                if self._stop_event.is_set():
                    break
                if message.get("type") != "message":
                    continue
                data = message.get("data", "")
                await self._handle_state(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KillSwitchSubscriber: Redis listen error: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe(KILL_SWITCH_CHANNEL)
                await redis.close()
            except Exception:  # noqa: S110
                pass

    async def _handle_state(self, data: str) -> None:
        """Map the received state to a Nautilus TradingState and apply it."""
        state = _STATE_MAP.get(data)
        if state is None:
            logger.warning("KillSwitchSubscriber: unknown state %r", data)
            return

        logger.critical(
            "KillSwitchSubscriber: received %s → applying TradingState.%s",
            data,
            state.name,
        )

        # Schedule on the node's event loop to avoid thread-safety issues.
        loop = self._node.get_event_loop()
        if loop is not None and loop.is_running():
            done_event = threading.Event()
            exc_container: list[Exception] = []

            def _apply() -> None:
                try:
                    self._apply_state(state, data)
                except Exception as exc:  # noqa: BLE001
                    exc_container.append(exc)
                finally:
                    done_event.set()

            loop.call_soon_threadsafe(_apply)
            if not done_event.wait(timeout=10):
                logger.error("KillSwitchSubscriber: state apply timed out")
                return
            if exc_container:
                logger.error(
                    "KillSwitchSubscriber: state apply failed: %s", exc_container[0]
                )
        else:
            self._apply_state(state, data)

    def _apply_state(self, state: TradingState, raw_state: str) -> None:
        """Apply the trading state and, for HALTED, trigger emergency exit."""
        risk_engine = self._node.kernel.exec_engine.risk_engine
        try:
            risk_engine.set_trading_state(state)
        except Exception as exc:  # noqa: BLE001
            logger.error("KillSwitchSubscriber: set_trading_state failed: %s", exc)
            return

        if raw_state == "HALTED":
            self._cancel_all_orders()

    def _cancel_all_orders(self) -> None:
        """Cancel all orders and close all positions across all strategies."""
        for strategy in self._node.trader.strategies():
            try:
                strategy.market_exit()
                logger.info(
                    "KillSwitchSubscriber: market_exit triggered for %s",
                    strategy.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "KillSwitchSubscriber: market_exit failed for %s: %s",
                    strategy.id,
                    exc,
                )
