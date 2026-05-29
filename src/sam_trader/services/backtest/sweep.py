"""Parameter sweep — grid search via multi-config BacktestNode.

Generates a :class:`BacktestRunConfig` per grid combination from ``--sweep``
flags, runs them via a single :class:`BacktestNode`, and outputs a ranked
comparison table sorted by Sharpe ratio.

Usage::

    from sam_trader.services.backtest.sweep import ParameterSweep

    sweeper = ParameterSweep(
        wrapper=BacktestEngineWrapper(catalog_path="data/catalog"),
        base_strategies=[orb_bundle],
        instrument_ids=["TSLA.NASDAQ"],
        bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
        start="2024-01-01",
        end="2024-06-30",
    )
    results = sweeper.run(
        param_grid={"stop_loss_ticks": [5, 10, 15], "take_profit_ticks": [20, 30, 40]},
    )
    print(sweeper.format_table(results))
"""

from __future__ import annotations

import copy
import logging
from itertools import product
from typing import Any

from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.engine import BacktestEngineWrapper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def parse_sweep_flags(sweep_flags: list[str]) -> dict[str, list[str | int | float]]:
    """Parse ``--sweep key=val1,val2,val3`` flags into a parameter grid.

    Values are auto-typed: integers, floats, or kept as strings.

    Example::

        parse_sweep_flags([
            "stop_loss_ticks=5,10,15",
            "take_profit_ticks=20,30,40",
        ])
        # → {"stop_loss_ticks": [5, 10, 15], "take_profit_ticks": [20, 30, 40]}

    Parameters
    ----------
    sweep_flags : list[str]
        Raw ``--sweep`` flag values from Click.

    Returns
    -------
    dict[str, list]
        Parameter name → list of typed values.

    Raises
    ------
    ValueError
        If a flag is missing ``=`` or has an empty key.

    """
    grid: dict[str, list[str | int | float]] = {}
    for flag in sweep_flags:
        if "=" not in flag:
            raise ValueError(
                f"Invalid sweep flag: {flag!r} — expected key=val1,val2,..."
            )
        key, _, raw_values = flag.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid sweep flag: {flag!r} — key cannot be empty")
        values: list[str | int | float] = []
        for raw_val in raw_values.split(","):
            raw_val = raw_val.strip()
            if not raw_val:
                continue
            # Auto-type
            typed_val: str | int | float
            try:
                typed_val = int(raw_val)
            except ValueError:
                try:
                    typed_val = float(raw_val)
                except ValueError:
                    typed_val = raw_val
            values.append(typed_val)
        if not values:
            raise ValueError(f"Invalid sweep flag: {flag!r} — no values after '='")
        grid[key] = values
    return grid


def generate_sweep_grid(
    param_grid: dict[str, list],
) -> list[dict[str, Any]]:
    """Generate the cartesian product of a parameter grid.

    Example::

        generate_sweep_grid({"a": [1, 2], "b": [10, 20]})
        # → [{"a": 1, "b": 10}, {"a": 1, "b": 20}, {"a": 2, "b": 10}, {"a": 2, "b": 20}]

    Parameters
    ----------
    param_grid : dict[str, list]
        Parameter name → list of values.

    Returns
    -------
    list[dict]
        One dict per combination (cartesian product).

    """
    if not param_grid:
        return [{}]

    keys = list(param_grid.keys())
    combinations = []
    for values in product(*param_grid.values()):
        combinations.append(dict(zip(keys, values)))
    return combinations


def _patch_strategy_config(
    strategy: ImportableStrategyConfig,
    combo: dict[str, Any],
) -> ImportableStrategyConfig:
    """Deep-copy a strategy config and patch it with sweep parameters.

    Parameters
    ----------
    strategy : ImportableStrategyConfig
        The base strategy configuration.
    combo : dict
        Parameter overrides to merge into the config.

    Returns
    -------
    ImportableStrategyConfig
        A new config with the overrides applied.

    """
    new_config = copy.deepcopy(strategy.config)
    new_config.update(combo)
    return ImportableStrategyConfig(
        strategy_path=strategy.strategy_path,
        config_path=strategy.config_path,
        config=new_config,
    )


# ---------------------------------------------------------------------------
# ParameterSweep
# ---------------------------------------------------------------------------


class ParameterSweep:
    """Orchestrate a parameter sweep across a grid of strategy parameters.

    Generates one :class:`BacktestRunConfig` per combination, runs them all
    via a single :class:`BacktestNode`, and returns ranked results.

    Parameters
    ----------
    wrapper : BacktestEngineWrapper
        Pre-configured engine wrapper with catalog path set.
    base_strategies : list[ImportableStrategyConfig]
        Strategy configuration(s) to sweep.
    instrument_ids : list[str]
        Instrument IDs to backtest over.
    bar_types : list[str]
        Bar type strings for data loading.
    start : str
        Start date (ISO format, e.g. ``"2024-01-01"``).
    end : str
        End date (ISO format, e.g. ``"2024-06-30"``).

    """

    def __init__(
        self,
        wrapper: BacktestEngineWrapper,
        base_strategies: list[ImportableStrategyConfig],
        instrument_ids: list[str],
        bar_types: list[str],
        start: str,
        end: str,
    ) -> None:
        self._wrapper = wrapper
        self._base_strategies = base_strategies
        self._instrument_ids = instrument_ids
        self._bar_types = bar_types
        self._start = start
        self._end = end

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        param_grid: dict[str, list],
        *,
        run_analysis: bool = True,
    ) -> list[dict[str, Any]]:
        r"""Execute the parameter sweep and return ranked results.

        Parameters
        ----------
        param_grid : dict[str, list]
            Parameter name → list of values to sweep over.
        run_analysis : bool
            When ``True`` (default), auto-compute portfolio statistics.

        Returns
        -------
        list[dict]
            One dict per parameter combination, sorted by Sharpe ratio
            (descending).  Each dict contains:

            * ``combo`` — parameter combination applied
            * ``strategy_id`` — strategy identifier
            * ``net_pnl`` — total P&L
            * ``sharpe`` — Sharpe ratio
            * ``max_drawdown`` — max drawdown fraction
            * ``win_rate`` — fraction of winning trades
            * ``total_trades`` — total order count
            * ``elapsed`` — backtest elapsed seconds

        Raises
        ------
        ValueError
            If the parameter grid is empty.
        BacktestEngineError
            If the sweep fails.

        """
        if not param_grid:
            raise ValueError("Parameter grid cannot be empty")

        combos = generate_sweep_grid(param_grid)

        # Build one BacktestRunConfig per combo.
        configs: list[BacktestRunConfig] = []
        config_combo_map: dict[str, dict[str, Any]] = {}

        for idx, combo in enumerate(combos):
            patched_strategies = [
                _patch_strategy_config(s, combo) for s in self._base_strategies
            ]

            run_config = self._wrapper.build_run_config(
                strategies=patched_strategies,
                instrument_ids=self._instrument_ids,
                bar_types=self._bar_types,
                start=self._start,
                end=self._end,
                run_analysis=run_analysis,
            )
            configs.append(run_config)

            cid = getattr(run_config, "id", str(idx))
            config_combo_map[cid] = combo

        # Run all configs via single BacktestNode.
        raw_results: list[BacktestResult] = self._wrapper.run_multi(configs)

        # Collect results paired with their combo.
        sweep_results: list[dict[str, Any]] = []
        for idx, raw_result in enumerate(raw_results):
            cid = getattr(configs[idx], "id", str(idx))
            combo = config_combo_map.get(cid, {})
            entry = self._extract_result(raw_result, combo)
            sweep_results.append(entry)

        # Sort by Sharpe descending (N/A values sink to bottom).
        sweep_results.sort(
            key=lambda r: (
                r["sharpe"] is not None,
                r["sharpe"] if r["sharpe"] is not None else float("-inf"),
            ),
            reverse=True,
        )

        return sweep_results

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_table(
        results: list[dict[str, Any]],
        *,
        sort_key: str = "sharpe",
    ) -> str:
        """Format sweep results as a ranked comparison table.

        Parameters
        ----------
        results : list[dict]
            Results from :meth:`run`.
        sort_key : str
            Metric column to highlight as the sort key.

        Returns
        -------
        str
            Multi-line human-readable table.

        """
        if not results:
            return "Parameter Sweep: No results."

        # Determine which combo keys to show
        combo_keys: list[str] = []
        for r in results:
            combo = r.get("combo", {})
            for k in combo:
                if k not in combo_keys:
                    combo_keys.append(k)

        # Build header
        header_cols = []
        for k in combo_keys:
            header_cols.append(k.replace("_", " ").title())
        header_cols += [
            "Strategy",
            "Net P&L",
            "Sharpe ★",
            "Max DD",
            "Win Rate",
            "Trades",
            "Elapsed",
        ]

        # Calculate column widths
        widths: list[int] = [len(c) for c in header_cols]
        for r in results:
            combo = r.get("combo", {})
            for idx, k in enumerate(combo_keys):
                val_str = str(combo.get(k, ""))
                widths[idx] = max(widths[idx], len(val_str))
            strategy_str = str(r.get("strategy_id", ""))[:32]
            widths[len(combo_keys)] = max(widths[len(combo_keys)], len(strategy_str))

        # Helper formatters
        def _fmt_val(val: Any, default: str = " N/A") -> str:
            if val is None:
                return default
            if isinstance(val, float):
                return f" {val:.4f}" if abs(val) < 100 else f" {val:.2f}"
            if isinstance(val, (int, bool)):
                return f" {val}"
            return f" {val}"

        def _fmt_pnl(val: Any) -> str:
            if val is None:
                return "     N/A"
            return f" {float(val):+.2f}"

        def _fmt_pct(val: Any) -> str:
            if val is None:
                return "   N/A"
            return f" {float(val):+.1%}"

        def _fmt_elapsed(val: Any) -> str:
            if val is None:
                return "   N/A"
            return f" {float(val):.1f}s"

        # Build rows
        lines: list[str] = []
        separator = "─" * 80
        lines.append("Parameter Sweep Results")
        lines.append("=" * 80)
        lines.append(
            "  " + " │ ".join(f"{h:<{w}}" for h, w in zip(header_cols, widths))
        )
        lines.append("  " + separator)

        for r in results:
            row_parts: list[str] = []
            combo = r.get("combo", {})
            for idx, k in enumerate(combo_keys):
                row_parts.append(f"{str(combo.get(k, '')):<{widths[idx]}}")
            row_parts.append(
                f"{str(r.get('strategy_id', ''))[:32]:<{widths[len(combo_keys)]}}"
            )
            # Use fixed widths for numeric columns (12 chars each for alignment)
            net_pnl = _fmt_pnl(r.get("net_pnl"))
            sharpe = _fmt_val(r.get("sharpe"))
            max_dd = _fmt_pct(r.get("max_drawdown"))
            win_rate = _fmt_pct(r.get("win_rate"))
            trades = _fmt_val(r.get("total_trades"))
            elapsed = _fmt_elapsed(r.get("elapsed"))
            row_parts.append(f"{net_pnl:>8}")
            row_parts.append(f"{sharpe:>8}")
            row_parts.append(f"{max_dd:>8}")
            row_parts.append(f"{win_rate:>8}")
            row_parts.append(f"{trades:>8}")
            row_parts.append(f"{elapsed:>8}")

            lines.append("  " + " │ ".join(row_parts))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_result(
        raw_result: BacktestResult,
        combo: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract summary metrics from a single sweep result.

        Parameters
        ----------
        raw_result : BacktestResult
            The raw Nautilus result object.
        combo : dict
            The parameter combination that produced this result.

        Returns
        -------
        dict
            Metrics dict with combo, strategy_id, net_pnl, sharpe,
            max_drawdown, win_rate, total_trades, elapsed.

        """
        net_pnl: float | None = None
        strategy_key: str = "unknown"

        stats_pnls = getattr(raw_result, "stats_pnls", {}) or {}
        for key, pnl_data in stats_pnls.items():
            strategy_key = str(key)
            if isinstance(pnl_data, dict):
                net_pnl = pnl_data.get("total_pnl")
                if net_pnl is not None:
                    break

        stats_returns = getattr(raw_result, "stats_returns", {}) or {}
        # stats_returns may be a dict-per-strategy or flat
        strategy_returns = {}
        if isinstance(stats_returns, dict):
            if strategy_key in stats_returns:
                strategy_returns = stats_returns[strategy_key]
            else:
                strategy_returns = stats_returns

        sharpe = (
            strategy_returns.get("sharpe_ratio")
            if isinstance(strategy_returns, dict)
            else None
        )
        max_dd = (
            strategy_returns.get("max_drawdown")
            if isinstance(strategy_returns, dict)
            else None
        )
        win_rate = (
            strategy_returns.get("win_rate")
            if isinstance(strategy_returns, dict)
            else None
        )

        total_trades = getattr(raw_result, "total_orders", None)
        elapsed = getattr(raw_result, "elapsed_time", None)

        return {
            "combo": combo,
            "strategy_id": strategy_key,
            "net_pnl": (
                round(net_pnl, 2) if isinstance(net_pnl, (int, float)) else net_pnl
            ),
            "sharpe": round(sharpe, 4) if isinstance(sharpe, (int, float)) else sharpe,
            "max_drawdown": (
                round(max_dd, 4) if isinstance(max_dd, (int, float)) else max_dd
            ),
            "win_rate": (
                round(win_rate, 4) if isinstance(win_rate, (int, float)) else win_rate
            ),
            "total_trades": int(total_trades) if total_trades is not None else None,
            "elapsed": (
                round(elapsed, 2) if isinstance(elapsed, (int, float)) else elapsed
            ),
        }
