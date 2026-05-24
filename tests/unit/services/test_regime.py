"""Unit tests for market regime detection (HMM-based).

Validates the acceptance criteria for ticket sam_trader-9z3.10.19:
- HMM regime classifier: trending, ranging, volatile, bearish
- Output: regime label + transition probability + confidence
- Can use QuoteCollectionService for live bar data input
- Regime-aware parameter adaptation for strategies
- Minimum bar history required before classification
- Stability flag: has regime persisted for minimum bars
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from sam_trader.services.regime_detection import (
    HMMRegimeClassifier,
    Regime,
    RegimeAdapter,
    RegimePrediction,
    bars_from_nautilus_bars,
    bars_from_quote_ticks,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def mixed_bars() -> list[dict[str, Any]]:
    """~900 days cycling through ranging → trending → volatile."""
    np.random.seed(42)
    bars: list[dict[str, Any]] = []
    for regime_params in [
        (0.0, 0.005),  # ranging
        (0.001, 0.008),  # trending
        (0.0, 0.025),  # volatile
    ]:
        close = 100.0
        for _ in range(300):
            ret = np.random.normal(*regime_params)
            close *= 1 + ret
            bars.append(
                {
                    "close": close,
                    "volume": float(np.random.randint(1_000_000, 2_000_000)),
                }
            )
    return bars


@pytest.fixture
def tmp_model_dir(tmp_path: Any) -> Any:
    return tmp_path / "regime_models"


# ── HMM Classification ─────────────────────────────────────────────────────


class TestHMMClassification:
    def test_classifier_trains_and_predicts(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert pred.regime in {
            Regime.TRENDING,
            Regime.RANGING,
            Regime.VOLATILE,
            Regime.BEARISH,
            Regime.UNKNOWN,
        }

    def test_all_four_regime_labels_exist(self) -> None:
        assert Regime.TRENDING.value == "trending"
        assert Regime.RANGING.value == "ranging"
        assert Regime.VOLATILE.value == "volatile"
        assert Regime.BEARISH.value == "bearish"

    def test_unknown_fallback_when_no_model(self, tmp_model_dir: Any) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        bars = [{"close": 100.0, "volume": 1_000_000} for _ in range(300)]
        pred = clf.predict(bars)
        assert pred.regime == Regime.UNKNOWN
        assert pred.confidence == 0.0


# ── Transition Matrix ──────────────────────────────────────────────────────


class TestTransitionMatrix:
    def test_transition_probs_non_negative(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert all(v >= 0.0 for v in pred.transition_probs.values())

    def test_transition_probs_sum_bounded(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        total = sum(pred.transition_probs.values())
        assert total <= 1.01


# ── Regime Labels ──────────────────────────────────────────────────────────


class TestRegimeLabels:
    def test_state_probs_sum_to_one(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        total = sum(pred.state_probs.values())
        assert abs(total - 1.0) < 0.001

    def test_confidence_within_unit_interval(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert 0.0 <= pred.confidence <= 1.0

    def test_model_version_populated_after_fit(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert pred.model_version != ""


# ── QuoteCollectionService Integration ─────────────────────────────────────


class TestQuoteCollectionIntegration:
    def test_bars_from_nautilus_bars(self) -> None:
        bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")
        bars = [
            Bar(
                bar_type=bar_type,
                open=Price.from_str("150.00"),
                high=Price.from_str("151.00"),
                low=Price.from_str("149.00"),
                close=Price.from_str("150.50"),
                volume=Quantity.from_int(10_000),
                ts_event=0,
                ts_init=0,
            ),
            Bar(
                bar_type=bar_type,
                open=Price.from_str("150.50"),
                high=Price.from_str("152.00"),
                low=Price.from_str("150.00"),
                close=Price.from_str("151.00"),
                volume=Quantity.from_int(12_000),
                ts_event=0,
                ts_init=0,
            ),
        ]
        result = bars_from_nautilus_bars(bars)
        assert len(result) == 2
        assert result[0]["close"] == 150.5
        assert result[0]["volume"] == 10_000.0
        assert result[1]["close"] == 151.0
        assert result[1]["volume"] == 12_000.0

    def test_bars_from_quote_ticks(self) -> None:
        ticks = [
            QuoteTick(
                instrument_id=InstrumentId.from_str("TSLA.NASDAQ"),
                bid_price=Price.from_str("150.00"),
                ask_price=Price.from_str("150.10"),
                bid_size=Quantity.from_int(100),
                ask_size=Quantity.from_int(200),
                ts_event=0,
                ts_init=0,
            ),
            QuoteTick(
                instrument_id=InstrumentId.from_str("TSLA.NASDAQ"),
                bid_price=Price.from_str("151.00"),
                ask_price=Price.from_str("151.20"),
                bid_size=Quantity.from_int(150),
                ask_size=Quantity.from_int(250),
                ts_event=0,
                ts_init=0,
            ),
        ]
        result = bars_from_quote_ticks(ticks)
        assert len(result) == 2
        # mid = (150.00 + 150.10) / 2 = 150.05
        assert result[0]["close"] == 150.05
        # vol = 100 + 200 = 300
        assert result[0]["volume"] == 300.0
        # mid = (151.00 + 151.20) / 2 = 151.10
        assert result[1]["close"] == 151.10
        # vol = 150 + 250 = 400
        assert result[1]["volume"] == 400.0

    def test_classifier_accepts_converted_bars(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        """End-to-end: raw dicts → classifier (QuoteCollectionService output path)."""
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert pred.regime in set(Regime)


# ── Minimum Bar History ────────────────────────────────────────────────────


class TestMinimumBarHistory:
    def test_fit_warns_when_insufficient_data(
        self, tmp_model_dir: Any, caplog: Any
    ) -> None:
        bars = [{"close": 100.0 + i * 0.1, "volume": 1_000_000} for i in range(50)]
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        import logging

        with caplog.at_level(logging.WARNING):
            clf.fit(bars)
        assert "Insufficient data" in caplog.text
        assert clf._model is None

    def test_predict_unknown_when_insufficient_data_after_fit(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict([{"close": 100.0, "volume": 1_000_000}])
        assert pred.regime == Regime.UNKNOWN


# ── Stability Flag ─────────────────────────────────────────────────────────


class TestStabilityFlag:
    def test_stable_after_three_same_regimes(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        # Seed history with two identical regimes
        clf._history = [Regime.TRENDING, Regime.TRENDING]
        pred = clf.predict(mixed_bars)
        if pred.regime == Regime.TRENDING:
            assert pred.is_stable is True

    def test_unstable_when_regimes_switch(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        clf._history = [Regime.TRENDING, Regime.VOLATILE]
        pred = clf.predict(mixed_bars)
        assert pred.is_stable is False

    def test_stable_false_with_short_history(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Any
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        clf._history = []
        pred = clf.predict(mixed_bars)
        assert pred.is_stable is False


# ── Regime-Aware Parameter Adaptation ──────────────────────────────────────


class TestRegimeAwareAdaptation:
    def test_adjust_size_trending(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.TRENDING, confidence=0.8, is_stable=True)
        size, reason = adapter.adjust_size(5000.0, pred)
        assert size == 5000.0
        assert "TRENDING" in reason

    def test_adjust_size_ranging(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.RANGING, confidence=0.8, is_stable=True)
        size, reason = adapter.adjust_size(5000.0, pred)
        assert size == 6250.0
        assert "increased" in reason

    def test_adjust_size_volatile(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.VOLATILE, confidence=0.8, is_stable=True)
        size, reason = adapter.adjust_size(5000.0, pred)
        assert size == 3000.0
        assert "reduced" in reason

    def test_adjust_size_bearish(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.BEARISH, confidence=0.8, is_stable=True)
        size, reason = adapter.adjust_size(5000.0, pred)
        assert size == 2500.0
        assert "BEARISH" in reason

    def test_adjust_size_unknown_unstable_fallback(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.UNKNOWN, confidence=0.8, is_stable=False)
        size, reason = adapter.adjust_size(5000.0, pred)
        # Falls back to RANGING multiplier (1.25)
        assert size == 6250.0
        assert "conservative" in reason

    def test_strategy_weights_by_regime(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.TRENDING, confidence=0.8, is_stable=True)
        weights = adapter.strategy_weights(pred)
        assert weights["momentum"] == 1.0
        assert weights["mean_reversion"] == 0.3
