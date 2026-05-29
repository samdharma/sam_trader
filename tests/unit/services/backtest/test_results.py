"""Unit tests for BacktestResultStore."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.backtest.results import BacktestResult

from sam_trader.services.backtest.results import BacktestResultStore, _ns_to_datetime

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_dsn() -> str:
    """Return a test PG DSN."""
    return "postgresql://sam:sam_secret@localhost:5432/sam_trader"


@pytest.fixture
def store(pg_dsn: str) -> BacktestResultStore:
    """Return a BacktestResultStore with a test DSN."""
    return BacktestResultStore(pg_dsn=pg_dsn)


@pytest.fixture
def sample_result() -> BacktestResult:
    """Return a realistic BacktestResult for testing."""
    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1_000_000_000)
    return BacktestResult(
        trader_id="BACKTEST-001",
        machine_id="test-machine",
        run_config_id="test-run-config-id",
        instance_id="test-instance",
        run_id="test-run-abc123",
        run_started=now_ns,
        run_finished=now_ns,
        backtest_start=int(
            datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000
        ),
        backtest_end=int(
            datetime(2024, 6, 30, tzinfo=timezone.utc).timestamp() * 1_000_000_000
        ),
        elapsed_time=2.5,
        iterations=1000,
        total_events=50000,
        total_orders=120,
        total_positions=60,
        stats_pnls={"OrbStrategy": {"total_pnl": 1500.0, "avg_win": 200.0}},
        stats_returns={
            "sharpe_ratio": 1.8,
            "sortino_ratio": 2.1,
            "max_drawdown": -0.12,
            "win_rate": 0.55,
            "profit_factor": 1.8,
        },
    )


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> MagicMock:
    """Create a mock asyncpg pool with an acquire context manager."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire.return_value = ctx
    pool.close = AsyncMock(return_value=None)

    return pool


# ---------------------------------------------------------------------------
# Test: ns_to_datetime helper
# ---------------------------------------------------------------------------


class TestNsToDatetime:
    """Tests for _ns_to_datetime."""

    def test_converts_known_timestamp(self) -> None:
        """Known nanosecond timestamp converts to correct UTC datetime."""
        ns = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        result = _ns_to_datetime(ns)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        assert result.tzinfo is not None

    def test_handles_zero(self) -> None:
        """Zero nanoseconds → epoch."""
        result = _ns_to_datetime(0)
        assert result.year == 1970
        assert result.month == 1
        assert result.day == 1


# ---------------------------------------------------------------------------
# Test: save()
# ---------------------------------------------------------------------------


class TestSave:
    """Tests for BacktestResultStore.save()."""

    def test_saves_row_with_all_fields(
        self,
        store: BacktestResultStore,
        sample_result: BacktestResult,
    ) -> None:
        """save() inserts a complete row with all JSONB columns."""
        pool = _make_mock_pool()

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            run_id = asyncio.run(
                store.save(
                    result=sample_result,
                    strategy_id="US-orb_tsla_5m",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                    status="completed",
                    strategy_family="ORB",
                    strategy_version="v3.1.0",
                    tags=["live-like", "us-market"],
                )
            )

        assert run_id == "test-run-abc123"
        pool.acquire.assert_called_once()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_called_once()

        # Verify the SQL call arguments
        call_args = conn.execute.call_args
        sql, *params = call_args[0]
        assert "INSERT INTO backtest_results" in sql
        assert params[0] == "test-run-abc123"  # run_id
        assert params[2] == "US-orb_tsla_5m"  # strategy_id
        assert params[3] == "TSLA.NASDAQ"  # instrument_id
        assert params[4] == "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"  # bar_type
        assert params[7] == "completed"  # status

    def test_save_with_equity_curve(
        self,
        store: BacktestResultStore,
        sample_result: BacktestResult,
    ) -> None:
        """Equity curve JSONB round-trips correctly."""
        pool = _make_mock_pool()

        equity = [
            {"ts": "2024-01-02T10:00:00Z", "equity": 100000.0},
            {"ts": "2024-01-02T11:00:00Z", "equity": 100500.0},
            {"ts": "2024-01-02T12:00:00Z", "equity": 99800.0},
        ]

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=sample_result,
                    strategy_id="US-momentum_aapl",
                    instrument_id="AAPL.NASDAQ",
                    bar_type="AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                    equity_curve=equity,
                )
            )

        conn = pool.acquire.return_value.__aenter__.return_value
        call_args = conn.execute.call_args
        _, *params = call_args[0]
        ec_json = params[14]  # equity_curve param index
        decoded = json.loads(ec_json)
        assert len(decoded) == 3
        assert decoded[0]["equity"] == 100000.0
        assert decoded[2]["equity"] == 99800.0

    def test_save_with_none_stats(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Results with empty stats serialize correctly."""
        pool = _make_mock_pool()

        now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BT",
            machine_id="m",
            run_config_id="rc",
            instance_id="i",
            run_id="run-null-stats",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=0.0,
            iterations=0,
            total_events=0,
            total_orders=0,
            total_positions=0,
            stats_pnls={},
            stats_returns={},
        )

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=result,
                    strategy_id="test",
                    instrument_id="TEST.X",
                    bar_type="TEST.X-1-DAY-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 2),
                )
            )

        conn = pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.called

    def test_upserts_on_conflict(
        self,
        store: BacktestResultStore,
        sample_result: BacktestResult,
    ) -> None:
        """ON CONFLICT clause updates existing row."""
        pool = _make_mock_pool()

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=sample_result,
                    strategy_id="US-orb_tsla_5m",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                )
            )

        conn = pool.acquire.return_value.__aenter__.return_value
        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT (run_id) DO UPDATE" in sql

    def test_status_passed_through(
        self,
        store: BacktestResultStore,
        sample_result: BacktestResult,
    ) -> None:
        """Status value is passed through to the SQL."""
        pool = _make_mock_pool()

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=sample_result,
                    strategy_id="test",
                    instrument_id="TEST.X",
                    bar_type="TEST.X-1-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 2),
                    status="running",
                )
            )

        conn = pool.acquire.return_value.__aenter__.return_value
        _, *params = conn.execute.call_args[0]
        assert params[7] == "running"

    def test_pool_closed_after_save(
        self,
        store: BacktestResultStore,
        sample_result: BacktestResult,
    ) -> None:
        """The connection pool is properly closed after save."""
        pool = _make_mock_pool()

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=sample_result,
                    strategy_id="test",
                    instrument_id="TEST.X",
                    bar_type="TEST.X-1-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 2),
                )
            )

        pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test: get_by_strategy()
# ---------------------------------------------------------------------------


class TestGetByStrategy:
    """Tests for BacktestResultStore.get_by_strategy()."""

    def test_returns_rows_for_strategy(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Returns deserialized rows for a given strategy_id."""
        pool = _make_mock_pool()

        mock_row = {
            "id": 1,
            "run_id": "run-001",
            "run_config_id": "rc-001",
            "strategy_id": "US-orb_tsla_5m",
            "instrument_id": "TSLA.NASDAQ",
            "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 6, 30),
            "status": "completed",
            "total_events": 50000,
            "total_orders": 120,
            "total_positions": 60,
            "elapsed_secs": 2.5,
            "stats_pnls": '{"total_pnl": 1500.0}',
            "stats_returns": '{"sharpe_ratio": 1.8}',
            "equity_curve": "null",
            "strategy_family": "ORB",
            "strategy_version": "v3.1.0",
            "tags": '["live-like"]',
            "created_at": datetime.now(timezone.utc),
            "error_message": None,
        }

        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[mock_row])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            rows = asyncio.run(store.get_by_strategy("US-orb_tsla_5m"))

        assert len(rows) == 1
        row = rows[0]
        assert row["run_id"] == "run-001"
        assert row["strategy_id"] == "US-orb_tsla_5m"
        assert row["stats_pnls"] == {"total_pnl": 1500.0}
        assert row["stats_returns"] == {"sharpe_ratio": 1.8}
        assert row["tags"] == ["live-like"]
        assert row["equity_curve"] is None
        assert row["strategy_family"] == "ORB"

        conn.fetch.assert_called_once()
        sql = conn.fetch.call_args[0][0]
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT $2" in sql

    def test_empty_result(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Returns empty list when no rows match."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            rows = asyncio.run(store.get_by_strategy("non-existent"))

        assert rows == []

    def test_respects_limit(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Custom limit is passed to the query."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(store.get_by_strategy("test", limit=10))

        sql = conn.fetch.call_args[0][0]
        assert "LIMIT $2" in sql
        assert conn.fetch.call_args[0][2] == 10


# ---------------------------------------------------------------------------
# Test: get_by_date_range()
# ---------------------------------------------------------------------------


class TestGetByDateRange:
    """Tests for BacktestResultStore.get_by_date_range()."""

    def test_filters_by_date_range(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Returns rows whose backtest window overlaps the date range."""
        pool = _make_mock_pool()
        mock_row = {
            "id": 1,
            "run_id": "run-001",
            "run_config_id": "rc-001",
            "strategy_id": "test",
            "instrument_id": "TSLA.NASDAQ",
            "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "start_date": date(2024, 3, 1),
            "end_date": date(2024, 3, 31),
            "status": "completed",
            "total_events": 1000,
            "total_orders": 10,
            "total_positions": 5,
            "elapsed_secs": 1.0,
            "stats_pnls": "null",
            "stats_returns": "null",
            "equity_curve": "null",
            "strategy_family": None,
            "strategy_version": None,
            "tags": "null",
            "created_at": datetime.now(timezone.utc),
            "error_message": None,
        }

        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[mock_row])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            rows = asyncio.run(
                store.get_by_date_range(date(2024, 3, 1), date(2024, 3, 31))
            )

        assert len(rows) == 1
        assert rows[0]["run_id"] == "run-001"

        sql = conn.fetch.call_args[0][0]
        assert "start_date <= $1" in sql
        assert "end_date >= $2" in sql

    def test_no_matching_range(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Returns empty list when no runs overlap the range."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            rows = asyncio.run(
                store.get_by_date_range(date(2020, 1, 1), date(2020, 1, 31))
            )

        assert rows == []

    def test_pool_closed_after_query(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Pool is closed after query."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(store.get_by_date_range(date(2024, 1, 1), date(2024, 6, 30)))

        pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test: get_by_family()
# ---------------------------------------------------------------------------


class TestGetByFamily:
    """Tests for BacktestResultStore.get_by_family()."""

    def test_filters_by_strategy_family(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Returns rows matching a strategy family."""
        pool = _make_mock_pool()
        mock_row = {
            "id": 2,
            "run_id": "run-family-orb",
            "run_config_id": "rc",
            "strategy_id": "US-orb_tsla_5m",
            "instrument_id": "TSLA.NASDAQ",
            "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 6, 30),
            "status": "completed",
            "total_events": 100,
            "total_orders": 10,
            "total_positions": 5,
            "elapsed_secs": 1.0,
            "stats_pnls": "null",
            "stats_returns": "null",
            "equity_curve": "null",
            "strategy_family": "ORB",
            "strategy_version": "v3.1.0",
            "tags": "null",
            "created_at": datetime.now(timezone.utc),
            "error_message": None,
        }

        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[mock_row])

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            rows = asyncio.run(store.get_by_family("ORB"))

        assert len(rows) == 1
        assert rows[0]["strategy_family"] == "ORB"

        sql = conn.fetch.call_args[0][0]
        assert "strategy_family = $1" in sql


# ---------------------------------------------------------------------------
# Test: JSONB serialization edge cases
# ---------------------------------------------------------------------------


class TestJsonbEdgeCases:
    """Edge-case tests for JSONB serialization."""

    def test_nan_to_null(
        self,
        store: BacktestResultStore,
    ) -> None:
        """float('nan') in stats is serialized as JSON null."""
        pool = _make_mock_pool()

        now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        pnls: dict = {"total_pnl": float("nan")}
        rets: dict = {"sharpe": float("nan")}
        result = BacktestResult(
            trader_id="BT",
            machine_id="m",
            run_config_id="rc",
            instance_id="i",
            run_id="run-nan",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=0.0,
            iterations=0,
            total_events=0,
            total_orders=0,
            total_positions=0,
            stats_pnls=pnls,
            stats_returns=rets,
        )

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=result,
                    strategy_id="test",
                    instrument_id="TEST.X",
                    bar_type="TEST.X-1-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 2),
                )
            )

        conn = pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.called

        # Verify NaN was converted to None/null in the JSON
        _, *params = conn.execute.call_args[0]
        pnls_json = params[12]
        parsed = json.loads(pnls_json)
        assert parsed["total_pnl"] is None  # NaN → None

    def test_deeply_nested_stats(
        self,
        store: BacktestResultStore,
    ) -> None:
        """Deeply nested dict in stats is serialized correctly."""
        pool = _make_mock_pool()

        now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        pnls: dict = {
            "strategy_A": {
                "by_month": {
                    "2024-01": {"pnl": 100.0, "trades": 5},
                    "2024-02": {"pnl": -50.0, "trades": 3},
                }
            }
        }
        rets: dict = {"strategy_A": {"sharpe_ratio": 1.2, "sortino_ratio": 1.5}}
        result = BacktestResult(
            trader_id="BT",
            machine_id="m",
            run_config_id="rc",
            instance_id="i",
            run_id="run-nested",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=0.0,
            iterations=0,
            total_events=0,
            total_orders=0,
            total_positions=0,
            stats_pnls=pnls,
            stats_returns=rets,
        )

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=result,
                    strategy_id="test",
                    instrument_id="TEST.X",
                    bar_type="TEST.X-1-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                )
            )

        conn = pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.called

        _, *params = conn.execute.call_args[0]
        pnls_json = params[12]
        returns_json = params[13]
        assert isinstance(json.loads(pnls_json), dict)
        assert isinstance(json.loads(returns_json), dict)

    def test_tags_round_trip(
        self,
        store: BacktestResultStore,
        sample_result: BacktestResult,
    ) -> None:
        """Tags list round-trips as JSONB array."""
        pool = _make_mock_pool()

        tags = ["live-like", "us-market", "optimized"]

        with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
            import asyncio

            asyncio.run(
                store.save(
                    result=sample_result,
                    strategy_id="test",
                    instrument_id="TEST.X",
                    bar_type="TEST.X-1-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 2),
                    tags=tags,
                )
            )

        _, *params = (
            pool.acquire.return_value.__aenter__.return_value.execute.call_args[0]
        )
        tags_json = params[17]
        assert json.loads(tags_json) == tags
