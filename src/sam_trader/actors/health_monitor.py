"""HealthMonitorActor — periodic heartbeat with system health stats."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import Venue

from sam_trader.services.market_calendar import MarketCalendarService


class HealthMonitorActorConfig(ActorConfig, frozen=True):
    """Configuration for the HealthMonitorActor.

    Parameters
    ----------
    interval : int, default 30
        Heartbeat interval in seconds.
    bar_stale_threshold : int, default 300
        Seconds without a bar before warning during market hours.
    futu_enabled : bool, default False
        Whether Futu venue is expected to be connected.
    ib_enabled : bool, default False
        Whether IBKR venue is expected to be connected.
    redis_host : str, optional
        Redis host for publishing heartbeat (empty = disabled).
    redis_port : int, default 6379
        Redis port.
    redis_password : str, optional
        Redis password.
    market_timezone : str, default "America/New_York"
        Timezone for market-hours check (e.g. "Asia/Hong_Kong" for HK).
    market_open_time : str, default "09:30"
        Local market open time HH:MM.
    market_close_time : str, default "16:00"
        Local market close time HH:MM.
    market : str, default ""
        Market code for calendar-aware hours ("US" or "HK").
        When set, overrides *market_timezone*/*market_open_time*/*market_close_time*
        with the canonical calendar for that market.
    market_calendar_enabled : bool, default True
        Master switch for market calendar integration. When ``False``,
        legacy weekday+fixed-hours logic is used even if *market* is set.

    """

    interval: int = 30
    bar_stale_threshold: int = 300
    futu_enabled: bool = False
    ib_enabled: bool = False
    redis_host: str = ""
    redis_port: int = 6379
    redis_password: str = ""
    market_timezone: str = "America/New_York"
    market_open_time: str = "09:30"
    market_close_time: str = "16:00"
    market: str = ""
    bar_type_strs: list[str] | None = None
    market_calendar_enabled: bool = True


class HealthMonitorActor(Actor):
    """Actor that emits periodic heartbeat logs with system health stats.

    Reports total orders, positions, and per-venue connection status.

    Parameters
    ----------
    config : HealthMonitorActorConfig
        Actor configuration.

    """

    def __init__(self, config: HealthMonitorActorConfig):
        super().__init__(config)
        self._timer_name = "health_monitor_heartbeat"
        self._last_bar_times: dict[str, datetime] = {}
        self._bar_type_display: dict[str, str] = {}
        self._redis: aioredis.Redis | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._last_venue_conn: dict[str, bool] = {}
        self._calendar: MarketCalendarService | None = None

    def on_start(self) -> None:
        """Set the first heartbeat alert when the actor starts."""
        next_time = self.clock.utc_now() + timedelta(seconds=self.config.interval)
        self.clock.set_time_alert(
            self._timer_name,
            next_time,
            self._on_heartbeat,
        )
        self.log.info("HealthMonitorActor: heartbeat started")
        # Capture the event loop in the async context so sync timer
        # callbacks can still schedule async Redis writes.
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
        if self.config.redis_host:
            try:
                self._redis = aioredis.Redis(
                    host=self.config.redis_host,
                    port=self.config.redis_port,
                    password=self.config.redis_password or None,
                    decode_responses=True,
                )
            except Exception as exc:  # noqa: BLE001
                self.log.warning("HealthMonitorActor: Redis connect failed: %s", exc)
        if self.config.market and self.config.market_calendar_enabled:
            self._calendar = MarketCalendarService()

        # Subscribe to bar types so on_bar() receives bar events.
        bar_type_strs = self.config.bar_type_strs or []
        for bts in bar_type_strs:
            try:
                bt = BarType.from_str(bts)
                self.subscribe_bars(bt)
                instrument_id = str(bt.instrument_id)
                self._bar_type_display[instrument_id] = str(bt.spec)
                self.log.info(
                    f"HealthMonitorActor: subscribed to bars for {instrument_id} "
                    f"({bt.spec})"
                )
            except Exception as exc:  # noqa: BLE001
                self.log.error(
                    f"HealthMonitorActor: failed to subscribe to bar type "
                    f"'{bts}': {exc}"
                )

    def on_bar(self, bar: Bar) -> None:
        """Track the last bar received time per instrument.

        Also persists bar receipt telemetry to Redis for external
        monitoring (dashboard, CLI) without parsing container logs.
        """
        instrument_id = str(bar.bar_type.instrument_id)
        now = self.clock.utc_now()
        self._last_bar_times[instrument_id] = now
        if instrument_id not in self._bar_type_display:
            self._bar_type_display[instrument_id] = str(bar.bar_type.spec)
        try:
            self._write_bar_telemetry_to_redis(instrument_id, now)
            self._write_bar_recent_to_redis(instrument_id, bar, now)
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"HealthMonitorActor: on_bar Redis write failed for "
                f"{instrument_id}: {exc}"
            )

    def _build_heartbeat_msg(
        self,
        timestamp: datetime,
        orders_total: int,
        positions_total: int,
        venue_status: dict[str, dict[str, Any]],
    ) -> str:
        """Return the formatted heartbeat log message."""
        bar_lines = []
        for instrument_id, last_ts in self._last_bar_times.items():
            age_seconds = int((timestamp - last_ts).total_seconds())
            display = self._bar_type_display.get(instrument_id, "")
            bar_lines.append(
                f"{instrument_id}({display}, last="
                f"{last_ts.strftime('%H:%M:%S')}, age={age_seconds}s)"
            )

        bars_str = ", ".join(bar_lines) if bar_lines else "none"

        venue_lines = []
        for venue_name, status in venue_status.items():
            conn = "UP" if status["connected"] else "DOWN"
            venue_lines.append(
                f"{venue_name}(orders={status['orders']} "
                f"positions={status['positions']} conn={conn})"
            )

        venues_str = " | ".join(venue_lines) if venue_lines else "none"

        return (
            f"heartbeat timestamp={timestamp.isoformat()} "
            f"orders_total={orders_total} positions_total={positions_total} "
            f"venues=[{venues_str}] bars=[{bars_str}]"
        )

    def _find_stale_instruments(self, now: datetime) -> list[str]:
        """Return instrument IDs that have not received a bar recently."""
        stale: list[str] = []
        if not self._is_market_hours(now):
            if self._calendar is not None:
                tz = ZoneInfo(self._calendar.market_timezone(self.config.market))
                local = now.astimezone(tz)
                if not self._calendar.is_trading_day(self.config.market, local.date()):
                    name = self._calendar.holiday_name(self.config.market, local.date())
                    holiday_str = f" ({name})" if name else ""
                    self.log.info(
                        f"Today is a {self.config.market} holiday{holiday_str}. "
                        "Skipping stale bar checks."
                    )
            return stale
        for instrument_id, last_ts in self._last_bar_times.items():
            age_seconds = int((now - last_ts).total_seconds())
            if age_seconds > self.config.bar_stale_threshold:
                stale.append(instrument_id)
        return stale

    def _on_heartbeat(self, alert=None) -> None:  # noqa: ARG002
        """Emit heartbeat log and schedule the next alert."""
        orders_total = self.cache.orders_total_count()
        positions_total = self.cache.positions_total_count()
        timestamp = self.clock.utc_now()

        venue_status: dict[str, dict[str, object]] = {}
        for venue_name, enabled in (
            ("FUTU", self.config.futu_enabled),
            ("IB", self.config.ib_enabled),
        ):
            if not enabled:
                continue
            venue = Venue(venue_name)
            orders = self.cache.orders_total_count(venue=venue)
            positions = self.cache.positions_total_count(venue=venue)
            account = self.cache.account_for_venue(venue=venue)
            # In SIMULATE mode account_for_venue may return None even
            # when the venue is connected.  Fall back to bar activity
            # as evidence of a live data pipeline.
            has_any_bars = len(self._last_bar_times) > 0
            connected = account is not None or (enabled and has_any_bars)
            prev_connected = self._last_venue_conn.get(venue_name)
            if prev_connected != connected:
                self._write_venue_conn_to_redis(venue_name, connected, timestamp)
                self._last_venue_conn[venue_name] = connected
            venue_status[venue_name] = {
                "orders": orders,
                "positions": positions,
                "connected": connected,
            }

        heartbeat_msg = self._build_heartbeat_msg(
            timestamp, orders_total, positions_total, venue_status
        )
        self.log.info(heartbeat_msg)
        self._write_heartbeat_to_redis(timestamp)

        stale_instruments = self._find_stale_instruments(timestamp)
        if stale_instruments:
            stale_str = ", ".join(stale_instruments)
            self.log.warning(
                f"No bar received for {stale_str} in > "
                f"{self.config.bar_stale_threshold}s during market hours"
            )

        next_time = self.clock.utc_now() + timedelta(seconds=self.config.interval)
        self.clock.set_time_alert(
            self._timer_name,
            next_time,
            self._on_heartbeat,
            override=True,
        )

    def _write_bar_telemetry_to_redis(
        self, instrument_id: str, timestamp: datetime
    ) -> None:
        """Persist bar receipt timestamp and daily counter to Redis.

        Fire-and-forget: schedules async Redis commands via the event loop
        captured in ``on_start`` so that ``on_bar`` never blocks.
        """
        if self._redis is None or self._main_loop is None:
            return
        date_str = timestamp.date().isoformat()
        try:
            self._main_loop.create_task(
                self._redis.setex(  # type: ignore[arg-type]
                    f"sam:bars:last:{instrument_id}",
                    86400,
                    timestamp.isoformat(),
                )
            )
            self._main_loop.create_task(
                self._redis.hincrby(  # type: ignore[arg-type]
                    f"sam:bars:count:{date_str}",
                    instrument_id,
                    1,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"HealthMonitorActor: Redis bar telemetry failed for "
                f"{instrument_id}: {exc}"
            )

    def _write_bar_recent_to_redis(
        self, instrument_id: str, bar: Bar, timestamp: datetime
    ) -> None:
        """Persist full bar OHLCV to a Redis list for dashboard detail view.

        Fire-and-forget: schedules async Redis commands via the event loop
        captured in ``on_start`` so that ``on_bar`` never blocks.
        Keeps the most recent 100 bars per instrument with a 24-hour TTL.
        """
        redis = self._redis
        if redis is None or self._main_loop is None:
            return
        import json

        try:
            bar_json = json.dumps(
                {
                    "ts": timestamp.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                }
            )

            async def _push() -> None:
                pipe = redis.pipeline()
                await pipe.lpush(  # type: ignore[misc]
                    f"sam:bars:recent:{instrument_id}", bar_json
                )
                await pipe.ltrim(  # type: ignore[misc]
                    f"sam:bars:recent:{instrument_id}", 0, 99
                )
                await pipe.expire(  # type: ignore[misc]
                    f"sam:bars:recent:{instrument_id}", 86400
                )
                await pipe.execute()  # type: ignore[misc]

            self._main_loop.create_task(_push())
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"HealthMonitorActor: Redis bar recent list failed for "
                f"{instrument_id}: {exc}"
            )

    def _write_venue_conn_to_redis(
        self, venue_name: str, connected: bool, timestamp: datetime
    ) -> None:
        """Persist venue connection status change to Redis.

        Fire-and-forget: schedules async Redis commands via the event loop
        captured in ``on_start`` so that the heartbeat callback never blocks.
        """
        if self._redis is None or self._main_loop is None:
            return
        status_str = "UP" if connected else "DOWN"
        try:
            self._main_loop.create_task(
                self._redis.set(  # type: ignore[arg-type]
                    f"sam:venue:conn:{venue_name}",
                    f"{status_str}:{timestamp.isoformat()}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"HealthMonitorActor: Redis venue conn write failed for "
                f"{venue_name}: {exc}"
            )

    def _write_heartbeat_to_redis(self, timestamp: datetime) -> None:
        """Persist heartbeat timestamp to Redis for the safety monitor.

        Uses the event loop captured in ``on_start`` because this method is
        called from a synchronous timer callback where
        ``asyncio.get_running_loop()`` raises ``RuntimeError``.
        """
        if self._redis is None or self._main_loop is None:
            return
        try:
            self._main_loop.create_task(
                self._redis.set(  # type: ignore[arg-type]
                    "sam:heartbeat:last", timestamp.isoformat()
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                "HealthMonitorActor: Redis heartbeat write failed: %s",
                exc,
            )

    def _is_market_hours(self, ts: datetime) -> bool:
        """Return True if *ts* is within configured market hours."""
        if self._calendar is not None:
            tz = ZoneInfo(self._calendar.market_timezone(self.config.market))
            local = ts.astimezone(tz)
            if not self._calendar.is_trading_day(self.config.market, local.date()):
                return False
            open_time, close_time = self._calendar.market_hours(
                self.config.market, local.date()
            )
            market_open = local.replace(
                hour=open_time.hour,
                minute=open_time.minute,
                second=0,
                microsecond=0,
            )
            market_close = local.replace(
                hour=close_time.hour,
                minute=close_time.minute,
                second=0,
                microsecond=0,
            )
            return market_open <= local < market_close

        tz = ZoneInfo(self.config.market_timezone)
        local = ts.astimezone(tz)
        if local.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        open_parts = self.config.market_open_time.split(":")
        close_parts = self.config.market_close_time.split(":")
        market_open = local.replace(
            hour=int(open_parts[0]),
            minute=int(open_parts[1]) if len(open_parts) > 1 else 0,
            second=0,
            microsecond=0,
        )
        market_close = local.replace(
            hour=int(close_parts[0]),
            minute=int(close_parts[1]) if len(close_parts) > 1 else 0,
            second=0,
            microsecond=0,
        )
        return market_open <= local < market_close

    def on_stop(self) -> None:
        """Cancel all timers when the actor stops."""
        self.clock.cancel_timers()
        self.log.info("HealthMonitorActor: heartbeat stopped")
