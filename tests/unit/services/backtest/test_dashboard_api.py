"""Unit tests for backtest dashboard REST API handlers."""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.dashboard_api import (
    _build_strategy_from_body,
    _derive_config_path_from_strategy_path,
    _discover_bar_types,
    _generate_run_id,
    _get_catalog,
    _lookup_strategies_from_bundles,
    _resolve_strategies,
    _run_registry,
    _run_registry_lock,
    handle_backtest_catalog_instruments,
    handle_backtest_catalog_status,
    handle_backtest_catalog_strategies,
    handle_backtest_compare,
    handle_backtest_run,
    handle_backtest_run_status,
    handle_backtest_runs,
    handle_backtest_runs_detail,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_store_rows(*rows: dict[str, Any]) -> MagicMock:
    """Create a store mock that returns given rows from get_all and related methods."""
    store = MagicMock()
    store.get_all = MagicMock()
    store.get_by_run_id = MagicMock()
    store.get_by_run_ids = MagicMock()

    async def _get_all(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return list(rows)

    async def _get_by_run_id(run_id: str) -> dict[str, Any] | None:
        for r in rows:
            if r.get("run_id") == run_id:
                return r
        return None

    async def _get_by_run_ids(run_ids: list[str]) -> list[dict[str, Any]]:
        return [r for r in rows if r.get("run_id") in run_ids]

    store.get_all.side_effect = _get_all
    store.get_by_run_id.side_effect = _get_by_run_id
    store.get_by_run_ids.side_effect = _get_by_run_ids
    return store


def _make_row(
    run_id: str = "bt-001",
    strategy_id: str = "tsla-orb",
    instrument_id: str = "TSLA.NASDAQ",
    bar_type: str = "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
    start_date: date = date(2024, 1, 1),
    end_date: date = date(2024, 6, 30),
    status: str = "completed",
    total_events: int = 1000,
    total_orders: int = 20,
    total_positions: int = 10,
    elapsed_secs: float = 5.5,
    stats_returns: dict[str, Any] | None = None,
    stats_pnls: dict[str, Any] | None = None,
    equity_curve: list[dict] | None = None,
    strategy_family: str | None = "ORB",
    strategy_version: str | None = "1.0",
    tags: list[str] | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "total_events": total_events,
        "total_orders": total_orders,
        "total_positions": total_positions,
        "elapsed_secs": elapsed_secs,
        "stats_returns": stats_returns or {"Sharpe Ratio (252 days)": 1.5},
        "stats_pnls": stats_pnls or {"USD": {"PnL (total)": 5000.0}},
        "equity_curve": equity_curve or [],
        "error_message": None,
        "strategy_family": strategy_family,
        "strategy_version": strategy_version,
        "tags": tags or [],
        "created_at": created_at
        or datetime(2024, 6, 30, 12, 0, 0, tzinfo=timezone.utc),
    }


def _clear_run_registry() -> None:
    """Clear the in-memory run registry between tests."""
    with _run_registry_lock:
        _run_registry.clear()


# ---------------------------------------------------------------------------
# handle_backtest_run (POST /api/backtest/run)
# ---------------------------------------------------------------------------


class TestHandleBacktestRun:
    """Tests for POST /api/backtest/run — launch asynchronous backtest."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_importable_strategy(
        strategy_path: str = "sam_trader.strategies.orb:OrbStrategy",
        config_path: str = "sam_trader.strategies.orb:OrbStrategyConfig",
        config: dict[str, Any] | None = None,
    ) -> ImportableStrategyConfig:
        """Build a minimal ImportableStrategyConfig for test return values."""
        return ImportableStrategyConfig(
            strategy_path=strategy_path,
            config_path=config_path,
            config=config or {"bundle_id": "tsla-orb", "strategy_id": "US-tsla-orb"},
        )

    # ------------------------------------------------------------------
    # strategy resolution via bundles lookup
    # ------------------------------------------------------------------

    def test_minimal_body_with_bundles_lookup(self) -> None:
        """Launch with strategy_id resolved from bundles.yaml."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ) as mock_thread:
                    result = handle_backtest_run(body, catalog_path="data/catalog")

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert mock_thread.called

    def test_missing_strategy_id(self) -> None:
        """Missing strategy_id and strategy_path returns error."""
        result = handle_backtest_run(
            {
                "instrument_ids": ["TSLA.NASDAQ"],
                "start": "2024-01-01",
                "end": "2024-06-30",
            }
        )
        assert "error" in result

    def test_missing_instrument_ids(self) -> None:
        """Missing instrument_ids returns error."""
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            result = handle_backtest_run(
                {
                    "strategy_id": "tsla-orb",
                    "start": "2024-01-01",
                    "end": "2024-06-30",
                }
            )
        assert "error" in result
        assert "instrument_ids" in result["error"]

    def test_missing_dates(self) -> None:
        """Missing start or end returns error."""
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            result = handle_backtest_run(
                {
                    "strategy_id": "tsla-orb",
                    "instrument_ids": ["TSLA.NASDAQ"],
                }
            )
        assert "error" in result

    def test_auto_bar_types_no_catalog(self) -> None:
        """Auto-discovers bar types as default when catalog is unavailable."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ):
                    result = handle_backtest_run(body)

        assert result["run_id"].startswith("bt-")
        # Verify the run_registry has default bar types
        with _run_registry_lock:
            entry = _run_registry.get(result["run_id"])
            assert entry is not None
            assert entry["bar_types"] == ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"]

    def test_run_registry_created(self) -> None:
        """A new backtest adds an entry to the run registry."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ):
                    result = handle_backtest_run(body)

        with _run_registry_lock:
            entry = _run_registry.get(result["run_id"])
            assert entry is not None
            assert entry["status"] == "started"
            assert entry["strategy_id"] == "tsla-orb"
            assert entry["instrument_ids"] == ["TSLA.NASDAQ"]

    # ------------------------------------------------------------------
    # strategy resolution via direct strategy_path in body
    # ------------------------------------------------------------------

    def test_direct_strategy_path_resolution(self) -> None:
        """POST with strategy_path + config_path builds config directly."""
        body = {
            "strategy_path": "sam_trader.strategies.orb:OrbStrategy",
            "config_path": "sam_trader.strategies.orb:OrbStrategyConfig",
            "config": {"instrument_id": "TSLA.NASDAQ"},
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        _clear_run_registry()
        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=None,
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api.threading.Thread",
            ) as mock_thread:
                result = handle_backtest_run(body)

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert mock_thread.called

    def test_direct_strategy_path_auto_config_path(self) -> None:
        """strategy_path without config_path auto-derives Config class path."""
        body = {
            "strategy_path": "sam_trader.strategies.orb:OrbStrategy",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        _clear_run_registry()
        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=None,
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api.threading.Thread",
            ) as mock_thread:
                result = handle_backtest_run(body)

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert mock_thread.called


# ---------------------------------------------------------------------------
# handle_backtest_run_status (GET /api/backtest/run/<id>/status)
# ---------------------------------------------------------------------------


class TestHandleBacktestRunStatus:
    """Tests for GET /api/backtest/run/<id>/status."""

    def test_existing_run(self) -> None:
        """Return status for an existing run."""
        _clear_run_registry()
        with _run_registry_lock:
            _run_registry["bt-abc"] = {
                "run_id": "bt-abc",
                "status": "running",
                "progress_pct": 45,
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:01:00",
            }

        result = handle_backtest_run_status("bt-abc")
        assert result["run_id"] == "bt-abc"
        assert result["status"] == "running"
        assert result["progress_pct"] == 45

    def test_unknown_run(self) -> None:
        """Return error for unknown run."""
        _clear_run_registry()
        result = handle_backtest_run_status("bt-nonexistent")
        assert "error" in result

    def test_completed_run_with_result(self) -> None:
        """Completed run returns result data."""
        _clear_run_registry()
        with _run_registry_lock:
            _run_registry["bt-xyz"] = {
                "run_id": "bt-xyz",
                "status": "completed",
                "progress_pct": 100,
                "result": {"sharpe_ratio": 2.1, "total_pnl": 10000.0},
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:02:00",
            }

        result = handle_backtest_run_status("bt-xyz")
        assert result["status"] == "completed"
        assert result["progress_pct"] == 100
        assert result["result"]["sharpe_ratio"] == 2.1

    def test_failed_run_with_error(self) -> None:
        """Failed run returns error message."""
        _clear_run_registry()
        with _run_registry_lock:
            _run_registry["bt-fail"] = {
                "run_id": "bt-fail",
                "status": "failed",
                "progress_pct": 50,
                "error": "Catalog not found",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:01:00",
            }

        result = handle_backtest_run_status("bt-fail")
        assert result["status"] == "failed"
        assert result["error"] == "Catalog not found"


# ---------------------------------------------------------------------------
# handle_backtest_runs (GET /api/backtest/runs)
# ---------------------------------------------------------------------------


class TestHandleBacktestRuns:
    """Tests for GET /api/backtest/runs — list past runs."""

    def test_empty_store(self) -> None:
        """Empty store returns empty list."""
        store = _mock_store_rows()
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs(limit=10)
        assert result == []

    def test_returns_summary_fields(self) -> None:
        """Each result has summary fields, no heavy JSONB."""
        row = _make_row()
        store = _mock_store_rows(row)
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs()

        assert len(result) == 1
        r = result[0]
        assert r["run_id"] == "bt-001"
        assert r["strategy_id"] == "tsla-orb"
        assert r["instrument_id"] == "TSLA.NASDAQ"
        assert r["bar_type"] == "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        assert r["start_date"] == "2024-01-01"
        assert r["end_date"] == "2024-06-30"
        assert r["status"] == "completed"
        assert r["elapsed_secs"] == 5.5
        assert r["strategy_family"] == "ORB"
        # Heavy fields should NOT be in summary
        assert "stats_returns" not in r
        assert "stats_pnls" not in r
        assert "equity_curve" not in r

    def test_limit_parameter(self) -> None:
        """limit parameter is passed to store."""
        store = _mock_store_rows(_make_row("a"), _make_row("b"), _make_row("c"))
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            handle_backtest_runs(limit=5)
        store.get_all.assert_called_once()
        # Check limit was passed
        call_kwargs = store.get_all.call_args
        assert call_kwargs.kwargs.get("limit") == 5

    def test_multiple_runs(self) -> None:
        """Multiple runs are returned."""
        rows = [
            _make_row("bt-001", strategy_id="tsla-orb"),
            _make_row("bt-002", strategy_id="aapl-mom"),
            _make_row("bt-003", strategy_id="nvda-orb"),
        ]
        store = _mock_store_rows(*rows)
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs()
        assert len(result) == 3

    def test_db_error_returns_empty(self) -> None:
        """Database error returns empty list gracefully."""
        store = MagicMock()

        async def _raise(*_: Any, **__: Any) -> list:
            raise RuntimeError("DB down")

        store.get_all.side_effect = _raise
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs()
        assert result == []


# ---------------------------------------------------------------------------
# handle_backtest_runs_detail (GET /api/backtest/runs/<id>)
# ---------------------------------------------------------------------------


class TestHandleBacktestRunsDetail:
    """Tests for GET /api/backtest/runs/<id> — full result."""

    def test_existing_run(self) -> None:
        """Return full detail including stats and equity curve."""
        equity = [{"timestamp": "2024-01-01", "value": 100000}]
        row = _make_row(
            equity_curve=equity,
            stats_returns={"Sharpe Ratio (252 days)": 1.8},
            stats_pnls={"tsla-orb": {"PnL (total)": 7500.0}},
        )
        store = _mock_store_rows(row)
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs_detail("bt-001")

        assert result["run_id"] == "bt-001"
        assert result["stats_returns"]["Sharpe Ratio (252 days)"] == 1.8
        assert result["equity_curve"] == equity
        assert result["total_events"] == 1000
        assert result["strategy_version"] == "1.0"

    def test_unknown_run(self) -> None:
        """Unknown run_id returns error."""
        store = _mock_store_rows()
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs_detail("bt-unknown")

        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_null_dates_in_row(self) -> None:
        """Rows with None dates show as None strings."""
        row = _make_row(start_date=None, end_date=None)  # type: ignore[arg-type]
        row["start_date"] = None
        row["end_date"] = None
        store = _mock_store_rows(row)
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs_detail("bt-001")

        assert result["start_date"] is None
        assert result["end_date"] is None

    def test_db_error(self) -> None:
        """Database error returns error dict."""
        store = MagicMock()

        async def _raise(*_: Any, **__: Any) -> dict | None:
            raise RuntimeError("DB down")

        store.get_by_run_id.side_effect = _raise
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_runs_detail("bt-001")

        assert "error" in result
        assert "Database error" in result["error"]


# ---------------------------------------------------------------------------
# handle_backtest_compare (GET /api/backtest/compare?runs=id1,id2)
# ---------------------------------------------------------------------------


class TestHandleBacktestCompare:
    """Tests for GET /api/backtest/compare — side-by-side metrics."""

    def test_two_runs_comparison(self) -> None:
        """Compare two runs returns side-by-side metrics."""
        rows = [
            _make_row(
                "bt-001",
                strategy_id="tsla-orb",
                stats_returns={
                    "Sharpe Ratio (252 days)": 1.5,
                    "Max Drawdown": 0.10,
                },
                stats_pnls={"tsla-orb": {"PnL (total)": 5000.0}},
            ),
            _make_row(
                "bt-002",
                strategy_id="aapl-mom",
                stats_returns={
                    "Sharpe Ratio (252 days)": 0.8,
                    "Max Drawdown": 0.25,
                },
                stats_pnls={"aapl-mom": {"PnL (total)": -1000.0}},
            ),
        ]
        store = _mock_store_rows(*rows)
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_compare(["bt-001", "bt-002"])

        assert "runs" in result
        assert "comparison" in result
        assert len(result["runs"]) == 2
        assert result["runs"]["bt-001"]["strategy_id"] == "tsla-orb"
        assert result["runs"]["bt-002"]["strategy_id"] == "aapl-mom"

        # Comparison table
        comparison = result["comparison"]
        sharpe_row = [r for r in comparison if r["metric"] == "sharpe_ratio"][0]
        assert sharpe_row["bt-001"] == 1.5
        assert sharpe_row["bt-002"] == 0.8

    def test_missing_runs(self) -> None:
        """Requested run that doesn't exist gets error marker."""
        store = _mock_store_rows(_make_row("bt-001"))
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_compare(["bt-001", "bt-missing"])

        assert result["runs"]["bt-001"]["strategy_id"] == "tsla-orb"
        assert result["runs"]["bt-missing"] == {"error": "Not found"}

    def test_empty_run_ids(self) -> None:
        """No run_ids provided returns nothing."""
        store = _mock_store_rows()
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_compare([])

        # get_by_run_ids should not be called with empty list
        assert "comparison" in result or "error" in result

    def test_db_error_returns_error(self) -> None:
        """Database error returns error dict."""
        store = MagicMock()

        async def _raise(*_: Any, **__: Any) -> list:
            raise RuntimeError("DB down")

        store.get_by_run_ids.side_effect = _raise
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_compare(["bt-001"])

        assert "error" in result

    def test_all_metrics_included(self) -> None:
        """Comparison table includes all expected metrics."""
        rows = [
            _make_row("bt-001", stats_returns={"Sharpe Ratio (252 days)": 1.0}),
            _make_row("bt-002", stats_returns={"Sharpe Ratio (252 days)": 1.0}),
        ]
        store = _mock_store_rows(*rows)
        with patch(
            "sam_trader.services.backtest.dashboard_api.BacktestResultStore",
            return_value=store,
        ):
            result = handle_backtest_compare(["bt-001", "bt-002"])

        metrics = [r["metric"] for r in result["comparison"]]
        assert "sharpe_ratio" in metrics
        assert "sortino_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "profit_factor" in metrics
        assert "expectancy" in metrics
        assert "total_pnl" in metrics
        assert "cagr" in metrics
        assert "calmar_ratio" in metrics
        assert "volatility" in metrics
        assert "total_events" in metrics
        assert "total_orders" in metrics
        assert "total_positions" in metrics
        assert "elapsed_secs" in metrics


# ---------------------------------------------------------------------------
# handle_backtest_catalog_instruments
# ---------------------------------------------------------------------------


class TestHandleBacktestCatalogInstruments:
    """Tests for GET /api/backtest/catalog/instruments."""

    def test_no_catalog_directory(self) -> None:
        """Non-existent catalog returns empty list."""
        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=None,
        ):
            result = handle_backtest_catalog_instruments(catalog_path="/nonexistent")
        assert result == []

    def test_catalog_instruments(self) -> None:
        """Catalog with instruments returns list with full bar type strings."""
        mock_catalog = MagicMock()
        mock_instr_a = MagicMock()
        mock_instr_a.id = "TSLA.NASDAQ"
        mock_instr_b = MagicMock()
        mock_instr_b.id = "AAPL.NASDAQ"
        mock_catalog.instruments.return_value = [mock_instr_a, mock_instr_b]
        mock_catalog.path = "/fake/catalog"
        mock_catalog.query_first_timestamp.return_value = "2024-01-01T00:00:00"
        mock_catalog.query_last_timestamp.return_value = "2024-06-30T00:00:00"

        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=mock_catalog,
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._discover_bar_types",
                return_value=[
                    "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                    "TSLA.NASDAQ-1-HOUR-LAST-EXTERNAL",
                ],
            ):
                result = handle_backtest_catalog_instruments()

        assert len(result) == 2
        assert result[0]["instrument_id"] == "TSLA.NASDAQ"
        assert "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL" in result[0]["bar_types"]
        assert result[1]["instrument_id"] == "AAPL.NASDAQ"

    def test_catalog_enumeration_error(self) -> None:
        """Error during catalog enumeration returns empty list."""
        mock_catalog = MagicMock()
        mock_catalog.instruments.side_effect = RuntimeError("Permission denied")
        mock_catalog.path = "/fake/catalog"

        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=mock_catalog,
        ):
            result = handle_backtest_catalog_instruments()

        assert result == []


# ---------------------------------------------------------------------------
# handle_backtest_catalog_strategies
# ---------------------------------------------------------------------------


class TestHandleBacktestCatalogStrategies:
    """Tests for GET /api/backtest/catalog/strategies."""

    def test_returns_enabled_bundles(self) -> None:
        """Enabled bundles are returned with all metadata fields."""
        mock_configs = [
            ImportableStrategyConfig(
                strategy_path="sam_trader.strategies.orb:OrbStrategy",
                config_path="sam_trader.strategies.orb:OrbStrategyConfig",
                config={
                    "bundle_id": "tsla-orb",
                    "instrument_id": "TSLA.NASDAQ",
                    "venue": "FUTU",
                    "market": "US",
                    "family": "ORB",
                },
            ),
            ImportableStrategyConfig(
                strategy_path="sam_trader.strategies.momentum:MomentumStrategy",
                config_path="sam_trader.strategies.momentum:MomentumStrategyConfig",
                config={
                    "bundle_id": "aapl-mom",
                    "instrument_id": "AAPL.NASDAQ",
                    "venue": "IB",
                    "market": "US",
                },
            ),
        ]
        with patch(
            "sam_trader.services.backtest.dashboard_api.load_bundles",
            return_value=mock_configs,
        ):
            result = handle_backtest_catalog_strategies()

        assert len(result) == 2
        r0 = result[0]
        assert r0["bundle_id"] == "tsla-orb"
        assert r0["strategy_path"] == "sam_trader.strategies.orb:OrbStrategy"
        assert r0["instrument_id"] == "TSLA.NASDAQ"
        assert r0["venue"] == "FUTU"
        assert r0["market"] == "US"
        assert r0["family"] == "ORB"
        assert r0["enabled"] is True

        r1 = result[1]
        assert r1["bundle_id"] == "aapl-mom"
        assert r1["family"] is None
        assert r1["enabled"] is True

    def test_missing_file_returns_empty(self) -> None:
        """Missing bundles file returns empty list gracefully."""
        with patch(
            "sam_trader.services.backtest.dashboard_api.load_bundles",
            side_effect=RuntimeError("file not found"),
        ):
            result = handle_backtest_catalog_strategies()

        assert result == []

    def test_empty_bundles_returns_empty(self) -> None:
        """No enabled bundles returns empty list."""
        with patch(
            "sam_trader.services.backtest.dashboard_api.load_bundles",
            return_value=[],
        ):
            result = handle_backtest_catalog_strategies()

        assert result == []


# ---------------------------------------------------------------------------
# handle_backtest_catalog_status
# ---------------------------------------------------------------------------


class TestHandleBacktestCatalogStatus:
    """Tests for GET /api/backtest/catalog/status."""

    def test_missing_catalog_directory(self) -> None:
        """Missing catalog directory returns catalog_exists=False with message."""
        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=None,
        ):
            result = handle_backtest_catalog_status()

        assert result["total_instruments"] == 0
        assert result["oldest_bar"] is None
        assert result["newest_bar"] is None
        assert result["catalog_exists"] is False
        assert "sam download-bars" in result["message"]

    def test_empty_catalog_directory(self) -> None:
        """Empty catalog directory returns catalog_exists=True with message."""
        mock_catalog = MagicMock()
        mock_catalog.instruments.return_value = []
        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=mock_catalog,
        ):
            result = handle_backtest_catalog_status()

        assert result["total_instruments"] == 0
        assert result["oldest_bar"] is None
        assert result["newest_bar"] is None
        assert result["catalog_exists"] is True
        assert "sam download-bars" in result["message"]

    def test_catalog_with_data(self) -> None:
        """Catalog with instruments returns aggregate stats."""
        mock_catalog = MagicMock()
        mock_instr = MagicMock()
        mock_instr.id = "TSLA.NASDAQ"
        mock_catalog.instruments.return_value = [mock_instr]
        mock_catalog.path = "/fake/catalog"
        mock_catalog.query_first_timestamp.return_value = "2024-01-01T00:00:00"
        mock_catalog.query_last_timestamp.return_value = "2024-06-30T00:00:00"

        with patch(
            "sam_trader.services.backtest.dashboard_api._get_catalog",
            return_value=mock_catalog,
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._discover_bar_types",
                return_value=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            ):
                result = handle_backtest_catalog_status()

        assert result["total_instruments"] == 1
        assert result["oldest_bar"] is not None
        assert result["newest_bar"] is not None
        assert result["catalog_exists"] is True
        assert result["message"] is None


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------


class TestGenerateRunId:
    """Tests for _generate_run_id."""

    def test_generates_unique_ids(self) -> None:
        """Each call produces a unique ID."""
        ids = {_generate_run_id() for _ in range(100)}
        assert len(ids) == 100

    def test_prefix_is_bt(self) -> None:
        """IDs start with bt-."""
        for _ in range(10):
            assert _generate_run_id().startswith("bt-")


# ---------------------------------------------------------------------------
# Strategy resolution helpers
# ---------------------------------------------------------------------------


class TestResolveStrategies:
    """Tests for _resolve_strategies — body → ImportableStrategyConfig."""

    def test_direct_strategy_path_takes_priority(self) -> None:
        """When strategy_path is provided, direct config is used."""
        body = {
            "strategy_path": "sam_trader.strategies.momentum:MomentumStrategy",
            "config_path": "sam_trader.strategies.momentum:MomentumStrategyConfig",
            "config": {"instrument_id": "NVDA.NASDAQ"},
            "strategy_id": "ignored-bundle-id",
        }
        strategies, error = _resolve_strategies(body)

        assert error is None
        assert strategies is not None
        assert len(strategies) == 1
        assert (
            strategies[0].strategy_path
            == "sam_trader.strategies.momentum:MomentumStrategy"
        )
        assert (
            strategies[0].config_path
            == "sam_trader.strategies.momentum:MomentumStrategyConfig"
        )
        assert strategies[0].config["instrument_id"] == "NVDA.NASDAQ"

    def test_falls_back_to_bundles_when_no_strategy_path(self) -> None:
        """When only strategy_id is provided, bundles.yaml is used."""
        body = {"strategy_id": "orb-aggressive-tsla"}
        mock_strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={"bundle_id": "orb-aggressive-tsla"},
        )
        with patch(
            "sam_trader.services.backtest.dashboard_api"
            "._lookup_strategies_from_bundles",
            return_value=[mock_strategy],
        ):
            strategies, error = _resolve_strategies(body)

        assert error is None
        assert strategies is not None
        assert len(strategies) == 1

    def test_bundle_not_found_returns_error(self) -> None:
        """When strategy_id doesn't match any bundle, returns error."""
        body = {"strategy_id": "nonexistent-bundle"}
        with patch(
            "sam_trader.services.backtest.dashboard_api"
            "._lookup_strategies_from_bundles",
            return_value=None,
        ):
            strategies, error = _resolve_strategies(body)

        assert strategies is None
        assert error is not None
        assert "not found" in error.lower()

    def test_neither_provided_returns_error(self) -> None:
        """When neither strategy_id nor strategy_path is provided, error."""
        body: dict[str, Any] = {"instrument_ids": ["TSLA.NASDAQ"]}
        strategies, error = _resolve_strategies(body)

        assert strategies is None
        assert error is not None
        assert "strategy_id" in error


class TestLookupStrategiesFromBundles:
    """Tests for _lookup_strategies_from_bundles — bundles.yaml lookup."""

    def test_match_by_bundle_id(self) -> None:
        """Matches by bundle_id in config."""
        mock_configs = [
            ImportableStrategyConfig(
                strategy_path="sp",
                config_path="cp",
                config={"bundle_id": "tsla-orb", "strategy_id": "US-tsla-orb"},
            ),
            ImportableStrategyConfig(
                strategy_path="sp",
                config_path="cp",
                config={"bundle_id": "aapl-orb", "strategy_id": "US-aapl-orb"},
            ),
        ]
        with patch(
            "sam_trader.services.backtest.dashboard_api.load_bundles",
            return_value=mock_configs,
        ):
            result = _lookup_strategies_from_bundles("tsla-orb")

        assert result is not None
        assert len(result) == 1
        assert result[0].config["bundle_id"] == "tsla-orb"

    def test_match_by_strategy_id(self) -> None:
        """Matches by market-prefixed strategy_id for backward compat."""
        mock_configs = [
            ImportableStrategyConfig(
                strategy_path="sp",
                config_path="cp",
                config={"bundle_id": "rdw-1m", "strategy_id": "US-rdw-1m"},
            ),
        ]
        with patch(
            "sam_trader.services.backtest.dashboard_api.load_bundles",
            return_value=mock_configs,
        ):
            result = _lookup_strategies_from_bundles("US-rdw-1m")

        assert result is not None
        assert len(result) == 1
        assert result[0].config["bundle_id"] == "rdw-1m"

    def test_no_match_returns_none(self) -> None:
        """No matching bundle returns None."""
        mock_configs: list[ImportableStrategyConfig] = []
        with patch(
            "sam_trader.services.backtest.dashboard_api.load_bundles",
            return_value=mock_configs,
        ):
            result = _lookup_strategies_from_bundles("unknown")

        assert result is None

    def test_missing_file_returns_none(self) -> None:
        """When bundles file doesn't exist, returns None."""
        with patch("pathlib.Path.exists", return_value=False):
            result = _lookup_strategies_from_bundles(
                "tsla-orb", bundles_path="/nonexistent/bundles.yaml"
            )

        assert result is None

    def test_load_error_returns_none(self) -> None:
        """When bundles file can't be parsed, returns None."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch(
                "sam_trader.services.backtest.dashboard_api.load_bundles",
                side_effect=RuntimeError("invalid YAML"),
            ):
                result = _lookup_strategies_from_bundles("tsla-orb")

        assert result is None


class TestBuildStrategyFromBody:
    """Tests for _build_strategy_from_body — direct ImportableStrategyConfig."""

    def test_full_fields(self) -> None:
        """All fields provided builds a complete config."""
        body = {
            "strategy_path": "sam_trader.strategies.orb:OrbStrategy",
            "config_path": "sam_trader.strategies.orb:OrbStrategyConfig",
            "config": {"instrument_id": "TSLA.NASDAQ", "trade_size": 10},
        }
        result = _build_strategy_from_body(body)

        assert result is not None
        assert result.strategy_path == "sam_trader.strategies.orb:OrbStrategy"
        assert result.config_path == "sam_trader.strategies.orb:OrbStrategyConfig"
        assert result.config["instrument_id"] == "TSLA.NASDAQ"
        assert result.config["trade_size"] == 10

    def test_auto_config_path(self) -> None:
        """config_path auto-derived from strategy_path when omitted."""
        body = {
            "strategy_path": "sam_trader.strategies.momentum:MomentumStrategy",
        }
        result = _build_strategy_from_body(body)

        assert result is not None
        assert (
            result.config_path
            == "sam_trader.strategies.momentum:MomentumStrategyConfig"
        )

    def test_no_strategy_path_returns_none(self) -> None:
        """When strategy_path is missing, returns None."""
        body: dict[str, Any] = {"config": {}}
        result = _build_strategy_from_body(body)
        assert result is None

    def test_empty_config_defaults_to_empty_dict(self) -> None:
        """Missing config field defaults to empty dict."""
        body = {"strategy_path": "sam_trader.strategies.orb:OrbStrategy"}
        result = _build_strategy_from_body(body)

        assert result is not None
        assert result.config == {}


class TestDeriveConfigPath:
    """Tests for _derive_config_path_from_strategy_path."""

    def test_appends_config_suffix(self) -> None:
        """Derives config class name by appending 'Config'."""
        result = _derive_config_path_from_strategy_path(
            "sam_trader.strategies.orb:OrbStrategy"
        )
        assert result == "sam_trader.strategies.orb:OrbStrategyConfig"

    def test_preserves_module_path(self) -> None:
        """Module path is unchanged."""
        result = _derive_config_path_from_strategy_path("a.b.c:MyStrategy")
        assert result == "a.b.c:MyStrategyConfig"


class TestGetCatalog:
    """Tests for _get_catalog."""

    def test_nonexistent_path(self) -> None:
        """Returns None for nonexistent path."""
        result = _get_catalog("/definitely/not/a/real/catalog")
        assert result is None


class TestDiscoverBarTypes:
    """Tests for _discover_bar_types."""

    def test_no_data_dir(self) -> None:
        """Returns empty when data/bar doesn't exist."""
        catalog = MagicMock()
        catalog.path = "/fake/catalog"
        # Mock Path.exists to return False for data/bar
        with patch("pathlib.Path.exists", return_value=False):
            result = _discover_bar_types(catalog, "TSLA.NASDAQ")
        assert result == []

    def test_discover_bar_types(self) -> None:
        """Returns full bar type strings from filenames."""
        catalog = MagicMock()
        catalog.path = "/fake/catalog"

        # Create fake Path.glob results
        fake_files = [
            Path("/fake/catalog/data/bar/TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet"),
            Path("/fake/catalog/data/bar/TSLA.NASDAQ-1-HOUR-LAST-EXTERNAL.parquet"),
            Path("/fake/catalog/data/bar/AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet"),
        ]

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.glob", return_value=fake_files):
                result = _discover_bar_types(catalog, "TSLA.NASDAQ")

        assert len(result) == 2
        assert "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL" in result
        assert "TSLA.NASDAQ-1-HOUR-LAST-EXTERNAL" in result
        # AAPL should NOT be included
        assert "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL" not in result

    def test_discover_bar_types_with_real_files(self, tmp_path: Path) -> None:
        """Scan actual parquet files on disk and return full bar type strings."""
        catalog = MagicMock()
        bar_dir = tmp_path / "data" / "bar"
        bar_dir.mkdir(parents=True)
        # Create dummy parquet files for multiple instruments and bar types
        (bar_dir / "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet").write_text("")
        (bar_dir / "TSLA.NASDAQ-1-HOUR-LAST-EXTERNAL.parquet").write_text("")
        (bar_dir / "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet").write_text("")
        (bar_dir / "AAPL.NASDAQ-15-MINUTE-LAST-EXTERNAL.parquet").write_text("")
        (bar_dir / "00700.HKEX-1-MINUTE-LAST-EXTERNAL.parquet").write_text("")
        catalog.path = str(tmp_path)

        tsla = _discover_bar_types(catalog, "TSLA.NASDAQ")
        assert tsla == [
            "TSLA.NASDAQ-1-HOUR-LAST-EXTERNAL",
            "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
        ]

        aapl = _discover_bar_types(catalog, "AAPL.NASDAQ")
        assert aapl == [
            "AAPL.NASDAQ-15-MINUTE-LAST-EXTERNAL",
            "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
        ]

        hk = _discover_bar_types(catalog, "00700.HKEX")
        assert hk == ["00700.HKEX-1-MINUTE-LAST-EXTERNAL"]

    def test_external_suffix_preserved(self, tmp_path: Path) -> None:
        """The -EXTERNAL (or -INTERNAL) suffix is preserved in the bar type."""
        catalog = MagicMock()
        bar_dir = tmp_path / "data" / "bar"
        bar_dir.mkdir(parents=True)
        (bar_dir / "NVDA.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet").write_text("")
        (bar_dir / "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL.parquet").write_text("")
        catalog.path = str(tmp_path)

        result = _discover_bar_types(catalog, "NVDA.NASDAQ")
        assert result == [
            "NVDA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL",
        ]


# ---------------------------------------------------------------------------
# Walk-forward tests
# ---------------------------------------------------------------------------


class TestHandleBacktestRunWalkForward:
    """Tests for POST /api/backtest/run with walk_forward=true."""

    @staticmethod
    def _make_importable_strategy(
        strategy_path: str = "sam_trader.strategies.orb:OrbStrategy",
        config_path: str = "sam_trader.strategies.orb:OrbStrategyConfig",
        config: dict[str, Any] | None = None,
    ) -> ImportableStrategyConfig:
        return ImportableStrategyConfig(
            strategy_path=strategy_path,
            config_path=config_path,
            config=config or {"bundle_id": "tsla-orb", "strategy_id": "US-tsla-orb"},
        )

    def test_walk_forward_returns_mode(self) -> None:
        """Walk-forward request returns mode=walk_forward."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-12-31",
            "walk_forward": True,
            "train_days": 90,
            "test_days": 30,
            "sweep_flags": ["stop_loss_ticks=5,10"],
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ) as mock_thread:
                    result = handle_backtest_run(body, catalog_path="data/catalog")

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert result["mode"] == "walk_forward"
        assert mock_thread.called

    def test_walk_forward_missing_sweep_flags(self) -> None:
        """Walk-forward without sweep parameters returns error."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-12-31",
            "walk_forward": True,
            "train_days": 90,
            "test_days": 30,
        }
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            result = handle_backtest_run(body)

        assert "error" in result
        assert "sweep" in result["error"].lower()

    def test_walk_forward_invalid_train_days(self) -> None:
        """Walk-forward with non-numeric train_days returns error."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-12-31",
            "walk_forward": True,
            "train_days": "abc",
            "test_days": 30,
            "sweep_flags": ["stop_loss_ticks=5,10"],
        }
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            result = handle_backtest_run(body)

        assert "error" in result
        assert "Invalid" in result["error"]

    def test_plain_backtest_returns_mode_backtest(self) -> None:
        """Plain backtest returns mode=backtest."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ):
                    result = handle_backtest_run(body)

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert result.get("mode") == "backtest"


class TestHandleBacktestRunSweep:
    """Tests for POST /api/backtest/run with sweep-only mode."""

    @staticmethod
    def _make_importable_strategy(
        strategy_path: str = "sam_trader.strategies.orb:OrbStrategy",
        config_path: str = "sam_trader.strategies.orb:OrbStrategyConfig",
        config: dict[str, Any] | None = None,
    ) -> ImportableStrategyConfig:
        return ImportableStrategyConfig(
            strategy_path=strategy_path,
            config_path=config_path,
            config=config or {"bundle_id": "tsla-orb", "strategy_id": "US-tsla-orb"},
        )

    def test_sweep_returns_mode_sweep(self) -> None:
        """Sweep request returns mode=sweep."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
            "sweep_flags": ["stop_loss_ticks=5,10"],
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ) as mock_thread:
                    result = handle_backtest_run(body, catalog_path="data/catalog")

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert result["mode"] == "sweep"
        assert mock_thread.called

    def test_sweep_with_sweep_params_format(self) -> None:
        """Sweep request with sweep_params dict returns mode=sweep."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-06-30",
            "sweep_params": {"stop_loss_ticks": [5, 10]},
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ) as mock_thread:
                    result = handle_backtest_run(body)

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert result["mode"] == "sweep"
        assert mock_thread.called

    def test_sweep_and_walk_forward_prefers_walk_forward(self) -> None:
        """When both sweep and walk-forward are requested, walk-forward wins."""
        body = {
            "strategy_id": "tsla-orb",
            "instrument_ids": ["TSLA.NASDAQ"],
            "start": "2024-01-01",
            "end": "2024-12-31",
            "walk_forward": True,
            "train_days": 90,
            "test_days": 30,
            "sweep_flags": ["stop_loss_ticks=5,10"],
        }
        _clear_run_registry()
        strategy = self._make_importable_strategy()
        with patch(
            "sam_trader.services.backtest.dashboard_api._resolve_strategies",
            return_value=([strategy], None),
        ):
            with patch(
                "sam_trader.services.backtest.dashboard_api._get_catalog",
                return_value=None,
            ):
                with patch(
                    "sam_trader.services.backtest.dashboard_api.threading.Thread",
                ) as mock_thread:
                    result = handle_backtest_run(body)

        assert result["run_id"].startswith("bt-")
        assert result["status"] == "started"
        assert result["mode"] == "walk_forward"
        assert mock_thread.called


class TestParseSweepBody:
    """Tests for _parse_sweep_body."""

    def test_sweep_flags_format(self) -> None:
        """CLI-style sweep_flags are parsed into a param grid."""
        from sam_trader.services.backtest.dashboard_api import _parse_sweep_body

        body = {"sweep_flags": ["stop_loss_ticks=5,10,15", "take_profit_ticks=20,30"]}
        grid = _parse_sweep_body(body)
        assert grid == {"stop_loss_ticks": [5, 10, 15], "take_profit_ticks": [20, 30]}

    def test_sweep_params_format(self) -> None:
        """Pre-parsed sweep_params dict is returned as-is."""
        from sam_trader.services.backtest.dashboard_api import _parse_sweep_body

        body = {"sweep_params": {"foo": [1, 2], "bar": ["a", "b"]}}
        grid = _parse_sweep_body(body)
        assert grid == {"foo": [1, 2], "bar": ["a", "b"]}

    def test_empty_body(self) -> None:
        """Missing sweep fields return empty dict."""
        from sam_trader.services.backtest.dashboard_api import _parse_sweep_body

        assert _parse_sweep_body({}) == {}

    def test_invalid_sweep_flags_logged(self) -> None:
        """Invalid sweep_flags are logged and return empty dict."""
        from sam_trader.services.backtest.dashboard_api import _parse_sweep_body

        body = {"sweep_flags": ["no_equals_sign"]}
        grid = _parse_sweep_body(body)
        assert grid == {}


class TestRunRegistryThreadSafety:
    """Tests for the in-memory run registry thread safety."""

    def test_concurrent_writes(self) -> None:
        """Multiple threads updating registry don't corrupt data."""
        _clear_run_registry()

        errors: list[Exception] = []

        def _writer(start: int, count: int) -> None:
            try:
                for i in range(start, start + count):
                    run_id = f"bt-{i:04d}"
                    with _run_registry_lock:
                        _run_registry[run_id] = {
                            "run_id": run_id,
                            "status": "completed",
                            "progress_pct": 100,
                        }
            except Exception as exc:
                errors.append(exc)

        threads: list[threading.Thread] = []
        for ti in range(4):
            th = threading.Thread(target=_writer, args=(ti * 100, 100))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

        assert len(errors) == 0  # noqa: S101
        with _run_registry_lock:
            assert len(_run_registry) == 400
