"""HealthMonitorActor — periodic heartbeat with system health stats."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import Venue


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

    """

    interval: int = 30
    bar_stale_threshold: int = 300
    futu_enabled: bool = False
    ib_enabled: bool = False


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

    def on_start(self) -> None:
        """Set the first heartbeat alert when the actor starts."""
        next_time = self.clock.utc_now() + timedelta(seconds=self.config.interval)
        self.clock.set_time_alert(
            self._timer_name,
            next_time,
            self._on_heartbeat,
        )
        self.log.info("HealthMonitorActor: heartbeat started")

    def on_bar(self, bar: Bar) -> None:
        """Track the last bar received time per instrument."""
        instrument_id = str(bar.bar_type.instrument_id)
        self._last_bar_times[instrument_id] = self.clock.utc_now()

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
            bar_lines.append(f"{instrument_id} ({age_seconds}s ago)")

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
            connected = account is not None
            venue_status[venue_name] = {
                "orders": orders,
                "positions": positions,
                "connected": connected,
            }

        heartbeat_msg = self._build_heartbeat_msg(
            timestamp, orders_total, positions_total, venue_status
        )
        self.log.info(heartbeat_msg)

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

    @staticmethod
    def _is_market_hours(ts: datetime) -> bool:
        """Return True if *ts* is within US equity market hours (09:30–16:00 ET)."""
        et = ts.astimezone(ZoneInfo("America/New_York"))
        if et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        market_open = et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= et < market_close

    def on_stop(self) -> None:
        """Cancel all timers when the actor stops."""
        self.clock.cancel_timers()
        self.log.info("HealthMonitorActor: heartbeat stopped")
