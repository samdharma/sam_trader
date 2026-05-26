"""Unit tests for HealthMonitorActor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
def redis_actor_config() -> HealthMonitorActorConfig:
    return HealthMonitorActorConfig(
        interval=30,
        bar_stale_threshold=300,
        futu_enabled=True,
        ib_enabled=True,
        redis_host="localhost",
        redis_port=6379,
        redis_password="",
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


@pytest.fixture
def redis_registered_actor(
    redis_actor_config: HealthMonitorActorConfig,
) -> HealthMonitorActor:
    actor = HealthMonitorActor(redis_actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.setex = AsyncMock(return_value=True)
    redis.hincrby = AsyncMock(return_value=1)
    redis.set = AsyncMock(return_value=True)
    redis.close = AsyncMock(return_value=None)
    redis.pipeline = MagicMock(return_value=redis)
    redis.lpush = AsyncMock(return_value=1)
    redis.ltrim = AsyncMock(return_value=True)
    redis.expire = AsyncMock(return_value=True)
    redis.execute = AsyncMock(return_value=[1, True, True])
    return redis


class TestHealthMonitorActorConfig:
    def test_default_values(self) -> None:
        cfg = HealthMonitorActorConfig()
        assert cfg.interval == 30
        assert cfg.bar_stale_threshold == 300
        assert cfg.futu_enabled is False
        assert cfg.ib_enabled is False
        assert cfg.market == ""
        assert cfg.market_calendar_enabled is True

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

    def test_on_bar_writes_redis_telemetry(
        self,
        redis_registered_actor: HealthMonitorActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.health_monitor.aioredis.Redis",
                return_value=mock_redis,
            ):
                redis_registered_actor.on_start()
                await asyncio.sleep(0.01)

                bar = TestDataStubs.bar_5decimal_5min_bid()
                redis_registered_actor.on_bar(bar)
                await asyncio.sleep(0.01)

                instrument_id = str(bar.bar_type.instrument_id)
                mock_redis.setex.assert_awaited_once()
                call_args = mock_redis.setex.call_args
                assert call_args[0][0] == f"sam:bars:last:{instrument_id}"
                assert call_args[0][1] == 86400
                assert isinstance(call_args[0][2], str)

                mock_redis.hincrby.assert_awaited_once()
                hcall = mock_redis.hincrby.call_args
                assert hcall[0][0].startswith("sam:bars:count:")
                assert hcall[0][1] == instrument_id
                assert hcall[0][2] == 1

        asyncio.run(_test())

    def test_on_bar_writes_redis_recent_list(
        self,
        redis_registered_actor: HealthMonitorActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.health_monitor.aioredis.Redis",
                return_value=mock_redis,
            ):
                redis_registered_actor.on_start()
                await asyncio.sleep(0.01)

                bar = TestDataStubs.bar_5decimal_5min_bid()
                redis_registered_actor.on_bar(bar)
                await asyncio.sleep(0.01)

                instrument_id = str(bar.bar_type.instrument_id)
                mock_redis.pipeline.assert_called_once()
                mock_redis.lpush.assert_awaited_once()
                lcall = mock_redis.lpush.call_args
                assert lcall[0][0] == f"sam:bars:recent:{instrument_id}"
                import json

                payload = json.loads(lcall[0][1])
                assert "ts" in payload
                assert "open" in payload
                assert "high" in payload
                assert "low" in payload
                assert "close" in payload
                assert "volume" in payload

                mock_redis.ltrim.assert_awaited_once()
                tcall = mock_redis.ltrim.call_args
                assert tcall[0][0] == f"sam:bars:recent:{instrument_id}"
                assert tcall[0][1] == 0
                assert tcall[0][2] == 99

                mock_redis.expire.assert_awaited_once()
                ecall = mock_redis.expire.call_args
                assert ecall[0][0] == f"sam:bars:recent:{instrument_id}"
                assert ecall[0][1] == 86400

        asyncio.run(_test())

    def test_on_bar_no_redis_when_not_configured(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # redis_host is empty by default
        registered_actor.on_start()
        bar = TestDataStubs.bar_5decimal_5min_bid()
        # Should not raise
        registered_actor.on_bar(bar)

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

    def test_on_heartbeat_writes_venue_conn_on_change(
        self,
        redis_registered_actor: HealthMonitorActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.health_monitor.aioredis.Redis",
                return_value=mock_redis,
            ):
                redis_registered_actor.on_start()
                await asyncio.sleep(0.01)

                # First heartbeat — both FUTU and IB transition from unknown
                # (no bars, but account_for_venue returns None and enabled=True
                # with no bars → connected=False based on has_any_bars logic)
                redis_registered_actor._on_heartbeat()
                await asyncio.sleep(0.01)

                # After first heartbeat, status should be recorded for both venues
                # Since no account and no bars, connected=False
                mock_redis.set.assert_called()
                venue_calls = [
                    c
                    for c in mock_redis.set.call_args_list
                    if "sam:venue:conn" in c[0][0]
                ]
                assert len(venue_calls) == 2
                futu_call = [c for c in venue_calls if "FUTU" in c[0][0]]
                ib_call = [c for c in venue_calls if "IB" in c[0][0]]
                assert len(futu_call) == 1
                assert "DOWN" in futu_call[0][0][1]
                assert len(ib_call) == 1
                assert "DOWN" in ib_call[0][0][1]

                # Second heartbeat — status unchanged, no new venue conn writes
                redis_registered_actor._on_heartbeat()
                await asyncio.sleep(0.01)
                venue_calls_after = [
                    c
                    for c in mock_redis.set.call_args_list
                    if "sam:venue:conn" in c[0][0]
                ]
                assert len(venue_calls_after) == 2

        asyncio.run(_test())

    def test_on_heartbeat_no_venue_conn_write_when_redis_not_ready(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # redis_host is empty by default, so _redis is None
        registered_actor.on_start()
        # Should not raise
        registered_actor._on_heartbeat()

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

    def test_is_market_hours_weekday(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # Monday 10:00 ET = 15:00 UTC
        ts = datetime(2024, 1, 8, 15, 0, 0, tzinfo=timezone.utc)
        assert registered_actor._is_market_hours(ts) is True

    def test_is_market_hours_weekend(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # Saturday 10:00 ET = 15:00 UTC
        ts = datetime(2024, 1, 6, 15, 0, 0, tzinfo=timezone.utc)
        assert registered_actor._is_market_hours(ts) is False

    def test_is_market_hours_before_open(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # Monday 09:00 ET = 14:00 UTC
        ts = datetime(2024, 1, 8, 14, 0, 0, tzinfo=timezone.utc)
        assert registered_actor._is_market_hours(ts) is False

    def test_is_market_hours_after_close(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        # Monday 17:00 ET = 22:00 UTC
        ts = datetime(2024, 1, 8, 22, 0, 0, tzinfo=timezone.utc)
        assert registered_actor._is_market_hours(ts) is False

    def test_is_market_hours_hk_weekday(
        self,
    ) -> None:
        # HK market: 09:30-16:00 HKT
        # Monday 10:00 HKT = 02:00 UTC
        cfg = HealthMonitorActorConfig(
            market_timezone="Asia/Hong_Kong",
            market_open_time="09:30",
            market_close_time="16:00",
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        ts = datetime(2024, 1, 8, 2, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is True

    def test_is_market_hours_hk_outside_hours(
        self,
    ) -> None:
        # HK market closed at 20:00 HKT = 12:00 UTC
        cfg = HealthMonitorActorConfig(
            market_timezone="Asia/Hong_Kong",
            market_open_time="09:30",
            market_close_time="16:00",
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        ts = datetime(2024, 1, 8, 12, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is False

    def test_is_market_hours_hk_weekend(
        self,
    ) -> None:
        # Saturday 10:00 HKT = 02:00 UTC
        cfg = HealthMonitorActorConfig(
            market_timezone="Asia/Hong_Kong",
            market_open_time="09:30",
            market_close_time="16:00",
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        ts = datetime(2024, 1, 6, 2, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is False

    def test_on_start_subscribes_to_bars(
        self,
    ) -> None:
        """on_start calls subscribe_bars for each bar_type_str in config."""
        cfg = HealthMonitorActorConfig(
            bar_type_strs=[
                "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "AAPL.NASDAQ-5-MINUTE-BID-INTERNAL",
            ],
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )

        with patch.object(actor, "subscribe_bars") as mock_sub:
            actor.on_start()
            assert mock_sub.call_count == 2
            # Verify the BarType objects passed are correct
            called_bar_types = [call[0][0] for call in mock_sub.call_args_list]
            assert str(called_bar_types[0].instrument_id) == "TSLA.NASDAQ"
            assert str(called_bar_types[1].instrument_id) == "AAPL.NASDAQ"

    def test_on_start_subscribes_to_bars_populates_display(
        self,
    ) -> None:
        """on_start stores bar type display strings in _bar_type_display."""
        cfg = HealthMonitorActorConfig(
            bar_type_strs=["TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"],
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )

        with patch.object(actor, "subscribe_bars"):
            actor.on_start()

        assert "TSLA.NASDAQ" in actor._bar_type_display
        assert actor._bar_type_display["TSLA.NASDAQ"] == "15-MINUTE-LAST"

    def test_build_heartbeat_msg_format_with_bars(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        """Heartbeat message includes bar type display when bars received."""
        ts = datetime(2024, 1, 8, 15, 10, 0, tzinfo=timezone.utc)
        last_bar = datetime(2024, 1, 8, 15, 5, 0, tzinfo=timezone.utc)

        registered_actor._last_bar_times["TSLA.NASDAQ"] = last_bar
        registered_actor._bar_type_display["TSLA.NASDAQ"] = "15-MINUTE-LAST"

        venue_status = {
            "FUTU": {"orders": 1, "positions": 0, "connected": True},
        }
        msg = registered_actor._build_heartbeat_msg(
            timestamp=ts,
            orders_total=1,
            positions_total=0,
            venue_status=venue_status,
        )
        assert "bars=[TSLA.NASDAQ(15-MINUTE-LAST, last=15:05:00, age=300s)]" in msg
        assert "bars=[none]" not in msg

    def test_on_bar_records_bar_type_display(
        self, registered_actor: HealthMonitorActor
    ) -> None:
        """on_bar stores bar type display string when not already known."""
        bar = TestDataStubs.bar_5decimal_5min_bid()
        registered_actor.on_bar(bar)
        instrument_id = str(bar.bar_type.instrument_id)
        assert instrument_id in registered_actor._bar_type_display
        assert registered_actor._bar_type_display[instrument_id] == str(
            bar.bar_type.spec
        )

    def test_on_start_bad_bar_type_logs_error(
        self,
    ) -> None:
        """on_start logs error (not crash) for malformed bar_type_str."""
        cfg = HealthMonitorActorConfig(
            bar_type_strs=["INVALID-BAR-TYPE"],
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        with patch.object(actor, "subscribe_bars"):
            actor.on_start()
        # Actor should not crash — subscribe_bars was never called
        # for the bad type (the exception was caught and logged)


class TestHealthMonitorActorCalendar:
    """Tests for market-calendar-aware _is_market_hours."""

    def test_calendar_us_weekday(self) -> None:
        cfg = HealthMonitorActorConfig(market="US")
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # Monday 10:00 ET = 15:00 UTC
        ts = datetime(2024, 1, 8, 15, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is True

    def test_calendar_us_holiday(self) -> None:
        cfg = HealthMonitorActorConfig(market="US")
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # 2024-07-04 is a US holiday (Thursday) — 10:00 ET
        ts = datetime(2024, 7, 4, 15, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is False

    def test_calendar_us_early_close(self) -> None:
        cfg = HealthMonitorActorConfig(market="US")
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # 2024-07-03 is the day before Independence Day — early close at 13:00 ET
        ts = datetime(2024, 7, 3, 18, 0, 0, tzinfo=timezone.utc)  # 14:00 ET
        assert actor._is_market_hours(ts) is False

    def test_calendar_hk_weekday(self) -> None:
        cfg = HealthMonitorActorConfig(market="HK")
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # Monday 10:00 HKT = 02:00 UTC
        ts = datetime(2024, 1, 8, 2, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is True

    def test_calendar_hk_holiday(self) -> None:
        cfg = HealthMonitorActorConfig(market="HK")
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # 2024-10-01 is HK holiday (Tuesday) — 10:00 HKT
        ts = datetime(2024, 10, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is False

    def test_calendar_no_market_uses_legacy(self) -> None:
        cfg = HealthMonitorActorConfig(
            market_timezone="America/New_York",
            market_open_time="09:30",
            market_close_time="16:00",
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # Holiday but no market set — legacy logic doesn't know about holidays
        ts = datetime(2024, 7, 4, 15, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is True

    def test_calendar_disabled_uses_legacy(self) -> None:
        cfg = HealthMonitorActorConfig(
            market="US",
            market_calendar_enabled=False,
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        # Holiday but calendar disabled — legacy logic doesn't know about holidays
        ts = datetime(2024, 7, 4, 15, 0, 0, tzinfo=timezone.utc)
        assert actor._is_market_hours(ts) is True

    def test_holiday_logs_skip_message(self) -> None:
        cfg = HealthMonitorActorConfig(
            market="US",
            market_calendar_enabled=True,
            bar_stale_threshold=300,
        )
        actor = HealthMonitorActor(cfg)
        actor.register_base(
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        actor.on_start()
        actor._calendar = MagicMock()
        actor._calendar.market_timezone.return_value = "America/New_York"
        actor._calendar.is_trading_day.return_value = False
        actor._calendar.holiday_name.return_value = "Independence Day"
        ts = datetime(2024, 7, 4, 15, 0, 0, tzinfo=timezone.utc)
        stale = actor._find_stale_instruments(ts)
        assert stale == []
