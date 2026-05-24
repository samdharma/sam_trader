"""Unit tests for portfolio heat monitor.

Validates the acceptance criteria for ticket sam_trader-9z3.10.23:
- Real-time heat tracking across all proposed positions
- Heat threshold warnings (% of NAV)
- Concentration limits per sector/symbol
- Output: heat_map with per-symbol risk contribution
"""

from __future__ import annotations

import pytest

from sam_trader.services.heat_monitor import (
    HeatMonitorConfig,
    HeatMonitorResult,
    PortfolioHeatMonitor,
    ProposedPosition,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def config() -> HeatMonitorConfig:
    return HeatMonitorConfig(
        nav=1_000_000.0,
        heat_threshold_pct=0.05,
        max_symbol_concentration_pct=0.10,
        max_sector_concentration_pct=0.25,
    )


@pytest.fixture
def monitor(config: HeatMonitorConfig) -> PortfolioHeatMonitor:
    return PortfolioHeatMonitor(config)


# ── test_basic_structure ───────────────────────────────────────────────────


class TestBasicStructure:
    """Smoke tests for dataclasses and monitor instantiation."""

    def test_proposed_position_defaults(self) -> None:
        """Venue defaults to FUTU."""
        pos = ProposedPosition(
            instrument_id="TSLA.NASDAQ",
            sector="Technology",
            notional=10_000.0,
            estimated_risk=500.0,
        )
        assert pos.venue == "FUTU"

    def test_config_defaults(self) -> None:
        """Threshold defaults are sensible."""
        cfg = HeatMonitorConfig(nav=500_000.0)
        assert cfg.heat_threshold_pct == 0.05
        assert cfg.max_symbol_concentration_pct == 0.10
        assert cfg.max_sector_concentration_pct == 0.25

    def test_monitor_empty_limits_passes(self, monitor: PortfolioHeatMonitor) -> None:
        """No positions → zero heat, all checks pass."""
        result = monitor.compute([])
        assert result.passed is True
        assert result.total_heat_pct == 0.0
        assert result.total_notional == 0.0
        assert result.heat_map == {}
        assert result.sector_map == {}
        assert result.warnings == []

    def test_result_fields_present(self, monitor: PortfolioHeatMonitor) -> None:
        """HeatMonitorResult contains all expected fields."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="AAPL.NASDAQ",
                    sector="Technology",
                    notional=10_000.0,
                    estimated_risk=200.0,
                ),
            ]
        )
        assert isinstance(result, HeatMonitorResult)
        assert hasattr(result, "total_heat_pct")
        assert hasattr(result, "total_notional")
        assert hasattr(result, "heat_map")
        assert hasattr(result, "sector_map")
        assert hasattr(result, "warnings")
        assert hasattr(result, "passed")


# ── test_heat_tracking ─────────────────────────────────────────────────────


class TestHeatTracking:
    """AC: Real-time heat tracking across all proposed positions."""

    def test_single_position_heat(self, monitor: PortfolioHeatMonitor) -> None:
        """Heat equals estimated_risk / NAV for a single position."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=50_000.0,
                    estimated_risk=5_000.0,
                ),
            ]
        )
        assert result.total_heat_pct == pytest.approx(0.005)
        assert result.passed is True

    def test_multiple_positions_aggregate_risk(
        self, monitor: PortfolioHeatMonitor
    ) -> None:
        """Heat aggregates risk across all positions."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=50_000.0,
                    estimated_risk=5_000.0,
                ),
                ProposedPosition(
                    instrument_id="NVDA.NASDAQ",
                    sector="Technology",
                    notional=30_000.0,
                    estimated_risk=3_000.0,
                ),
            ]
        )
        assert result.total_heat_pct == pytest.approx(0.008)
        assert result.total_notional == 80_000.0
        assert result.passed is True

    def test_heat_map_risk_contribution(self, monitor: PortfolioHeatMonitor) -> None:
        """AC: Output heat_map with per-symbol risk contribution."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=50_000.0,
                    estimated_risk=5_000.0,
                ),
                ProposedPosition(
                    instrument_id="NVDA.NASDAQ",
                    sector="Technology",
                    notional=30_000.0,
                    estimated_risk=3_000.0,
                ),
            ]
        )
        assert "TSLA.NASDAQ" in result.heat_map
        assert "NVDA.NASDAQ" in result.heat_map
        assert result.heat_map["TSLA.NASDAQ"].risk_contribution == 5_000.0
        assert result.heat_map["NVDA.NASDAQ"].risk_contribution == 3_000.0

    def test_heat_map_concentration_pct(self, monitor: PortfolioHeatMonitor) -> None:
        """Concentration is notional / NAV."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=100_000.0,
                    estimated_risk=2_000.0,
                ),
            ]
        )
        entry = result.heat_map["TSLA.NASDAQ"]
        assert entry.concentration_pct == pytest.approx(0.10)


# ── test_heat_threshold_warnings ───────────────────────────────────────────


class TestHeatThresholdWarnings:
    """AC: Heat threshold warnings (% of NAV)."""

    def test_passes_when_under_threshold(self, monitor: PortfolioHeatMonitor) -> None:
        """Aggregate risk below 5 % of NAV passes."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=50_000.0,
                    estimated_risk=1_000.0,
                ),
            ]
        )
        assert result.passed is True
        assert result.warnings == []

    def test_fails_when_over_threshold(self, monitor: PortfolioHeatMonitor) -> None:
        """Aggregate risk above 5 % of NAV triggers warning."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=500_000.0,
                    estimated_risk=60_000.0,
                ),
            ]
        )
        assert result.passed is False
        assert any("Portfolio heat" in w for w in result.warnings)

    def test_exactly_at_threshold_passes(self) -> None:
        """Heat exactly equal to threshold passes (not >)."""
        cfg = HeatMonitorConfig(
            nav=1_000_000.0,
            heat_threshold_pct=0.05,
            max_symbol_concentration_pct=1.0,
            max_sector_concentration_pct=1.0,
        )
        monitor = PortfolioHeatMonitor(cfg)
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=500_000.0,
                    estimated_risk=50_000.0,
                ),
            ]
        )
        assert result.total_heat_pct == pytest.approx(0.05)
        assert result.passed is True


# ── test_symbol_concentration ──────────────────────────────────────────────


class TestSymbolConcentration:
    """AC: Concentration limits per symbol."""

    def test_passes_when_under_limit(self, monitor: PortfolioHeatMonitor) -> None:
        """Symbol notional below 10 % of NAV passes."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=50_000.0,
                    estimated_risk=1_000.0,
                ),
            ]
        )
        assert result.passed is True
        assert result.heat_map["TSLA.NASDAQ"].warning is None

    def test_fails_when_over_limit(self, monitor: PortfolioHeatMonitor) -> None:
        """Symbol notional above 10 % of NAV triggers warning."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=150_000.0,
                    estimated_risk=1_000.0,
                ),
            ]
        )
        assert result.passed is False
        assert any(
            "Symbol concentration limit breached: TSLA.NASDAQ" in w
            for w in result.warnings
        )
        assert result.heat_map["TSLA.NASDAQ"].warning is not None


# ── test_sector_concentration ──────────────────────────────────────────────


class TestSectorConcentration:
    """AC: Concentration limits per sector."""

    def test_passes_when_under_limit(self, monitor: PortfolioHeatMonitor) -> None:
        """Sector notional below 25 % of NAV passes."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=100_000.0,
                    estimated_risk=1_000.0,
                ),
                ProposedPosition(
                    instrument_id="AAPL.NASDAQ",
                    sector="Technology",
                    notional=100_000.0,
                    estimated_risk=1_000.0,
                ),
            ]
        )
        assert result.passed is True
        assert result.sector_map["Technology"] == pytest.approx(0.20)

    def test_fails_when_over_limit(self, monitor: PortfolioHeatMonitor) -> None:
        """Sector notional above 25 % of NAV triggers warning."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=200_000.0,
                    estimated_risk=1_000.0,
                ),
                ProposedPosition(
                    instrument_id="AAPL.NASDAQ",
                    sector="Technology",
                    notional=100_000.0,
                    estimated_risk=1_000.0,
                ),
            ]
        )
        assert result.passed is False
        assert any(
            "Sector concentration limit breached: Technology" in w
            for w in result.warnings
        )


# ── test_input_validation ──────────────────────────────────────────────────


class TestInputValidation:
    """Invalid inputs raise ValueError immediately."""

    def test_zero_nav_raises(self) -> None:
        monitor = PortfolioHeatMonitor(HeatMonitorConfig(nav=0.0))
        with pytest.raises(ValueError, match="NAV must be positive"):
            monitor.compute([])

    def test_negative_nav_raises(self) -> None:
        monitor = PortfolioHeatMonitor(HeatMonitorConfig(nav=-100_000.0))
        with pytest.raises(ValueError, match="NAV must be positive"):
            monitor.compute([])


# ── test_edge_cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary and edge-case behaviour."""

    def test_none_positions_defaults_to_empty(
        self, monitor: PortfolioHeatMonitor
    ) -> None:
        """Omitting positions is equivalent to an empty list."""
        result = monitor.compute(None)
        assert result.passed is True
        assert result.total_heat_pct == 0.0

    def test_multiple_warnings_returned(self, monitor: PortfolioHeatMonitor) -> None:
        """All breached limits report their reasons."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=200_000.0,
                    estimated_risk=60_000.0,
                ),
            ]
        )
        assert result.passed is False
        assert len(result.warnings) >= 2
        assert any("Portfolio heat" in w for w in result.warnings)
        assert any("Symbol concentration" in w for w in result.warnings)

    def test_multi_venue_positions(self, monitor: PortfolioHeatMonitor) -> None:
        """Positions from different venues aggregate correctly."""
        result = monitor.compute(
            [
                ProposedPosition(
                    instrument_id="TSLA.NASDAQ",
                    sector="Technology",
                    notional=50_000.0,
                    estimated_risk=1_000.0,
                    venue="FUTU",
                ),
                ProposedPosition(
                    instrument_id="BABA.NYSE",
                    sector="Consumer",
                    notional=30_000.0,
                    estimated_risk=500.0,
                    venue="IB",
                ),
            ]
        )
        assert result.total_notional == 80_000.0
        assert result.total_heat_pct == pytest.approx(0.0015)
        assert result.passed is True
