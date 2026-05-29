"""Backtesting framework — BacktestNode + BacktestRunConfig integration."""

from sam_trader.services.backtest.dashboard_api import (
    handle_backtest_catalog_instruments,
    handle_backtest_catalog_status,
    handle_backtest_compare,
    handle_backtest_run,
    handle_backtest_run_status,
    handle_backtest_runs,
    handle_backtest_runs_detail,
)
from sam_trader.services.backtest.engine import BacktestEngineWrapper
from sam_trader.services.backtest.results import BacktestResultStore
from sam_trader.services.backtest.sweep import (
    ParameterSweep,
    generate_sweep_grid,
    parse_sweep_flags,
)
from sam_trader.services.backtest.walk_forward import (
    WalkForward,
    WalkForwardResult,
    WindowResult,
    parse_days_flag,
)

__all__ = [
    "BacktestEngineWrapper",
    "BacktestResultStore",
    "ParameterSweep",
    "WalkForward",
    "WalkForwardResult",
    "WindowResult",
    "generate_sweep_grid",
    "parse_days_flag",
    "parse_sweep_flags",
    "handle_backtest_run",
    "handle_backtest_run_status",
    "handle_backtest_runs",
    "handle_backtest_runs_detail",
    "handle_backtest_compare",
    "handle_backtest_catalog_instruments",
    "handle_backtest_catalog_status",
]
