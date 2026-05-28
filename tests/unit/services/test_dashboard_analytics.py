"""Unit tests for dashboard analytics computations."""

from __future__ import annotations

import pytest

from sam_trader.services.dashboard_analytics import (
    EquityPoint,
    PerformanceKPIs,
    compute_drawdown,
    compute_equity_curve,
    compute_kpis,
    render_drawdown_svg,
    render_equity_curve_svg,
)


class TestComputeEquityCurve:
    """Tests for equity curve computation."""

    def test_empty_input(self) -> None:
        """Empty daily P&L returns empty curve."""
        assert compute_equity_curve([]) == []

    def test_single_day(self) -> None:
        """One day produces one point starting from the P&L value."""
        result = compute_equity_curve([{"date": "2026-05-01", "pnl": 100.0}])
        assert len(result) == 1
        assert result[0] == EquityPoint(date="2026-05-01", equity=100.0, pnl=100.0)

    def test_multi_day_cumulative(self) -> None:
        """Equity accumulates daily P&L."""
        result = compute_equity_curve(
            [
                {"date": "2026-05-01", "pnl": 100.0},
                {"date": "2026-05-02", "pnl": -50.0},
                {"date": "2026-05-03", "pnl": 75.0},
            ]
        )
        assert result[0].equity == 100.0
        assert result[1].equity == 50.0
        assert result[2].equity == 125.0

    def test_sorts_by_date(self) -> None:
        """Unordered input is sorted by date."""
        result = compute_equity_curve(
            [
                {"date": "2026-05-03", "pnl": 10.0},
                {"date": "2026-05-01", "pnl": 20.0},
            ]
        )
        assert result[0].date == "2026-05-01"
        assert result[1].date == "2026-05-03"


class TestComputeDrawdown:
    """Tests for drawdown computation."""

    def test_empty_curve(self) -> None:
        """Empty equity curve yields zero drawdown."""
        dd = compute_drawdown([])
        assert dd["current_dd_pct"] == 0.0
        assert dd["max_dd_pct"] == 0.0
        assert dd["events"] == []

    def test_no_drawdown(self) -> None:
        """Always-rising equity has zero drawdown."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=110.0, pnl=10.0),
            EquityPoint(date="2026-05-03", equity=120.0, pnl=10.0),
        ]
        dd = compute_drawdown(points)
        assert dd["current_dd_pct"] == 0.0
        assert dd["max_dd_pct"] == 0.0
        assert dd["events"] == []

    def test_single_drawdown_recovery(self) -> None:
        """One drawdown that recovers produces one event."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=90.0, pnl=-10.0),
            EquityPoint(date="2026-05-03", equity=85.0, pnl=-5.0),
            EquityPoint(date="2026-05-04", equity=110.0, pnl=25.0),
        ]
        dd = compute_drawdown(points)
        assert dd["current_dd_pct"] == 0.0
        assert dd["max_dd_pct"] == -15.0
        assert len(dd["events"]) == 1
        ev = dd["events"][0]
        assert ev.start_date == "2026-05-01"
        assert ev.trough_date == "2026-05-03"
        assert ev.end_date == "2026-05-04"
        assert ev.depth_pct == -15.0
        assert ev.recovery_days == 1

    def test_open_drawdown(self) -> None:
        """Drawdown at end of series without recovery."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=90.0, pnl=-10.0),
            EquityPoint(date="2026-05-03", equity=85.0, pnl=-5.0),
        ]
        dd = compute_drawdown(points)
        assert dd["current_dd_pct"] == -15.0
        assert dd["max_dd_pct"] == -15.0
        assert len(dd["events"]) == 1
        ev = dd["events"][0]
        assert ev.end_date is None
        assert ev.recovery_days is None

    def test_multiple_drawdowns(self) -> None:
        """Multiple distinct drawdown events."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=90.0, pnl=-10.0),
            EquityPoint(date="2026-05-03", equity=110.0, pnl=20.0),
            EquityPoint(date="2026-05-04", equity=95.0, pnl=-15.0),
            EquityPoint(date="2026-05-05", equity=120.0, pnl=25.0),
        ]
        dd = compute_drawdown(points)
        assert len(dd["events"]) == 2
        assert dd["events"][0].depth_pct == -10.0
        assert dd["events"][1].depth_pct == pytest.approx(-13.64, rel=0.01)

    def test_zero_peak_handling(self) -> None:
        """Starting from zero equity avoids division by zero."""
        points = [
            EquityPoint(date="2026-05-01", equity=0.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=-10.0, pnl=-10.0),
        ]
        dd = compute_drawdown(points)
        assert dd["max_dd_pct"] == 0.0  # No division by zero


class TestComputeKPIs:
    """Tests for KPI computation."""

    def test_empty_curve(self) -> None:
        """Empty curve yields all-zero KPIs."""
        kpis = compute_kpis([])
        assert kpis == PerformanceKPIs(
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

    def test_basic_kpis(self) -> None:
        """Simple equity curve produces sensible KPIs."""
        points = [
            EquityPoint(date="2026-05-01", equity=10.0, pnl=10.0),
            EquityPoint(date="2026-05-02", equity=20.0, pnl=10.0),
            EquityPoint(date="2026-05-03", equity=15.0, pnl=-5.0),
            EquityPoint(date="2026-05-04", equity=25.0, pnl=10.0),
        ]
        kpis = compute_kpis(points, lookback_days=4)
        assert kpis.net_pnl == 25.0
        assert kpis.win_rate == 75.0  # 3 wins / 4 days
        assert kpis.max_drawdown_pct == -25.0  # 20 -> 15 is 25% DD
        # Expectancy = 0.75 * 10 - 0.25 * 5 = 7.5 - 1.25 = 6.25
        assert kpis.expectancy == pytest.approx(6.25, rel=0.01)

    def test_delta_vs_prior_period(self) -> None:
        """Delta compares current window with prior window."""
        points = [
            EquityPoint(date="2026-05-01", equity=10.0, pnl=10.0),
            EquityPoint(date="2026-05-02", equity=5.0, pnl=-5.0),
            EquityPoint(date="2026-05-03", equity=20.0, pnl=15.0),
            EquityPoint(date="2026-05-04", equity=25.0, pnl=5.0),
        ]
        kpis = compute_kpis(points, lookback_days=2)
        # Current window = last 2 days: pnl 15 + 5 = 20
        # Prior window = first 2 days: pnl 10 + (-5) = 5
        assert kpis.net_pnl == 20.0
        assert kpis.net_pnl_delta == 15.0

    def test_sharpe_zero_std(self) -> None:
        """Sharpe is zero when all returns are identical."""
        points = [
            EquityPoint(date="2026-05-01", equity=10.0, pnl=10.0),
            EquityPoint(date="2026-05-02", equity=20.0, pnl=10.0),
            EquityPoint(date="2026-05-03", equity=30.0, pnl=10.0),
        ]
        kpis = compute_kpis(points, lookback_days=3)
        assert kpis.sharpe_20d == 0.0


class TestRenderEquityCurveSvg:
    """Tests for inline SVG equity curve rendering."""

    def test_empty_data(self) -> None:
        """Empty data renders a 'No data' SVG."""
        svg = render_equity_curve_svg([])
        assert "No data" in svg

    def test_renders_svg_structure(self) -> None:
        """Basic equity curve produces valid SVG markup."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=110.0, pnl=10.0),
        ]
        svg = render_equity_curve_svg(points)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "<path" in svg
        assert "<circle" in svg

    def test_tooltips_present(self) -> None:
        """Data points have SVG title tooltips."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
        ]
        svg = render_equity_curve_svg(points)
        assert "<title>" in svg
        assert "2026-05-01" in svg


class TestRenderDrawdownSvg:
    """Tests for inline SVG drawdown rendering."""

    def test_empty_data(self) -> None:
        """Empty data renders a 'No data' SVG."""
        svg = render_drawdown_svg([])
        assert "No data" in svg

    def test_renders_svg_structure(self) -> None:
        """Basic drawdown produces valid SVG markup."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=90.0, pnl=-10.0),
        ]
        svg = render_drawdown_svg(points)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "<path" in svg
        # Drawdown uses red fill
        assert "#f85149" in svg

    def test_area_fill_present(self) -> None:
        """Drawdown area is filled below the line."""
        points = [
            EquityPoint(date="2026-05-01", equity=100.0, pnl=0.0),
            EquityPoint(date="2026-05-02", equity=90.0, pnl=-10.0),
        ]
        svg = render_drawdown_svg(points)
        assert "fill-opacity" in svg
