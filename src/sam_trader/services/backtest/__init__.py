"""Backtesting framework — BacktestNode + BacktestRunConfig integration."""

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
]
