"""Unit tests for Monte Carlo position sizer.

Validates the acceptance criteria for ticket sam_trader-9z3.10.21:
- Monte Carlo simulation for position sizing (default 10,000 sims)
- VaR-based risk limit computation
- Inputs: capital, risk_per_trade, stop_loss_pct, daily_volatility
- Output: position_size (shares), max_risk_dollars, var_95
- Configurable simulation count and confidence level
"""

from __future__ import annotations

import pytest

from sam_trader.services.risk_sizing import (
    MonteCarloPositionSizer,
    PositionSizeResult,
    SizerConfig,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def default_sizer() -> MonteCarloPositionSizer:
    return MonteCarloPositionSizer(SizerConfig(random_seed=42))


@pytest.fixture
def small_sizer() -> MonteCarloPositionSizer:
    """Fast sizer with only 1,000 simulations for quicker tests."""
    return MonteCarloPositionSizer(SizerConfig(simulation_count=1_000, random_seed=42))


# ── test_monte_carlo_sizer ─────────────────────────────────────────────────


class TestMonteCarloSizer:
    """Tests for the core ``size()`` method and Monte Carlo behaviour."""

    def test_default_simulation_count(
        self, default_sizer: MonteCarloPositionSizer
    ) -> None:
        """AC: default simulation count is 10,000."""
        assert default_sizer.config.simulation_count == 10_000

    def test_output_fields_present(self, small_sizer: MonteCarloPositionSizer) -> None:
        """AC: output contains position_size, max_risk_dollars, var_95."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.015,
            entry_price=100.0,
        )
        assert isinstance(result, PositionSizeResult)
        assert hasattr(result, "position_size")
        assert hasattr(result, "max_risk_dollars")
        assert hasattr(result, "var_95")

    def test_position_size_is_non_negative_integer(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """AC: position_size is a non-negative integer."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.015,
            entry_price=100.0,
        )
        assert isinstance(result.position_size, int)
        assert result.position_size >= 0

    def test_naive_sizing_when_volatility_low(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """When volatility is very low, VaR is smaller than stop-loss,
        so the naive stop-loss sizing dominates."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.001,  # 0.1 % daily vol — extremely low
            entry_price=100.0,
        )
        # Naive shares = 1_000 / (100 * 0.02) = 500
        # With near-zero vol, MC VaR is tiny, so var-based shares >> 500
        # Conservative = min(500, huge) = 500
        assert result.position_size == 500
        assert result.max_risk_dollars == pytest.approx(1_000.0, rel=0.01)

    def test_var_caps_position_when_volatility_high(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """When volatility is high, MC VaR exceeds the stop-loss distance,
        so the VaR-based limit caps the position size below the naive count."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.10,  # 10 % daily vol — very high
            entry_price=100.0,
        )
        # Naive shares = 500, but VaR per share will be > $2, so
        # var-based shares < 500.  Conservative should be < 500.
        assert result.position_size < 500
        assert result.position_size > 0

    def test_capital_ceiling_respected(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """Position cannot exceed what capital allows."""
        result = small_sizer.size(
            capital=5_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.001,
            entry_price=100.0,
        )
        # Capital allows at most 5_000 / 100 = 50 shares
        assert result.position_size <= 50

    def test_reproducible_with_same_seed(self) -> None:
        """Same seed → identical result."""
        sizer_a = MonteCarloPositionSizer(
            SizerConfig(simulation_count=5_000, random_seed=123)
        )
        sizer_b = MonteCarloPositionSizer(
            SizerConfig(simulation_count=5_000, random_seed=123)
        )
        res_a = sizer_a.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.03,
            entry_price=100.0,
        )
        res_b = sizer_b.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.03,
            entry_price=100.0,
        )
        assert res_a == res_b

    def test_different_seed_can_vary_slightly(self) -> None:
        """Different seeds → possibly different results (non-deterministic MC)."""
        sizer_a = MonteCarloPositionSizer(
            SizerConfig(simulation_count=5_000, random_seed=1)
        )
        sizer_b = MonteCarloPositionSizer(
            SizerConfig(simulation_count=5_000, random_seed=2)
        )
        res_a = sizer_a.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.05,
            entry_price=100.0,
        )
        res_b = sizer_b.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.05,
            entry_price=100.0,
        )
        # They should be close, but not guaranteed identical
        assert abs(res_a.position_size - res_b.position_size) <= 20

    def test_position_size_zero_when_risk_too_small(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """If risk_per_trade is smaller than one share's stop-loss, size = 0."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1.0,
            stop_loss_pct=0.02,
            daily_volatility=0.015,
            entry_price=100.0,
        )
        # Naive shares = 1 / 2 = 0.5 -> int -> 0
        assert result.position_size == 0
        assert result.max_risk_dollars == 0.0
        assert result.var_95 == 0.0

    @pytest.mark.parametrize(
        "capital,risk,stop,vol,entry",
        [
            (0, 1_000, 0.02, 0.015, 100.0),
            (100_000, 0, 0.02, 0.015, 100.0),
            (100_000, 1_000, 0, 0.015, 100.0),
            (100_000, 1_000, 1.0, 0.015, 100.0),
            (100_000, 1_000, 0.02, 0, 100.0),
            (100_000, 1_000, 0.02, 0.015, 0),
            (100_000, 200_000, 0.02, 0.015, 100.0),
        ],
    )
    def test_invalid_inputs_raise(
        self,
        small_sizer: MonteCarloPositionSizer,
        capital: float,
        risk: float,
        stop: float,
        vol: float,
        entry: float,
    ) -> None:
        """All non-positive or out-of-range inputs raise ValueError."""
        with pytest.raises(ValueError):
            small_sizer.size(
                capital=capital,
                risk_per_trade=risk,
                stop_loss_pct=stop,
                daily_volatility=vol,
                entry_price=entry,
            )


# ── test_var_limit ───────────────────────────────────────────────────────────


class TestVaRLimit:
    """Tests specifically for the VaR-based risk limit behaviour."""

    def test_var_95_is_positive(self, small_sizer: MonteCarloPositionSizer) -> None:
        """AC: var_95 is a positive dollar amount."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.02,
            entry_price=100.0,
        )
        assert result.var_95 > 0

    def test_var_95_scales_with_position_size(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """Doubling risk_per_trade (therefore shares) should roughly double var_95."""
        r1 = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=500.0,
            stop_loss_pct=0.02,
            daily_volatility=0.02,
            entry_price=100.0,
        )
        r2 = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.02,
            entry_price=100.0,
        )
        # r2 should have ~2× the shares, so ~2× the VaR
        assert r2.position_size >= r1.position_size
        if r1.position_size > 0:
            ratio = r2.var_95 / r1.var_95
            assert pytest.approx(ratio, rel=0.15) == r2.position_size / r1.position_size

    def test_var_95_increases_with_volatility(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """Higher volatility → higher VaR per share → fewer shares."""
        r_low = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.01,
            entry_price=100.0,
        )
        r_high = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.05,
            entry_price=100.0,
        )
        assert r_high.var_95 > r_low.var_95
        assert r_high.position_size <= r_low.position_size

    def test_confidence_level_is_configurable(self) -> None:
        """AC: confidence level is configurable.

        A 99 % VaR should be larger than a 90 % VaR for the same inputs,
        producing a smaller (more conservative) position size.
        """
        sizer_90 = MonteCarloPositionSizer(
            SizerConfig(simulation_count=5_000, confidence_level=0.90, random_seed=42)
        )
        sizer_99 = MonteCarloPositionSizer(
            SizerConfig(simulation_count=5_000, confidence_level=0.99, random_seed=42)
        )
        r90 = sizer_90.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.03,
            entry_price=100.0,
        )
        r99 = sizer_99.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.03,
            entry_price=100.0,
        )
        assert r99.var_95 >= r90.var_95
        assert r99.position_size <= r90.position_size

    def test_var_95_does_not_exceed_risk_per_trade(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """The VaR-based sizing should keep var_95 ≈ risk_per_trade.

        When volatility is high the VaR limit is the binding constraint,
        so the position is sized so that its total VaR is close to
        *risk_per_trade*.
        """
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.05,
            entry_price=100.0,
        )
        # With high vol the VaR limit caps the position, so total VaR
        # should be approximately equal to risk_per_trade.
        assert result.var_95 <= 1_050.0
        assert result.var_95 >= 900.0

    def test_max_risk_dollars_matches_stop_loss(
        self, small_sizer: MonteCarloPositionSizer
    ) -> None:
        """max_risk_dollars = position_size × entry_price × stop_loss_pct."""
        entry = 123.45
        stop = 0.03
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=2_000.0,
            stop_loss_pct=stop,
            daily_volatility=0.02,
            entry_price=entry,
        )
        expected = result.position_size * entry * stop
        assert result.max_risk_dollars == pytest.approx(expected, abs=0.01)

    def test_entry_price_default(self, small_sizer: MonteCarloPositionSizer) -> None:
        """Calling ``size()`` without entry_price uses the default 100.0."""
        result = small_sizer.size(
            capital=100_000.0,
            risk_per_trade=1_000.0,
            stop_loss_pct=0.02,
            daily_volatility=0.015,
        )
        assert result.position_size > 0
        assert result.max_risk_dollars == pytest.approx(
            result.position_size * 100.0 * 0.02, abs=0.01
        )
