"""Unit tests for HealthMonitorActor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs

from sam_trader.actors.health_monitor import (  # noqa: E501
    HealthMonitorActor,
    HealthMonitorActorConfig,
)


@pytest.fixture
def actor_config() -> HealthMonitorActorConfig:
    return HealthMonitorActorConfig(
        interval=30,
        bar_stale_threshold=300,
        futu_enabled=True,
        ib_enabled=True,
    )


@pytest.fixture
def registered_actor(
    actor_config: HealthMonitorActorConfig,
) -> HealthMonitorActor:
    actor = HealthMonitorActor(actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


class TestHealthMonitorActorConfig:
    def test_default_values(self) -> None:
        cfg = HealthMonitorActorConfig()
        assert cfg.interval == 30
        assert cfg.bar_stale_threshold == 300
        assert cfg.futu_enabled is False
        assert cfg.ib_enabled is False

    def test_custom_values(self, actor_config: HealthMonitorActorConfig) -> None:
        assert actor_config.interval == 30
        assert actor_config.bar_stale_threshold == 300
        assert actor_config.futu_enabled is True
        assert actor_config.ib_enabled is True


class TestHealthMonitorActor:
    def test_is_actor_subclass(self, actor_config: HealthMonitorActorConfig) -> None:
        actor = HealthMonitorActor(actor_config)
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_schedules_heartbeat(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        registered_actor.on_start()
        alerts = registered_actor.clock.timer_names
        assert "health_monitor_heartbeat" in alerts

    def test_on_bar_tracks_last_bar_time(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_bar(bar)
        instrument_id = str(bar.bar_type.instrument_id)
        assert instrument_id in registered_actor._last_bar_times

    def test_build_heartbeat_msg_format(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        ts = datetime.now(timezone.utc)
        venue_status = {
            "FUTU": {"orders": 1, "positions": 0, "connected": True},
            "IB": {"orders": 2, "positions": 1, "connected": False},
        }
        msg = registered_actor._build_heartbeat_msg(
            timestamp=ts,
            orders_total=3,
            positions_total=1,
            venue_status=venue_status,
        )
        assert "heartbeat" in msg
        assert "orders_total=3" in msg
        assert "positions_total=1" in msg
        assert "FUTU(orders=1 positions=0 conn=UP)" in msg
        assert "IB(orders=2 positions=1 conn=DOWN)" in msg
        assert "bars=[none]" in msg

    def test_on_heartbeat_schedules_next(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        registered_actor.on_start()
        registered_actor._on_heartbeat()
        # Timer should still be registered (rescheduled with override=True)
        assert "health_monitor_heartbeat" in registered_actor.clock.timer_names

    def test_build_heartbeat_msg_no_venues(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        ts = datetime.now(timezone.utc)
        msg = registered_actor._build_heartbeat_msg(
            timestamp=ts,
            orders_total=0,
            positions_total=0,
            venue_status={},
        )
        assert "venues=[none]" in msg

    def test_stale_instruments_during_market_hours(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # Mock market hours to be open
        with patch.object(
            HealthMonitorActor,
            "_is_market_hours",
            return_value=True,
        ):
            registered_actor._last_bar_times["TSLA.NASDAQ"] = datetime.now(
                timezone.utc
            ) - timedelta(seconds=400)
            stale = registered_actor._find_stale_instruments(datetime.now(timezone.utc))
            assert "TSLA.NASDAQ" in stale

    def test_no_stale_instruments_outside_market_hours(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        with patch.object(
            HealthMonitorActor,
            "_is_market_hours",
            return_value=False,
        ):
            registered_actor._last_bar_times["TSLA.NASDAQ"] = datetime.now(
                timezone.utc
            ) - timedelta(seconds=400)
            stale = registered_actor._find_stale_instruments(datetime.now(timezone.utc))
            assert stale == []

    def test_find_stale_instruments_returns_stale(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        with patch.object(
            HealthMonitorActor,
            "_is_market_hours",
            return_value=True,
        ):
            registered_actor._last_bar_times["TSLA.NASDAQ"] = datetime.now(
                timezone.utc
            ) - timedelta(seconds=400)
            stale = registered_actor._find_stale_instruments(datetime.now(timezone.utc))
            assert "TSLA.NASDAQ" in stale

    def test_on_stop_cancels_timers(self, registered_actor: HealthMonitorActor) -> None:
        registered_actor.on_start()
        assert "health_monitor_heartbeat" in registered_actor.clock.timer_names
        registered_actor.on_stop()
        assert "health_monitor_heartbeat" not in registered_actor.clock.timer_names

    def test_is_market_hours_weekday(self) -> None:
        # Monday 10:00 ET
        ts = datetime(2024, 1, 8, 15, 0, 0, tzinfo=timezone.utc)
        assert HealthMonitorActor._is_market_hours(ts) is True

    def test_is_market_hours_weekend(self) -> None:
        # Saturday 10:00 ET
        ts = datetime(2024, 1, 6, 15, 0, 0, tzinfo=timezone.utc)
        assert HealthMonitorActor._is_market_hours(ts) is False

    def test_is_market_hours_before_open(self) -> None:
        # Monday 09:00 ET
        ts = datetime(2024, 1, 8, 14, 0, 0, tzinfo=timezone.utc)
        assert HealthMonitorActor._is_market_hours(ts) is False

    def test_is_market_hours_after_close(self) -> None:
        # Monday 17:00 ET
        ts = datetime(2024, 1, 8, 22, 0, 0, tzinfo=timezone.utc)
        assert HealthMonitorActor._is_market_hours(ts) is False
