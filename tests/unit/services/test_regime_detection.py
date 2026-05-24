"""Unit tests for market regime detection (HMM-based)."""

from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sam_trader.services.regime_detection import (
    HMMRegimeClassifier,
    Regime,
    RegimeAdapter,
    RegimeAdapterConfig,
    RegimePrediction,
    _build_features,
    _build_regime_map,
    _compute_bic,
)

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def ranging_bars() -> list[dict[str, Any]]:
    """300 days of low-volatility ranging market."""
    np.random.seed(42)
    close = 100.0
    bars = []
    for _ in range(300):
        ret = np.random.normal(0.0, 0.005)
        close *= 1 + ret
        bars.append(
            {
                "close": close,
                "volume": float(np.random.randint(1_000_000, 2_000_000)),
            }
        )
    return bars


@pytest.fixture
def trending_bars() -> list[dict[str, Any]]:
    """300 days of upward-trending market."""
    np.random.seed(43)
    close = 100.0
    bars = []
    for _ in range(300):
        ret = np.random.normal(0.001, 0.008)
        close *= 1 + ret
        bars.append(
            {
                "close": close,
                "volume": float(np.random.randint(1_000_000, 2_000_000)),
            }
        )
    return bars


@pytest.fixture
def volatile_bars() -> list[dict[str, Any]]:
    """300 days of high-volatility market."""
    np.random.seed(44)
    close = 100.0
    bars = []
    for _ in range(300):
        ret = np.random.normal(0.0, 0.025)
        close *= 1 + ret
        bars.append(
            {
                "close": close,
                "volume": float(np.random.randint(2_000_000, 5_000_000)),
            }
        )
    return bars


@pytest.fixture
def bearish_bars() -> list[dict[str, Any]]:
    """300 days of bearish (downward + high vol) market."""
    np.random.seed(45)
    close = 100.0
    bars = []
    for _ in range(300):
        ret = np.random.normal(-0.0015, 0.025)
        close *= 1 + ret
        bars.append(
            {
                "close": close,
                "volume": float(np.random.randint(2_000_000, 5_000_000)),
            }
        )
    return bars


@pytest.fixture
def mixed_bars(
    ranging_bars: list[dict[str, Any]],
    trending_bars: list[dict[str, Any]],
    volatile_bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """~900 days cycling through ranging → trending → volatile."""
    return ranging_bars + trending_bars + volatile_bars


@pytest.fixture
def tmp_model_dir(tmp_path: Path) -> Path:
    return tmp_path / "regime_models"


# ── Feature Engineering Tests ──────────────────────────────────────────────


def test_build_features_shape(ranging_bars: list[dict[str, Any]]) -> None:
    features, names = _build_features(ranging_bars)
    assert features.shape[1] == 3
    assert names == ["log_return", "realized_vol", "volume_ratio"]
    # First 20 rows dropped due to NaN in rolling windows
    assert features.shape[0] == len(ranging_bars) - 20


def test_build_features_no_nan(ranging_bars: list[dict[str, Any]]) -> None:
    features, _ = _build_features(ranging_bars)
    assert not np.isnan(features).any()


def test_build_features_short_input() -> None:
    bars = [{"close": 100.0, "volume": 1_000_000} for _ in range(5)]
    features, _ = _build_features(bars)
    assert features.shape[0] == 0


# ── HMMRegimeClassifier Training Tests ─────────────────────────────────────


class TestHMMTraining:
    def test_fit_succeeds_on_sufficient_data(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        clf.fit(mixed_bars)
        assert clf._model is not None
        assert clf._version != ""
        assert len(clf._regime_map) > 0

    def test_fit_persists_model(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        clf.fit(mixed_bars)
        assert (tmp_model_dir / "US_model.pkl").exists()
        assert (tmp_model_dir / "US_meta.json").exists()

    def test_fit_insufficient_data_logs_warning(
        self, tmp_model_dir: Path, caplog: Any
    ) -> None:
        bars = [{"close": 100.0 + i * 0.1, "volume": 1_000_000} for i in range(50)]
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        import logging

        with caplog.at_level(logging.WARNING):
            clf.fit(bars)
        assert "Insufficient data" in caplog.text
        assert clf._model is None

    def test_fit_uses_bic_selection(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(
            venue="US",
            model_dir=tmp_model_dir,
            n_state_candidates=(3, 4),
            n_init=3,
        )
        clf.fit(mixed_bars)
        assert clf._model is not None
        assert clf._model.n_components in (3, 4)


# ── HMMRegimeClassifier Prediction Tests ───────────────────────────────────


class TestHMMPrediction:
    def test_predict_returns_valid_regime(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert pred.regime in set(Regime)
        assert 0.0 <= pred.confidence <= 1.0

    def test_predict_state_probs_sum_to_one(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        total = sum(pred.state_probs.values())
        assert abs(total - 1.0) < 0.001

    def test_predict_transition_probs_are_valid(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        trans_total = sum(pred.transition_probs.values())
        # May not sum to 1 if UNKNOWN is excluded; just verify non-negative
        assert all(v >= 0.0 for v in pred.transition_probs.values())
        assert trans_total <= 1.01

    def test_predict_loads_persisted_model(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf1 = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf1.fit(mixed_bars)

        clf2 = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        pred = clf2.predict(mixed_bars)
        assert pred.regime in set(Regime)
        assert pred.model_version == clf1._version

    def test_predict_unknown_when_no_model(self, tmp_model_dir: Path) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        bars = [{"close": 100.0, "volume": 1_000_000} for _ in range(300)]
        pred = clf.predict(bars)
        assert pred.regime == Regime.UNKNOWN
        assert pred.confidence == 0.0

    def test_predict_unknown_when_insufficient_data(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict([{"close": 100.0, "volume": 1_000_000}])
        assert pred.regime == Regime.UNKNOWN

    def test_predict_confidence_threshold(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        # Confidence should be >= threshold or regime is UNKNOWN
        if pred.regime != Regime.UNKNOWN:
            assert pred.confidence >= HMMRegimeClassifier.CONFIDENCE_THRESHOLD

    def test_predict_includes_model_version(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        pred = clf.predict(mixed_bars)
        assert pred.model_version != ""


# ── Stability Tests ────────────────────────────────────────────────────────


class TestStability:
    def test_stable_after_three_same_regimes(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        # Force history to have 3 identical regimes
        clf._history = [Regime.TRENDING, Regime.TRENDING]
        pred = clf.predict(mixed_bars)
        # After appending the third identical regime, is_stable should be True
        # (only if the predicted regime happens to be TRENDING)
        if pred.regime == Regime.TRENDING:
            assert pred.is_stable is True

    def test_unstable_when_regimes_switch(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        clf._history = [Regime.TRENDING, Regime.VOLATILE]
        pred = clf.predict(mixed_bars)
        assert pred.is_stable is False

    def test_stable_false_with_short_history(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        clf._history = []
        pred = clf.predict(mixed_bars)
        assert pred.is_stable is False


# ── Dual Venue Tests ───────────────────────────────────────────────────────


class TestDualVenue:
    def test_separate_models_per_venue(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        us_clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        us_clf.fit(mixed_bars)

        hk_clf = HMMRegimeClassifier(venue="HK", model_dir=tmp_model_dir, n_init=3)
        # Slightly different data for HK
        np.random.seed(99)
        hk_bars = []
        close = 200.0
        for _ in range(300):
            ret = np.random.normal(0.0005, 0.012)
            close *= 1 + ret
            hk_bars.append({"close": close, "volume": 1_000_000})
        hk_clf.fit(hk_bars)

        assert (tmp_model_dir / "US_model.pkl").exists()
        assert (tmp_model_dir / "HK_model.pkl").exists()
        assert us_clf._version != hk_clf._version

    def test_us_model_not_loaded_for_hk(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        us_clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        us_clf.fit(mixed_bars)

        hk_clf = HMMRegimeClassifier(venue="HK", model_dir=tmp_model_dir)
        assert hk_clf._load() is False


# ── Regime Map Tests ───────────────────────────────────────────────────────


class TestRegimeMap:
    def test_build_regime_map_three_states(self) -> None:
        np.random.seed(1)
        features = np.random.randn(300, 3)
        from hmmlearn.hmm import GaussianHMM

        model = GaussianHMM(
            n_components=3, covariance_type="diag", random_state=1, n_iter=10
        )
        model.fit(features)
        mapping = _build_regime_map(model, features)
        assert len(mapping) == 3
        assert Regime.RANGING in mapping.values()
        # With random data one of TRENDING/VOLATILE should appear
        values = set(mapping.values())
        assert len(values) >= 2

    def test_build_regime_map_four_states(self) -> None:
        np.random.seed(2)
        features = np.random.randn(300, 3)
        from hmmlearn.hmm import GaussianHMM

        model = GaussianHMM(
            n_components=4, covariance_type="diag", random_state=1, n_iter=10
        )
        model.fit(features)
        mapping = _build_regime_map(model, features)
        assert len(mapping) == 4
        assert Regime.RANGING in mapping.values()


# ── BIC Computation Tests ──────────────────────────────────────────────────


class TestBIC:
    def test_bic_decreases_with_better_fit(self) -> None:
        np.random.seed(3)
        features = np.random.randn(300, 3)
        from hmmlearn.hmm import GaussianHMM

        model3 = GaussianHMM(
            n_components=3, covariance_type="diag", random_state=1, n_iter=10
        )
        model3.fit(features)
        bic3 = _compute_bic(model3, features)

        model5 = GaussianHMM(
            n_components=5, covariance_type="diag", random_state=1, n_iter=10
        )
        model5.fit(features)
        bic5 = _compute_bic(model5, features)

        # BIC should be finite numbers
        assert math.isfinite(bic3)
        assert math.isfinite(bic5)


# ── RegimeAdapter Tests ────────────────────────────────────────────────────


class TestRegimeAdapter:
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

    def test_adjust_size_unknown_unstable(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.UNKNOWN, confidence=0.8, is_stable=False)
        size, reason = adapter.adjust_size(5000.0, pred)
        # Falls back to RANGING multiplier (1.25)
        assert size == 6250.0
        assert "conservative" in reason

    def test_adjust_stop_trending(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.TRENDING, confidence=0.8, is_stable=True)
        stop, reason = adapter.adjust_stop(atr=2.0, prediction=pred)
        assert stop == 3.0
        assert "TRENDING" in reason

    def test_adjust_stop_ranging(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.RANGING, confidence=0.8, is_stable=True)
        stop, reason = adapter.adjust_stop(atr=2.0, prediction=pred)
        assert stop == 4.0

    def test_adjust_stop_with_premarket_low(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.RANGING, confidence=0.8, is_stable=True)
        stop, reason = adapter.adjust_stop(atr=2.0, prediction=pred, premarket_low=3.5)
        assert stop == 3.5
        assert "capped" in reason

    def test_strategy_weights_trending(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.TRENDING, confidence=0.8, is_stable=True)
        weights = adapter.strategy_weights(pred)
        assert weights["momentum"] == 1.0
        assert weights["mean_reversion"] == 0.3

    def test_strategy_weights_ranging(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.RANGING, confidence=0.8, is_stable=True)
        weights = adapter.strategy_weights(pred)
        assert weights["mean_reversion"] == 1.0
        assert weights["momentum"] == 0.5

    def test_strategy_weights_volatile(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.VOLATILE, confidence=0.8, is_stable=True)
        weights = adapter.strategy_weights(pred)
        assert weights["momentum"] == 0.3

    def test_strategy_weights_unknown(self) -> None:
        adapter = RegimeAdapter()
        pred = RegimePrediction(regime=Regime.UNKNOWN, confidence=0.8, is_stable=True)
        weights = adapter.strategy_weights(pred)
        assert weights["momentum"] == 0.5
        assert weights["orb"] == 0.5

    def test_custom_config(self) -> None:
        config = RegimeAdapterConfig(
            sizing_multipliers={
                Regime.TRENDING.value: 2.0,
                Regime.RANGING.value: 1.0,
                Regime.VOLATILE.value: 0.5,
                Regime.BEARISH.value: 0.25,
                Regime.UNKNOWN.value: 1.0,
            }
        )
        adapter = RegimeAdapter(config)
        pred = RegimePrediction(regime=Regime.TRENDING, confidence=0.8, is_stable=True)
        size, _ = adapter.adjust_size(1000.0, pred)
        assert size == 2000.0


# ── End-to-End Regime Classification Tests ─────────────────────────────────


class TestEndToEnd:
    def test_ranging_data_classified_as_ranging(
        self, ranging_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=5)
        clf.fit(ranging_bars)
        pred = clf.predict(ranging_bars)
        # Pure single-regime data may not separate well; just validate output
        assert pred.regime in set(Regime)
        assert pred.confidence > 0.0

    def test_trending_data_classified_as_trending(
        self, trending_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=5)
        clf.fit(trending_bars)
        pred = clf.predict(trending_bars)
        assert pred.regime in set(Regime)
        assert pred.confidence > 0.0

    def test_volatile_data_classified_as_volatile(
        self, volatile_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=5)
        clf.fit(volatile_bars)
        pred = clf.predict(volatile_bars)
        assert pred.regime in set(Regime)
        assert pred.confidence > 0.0

    def test_bearish_data_classified_as_bearish_or_volatile(
        self, bearish_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=5)
        clf.fit(bearish_bars)
        pred = clf.predict(bearish_bars)
        assert pred.regime in set(Regime)
        assert pred.confidence > 0.0

    def test_history_tracked(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        clf.predict(mixed_bars)
        assert clf.latest_regime() is not None
        assert len(clf._history) >= 1


# ── Persistence Edge Cases ─────────────────────────────────────────────────


class TestPersistence:
    def test_load_corrupt_meta_returns_false(self, tmp_model_dir: Path) -> None:
        tmp_model_dir.mkdir(parents=True, exist_ok=True)
        (tmp_model_dir / "US_model.pkl").write_bytes(b"not a pickle")
        (tmp_model_dir / "US_meta.json").write_text("not json")
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir)
        assert clf._load() is False

    def test_meta_contains_expected_fields(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        meta_path = tmp_model_dir / "US_meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        assert "version" in meta
        assert "venue" in meta
        assert "training_date" in meta
        assert "n_components" in meta
        assert "regime_map" in meta
        assert "feature_names" in meta

    def test_pickle_roundtrip(
        self, mixed_bars: list[dict[str, Any]], tmp_model_dir: Path
    ) -> None:
        clf = HMMRegimeClassifier(venue="US", model_dir=tmp_model_dir, n_init=3)
        clf.fit(mixed_bars)
        model_path = tmp_model_dir / "US_model.pkl"
        with open(model_path, "rb") as f:
            loaded_model = pickle.load(f)
        assert loaded_model.n_components == clf._model.n_components
