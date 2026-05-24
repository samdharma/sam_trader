"""AI Scoring Engine — LLM candidate evaluation with rule-based fallback.

Consumes enriched :class:`~sam_trader.services.gap_scanner.GapCandidate`
objects and produces per-symbol trading recommendations.

Usage
-----
    from sam_trader.services.ai_scoring import AIScoringEngine, AIScoringConfig
    from sam_trader.services.gap_scanner import GapCandidate

    engine = AIScoringEngine(AIScoringConfig())
    rec = engine.score(candidate, context={"atr": 2.5, "pmh": 152.0, "pml": 148.0})
    # rec.grade -> Grade.STRONG_BUY | BUY | HOLD | SKIP
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from sam_trader.services.gap_scanner import GapCandidate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Grade(str, Enum):
    """Recommendation grade derived from total score and confidence."""

    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SKIP = "SKIP"


class Conviction(str, Enum):
    """Granular conviction level."""

    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionScores:
    """Six-dimension score breakdown (0–100 total)."""

    gap_quality: int = 0
    technical_setup: int = 0
    sentiment: int = 0
    liquidity: int = 0
    risk: int = 0
    market_context: int = 0

    @property
    def total(self) -> int:
        return (
            self.gap_quality
            + self.technical_setup
            + self.sentiment
            + self.liquidity
            + self.risk
            + self.market_context
        )


@dataclass(frozen=True)
class TradeParameters:
    """Suggested trade parameters for a recommendation."""

    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    position_size_pct: float = 0.0

    @property
    def risk_reward_ratio(self) -> float:
        """Risk/reward ratio (0.0 if invalid)."""
        risk = self.entry - self.stop
        reward = self.target - self.entry
        if risk <= 0 or reward <= 0:
            return 0.0
        return round(reward / risk, 2)


@dataclass(frozen=True)
class AIRecommendation:
    """Complete AI scoring output for a single gap candidate."""

    instrument_id: str
    grade: Grade
    conviction: Conviction
    confidence: float
    scores: DimensionScores
    trade_params: TradeParameters
    reasoning: str
    key_factors: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    llm_used: str = "RuleBased"
    trace_id: str = ""
    timestamp: str = ""


@dataclass(frozen=True)
class AIScoringConfig:
    """Configuration for the AI scoring engine."""

    # Grade thresholds
    strong_buy_score_threshold: int = 80
    strong_buy_confidence_threshold: float = 0.7
    buy_score_threshold: int = 60
    buy_confidence_threshold: float = 0.5
    hold_score_threshold: int = 40
    hold_confidence_threshold: float = 0.3

    # Position sizing bounds (% of portfolio equity)
    strong_buy_size_min: float = 5.0
    strong_buy_size_max: float = 15.0
    buy_size_min: float = 2.0
    buy_size_max: float = 8.0
    hold_size_min: float = 0.0
    hold_size_max: float = 3.0

    # Trade parameter rules
    min_risk_reward: float = 1.5
    stop_atr_multiplier: float = 1.5
    entry_slack_pct: float = 1.0

    # Fallback behaviour
    fallback_enabled: bool = True
    llm_timeout_secs: int = 30


# ---------------------------------------------------------------------------
# LLM Client Protocol
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """Protocol for an LLM inference client."""

    def complete(self, prompt: str) -> str:
        """Send *prompt* to the LLM and return the raw response text."""
        ...


class DeepSeekClient:
    """Thin DeepSeek API client (falls back gracefully on network errors)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1/chat/completions",
    ) -> None:
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._model = model
        self._base_url = base_url

    def complete(self, prompt: str) -> str:
        if not self._api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
        # Minimal urllib implementation to avoid extra dependencies
        try:
            import urllib.error
            import urllib.request

            payload = json.dumps(
                {
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 512,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                self._base_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return str(data["choices"][0]["message"]["content"])
        except Exception as exc:
            raise RuntimeError(f"DeepSeek API call failed: {exc}") from exc


class KimiClient:
    """Thin Moonshot Kimi API client (falls back gracefully on network errors)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "moonshot-v1-8k",
        base_url: str = "https://api.moonshot.cn/v1/chat/completions",
    ) -> None:
        self._api_key = api_key or os.getenv("KIMI_API_KEY", "")
        self._model = model
        self._base_url = base_url

    def complete(self, prompt: str) -> str:
        if not self._api_key:
            raise RuntimeError("KIMI_API_KEY not configured")
        try:
            import urllib.error
            import urllib.request

            payload = json.dumps(
                {
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 512,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                self._base_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return str(data["choices"][0]["message"]["content"])
        except Exception as exc:
            raise RuntimeError(f"Kimi API call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


class AIScoringEngine:
    """Score gap candidates across six dimensions and produce recommendations.

    Parameters
    ----------
    config : AIScoringConfig
        Thresholds and sizing bounds.
    llm_client : LLMClient | None
        Primary LLM provider. If *None*, rule-based fallback is always used.
    fallback_llm_client : LLMClient | None
        Secondary LLM provider tried when primary fails.

    """

    def __init__(
        self,
        config: AIScoringConfig | None = None,
        llm_client: LLMClient | None = None,
        fallback_llm_client: LLMClient | None = None,
    ) -> None:
        self._config = config or AIScoringConfig()
        self._llm = llm_client
        self._fallback_llm = fallback_llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        candidate: GapCandidate,
        context: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> AIRecommendation:
        """Score a single gap candidate.

        Parameters
        ----------
        candidate : GapCandidate
            Enriched gap candidate from the scanner.
        context : dict[str, Any] | None
            Optional enrichment data: ``atr``, ``pmh``, ``pml``, ``rsi``,
            ``sector``, ``relative_volume``, ``dollar_volume``, etc.
        trace_id : str | None
            Pipeline trace identifier for auditability.

        Returns
        -------
        AIRecommendation

        """
        ctx = context or {}
        tid = trace_id or str(uuid.uuid4())[:8]
        ts = datetime.now(timezone.utc).isoformat()

        # 1. Attempt LLM path
        llm_label = "RuleBased"
        if self._llm is not None:
            try:
                rec = self._score_llm(candidate, ctx, self._llm, tid, ts, label="LLM")
                return rec
            except Exception as exc:
                logger.warning("Primary LLM failed: %s", exc)

        if self._fallback_llm is not None:
            try:
                rec = self._score_llm(
                    candidate, ctx, self._fallback_llm, tid, ts, label="FallbackLLM"
                )
                return rec
            except Exception as exc:
                logger.warning("Fallback LLM failed: %s", exc)

        # 2. Rule-based fallback
        return self._score_rule_based(candidate, ctx, tid, ts, label=llm_label)

    # ------------------------------------------------------------------
    # Rule-based scoring
    # ------------------------------------------------------------------

    def _score_rule_based(
        self,
        candidate: GapCandidate,
        ctx: dict[str, Any],
        trace_id: str,
        timestamp: str,
        label: str,
    ) -> AIRecommendation:
        scores = self._compute_dimension_scores(candidate, ctx)
        confidence = self._compute_confidence(candidate, ctx)
        grade = self._assign_grade(scores.total, confidence)
        conviction = self._assign_conviction(confidence)
        trade_params = self._compute_trade_params(candidate, grade, scores, ctx)
        reasoning, key_factors, risk_factors = self._build_reasoning(
            candidate, scores, grade, trade_params, ctx
        )

        return AIRecommendation(
            instrument_id=candidate.instrument_id,
            grade=grade,
            conviction=conviction,
            confidence=round(confidence, 2),
            scores=scores,
            trade_params=trade_params,
            reasoning=reasoning,
            key_factors=key_factors,
            risk_factors=risk_factors,
            llm_used=label,
            trace_id=trace_id,
            timestamp=timestamp,
        )

    def _compute_dimension_scores(
        self,
        candidate: GapCandidate,
        ctx: dict[str, Any],
    ) -> DimensionScores:
        """Deterministic six-dimension scoring."""
        gap_abs = abs(candidate.gap_pct)
        mid = (candidate.bid + candidate.ask) / 2
        spread = candidate.ask - candidate.bid
        spread_pct = (spread / mid * 100) if mid > 0 else 0.0

        # 1. Gap Quality (0–25)
        gap_quality = int(min(gap_abs * 2.5, 15))
        if candidate.trend == "RISING":
            gap_quality += 5
        elif candidate.trend == "STABLE":
            gap_quality += 2
        elif candidate.trend == "FADING":
            gap_quality = max(0, gap_quality - 2)
        elif candidate.trend == "LATE_BREAKER":
            gap_quality += 1
        if candidate.cross_validated:
            gap_quality += 3
        gap_quality = max(0, min(25, gap_quality))

        # 2. Technical Setup (0–20)
        tech_setup = 0
        if spread_pct < 0.1:
            tech_setup += 8
        elif spread_pct < 0.5:
            tech_setup += 5
        elif spread_pct < 1.0:
            tech_setup += 2
        else:
            tech_setup += 1

        # Gap direction consistency with price action
        if candidate.gap_pct > 0 and candidate.quote_last > candidate.prev_close:
            tech_setup += 5
        elif candidate.gap_pct < 0 and candidate.quote_last < candidate.prev_close:
            tech_setup += 5
        else:
            tech_setup += 2

        # Context bonuses
        atr = ctx.get("atr")
        pmh = ctx.get("pmh")
        pml = ctx.get("pml")
        if atr is not None and pmh is not None and pml is not None:
            # ATR context: reasonable ATR relative to price
            atr_pct = (atr / mid * 100) if mid > 0 else 0
            if 0.5 <= atr_pct <= 5.0:
                tech_setup += 4
            else:
                tech_setup += 2
        else:
            tech_setup += 3  # partial credit for missing context

        tech_setup = max(0, min(20, tech_setup))

        # 3. Sentiment (0–20) — neutral base when no news data
        sentiment = ctx.get("sentiment_score", 10)
        headline_count = ctx.get("headline_count", 0)
        if headline_count > 5:
            sentiment += 2
        elif headline_count > 0:
            sentiment += 1
        sentiment = max(0, min(20, sentiment))

        # 4. Liquidity (0–15)
        liquidity = 0
        if spread_pct < 0.1:
            liquidity += 10
        elif spread_pct < 0.3:
            liquidity += 7
        elif spread_pct < 0.5:
            liquidity += 4
        else:
            liquidity += 1

        if candidate.cross_validated:
            liquidity += 3

        rel_vol = ctx.get("relative_volume")
        if rel_vol is not None and rel_vol > 2.0:
            liquidity += 2
        elif rel_vol is not None and rel_vol > 1.0:
            liquidity += 1

        # Price accessibility
        if 5.0 <= candidate.quote_last <= 500.0:
            liquidity += 1

        liquidity = max(0, min(15, liquidity))

        # 5. Risk (0–10) — higher = better (lower risk)
        risk = max(0, int(10 - gap_abs / 2))
        if candidate.cross_validated:
            risk += 1
        if candidate.trend == "RISING" and candidate.gap_pct > 0:
            risk += 1
        elif candidate.trend == "FADING" and candidate.gap_pct > 0:
            risk = max(0, risk - 2)
        risk = max(0, min(10, risk))

        # 6. Market Context (0–10)
        market_ctx = 5  # neutral base
        if candidate.pass_number == 2:
            market_ctx += 2
        if candidate.cross_validated:
            market_ctx += 2
        index_alignment = ctx.get("index_alignment")
        if index_alignment is not None:
            if abs(index_alignment) < 0.3:
                market_ctx += 1
        market_ctx = max(0, min(10, market_ctx))

        return DimensionScores(
            gap_quality=gap_quality,
            technical_setup=tech_setup,
            sentiment=sentiment,
            liquidity=liquidity,
            risk=risk,
            market_context=market_ctx,
        )

    def _compute_confidence(
        self,
        candidate: GapCandidate,
        ctx: dict[str, Any],
    ) -> float:
        """Return a confidence value in [0.0, 1.0]."""
        base = 0.25
        if candidate.cross_validated:
            base += 0.15
        if candidate.pass_number == 2:
            base += 0.10
        if ctx.get("atr") is not None:
            base += 0.10
        if ctx.get("pmh") is not None and ctx.get("pml") is not None:
            base += 0.10
        if ctx.get("sentiment_score") is not None:
            base += 0.10
        if ctx.get("relative_volume") is not None:
            base += 0.10
        return min(1.0, base)

    def _assign_grade(self, total_score: int, confidence: float) -> Grade:
        cfg = self._config
        if (
            total_score >= cfg.strong_buy_score_threshold
            and confidence >= cfg.strong_buy_confidence_threshold
        ):
            return Grade.STRONG_BUY
        if (
            total_score >= cfg.buy_score_threshold
            and confidence >= cfg.buy_confidence_threshold
        ):
            return Grade.BUY
        if (
            total_score >= cfg.hold_score_threshold
            or confidence >= cfg.hold_confidence_threshold
        ):
            return Grade.HOLD
        return Grade.SKIP

    @staticmethod
    def _assign_conviction(confidence: float) -> Conviction:
        if confidence >= 0.7:
            return Conviction.STRONG
        if confidence >= 0.4:
            return Conviction.MODERATE
        return Conviction.WEAK

    def _compute_trade_params(
        self,
        candidate: GapCandidate,
        grade: Grade,
        scores: DimensionScores,
        ctx: dict[str, Any],
    ) -> TradeParameters:
        """Suggest entry, stop, target and position size."""
        mid = (candidate.bid + candidate.ask) / 2
        entry = round(mid, 4)

        # Stop: ATR-based or percentage fallback
        atr = ctx.get("atr")
        pml = ctx.get("pml")
        if atr is not None and atr > 0:
            atr_stop = entry - (self._config.stop_atr_multiplier * atr)
        else:
            atr_stop = entry * 0.97

        if pml is not None and pml > 0:
            stop = min(atr_stop, pml)
        else:
            stop = atr_stop

        stop = round(stop, 4)

        # Target: ensure minimum R/R
        risk = entry - stop
        if risk <= 0:
            risk = entry * 0.02  # 2% fallback
        target = entry + (self._config.min_risk_reward * risk)
        target = round(target, 4)

        # Position size by grade
        cfg = self._config
        if grade == Grade.STRONG_BUY:
            size = (cfg.strong_buy_size_min + cfg.strong_buy_size_max) / 2
        elif grade == Grade.BUY:
            size = (cfg.buy_size_min + cfg.buy_size_max) / 2
        elif grade == Grade.HOLD:
            size = (cfg.hold_size_min + cfg.hold_size_max) / 2
        else:
            size = 0.0

        return TradeParameters(
            entry=entry,
            stop=stop,
            target=target,
            position_size_pct=round(size, 2),
        )

    def _build_reasoning(
        self,
        candidate: GapCandidate,
        scores: DimensionScores,
        grade: Grade,
        trade_params: TradeParameters,
        ctx: dict[str, Any],
    ) -> tuple[str, list[str], list[str]]:
        """Return (reasoning, key_factors, risk_factors)."""
        direction = "up" if candidate.gap_pct > 0 else "down"
        key_factors: list[str] = [
            f"{direction} gap of {abs(candidate.gap_pct):.2f}%",
            f"trend={candidate.trend}",
            f"cross_validated={candidate.cross_validated}",
        ]
        risk_factors: list[str] = []

        if abs(candidate.gap_pct) > 10:
            risk_factors.append("Large gap magnitude increases volatility risk")
        if not candidate.cross_validated:
            risk_factors.append("Price not cross-validated across brokers")
        if candidate.trend == "FADING":
            risk_factors.append("Fading momentum in pre-market")

        reasoning = (
            f"Rule-based analysis for {candidate.instrument_id}: "
            f"{direction} gap {abs(candidate.gap_pct):.2f}% "
            f"with trend {candidate.trend}. "
            f"Total score {scores.total}/100 (gap_quality={scores.gap_quality}, "
            f"technical_setup={scores.technical_setup}, sentiment={scores.sentiment}, "
            f"liquidity={scores.liquidity}, risk={scores.risk}, "
            f"market_context={scores.market_context}). "
            f"Assigned grade {grade.value} with suggested "
            f"entry={trade_params.entry}, stop={trade_params.stop}, "
            f"target={trade_params.target} "
            f"(R/R={trade_params.risk_reward_ratio})."
        )
        return reasoning, key_factors, risk_factors

    # ------------------------------------------------------------------
    # LLM path (structured via prompt engineering)
    # ------------------------------------------------------------------

    def _score_llm(
        self,
        candidate: GapCandidate,
        ctx: dict[str, Any],
        client: LLMClient,
        trace_id: str,
        timestamp: str,
        label: str,
    ) -> AIRecommendation:
        """Score via LLM and parse structured JSON response."""
        prompt = self._build_llm_prompt(candidate, ctx)
        raw = client.complete(prompt)
        parsed = self._parse_llm_response(raw)

        scores = DimensionScores(
            gap_quality=int(parsed.get("gap_quality", 0)),
            technical_setup=int(parsed.get("technical_setup", 0)),
            sentiment=int(parsed.get("sentiment", 0)),
            liquidity=int(parsed.get("liquidity", 0)),
            risk=int(parsed.get("risk", 0)),
            market_context=int(parsed.get("market_context", 0)),
        )
        confidence = float(parsed.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        grade = self._assign_grade(scores.total, confidence)
        conviction = self._assign_conviction(confidence)
        trade_params = TradeParameters(
            entry=float(parsed.get("entry", candidate.quote_last)),
            stop=float(parsed.get("stop", candidate.quote_last * 0.97)),
            target=float(parsed.get("target", candidate.quote_last * 1.03)),
            position_size_pct=float(parsed.get("position_size_pct", 0.0)),
        )

        return AIRecommendation(
            instrument_id=candidate.instrument_id,
            grade=grade,
            conviction=conviction,
            confidence=round(confidence, 2),
            scores=scores,
            trade_params=trade_params,
            reasoning=str(parsed.get("reasoning", "")),
            key_factors=list(parsed.get("key_factors", [])),
            risk_factors=list(parsed.get("risk_factors", [])),
            llm_used=label,
            trace_id=trace_id,
            timestamp=timestamp,
        )

    @staticmethod
    def _build_llm_prompt(
        candidate: GapCandidate,
        ctx: dict[str, Any],
    ) -> str:
        """Construct a deterministic prompt for LLM scoring."""
        return (
            "You are a quantitative trading analyst. "
            "Score this pre-market gap candidate\n"
            "across six dimensions and output ONLY valid JSON.\n\n"
            "Symbol: {symbol}\n"
            "Prev Close: {prev_close}\n"
            "Current Quote: {quote_last}\n"
            "Gap %: {gap_pct}\n"
            "Bid: {bid}  Ask: {ask}\n"
            "Trend: {trend}\n"
            "Pass: {pass_number}\n"
            "Cross-validated: {cross_validated}\n"
            "Context: {context}\n\n"
            "Return JSON with these exact keys:\n"
            "  gap_quality (0-25), technical_setup (0-20), "
            "sentiment (0-20),\n"
            "  liquidity (0-15), risk (0-10), "
            "market_context (0-10),\n"
            "  confidence (0.0-1.0), entry (float), stop (float), target (float),\n"
            "  position_size_pct (0-15), reasoning (string),\n"
            "  key_factors (list of strings), risk_factors (list of strings).\n"
        ).format(
            symbol=candidate.instrument_id,
            prev_close=candidate.prev_close,
            quote_last=candidate.quote_last,
            gap_pct=candidate.gap_pct,
            bid=candidate.bid,
            ask=candidate.ask,
            trend=candidate.trend,
            pass_number=candidate.pass_number,
            cross_validated=candidate.cross_validated,
            context=json.dumps(ctx),
        )

    @staticmethod
    def _parse_llm_response(raw: str) -> dict[str, Any]:
        """Extract JSON from an LLM response string."""
        # Try direct parse first
        text = raw.strip()
        try:
            return json.loads(text)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks
        import re

        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass

        # Last resort: find first { ... } block
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass

        raise RuntimeError(f"Could not parse LLM response as JSON: {text[:200]}")
