"""Portfolio heat monitor — real-time concentration and risk-heat tracking.

Tracks aggregate risk heat and concentration across all proposed positions
in the pre-market pipeline.  Designed for use in sam-services (Phase 9).

Usage
-----
    from sam_trader.services.heat_monitor import (
        PortfolioHeatMonitor,
        HeatMonitorConfig,
        ProposedPosition,
        HeatMonitorResult,
    )

    config = HeatMonitorConfig(nav=1_000_000.0)
    monitor = PortfolioHeatMonitor(config)

    positions = [
        ProposedPosition(
            instrument_id="TSLA.NASDAQ",
            sector="Technology",
            notional=50_000.0,
            estimated_risk=1_000.0,
        ),
    ]

    result = monitor.compute(positions)
    # result.total_heat_pct -> 0.1 %
    # result.heat_map -> per-symbol breakdown
    # result.warnings -> list of breached limits
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedPosition:
    """A single proposed trade candidate."""

    instrument_id: str
    sector: str
    notional: float
    estimated_risk: float
    venue: str = "FUTU"


@dataclass(frozen=True)
class HeatMonitorConfig:
    """Configuration for portfolio heat monitoring.

    All percentage fields are expressed as decimals
    (e.g. ``0.05`` for 5 %).
    """

    nav: float
    """Net asset value — denominator for all percentage calculations."""
    heat_threshold_pct: float = 0.05
    """Maximum aggregate risk as a fraction of NAV before a warning is emitted."""
    max_symbol_concentration_pct: float = 0.10
    """Maximum notional concentration for a single symbol."""
    max_sector_concentration_pct: float = 0.25
    """Maximum notional concentration for a single sector."""


@dataclass(frozen=True)
class HeatMapEntry:
    """Per-symbol breakdown in the heat map."""

    instrument_id: str
    notional: float
    risk_contribution: float
    concentration_pct: float
    warning: str | None = None


@dataclass(frozen=True)
class HeatMonitorResult:
    """Outcome of a portfolio heat computation."""

    total_heat_pct: float
    total_notional: float
    heat_map: dict[str, HeatMapEntry]
    sector_map: dict[str, float]
    warnings: list[str]
    passed: bool


# ---------------------------------------------------------------------------
# Heat monitor
# ---------------------------------------------------------------------------


class PortfolioHeatMonitor:
    """Computes portfolio heat and concentration for a set of proposed positions.

    Parameters
    ----------
    config : HeatMonitorConfig | None
        Monitoring thresholds and NAV.  If *None*, a default config with
        ``nav=0.0`` is used (you must still override NAV before calling
        :meth:`compute`).
    """

    def __init__(self, config: HeatMonitorConfig | None = None) -> None:
        self.config = config or HeatMonitorConfig(nav=0.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(
        self,
        positions: list[ProposedPosition] | None = None,
    ) -> HeatMonitorResult:
        """Calculate heat map and warnings for *positions*.

        Parameters
        ----------
        positions
            List of proposed positions to analyse.  Defaults to an empty
            list (zero heat).

        Returns
        -------
        HeatMonitorResult

        Raises
        ------
        ValueError
            If ``config.nav`` is not positive.
        """
        if positions is None:
            positions = []

        if self.config.nav <= 0:
            raise ValueError("NAV must be positive")

        total_risk = 0.0
        total_notional = 0.0
        heat_map: dict[str, HeatMapEntry] = {}
        sector_notional: dict[str, float] = {}

        # -- First pass: aggregate ---------------------------------
        for pos in positions:
            total_risk += pos.estimated_risk
            total_notional += pos.notional
            sector_notional[pos.sector] = (
                sector_notional.get(pos.sector, 0.0) + pos.notional
            )
            heat_map[pos.instrument_id] = HeatMapEntry(
                instrument_id=pos.instrument_id,
                notional=pos.notional,
                risk_contribution=pos.estimated_risk,
                concentration_pct=round(pos.notional / self.config.nav, 6),
            )

        total_heat_pct = total_risk / self.config.nav
        sector_map = {
            sector: round(notional / self.config.nav, 6)
            for sector, notional in sector_notional.items()
        }

        # -- Second pass: warnings ---------------------------------
        warnings: list[str] = []

        if total_heat_pct > self.config.heat_threshold_pct:
            warnings.append(
                f"Portfolio heat {total_heat_pct:.2%} exceeds threshold "
                f"{self.config.heat_threshold_pct:.2%}"
            )

        for instrument_id, entry in list(heat_map.items()):
            if entry.concentration_pct > self.config.max_symbol_concentration_pct:
                msg = (
                    f"Symbol concentration limit breached: {instrument_id} "
                    f"{entry.concentration_pct:.2%} > "
                    f"{self.config.max_symbol_concentration_pct:.2%}"
                )
                warnings.append(msg)
                heat_map[instrument_id] = replace(entry, warning=msg)

        for sector, pct in sector_map.items():
            if pct > self.config.max_sector_concentration_pct:
                warnings.append(
                    f"Sector concentration limit breached: {sector} "
                    f"{pct:.2%} > {self.config.max_sector_concentration_pct:.2%}"
                )

        passed = len(warnings) == 0

        if not passed:
            logger.warning(
                "Heat monitor: %d warning(s): %s",
                len(warnings),
                "; ".join(warnings),
            )
        else:
            logger.debug(
                "Heat monitor: total_heat=%.4f notional=%.2f positions=%d",
                total_heat_pct,
                total_notional,
                len(positions),
            )

        return HeatMonitorResult(
            total_heat_pct=round(total_heat_pct, 6),
            total_notional=round(total_notional, 2),
            heat_map=heat_map,
            sector_map=sector_map,
            warnings=warnings,
            passed=passed,
        )
