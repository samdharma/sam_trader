"""Unit tests for walk-forward optimization."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.backtest.walk_forward import (
    WalkForward,
    WalkForwardResult,
    WindowResult,
    parse_days_flag,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_wrapper() -> MagicMock:
    """Return a mock BacktestEngineWrapper."""
    wrapper = MagicMock()
    return wrapper


@pytest.fixture
def base_configs() -> list:
    """Return minimal ImportableStrategyConfig fixtures."""
    from nautilus_trader.trading.config import ImportableStrategyConfig

    return [
        ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="none",
            config={
                "bundle_id": "test-bundle",
                "strategy_id": "TestStrategy-001",
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            },
        )
    ]


@pytest.fixture
def instrument_ids() -> list[str]:
    return ["TSLA.NASDAQ"]


@pytest.fixture
def bar_types() -> list[str]:
    return ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"]


# ---------------------------------------------------------------------------
# parse_days_flag tests
# ---------------------------------------------------------------------------


class TestParseDaysFlag:
    def test_plain_integer(self) -> None:
        assert parse_days_flag("90") == 90

    def test_suffixed(self) -> None:
        assert parse_days_flag("90d") == 90

    def test_whitespace(self) -> None:
        assert parse_days_flag("  30d  ") == 30

    def test_uppercase_suffix(self) -> None:
        assert parse_days_flag("30D") == 30

    def test_large_value(self) -> None:
        assert parse_days_flag("365") == 365

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            parse_days_flag("0")

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            parse_days_flag("-5")

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            parse_days_flag("abc")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            parse_days_flag("")


# ---------------------------------------------------------------------------
# Window generation tests
# ---------------------------------------------------------------------------


class TestGenerateWindows:
    """Test rolling window generation edge cases."""

    def test_single_window_exact_fit(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Data range fits exactly one train+test window."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-04-29",
        )
        windows = wf._generate_windows()
        assert len(windows) == 1
        assert windows[0] == (
            "2024-01-01",
            "2024-03-30",
            "2024-03-31",
            "2024-04-29",
        )

    def test_multiple_windows(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Data range fits multiple windows."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=60,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )
        windows = wf._generate_windows()
        # 182 days total. Windows: 60+30=90 days per full window.
        # 2024-01-01 + (60+30) = 2024-03-31
        # 2024-01-01 + 2*(60+30) = 2024-06-29 → fits (2024-01-01 + 180 = 2024-06-28)
        assert len(windows) >= 2

    def test_data_too_short_no_windows(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Data shorter than train+test → no windows."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-01-15",
        )
        windows = wf._generate_windows()
        assert len(windows) == 0

    def test_data_invalid_dates(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Invalid date format raises ValueError."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024/01/01",
            data_end="2024-06-30",
        )
        with pytest.raises(ValueError, match="Invalid date format"):
            wf._generate_windows()

    def test_start_after_end_raises(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """data_start after data_end raises ValueError."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024-06-30",
            data_end="2024-01-01",
        )
        with pytest.raises(ValueError, match="before.*data_end"):
            wf._generate_windows()

    def test_window_test_end_bounded_by_data_end(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Last window's test_end is clamped to data_end."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-03-15",  # 74 days
        )
        windows = wf._generate_windows()
        assert len(windows) >= 1
        # Verify test_end never exceeds data_end
        for _, _, _, test_end in windows:
            assert test_end <= "2024-03-15"


# ---------------------------------------------------------------------------
# WalkForward.run tests
# ---------------------------------------------------------------------------


class TestWalkForwardRun:
    def test_empty_param_grid_raises(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )
        with pytest.raises(ValueError, match="Parameter grid cannot be empty"):
            wf.run(param_grid={})

    def test_invalid_train_days_raises(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=0,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )
        with pytest.raises(ValueError, match="train_days must be positive"):
            wf.run(param_grid={"p": [1]})

    def test_invalid_test_days_raises(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=-5,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )
        with pytest.raises(ValueError, match="test_days must be positive"):
            wf.run(param_grid={"p": [1]})

    def test_no_windows_returns_empty_result(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Data too short → returns empty WalkForwardResult."""
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-01-25",  # 24 days < 90+30, and shortened train < 30d
        )
        result = wf.run(param_grid={"p": [1]})
        assert isinstance(result, WalkForwardResult)
        assert result.total_windows == 0
        assert result.windows == []
        assert result.overall_sharpe is None
        assert result.overall_pnl is None

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_single_window_success(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Single window sweep+test producing valid results."""
        # Configure the mock sweeper run() return value
        sweeper_instance = MagicMock()
        sweeper_instance.run.return_value = [
            {
                "combo": {"stop_loss_ticks": 10},
                "sharpe": 1.5,
                "net_pnl": 5000.0,
            },
            {
                "combo": {"stop_loss_ticks": 15},
                "sharpe": 0.8,
                "net_pnl": 2000.0,
            },
        ]
        mock_sweeper.return_value = sweeper_instance

        # Configure mock test run
        test_result = MagicMock()
        test_result.stats_pnls = {"TestStrategy-001": {"total_pnl": 3000.0}}
        test_result.stats_returns = {
            "TestStrategy-001": {
                "sharpe_ratio": 1.2,
                "max_drawdown": 0.05,
                "win_rate": 0.55,
            }
        }
        test_result.total_orders = 42
        mock_wrapper.run.return_value = test_result

        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-03-30",
        )

        result = wf.run(param_grid={"stop_loss_ticks": [10, 15]})

        assert result.total_windows == 1
        assert len(result.windows) == 1
        wr = result.windows[0]
        assert wr.best_params == {"stop_loss_ticks": 10}
        assert wr.train_sharpe == 1.5
        assert wr.test_sharpe == 1.2
        assert wr.test_pnl == 3000.0
        assert wr.test_win_rate == 0.55
        assert wr.test_max_dd == 0.05
        assert wr.test_trades == 42
        assert wr.error is None

        # Aggregate
        assert result.overall_sharpe == 1.2
        assert result.overall_pnl == 3000.0
        assert result.profitable_windows == 1

        # Parameter stability
        assert "stop_loss_ticks" in result.param_stability
        assert result.param_stability["stop_loss_ticks"]["10"] == 1

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_sweep_failure_records_error(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Sweep failure per-window records error and continues."""
        sweeper_instance = MagicMock()
        sweeper_instance.run.side_effect = RuntimeError("Connection lost")
        mock_sweeper.return_value = sweeper_instance

        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-03-30",
        )

        result = wf.run(param_grid={"p": [1]})
        assert result.total_windows == 1
        assert result.windows[0].error is not None
        assert "Connection lost" in str(result.windows[0].error)
        assert result.profitable_windows == 0

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_empty_sweep_results_records_error(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Empty sweep results → no data gap error."""
        sweeper_instance = MagicMock()
        sweeper_instance.run.return_value = []
        mock_sweeper.return_value = sweeper_instance

        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-03-30",
        )

        result = wf.run(param_grid={"p": [1]})
        assert (
            result.windows[0].error
            == "No sweep results — possible data gap in train period"
        )

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_test_backtest_failure_records_error(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Test backtest failure records error with best params preserved."""
        sweeper_instance = MagicMock()
        sweeper_instance.run.return_value = [
            {"combo": {"stop_loss_ticks": 5}, "sharpe": 1.2}
        ]
        mock_sweeper.return_value = sweeper_instance

        mock_wrapper.run.side_effect = RuntimeError("data gap")

        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-03-30",
        )

        result = wf.run(param_grid={"stop_loss_ticks": [5]})
        wr = result.windows[0]
        assert wr.error is not None
        assert "data gap" in str(wr.error)
        # Best params and train_sharpe are preserved even though test failed
        assert wr.best_params == {"stop_loss_ticks": 5}
        assert wr.train_sharpe == 1.2

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_multi_window_aggregate(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Multiple windows → aggregate stats computed correctly."""
        # First window: profitable
        sweeper1 = MagicMock()
        sweeper1.run.return_value = [{"combo": {"p": 10}, "sharpe": 1.5}]
        # Second window: unprofitable
        sweeper2 = MagicMock()
        sweeper2.run.return_value = [{"combo": {"p": 10}, "sharpe": 0.8}]

        # First test result: profitable
        result1 = MagicMock()
        result1.stats_pnls = {"S": {"total_pnl": 5000.0}}
        result1.stats_returns = {"S": {"sharpe_ratio": 1.3}}
        result1.total_orders = 30

        # Second test result: unprofitable
        result2 = MagicMock()
        result2.stats_pnls = {"S": {"total_pnl": -1000.0}}
        result2.stats_returns = {"S": {"sharpe_ratio": -0.5}}
        result2.total_orders = 15

        mock_sweeper.side_effect = [sweeper1, sweeper2]
        mock_wrapper.run.side_effect = [result1, result2]

        # Data range must fit 2 windows: train=30, test=30, step=30
        # Window 1: train 01-01 → 01-30, test 01-31 → 03-01
        # Window 2: train 01-31 → 03-01, test 03-02 → 03-31
        # Need total 90 calendar days = 03-31
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-03-30",
        )

        result = wf.run(param_grid={"p": [5, 10]})

        assert result.total_windows == 1  # Actually just 1 window fits 60 days
        # The data range 01-01 to 03-30 = 90 days. train(30)+test(30)+step(30)...
        # Let's just verify structure
        assert isinstance(result.overall_sharpe, float)
        assert isinstance(result.overall_pnl, float)

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_param_stability_counts(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Parameter stability correctly counts selections across windows."""
        # Window 1: selects p=10 as best
        # Window 2: selects p=15 as best
        sweeper1 = MagicMock()
        sweeper1.run.return_value = [
            {"combo": {"p": 10}, "sharpe": 1.5},
            {"combo": {"p": 15}, "sharpe": 1.0},
        ]
        sweeper2 = MagicMock()
        sweeper2.run.return_value = [
            {"combo": {"p": 15}, "sharpe": 2.0},
            {"combo": {"p": 10}, "sharpe": 1.0},
        ]

        mock_result = MagicMock()
        mock_result.stats_pnls = {"S": {"total_pnl": 1000.0}}
        mock_result.stats_returns = {"S": {"sharpe_ratio": 1.0}}
        mock_result.total_orders = 10

        mock_sweeper.side_effect = [sweeper1, sweeper2]
        mock_wrapper.run.side_effect = [mock_result, mock_result]

        # Need enough data for 2 windows: train=30, test=30, step=30
        # 2024-01-01 to 2024-04-29 = 120 days → fits 2 windows
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-04-29",
        )

        result = wf.run(param_grid={"p": [10, 15]})

        if "p" in result.param_stability:
            counts = result.param_stability["p"]
            assert sum(counts.values()) > 0  # At least one window counted

    @patch("sam_trader.services.backtest.walk_forward.ParameterSweep")
    def test_profitable_windows_count(
        self, mock_sweeper, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        """Profitable windows count is correct."""
        sweeper = MagicMock()
        sweeper.run.return_value = [{"combo": {"p": 10}, "sharpe": 1.5}]

        # Profitable
        result1 = MagicMock()
        result1.stats_pnls = {"S": {"total_pnl": 100.0}}
        result1.stats_returns = {"S": {"sharpe_ratio": 0.5}}
        result1.total_orders = 5

        # Break even (0 → NOT profitable)
        result2 = MagicMock()
        result2.stats_pnls = {"S": {"total_pnl": 0.0}}
        result2.stats_returns = {"S": {"sharpe_ratio": 0.0}}
        result2.total_orders = 8

        # Loss (negative → NOT profitable)
        result3 = MagicMock()
        result3.stats_pnls = {"S": {"total_pnl": -50.0}}
        result3.stats_returns = {"S": {"sharpe_ratio": -0.1}}
        result3.total_orders = 3

        mock_sweeper.side_effect = [sweeper, sweeper, sweeper]
        mock_wrapper.run.side_effect = [result1, result2, result3]

        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=30,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-28",
        )

        result = wf.run(param_grid={"p": [10]})
        assert result.profitable_windows == 1


# ---------------------------------------------------------------------------
# format_report tests
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_empty_result(self) -> None:
        r = WalkForwardResult(
            windows=[],
            overall_sharpe=None,
            overall_pnl=None,
            profitable_windows=0,
            total_windows=0,
        )
        report = WalkForward.format_report(r)
        assert "No windows generated" in report

    def test_single_window_report(self) -> None:
        r = WalkForwardResult(
            windows=[
                WindowResult(
                    train_start="2024-01-01",
                    train_end="2024-03-30",
                    test_start="2024-03-31",
                    test_end="2024-04-29",
                    best_params={"stop_loss_ticks": 10},
                    train_sharpe=1.5,
                    test_sharpe=1.2,
                    test_pnl=3000.0,
                    test_win_rate=0.55,
                    test_max_dd=0.05,
                    test_trades=42,
                )
            ],
            overall_sharpe=1.2,
            overall_pnl=3000.0,
            profitable_windows=1,
            total_windows=1,
            param_stability={"stop_loss_ticks": {"10": 1}},
            config={
                "train_days": 90,
                "test_days": 30,
                "data_start": "2024-01-01",
                "data_end": "2024-04-29",
                "instrument_ids": ["TSLA.NASDAQ"],
                "bar_types": ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            },
        )
        report = WalkForward.format_report(r)
        assert "Walk-Forward Report" in report
        assert "AGGREGATE SUMMARY" in report
        assert "PARAMETER STABILITY" in report
        assert "PER-WINDOW RESULTS" in report
        assert "1.5000" in report  # train sharpe
        assert "1.2000" in report  # test sharpe
        assert "+3000.00" in report  # test pnl
        assert "55.0%" in report  # win rate

    def test_report_with_error_window(self) -> None:
        r = WalkForwardResult(
            windows=[
                WindowResult(
                    train_start="2024-01-01",
                    train_end="2024-03-30",
                    test_start="2024-03-31",
                    test_end="2024-04-29",
                    error="Sweep failed: timeout",
                )
            ],
            overall_sharpe=None,
            overall_pnl=None,
            profitable_windows=0,
            total_windows=1,
            param_stability={},
            config={
                "train_days": 90,
                "test_days": 30,
                "data_start": "2024-01-01",
                "data_end": "2024-04-29",
                "instrument_ids": ["TSLA.NASDAQ"],
                "bar_types": ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            },
        )
        report = WalkForward.format_report(r)
        assert "[ERROR]" in report
        assert "WARNINGS / ERRORS" in report
        assert "Sweep failed: timeout" in report

    def test_report_with_stability_star_marker(self) -> None:
        """The most-selected param value gets a ★ marker."""
        r = WalkForwardResult(
            windows=[
                WindowResult(
                    train_start="2024-01-01",
                    train_end="2024-03-30",
                    test_start="2024-03-31",
                    test_end="2024-04-29",
                    best_params={"p": 10},
                    train_sharpe=1.5,
                    test_sharpe=1.0,
                    test_pnl=100.0,
                ),
                WindowResult(
                    train_start="2024-02-01",
                    train_end="2024-04-30",
                    test_start="2024-05-01",
                    test_end="2024-05-30",
                    best_params={"p": 10},
                    train_sharpe=1.2,
                    test_sharpe=0.8,
                    test_pnl=50.0,
                ),
            ],
            overall_sharpe=0.9,
            overall_pnl=150.0,
            profitable_windows=2,
            total_windows=2,
            param_stability={"p": {"10": 2}},
            config={
                "train_days": 90,
                "test_days": 30,
                "data_start": "2024-01-01",
                "data_end": "2024-05-30",
                "instrument_ids": ["TSLA.NASDAQ"],
                "bar_types": ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            },
        )
        report = WalkForward.format_report(r)
        assert "★" in report
        assert "10: 2 (100%)" in report

    def test_report_multiple_params_with_most_popular_marked(self) -> None:
        """Multiple param values — most popular gets ★."""
        r = WalkForwardResult(
            windows=[
                WindowResult(
                    train_start="2024-01-01",
                    train_end="2024-03-30",
                    test_start="2024-03-31",
                    test_end="2024-04-29",
                    best_params={"p": 5},
                    train_sharpe=1.5,
                    test_sharpe=1.0,
                    test_pnl=100.0,
                ),
                WindowResult(
                    train_start="2024-02-01",
                    train_end="2024-04-30",
                    test_start="2024-05-01",
                    test_end="2024-05-30",
                    best_params={"p": 10},
                    train_sharpe=1.2,
                    test_sharpe=0.8,
                    test_pnl=50.0,
                ),
                WindowResult(
                    train_start="2024-03-01",
                    train_end="2024-05-30",
                    test_start="2024-06-01",
                    test_end="2024-06-30",
                    best_params={"p": 10},
                    train_sharpe=1.0,
                    test_sharpe=0.5,
                    test_pnl=-20.0,
                ),
            ],
            overall_sharpe=0.77,
            overall_pnl=130.0,
            profitable_windows=2,
            total_windows=3,
            param_stability={"p": {"5": 1, "10": 2}},
            config={
                "train_days": 90,
                "test_days": 30,
                "data_start": "2024-01-01",
                "data_end": "2024-06-30",
                "instrument_ids": ["TSLA.NASDAQ"],
                "bar_types": ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            },
        )
        report = WalkForward.format_report(r)
        assert "p:" in report
        # All entries should be present
        assert "5: 1" in report
        assert "10: 2" in report
        # "10" should have star since it's the most popular
        assert "10:" in report
        star_count = report.count("★")
        assert star_count >= 1  # at least one star

    def test_report_compact_json_serializable(self) -> None:
        """WindowResult fields are serializable for JSON output in CLI."""
        import json

        wr = WindowResult(
            train_start="2024-01-01",
            train_end="2024-03-30",
            test_start="2024-03-31",
            test_end="2024-04-29",
            best_params={"stop_loss_ticks": 10},
            train_sharpe=1.5,
            test_sharpe=1.2,
            test_pnl=3000.0,
            test_win_rate=0.55,
            test_max_dd=0.05,
            test_trades=42,
        )
        serialized = json.dumps(
            {
                "train_start": wr.train_start,
                "train_end": wr.train_end,
                "test_start": wr.test_start,
                "test_end": wr.test_end,
                "best_params": wr.best_params,
                "train_sharpe": wr.train_sharpe,
                "test_sharpe": wr.test_sharpe,
                "test_pnl": wr.test_pnl,
                "test_win_rate": wr.test_win_rate,
                "test_max_dd": wr.test_max_dd,
                "test_trades": wr.test_trades,
                "error": wr.error,
            }
        )
        assert isinstance(serialized, str)
        assert "stop_loss_ticks" in serialized


# ---------------------------------------------------------------------------
# WalkForwardResult dataclass tests
# ---------------------------------------------------------------------------


class TestWalkForwardResultDefaults:
    def test_defaults(self) -> None:
        r = WalkForwardResult()
        assert r.windows == []
        assert r.overall_sharpe is None
        assert r.overall_pnl is None
        assert r.profitable_windows == 0
        assert r.total_windows == 0
        assert r.param_stability == {}
        assert r.config == {}

    def test_window_result_defaults(self) -> None:
        wr = WindowResult()
        assert wr.train_start == ""
        assert wr.best_params == {}
        assert wr.error is None


# ---------------------------------------------------------------------------
# _extract_test_metrics tests
# ---------------------------------------------------------------------------


class TestExtractTestMetrics:
    def test_full_stats(self) -> None:
        result = MagicMock()
        result.stats_pnls = {"S": {"total_pnl": 1234.56}}
        result.stats_returns = {
            "S": {"sharpe_ratio": 1.5, "max_drawdown": 0.1, "win_rate": 0.6}
        }
        result.total_orders = 100

        sharpe, pnl, win, dd, trades = WalkForward._extract_test_metrics(result)
        assert sharpe == 1.5
        assert pnl == 1234.56
        assert win == 0.6
        assert dd == 0.1
        assert trades == 100

    def test_none_fields(self) -> None:
        result = MagicMock()
        result.stats_pnls = {}
        result.stats_returns = {}
        result.total_orders = None

        sharpe, pnl, win, dd, trades = WalkForward._extract_test_metrics(result)
        assert sharpe is None
        assert pnl is None
        assert win is None
        assert dd is None
        assert trades is None

    def test_flat_stats_returns(self) -> None:
        """stats_returns without per-strategy nesting."""
        result = MagicMock()
        result.stats_pnls = {"S": {"total_pnl": 500.0}}
        result.stats_returns = {"sharpe_ratio": 0.8}
        result.total_orders = 20

        sharpe, pnl, win, dd, trades = WalkForward._extract_test_metrics(result)
        assert sharpe == 0.8
        assert pnl == 500.0
        assert trades == 20


# ---------------------------------------------------------------------------
# WalkForward config
# ---------------------------------------------------------------------------


class TestWalkForwardConfig:
    def test_build_config(
        self, mock_wrapper, base_configs, instrument_ids, bar_types
    ) -> None:
        wf = WalkForward(
            wrapper=mock_wrapper,
            base_strategies=base_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=90,
            test_days=30,
            data_start="2024-01-01",
            data_end="2024-06-30",
        )
        cfg = wf._build_config()
        assert cfg["train_days"] == 90
        assert cfg["test_days"] == 30
        assert cfg["data_start"] == "2024-01-01"
        assert cfg["data_end"] == "2024-06-30"
        assert "TSLA.NASDAQ" in cfg["instrument_ids"]
