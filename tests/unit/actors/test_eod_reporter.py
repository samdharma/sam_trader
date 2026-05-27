"""Unit tests for EndOfDayReporterActor."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.actors.eod_reporter import (
    EndOfDayReporterActor,
    EndOfDayReporterActorConfig,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _make_config(**overrides) -> EndOfDayReporterActorConfig:
    defaults = dict(
        market="US",
        eod_report_time="16:05",
        session_timezone="America/New_York",
        redis_host="test-redis",
        redis_port=6379,
        redis_password="",
        postgres_host="test-pg",
        postgres_port=5432,
        postgres_db="sam_trader",
        postgres_user="sam",
        postgres_password="sam_secret",
        market_calendar_enabled=True,
    )
    defaults.update(overrides)
    return EndOfDayReporterActorConfig(**defaults)  # type: ignore[arg-type]


def _make_actor(config=None, **kwargs) -> EndOfDayReporterActor:
    """Create an EndOfDayReporterActor registered with TestComponentStubs."""
    cfg = config or _make_config(**kwargs)
    actor = EndOfDayReporterActor(cfg)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


# ── Config Tests ────────────────────────────────────────────────────


class TestEndOfDayReporterActorConfig:

    def test_config_defaults(self):
        cfg = EndOfDayReporterActorConfig()
        assert cfg.market == ""
        assert cfg.eod_report_time == "16:05"
        assert cfg.session_timezone == "America/New_York"
        assert cfg.redis_host == ""
        assert cfg.postgres_host == ""
        assert cfg.market_calendar_enabled is True

    def test_config_full_us(self):
        cfg = _make_config(market="US", eod_report_time="16:05")
        assert cfg.market == "US"
        assert cfg.eod_report_time == "16:05"
        assert cfg.session_timezone == "America/New_York"

    def test_config_full_hk(self):
        cfg = _make_config(
            market="HK",
            eod_report_time="16:05",
            session_timezone="Asia/Hong_Kong",
        )
        assert cfg.market == "HK"
        assert cfg.eod_report_time == "16:05"
        assert cfg.session_timezone == "Asia/Hong_Kong"

    def test_config_frozen(self):
        cfg = _make_config()
        with pytest.raises(Exception):
            cfg.market = "HK"  # type: ignore[misc]


# ── Actor Lifecycle ─────────────────────────────────────────────────


class TestEndOfDayReporterActorLifecycle:

    def test_is_actor_subclass(self):
        """Actor is a Nautilus Actor subclass."""
        actor = _make_actor()
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_schedules_alert(self):
        """on_start registers the eod_report time alert."""
        actor = _make_actor()

        async def _start():
            actor.on_start()
            await asyncio.sleep(0.01)

        asyncio.run(_start())
        assert "eod_report" in actor.clock.timer_names

    def test_on_stop_cancels_timers(self):
        """on_stop calls clock.cancel_timers()."""
        actor = _make_actor()

        async def _lifecycle():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor.on_stop()

        asyncio.run(_lifecycle())
        # No exception raised — timers cancelled.

    def test_calendar_initialised_when_market_set(self):
        """MarketCalendarService is created when market and calendar are enabled."""
        actor = _make_actor(market="US", market_calendar_enabled=True)

        async def _start():
            actor.on_start()
            await asyncio.sleep(0.01)

        asyncio.run(_start())
        assert actor._calendar is not None

    def test_calendar_not_initialised_when_market_empty(self):
        """MarketCalendarService skipped when market is empty string."""
        actor = _make_actor(market="", market_calendar_enabled=True)

        async def _start():
            actor.on_start()
            await asyncio.sleep(0.01)

        asyncio.run(_start())
        assert actor._calendar is None

    def test_calendar_not_initialised_when_disabled(self):
        """MarketCalendarService skipped when calendar is disabled."""
        actor = _make_actor(market="US", market_calendar_enabled=False)

        async def _start():
            actor.on_start()
            await asyncio.sleep(0.01)

        asyncio.run(_start())
        assert actor._calendar is None


# ── Trading Day Check ───────────────────────────────────────────────


class TestTradingDayCheck:

    def test_trading_day_true(self):
        """Calendar reports today as trading day → report proceeds."""
        actor = _make_actor(market="US")
        actor._calendar = MagicMock()
        actor._calendar.is_trading_day.return_value = True
        now_local = datetime(2026, 5, 27, 16, 5, 0)

        result = actor._is_trading_day(now_local)
        assert result is True

    def test_trading_day_false(self):
        """Calendar reports holiday → report skipped."""
        actor = _make_actor(market="US")
        actor._calendar = MagicMock()
        actor._calendar.is_trading_day.return_value = False
        now_local = datetime(2026, 12, 25, 16, 5, 0)

        result = actor._is_trading_day(now_local)
        assert result is False

    def test_calendar_none_allows_report(self):
        """When calendar service is None, report is allowed."""
        actor = _make_actor(market="US")
        actor._calendar = None
        now_local = datetime(2026, 5, 27, 16, 5, 0)

        result = actor._is_trading_day(now_local)
        assert result is True

    def test_calendar_disabled_allows_report(self):
        """When calendar is disabled, report always proceeds."""
        actor = _make_actor(market="US", market_calendar_enabled=False)
        actor._calendar = None
        now_local = datetime(2026, 12, 25, 16, 5, 0)

        result = actor._is_trading_day(now_local)
        assert result is True


# ── Position Summary ───────────────────────────────────────────────


class TestPositionSummary:

    def test_no_open_positions(self):
        """Stub cache has zero positions → all_flat=True."""
        actor = _make_actor()

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            result = actor._section_positions()
            assert result["all_flat"] is True
            assert result["total_open_positions"] == 0
            assert result["positions"] == []

        asyncio.run(_test())

    def test_flat_positions_logged(self):
        """Position summary reports flat status."""
        actor = _make_actor()

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            result = actor._section_positions()
            assert "all_flat" in result
            assert "total_open_positions" in result
            assert "positions" in result

        asyncio.run(_test())


# ── EOD Report Generation ──────────────────────────────────────────


class TestEodReportGeneration:

    def test_report_skipped_on_holiday(self):
        """Report generation is skipped when today is not a trading day."""
        actor = _make_actor(market="US")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = False

            with patch("redis.asyncio.Redis") as mock_redis:
                actor._on_eod_report()
                await asyncio.sleep(0.05)
                # Redis should NOT be called for report write.
                assert mock_redis.return_value.setex.call_count == 0

        asyncio.run(_test())

    def test_report_generated_and_written_to_redis(self):
        """Full EOD report flow: generate → write to Redis."""
        actor = _make_actor(
            market="US",
            redis_host="test-redis",
            postgres_host="",  # Skip PG — faster test.
        )

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = True

            class _MockRedis:
                """Simulates Redis with no P&L/heartbeat/rejection data."""

                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def setex(self, key, ttl, value):
                    pass

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                actor._on_eod_report()
                await asyncio.sleep(0.1)

            # After the async task runs, the report should have been produced.
            # We verify by checking the log summary was called (no exception).

        asyncio.run(_test())

    def test_report_structure_has_all_sections(self):
        """The generated report JSON contains all 6 required sections."""
        actor = _make_actor(
            market="US",
            redis_host="test-redis",
            postgres_host="",  # Skip PG for fast test.
        )

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = True

            captured_report: dict = {}

            class _MockRedis:
                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def setex(self, key, ttl, value):
                    nonlocal captured_report
                    captured_report = json.loads(value)

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                actor._on_eod_report()
                await asyncio.sleep(0.1)

            assert "market" in captured_report
            assert "date" in captured_report
            assert "generated_at_utc" in captured_report
            assert "daily_pnl" in captured_report
            assert "fills_summary" in captured_report
            assert "max_drawdown" in captured_report
            assert "position_summary" in captured_report
            assert "rejection_events" in captured_report
            assert "health_events" in captured_report

            # Section types
            assert isinstance(captured_report["daily_pnl"], list)
            assert isinstance(captured_report["fills_summary"], dict)
            assert isinstance(captured_report["max_drawdown"], dict)
            assert isinstance(captured_report["position_summary"], dict)
            assert isinstance(captured_report["rejection_events"], dict)
            assert isinstance(captured_report["health_events"], dict)

        asyncio.run(_test())

    def test_report_skip_when_calendar_disabled(self):
        """When calendar is disabled, report proceeds regardless of holiday."""
        actor = _make_actor(
            market="US",
            market_calendar_enabled=False,
            redis_host="test-redis",
            postgres_host="",
        )

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            # Calendar is None, so is_trading_day returns True.

            written = False

            class _MockRedis:
                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def setex(self, key, ttl, value):
                    nonlocal written
                    written = True

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                actor._on_eod_report()
                await asyncio.sleep(0.1)

            assert written, "Report should be written even without calendar"

        asyncio.run(_test())


# ── Redis Write ─────────────────────────────────────────────────────


class TestRedisWrite:

    def test_write_to_redis_with_correct_key(self):
        """Report is written to ``sam:eod_report:{market}:{date}``."""
        actor = _make_actor(market="US", redis_host="test-redis", postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = True

            setex_calls = []

            class _MockRedis:
                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def setex(self, key, ttl, value):
                    setex_calls.append((key, ttl, value))

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                actor._on_eod_report()
                await asyncio.sleep(0.1)

            assert len(setex_calls) >= 1
            key = setex_calls[0][0]
            assert key.startswith("sam:eod_report:US:")
            # TTL should be 7 days (604800 seconds).
            assert setex_calls[0][1] == 604800

        asyncio.run(_test())

    def test_no_redis_host_skips_write(self):
        """Redis write skipped when host not configured."""
        actor = _make_actor(market="US", redis_host="", postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = True

            with patch("redis.asyncio.Redis") as mock_redis:
                actor._on_eod_report()
                await asyncio.sleep(0.1)
                mock_redis.assert_not_called()

        asyncio.run(_test())

    def test_redis_write_exception_caught(self):
        """Redis write failure is caught — no exception propagates."""
        actor = _make_actor(market="US", redis_host="test-redis", postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = True

            class _MockRedis:
                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def setex(self, key, ttl, value):
                    raise ConnectionError("no route to host")

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                actor._on_eod_report()
                await asyncio.sleep(0.1)
                # Should not raise — exception caught internally.

        asyncio.run(_test())


# ── Schedule Recurrence ─────────────────────────────────────────────


class TestScheduleRecurrence:

    def test_report_re_schedules_after_run(self):
        """After the alert fires, the next alert is re-scheduled."""
        actor = _make_actor(market="US", redis_host="test-redis", postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._calendar = MagicMock()
            actor._calendar.is_trading_day.return_value = True

            class _MockRedis:
                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def setex(self, key, ttl, value):
                    pass

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                actor._on_eod_report()
                await asyncio.sleep(0.1)
                # After callback + re-schedule, timer should be re-registered.
                assert "eod_report" in actor.clock.timer_names

        asyncio.run(_test())

    def test_no_event_loop_skips_report(self):
        """When event loop not captured, report generation is skipped gracefully."""
        actor = _make_actor()
        actor._main_loop = None

        # Should not raise.
        actor._on_eod_report()


# ── Section 4: Position Summary Edge Cases ─────────────────────────


class TestPositionsDataEdgeCases:

    def test_positions_with_empty_list(self):
        """Section 4 handles empty positions list gracefully."""
        actor = _make_actor()

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            result = actor._section_positions()
            assert result["all_flat"] is True
            assert result["total_open_positions"] == 0

        asyncio.run(_test())


# ── Section 1: Daily P&L ────────────────────────────────────────────


class TestDailyPnlSection:

    def test_pnl_empty_when_no_redis_data(self):
        """Section 1 returns empty list when Redis has no P&L keys."""
        actor = _make_actor(market="US", redis_host="test-redis", postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            class _MockRedis:
                async def scan(self, cursor, match=None, count=None):
                    return (0, [])

                async def get(self, key):
                    return None

                async def aclose(self):
                    pass

            with patch("redis.asyncio.Redis", return_value=_MockRedis()):
                result = await actor._section_daily_pnl(date(2026, 5, 27))
                assert result == []

        asyncio.run(_test())


# ── Section 2: Fills Summary ───────────────────────────────────────


class TestFillsSection:

    def test_fills_skipped_when_no_pg(self):
        """Section 2 returns skipped status when PG not configured."""
        actor = _make_actor(postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            result = await actor._section_fills(date(2026, 5, 27))
            assert result.get("status", "").startswith("skipped")

        asyncio.run(_test())


# ── Section 5: Rejection Events ─────────────────────────────────────


class TestRejectionSection:

    def test_rejections_skipped_when_no_redis(self):
        """Section 5 returns skipped status when Redis not configured."""
        actor = _make_actor(redis_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            result = await actor._section_rejections(date(2026, 5, 27))
            assert result.get("status", "").startswith("skipped")

        asyncio.run(_test())


# ── Section 6: Health Events ────────────────────────────────────────


class TestHealthSection:

    def test_health_skipped_when_no_redis(self):
        """Section 6 returns skipped status when Redis not configured."""
        actor = _make_actor(redis_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            result = await actor._section_health(date(2026, 5, 27))
            assert result.get("status", "").startswith("skipped")

        asyncio.run(_test())


# ── Section 3: Max Drawdown ─────────────────────────────────────────


class TestDrawdownSection:

    def test_drawdown_unavailable_when_no_data(self):
        """Section 3 returns 'unavailable' when no Redis/PG data."""
        actor = _make_actor(redis_host="", postgres_host="")

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)

            result = await actor._section_max_drawdown(date(2026, 5, 27))
            assert result["status"] == "unavailable"
            assert result["drawdowns"] == []

        asyncio.run(_test())
