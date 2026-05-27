"""MarketSchedulerActor — LiveClock alerts for market-switch triggers.

Uses ``LiveClock.set_time_alert()`` to schedule daily triggers (all times in HKT):

- 16:00 HKT — HK close → trigger US market switch
- 04:00 HKT — US close → trigger HK market switch
- 04:00 HKT — maintenance window opens
- 07:00 HKT — maintenance window closes

Pre-switch gate checks: zero open positions, target-market brokers healthy,
and target market is a trading day. On success: ``Trader.save()`` +
``sam:market_switch_request`` published to Redis. Maintenance events published
to ``sam:maintenance_window`` channel.

Weekends and holidays skip all alerts via ``MarketCalendarService``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import Venue

from sam_trader.services.market_calendar import MarketCalendarService

# ── Alert constants (HKT) ──────────────────────────────────────────

_HK_CLOSE_TIME: tuple[int, int] = (16, 0)  # 16:00 HKT
_US_CLOSE_TIME: tuple[int, int] = (4, 0)  # 04:00 HKT
_MAINTENANCE_WINDOW_CLOSE: tuple[int, int] = (7, 0)  # 07:00 HKT

_ALERT_HK_CLOSE = "market_scheduler_hk_close"
_ALERT_US_CLOSE = "market_scheduler_us_close"
_ALERT_MAINTENANCE_CLOSE = "market_scheduler_maintenance_close"

# ── Config ─────────────────────────────────────────────────────────


class MarketSchedulerActorConfig(ActorConfig, frozen=True):
    """Configuration for the MarketSchedulerActor.

    Parameters
    ----------
    market : str
        Currently-active market code (``"US"``, ``"HK"``, or ``""``).
    market_calendar_enabled : bool
        Master switch for calendar-aware scheduling.
    session_timezone : str
        IANA timezone name for scheduling alerts (always ``"Asia/Hong_Kong"``
        since all trigger times are specified in HKT).
    redis_host : str
        Redis host for publishing switch/maintenance events.
    redis_port : int
        Redis port.
    redis_password : str
        Redis password.
    futu_enabled : bool
        Whether Futu venue is expected to be connected.
    ib_enabled : bool
        Whether IBKR venue is expected to be connected.

    """

    market: str = ""
    market_calendar_enabled: bool = True
    session_timezone: str = "Asia/Hong_Kong"
    redis_host: str = ""
    redis_port: int = 6379
    redis_password: str = ""
    futu_enabled: bool = False
    ib_enabled: bool = False


class MarketSchedulerActor(Actor):
    """Actor that schedules daily market-switch and maintenance-window alerts.

    All alert times are specified in HKT. The actor converts them to UTC
    before registering with ``LiveClock.set_time_alert()``.

    On a market-switch trigger, the actor runs a **pre-switch gate**:

    1. **Trading day check** — target market must be a trading day.
    2. **Zero open positions** — all net positions must be zero.
    3. **Broker health** — target-market brokers must be connected.

    If the gate passes, ``Trader.save()`` is called and a
    ``sam:market_switch_request`` message is published to Redis.

    Maintenance-window open/close events are published unconditionally
    (the operator decides what to act on).

    Parameters
    ----------
    config : MarketSchedulerActorConfig
        Actor configuration.

    """

    def __init__(self, config: MarketSchedulerActorConfig):
        super().__init__(config)
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._calendar: MarketCalendarService | None = None

    # ── Lifecycle ──────────────────────────────────────────────────

    def on_start(self) -> None:
        """Schedule all time alerts and initialise calendar service."""
        # Capture event loop for async Redis publishing.
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
            self.log.warning(
                "MarketSchedulerActor: unable to capture event loop — "
                "Redis publish will be skipped"
            )

        if self.config.market and self.config.market_calendar_enabled:
            self._calendar = MarketCalendarService()

        self._schedule_all_alerts()

    def on_stop(self) -> None:
        """Cancel all scheduled alerts when the actor stops."""
        self.clock.cancel_timers()

    # ── Alert Scheduling ───────────────────────────────────────────

    def _schedule_all_alerts(self) -> None:
        """Register all four time alerts with the LiveClock."""
        self._schedule_alert(
            _ALERT_HK_CLOSE,
            _HK_CLOSE_TIME,
            self._on_hk_close,
        )
        self._schedule_alert(
            _ALERT_US_CLOSE,
            _US_CLOSE_TIME,
            self._on_us_close,
        )
        self._schedule_alert(
            _ALERT_MAINTENANCE_CLOSE,
            _MAINTENANCE_WINDOW_CLOSE,
            self._on_maintenance_window_close,
        )

    def _schedule_alert(
        self,
        name: str,
        hkt_time: tuple[int, int],
        callback: Any,
    ) -> None:
        """Schedule a daily time alert at *hkt_time* (HKT hour, minute).

        Converts the local HKT time to a naive UTC datetime for
        ``LiveClock.set_time_alert()``. If today's occurrence is
        already past, schedules for tomorrow.
        """
        tz = ZoneInfo(self.config.session_timezone)
        now_utc = self.clock.utc_now()
        now_local = now_utc.astimezone(tz)

        hour, minute = hkt_time
        target_local = now_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

        # If today's target is already past, schedule for tomorrow.
        if target_local <= now_local:
            target_local += timedelta(days=1)

        # Convert to naive UTC for LiveClock.set_time_alert.
        target_utc: datetime = target_local.astimezone(ZoneInfo("UTC"))
        target_utc_naive = target_utc.replace(tzinfo=None)

        self.clock.set_time_alert(
            name,
            target_utc_naive,
            callback,
            override=True,
        )
        self.log.info(
            f"MarketSchedulerActor: {name} alert scheduled for "
            f"{target_utc_naive.isoformat()} UTC"
        )

    def _reschedule_alert(
        self,
        name: str,
        hkt_time: tuple[int, int],
        callback: Any,
    ) -> None:
        """Re-schedule a daily alert for tomorrow."""
        self._schedule_alert(name, hkt_time, callback)

    # ── Alert Callbacks ────────────────────────────────────────────

    def _on_hk_close(self, alert: UUID4 | None = None) -> None:  # noqa: ARG002
        """HK close (16:00 HKT) → trigger US market switch if gate passes."""
        self.log.info(
            "MarketSchedulerActor: HK close alert triggered — "
            "evaluating switch to US"
        )
        self._run_pre_switch_gate(target_market="US")

    def _on_us_close(self, alert: UUID4 | None = None) -> None:  # noqa: ARG002
        """US close (04:00 HKT) → trigger HK market switch + maintenance open."""
        self.log.info(
            "MarketSchedulerActor: US close alert triggered — "
            "evaluating switch to HK + maintenance window open"
        )
        # Publish maintenance window open unconditionally.
        self._publish_maintenance_event("open")
        self._run_pre_switch_gate(target_market="HK")

    def _on_maintenance_window_close(
        self, alert: UUID4 | None = None  # noqa: ARG002
    ) -> None:
        """Maintenance window close (07:00 HKT)."""
        self.log.info("MarketSchedulerActor: maintenance window close (07:00 HKT)")
        self._publish_maintenance_event("close")

    # ── Pre-Switch Gate ────────────────────────────────────────────

    def _run_pre_switch_gate(self, target_market: str) -> None:
        """Run the pre-switch gate before triggering a market switch.

        1. Check target market is a trading day.
        2. Check zero open positions.
        3. Check target-market broker health.

        On pass: saves state and publishes market_switch_request to Redis.
        """
        now_utc = self.clock.utc_now()
        tz = ZoneInfo(self.config.session_timezone)
        now_hkt = now_utc.astimezone(tz)

        # Gate 1: Target market must be a trading day.
        if not self._is_target_trading_day(target_market, now_hkt):
            self.log.warning(
                f"MarketSchedulerActor: switch to {target_market} SKIPPED "
                f"— not a trading day"
            )
            return

        # Gate 2: Zero open positions.
        positions = self.cache.positions()
        if positions:
            self.log.warning(
                f"MarketSchedulerActor: switch to {target_market} SKIPPED "
                f"— {len(positions)} open position(s) still held"
            )
            return

        # Gate 3: Broker health for target market.
        if not self._check_broker_health(target_market):
            self.log.warning(
                f"MarketSchedulerActor: switch to {target_market} SKIPPED "
                f"— target broker(s) unhealthy"
            )
            return

        # All gates passed — publish switch request.
        # State saving is handled by the restart orchestrator via
        # RestartSubscriber which listens for sam:market_switch_request.
        self.log.info(
            f"MarketSchedulerActor: pre-switch gate PASSED for {target_market} "
            f"— publishing switch request"
        )

        self._publish_market_switch_request(target_market, now_hkt)

    # ── Gate Helpers ───────────────────────────────────────────────

    def _is_target_trading_day(self, target_market: str, now_hkt: datetime) -> bool:
        """Check if *target_market* is a trading day for the current date."""
        date_val = now_hkt.date()
        if self._calendar is None:
            self.log.debug(
                "MarketSchedulerActor: no calendar service — "
                "skipping trading-day check",
            )
            return True  # Allow switch if calendar unavailable.

        is_trading = self._calendar.is_trading_day(target_market, date_val)
        if not is_trading:
            holiday = self._calendar.holiday_name(target_market, date_val)
            reason = f"{target_market} holiday on {date_val.isoformat()}" + (
                f" ({holiday})" if holiday else ""
            )
            self.log.warning(f"MarketSchedulerActor: {reason}")
        return is_trading

    def _check_broker_health(self, target_market: str) -> bool:
        """Check that the target market's expected brokers are connected.

        - Futu is always expected (serves both US and HK).
        - IB is only expected for US market.
        """
        # Futu must always be connected.
        if self.config.futu_enabled:
            acc = self.cache.account_for_venue(Venue("FUTU"))
            if acc is None:
                self.log.warning("MarketSchedulerActor: Futu account not in cache")
                return False

        # IB checked only for US target.
        if target_market == "US" and self.config.ib_enabled:
            acc = self.cache.account_for_venue(Venue("IB"))
            if acc is None:
                self.log.warning("MarketSchedulerActor: IB account not in cache")
                return False

        return True

    # ── Redis Publishing ───────────────────────────────────────────

    def _publish_market_switch_request(
        self, target_market: str, now_hkt: datetime
    ) -> None:
        """Publish ``sam:market_switch_request`` event to Redis."""
        if self._main_loop is None:
            self.log.warning(
                "MarketSchedulerActor: cannot publish switch request — " "no event loop"
            )
            return

        self._main_loop.create_task(
            self._redis_publish(
                "sam:market_switch_request",
                {
                    "target": target_market,
                    "timestamp": now_hkt.isoformat(),
                },
            )
        )

    def _publish_maintenance_event(self, action: str) -> None:
        """Publish ``sam:maintenance_window`` event to Redis."""
        if self._main_loop is None:
            self.log.warning(
                "MarketSchedulerActor: cannot publish maintenance event — "
                "no event loop"
            )
            return

        now_utc = self.clock.utc_now()
        now_hkt = now_utc.astimezone(ZoneInfo(self.config.session_timezone))

        self._main_loop.create_task(
            self._redis_publish(
                "sam:maintenance_window",
                {
                    "action": action,
                    "timestamp": now_hkt.isoformat(),
                },
            )
        )

    async def _redis_publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish a JSON message to the given Redis channel."""
        if not self.config.redis_host:
            self.log.debug(
                f"MarketSchedulerActor: Redis host not configured — "
                f"skipping publish to {channel}"
            )
            return

        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                socket_connect_timeout=5,
            )
            msg = json.dumps(payload)
            await r.publish(channel, msg)
            await r.aclose()
            self.log.info(f"MarketSchedulerActor: published to {channel}: {msg}")
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"MarketSchedulerActor: Redis publish to {channel} failed: {exc}"
            )
