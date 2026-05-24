"""Unit tests for pre-trade risk checks.

Validates the acceptance criteria for ticket sam_trader-9z3.10.22:
- Max exposure per venue check
- Daily loss limit enforcement
- Margin requirement validation
- Reject candidates that fail any check
- Configurable limits per venue
"""

from __future__ import annotations

import pytest

from sam_trader.services.risk_checks import (
    PortfolioState,
    PreTradeRiskChecker,
    RiskCheckResult,
    VenueRiskLimits,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def futu_limits() -> dict[str, VenueRiskLimits]:
    return {
        "FUTU": VenueRiskLimits(
            max_exposure=500_000.0,
            max_daily_loss=10_000.0,
            margin_requirement_pct=0.5,
            max_notional_per_order=100_000.0,
        ),
    }


@pytest.fixture
def checker(futu_limits: dict[str, VenueRiskLimits]) -> PreTradeRiskChecker:
    return PreTradeRiskChecker(futu_limits)


@pytest.fixture
def zero_portfolio() -> PortfolioState:
    """Zero-state portfolio with ample buying power so margin check passes."""
    return PortfolioState(venue="FUTU", available_buying_power=1_000_000.0)


# ── test_basic_structure ───────────────────────────────────────────────────


class TestBasicStructure:
    """Smoke tests for dataclasses and checker instantiation."""

    def test_venue_risk_limits_defaults(self) -> None:
        """All-zero defaults mean checks are disabled."""
        limits = VenueRiskLimits()
        assert limits.max_exposure == 0.0
        assert limits.max_daily_loss == 0.0
        assert limits.margin_requirement_pct == 1.0
        assert limits.max_notional_per_order == 0.0

    def test_portfolio_state_defaults(self) -> None:
        """PortfolioState defaults to zero state."""
        state = PortfolioState(venue="IB")
        assert state.open_exposure == 0.0
        assert state.realized_pnl_today == 0.0
        assert state.available_buying_power == 0.0

    def test_checker_empty_limits_passes_everything(self) -> None:
        """No limits configured → every check passes."""
        c = PreTradeRiskChecker({})
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=1_000,
            entry_price=500.0,
            stop_price=450.0,
        )
        assert result.passed is True
        assert result.rejected_reasons == []

    def test_result_fields_present(self, checker: PreTradeRiskChecker) -> None:
        """RiskCheckResult contains all expected fields."""
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=PortfolioState(venue="FUTU", available_buying_power=1_000_000.0),
        )
        assert isinstance(result, RiskCheckResult)
        assert hasattr(result, "passed")
        assert hasattr(result, "rejected_reasons")
        assert hasattr(result, "post_trade_exposure")
        assert hasattr(result, "estimated_risk_dollars")
        assert hasattr(result, "required_margin")


# ── test_exposure_check ────────────────────────────────────────────────────


class TestMaxExposurePerVenue:
    """AC: Max exposure per venue check."""

    def test_passes_when_under_limit(self, checker: PreTradeRiskChecker) -> None:
        """Trade that keeps exposure under limit passes."""
        portfolio = PortfolioState(
            venue="FUTU", open_exposure=400_000.0, available_buying_power=1_000_000.0
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is True
        assert result.post_trade_exposure == 450_000.0

    def test_fails_when_over_limit(self, checker: PreTradeRiskChecker) -> None:
        """Trade that would exceed max exposure is rejected."""
        portfolio = PortfolioState(
            venue="FUTU", open_exposure=480_000.0, available_buying_power=1_000_000.0
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is False
        assert any("Exposure limit exceeded" in r for r in result.rejected_reasons)

    def test_zero_limit_disabled(self) -> None:
        """max_exposure=0 disables the exposure check."""
        limits = {"FUTU": VenueRiskLimits(max_exposure=0.0, margin_requirement_pct=0.0)}
        c = PreTradeRiskChecker(limits)
        portfolio = PortfolioState(venue="FUTU", open_exposure=1_000_000.0)
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is True

    def test_exactly_at_limit_passes(self, checker: PreTradeRiskChecker) -> None:
        """Exposure exactly equal to limit passes (not >)."""
        portfolio = PortfolioState(
            venue="FUTU", open_exposure=450_000.0, available_buying_power=1_000_000.0
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is True
        assert result.post_trade_exposure == 500_000.0


# ── test_daily_loss_check ──────────────────────────────────────────────────


class TestDailyLossLimit:
    """AC: Daily loss limit enforcement."""

    def test_passes_when_no_loss(self, checker: PreTradeRiskChecker) -> None:
        """Portfolio with zero realized P&L passes."""
        portfolio = PortfolioState(
            venue="FUTU", realized_pnl_today=0.0, available_buying_power=1_000_000.0
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is True

    def test_passes_when_small_loss(self, checker: PreTradeRiskChecker) -> None:
        """Portfolio with small realized loss still passes."""
        portfolio = PortfolioState(
            venue="FUTU",
            realized_pnl_today=-1_000.0,
            available_buying_power=1_000_000.0,
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=portfolio,
        )
        assert result.passed is True

    def test_fails_when_already_breached(self, checker: PreTradeRiskChecker) -> None:
        """If realized P&L already exceeds daily loss limit, reject immediately."""
        portfolio = PortfolioState(
            venue="FUTU",
            realized_pnl_today=-15_000.0,
            available_buying_power=1_000_000.0,
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=portfolio,
        )
        assert result.passed is False
        assert any("already breached" in r for r in result.rejected_reasons)

    def test_fails_when_would_exceed(self, checker: PreTradeRiskChecker) -> None:
        """If current loss + estimated risk would exceed limit, reject."""
        portfolio = PortfolioState(
            venue="FUTU",
            realized_pnl_today=-8_000.0,
            available_buying_power=1_000_000.0,
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=500,
            entry_price=100.0,
            stop_price=90.0,
            portfolio=portfolio,
        )
        # Estimated risk = 500 * 10 = 5_000
        # |−8_000| + 5_000 = 13_000 > 10_000 limit
        assert result.passed is False
        assert any("would be exceeded" in r for r in result.rejected_reasons)

    def test_zero_limit_disabled(self) -> None:
        """max_daily_loss=0 disables the daily loss check."""
        limits = {
            "FUTU": VenueRiskLimits(max_daily_loss=0.0, margin_requirement_pct=0.0)
        }
        c = PreTradeRiskChecker(limits)
        portfolio = PortfolioState(venue="FUTU", realized_pnl_today=-1_000_000.0)
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=portfolio,
        )
        assert result.passed is True

    def test_profit_does_not_block(self, checker: PreTradeRiskChecker) -> None:
        """Positive realized P&L never blocks on daily loss limit."""
        portfolio = PortfolioState(
            venue="FUTU",
            realized_pnl_today=50_000.0,
            available_buying_power=1_000_000.0,
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=portfolio,
        )
        assert result.passed is True


# ── test_margin_check ──────────────────────────────────────────────────────


class TestMarginRequirement:
    """AC: Margin requirement validation."""

    def test_passes_when_sufficient_buying_power(
        self, checker: PreTradeRiskChecker
    ) -> None:
        """Trade within available buying power passes."""
        portfolio = PortfolioState(venue="FUTU", available_buying_power=100_000.0)
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        # Required margin = 100 * 500 * 0.5 = 25_000
        assert result.passed is True
        assert result.required_margin == 25_000.0

    def test_fails_when_insufficient_buying_power(
        self, checker: PreTradeRiskChecker
    ) -> None:
        """Trade exceeding available buying power is rejected."""
        portfolio = PortfolioState(venue="FUTU", available_buying_power=10_000.0)
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is False
        assert any("Insufficient buying power" in r for r in result.rejected_reasons)

    def test_zero_margin_disabled(self) -> None:
        """margin_requirement_pct=0 disables margin check."""
        limits = {"FUTU": VenueRiskLimits(margin_requirement_pct=0.0)}
        c = PreTradeRiskChecker(limits)
        portfolio = PortfolioState(venue="FUTU", available_buying_power=0.0)
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is True
        assert result.required_margin == 0.0

    def test_full_margin_requirement(self) -> None:
        """margin_requirement_pct=1.0 requires full cash."""
        limits = {"FUTU": VenueRiskLimits(margin_requirement_pct=1.0)}
        c = PreTradeRiskChecker(limits)
        portfolio = PortfolioState(venue="FUTU", available_buying_power=49_999.0)
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is False
        assert result.required_margin == 50_000.0


# ── test_notional_per_order ────────────────────────────────────────────────


class TestMaxNotionalPerOrder:
    """Order-level notional limit."""

    def test_passes_when_under_order_limit(self, checker: PreTradeRiskChecker) -> None:
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=PortfolioState(venue="FUTU", available_buying_power=1_000_000.0),
        )
        # Notional = 50_000 < 100_000 limit
        assert result.passed is True

    def test_fails_when_over_order_limit(self, checker: PreTradeRiskChecker) -> None:
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=300,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=PortfolioState(venue="FUTU", available_buying_power=1_000_000.0),
        )
        # Notional = 150_000 > 100_000 limit
        assert result.passed is False
        assert any(
            "Order notional limit exceeded" in r for r in result.rejected_reasons
        )

    def test_zero_limit_disabled(self) -> None:
        limits = {
            "FUTU": VenueRiskLimits(
                max_notional_per_order=0.0, margin_requirement_pct=0.0
            )
        }
        c = PreTradeRiskChecker(limits)
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10_000,
            entry_price=500.0,
            stop_price=490.0,
        )
        assert result.passed is True


# ── test_rejection ─────────────────────────────────────────────────────────


class TestRejectionBehavior:
    """AC: Reject candidates that fail any check."""

    def test_single_failure_rejects(self, checker: PreTradeRiskChecker) -> None:
        """One failing check is enough to reject."""
        portfolio = PortfolioState(venue="FUTU", available_buying_power=1.0)
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is False
        assert len(result.rejected_reasons) >= 1

    def test_multiple_reasons_returned(self, checker: PreTradeRiskChecker) -> None:
        """All failing checks report their reasons."""
        portfolio = PortfolioState(
            venue="FUTU",
            open_exposure=490_000.0,
            realized_pnl_today=-15_000.0,
            available_buying_power=1.0,
        )
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
            portfolio=portfolio,
        )
        assert result.passed is False
        assert len(result.rejected_reasons) >= 3
        assert any("Exposure" in r for r in result.rejected_reasons)
        assert any("loss limit" in r for r in result.rejected_reasons)
        assert any("buying power" in r for r in result.rejected_reasons)

    def test_passed_has_empty_reasons(self, checker: PreTradeRiskChecker) -> None:
        """Successful checks return an empty reasons list."""
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=10,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=PortfolioState(venue="FUTU", available_buying_power=1_000_000.0),
        )
        assert result.passed is True
        assert result.rejected_reasons == []


# ── test_per_venue_config ──────────────────────────────────────────────────


class TestPerVenueConfiguration:
    """AC: Configurable limits per venue."""

    def test_different_limits_per_venue(self) -> None:
        """FUTU and IB can have independent limit profiles."""
        limits = {
            "FUTU": VenueRiskLimits(
                max_exposure=500_000.0,
                max_daily_loss=10_000.0,
                margin_requirement_pct=0.5,
            ),
            "IB": VenueRiskLimits(
                max_exposure=1_000_000.0,
                max_daily_loss=25_000.0,
                margin_requirement_pct=0.25,
            ),
        }
        c = PreTradeRiskChecker(limits)

        futu_result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
        )
        ib_result = c.check(
            venue="IB",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=500.0,
            stop_price=490.0,
        )
        # FUTU requires 25_000 margin, IB requires 12_500
        assert futu_result.required_margin == 25_000.0
        assert ib_result.required_margin == 12_500.0

    def test_venue_without_limits_is_permissive(self) -> None:
        """A venue not in the limits dict has all checks disabled."""
        limits = {"FUTU": VenueRiskLimits(max_exposure=100.0)}
        c = PreTradeRiskChecker(limits)
        result = c.check(
            venue="IB",
            instrument_id="TSLA.NASDAQ",
            position_size=10_000,
            entry_price=500.0,
            stop_price=490.0,
        )
        assert result.passed is True

    def test_missing_venue_no_crash(self) -> None:
        """Checking a missing venue does not raise."""
        c = PreTradeRiskChecker({})
        result = c.check(
            venue="UNKNOWN",
            instrument_id="XYZ",
            position_size=100,
            entry_price=10.0,
            stop_price=9.0,
        )
        assert result.passed is True


# ── test_input_validation ──────────────────────────────────────────────────


class TestInputValidation:
    """Invalid inputs raise ValueError immediately."""

    def test_empty_venue_raises(self) -> None:
        c = PreTradeRiskChecker({})
        with pytest.raises(ValueError, match="venue must be non-empty"):
            c.check(
                venue="",
                instrument_id="TSLA.NASDAQ",
                position_size=10,
                entry_price=100.0,
                stop_price=95.0,
            )

    def test_empty_instrument_raises(self) -> None:
        c = PreTradeRiskChecker({})
        with pytest.raises(ValueError, match="instrument_id must be non-empty"):
            c.check(
                venue="FUTU",
                instrument_id="",
                position_size=10,
                entry_price=100.0,
                stop_price=95.0,
            )

    def test_negative_position_size_raises(self) -> None:
        c = PreTradeRiskChecker({})
        with pytest.raises(ValueError, match="position_size must be non-negative"):
            c.check(
                venue="FUTU",
                instrument_id="TSLA.NASDAQ",
                position_size=-1,
                entry_price=100.0,
                stop_price=95.0,
            )

    def test_zero_entry_price_raises(self) -> None:
        c = PreTradeRiskChecker({})
        with pytest.raises(ValueError, match="entry_price must be positive"):
            c.check(
                venue="FUTU",
                instrument_id="TSLA.NASDAQ",
                position_size=10,
                entry_price=0.0,
                stop_price=95.0,
            )

    def test_zero_stop_price_raises(self) -> None:
        c = PreTradeRiskChecker({})
        with pytest.raises(ValueError, match="stop_price must be positive"):
            c.check(
                venue="FUTU",
                instrument_id="TSLA.NASDAQ",
                position_size=10,
                entry_price=100.0,
                stop_price=0.0,
            )


# ── test_edge_cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary and edge-case behaviour."""

    def test_position_size_zero_passes(self) -> None:
        """A zero-size order trivially passes all checks."""
        limits = {
            "FUTU": VenueRiskLimits(
                max_exposure=100.0,
                max_daily_loss=100.0,
                margin_requirement_pct=1.0,
                max_notional_per_order=100.0,
            )
        }
        c = PreTradeRiskChecker(limits)
        portfolio = PortfolioState(
            venue="FUTU",
            open_exposure=0.0,
            realized_pnl_today=0.0,
            available_buying_power=0.0,
        )
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=0,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=portfolio,
        )
        assert result.passed is True
        assert result.estimated_risk_dollars == 0.0
        assert result.required_margin == 0.0

    def test_stop_above_entry_still_computes_risk(
        self, checker: PreTradeRiskChecker
    ) -> None:
        """Risk uses absolute difference, so stop > entry still works."""
        result = checker.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=100.0,
            stop_price=110.0,
            portfolio=PortfolioState(venue="FUTU", available_buying_power=1_000_000.0),
        )
        assert result.estimated_risk_dollars == 1_000.0

    def test_portfolio_none_defaults_to_zero(
        self, checker: PreTradeRiskChecker
    ) -> None:
        """Omitting portfolio is equivalent to all-zero state."""
        # Use a checker with only notional limit so margin doesn't fail
        limits = {
            "FUTU": VenueRiskLimits(
                max_notional_per_order=100_000.0, margin_requirement_pct=0.0
            )
        }
        c = PreTradeRiskChecker(limits)
        result = c.check(
            venue="FUTU",
            instrument_id="TSLA.NASDAQ",
            position_size=100,
            entry_price=100.0,
            stop_price=95.0,
            portfolio=None,
        )
        assert result.passed is True
        assert result.post_trade_exposure == 10_000.0
