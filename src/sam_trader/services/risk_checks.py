"""Pre-trade risk checks — venue-level exposure, loss-limit and margin validation.

Rejects pipeline candidates that would breach configured per-venue risk limits.
Designed for use in the sam-services pre-market pipeline (Phase 9).

Usage
-----
    from sam_trader.services.risk_checks import (
        PreTradeRiskChecker,
        VenueRiskLimits,
        PortfolioState,
        RiskCheckResult,
    )

    limits = {
        "FUTU": VenueRiskLimits(
            max_exposure=500_000.0,
            max_daily_loss=10_000.0,
            margin_requirement_pct=0.5,
            max_notional_per_order=100_000.0,
        ),
    }
    checker = PreTradeRiskChecker(limits)

    portfolio = PortfolioState(
        venue="FUTU",
        open_exposure=200_000.0,
        realized_pnl_today=-2_000.0,
        available_buying_power=150_000.0,
    )

    result = checker.check(
        venue="FUTU",
        instrument_id="TSLA.NASDAQ",
        position_size=100,
        entry_price=150.0,
        stop_price=145.0,
        portfolio=portfolio,
    )
    # result.passed -> bool
    # result.rejected_reasons -> list[str]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VenueRiskLimits:
    """Configurable risk limits for a single venue.

    All monetary fields are in the account base currency (typically USD).
    A value of ``0.0`` disables that specific check (permissive default).
    """

    max_exposure: float = 0.0
    """Maximum total notional exposure (open positions + proposed trade)."""
    max_daily_loss: float = 0.0
    """Maximum absolute daily loss before new trades are blocked."""
    margin_requirement_pct: float = 1.0
    """Margin required as a fraction of notional (1.0 = 100 % cash)."""
    max_notional_per_order: float = 0.0
    """Maximum notional value for a single order."""


@dataclass(frozen=True)
class PortfolioState:
    """Snapshot of portfolio state required for pre-trade risk checks.

    This dataclass is intentionally decoupled from PostgreSQL/Redis so
    that the checker remains fully testable without database dependencies.
    """

    venue: str
    open_exposure: float = 0.0
    """Current open position notional for the venue (shares × mark)."""
    realized_pnl_today: float = 0.0
    """Realized P&L today (negative = loss, positive = profit)."""
    available_buying_power: float = 0.0
    """Cash + margin available for new trades."""


@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of a pre-trade risk check."""

    passed: bool
    """True only if **all** configured checks passed."""
    rejected_reasons: list[str]
    """Human-readable reasons for rejection (empty when ``passed`` is True)."""
    post_trade_exposure: float
    """Projected total exposure if the trade were executed."""
    estimated_risk_dollars: float
    """Estimated max risk for the proposed trade (position_size × |entry − stop|)."""
    required_margin: float
    """Margin dollars required for the proposed trade."""


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


class PreTradeRiskChecker:
    """Validates a proposed trade against per-venue risk limits.

    Parameters
    ----------
    limits : dict[str, VenueRiskLimits] | None
        Mapping ``venue -> limits``.  If a venue is missing, every check
        for that venue passes (permissive default).
    """

    def __init__(self, limits: dict[str, VenueRiskLimits] | None = None) -> None:
        self._limits: dict[str, VenueRiskLimits] = dict(limits) if limits else {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(  # noqa: PLR0913
        self,
        venue: str,
        instrument_id: str,
        position_size: int,
        entry_price: float,
        stop_price: float,
        portfolio: PortfolioState | None = None,
    ) -> RiskCheckResult:
        """Run all configured risk checks for a proposed trade.

        Parameters
        ----------
        venue
            Venue identifier (e.g. ``"FUTU"`` or ``"IB"``).
        instrument_id
            Nautilus instrument identifier (used in log messages).
        position_size
            Number of shares/contracts proposed.
        entry_price
            Expected entry price per share.
        stop_price
            Stop-loss price per share (used for risk estimation).
        portfolio
            Current portfolio snapshot.  If *None*, a zero-state snapshot
            is assumed.

        Returns
        -------
        RiskCheckResult

        """
        if portfolio is None:
            portfolio = PortfolioState(venue=venue)

        self._validate_inputs(
            venue, instrument_id, position_size, entry_price, stop_price
        )

        notional = position_size * entry_price
        risk_per_share = abs(entry_price - stop_price)
        estimated_risk = position_size * risk_per_share
        required_margin = notional * self._margin_pct(venue)
        post_exposure = portfolio.open_exposure + notional

        reasons: list[str] = []

        # 1. Max exposure per venue
        self._check_exposure(venue, post_exposure, reasons)

        # 2. Daily loss limit
        self._check_daily_loss(
            venue, portfolio.realized_pnl_today, estimated_risk, reasons
        )

        # 3. Margin requirement
        self._check_margin(
            venue, required_margin, portfolio.available_buying_power, reasons
        )

        # 4. Max notional per order
        self._check_notional_per_order(venue, notional, reasons)

        passed = len(reasons) == 0

        if not passed:
            logger.warning(
                "Risk check FAILED for %s@%s: %s",
                instrument_id,
                venue,
                "; ".join(reasons),
            )
        else:
            logger.debug(
                "Risk check PASSED for %s@%s (exposure=%.2f risk=%.2f margin=%.2f)",
                instrument_id,
                venue,
                post_exposure,
                estimated_risk,
                required_margin,
            )

        return RiskCheckResult(
            passed=passed,
            rejected_reasons=reasons,
            post_trade_exposure=round(post_exposure, 2),
            estimated_risk_dollars=round(estimated_risk, 2),
            required_margin=round(required_margin, 2),
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_exposure(
        self, venue: str, post_exposure: float, reasons: list[str]
    ) -> None:
        limit = self._limits.get(venue)
        if limit is None:
            return
        max_exp = limit.max_exposure
        if max_exp > 0 and post_exposure > max_exp:
            reasons.append(
                f"Exposure limit exceeded: {post_exposure:,.2f} > {max_exp:,.2f}"
            )

    def _check_daily_loss(
        self,
        venue: str,
        realized_pnl_today: float,
        estimated_risk: float,
        reasons: list[str],
    ) -> None:
        limit = self._limits.get(venue)
        if limit is None:
            return
        max_loss = limit.max_daily_loss
        if max_loss <= 0:
            return

        current_loss = max(0.0, -realized_pnl_today)

        # Already exceeded
        if current_loss >= max_loss:
            reasons.append(
                f"Daily loss limit already breached: "
                f"{-realized_pnl_today:,.2f} >= {max_loss:,.2f}"
            )
            return

        # Would exceed after adding estimated risk
        projected_loss = current_loss + estimated_risk
        if projected_loss > max_loss:
            reasons.append(
                f"Daily loss limit would be exceeded: "
                f"{current_loss:,.2f} + {estimated_risk:,.2f} = "
                f"{projected_loss:,.2f} > {max_loss:,.2f}"
            )

    def _check_margin(
        self,
        venue: str,
        required_margin: float,
        available_buying_power: float,
        reasons: list[str],
    ) -> None:
        limit = self._limits.get(venue)
        if limit is None:
            return
        if limit.margin_requirement_pct <= 0:
            return
        if required_margin > available_buying_power:
            reasons.append(
                f"Insufficient buying power: required margin {required_margin:,.2f} > "
                f"available {available_buying_power:,.2f}"
            )

    def _check_notional_per_order(
        self, venue: str, notional: float, reasons: list[str]
    ) -> None:
        limit = self._limits.get(venue)
        if limit is None:
            return
        max_notional = limit.max_notional_per_order
        if max_notional > 0 and notional > max_notional:
            reasons.append(
                f"Order notional limit exceeded: {notional:,.2f} > {max_notional:,.2f}"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _margin_pct(self, venue: str) -> float:
        limit = self._limits.get(venue)
        if limit is None:
            return 0.0
        return limit.margin_requirement_pct

    @staticmethod
    def _validate_inputs(
        venue: str,
        instrument_id: str,
        position_size: int,
        entry_price: float,
        stop_price: float,
    ) -> None:
        if not venue:
            raise ValueError("venue must be non-empty")
        if not instrument_id:
            raise ValueError("instrument_id must be non-empty")
        if position_size < 0:
            raise ValueError("position_size must be non-negative")
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
