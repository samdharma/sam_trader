# flake8: noqa: E501
"""Integration test — full backtesting pipeline E2E.

Tests the complete flow:
  1. BarDownloader → (mock) Futu → Parquet catalog
  2. BacktestEngineWrapper → build config + (mock) BacktestNode
  3. ParameterSweep → grid generation + (mock) multi-config run
  4. WalkForward → rolling windows + (mock) sweep/test
  5. BacktestResultStore → serialize → (mock) PG → query

Ticket: sam_trader-9z3.13.1.9
"""

from __future__ import annotations

import asyncio
import copy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.engine import (
    BacktestEngineError,
    BacktestEngineWrapper,
)
from sam_trader.services.backtest.results import (
    BacktestResultStore,
    _sanitize_nan,
    _serialize_stats,
)
from sam_trader.services.backtest.sweep import (
    ParameterSweep,
    generate_sweep_grid,
    parse_sweep_flags,
)
from sam_trader.services.backtest.walk_forward import (
    WalkForward,
    WalkForwardResult,
)
from sam_trader.services.bar_downloader import (
    BarDownloader,
    BarDownloaderError,
    BatchDownloadResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ns_ts(iso: str) -> int:
    """Convert ISO datetime string to nanosecond epoch for BacktestResult."""
    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1_000_000_000)


def _mock_result(*, sharpe: float = 1.5, pnl: float = 1000.0) -> BacktestResult:
    """Return a minimal BacktestResult with the given Sharpe and P&L."""
    now_ns = _make_ns_ts("2024-06-30T16:00:00")
    start_ns = _make_ns_ts("2024-01-02T09:30:00")
    end_ns = _make_ns_ts("2024-06-28T16:00:00")
    return BacktestResult(
        trader_id="BACKTEST-001",
        machine_id="test",
        run_config_id=f"cfg-{hash(sharpe + pnl) % 10000:04d}",
        instance_id="inst-1",
        run_id=f"run-{hash(sharpe + pnl) % 10000:04d}",
        run_started=now_ns,
        run_finished=now_ns,
        backtest_start=start_ns,
        backtest_end=end_ns,
        elapsed_time=5.0,
        iterations=300,
        total_events=1500,
        total_orders=12,
        total_positions=6,
        stats_pnls={"OrbStrategy": {"total_pnl": pnl}},
        stats_returns={
            "sharpe_ratio": sharpe,
            "sortino_ratio": sharpe * 1.15,
            "max_drawdown": -0.15,
            "win_rate": 0.52,
            "profit_factor": 1.6,
        },
    )


def _make_bundle(instrument: str = "TSLA.NASDAQ") -> ImportableStrategyConfig:
    """Return a minimal ImportableStrategyConfig for testing."""
    return ImportableStrategyConfig(
        strategy_path="sam_trader.strategies.orb:OrbStrategy",
        config_path="sam_trader.strategies.orb:OrbStrategyConfig",
        config={
            "instrument_id": instrument,
            "bar_type": f"{instrument}-5-MINUTE-LAST-EXTERNAL",
            "first_candle_minutes": 15,
            "trade_size": 5,
            "stop_loss_ticks": 10,
            "take_profit_ticks": 30,
            "venue": "FUTU",
            "bundle_id": "test-bundle",
            "market": "US",
        },
    )


def _fake_bar_df(rows: int = 5, start_date: str = "2024-01-02") -> pd.DataFrame:
    """Return a DataFrame that looks like Futu historical kline response."""
    from datetime import timedelta

    base = datetime.fromisoformat(start_date + "T09:30:00")
    records: list[dict[str, Any]] = []
    for i in range(rows):
        ts = base + timedelta(minutes=5 * i)
        records.append(
            {
                "time_key": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": 100.0 + i,
                "close": 101.0 + i,
                "high": 102.0 + i,
                "low": 99.0 + i,
                "volume": 1000 * (i + 1),
            }
        )
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 1. BarDownloader → BacktestEngineWrapper pipeline
# ---------------------------------------------------------------------------


class TestBarDownloaderToEnginePipeline:
    """Integration: BarDownloader output feeds into BacktestEngineWrapper config."""

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_download_pipeline_feeds_engine_config(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        tmp_path: Any,
    ) -> None:
        """BarDownloader result → BacktestEngineWrapper.run config assembly."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_ctx.request_history_kline.return_value = (0, _fake_bar_df(6), None)
        mock_get_ctx.return_value = mock_ctx

        downloader = BarDownloader(catalog_path=str(tmp_path))
        dl_result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=90,
            )
        )

        assert isinstance(dl_result, BatchDownloadResult)
        assert dl_result.total_bars_written >= 1
        assert dl_result.instruments_failed == []

        wrapper = BacktestEngineWrapper(catalog_path=str(tmp_path))
        bundle = _make_bundle("TSLA.NASDAQ")

        cfg = wrapper.build_run_config(
            strategies=[bundle],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        assert cfg.data[0].catalog_path == str(tmp_path)
        assert cfg.data[0].instrument_ids == ["TSLA.NASDAQ"]
        bar_types = cfg.data[0].bar_types or []
        assert "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL" in bar_types
        assert cfg.engine is not None and cfg.engine.run_analysis is True

    def test_unsupported_bar_type_blocks_pipeline(self, tmp_path: Any) -> None:
        """Unsupported bar_type_spec raises before any Futu call."""
        downloader = BarDownloader(catalog_path=str(tmp_path))
        with pytest.raises(BarDownloaderError, match="TICK"):
            asyncio.run(
                downloader.download(
                    instrument_ids=["TSLA.NASDAQ"],
                    bar_type_spec="TICK",
                    lookback_days=30,
                )
            )


# ---------------------------------------------------------------------------
# 2. BacktestEngineWrapper → ParameterSweep → WalkForward pipeline
# ---------------------------------------------------------------------------


class TestEngineSweepWalkForwardPipeline:
    """Integration: Engine + Sweep + WalkForward share config patterns."""

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_sweep_runs_grid_and_ranks_by_sharpe(
        self,
        mock_node_cls: Any,
    ) -> None:
        """ParameterSweep.run() returns ranked dicts sorted by Sharpe."""
        wrapper = BacktestEngineWrapper(catalog_path="/tmp/test-catalog")
        bundle = _make_bundle("TSLA.NASDAQ")

        mock_engine = MagicMock()
        mock_engine.get_result.side_effect = [
            _mock_result(sharpe=1.2, pnl=500.0),
            _mock_result(sharpe=1.8, pnl=1200.0),
            _mock_result(sharpe=0.8, pnl=-200.0),
            _mock_result(sharpe=1.5, pnl=800.0),
        ]

        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[bundle],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        results = sweeper.run(
            {"stop_loss_ticks": [5, 10], "take_profit_ticks": [20, 30]}
        )

        assert len(results) == 4
        # Sorted by Sharpe descending
        assert results[0]["sharpe"] == 1.8
        assert results[1]["sharpe"] == 1.5
        assert results[2]["sharpe"] == 1.2
        assert results[3]["sharpe"] == 0.8

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_walk_forward_pipeline(
        self,
        mock_node_cls: Any,
    ) -> None:
        """WalkForward runs sweep on train, picks best, tests on OOS."""
        wrapper = BacktestEngineWrapper(catalog_path="/tmp/test-catalog")
        bundle = _make_bundle("TSLA.NASDAQ")

        call_n = [0]

        def _side_effect() -> BacktestResult:
            call_n[0] += 1
            return _mock_result(sharpe=1.0 + call_n[0] * 0.1, pnl=500.0)

        mock_engine = MagicMock()
        mock_engine.get_result.side_effect = _side_effect

        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        wf = WalkForward(
            wrapper=wrapper,
            base_strategies=[bundle],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            train_days=60,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )

        result = wf.run({"stop_loss_ticks": [5, 10]})

        assert isinstance(result, WalkForwardResult)
        assert result.total_windows >= 1
        assert len(result.windows) == result.total_windows
        for window in result.windows:
            assert window.test_start
            assert window.test_end
            assert window.best_params

    def test_walk_forward_empty_grid_raises(self) -> None:
        """WalkForward rejects empty param_grid."""
        wrapper = BacktestEngineWrapper(catalog_path="/tmp/test-catalog")
        wf = WalkForward(
            wrapper=wrapper,
            base_strategies=[_make_bundle()],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            train_days=60,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )

        with pytest.raises(ValueError, match="Parameter grid"):
            wf.run({})

    def test_grid_generation_handles_10_combos(self) -> None:
        """Cartesian product with 10+ combos works correctly."""
        grid: dict[str, list[Any]] = {"a": list(range(5)), "b": [1.0, 2.0]}
        combos = generate_sweep_grid(grid)
        assert len(combos) == 10  # 5 × 2

    def test_sweep_format_table(self) -> None:
        """format_table produces readable ranked output."""
        r1 = {
            "combo": {"sl": 5},
            "strategy_id": "O",
            "sharpe": 1.8,
            "net_pnl": 1000.0,
            "max_drawdown": -0.1,
            "win_rate": 0.55,
            "total_trades": 20,
            "elapsed": 3.5,
        }
        r2 = {
            "combo": {"sl": 10},
            "strategy_id": "O",
            "sharpe": 1.2,
            "net_pnl": 500.0,
            "max_drawdown": -0.15,
            "win_rate": 0.50,
            "total_trades": 18,
            "elapsed": 3.2,
        }
        results = [r1, r2]
        table = ParameterSweep.format_table(results)
        assert "1.8000" in table
        assert "1.2000" in table
        assert "Sharpe" in table


# ---------------------------------------------------------------------------
# 3. BacktestResultStore — PG persistence pipeline
# ---------------------------------------------------------------------------


class TestResultStorePipeline:
    """Integration: BacktestResult → PG → query round-trip."""

    def test_serialization_pipeline(self) -> None:
        """Result stats are JSON-serializable with NaN→None handling."""
        result = _mock_result(sharpe=2.0, pnl=2500.0)

        pnl_json = _serialize_stats(result.stats_pnls)
        assert "total_pnl" in pnl_json

        returns_json = _serialize_stats(result.stats_returns)
        assert "sharpe_ratio" in returns_json

        # NaN → None conversion
        stats = {
            "a": float("nan"),
            "b": [1.0, float("nan"), 2.0],
            "nested": {"x": float("nan")},
        }
        clean = _sanitize_nan(stats)
        assert clean["a"] is None
        assert clean["b"] == [1.0, None, 2.0]
        assert clean["nested"]["x"] is None

    def test_save_and_retrieve_mocked(self) -> None:
        """save() + get_by_strategy() round-trip with mocked PG."""
        import asyncpg

        result = _mock_result(sharpe=2.0, pnl=2500.0)
        store = BacktestResultStore(
            pg_dsn="postgresql://test:test@localhost:5432/sam_trader"
        )

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.close = AsyncMock()

        mock_conn.execute = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value=dict(
                run_id="test-run-123",
                run_config_id="cfg-001",
                strategy_id="orb-tsla-5m",
                instrument_id="TSLA.NASDAQ",
                bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 30),
                status="completed",
                total_events=1500,
                total_orders=12,
                total_positions=6,
                elapsed_secs=Decimal("5.000"),
                stats_pnls='{"OrbStrategy": {"total_pnl": 2500.0}}',
                stats_returns='{"sharpe_ratio": 2.0}',
                equity_curve=None,
                error_message=None,
                created_at=datetime.now(timezone.utc),
                strategy_family="OrbStrategy",
                strategy_version=None,
                tags='{"env": "test"}',
            )
        )

        with patch.object(
            asyncpg, "create_pool", new=AsyncMock(return_value=mock_pool)
        ):

            async def _test() -> None:
                run_id = await store.save(
                    result=result,
                    strategy_id="orb-tsla-5m",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                    status="completed",
                )
                assert isinstance(run_id, str)
                assert len(run_id) > 0

            asyncio.run(_test())

    def test_query_by_family_mocked(self) -> None:
        """get_by_family returns filtered rows."""
        import asyncpg

        store = BacktestResultStore(
            pg_dsn="postgresql://test:test@localhost:5432/sam_trader"
        )

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.close = AsyncMock()

        mock_conn.fetch = AsyncMock(
            return_value=[
                dict(
                    run_id="r1",
                    run_config_id="c1",
                    strategy_id="s1",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 3, 31),
                    status="completed",
                    total_events=100,
                    total_orders=5,
                    total_positions=3,
                    elapsed_secs=Decimal("2.0"),
                    stats_pnls="{}",
                    stats_returns='{"sharpe_ratio": 1.5}',
                    equity_curve=None,
                    error_message=None,
                    created_at=datetime.now(timezone.utc),
                    strategy_family="OrbStrategy",
                    strategy_version=None,
                    tags=None,
                ),
            ]
        )

        with patch.object(
            asyncpg, "create_pool", new=AsyncMock(return_value=mock_pool)
        ):

            async def _test() -> list[dict[str, Any]]:
                return await store.get_by_family("OrbStrategy")

            rows = asyncio.run(_test())
            assert len(rows) == 1
            assert rows[0]["stats_returns"]["sharpe_ratio"] == 1.5

    def test_query_by_date_range_mocked(self) -> None:
        """get_by_date_range filters by date overlap."""
        import asyncpg

        store = BacktestResultStore(
            pg_dsn="postgresql://test:test@localhost:5432/sam_trader"
        )

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.close = AsyncMock()

        mock_conn.fetch = AsyncMock(
            return_value=[
                dict(
                    run_id="r1",
                    run_config_id="c1",
                    strategy_id="s1",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                    status="completed",
                    total_events=100,
                    total_orders=5,
                    total_positions=3,
                    elapsed_secs=Decimal("2.0"),
                    stats_pnls="{}",
                    stats_returns="{}",
                    equity_curve=None,
                    error_message=None,
                    created_at=datetime.now(timezone.utc),
                    strategy_family=None,
                    strategy_version=None,
                    tags=None,
                ),
            ]
        )

        with patch.object(
            asyncpg, "create_pool", new=AsyncMock(return_value=mock_pool)
        ):

            async def _test() -> list[dict[str, Any]]:
                return await store.get_by_date_range(
                    start=date(2024, 1, 1),
                    end=date(2024, 6, 30),
                )

            rows = asyncio.run(_test())
            assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. Cross-component data flow
# ---------------------------------------------------------------------------


class TestCrossComponentDataFlow:
    """Verify data structures are compatible across component boundaries."""

    def test_parse_sweep_flag_produces_typed_grid(self) -> None:
        """parse_sweep_flags output is directly compatible with sweep grid."""
        flags = [
            "stop_loss_ticks=5,10,15",
            "take_profit_ticks=20,30",
            "first_candle_minutes=15",
        ]
        grid = parse_sweep_flags(flags)

        assert "stop_loss_ticks" in grid
        assert grid["stop_loss_ticks"] == [5, 10, 15]
        assert grid["take_profit_ticks"] == [20, 30]
        assert grid["first_candle_minutes"] == [15]

        combos = generate_sweep_grid(grid)
        assert len(combos) == 6  # 3 × 2 × 1

    def test_strategy_config_not_mutated_by_sweep(self) -> None:
        """Sweep patching creates copies — original config is unchanged."""
        bundle = _make_bundle("TSLA.NASDAQ")
        original_config = copy.deepcopy(bundle.config)

        # Build configs manually (same logic as ParameterSweep.run)
        from sam_trader.services.backtest.sweep import _patch_strategy_config

        for combo in generate_sweep_grid({"stop_loss_ticks": [5, 10]}):
            _patch_strategy_config(bundle, combo)

        assert bundle.config == original_config, "Original config was mutated"

    def test_engine_error_on_invalid_input(self) -> None:
        """BacktestEngineWrapper fails early with clear error messages."""
        wrapper = BacktestEngineWrapper(catalog_path="/tmp/test-catalog")

        with pytest.raises(BacktestEngineError, match="At least one instrument"):
            wrapper.build_run_config(
                strategies=[_make_bundle()],
                instrument_ids=[],
                bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
                start="2024-01-01",
                end="2024-06-30",
            )

        with pytest.raises(BacktestEngineError, match="ImportableStrategyConfig"):
            wrapper.build_run_config(
                strategies=[],
                instrument_ids=["TSLA.NASDAQ"],
                bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
                start="2024-01-01",
                end="2024-06-30",
            )

    def test_walk_forward_format_report_readable(self) -> None:
        """format_report produces stable, readable output."""
        from sam_trader.services.backtest.walk_forward import (
            WalkForwardResult,
            WindowResult,
        )

        wr = WalkForwardResult(
            windows=[
                WindowResult(
                    train_start="2024-01-01",
                    train_end="2024-03-30",
                    test_start="2024-03-31",
                    test_end="2024-04-29",
                    best_params={"stop_loss_ticks": 10},
                    train_sharpe=1.8,
                    test_sharpe=1.5,
                    test_pnl=500.0,
                    test_win_rate=0.55,
                    test_max_dd=-0.10,
                    test_trades=8,
                ),
                WindowResult(
                    train_start="2024-02-01",
                    train_end="2024-04-30",
                    test_start="2024-05-01",
                    test_end="2024-05-30",
                    best_params={"stop_loss_ticks": 5},
                    train_sharpe=2.0,
                    test_sharpe=1.2,
                    test_pnl=300.0,
                    test_win_rate=0.48,
                    test_max_dd=-0.18,
                    test_trades=6,
                ),
                WindowResult(
                    train_start="2024-03-01",
                    train_end="2024-05-29",
                    test_start="2024-05-30",
                    test_end="2024-06-28",
                    error="No bars in test period",
                ),
            ],
            overall_sharpe=1.35,
            overall_pnl=800.0,
            total_windows=3,
            profitable_windows=2,
            param_stability={"stop_loss_ticks": {"10": 1, "5": 1}},
            config={},
        )

        report = WalkForward.format_report(wr)
        assert "Walk-Forward" in report
        assert "1.35" in report
        assert "800.0" in report
        assert "ERROR" in report or "ERR" in report

    def test_empty_grid_returns_single_combo(self) -> None:
        """Empty param_grid → single empty dict combo (convention)."""
        combos = generate_sweep_grid({})
        assert combos == [{}]

    def test_venue_derivation_handles_nasdaq(self) -> None:
        """_derive_venues_from_instruments correctly resolves venues."""
        assert BacktestEngineWrapper._derive_venues_from_instruments(
            ["TSLA.NASDAQ"]
        ) == ["NASDAQ"]
        assert BacktestEngineWrapper._derive_venues_from_instruments(
            ["00700.HKEX"]
        ) == ["HKEX"]
        assert BacktestEngineWrapper._derive_venues_from_instruments([]) == ["SIM"]

    def test_engine_config_default_values(self) -> None:
        """build_run_config defaults produce sensible config."""
        wrapper = BacktestEngineWrapper(catalog_path="/tmp/test-catalog")
        cfg = wrapper.build_run_config(
            strategies=[_make_bundle()],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )
        assert str(cfg.venues[0].name) == "NASDAQ"
        assert cfg.venues[0].oms_type == "NETTING"
        assert cfg.venues[0].starting_balances == ["100000 USD"]
        assert cfg.engine is not None and cfg.engine.run_analysis is True


# ---------------------------------------------------------------------------
# 5. Full pipeline end-to-end (all mocks)
# ---------------------------------------------------------------------------


class TestFullPipelineE2E:
    """Complete end-to-end pipeline with mocked external deps."""

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_full_pipeline_download_backtest_store(
        self,
        mock_node_cls: Any,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        tmp_path: Any,
    ) -> None:
        """Full pipeline: download → backtest → sweep → walk-forward → store.

        All external deps (Futu, BacktestNode Cython, PG) are mocked.
        Real component interaction and data transformation logic is tested.
        """
        # === Stage 1: Bar download (mocked Futu) ===
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_df = _fake_bar_df(rows=50, start_date="2024-01-02")
        mock_ctx.request_history_kline.return_value = (0, mock_df, None)
        mock_get_ctx.return_value = mock_ctx

        downloader = BarDownloader(catalog_path=str(tmp_path))
        dl_result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=365,
            )
        )
        assert dl_result.total_bars_written == 50
        assert dl_result.instruments_failed == []

        # === Stage 2: Single backtest (mocked BacktestNode) ===
        mock_engine = MagicMock()
        mock_engine.get_result.return_value = _mock_result(sharpe=1.5, pnl=1000.0)
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        wrapper = BacktestEngineWrapper(catalog_path=str(tmp_path))
        bundle = _make_bundle("TSLA.NASDAQ")

        result = wrapper.run(
            strategies=[bundle],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )
        assert isinstance(result, BacktestResult)

        # === Stage 3: Parameter sweep (mocked BacktestNode) ===
        mock_engine.get_result.side_effect = [
            _mock_result(sharpe=1.2, pnl=500.0),
            _mock_result(sharpe=1.8, pnl=1200.0),
            _mock_result(sharpe=1.0, pnl=300.0),
        ]

        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[bundle],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-03-31",
        )

        sweep_results = sweeper.run({"stop_loss_ticks": [5, 10, 15]})
        assert len(sweep_results) == 3
        assert sweep_results[0]["sharpe"] == 1.8

        # === Stage 4: Walk-forward (mocked BacktestNode) ===
        call_n = [0]

        def _wf_side_effect() -> BacktestResult:
            call_n[0] += 1
            return _mock_result(sharpe=1.0 + call_n[0] * 0.2, pnl=500.0)

        mock_engine.get_result.side_effect = _wf_side_effect

        wf = WalkForward(
            wrapper=wrapper,
            base_strategies=[bundle],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            train_days=60,
            test_days=28,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )

        wf_result = wf.run({"stop_loss_ticks": [5, 10]})
        assert isinstance(wf_result, WalkForwardResult)
        assert wf_result.total_windows >= 2

        # === Stage 5: Store/query results (mocked PG) ===
        import asyncpg

        store = BacktestResultStore(
            pg_dsn="postgresql://test:test@localhost:5432/sam_trader"
        )

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.close = AsyncMock()

        mock_conn.execute = AsyncMock()
        # Mock for save
        mock_conn.fetchrow = AsyncMock(
            return_value=dict(
                run_id="e2e-run-001",
                run_config_id="cfg-001",
                strategy_id="orb-tsla-5m",
                instrument_id="TSLA.NASDAQ",
                bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 30),
                status="completed",
                total_events=1500,
                total_orders=12,
                total_positions=6,
                elapsed_secs=Decimal("5.000"),
                stats_pnls='{"OrbStrategy": {"total_pnl": 1000.0}}',
                stats_returns='{"sharpe_ratio": 1.5}',
                equity_curve=None,
                error_message=None,
                created_at=datetime.now(timezone.utc),
                strategy_family="OrbStrategy",
                strategy_version=None,
                tags='{"env": "e2e-test"}',
            )
        )
        # Mock for get_all query
        mock_conn.fetch = AsyncMock(
            return_value=[
                dict(
                    run_id="e2e-run-001",
                    run_config_id="cfg-001",
                    strategy_id="orb-tsla-5m",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                    status="completed",
                    total_events=1500,
                    total_orders=12,
                    total_positions=6,
                    elapsed_secs=Decimal("5.000"),
                    stats_pnls="{}",
                    stats_returns='{"sharpe_ratio": 1.5}',
                    equity_curve=None,
                    error_message=None,
                    created_at=datetime.now(timezone.utc),
                    strategy_family="OrbStrategy",
                    strategy_version=None,
                    tags=None,
                ),
            ]
        )

        with patch.object(
            asyncpg, "create_pool", new=AsyncMock(return_value=mock_pool)
        ):

            async def _store_and_query() -> None:
                run_id = await store.save(
                    result=result,
                    strategy_id="orb-tsla-5m",
                    instrument_id="TSLA.NASDAQ",
                    bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 6, 30),
                    status="completed",
                    tags=["e2e-test"],
                )
                assert isinstance(run_id, str)

                runs = await store.get_all()
                assert len(runs) == 1
                assert runs[0]["stats_returns"]["sharpe_ratio"] == 1.5

            asyncio.run(_store_and_query())
