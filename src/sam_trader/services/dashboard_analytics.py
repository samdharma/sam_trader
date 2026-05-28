"""Dashboard analytics computations — pure functions over trade data.

All functions are stateless and operate on plain Python data structures.
No I/O here; callers pass in data fetched from PG/Redis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class EquityPoint:
    """Single point on the equity curve."""

    date: str  # ISO date YYYY-MM-DD
    equity: float  # Cumulative equity value
    pnl: float  # Daily P&L


@dataclass(frozen=True)
class DrawdownEvent:
    """A single drawdown event from peak to recovery."""

    start_date: str  # Date when peak was reached (drawdown begins)
    trough_date: str  # Date of deepest point
    end_date: str | None  # Date when new peak was reached (None if still in drawdown)
    depth_pct: float  # Maximum drawdown percentage (negative)
    recovery_days: int | None  # Days from trough to recovery (None if not recovered)


@dataclass(frozen=True)
class PerformanceKPIs:
    """Five top-row KPIs with delta indicators vs prior period."""

    net_pnl: float
    net_pnl_delta: float
    win_rate: float
    win_rate_delta: float
    sharpe_20d: float
    sharpe_20d_delta: float
    max_drawdown_pct: float
    max_drawdown_delta: float
    expectancy: float
    expectancy_delta: float


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------


def compute_equity_curve(daily_pnl: list[dict[str, Any]]) -> list[EquityPoint]:
    """Build cumulative equity curve from daily P&L rows.

    Each row must have ``date`` (str YYYY-MM-DD) and ``pnl`` (float).
    Returns points sorted by date ascending.
    """
    if not daily_pnl:
        return []

    # Sort by date ascending
    sorted_rows = sorted(daily_pnl, key=lambda r: r.get("date", ""))
    equity = 0.0
    points: list[EquityPoint] = []
    for row in sorted_rows:
        pnl = float(row.get("pnl", 0.0))
        equity += pnl
        points.append(EquityPoint(date=str(row["date"]), equity=equity, pnl=pnl))
    return points


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------


def compute_drawdown(equity_curve: list[EquityPoint]) -> dict[str, Any]:
    """Compute drawdown statistics and events from an equity curve.

    Returns a dict with:
    - ``current_dd_pct``: current drawdown from last peak
    - ``max_dd_pct``: maximum drawdown over the whole series
    - ``events``: list of DrawdownEvent
    """
    if not equity_curve:
        return {
            "current_dd_pct": 0.0,
            "max_dd_pct": 0.0,
            "events": [],
        }

    peak = equity_curve[0].equity
    max_dd_pct = 0.0
    current_dd_pct = 0.0

    events: list[DrawdownEvent] = []
    in_drawdown = False
    dd_start_date = equity_curve[0].date
    dd_trough_date = equity_curve[0].date
    dd_trough_pct = 0.0

    for point in equity_curve:
        if point.equity > peak:
            # New peak — close any active drawdown
            if in_drawdown:
                recovery_days = (
                    datetime.strptime(point.date, "%Y-%m-%d").date()
                    - datetime.strptime(dd_trough_date, "%Y-%m-%d").date()
                ).days
                events.append(
                    DrawdownEvent(
                        start_date=dd_start_date,
                        trough_date=dd_trough_date,
                        end_date=point.date,
                        depth_pct=round(dd_trough_pct, 2),
                        recovery_days=recovery_days,
                    )
                )
            peak = point.equity
            in_drawdown = False
            dd_start_date = point.date
            current_dd_pct = 0.0
        elif point.equity < peak:
            in_drawdown = True
            dd_pct = ((point.equity - peak) / peak * 100) if peak != 0 else 0.0
            if dd_pct < dd_trough_pct:
                dd_trough_pct = dd_pct
                dd_trough_date = point.date
                # trough equity tracked implicitly via dd_trough_pct
            current_dd_pct = dd_pct
            if dd_pct < max_dd_pct:
                max_dd_pct = dd_pct

    # If still in drawdown at end of series, emit an open event
    if in_drawdown:
        events.append(
            DrawdownEvent(
                start_date=dd_start_date,
                trough_date=dd_trough_date,
                end_date=None,
                depth_pct=round(dd_trough_pct, 2),
                recovery_days=None,
            )
        )

    return {
        "current_dd_pct": round(current_dd_pct, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "events": events,
    }


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


def _sharpe(daily_returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Annualized Sharpe ratio from a daily return series."""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns) - risk_free_rate
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance) if variance > 0 else 0.0
    if std_dev == 0:
        return 0.0
    return (mean_r / std_dev) * math.sqrt(252)


def compute_kpis(
    equity_curve: list[EquityPoint], lookback_days: int = 30
) -> PerformanceKPIs:
    """Compute 5 KPIs with delta vs prior period.

    Current period = last *lookback_days* points.
    Prior period = *lookback_days* points before that.
    """
    if not equity_curve:
        return PerformanceKPIs(
            net_pnl=0.0,
            net_pnl_delta=0.0,
            win_rate=0.0,
            win_rate_delta=0.0,
            sharpe_20d=0.0,
            sharpe_20d_delta=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_delta=0.0,
            expectancy=0.0,
            expectancy_delta=0.0,
        )

    # Split into current and prior windows
    current = (
        equity_curve[-lookback_days:]
        if len(equity_curve) > lookback_days
        else equity_curve
    )
    prior_start = max(0, len(equity_curve) - 2 * lookback_days)
    prior = equity_curve[prior_start : prior_start + lookback_days]

    def _window_kpis(window: list[EquityPoint]) -> dict[str, float]:
        pnls = [p.pnl for p in window]
        net_pnl = sum(pnls)
        trades = [p for p in pnls if p != 0]
        win_rate = (
            (sum(1 for p in trades if p > 0) / len(trades) * 100) if trades else 0.0
        )
        sharpe = _sharpe(pnls)
        dd = compute_drawdown(window)
        max_dd = dd["max_dd_pct"]

        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p < 0]
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        wr = win_rate / 100.0
        expectancy = (wr * avg_win) - ((1 - wr) * avg_loss)

        return {
            "net_pnl": net_pnl,
            "win_rate": win_rate,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "expectancy": expectancy,
        }

    cur_k = _window_kpis(current)
    pri_k = _window_kpis(prior) if prior else {k: 0.0 for k in cur_k}

    return PerformanceKPIs(
        net_pnl=round(cur_k["net_pnl"], 2),
        net_pnl_delta=round(cur_k["net_pnl"] - pri_k["net_pnl"], 2),
        win_rate=round(cur_k["win_rate"], 1),
        win_rate_delta=round(cur_k["win_rate"] - pri_k["win_rate"], 1),
        sharpe_20d=round(cur_k["sharpe"], 2),
        sharpe_20d_delta=round(cur_k["sharpe"] - pri_k["sharpe"], 2),
        max_drawdown_pct=round(cur_k["max_dd"], 2),
        max_drawdown_delta=round(cur_k["max_dd"] - pri_k["max_dd"], 2),
        expectancy=round(cur_k["expectancy"], 2),
        expectancy_delta=round(cur_k["expectancy"] - pri_k["expectancy"], 2),
    )


# ---------------------------------------------------------------------------
# SVG chart rendering
# ---------------------------------------------------------------------------


def render_equity_curve_svg(
    points: list[EquityPoint], width: int = 600, height: int = 200
) -> str:
    """Render an inline SVG line chart for the equity curve."""
    if not points:
        no_data_svg = (
            f'<svg width="{width}" height="{height}" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<text x="10" y="20" fill="#8b949e">No data</text></svg>'
        )
        return no_data_svg

    padding = 40
    chart_w = width - padding * 2
    chart_h = height - padding * 2

    equities = [p.equity for p in points]
    min_eq = min(equities)
    max_eq = max(equities)
    eq_range = max_eq - min_eq if max_eq != min_eq else 1.0

    def _x(i: int) -> float:
        return padding + (i / max(1, len(points) - 1)) * chart_w

    def _y(v: float) -> float:
        return padding + chart_h - ((v - min_eq) / eq_range) * chart_h

    # Build path
    path_d = "M " + " L ".join(f"{_x(i)},{_y(v)}" for i, v in enumerate(equities))

    # Axis lines
    axis_h = (
        f'<line x1="{padding}" y1="{height - padding}" '
        f'x2="{width - padding}" y2="{height - padding}" '
        'stroke="#30363d" stroke-width="1"/>'
    )
    axis_v = (
        f'<line x1="{padding}" y1="{padding}" '
        f'x2="{padding}" y2="{height - padding}" '
        'stroke="#30363d" stroke-width="1"/>'
    )
    axes = axis_h + axis_v

    # Labels
    label_y_min = (
        f'<text x="{padding - 5}" y="{height - padding + 4}" '
        'text-anchor="end" fill="#8b949e" font-size="10">'
        f"{min_eq:,.0f}</text>"
    )
    label_y_max = (
        f'<text x="{padding - 5}" y="{padding + 4}" '
        'text-anchor="end" fill="#8b949e" font-size="10">'
        f"{max_eq:,.0f}</text>"
    )

    # Date labels (first, middle, last)
    date_labels = ""
    for idx in (0, len(points) // 2, len(points) - 1):
        date_labels += (
            f'<text x="{_x(idx)}" y="{height - padding + 14}" '
            'text-anchor="middle" fill="#8b949e" font-size="9">'
            f"{points[idx].date[5:]}</text>"
        )

    # Data points with tooltip titles
    circles = ""
    for i, p in enumerate(points):
        circles += (
            f'<circle cx="{_x(i)}" cy="{_y(p.equity)}" '
            'r="3" fill="#58a6ff" opacity="0.7">'
            f"<title>{p.date}: {p.equity:,.2f} "
            f"(P&amp;L {p.pnl:+.2f})</title></circle>"
        )

    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{path_d}" fill="none" stroke="#58a6ff" stroke-width="2"/>'
        f"{axes}{label_y_min}{label_y_max}{date_labels}{circles}"
        f"</svg>"
    )


def render_drawdown_svg(
    points: list[EquityPoint], width: int = 600, height: int = 200
) -> str:
    """Render an inline SVG area chart for drawdown percentage."""
    if not points:
        no_data_svg = (
            f'<svg width="{width}" height="{height}" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<text x="10" y="20" fill="#8b949e">No data</text></svg>'
        )
        return no_data_svg

    padding = 40
    chart_w = width - padding * 2
    chart_h = height - padding * 2

    # Compute drawdown series
    peak = points[0].equity
    dd_series: list[float] = []
    for p in points:
        if p.equity > peak:
            peak = p.equity
        dd_pct = ((p.equity - peak) / peak * 100) if peak != 0 else 0.0
        dd_series.append(dd_pct)

    min_dd = min(dd_series)
    max_dd = max(dd_series)  # Usually 0 or close to it
    dd_range = max_dd - min_dd if max_dd != min_dd else 1.0

    def _x(i: int) -> float:
        return padding + (i / max(1, len(points) - 1)) * chart_w

    def _y(v: float) -> float:
        return padding + chart_h - ((v - min_dd) / dd_range) * chart_h

    # Area path: start at first point, go through all points, close at baseline
    first_x = _x(0)
    line_d = "M " + " L ".join(f"{_x(i)},{_y(v)}" for i, v in enumerate(dd_series))
    # Close the area at the bottom of the chart (min_dd line)
    last_x = _x(len(points) - 1)
    bottom_y = _y(min_dd)
    area_d = f"{line_d} L {last_x},{bottom_y} L {first_x},{bottom_y} Z"

    axis_h = (
        f'<line x1="{padding}" y1="{height - padding}" '
        f'x2="{width - padding}" y2="{height - padding}" '
        'stroke="#30363d" stroke-width="1"/>'
    )
    axis_v = (
        f'<line x1="{padding}" y1="{padding}" '
        f'x2="{padding}" y2="{height - padding}" '
        'stroke="#30363d" stroke-width="1"/>'
    )
    axes = axis_h + axis_v

    label_y_min = (
        f'<text x="{padding - 5}" y="{height - padding + 4}" '
        'text-anchor="end" fill="#8b949e" font-size="10">'
        f"{min_dd:.1f}%</text>"
    )
    label_y_max = (
        f'<text x="{padding - 5}" y="{padding + 4}" '
        'text-anchor="end" fill="#8b949e" font-size="10">'
        f"{max_dd:.1f}%</text>"
    )

    date_labels = ""
    for idx in (0, len(points) // 2, len(points) - 1):
        date_labels += (
            f'<text x="{_x(idx)}" y="{height - padding + 14}" '
            'text-anchor="middle" fill="#8b949e" font-size="9">'
            f"{points[idx].date[5:]}</text>"
        )

    # Tooltip circles
    circles = ""
    for i, (p, dd) in enumerate(zip(points, dd_series)):
        circles += (
            f'<circle cx="{_x(i)}" cy="{_y(dd)}" '
            'r="3" fill="#f85149" opacity="0.7">'
            f"<title>{p.date}: DD {dd:.2f}%</title></circle>"
        )

    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{area_d}" fill="#f85149" fill-opacity="0.25" stroke="none"/>'
        f'<path d="{line_d}" fill="none" stroke="#f85149" stroke-width="2"/>'
        f"{axes}{label_y_min}{label_y_max}{date_labels}{circles}"
        f"</svg>"
    )
