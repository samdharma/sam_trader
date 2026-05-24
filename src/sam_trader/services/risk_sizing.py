"""Monte Carlo position sizer with VaR-based risk limits.

Computes conservative share counts by running geometric-Brownian-motion
simulations and capping the naive stop-loss sizing with a VaR-based limit.

Usage
-----
    from sam_trader.services.risk_sizing import (
        MonteCarloPositionSizer,
        SizerConfig,
        PositionSizeResult,
    )

    sizer = MonteCarloPositionSizer(SizerConfig(simulation_count=10_000))
    result = sizer.size(
        capital=100_000.0,
        risk_per_trade=1_000.0,
        stop_loss_pct=0.02,
        daily_volatility=0.015,
        entry_price=150.0,
    )
    # result.position_size   -> conservative share count
    # result.max_risk_dollars -> dollar risk at stop-loss level
    # result.var_95            -> 95 % VaR in dollars for the sized position
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionSizeResult:
    """Output of a position-sizing computation."""

    position_size: int
    """Conservative number of shares to trade."""
    max_risk_dollars: float
    """Dollar risk if the stop-loss is hit (position_size × entry × stop_loss_pct)."""
    var_95: float
    """VaR in dollars for the sized position at the configured confidence level."""


@dataclass(frozen=True)
class SizerConfig:
    """Configuration for the Monte Carlo position sizer."""

    simulation_count: int = 10_000
    """Number of Monte Carlo paths to simulate."""
    confidence_level: float = 0.95
    """Confidence level for VaR computation (e.g. 0.95 for 95 %)."""
    holding_period_days: int = 1
    """Holding period over which to simulate price evolution."""
    random_seed: int | None = None
    """Optional seed for reproducible simulations."""


# ---------------------------------------------------------------------------
# Sizer
# ---------------------------------------------------------------------------


class MonteCarloPositionSizer:
    """Position sizer that uses Monte Carlo simulation to respect VaR limits.

    The sizing logic is:

    1. **Naive size** — ``risk_per_trade / (entry_price * stop_loss_pct)``.
    2. **MC VaR size** — simulate *N* price paths, compute the loss
       distribution, and derive the share count that keeps the VaR-based
       risk within ``risk_per_trade``.
    3. **Conservative size** — ``min(naive, mc_var)``.
    """

    def __init__(self, config: SizerConfig | None = None) -> None:
        self.config = config or SizerConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def size(
        self,
        capital: float,
        risk_per_trade: float,
        stop_loss_pct: float,
        daily_volatility: float,
        entry_price: float = 100.0,
    ) -> PositionSizeResult:
        """Return a conservative position size.

        Parameters
        ----------
        capital
            Total trading capital (used as a sanity-check ceiling).
        risk_per_trade
            Maximum dollars willing to lose on this single trade.
        stop_loss_pct
            Stop-loss distance as a decimal fraction (e.g. 0.02 for 2 %).
        daily_volatility
            Expected daily volatility as a decimal fraction
            (e.g. 0.015 for 1.5 %).
        entry_price
            Expected entry price per share.  Defaults to ``100.0`` for
            back-of-the-envelope sizing; real usage should pass the
            actual mid / limit price.

        Returns
        -------
        PositionSizeResult

        Raises
        ------
        ValueError
            If any input is non-positive or if ``stop_loss_pct`` ≥ 1.
        """
        self._validate_inputs(
            capital, risk_per_trade, stop_loss_pct, daily_volatility, entry_price
        )

        # 1. Naive sizing — purely stop-loss based
        stop_loss_dollars = entry_price * stop_loss_pct
        naive_shares = risk_per_trade / stop_loss_dollars

        # 2. Monte-Carlo VaR sizing
        var_per_share = self._mc_var_per_share(entry_price, daily_volatility)
        # Avoid division-by-zero on pathological volatility
        var_based_shares = risk_per_trade / max(var_per_share, 1e-12)

        # 3. Conservative sizing
        conservative_shares = min(naive_shares, var_based_shares)

        # 4. Hard ceiling — cannot deploy more than capital allows
        max_shares_by_capital = capital / entry_price
        conservative_shares = min(conservative_shares, max_shares_by_capital)

        position_size = int(conservative_shares)
        if position_size < 1:
            position_size = 0

        max_risk_dollars = position_size * stop_loss_dollars
        var_95 = position_size * var_per_share

        logger.debug(
            "size(capital=%s risk=%s stop=%.4f vol=%.4f entry=%.2f) -> "
            "shares=%d max_risk=%.2f var_95=%.2f",
            capital,
            risk_per_trade,
            stop_loss_pct,
            daily_volatility,
            entry_price,
            position_size,
            max_risk_dollars,
            var_95,
        )

        return PositionSizeResult(
            position_size=position_size,
            max_risk_dollars=round(max_risk_dollars, 2),
            var_95=round(var_95, 2),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_inputs(
        self,
        capital: float,
        risk_per_trade: float,
        stop_loss_pct: float,
        daily_volatility: float,
        entry_price: float,
    ) -> None:
        if capital <= 0:
            raise ValueError("capital must be positive")
        if risk_per_trade <= 0:
            raise ValueError("risk_per_trade must be positive")
        if not (0 < stop_loss_pct < 1):
            raise ValueError("stop_loss_pct must be in (0, 1)")
        if daily_volatility <= 0:
            raise ValueError("daily_volatility must be positive")
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if risk_per_trade > capital:
            raise ValueError("risk_per_trade cannot exceed capital")

    def _mc_var_per_share(self, entry_price: float, daily_volatility: float) -> float:
        """Run a Monte Carlo simulation and return the VaR per share.

        We model the log-return over *holding_period_days* as
        ``N(0, daily_volatility * sqrt(holding_period_days))`` and
        apply it geometrically:  ``S = S0 * exp(return)``.

        The per-share loss is ``max(0, S0 - S)`` (long-only assumption).
        """
        cfg = self.config
        rng = np.random.default_rng(cfg.random_seed)

        sigma = daily_volatility * np.sqrt(cfg.holding_period_days)
        # Zero-drift assumption — we size for risk, not expected return
        returns = rng.normal(loc=0.0, scale=sigma, size=cfg.simulation_count)
        simulated_prices = entry_price * np.exp(returns)
        losses = np.maximum(0.0, entry_price - simulated_prices)

        percentile = cfg.confidence_level * 100.0
        var_per_share = float(np.percentile(losses, percentile))
        return var_per_share
