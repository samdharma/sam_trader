"""BarResubscriptionActor — re-subscribes to bars on reconnect or market open."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.trading.trader import Trader


class BarResubscriptionActorConfig(ActorConfig, frozen=True):
    """Configuration for the BarResubscriptionActor.

    Parameters
    ----------
    bar_types : list[BarType] | None
        The bar types to monitor.  If ``None``, bar types are auto-discovered
        from the strategy configs attached to the ``Trader`` instance passed to
        the actor constructor.
    market_open_time : time, default 09:30
        The time of day (in *market_open_tz*) when the market opens.
    market_open_tz : str, default "America/New_York"
        The timezone for *market_open_time*.
    market_close_time : time, default 16:00
        The time of day (in *market_open_tz*) when the market closes.
    enabled : bool, default True
        Whether the actor is active.
    stale_timeout_seconds : int, default 300
        Seconds without a bar during market hours before a forced
        re-subscription is triggered.
    check_interval_seconds : int, default 60
        How often (in seconds) to run the periodic staleness check.

    """

    bar_types: list[BarType] | None = None
    market_open_time: time = time(9, 30)
    market_open_tz: str = "America/New_York"
    market_close_time: time = time(16, 0)
    enabled: bool = True
    stale_timeout_seconds: int = 300
    check_interval_seconds: int = 60


class BarResubscriptionActor(Actor):
    """Actor that monitors bar flow and re-subscribes when stale.

    Bar subscriptions may not resume automatically after a data-client
    disconnect/reconnect or after the market opens.  This actor tracks bar
    counts per ``BarType`` and forces a re-subscription when:

    1. Zero bars have been received by the configured market-open time, or
    2. No bar has arrived for ``stale_timeout_seconds`` during market hours.

    Re-subscription works by briefly unsubscribing all components (strategies +
    this actor) so that Nautilus drops the external data-client subscription,
    then re-subscribing everyone.  This causes Futu OpenD / IB Gateway to
    receive a fresh subscription request.

    Parameters
    ----------
    config : BarResubscriptionActorConfig
        Actor configuration.
    trader : Trader | None, optional
        Reference to the running ``Trader`` instance.  Required for auto-
        discovery of bar types and for managing strategy subscriptions.

    """

    def __init__(
        self,
        config: BarResubscriptionActorConfig,
        trader: Trader | None = None,
    ):
        super().__init__(config)
        self._trader_ref = trader
        self._bar_counts: dict[BarType, int] = {}
        self._last_bar_times: dict[BarType, datetime] = {}
        self._subscribed_at: dict[BarType, datetime] = {}
        self._market_open_timer = "bar_resubscription_market_open"
        self._stale_check_timer = "bar_resubscription_stale_check"

    def on_start(self) -> None:
        """Subscribe to monitored bar types and schedule checks."""
        if not self.config.enabled:
            self.log.info("BarResubscriptionActor: disabled")
            return

        bar_types = self._resolve_bar_types()
        if not bar_types:
            self.log.info(
                "BarResubscriptionActor: no bar_types configured or discovered; idle"
            )
            return

        now = self.clock.utc_now()
        for bt in bar_types:
            self.subscribe_bars(bt)
            self._subscribed_at[bt] = now
            self._bar_counts[bt] = 0
            self._last_bar_times[bt] = now
            self.log.info(f"BarResubscriptionActor: monitoring bar type: {bt}")

        # Market-open timer
        next_open = self._next_market_open(now)
        self.clock.set_time_alert(
            self._market_open_timer,
            next_open,
            self._on_market_open,
        )
        self.log.info(
            f"BarResubscriptionActor: next market-open check at {next_open.isoformat()}"
        )

        # Periodic stale-check timer
        next_check = now + timedelta(seconds=self.config.check_interval_seconds)
        self.clock.set_time_alert(
            self._stale_check_timer,
            next_check,
            self._on_staleness_check,
        )
        self.log.info(
            f"BarResubscriptionActor: stale check every "
            f"{self.config.check_interval_seconds}s"
        )

    def on_bar(self, bar: Bar) -> None:
        """Increment the bar receipt counter for the given bar type."""
        if not self.config.enabled:
            return
        bt = bar.bar_type
        if bt in self._bar_counts:
            self._bar_counts[bt] += 1
            self._last_bar_times[bt] = self.clock.utc_now()

    def _on_market_open(self, alert: Any = None) -> None:  # noqa: ARG001
        """Evaluate bar counts and force re-subscription where needed."""
        now = self.clock.utc_now()
        for bt in list(self._bar_counts.keys()):
            count = self._bar_counts.get(bt, 0)
            if count == 0:
                self.log.info(
                    f"BarResubscriptionActor: re-subscribing at market open "
                    f"({count} bars received): {bt}"
                )
                self._force_resubscription(bt)
            else:
                self.log.info(
                    f"BarResubscriptionActor: bars flowing for {bt} "
                    f"({count} bars received)"
                )
            self._bar_counts[bt] = 0
            self._subscribed_at[bt] = now

        next_open = self._next_market_open(now)
        self.clock.set_time_alert(
            self._market_open_timer,
            next_open,
            self._on_market_open,
            override=True,
        )
        self.log.info(
            f"BarResubscriptionActor: next market-open check at {next_open.isoformat()}"
        )

    def _on_staleness_check(self, alert: Any = None) -> None:  # noqa: ARG001
        """Check for stale bars and force re-subscription during market hours."""
        now = self.clock.utc_now()
        if not self._is_market_hours(now):
            self._reschedule_stale_check(now)
            return

        for bt, last_ts in list(self._last_bar_times.items()):
            age_seconds = int((now - last_ts).total_seconds())
            if age_seconds > self.config.stale_timeout_seconds:
                self.log.info(
                    f"BarResubscriptionActor: stale bars detected "
                    f"({age_seconds}s > {self.config.stale_timeout_seconds}s), "
                    f"re-subscribing: {bt}"
                )
                self._force_resubscription(bt)
                self._last_bar_times[bt] = now
                self._bar_counts[bt] = 0

        self._reschedule_stale_check(now)

    def _reschedule_stale_check(self, now: datetime) -> None:
        """Schedule the next periodic staleness check."""
        next_check = now + timedelta(seconds=self.config.check_interval_seconds)
        self.clock.set_time_alert(
            self._stale_check_timer,
            next_check,
            self._on_staleness_check,
            override=True,
        )

    def _force_resubscription(self, bar_type: BarType) -> None:
        """Temporarily drop all subscribers to force an external re-subscribe."""
        # Unsubscribe this actor first.
        self.unsubscribe_bars(bar_type)

        # Unsubscribe any strategy that listens to this bar type.
        if self._trader_ref is not None:
            for strategy in self._trader_ref.strategies():
                strat_bt = getattr(strategy.config, "bar_type", None)
                if strat_bt == bar_type:
                    try:
                        strategy.unsubscribe_bars(bar_type)
                    except Exception as exc:  # noqa: BLE001
                        self.log.warning(
                            f"BarResubscriptionActor: error unsubscribing "
                            f"strategy {strategy.id}: {exc}"
                        )

        # Re-subscribe strategies.
        if self._trader_ref is not None:
            for strategy in self._trader_ref.strategies():
                strat_bt = getattr(strategy.config, "bar_type", None)
                if strat_bt == bar_type:
                    try:
                        strategy.subscribe_bars(bar_type)
                    except Exception as exc:  # noqa: BLE001
                        self.log.warning(
                            f"BarResubscriptionActor: error re-subscribing "
                            f"strategy {strategy.id}: {exc}"
                        )

        # Re-subscribe this actor.
        self.subscribe_bars(bar_type)

    def _resolve_bar_types(self) -> list[BarType]:
        """Return explicit bar_types or auto-discover from strategies."""
        if self.config.bar_types is not None:
            return list(self.config.bar_types)

        discovered: set[BarType] = set()
        if self._trader_ref is not None:
            for strategy in self._trader_ref.strategies():
                strat_bt = getattr(strategy.config, "bar_type", None)
                if isinstance(strat_bt, BarType):
                    discovered.add(strat_bt)
        return list(discovered)

    def _next_market_open(self, now: datetime) -> datetime:
        """Return the next occurrence of ``market_open_time`` in UTC."""
        tz = ZoneInfo(self.config.market_open_tz)
        now_local = now.astimezone(tz)
        candidate = datetime.combine(
            now_local.date(), self.config.market_open_time, tzinfo=tz
        )
        if now_local < candidate:
            return candidate.astimezone(timezone.utc)
        next_day = now_local.date() + timedelta(days=1)
        candidate = datetime.combine(next_day, self.config.market_open_time, tzinfo=tz)
        return candidate.astimezone(timezone.utc)

    def _is_market_hours(self, ts: datetime) -> bool:
        """Return True if *ts* is within configured market hours."""
        tz = ZoneInfo(self.config.market_open_tz)
        local = ts.astimezone(tz)
        if local.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        market_open = local.replace(
            hour=self.config.market_open_time.hour,
            minute=self.config.market_open_time.minute,
            second=0,
            microsecond=0,
        )
        market_close = local.replace(
            hour=self.config.market_close_time.hour,
            minute=self.config.market_close_time.minute,
            second=0,
            microsecond=0,
        )
        return market_open <= local < market_close

    def on_stop(self) -> None:
        """Cancel timers and clean up subscriptions."""
        self.clock.cancel_timers()
        if self.config.enabled:
            for bt in list(self._bar_counts.keys()):
                try:
                    self.unsubscribe_bars(bt)
                except Exception:  # noqa: S110
                    pass
        self.log.info("BarResubscriptionActor: stopped")
