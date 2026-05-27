"""Unit tests for ReadinessCheckerActor."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.actors.readiness_checker import (
    _CHECK_NAMES,
    ReadinessCheckerActor,
    ReadinessCheckerActorConfig,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _make_config(**overrides) -> ReadinessCheckerActorConfig:
    defaults = dict(
        market="US",
        sod_readiness_time="08:00",
        session_timezone="America/New_York",
        redis_host="test-redis",
        redis_port=6379,
        redis_password="",
        futu_enabled=True,
        ib_enabled=True,
        postgres_host="test-pg",
        postgres_port=5432,
        postgres_db="sam_trader",
        postgres_user="sam",
        postgres_password="sam_secret",
        instrument_ids=["AAPL.NASDAQ", "TSLA.NASDAQ"],
        bundle_count=3,
        market_calendar_enabled=True,
    )
    defaults.update(overrides)
    return ReadinessCheckerActorConfig(**defaults)  # type: ignore[arg-type]


def _make_actor(config=None, **kwargs) -> ReadinessCheckerActor:
    """Create a ReadinessCheckerActor registered with TestComponentStubs."""
    cfg = config or _make_config(**kwargs)
    actor = ReadinessCheckerActor(cfg)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


# ── Config Tests ────────────────────────────────────────────────────


class TestReadinessCheckerActorConfig:

    def test_config_defaults(self):
        cfg = ReadinessCheckerActorConfig()
        assert cfg.market == ""
        assert cfg.sod_readiness_time == "08:00"
        assert cfg.session_timezone == "America/New_York"
        assert cfg.redis_host == ""
        assert cfg.futu_enabled is False
        assert cfg.ib_enabled is False

    def test_config_full(self):
        cfg = _make_config()
        assert cfg.market == "US"
        assert cfg.instrument_ids == ["AAPL.NASDAQ", "TSLA.NASDAQ"]
        assert cfg.bundle_count == 3

    def test_config_frozen(self):
        cfg = _make_config()
        with pytest.raises(Exception):
            cfg.market = "HK"  # type: ignore[misc]

    def test_config_market_hk(self):
        cfg = _make_config(
            market="HK",
            sod_readiness_time="07:00",
            session_timezone="Asia/Hong_Kong",
            ib_enabled=False,
        )
        assert cfg.market == "HK"
        assert cfg.sod_readiness_time == "07:00"
        assert cfg.session_timezone == "Asia/Hong_Kong"
        assert cfg.ib_enabled is False

    def test_config_empty_instrument_ids(self):
        cfg = _make_config(instrument_ids=None)
        assert cfg.instrument_ids is None


# ── Check 1: Broker Connectivity ────────────────────────────────────


class TestBrokerConnectivity:

    def test_futu_disconnected_when_no_account(self):
        """FAIL when Futu enabled but no account in cache (stub cache is empty)."""
        actor = _make_actor(futu_enabled=True, ib_enabled=False)
        result, detail = actor._check_broker_connectivity()
        assert result == "FAIL"
        assert "FUTU" in detail

    def test_no_brokers_enabled_always_pass(self):
        """PASS when no brokers are expected."""
        actor = _make_actor(futu_enabled=False, ib_enabled=False)
        result, detail = actor._check_broker_connectivity()
        assert result == "PASS"

    def test_ib_disconnected_when_no_account(self):
        """FAIL when IB enabled but no account in cache."""
        actor = _make_actor(futu_enabled=False, ib_enabled=True)
        result, detail = actor._check_broker_connectivity()
        assert result == "FAIL"
        assert "IB" in detail

    def test_both_disconnected(self):
        """FAIL when both expected but neither has an account."""
        actor = _make_actor(futu_enabled=True, ib_enabled=True)
        result, detail = actor._check_broker_connectivity()
        assert result == "FAIL"
        assert "FUTU" in detail
        assert "IB" in detail


# ── Check 2: QuoteTick Flow ────────────────────────────────────────


class TestQuoteFlow:

    def test_all_fresh(self):
        actor = _make_actor(instrument_ids=["AAPL.NASDAQ", "TSLA.NASDAQ"])
        now = datetime(2026, 5, 27, 12, 0, 0)
        actor._last_quote_times = {
            "AAPL.NASDAQ": now - timedelta(seconds=10),
            "TSLA.NASDAQ": now - timedelta(seconds=30),
        }
        result, detail = actor._check_quote_flow(now)
        assert result == "PASS"
        assert "all" in detail

    def test_one_stale(self):
        actor = _make_actor(instrument_ids=["AAPL.NASDAQ", "TSLA.NASDAQ"])
        now = datetime(2026, 5, 27, 12, 0, 0)
        actor._last_quote_times = {
            "AAPL.NASDAQ": now - timedelta(seconds=600),
            "TSLA.NASDAQ": now - timedelta(seconds=10),
        }
        result, detail = actor._check_quote_flow(now)
        assert result == "PASS"
        assert "1/2" in detail

    def test_all_stale(self):
        actor = _make_actor(instrument_ids=["AAPL.NASDAQ"])
        now = datetime(2026, 5, 27, 12, 0, 0)
        actor._last_quote_times = {"AAPL.NASDAQ": now - timedelta(seconds=600)}
        result, detail = actor._check_quote_flow(now)
        assert result == "FAIL"
        assert "stale" in detail

    def test_no_quotes_received(self):
        actor = _make_actor(instrument_ids=["AAPL.NASDAQ"])
        now = datetime(2026, 5, 27, 12, 0, 0)
        result, detail = actor._check_quote_flow(now)
        assert result == "FAIL"
        assert "no quote" in detail.lower()

    def test_no_instruments_configured(self):
        actor = _make_actor(instrument_ids=[])
        now = datetime(2026, 5, 27, 12, 0, 0)
        result, detail = actor._check_quote_flow(now)
        assert result == "SKIP"


# ── Check 5: Bundles Loaded ────────────────────────────────────────


class TestBundlesLoaded:

    def test_bundles_present(self):
        actor = _make_actor(bundle_count=5)
        result, detail = actor._check_bundles_loaded()
        assert result == "PASS"
        assert "5" in detail

    def test_no_bundles(self):
        actor = _make_actor(bundle_count=0)
        result, detail = actor._check_bundles_loaded()
        assert result == "FAIL"
        assert "zero" in detail.lower()


# ── Check 6: Redis / PG Health ─────────────────────────────────────


class TestRedisPgHealth:

    def test_both_healthy(self):
        actor = _make_actor()
        with (
            patch("redis.asyncio.Redis") as mock_redis,
            patch("sam_trader.actors.readiness_checker.asyncpg") as mock_pg,
        ):
            mock_redis.return_value.ping = AsyncMock()
            mock_redis.return_value.aclose = AsyncMock()
            mock_pg.connect = AsyncMock()
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_pg.connect.return_value = mock_conn

            result, detail = asyncio.run(actor._check_redis_pg_health())
            assert result == "PASS"

    def test_redis_down(self):
        actor = _make_actor()
        with (
            patch("redis.asyncio.Redis") as mock_redis,
            patch("sam_trader.actors.readiness_checker.asyncpg") as mock_pg,
        ):
            mock_redis.return_value.ping = AsyncMock(
                side_effect=ConnectionError("no route")
            )
            mock_redis.return_value.aclose = AsyncMock()
            mock_pg.connect = AsyncMock()
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_conn.close = AsyncMock()
            mock_pg.connect.return_value = mock_conn

            result, detail = asyncio.run(actor._check_redis_pg_health())
            assert result == "FAIL"
            assert "Redis" in detail

    def test_pg_down(self):
        actor = _make_actor()
        with (
            patch("redis.asyncio.Redis") as mock_redis,
            patch("sam_trader.actors.readiness_checker.asyncpg") as mock_pg,
        ):
            mock_redis.return_value.ping = AsyncMock()
            mock_redis.return_value.aclose = AsyncMock()
            mock_pg.connect = AsyncMock(side_effect=OSError("connection refused"))

            result, detail = asyncio.run(actor._check_redis_pg_health())
            assert result == "FAIL"
            assert "PostgreSQL" in detail

    def test_both_down(self):
        actor = _make_actor()
        with (
            patch("redis.asyncio.Redis") as mock_redis,
            patch("sam_trader.actors.readiness_checker.asyncpg") as mock_pg,
        ):
            mock_redis.return_value.ping = AsyncMock(
                side_effect=ConnectionError("no route")
            )
            mock_redis.return_value.aclose = AsyncMock()
            mock_pg.connect = AsyncMock(side_effect=OSError("connection refused"))

            result, detail = asyncio.run(actor._check_redis_pg_health())
            assert result == "FAIL"
            assert "Redis" in detail
            assert "PostgreSQL" in detail

    def test_redis_not_configured(self):
        actor = _make_actor(redis_host="", postgres_host="")
        result, detail = asyncio.run(actor._check_redis_pg_health())
        assert result == "FAIL"
        assert "host not configured" in detail


# ── Check 7: Calendar Trading Day ──────────────────────────────────


class TestCalendarTradingDay:

    def test_trading_day(self):
        actor = _make_actor(market="US", market_calendar_enabled=True)
        actor._calendar = MagicMock()
        actor._calendar.is_trading_day.return_value = True
        now_local = datetime(2026, 5, 27, 8, 0, 0)
        result, detail = actor._check_calendar_trading_day(now_local)
        assert result == "PASS"
        assert "trading day" in detail

    def test_holiday(self):
        actor = _make_actor(market="US", market_calendar_enabled=True)
        actor._calendar = MagicMock()
        actor._calendar.is_trading_day.return_value = False
        actor._calendar.holiday_name.return_value = "Christmas"
        now_local = datetime(2026, 12, 25, 8, 0, 0)
        result, detail = actor._check_calendar_trading_day(now_local)
        assert result == "FAIL"
        assert "holiday" in detail.lower()

    def test_calendar_disabled(self):
        actor = _make_actor(market="US", market_calendar_enabled=False)
        now_local = datetime(2026, 5, 27, 8, 0, 0)
        result, detail = actor._check_calendar_trading_day(now_local)
        assert result == "SKIP"

    def test_no_market_configured(self):
        actor = _make_actor(market="", market_calendar_enabled=True)
        now_local = datetime(2026, 5, 27, 8, 0, 0)
        result, detail = actor._check_calendar_trading_day(now_local)
        assert result == "SKIP"

    def test_calendar_lookup_exception(self):
        actor = _make_actor(market="US", market_calendar_enabled=True)
        actor._calendar = MagicMock()
        actor._calendar.is_trading_day.side_effect = ValueError("invalid")
        now_local = datetime(2026, 5, 27, 8, 0, 0)
        result, detail = actor._check_calendar_trading_day(now_local)
        assert result == "FAIL"


# ── Quote Tick Tracking ────────────────────────────────────────────


class TestQuoteTickTracking:

    def test_tracks_quote_tick(self):
        actor = _make_actor()
        tick = MagicMock()
        tick.instrument_id = InstrumentId(Symbol("AAPL"), Venue("NASDAQ"))

        actor.on_quote_tick(tick)

        assert "AAPL.NASDAQ" in actor._last_quote_times
        # Verify timestamp was recorded (can't mock utc_now on Cython clock)
        assert isinstance(actor._last_quote_times["AAPL.NASDAQ"], datetime)


# ── Readiness Report Aggregation ───────────────────────────────────


class TestReadinessReport:

    def test_all_checks_pass(self):
        results = {name: ("PASS", "") for name in _CHECK_NAMES}
        overall = "PASS" if all(v[0] != "FAIL" for v in results.values()) else "FAIL"
        assert overall == "PASS"

    def test_one_check_fails(self):
        results = {name: ("PASS", "") for name in _CHECK_NAMES}
        results["redis_pg_health"] = ("FAIL", "connection refused")
        overall = "PASS" if all(v[0] != "FAIL" for v in results.values()) else "FAIL"
        assert overall == "FAIL"

    def test_skips_dont_break_pass(self):
        results = {
            "broker_connectivity": ("PASS", ""),
            "quote_flow": ("SKIP", ""),
            "instruments_resolved": ("SKIP", ""),
            "account_status": ("PASS", ""),
            "bundles_loaded": ("PASS", ""),
            "redis_pg_health": ("PASS", ""),
            "calendar_trading_day": ("SKIP", ""),
        }
        overall = "PASS" if all(v[0] != "FAIL" for v in results.values()) else "FAIL"
        assert overall == "PASS"


# ── Existing Actor Tests (with TestComponentStubs) ──────────────────


class TestReadinessCheckerActor:

    def test_is_actor_subclass(self):
        """Actor is a Nautilus Actor subclass."""
        actor = _make_actor()
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_schedules_readiness_check(self):
        """on_start sets a time alert for the SOD readiness check."""
        actor = _make_actor()

        # Need to capture event loop for on_start
        async def _start():
            actor.on_start()
            await asyncio.sleep(0.01)

        asyncio.run(_start())
        assert "sod_readiness_check" in actor.clock.timer_names

    def test_on_stop_cancels_timers(self):
        """on_stop calls clock.cancel_timers()."""
        actor = _make_actor()

        async def _lifecycle():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor.on_stop()

        asyncio.run(_lifecycle())
        # Timers cancelled — no exception raised

    def test_on_quote_tick_records_timestamp(self):
        """on_quote_tick stores the last quote time for an instrument."""
        from nautilus_trader.test_kit.stubs.data import TestDataStubs

        actor = _make_actor()
        quote = TestDataStubs.quote_tick()
        instrument_id = str(quote.instrument_id)
        actor.on_quote_tick(quote)
        assert instrument_id in actor._last_quote_times
        age = actor.clock.utc_now() - actor._last_quote_times[instrument_id]
        assert age.total_seconds() < 5

    def test_on_start_subscribes_to_quote_ticks(self):
        """on_start subscribes to quote ticks for configured instruments."""
        actor = _make_actor(instrument_ids=["AAPL.NASDAQ"])

        async def _start():
            actor.on_start()
            await asyncio.sleep(0.01)

        asyncio.run(_start())
        # Subscription recorded — no exception means it worked
        subscribed = actor.msgbus.subscriptions()
        # The subscriptions list should have entries for quote ticks
        assert len(subscribed) > 0

    def test_schedule_next_check_logs(self):
        """_schedule_next_check sets the alert."""
        actor = _make_actor()
        actor._schedule_next_check()
        assert "sod_readiness_check" in actor.clock.timer_names

    def test_on_sod_readiness_check_reschedules(self):
        """_on_sod_readiness_check reschedules the timer for next day."""
        actor = _make_actor()

        async def _test():
            actor.on_start()
            await asyncio.sleep(0.01)
            actor._on_sod_readiness_check()
            await asyncio.sleep(0.01)

        asyncio.run(_test())
        assert "sod_readiness_check" in actor.clock.timer_names

    def test_log_readiness_report_all_pass(self):
        """_log_readiness_report does not raise when all checks pass."""
        actor = _make_actor()
        report = {
            "market": "US",
            "date": "2026-05-27",
            "overall": "PASS",
            "checks": [
                {"name": name, "result": "PASS", "detail": "ok"}
                for name in _CHECK_NAMES
            ],
        }
        # Should not raise
        actor._log_readiness_report(report)

    def test_log_readiness_report_fail(self):
        """_log_readiness_report does not raise when checks fail."""
        actor = _make_actor()
        report = {
            "market": "US",
            "date": "2026-05-27",
            "overall": "FAIL",
            "checks": [
                {"name": "broker_connectivity", "result": "FAIL", "detail": "err"},
            ]
            + [{"name": n, "result": "PASS", "detail": "ok"} for n in _CHECK_NAMES[1:]],
        }
        # Should not raise
        actor._log_readiness_report(report)


# ── Redis Write ────────────────────────────────────────────────────


class TestRedisWrite:

    def test_write_success(self):
        actor = _make_actor(market="US")
        today = date(2026, 5, 27)
        report = {"market": "US", "date": "2026-05-27", "overall": "PASS", "checks": []}

        with patch("redis.asyncio.Redis") as mock_redis:
            instance = mock_redis.return_value
            instance.setex = AsyncMock()
            instance.aclose = AsyncMock()

            asyncio.run(actor._write_readiness_to_redis(today, report))

            instance.setex.assert_called_once()
            call_args = instance.setex.call_args
            assert call_args[0][0] == "sam:readiness:US:2026-05-27"
            assert call_args[0][1] == 172800
            payload = json.loads(call_args[0][2])
            assert payload["overall"] == "PASS"

    def test_write_no_redis_host(self):
        actor = _make_actor(redis_host="")
        today = date(2026, 5, 27)
        report = {"overall": "PASS"}

        with patch("redis.asyncio.Redis") as mock_redis:
            asyncio.run(actor._write_readiness_to_redis(today, report))
            mock_redis.assert_not_called()

    def test_write_exception_handled(self):
        """Redis write failure is caught — no exception propagates."""
        actor = _make_actor(market="US")
        today = date(2026, 5, 27)
        report = {"overall": "PASS"}

        with patch("redis.asyncio.Redis") as mock_redis:
            instance = mock_redis.return_value
            instance.setex = AsyncMock(side_effect=ConnectionError("no route"))
            instance.aclose = AsyncMock()

            # Should not raise
            asyncio.run(actor._write_readiness_to_redis(today, report))
