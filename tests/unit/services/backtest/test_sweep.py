"""Unit tests for parameter sweep engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.engine import BacktestEngineWrapper
from sam_trader.services.backtest.sweep import (
    ParameterSweep,
    _patch_strategy_config,
    generate_sweep_grid,
    parse_sweep_flags,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wrapper() -> BacktestEngineWrapper:
    """Return a BacktestEngineWrapper with a temp catalog path."""
    return BacktestEngineWrapper(catalog_path="/tmp/test-catalog")


@pytest.fixture
def orb_strategy() -> ImportableStrategyConfig:
    """Return a minimal ORB strategy config."""
    return ImportableStrategyConfig(
        strategy_path="sam_trader.strategies.orb:OrbStrategy",
        config_path="sam_trader.strategies.orb:OrbStrategyConfig",
        config={
            "instrument_id": "TSLA.NASDAQ",
            "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "first_candle_minutes": 15,
            "trade_size": 5,
            "stop_loss_ticks": 10,
            "take_profit_ticks": 30,
            "venue": "FUTU",
            "bundle_id": "tsla-orb-5m",
            "market": "US",
        },
    )


@pytest.fixture
def momentum_strategy() -> ImportableStrategyConfig:
    """Return a minimal Momentum strategy config."""
    return ImportableStrategyConfig(
        strategy_path="sam_trader.strategies.momentum:MomentumStrategy",
        config_path="sam_trader.strategies.momentum:MomentumStrategyConfig",
        config={
            "instrument_id": "AAPL.NASDAQ",
            "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "window": 20,
            "trade_size": 10,
            "venue": "FUTU",
            "bundle_id": "aapl-momentum-5m",
            "market": "US",
        },
    )


def _make_result(
    pnl: float = 1000.0,
    sharpe: float | None = 1.5,
    max_dd: float = -0.10,
    win_rate: float = 0.55,
    orders: int = 10,
    elapsed: float = 2.0,
    strategy_key: str = "OrbStrategy-001",
) -> BacktestResult:
    """Build a minimal BacktestResult for testing."""
    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1_000_000_000)
    return BacktestResult(
        trader_id="BACKTEST-001",
        machine_id="test",
        run_config_id="config-1",
        instance_id="inst-1",
        run_id="run-1",
        run_started=now_ns,
        run_finished=now_ns,
        backtest_start=now_ns,
        backtest_end=now_ns,
        elapsed_time=elapsed,
        iterations=100,
        total_events=500,
        total_orders=orders,
        total_positions=0,
        stats_pnls={strategy_key: {"total_pnl": pnl}},
        stats_returns={
            "sharpe_ratio": sharpe,  # type: ignore[dict-item]
            "max_drawdown": max_dd,
            "win_rate": win_rate,
        },
    )


# ---------------------------------------------------------------------------
# parse_sweep_flags tests
# ---------------------------------------------------------------------------


class TestParseSweepFlags:
    """Tests for parse_sweep_flags."""

    def test_parses_single_flag(self) -> None:
        """Single sweep flag yields single-key grid."""
        result = parse_sweep_flags(["stop_loss_ticks=5,10,15"])
        assert result == {"stop_loss_ticks": [5, 10, 15]}

    def test_parses_multiple_flags(self) -> None:
        """Multiple sweep flags yield multi-key grid."""
        result = parse_sweep_flags(
            [
                "stop_loss_ticks=5,10,15",
                "take_profit_ticks=20,30,40",
            ]
        )
        assert result == {
            "stop_loss_ticks": [5, 10, 15],
            "take_profit_ticks": [20, 30, 40],
        }

    def test_parses_float_values(self) -> None:
        """Float values are auto-detected."""
        result = parse_sweep_flags(["threshold=0.5,1.0,1.5"])
        assert result == {"threshold": [0.5, 1.0, 1.5]}

    def test_parses_string_values(self) -> None:
        """Non-numeric values stay as strings."""
        result = parse_sweep_flags(["venue=FUTU,IB"])
        assert result == {"venue": ["FUTU", "IB"]}

    def test_parses_mixed_types(self) -> None:
        """Mixed int/float/string values work."""
        result = parse_sweep_flags(["mixed=10,2.5,hello"])
        assert result == {"mixed": [10, 2.5, "hello"]}

    def test_raises_on_missing_equals(self) -> None:
        """Flag without = raises ValueError."""
        with pytest.raises(ValueError, match="Invalid sweep flag"):
            parse_sweep_flags(["stop_loss_ticks"])

    def test_raises_on_empty_key(self) -> None:
        """Flag with empty key raises ValueError."""
        with pytest.raises(ValueError, match="key cannot be empty"):
            parse_sweep_flags(["=5,10,15"])

    def test_raises_on_empty_values(self) -> None:
        """Flag with no values after = raises ValueError."""
        with pytest.raises(ValueError, match="no values after"):
            parse_sweep_flags(["stop_loss_ticks="])

    def test_handles_whitespace(self) -> None:
        """Whitespace around values is trimmed."""
        result = parse_sweep_flags(["  stop_loss_ticks = 5 , 10 , 15  "])
        assert result == {"stop_loss_ticks": [5, 10, 15]}

    def test_skips_empty_value_slots(self) -> None:
        """Empty slots between commas are skipped."""
        result = parse_sweep_flags(["x=5,,10"])
        assert result == {"x": [5, 10]}


# ---------------------------------------------------------------------------
# generate_sweep_grid tests
# ---------------------------------------------------------------------------


class TestGenerateSweepGrid:
    """Tests for generate_sweep_grid."""

    def test_single_param_returns_list_of_singles(self) -> None:
        """Single parameter returns one combo per value."""
        result = generate_sweep_grid({"a": [1, 2, 3]})
        assert result == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_two_params_returns_cartesian_product(self) -> None:
        """Two parameters yield cartesian product."""
        result = generate_sweep_grid({"a": [1, 2], "b": [10, 20]})
        assert result == [
            {"a": 1, "b": 10},
            {"a": 1, "b": 20},
            {"a": 2, "b": 10},
            {"a": 2, "b": 20},
        ]

    def test_three_params(self) -> None:
        """Three parameters with 2 values each → 8 combos."""
        result = generate_sweep_grid({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
        assert len(result) == 8

    def test_empty_grid_returns_single_empty_dict(self) -> None:
        """Empty grid returns a single empty dict."""
        result = generate_sweep_grid({})
        assert result == [{}]

    def test_10_combos(self) -> None:
        """10+ combos work correctly."""
        result = generate_sweep_grid({"a": list(range(5)), "b": list(range(3))})
        assert len(result) == 15  # 5 × 3

    def test_single_value_in_grid(self) -> None:
        """Single value per key works."""
        result = generate_sweep_grid({"a": [1]})
        assert result == [{"a": 1}]


# ---------------------------------------------------------------------------
# _patch_strategy_config tests
# ---------------------------------------------------------------------------


class TestPatchStrategyConfig:
    """Tests for _patch_strategy_config."""

    def test_patches_config_with_combo(
        self, orb_strategy: ImportableStrategyConfig
    ) -> None:
        """Combo values override config fields."""
        combo = {"stop_loss_ticks": 5, "take_profit_ticks": 20}
        patched = _patch_strategy_config(orb_strategy, combo)
        assert patched.config["stop_loss_ticks"] == 5
        assert patched.config["take_profit_ticks"] == 20
        # Unpatched fields remain
        assert patched.config["trade_size"] == 5
        assert patched.config["instrument_id"] == "TSLA.NASDAQ"

    def test_original_config_not_mutated(
        self, orb_strategy: ImportableStrategyConfig
    ) -> None:
        """Original strategy config is not modified."""
        original_sl = orb_strategy.config["stop_loss_ticks"]
        _patch_strategy_config(orb_strategy, {"stop_loss_ticks": 999})
        assert orb_strategy.config["stop_loss_ticks"] == original_sl

    def test_deep_copy_prevents_nested_mutation(
        self, orb_strategy: ImportableStrategyConfig
    ) -> None:
        """Nested config values are deep-copied."""
        combo = {"stop_loss_ticks": 5}
        patched = _patch_strategy_config(orb_strategy, combo)
        # Modify the patched config further — shouldn't affect original
        patched.config["stop_loss_ticks"] = 999
        assert orb_strategy.config["stop_loss_ticks"] == 10


# ---------------------------------------------------------------------------
# ParameterSweep tests
# ---------------------------------------------------------------------------


class TestParameterSweep:
    """Tests for ParameterSweep orchestration."""

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_single_param_sweep(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy: ImportableStrategyConfig,
    ) -> None:
        """Single parameter sweep returns one result per value."""
        # Build results with different P&L/Sharpe per combo
        results = [
            _make_result(pnl=1000.0, sharpe=1.5, strategy_key="Orb-1"),
            _make_result(pnl=1200.0, sharpe=1.8, strategy_key="Orb-2"),
            _make_result(pnl=800.0, sharpe=1.2, strategy_key="Orb-3"),
        ]
        mock_engine = MagicMock()
        mock_engine.get_result.side_effect = results
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[orb_strategy],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        sweep_results = sweeper.run(param_grid={"stop_loss_ticks": [5, 10, 15]})

        assert len(sweep_results) == 3
        # Results should be sorted by Sharpe descending
        assert sweep_results[0]["sharpe"] == 1.8
        assert sweep_results[0]["net_pnl"] == 1200.0
        assert sweep_results[0]["combo"] == {"stop_loss_ticks": 10}

        assert sweep_results[1]["sharpe"] == 1.5
        assert sweep_results[2]["sharpe"] == 1.2

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_two_param_sweep(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy: ImportableStrategyConfig,
    ) -> None:
        """2-parameter sweep generates cartesian product of configs."""
        # 2×3 = 6 combos
        results = [
            _make_result(
                pnl=float(i) * 100, sharpe=float(i) * 0.5, strategy_key=f"Orb-{i}"
            )
            for i in range(6)
        ]
        mock_engine = MagicMock()
        mock_engine.get_result.side_effect = results
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[orb_strategy],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        sweep_results = sweeper.run(
            param_grid={
                "stop_loss_ticks": [5, 10],
                "take_profit_ticks": [20, 30, 40],
            }
        )

        assert len(sweep_results) == 6
        # Descending by Sharpe
        for i in range(len(sweep_results) - 1):
            assert sweep_results[i]["sharpe"] >= sweep_results[i + 1]["sharpe"]

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_handles_none_sharpe(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy: ImportableStrategyConfig,
    ) -> None:
        """Configs with None Sharpe sink to bottom."""
        results = [
            _make_result(pnl=1000.0, sharpe=1.5, strategy_key="Orb-A"),
            _make_result(pnl=500.0, sharpe=None, strategy_key="Orb-B"),
            _make_result(pnl=2000.0, sharpe=2.0, strategy_key="Orb-C"),
        ]
        mock_engine = MagicMock()
        mock_engine.get_result.side_effect = results
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[orb_strategy],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        sweep_results = sweeper.run(param_grid={"stop_loss_ticks": [5, 10, 15]})

        assert len(sweep_results) == 3
        assert sweep_results[0]["sharpe"] == 2.0
        assert sweep_results[1]["sharpe"] == 1.5
        assert sweep_results[2]["sharpe"] is None  # Sinks to bottom

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_empty_grid_raises(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy: ImportableStrategyConfig,
    ) -> None:
        """Empty parameter grid raises ValueError."""
        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[orb_strategy],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        with pytest.raises(ValueError, match="Parameter grid cannot be empty"):
            sweeper.run(param_grid={})

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_multi_strategy_sweep(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy: ImportableStrategyConfig,
        momentum_strategy: ImportableStrategyConfig,
    ) -> None:
        """Multi-strategy sweep creates correct number of configs."""
        # 2 combos × 1 = 2 configs (each with 2 strategies)
        results = [
            _make_result(pnl=1000.0, sharpe=1.5, strategy_key="OrbStrategy-001"),
            _make_result(pnl=2000.0, sharpe=2.0, strategy_key="MomentumStrategy-001"),
        ]
        mock_engine = MagicMock()
        mock_engine.get_result.side_effect = results
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=[orb_strategy, momentum_strategy],
            instrument_ids=["TSLA.NASDAQ", "AAPL.NASDAQ"],
            bar_types=[
                "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            ],
            start="2024-01-01",
            end="2024-06-30",
        )

        sweep_results = sweeper.run(param_grid={"stop_loss_ticks": [5, 10]})

        # 2 combos, BacktestNode returns 1 result per config
        # (with per-strategy stats)
        assert len(sweep_results) == 2
        # Highest Sharpe first
        assert sweep_results[0]["sharpe"] == 2.0

    # ------------------------------------------------------------------
    # format_table tests
    # ------------------------------------------------------------------

    def test_format_table_basic(self) -> None:
        """format_table produces a readable ranked table."""
        results = [
            {
                "combo": {"stop_loss_ticks": 10, "take_profit_ticks": 40},
                "strategy_id": "OrbStrategy-001",
                "net_pnl": 3120.0,
                "sharpe": 1.68,
                "max_drawdown": -0.071,
                "win_rate": 0.51,
                "total_trades": 48,
                "elapsed": 3.5,
            },
            {
                "combo": {"stop_loss_ticks": 10, "take_profit_ticks": 30},
                "strategy_id": "OrbStrategy-001",
                "net_pnl": 2456.0,
                "sharpe": 1.42,
                "max_drawdown": -0.087,
                "win_rate": 0.48,
                "total_trades": 42,
                "elapsed": 3.2,
            },
            {
                "combo": {"stop_loss_ticks": 5, "take_profit_ticks": 20},
                "strategy_id": "OrbStrategy-001",
                "net_pnl": 1234.0,
                "sharpe": 0.85,
                "max_drawdown": -0.123,
                "win_rate": 0.42,
                "total_trades": 35,
                "elapsed": 2.8,
            },
        ]

        table = ParameterSweep.format_table(results)
        assert "Parameter Sweep Results" in table
        assert "Stop Loss Ticks" in table
        assert "Take Profit Ticks" in table
        assert "OrbStrategy-001" in table
        assert "Sharpe ★" in table

    def test_format_table_empty(self) -> None:
        """Empty results produce a message."""
        table = ParameterSweep.format_table([])
        assert "No results" in table

    def test_format_table_single_result(self) -> None:
        """Single result still produces a valid table."""
        results = [
            {
                "combo": {"a": 1},
                "strategy_id": "Test",
                "net_pnl": 100.0,
                "sharpe": 1.0,
                "max_drawdown": -0.05,
                "win_rate": 0.5,
                "total_trades": 10,
                "elapsed": 1.0,
            }
        ]
        table = ParameterSweep.format_table(results)
        assert "Parameter Sweep Results" in table

    def test_format_table_handles_missing_combo_keys(self) -> None:
        """Results with different combo key sets still format correctly."""
        results = [
            {
                "combo": {"a": 1, "b": 2},
                "strategy_id": "A",
                "net_pnl": 100.0,
                "sharpe": 1.0,
                "max_drawdown": -0.05,
                "win_rate": 0.5,
                "total_trades": 10,
                "elapsed": 1.0,
            },
            {
                "combo": {"a": 3},
                "strategy_id": "B",
                "net_pnl": 200.0,
                "sharpe": 1.2,
                "max_drawdown": -0.03,
                "win_rate": 0.6,
                "total_trades": 12,
                "elapsed": 1.5,
            },
        ]
        table = ParameterSweep.format_table(results)
        assert "A" in table
        assert "B" in table

    def test_format_table_with_none_values(self) -> None:
        """N/A values in results are handled gracefully."""
        results = [
            {
                "combo": {"a": 1},
                "strategy_id": "Test",
                "net_pnl": None,
                "sharpe": None,
                "max_drawdown": None,
                "win_rate": None,
                "total_trades": None,
                "elapsed": None,
            }
        ]
        table = ParameterSweep.format_table(results)
        assert "Parameter Sweep Results" in table
        # Should not crash
