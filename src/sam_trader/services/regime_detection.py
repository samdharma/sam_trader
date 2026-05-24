"""Market regime detection using Hidden Markov Models.

Usage (inside sam-services container):
    from sam_trader.services.regime_detection import (
        HMMRegimeClassifier,
        RegimeAdapter,
        RegimePrediction,
    )

    clf = HMMRegimeClassifier(venue="US")
    clf.fit(historical_bars)
    pred = clf.predict(latest_bars)
    # pred.regime -> Regime.TRENDING / RANGING / VOLATILE / BEARISH / UNKNOWN

    adapter = RegimeAdapter()
    size, reason = adapter.adjust_size(base_size=5000.0, prediction=pred)
"""

from __future__ import annotations

import json
import logging
import math
import pickle
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

# Optional hmmlearn — graceful degradation if missing
_hmmlearn: Any = None
try:
    from hmmlearn import hmm as _hmm_module  # type: ignore[import-untyped]

    _hmmlearn = _hmm_module
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger("sam_trader.regime_detection")

warnings.filterwarnings(
    "ignore",
    message=".*numerical issues on current data.*",
    category=RuntimeWarning,
)


# ── Enums & Dataclasses ────────────────────────────────────────────────────


class Regime(Enum):
    """Market regime labels."""

    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RegimePrediction:
    """Output of a single regime classification."""

    regime: Regime
    confidence: float
    state_probs: dict[str, float] = field(default_factory=dict)
    transition_probs: dict[str, float] = field(default_factory=dict)
    is_stable: bool = False
    model_version: str = ""


@dataclass(frozen=True)
class RegimeAdapterConfig:
    """Configuration for regime-aware parameter adaptation."""

    sizing_multipliers: dict[str, float] = field(
        default_factory=lambda: {
            Regime.TRENDING.value: 1.0,
            Regime.RANGING.value: 1.25,
            Regime.VOLATILE.value: 0.60,
            Regime.BEARISH.value: 0.50,
            Regime.UNKNOWN.value: 1.0,
        }
    )
    stop_atr_multipliers: dict[str, float] = field(
        default_factory=lambda: {
            Regime.TRENDING.value: 1.5,
            Regime.RANGING.value: 2.0,
            Regime.VOLATILE.value: 1.0,
            Regime.BEARISH.value: 1.0,
            Regime.UNKNOWN.value: 1.5,
        }
    )
    strategy_weights: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            Regime.TRENDING.value: {
                "momentum": 1.0,
                "orb": 0.8,
                "mean_reversion": 0.3,
            },
            Regime.RANGING.value: {
                "momentum": 0.5,
                "orb": 0.5,
                "mean_reversion": 1.0,
            },
            Regime.VOLATILE.value: {
                "momentum": 0.3,
                "orb": 0.5,
                "mean_reversion": 0.8,
            },
            Regime.BEARISH.value: {
                "momentum": 0.2,
                "orb": 0.3,
                "mean_reversion": 0.5,
            },
            Regime.UNKNOWN.value: {
                "momentum": 0.5,
                "orb": 0.5,
                "mean_reversion": 0.5,
            },
        }
    )


# ── Feature Engineering ────────────────────────────────────────────────────


def _build_features(
    bars: list[dict[str, Any]],
    vol_window: int = 20,
) -> tuple[np.ndarray, list[str]]:
    """Convert raw bar dicts into HMM feature matrix.

    Features
    --------
    1. log_return      – log(close_t / close_{t-1})
    2. realized_vol    – rolling std(log_return, *vol_window*)
    3. volume_ratio    – volume_t / rolling_mean(volume, *vol_window*)

    Returns
    -------
    (features, feature_names) where *features* has shape (n, 3).
    The first *vol_window* rows (containing NaNs) are dropped.
    """
    if len(bars) < vol_window + 1:
        return np.empty((0, 3)), ["log_return", "realized_vol", "volume_ratio"]

    closes = np.array([float(b["close"]) for b in bars])
    volumes = np.array([float(b.get("volume", 0.0)) for b in bars])

    log_ret = np.diff(np.log(np.maximum(closes, 1e-9)))
    # Pad log_ret to match original length (first value is NaN)
    log_ret = np.concatenate(([np.nan], log_ret))

    def _rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
        result = np.full_like(arr, np.nan)
        for i in range(window - 1, len(arr)):
            result[i] = np.std(arr[i - window + 1 : i + 1], ddof=1)
        return result

    def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
        result = np.full_like(arr, np.nan)
        for i in range(window - 1, len(arr)):
            result[i] = np.mean(arr[i - window + 1 : i + 1])
        return result

    realized_vol = _rolling_std(log_ret, vol_window)
    vol_ma = _rolling_mean(volumes, vol_window)
    volume_ratio = np.where(vol_ma > 0, volumes / vol_ma, np.nan)

    features = np.column_stack([log_ret, realized_vol, volume_ratio])
    # Drop rows with any NaN
    mask = ~np.isnan(features).any(axis=1)
    features = features[mask]
    return features, ["log_return", "realized_vol", "volume_ratio"]


# ── HMM Model Training & Inference ─────────────────────────────────────────


def _compute_bic(model: Any, features: np.ndarray) -> float:
    """Compute Bayesian Information Criterion for a fitted HMM."""
    n_samples = features.shape[0]
    log_likelihood = float(model.score(features))
    n_params = model.n_features * model.n_components  # means
    if model.covariance_type == "diag":
        n_params += model.n_features * model.n_components  # variances
    elif model.covariance_type == "full":
        n_params += model.n_features * (model.n_features + 1) // 2 * model.n_components
    n_params += model.n_components * model.n_components  # transmat
    n_params += model.n_components - 1  # startprob
    return float(-2 * log_likelihood + n_params * math.log(n_samples))


def _build_regime_map(model: Any, features: np.ndarray) -> dict[int, Regime]:
    """Map latent HMM states to semantic regime labels.

    Heuristic
    ---------
    1. Compute mean absolute return (volatility proxy) per state.
    2. Compute mean raw return per state.
    3. Sort states by volatility (ascending).
    4. Assign:
       - lowest vol  → RANGING
       - mid-low vol + positive return → TRENDING
       - mid-high vol → VOLATILE
       - highest vol + negative return → BEARISH
       - highest vol + positive return → VOLATILE
    """
    n_states = model.n_components
    means = model.means_  # shape (n_states, n_features)

    # Feature 0 is log_return, feature 1 is realized_vol
    mean_ret = means[:, 0]
    mean_vol = np.abs(means[:, 0]) + means[:, 1]  # abs(return) + realized_vol proxy

    # Sort by volatility ascending
    sorted_indices = np.argsort(mean_vol)

    regime_map: dict[int, Regime] = {}
    assigned: set[Regime] = set()

    # Lowest volatility → RANGING
    regime_map[int(sorted_indices[0])] = Regime.RANGING
    assigned.add(Regime.RANGING)

    # Highest volatility states
    highest_idx = int(sorted_indices[-1])
    second_highest_idx = int(sorted_indices[-2]) if n_states >= 4 else None

    if n_states >= 4 and mean_ret[highest_idx] < 0:
        regime_map[highest_idx] = Regime.BEARISH
        assigned.add(Regime.BEARISH)
        if second_highest_idx is not None:
            regime_map[second_highest_idx] = Regime.VOLATILE
            assigned.add(Regime.VOLATILE)
    else:
        regime_map[highest_idx] = Regime.VOLATILE
        assigned.add(Regime.VOLATILE)
        if second_highest_idx is not None and mean_ret[second_highest_idx] < 0:
            regime_map[second_highest_idx] = Regime.BEARISH
            assigned.add(Regime.BEARISH)

    # Remaining states: assign TRENDING to positive-return states,
    # VOLATILE to negative-return high-vol states, MID_VOL fallback
    for idx in sorted_indices:
        idx_int = int(idx)
        if idx_int in regime_map:
            continue
        if mean_ret[idx_int] > 0 and Regime.TRENDING not in assigned:
            regime_map[idx_int] = Regime.TRENDING
            assigned.add(Regime.TRENDING)
        elif mean_ret[idx_int] < -0.001 and Regime.BEARISH not in assigned:
            regime_map[idx_int] = Regime.BEARISH
            assigned.add(Regime.BEARISH)
        else:
            # Fallback: if TRENDING not assigned yet, use it; else VOLATILE
            if Regime.TRENDING not in assigned:
                regime_map[idx_int] = Regime.TRENDING
                assigned.add(Regime.TRENDING)
            else:
                regime_map[idx_int] = Regime.VOLATILE
                assigned.add(Regime.VOLATILE)

    return regime_map


class HMMRegimeClassifier:
    """HMM-based market regime classifier.

    Parameters
    ----------
    venue : str
        Market venue (e.g. "US", "HK"). Models are persisted per-venue.
    model_dir : str | Path | None
        Directory for model persistence. Defaults to ``~/.sam_trader/regime_models``.
    n_state_candidates : tuple[int, ...]
        Candidate numbers of hidden states to evaluate via BIC.
    n_init : int
        Random initializations per candidate state count.
    random_state : int
        Seed for reproducibility.
    """

    MIN_BARS: int = 252
    STABILITY_THRESHOLD: int = 3
    CONFIDENCE_THRESHOLD: float = 0.6

    def __init__(
        self,
        venue: str = "US",
        model_dir: str | Path | None = None,
        n_state_candidates: tuple[int, ...] = (3, 4, 5, 6, 7),
        n_init: int = 10,
        random_state: int = 42,
    ) -> None:
        self.venue = venue.upper()
        self.model_dir = (
            Path(model_dir)
            if model_dir
            else Path.home() / ".sam_trader" / "regime_models"
        )
        self.n_state_candidates = n_state_candidates
        self.n_init = n_init
        self.random_state = random_state
        self._model: Any = None
        self._regime_map: dict[int, Regime] = {}
        self._version: str = ""
        self._feature_names: list[str] = []
        self._history: list[Regime] = []

    # ── Public API ─────────────────────────────────────────────────────────

    def fit(self, bars: list[dict[str, Any]]) -> None:
        """Train the best HMM on *bars* and persist it."""
        if _hmmlearn is None:
            logger.error("hmmlearn is not installed; cannot train regime model")
            return

        features, feature_names = _build_features(bars)
        if features.shape[0] < self.MIN_BARS:
            logger.warning(
                "Insufficient data for regime training: %d bars (need %d)",
                features.shape[0],
                self.MIN_BARS,
            )
            return

        best_model: Any = None
        best_bic = float("inf")
        best_regime_map: dict[int, Regime] = {}

        for n_states in self.n_state_candidates:
            candidate_model = self._train_candidate(features, n_states)
            if candidate_model is None:
                continue
            bic = _compute_bic(candidate_model, features)
            logger.debug("Candidate n_states=%d BIC=%.2f", n_states, bic)
            if bic < best_bic:
                best_bic = bic
                best_model = candidate_model
                best_regime_map = _build_regime_map(candidate_model, features)

        if best_model is None:
            logger.error("All HMM training candidates failed")
            return

        self._model = best_model
        self._regime_map = best_regime_map
        self._feature_names = feature_names
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        bic_suffix = abs(int(best_bic)) % 10000
        self._version = f"{ts}-{best_model.n_components}-{bic_suffix:04d}"
        self._save()
        logger.info(
            "Trained regime model venue=%s states=%d version=%s",
            self.venue,
            best_model.n_components,
            self._version,
        )

    def predict(self, bars: list[dict[str, Any]]) -> RegimePrediction:
        """Classify the most recent regime from *bars*.

        Uses the forward algorithm (via ``predict_proba`` on the full
        history up to the latest bar) so no future information leaks.
        """
        if self._model is None:
            if not self._load():
                return self._unknown_prediction("No model available")

        features, _ = _build_features(bars)
        if features.shape[0] == 0:
            return self._unknown_prediction("Insufficient data for prediction")

        # Forward algorithm: state probabilities for the last observation
        state_probs = self._model.predict_proba(features)[-1]

        # Map state probabilities → regime probabilities
        regime_probs: dict[Regime, float] = {
            r: 0.0 for r in Regime if r != Regime.UNKNOWN
        }
        for state_idx, prob in enumerate(state_probs):
            regime = self._regime_map.get(state_idx, Regime.UNKNOWN)
            regime_probs[regime] = regime_probs.get(regime, 0.0) + prob

        # Normalize
        total = sum(regime_probs.values())
        if total > 0:
            regime_probs = {k: v / total for k, v in regime_probs.items()}

        best_regime = max(regime_probs, key=regime_probs.get)  # type: ignore[arg-type]
        confidence = float(regime_probs[best_regime])

        if confidence < self.CONFIDENCE_THRESHOLD:
            best_regime = Regime.UNKNOWN
            confidence = float(regime_probs.get(Regime.UNKNOWN, 0.0))

        # Stability check
        self._history.append(best_regime)
        is_stable = self._check_stability()

        # Transition probabilities for the most likely state
        trans = self._transition_for_regime(best_regime)

        return RegimePrediction(
            regime=best_regime,
            confidence=round(confidence, 6),
            state_probs={r.value: round(p, 6) for r, p in regime_probs.items()},
            transition_probs={r.value: round(p, 6) for r, p in trans.items()},
            is_stable=is_stable,
            model_version=self._version,
        )

    def latest_regime(self) -> Regime | None:
        """Return the most recent predicted regime, or *None*."""
        return self._history[-1] if self._history else None

    # ── Persistence ────────────────────────────────────────────────────────

    def _save(self) -> None:
        """Persist model + metadata to disk."""
        self.model_dir.mkdir(parents=True, exist_ok=True)
        model_path = self.model_dir / f"{self.venue}_model.pkl"
        meta_path = self.model_dir / f"{self.venue}_meta.json"

        with open(model_path, "wb") as f:
            pickle.dump(self._model, f)

        meta = {
            "version": self._version,
            "venue": self.venue,
            "training_date": datetime.now(timezone.utc).isoformat(),
            "n_components": self._model.n_components,
            "covariance_type": self._model.covariance_type,
            "regime_map": {str(k): v.value for k, v in self._regime_map.items()},
            "feature_names": self._feature_names,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    def _load(self) -> bool:
        """Load model + metadata from disk. Returns *True* on success."""
        model_path = self.model_dir / f"{self.venue}_model.pkl"
        meta_path = self.model_dir / f"{self.venue}_meta.json"

        if not model_path.exists() or not meta_path.exists():
            return False

        try:
            with open(model_path, "rb") as f:
                self._model = pickle.load(f)
            with open(meta_path) as f:
                meta = json.load(f)
            self._version = meta.get("version", "")
            self._feature_names = meta.get("feature_names", [])
            self._regime_map = {
                int(k): Regime(v) for k, v in meta.get("regime_map", {}).items()
            }
            return True
        except Exception as exc:
            logger.warning("Failed to load regime model: %s", exc)
            return False

    # ── Internal helpers ───────────────────────────────────────────────────

    def _train_candidate(self, features: np.ndarray, n_states: int) -> Any:
        """Train a single GaussianHMM candidate. Returns *None* on failure."""
        best_ll = -float("inf")
        best_model: Any = None
        rng = np.random.RandomState(self.random_state)

        for attempt in range(self.n_init):
            model = _hmmlearn.GaussianHMM(
                n_components=n_states,
                covariance_type="diag",
                random_state=rng.randint(0, 2**31 - 1),
                n_iter=100,
                tol=1e-3,
            )
            try:
                model.fit(features)
                ll = model.score(features)
                if ll > best_ll and model.monitor_.converged:
                    best_ll = ll
                    best_model = model
            except Exception as exc:
                logger.debug("HMM training attempt %d failed: %s", attempt, exc)
                continue

        return best_model

    def _check_stability(self) -> bool:
        """True if the same regime has persisted for *STABILITY_THRESHOLD* days."""
        if len(self._history) < self.STABILITY_THRESHOLD:
            return False
        recent = self._history[-self.STABILITY_THRESHOLD :]
        return all(r == recent[0] for r in recent)

    def _transition_for_regime(self, regime: Regime) -> dict[Regime, float]:
        """Aggregate transition matrix row for a *regime* (sum over states)."""
        if self._model is None or regime == Regime.UNKNOWN:
            return {r: 0.0 for r in Regime if r != Regime.UNKNOWN}

        # Find which latent states map to this regime
        state_indices = [s for s, r in self._regime_map.items() if r == regime]
        if not state_indices:
            return {r: 0.0 for r in Regime if r != Regime.UNKNOWN}

        # Average transition rows weighted by stationary distribution
        transmat = self._model.transmat_
        startprob = self._model.startprob_
        avg_row = np.zeros(transmat.shape[1])
        weight_sum = 0.0
        for s in state_indices:
            w = startprob[s]
            avg_row += transmat[s] * w
            weight_sum += w
        if weight_sum > 0:
            avg_row /= weight_sum

        # Map back to regimes
        regime_probs: dict[Regime, float] = {
            r: 0.0 for r in Regime if r != Regime.UNKNOWN
        }
        for state_idx, prob in enumerate(avg_row):
            r = self._regime_map.get(state_idx, Regime.UNKNOWN)
            if r != Regime.UNKNOWN:
                regime_probs[r] = regime_probs.get(r, 0.0) + float(prob)
        return regime_probs

    @staticmethod
    def _unknown_prediction(reason: str) -> RegimePrediction:
        logger.warning("Regime classification returned UNKNOWN: %s", reason)
        return RegimePrediction(
            regime=Regime.UNKNOWN,
            confidence=0.0,
            state_probs={r.value: 0.0 for r in Regime if r != Regime.UNKNOWN},
            transition_probs={r.value: 0.0 for r in Regime if r != Regime.UNKNOWN},
            is_stable=False,
            model_version="",
        )


# ── Regime-Aware Parameter Adaptation ──────────────────────────────────────


class RegimeAdapter:
    """Adapt trading parameters based on detected regime.

    Parameters
    ----------
    config : RegimeAdapterConfig | None
        Override defaults for sizing multipliers, stop distances, etc.
    """

    def __init__(self, config: RegimeAdapterConfig | None = None) -> None:
        self.config = config or RegimeAdapterConfig()

    def adjust_size(
        self, base_size: float, prediction: RegimePrediction
    ) -> tuple[float, str]:
        """Return adjusted position size and human-readable reasoning.

        Parameters
        ----------
        base_size : float
            Nominal position size in base currency.
        prediction : RegimePrediction
            Current regime prediction.

        Returns
        -------
        (adjusted_size, reasoning)
        """
        regime_val = prediction.regime.value
        mult = self.config.sizing_multipliers.get(regime_val, 1.0)

        if prediction.regime == Regime.UNKNOWN or not prediction.is_stable:
            # Conservative fallback when regime is unknown or flickering
            mult = self.config.sizing_multipliers.get(Regime.RANGING.value, 1.0)
            adjusted = base_size * mult
            return round(adjusted, 2), (
                f"Regime {prediction.regime.value} (unstable) — "
                f"conservative sizing at {mult:.0%} = ${adjusted:,.2f}"
            )

        adjusted = base_size * mult
        pct = (mult - 1.0) * 100
        direction = "increased" if pct > 0 else "reduced" if pct < 0 else "unchanged"
        reason = (
            f"{prediction.regime.value.upper()} regime: size {direction} "
            f"by {abs(pct):.0f}% = ${adjusted:,.2f}"
        )
        return round(adjusted, 2), reason

    def adjust_stop(
        self,
        atr: float,
        prediction: RegimePrediction,
        premarket_low: float | None = None,
    ) -> tuple[float, str]:
        """Return adjusted stop distance and reasoning.

        Parameters
        ----------
        atr : float
            Average True Range in price units.
        prediction : RegimePrediction
            Current regime prediction.
        premarket_low : float | None
            Pre-market low price. If provided, the stop is capped at this
            level (tighter of ATR-based and PML-based).

        Returns
        -------
        (stop_distance, reasoning)
        """
        regime_val = prediction.regime.value
        mult = self.config.stop_atr_multipliers.get(regime_val, 1.5)
        stop = atr * mult

        if premarket_low is not None and stop > premarket_low:
            stop = premarket_low
            reason = (
                f"{prediction.regime.value.upper()} regime: ATR×{mult}=${stop:.2f} "
                f"capped at pre-market low ${premarket_low:.2f}"
            )
        else:
            reason = (
                f"{prediction.regime.value.upper()} regime: "
                f"stop = ATR×{mult} = ${stop:.2f}"
            )

        return round(stop, 4), reason

    def strategy_weights(self, prediction: RegimePrediction) -> dict[str, float]:
        """Return recommended strategy weights for the current regime.

        Returns a copy so callers may modify without side effects.
        """
        regime_val = prediction.regime.value
        weights = self.config.strategy_weights.get(
            regime_val,
            self.config.strategy_weights.get(Regime.UNKNOWN.value, {}),
        )
        return dict(weights)
