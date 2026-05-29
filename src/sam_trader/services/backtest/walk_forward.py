"""Walk-forward optimization — rolling train/test windows.

Implements the walk-forward analysis pattern described in
BUILD_PLAN_12.1.md §7: for each rolling window, sweep parameters on the
train (in-sample) period, select the best by Sharpe, then run a single
backtest on the test (out-of-sample) period.  An aggregate stability
report is produced.

Usage::

    from sam_trader.services.backtest.walk_forward import WalkForward

    wf = WalkForward(
        wrapper=BacktestEngineWrapper(catalog_path="data/catalog"),
        base_strategies=[orb_bundle],
        instrument_ids=["TSLA.NASDAQ"],
        bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
        train_days=90,
        test_days=30,
        data_start="2024-01-01",
        data_end="2024-12-31",
    )
    result = wf.run(param_grid={"stop_loss_ticks": [5, 10, 15]})
    print(wf.format_report(result))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.engine import BacktestEngineWrapper
from sam_trader.services.backtest.sweep import ParameterSweep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WindowResult:
    """Result for a single walk-forward train/test window.

    Attributes
    ----------
    train_start : str
        ISO-format start date of the training (in-sample) period.
    train_end : str
        ISO-format end date of the training period.
    test_start : str
        ISO-format start date of the test (out-of-sample) period.
    test_end : str
        ISO-format end date of the test period.
    best_params : dict
        Parameter combination with the highest train-period Sharpe.
    train_sharpe : float | None
        Sharpe ratio of the best parameter set on the train period.
    test_sharpe : float | None
        Sharpe ratio of the best parameter set on the test period.
    test_pnl : float | None
        Total P&L on the test period.
    test_win_rate : float | None
        Win rate on the test period.
    test_max_dd : float | None
        Max drawdown on the test period.
    test_trades : int | None
        Total orders on the test period.
    error : str | None
        Error message if this window could not be processed.
    """

    train_start: str = ""
    train_end: str = ""
    test_start: str = ""
    test_end: str = ""
    best_params: dict[str, Any] = field(default_factory=dict)
    train_sharpe: float | None = None
    test_sharpe: float | None = None
    test_pnl: float | None = None
    test_win_rate: float | None = None
    test_max_dd: float | None = None
    test_trades: int | None = None
    error: str | None = None


@dataclass
class WalkForwardResult:
    """Aggregate walk-forward report.

    Attributes
    ----------
    windows : list[WindowResult]
        Per-window results in chronological order.
    overall_sharpe : float | None
        Mean test-period Sharpe across all windows.
    overall_pnl : float | None
        Sum of test-period P&L across all windows.
    profitable_windows : int
        Number of windows with positive test-period P&L.
    total_windows : int
        Total number of windows processed.
    param_stability : dict[str, dict[str, int]]
        For each sweep parameter, maps each value to the number of
        windows where it was selected as the best.
    config : dict[str, Any]
        Walk-forward configuration for traceability.
    """

    windows: list[WindowResult] = field(default_factory=list)
    overall_sharpe: float | None = None
    overall_pnl: float | None = None
    profitable_windows: int = 0
    total_windows: int = 0
    param_stability: dict[str, dict[str, int]] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WalkForward
# ---------------------------------------------------------------------------


class WalkForward:
    """Orchestrate walk-forward optimization with rolling train/test windows.

    For each window:
      1. Sweep parameters on the *train* period.
      2. Select the best parameter combination by Sharpe ratio.
      3. Run a single backtest on the *test* period with those params.
      4. Record window results.

    After all windows are processed, an aggregate stability report
    summarises overall performance and parameter preference consistency.

    Parameters
    ----------
    wrapper : BacktestEngineWrapper
        Pre-configured engine wrapper with catalog path set.
    base_strategies : list[ImportableStrategyConfig]
        Strategy configuration(s) to evaluate.
    instrument_ids : list[str]
        Instrument IDs for data loading.
    bar_types : list[str]
        Bar type strings for data loading.
    train_days : int
        Number of *calendar* days in each training window.
    test_days : int
        Number of *calendar* days in each test window.
    data_start : str
        Earliest date for which data is available (ISO format).
    data_end : str
        Latest date for which data is available (ISO format).

    """

    def __init__(
        self,
        wrapper: BacktestEngineWrapper,
        base_strategies: list[ImportableStrategyConfig],
        instrument_ids: list[str],
        bar_types: list[str],
        train_days: int,
        test_days: int,
        data_start: str,
        data_end: str,
    ) -> None:
        self._wrapper = wrapper
        self._base_strategies = base_strategies
        self._instrument_ids = instrument_ids
        self._bar_types = bar_types
        self._train_days = train_days
        self._test_days = test_days
        self._data_start = data_start
        self._data_end = data_end

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        param_grid: dict[str, list],
        *,
        run_analysis: bool = True,
    ) -> WalkForwardResult:
        """Execute walk-forward optimization across all rolling windows.

        Parameters
        ----------
        param_grid : dict[str, list]
            Parameter name → list of values to sweep over.
        run_analysis : bool
            When ``True`` (default), auto-compute portfolio statistics.

        Returns
        -------
        WalkForwardResult
            Aggregate report with per-window results, overall metrics,
            and parameter stability statistics.

        Raises
        ------
        ValueError
            If the parameter grid is empty or train/test days are invalid.

        """
        if not param_grid:
            raise ValueError("Parameter grid cannot be empty")

        if self._train_days <= 0:
            raise ValueError(f"train_days must be positive, got {self._train_days}")
        if self._test_days <= 0:
            raise ValueError(f"test_days must be positive, got {self._test_days}")

        windows = self._generate_windows()

        if not windows:
            logger.warning(
                "No walk-forward windows generated: data range (%s → %s) "
                "shorter than train+test period (%d + %d days).",
                self._data_start,
                self._data_end,
                self._train_days,
                self._test_days,
            )
            return WalkForwardResult(
                windows=[],
                overall_sharpe=None,
                overall_pnl=None,
                profitable_windows=0,
                total_windows=0,
                param_stability={},
                config=self._build_config(),
            )

        window_results: list[WindowResult] = []
        param_counts: dict[str, dict[str, int]] = {}

        for train_start, train_end, test_start, test_end in windows:
            wr = self._process_window(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                param_grid=param_grid,
                run_analysis=run_analysis,
            )
            window_results.append(wr)

            # Update parameter stability counts
            if wr.best_params and not wr.error:
                for key, val in wr.best_params.items():
                    if key not in param_counts:
                        param_counts[key] = {}
                    val_str = str(val)
                    param_counts[key][val_str] = param_counts[key].get(val_str, 0) + 1

        # Compute aggregate metrics
        test_sharpes: list[float] = []
        total_pnl: float = 0.0
        profitable: int = 0

        for wr in window_results:
            if wr.test_sharpe is not None and not wr.error:
                test_sharpes.append(wr.test_sharpe)
            if wr.test_pnl is not None and not wr.error:
                total_pnl += wr.test_pnl
                if wr.test_pnl > 0:
                    profitable += 1

        overall_sharpe: float | None = None
        if test_sharpes:
            overall_sharpe = round(sum(test_sharpes) / len(test_sharpes), 4)

        overall_pnl: float | None = round(total_pnl, 2) if window_results else None

        return WalkForwardResult(
            windows=window_results,
            overall_sharpe=overall_sharpe,
            overall_pnl=overall_pnl,
            profitable_windows=profitable,
            total_windows=len(window_results),
            param_stability=param_counts,
            config=self._build_config(),
        )

    # ------------------------------------------------------------------
    # Report formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_report(result: WalkForwardResult) -> str:
        """Format a :class:`WalkForwardResult` as a human-readable report.

        Parameters
        ----------
        result : WalkForwardResult
            The walk-forward result to format.

        Returns
        -------
        str
            Multi-line formatted report string.

        """
        if result.total_windows == 0:
            return (
                "Walk-Forward Report\n"
                "====================\n"
                "No windows generated — data range shorter than "
                "train + test period."
            )

        lines: list[str] = []
        lines.append("Walk-Forward Report")
        lines.append("=" * 80)

        # Config header
        cfg = result.config
        lines.append(
            f"Train: {cfg.get('train_days')}d  |  "
            f"Test: {cfg.get('test_days')}d  |  "
            f"Data: {cfg.get('data_start')} → {cfg.get('data_end')}  |  "
            f"Instruments: {', '.join(cfg.get('instrument_ids', []))}"
        )
        lines.append("")

        # Aggregate summary
        lines.append("─" * 80)
        lines.append("AGGREGATE SUMMARY")
        lines.append("─" * 80)
        lines.append(
            f"  Overall Sharpe (mean test):     " f"{result.overall_sharpe:.4f}"
            if result.overall_sharpe is not None
            else "  Overall Sharpe (mean test):       N/A"
        )
        lines.append(
            f"  Total Test P&L:                " f"{result.overall_pnl:+.2f}"
            if result.overall_pnl is not None
            else "  Total Test P&L:                  N/A"
        )
        lines.append(
            f"  Profitable Windows:            "
            f"{result.profitable_windows}/{result.total_windows} "
            f"({result.profitable_windows / result.total_windows * 100:.0f}%)"
            if result.total_windows > 0
            else "  0/0"
        )

        # Parameter stability
        if result.param_stability:
            lines.append("")
            lines.append("─" * 80)
            lines.append("PARAMETER STABILITY (selection frequency per window)")
            lines.append("─" * 80)
            for param_name in sorted(result.param_stability.keys()):
                counts = result.param_stability[param_name]
                total_selections = sum(counts.values())
                # Show most popular first
                sorted_vals = sorted(counts.items(), key=lambda x: x[1], reverse=True)
                parts: list[str] = []
                for val_str, count in sorted_vals:
                    pct = count / total_selections * 100
                    marker = " ★" if count == max(counts.values()) else ""
                    parts.append(f"{val_str}: {count} ({pct:.0f}%){marker}")
                lines.append(f"  {param_name}:  {', '.join(parts)}")

        # Per-window table
        lines.append("")
        lines.append("─" * 80)
        lines.append("PER-WINDOW RESULTS")
        lines.append("─" * 80)

        # Header
        header = (
            f"{'Train':^25s} │ {'Test':^25s} │ "
            f"{'Best Params':^30s} │ "
            f"{'Trn Sharpe':>10s} │ {'Tst Sharpe':>10s} │ "
            f"{'Tst P&L':>10s} │ {'Win Rate':>8s}"
        )
        lines.append(header)
        lines.append("─" * 80)

        for wr in result.windows:
            train_range = f"{wr.train_start} → {wr.train_end}"
            test_range = f"{wr.test_start} → {wr.test_end}"

            # Compact best-params display
            param_parts = [f"{k}={v}" for k, v in wr.best_params.items()]
            best_str = ", ".join(param_parts) if param_parts else "—"

            train_sharpe_str = (
                f"{wr.train_sharpe:.4f}" if wr.train_sharpe is not None else "N/A"
            )
            test_sharpe_str = (
                f"{wr.test_sharpe:.4f}" if wr.test_sharpe is not None else "N/A"
            )
            test_pnl_str = f"{wr.test_pnl:+.2f}" if wr.test_pnl is not None else "N/A"
            win_rate_str = (
                f"{wr.test_win_rate:.1%}" if wr.test_win_rate is not None else "N/A"
            )

            if wr.error:
                row = (
                    f"{train_range:<25s} │ {test_range:<25s} │ "
                    f"{'[ERROR]':<30s} │ "
                    f"{'—':>10s} │ {'—':>10s} │ {'—':>10s} │ {'—':>8s}"
                )
            else:
                row = (
                    f"{train_range:<25s} │ {test_range:<25s} │ "
                    f"{best_str:<30s} │ "
                    f"{train_sharpe_str:>10s} │ {test_sharpe_str:>10s} │ "
                    f"{test_pnl_str:>10s} │ {win_rate_str:>8s}"
                )
            lines.append(row)

        # Error summary
        errors = [wr for wr in result.windows if wr.error]
        if errors:
            lines.append("")
            lines.append("─" * 80)
            lines.append(
                f"WARNINGS / ERRORS ({len(errors)} of {result.total_windows} windows)"
            )
            lines.append("─" * 80)
            for wr in errors:
                lines.append(f"  {wr.train_start} → {wr.test_end}: {wr.error}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: window generation
    # ------------------------------------------------------------------

    def _generate_windows(
        self,
    ) -> list[tuple[str, str, str, str]]:
        """Generate rolling train/test windows.

        Returns
        -------
        list[tuple[str, str, str, str]]
            List of ``(train_start, train_end, test_start, test_end)``
            tuples in ISO format.  An empty list means the data range
            is shorter than the combined train+test period.

        The first window starts at ``data_start``.  Each successive
        window advances by ``test_days`` (roll-forward step = test
        period length).

        """
        try:
            d_start = datetime.strptime(self._data_start, "%Y-%m-%d")
            d_end = datetime.strptime(self._data_end, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                f"Invalid date format (expected ISO YYYY-MM-DD): {exc}"
            ) from exc

        if d_start >= d_end:
            raise ValueError(
                f"data_start ({self._data_start}) must be before "
                f"data_end ({self._data_end})"
            )

        train_td = timedelta(days=self._train_days)
        test_td = timedelta(days=self._test_days)
        step_td = timedelta(days=self._test_days)

        # Edge case: data too short for even one full train+test window
        if d_start + train_td + test_td > d_end:
            # Try with a single shortened window.
            # Require the shortened train to be at least as long as test_days
            # so the window has a minimally meaningful train period.
            train_end_date = d_end - test_td
            min_train_end = d_start + timedelta(days=self._test_days)
            if train_end_date >= min_train_end:
                return [
                    (
                        d_start.strftime("%Y-%m-%d"),
                        train_end_date.strftime("%Y-%m-%d"),
                        (train_end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                        d_end.strftime("%Y-%m-%d"),
                    )
                ]
            # Data is too short even for a minimally-meaningful train period.
            return []

        windows: list[tuple[str, str, str, str]] = []
        cursor = d_start

        while cursor + train_td + test_td <= d_end:
            train_end = cursor + train_td - timedelta(days=1)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + test_td - timedelta(days=1)

            # Clamp test_end to data_end (last window may be shorter)
            actual_test_end = min(test_end, d_end)

            windows.append(
                (
                    cursor.strftime("%Y-%m-%d"),
                    train_end.strftime("%Y-%m-%d"),
                    test_start.strftime("%Y-%m-%d"),
                    actual_test_end.strftime("%Y-%m-%d"),
                )
            )

            cursor += step_td

        return windows

    # ------------------------------------------------------------------
    # Internal: per-window processing
    # ------------------------------------------------------------------

    def _process_window(
        self,
        train_start: str,
        train_end: str,
        test_start: str,
        test_end: str,
        param_grid: dict[str, list],
        run_analysis: bool,
    ) -> WindowResult:
        """Process a single walk-forward window.

        1. Sweep parameters on train period.
        2. Select best by Sharpe.
        3. Run single backtest on test period.

        """
        # Step 1: Sweep on train period
        try:
            sweeper = ParameterSweep(
                wrapper=self._wrapper,
                base_strategies=list(self._base_strategies),
                instrument_ids=self._instrument_ids,
                bar_types=self._bar_types,
                start=train_start,
                end=train_end,
            )
            sweep_results = sweeper.run(
                param_grid=param_grid,
                run_analysis=run_analysis,
            )
        except Exception as exc:
            logger.warning(
                "Sweep failed for train window %s → %s: %s",
                train_start,
                train_end,
                exc,
            )
            return WindowResult(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                error=f"Train sweep failed: {exc}",
            )

        if not sweep_results:
            return WindowResult(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                error="No sweep results — possible data gap in train period",
            )

        # Step 2: Select best by Sharpe (already sorted descending by sweep)
        best = sweep_results[0]
        best_params: dict[str, Any] = best.get("combo", {})
        train_sharpe: float | None = best.get("sharpe")

        # Step 3: Run on test period with best params
        try:
            from sam_trader.services.backtest.sweep import _patch_strategy_config

            patched_strategies = [
                _patch_strategy_config(s, best_params) for s in self._base_strategies
            ]

            test_result = self._wrapper.run(
                strategies=patched_strategies,
                instrument_ids=self._instrument_ids,
                bar_types=self._bar_types,
                start=test_start,
                end=test_end,
                run_analysis=run_analysis,
            )
        except Exception as exc:
            logger.warning(
                "Test backtest failed for window %s → %s: %s",
                test_start,
                test_end,
                exc,
            )
            return WindowResult(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                best_params=best_params,
                train_sharpe=train_sharpe,
                error=f"Test backtest failed: {exc}",
            )

        # Extract test metrics
        test_sharpe, test_pnl, test_win_rate, test_max_dd, test_trades = (
            self._extract_test_metrics(test_result)
        )

        return WindowResult(
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            best_params=best_params,
            train_sharpe=train_sharpe,
            test_sharpe=test_sharpe,
            test_pnl=test_pnl,
            test_win_rate=test_win_rate,
            test_max_dd=test_max_dd,
            test_trades=test_trades,
        )

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_test_metrics(
        test_result: Any,
    ) -> tuple[
        float | None,
        float | None,
        float | None,
        float | None,
        int | None,
    ]:
        """Extract key metrics from a test-period BacktestResult.

        Returns (sharpe, pnl, win_rate, max_dd, total_trades).
        """
        stats_pnls = getattr(test_result, "stats_pnls", {}) or {}
        stats_returns = getattr(test_result, "stats_returns", {}) or {}

        # stats_returns might be flat or per-strategy
        strategy_returns: dict[str, Any] = {}
        if isinstance(stats_returns, dict):
            # Try per-strategy first
            if stats_pnls:
                first_key = next(iter(stats_pnls))
                candidate = (
                    stats_returns.get(first_key)
                    if isinstance(stats_returns, dict)
                    else None
                )
                strategy_returns = (
                    candidate if isinstance(candidate, dict) else stats_returns
                )
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

        # P&L — from stats_pnls
        total_pnl: float | None = None
        for _key, pnl_data in stats_pnls.items():
            if isinstance(pnl_data, dict):
                pnl_val = pnl_data.get("total_pnl")
                if isinstance(pnl_val, (int, float)):
                    total_pnl = float(pnl_val)
                break

        trades = getattr(test_result, "total_orders", None)
        total_trades: int | None = int(trades) if trades is not None else None

        return (
            round(sharpe, 4) if isinstance(sharpe, (int, float)) else sharpe,
            round(total_pnl, 2) if isinstance(total_pnl, (int, float)) else total_pnl,
            round(win_rate, 4) if isinstance(win_rate, (int, float)) else win_rate,
            round(max_dd, 4) if isinstance(max_dd, (int, float)) else max_dd,
            total_trades,
        )

    def _build_config(self) -> dict[str, Any]:
        """Build a config snapshot for the result report."""
        return {
            "train_days": self._train_days,
            "test_days": self._test_days,
            "data_start": self._data_start,
            "data_end": self._data_end,
            "instrument_ids": list(self._instrument_ids),
            "bar_types": list(self._bar_types),
        }


# ---------------------------------------------------------------------------
# Standalone helper: parse train/test day flags
# ---------------------------------------------------------------------------


def parse_days_flag(raw: str) -> int:
    """Parse a ``--train`` / ``--test`` flag value into an integer day count.

    Accepts bare integers (``"90"``) or suffixed (``"90d"``).

    Raises
    ------
    ValueError
        If the value cannot be parsed.

    """
    raw = raw.strip().lower()
    if raw.endswith("d"):
        raw = raw[:-1]
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid day value: {raw!r} — expected an integer "
            f"(e.g., '90' or '90d')"
        ) from exc
    if days <= 0:
        raise ValueError(f"Day count must be positive, got {days}")
    return days
