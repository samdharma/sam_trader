"""Unit tests for BarResubscriptionActor."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs

from sam_trader.actors.bar_resubscription import (
    BarResubscriptionActor,
    BarResubscriptionActorConfig,
)


@pytest.fixture
def actor_config() -> BarResubscriptionActorConfig:
    bar_type = TestDataStubs.bar_5decimal_5min_bid().bar_type
    return BarResubscriptionActorConfig(
        bar_types=[bar_type],
        market_open_time=time(9, 30),
        market_open_tz="America/New_York",
        enabled=True,
        stale_timeout_seconds=300,
        check_interval_seconds=60,
    )


@pytest.fixture
def registered_actor(
    actor_config: BarResubscriptionActorConfig,
) -> BarResubscriptionActor:
    actor = BarResubscriptionActor(actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


class TestBarResubscriptionActorConfig:
    def test_default_values(self) -> None:
        cfg = BarResubscriptionActorConfig()
        assert cfg.bar_types is None
        assert cfg.market_open_time == time(9, 30)
        assert cfg.market_open_tz == "America/New_York"
        assert cfg.enabled is True
        assert cfg.stale_timeout_seconds == 300
        assert cfg.check_interval_seconds == 60

    def test_custom_values(self, actor_config: BarResubscriptionActorConfig) -> None:
        assert actor_config.market_open_time == time(9, 30)
        assert actor_config.stale_timeout_seconds == 300
        assert actor_config.check_interval_seconds == 60
        assert actor_config.enabled is True


class TestBarResubscriptionActor:
    def test_is_actor_subclass(
        self, actor_config: BarResubscriptionActorConfig
    ) -> None:
        actor = BarResubscriptionActor(actor_config)
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_schedules_timers(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        registered_actor.on_start()
        assert "bar_resubscription_market_open" in registered_actor.clock.timer_names
        assert "bar_resubscription_stale_check" in registered_actor.clock.timer_names

    def test_on_start_disabled_does_nothing(self) -> None:
        cfg = BarResubscriptionActorConfig(enabled=False)
        actor = BarResubscriptionActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        assert "bar_resubscription_market_open" not in actor.clock.timer_names
        assert "bar_resubscription_stale_check" not in actor.clock.timer_names

    def test_on_start_no_bar_types_no_trader_is_idle(
        self,
    ) -> None:
        cfg = BarResubscriptionActorConfig(bar_types=None)
        actor = BarResubscriptionActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        assert "bar_resubscription_market_open" not in actor.clock.timer_names
        assert "bar_resubscription_stale_check" not in actor.clock.timer_names

    def test_on_start_auto_discovers_bar_types_from_trader(self) -> None:
        bar_type = TestDataStubs.bar_5decimal_5min_bid().bar_type
        strategy = MagicMock()
        strategy.config.bar_type = bar_type
        strategy.id = "TestStrategy-001"

        trader = MagicMock()
        trader.strategies.return_value = [strategy]

        cfg = BarResubscriptionActorConfig(bar_types=None)
        actor = BarResubscriptionActor(cfg, trader=trader)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        assert "bar_resubscription_market_open" in actor.clock.timer_names
        assert "bar_resubscription_stale_check" in actor.clock.timer_names
        assert bar_type in actor._bar_counts

    def test_on_bar_tracks_count_and_time(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_start()
        registered_actor.on_bar(bar)
        assert registered_actor._bar_counts[bar.bar_type] == 1
        assert bar.bar_type in registered_actor._last_bar_times

    def test_on_market_open_resubscribes_when_zero_bars(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        registered_actor.on_start()
        with patch.object(registered_actor, "_force_resubscription") as mock_force:
            registered_actor._on_market_open()
            mock_force.assert_called_once()

    def test_on_market_open_skips_when_bars_flowing(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_start()
        registered_actor.on_bar(bar)
        with patch.object(registered_actor, "_force_resubscription") as mock_force:
            registered_actor._on_market_open()
            mock_force.assert_not_called()

    def test_on_staleness_check_resubscribes_when_stale_during_market(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_start()
        # Simulate a bar received long ago
        registered_actor._last_bar_times[bar.bar_type] = datetime.now(
            timezone.utc
        ) - timedelta(seconds=400)
        with (
            patch.object(BarResubscriptionActor, "_is_market_hours", return_value=True),
            patch.object(registered_actor, "_force_resubscription") as mock_force,
        ):
            registered_actor._on_staleness_check()
            mock_force.assert_called_once()

    def test_on_staleness_check_skips_outside_market_hours(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_start()
        registered_actor._last_bar_times[bar.bar_type] = datetime.now(
            timezone.utc
        ) - timedelta(seconds=400)
        with (
            patch.object(
                BarResubscriptionActor, "_is_market_hours", return_value=False
            ),
            patch.object(registered_actor, "_force_resubscription") as mock_force,
        ):
            registered_actor._on_staleness_check()
            mock_force.assert_not_called()

    def test_on_staleness_check_skips_when_fresh(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_start()
        registered_actor.on_bar(bar)
        with (
            patch.object(BarResubscriptionActor, "_is_market_hours", return_value=True),
            patch.object(registered_actor, "_force_resubscription") as mock_force,
        ):
            registered_actor._on_staleness_check()
            mock_force.assert_not_called()

    def test_force_resubscription_unsubscribes_and_resubscribes(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        bar_type = TestDataStubs.bar_5decimal_5min_bid().bar_type
        strategy = MagicMock()
        strategy.config.bar_type = bar_type
        strategy.id = "TestStrategy-001"

        trader = MagicMock()
        trader.strategies.return_value = [strategy]
        registered_actor._trader_ref = trader

        registered_actor.on_start()
        with (
            patch.object(registered_actor, "subscribe_bars") as mock_sub,
            patch.object(registered_actor, "unsubscribe_bars") as mock_unsub,
        ):
            registered_actor._force_resubscription(bar_type)
            mock_unsub.assert_called_once_with(bar_type)
            assert strategy.unsubscribe_bars.called
            assert strategy.subscribe_bars.called
            mock_sub.assert_called_once_with(bar_type)

    def test_next_market_open_same_day(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        # 08:00 ET on a Monday -> 09:30 ET same day
        ts = datetime(2024, 1, 8, 13, 0, 0, tzinfo=timezone.utc)  # 08:00 ET
        result = registered_actor._next_market_open(ts)
        # 09:30 ET = 14:30 UTC in January (EST = UTC-5)
        assert result.hour == 14
        assert result.minute == 30

    def test_next_market_open_next_day(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        # 12:00 ET on a Monday -> 09:30 ET Tuesday
        ts = datetime(2024, 1, 8, 17, 0, 0, tzinfo=timezone.utc)  # 12:00 ET
        result = registered_actor._next_market_open(ts)
        assert result.day == 9  # next day

    def test_is_market_hours_weekday_open(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 8, 15, 0, 0, tzinfo=timezone.utc)  # 10:00 ET Mon
        assert registered_actor._is_market_hours(ts) is True

    def test_is_market_hours_weekend(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 6, 15, 0, 0, tzinfo=timezone.utc)  # Sat
        assert registered_actor._is_market_hours(ts) is False

    def test_is_market_hours_before_open(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 8, 14, 0, 0, tzinfo=timezone.utc)  # 09:00 ET Mon
        assert registered_actor._is_market_hours(ts) is False

    def test_is_market_hours_after_close(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 8, 22, 0, 0, tzinfo=timezone.utc)  # 17:00 ET Mon
        assert registered_actor._is_market_hours(ts) is False

    def test_on_stop_cancels_timers(
        self, registered_actor: BarResubscriptionActor
    ) -> None:
        registered_actor.on_start()
        assert "bar_resubscription_market_open" in registered_actor.clock.timer_names
        registered_actor.on_stop()
        assert (
            "bar_resubscription_market_open" not in registered_actor.clock.timer_names
        )
        assert (
            "bar_resubscription_stale_check" not in registered_actor.clock.timer_names
        )


class TestBarResubscriptionActorHK:
    @pytest.fixture
    def registered_actor_hk(self) -> BarResubscriptionActor:
        bar_type = TestDataStubs.bar_5decimal_5min_bid().bar_type
        cfg = BarResubscriptionActorConfig(
            bar_types=[bar_type],
            market_open_time=time(9, 30),
            market_open_tz="Asia/Hong_Kong",
            market_close_time=time(16, 0),
            enabled=True,
            stale_timeout_seconds=300,
            check_interval_seconds=60,
        )
        actor = BarResubscriptionActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        return actor

    def test_is_market_hours_hk_weekday(
        self, registered_actor_hk: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 8, 2, 0, 0, tzinfo=timezone.utc)  # 10:00 HKT Mon
        assert registered_actor_hk._is_market_hours(ts) is True

    def test_is_market_hours_hk_outside_hours(
        self, registered_actor_hk: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)  # 08:00 HKT Mon
        assert registered_actor_hk._is_market_hours(ts) is False

    def test_is_market_hours_hk_weekend(
        self, registered_actor_hk: BarResubscriptionActor
    ) -> None:
        ts = datetime(2024, 1, 6, 2, 0, 0, tzinfo=timezone.utc)  # 10:00 HKT Sat
        assert registered_actor_hk._is_market_hours(ts) is False

    def test_next_market_open_hk_same_day(
        self, registered_actor_hk: BarResubscriptionActor
    ) -> None:
        # 08:00 HKT on a Monday -> 09:30 HKT same day
        ts = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)  # 08:00 HKT Mon
        result = registered_actor_hk._next_market_open(ts)
        # 09:30 HKT = 01:30 UTC
        assert result.hour == 1
        assert result.minute == 30
        assert result.day == 8

    def test_next_market_open_hk_next_day(
        self, registered_actor_hk: BarResubscriptionActor
    ) -> None:
        # 12:00 HKT on a Monday -> 09:30 HKT Tuesday
        ts = datetime(2024, 1, 8, 4, 0, 0, tzinfo=timezone.utc)  # 12:00 HKT Mon
        result = registered_actor_hk._next_market_open(ts)
        assert result.day == 9  # next day
