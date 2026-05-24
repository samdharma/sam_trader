"""Unit tests for AIScoringEngine.

Validates dimension scoring, grade assignment, rule-based fallback,
LLM path, trade parameter computation, and confidence calculation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from sam_trader.services.ai_scoring import (
    AIScoringConfig,
    AIScoringEngine,
    Conviction,
    DeepSeekClient,
    DimensionScores,
    Grade,
    KimiClient,
    TradeParameters,
)
from sam_trader.services.gap_scanner import GapCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    instrument_id: str = "AAPL.NASDAQ",
    prev_close: float = 150.0,
    quote_last: float = 155.0,
    gap_pct: float = 3.33,
    bid: float = 154.9,
    ask: float = 155.1,
    trend: str = "RISING",
    pass_number: int = 1,
    cross_validated: bool = True,
) -> GapCandidate:
    return GapCandidate(
        instrument_id=instrument_id,
        prev_close=prev_close,
        quote_last=quote_last,
        gap_pct=gap_pct,
        bid=bid,
        ask=ask,
        volume=None,
        trend=trend,
        pass_number=pass_number,
        cross_validated=cross_validated,
        cross_validation_note="",
    )


@dataclass(frozen=True)
class _FakeLLM:
    """Fake LLM client that returns a fixed JSON string."""

    response: dict[str, object]

    def complete(self, prompt: str) -> str:
        return json.dumps(self.response)


class _FailingLLM:
    """Fake LLM client that always raises."""

    def complete(self, prompt: str) -> str:
        raise RuntimeError("LLM unavailable")


# ---------------------------------------------------------------------------
# Dimension Scoring
# ---------------------------------------------------------------------------


class TestDimensionScoring:
    def test_gap_quality_maximized(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(gap_pct=8.0, trend="RISING", cross_validated=True)
        scores = engine._compute_dimension_scores(cand, {})
        assert 0 <= scores.gap_quality <= 25
        assert scores.gap_quality >= 15  # strong gap + trend + xval

    def test_gap_quality_fading_penalty(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(gap_pct=5.0, trend="FADING", cross_validated=False)
        scores = engine._compute_dimension_scores(cand, {})
        assert scores.gap_quality < 15

    def test_technical_setup_spread_bonus(self) -> None:
        engine = AIScoringEngine()
        cand_tight = _make_candidate(bid=154.99, ask=155.01)  # ~0.013% spread
        cand_wide = _make_candidate(bid=154.0, ask=156.0)  # ~1.29% spread
        scores_tight = engine._compute_dimension_scores(cand_tight, {})
        scores_wide = engine._compute_dimension_scores(cand_wide, {})
        assert scores_tight.technical_setup > scores_wide.technical_setup

    def test_technical_setup_atr_context(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        scores_with = engine._compute_dimension_scores(
            cand, {"atr": 2.5, "pmh": 158.0, "pml": 152.0}
        )
        scores_without = engine._compute_dimension_scores(cand, {})
        assert scores_with.technical_setup >= scores_without.technical_setup

    def test_sentiment_default_neutral(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        scores = engine._compute_dimension_scores(cand, {})
        assert scores.sentiment == 10  # neutral base

    def test_sentiment_with_headlines(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        scores = engine._compute_dimension_scores(cand, {"headline_count": 10})
        assert scores.sentiment == 12

    def test_liquidity_tight_spread(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(bid=154.99, ask=155.01, cross_validated=True)
        scores = engine._compute_dimension_scores(cand, {})
        assert scores.liquidity >= 13  # tight spread + xval

    def test_liquidity_relative_volume(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        scores = engine._compute_dimension_scores(cand, {"relative_volume": 3.0})
        assert scores.liquidity >= 11  # base + rel_vol bonus

    def test_risk_low_gap_high_score(self) -> None:
        engine = AIScoringEngine()
        cand_small = _make_candidate(gap_pct=2.0)
        cand_large = _make_candidate(gap_pct=15.0)
        scores_small = engine._compute_dimension_scores(cand_small, {})
        scores_large = engine._compute_dimension_scores(cand_large, {})
        assert scores_small.risk > scores_large.risk

    def test_market_context_pass_two_bonus(self) -> None:
        engine = AIScoringEngine()
        cand_p1 = _make_candidate(pass_number=1)
        cand_p2 = _make_candidate(pass_number=2)
        scores_p1 = engine._compute_dimension_scores(cand_p1, {})
        scores_p2 = engine._compute_dimension_scores(cand_p2, {})
        assert scores_p2.market_context > scores_p1.market_context

    def test_total_score_within_range(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        scores = engine._compute_dimension_scores(cand, {})
        assert 0 <= scores.total <= 100


# ---------------------------------------------------------------------------
# Grade Assignment
# ---------------------------------------------------------------------------


class TestGradeAssignment:
    def test_strong_buy_threshold(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(80, 0.7) == Grade.STRONG_BUY
        assert engine._assign_grade(85, 0.75) == Grade.STRONG_BUY

    def test_strong_buy_score_too_low(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(79, 0.7) != Grade.STRONG_BUY

    def test_strong_buy_confidence_too_low(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(80, 0.69) != Grade.STRONG_BUY

    def test_buy_threshold(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(60, 0.5) == Grade.BUY
        assert engine._assign_grade(70, 0.6) == Grade.BUY

    def test_buy_score_too_low(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(59, 0.5) != Grade.BUY

    def test_hold_threshold(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(40, 0.2) == Grade.HOLD
        assert engine._assign_grade(30, 0.3) == Grade.HOLD

    def test_skip_default(self) -> None:
        engine = AIScoringEngine()
        assert engine._assign_grade(30, 0.2) == Grade.SKIP
        assert engine._assign_grade(0, 0.0) == Grade.SKIP

    def test_custom_thresholds(self) -> None:
        cfg = AIScoringConfig(
            strong_buy_score_threshold=90,
            strong_buy_confidence_threshold=0.8,
            buy_score_threshold=70,
            buy_confidence_threshold=0.6,
        )
        engine = AIScoringEngine(config=cfg)
        assert engine._assign_grade(90, 0.8) == Grade.STRONG_BUY
        assert engine._assign_grade(89, 0.8) == Grade.BUY
        assert engine._assign_grade(70, 0.6) == Grade.BUY
        assert engine._assign_grade(69, 0.6) == Grade.HOLD


# ---------------------------------------------------------------------------
# Conviction Assignment
# ---------------------------------------------------------------------------


class TestConvictionAssignment:
    def test_strong(self) -> None:
        assert AIScoringEngine._assign_conviction(0.7) == Conviction.STRONG
        assert AIScoringEngine._assign_conviction(1.0) == Conviction.STRONG

    def test_moderate(self) -> None:
        assert AIScoringEngine._assign_conviction(0.4) == Conviction.MODERATE
        assert AIScoringEngine._assign_conviction(0.69) == Conviction.MODERATE

    def test_weak(self) -> None:
        assert AIScoringEngine._assign_conviction(0.39) == Conviction.WEAK
        assert AIScoringEngine._assign_conviction(0.0) == Conviction.WEAK


# ---------------------------------------------------------------------------
# Confidence Calculation
# ---------------------------------------------------------------------------


class TestConfidenceCalculation:
    def test_base_confidence(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(cross_validated=False, pass_number=1)
        confidence = engine._compute_confidence(cand, {})
        assert 0.0 <= confidence <= 1.0
        assert confidence == pytest.approx(0.25, abs=0.01)  # base only

    def test_cross_validated_bonus(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(cross_validated=True)
        confidence = engine._compute_confidence(cand, {})
        assert confidence == pytest.approx(0.40, abs=0.01)  # base + 0.15

    def test_full_context_bonus(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(cross_validated=True, pass_number=2)
        ctx = {
            "atr": 2.5,
            "pmh": 158.0,
            "pml": 152.0,
            "sentiment_score": 15,
            "relative_volume": 2.5,
        }
        confidence = engine._compute_confidence(cand, ctx)
        assert confidence == pytest.approx(0.90, abs=0.01)  # capped


# ---------------------------------------------------------------------------
# Trade Parameters
# ---------------------------------------------------------------------------


class TestTradeParameters:
    def test_entry_near_quote(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(quote_last=155.0, bid=154.9, ask=155.1)
        scores = DimensionScores(
            gap_quality=20,
            technical_setup=15,
            sentiment=10,
            liquidity=10,
            risk=5,
            market_context=5,
        )
        params = engine._compute_trade_params(cand, Grade.STRONG_BUY, scores, {})
        assert params.entry == pytest.approx(155.0, abs=0.2)

    def test_stop_below_entry(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(quote_last=155.0, bid=154.9, ask=155.1)
        scores = DimensionScores()
        params = engine._compute_trade_params(cand, Grade.BUY, scores, {"atr": 2.5})
        assert params.stop < params.entry

    def test_target_above_entry(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(quote_last=155.0, bid=154.9, ask=155.1)
        scores = DimensionScores()
        params = engine._compute_trade_params(cand, Grade.BUY, scores, {"atr": 2.5})
        assert params.target > params.entry

    def test_risk_reward_at_least_1_5(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(quote_last=155.0, bid=154.9, ask=155.1)
        scores = DimensionScores()
        params = engine._compute_trade_params(
            cand, Grade.STRONG_BUY, scores, {"atr": 2.5}
        )
        assert params.risk_reward_ratio >= 1.5

    def test_stop_uses_pml_when_available(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(quote_last=155.0, bid=154.9, ask=155.1)
        scores = DimensionScores()
        params = engine._compute_trade_params(
            cand, Grade.BUY, scores, {"atr": 2.5, "pml": 148.0}
        )
        # ATR stop = 155 - 1.5*2.5 = 151.25; PML = 148.0 → min = 148.0
        assert params.stop == 148.0

    def test_position_size_by_grade(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        scores = DimensionScores()

        p_strong = engine._compute_trade_params(cand, Grade.STRONG_BUY, scores, {})
        p_buy = engine._compute_trade_params(cand, Grade.BUY, scores, {})
        p_hold = engine._compute_trade_params(cand, Grade.HOLD, scores, {})
        p_skip = engine._compute_trade_params(cand, Grade.SKIP, scores, {})

        assert 5.0 <= p_strong.position_size_pct <= 15.0
        assert 2.0 <= p_buy.position_size_pct <= 8.0
        assert 0.0 <= p_hold.position_size_pct <= 3.0
        assert p_skip.position_size_pct == 0.0

    def test_risk_reward_zero_on_invalid(self) -> None:
        tp = TradeParameters(entry=100.0, stop=105.0, target=110.0)
        assert tp.risk_reward_ratio == 0.0  # stop > entry → invalid risk


# ---------------------------------------------------------------------------
# Fallback Path
# ---------------------------------------------------------------------------


class TestFallbackPath:
    def test_no_llm_uses_rule_based(self) -> None:
        engine = AIScoringEngine(llm_client=None)
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.llm_used == "RuleBased"
        assert rec.grade in (Grade.STRONG_BUY, Grade.BUY, Grade.HOLD, Grade.SKIP)
        assert rec.reasoning
        assert len(rec.key_factors) >= 1

    def test_llm_failure_falls_back(self) -> None:
        engine = AIScoringEngine(llm_client=_FailingLLM())
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.llm_used == "RuleBased"
        assert rec.scores.total >= 0

    def test_fallback_llm_tried_first(self) -> None:
        primary = _FailingLLM()
        fallback = _FakeLLM(
            {
                "gap_quality": 20,
                "technical_setup": 15,
                "sentiment": 15,
                "liquidity": 12,
                "risk": 8,
                "market_context": 8,
                "confidence": 0.75,
                "entry": 155.0,
                "stop": 152.0,
                "target": 159.0,
                "position_size_pct": 10.0,
                "reasoning": "Fallback LLM analysis.",
                "key_factors": ["strong gap"],
                "risk_factors": [],
            }
        )
        engine = AIScoringEngine(llm_client=primary, fallback_llm_client=fallback)
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.llm_used == "FallbackLLM"
        assert rec.scores.total == 78
        assert rec.confidence == 0.75

    def test_pipeline_produces_output_for_zero_candidates(self) -> None:
        # Ensures no crash when scoring edge-case candidate
        engine = AIScoringEngine()
        cand = _make_candidate(gap_pct=0.0, bid=150.0, ask=150.0)
        rec = engine.score(cand)
        # Zero-gap candidate may still get HOLD/BUY from cross-validation
        # and tight spread; just verify the pipeline does not crash
        assert rec.scores.total >= 0
        assert rec.reasoning


# ---------------------------------------------------------------------------
# LLM Path
# ---------------------------------------------------------------------------


class TestLLMPath:
    def test_llm_success(self) -> None:
        llm = _FakeLLM(
            {
                "gap_quality": 22,
                "technical_setup": 18,
                "sentiment": 15,
                "liquidity": 13,
                "risk": 8,
                "market_context": 7,
                "confidence": 0.8,
                "entry": 156.0,
                "stop": 153.0,
                "target": 160.0,
                "position_size_pct": 12.0,
                "reasoning": "Strong gap with rising trend.",
                "key_factors": ["gap > 3%", "rising trend"],
                "risk_factors": ["elevated volatility"],
            }
        )
        engine = AIScoringEngine(llm_client=llm)
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.llm_used == "LLM"
        assert rec.grade == Grade.STRONG_BUY
        assert rec.scores.total == 83
        assert rec.confidence == 0.8
        assert rec.trade_params.entry == 156.0
        assert len(rec.key_factors) == 2
        assert len(rec.risk_factors) == 1

    def test_llm_response_with_markdown(self) -> None:
        class MarkdownLLM:
            def complete(self, prompt: str) -> str:
                return (
                    "```json\n"
                    '{"gap_quality":20,"technical_setup":15,"sentiment":10,'
                    '"liquidity":10,"risk":5,"market_context":5,'
                    '"confidence":0.6,"entry":155.0,"stop":152.0,'
                    '"target":159.0,"position_size_pct":5.0,'
                    '"reasoning":"ok","key_factors":["a"],'
                    '"risk_factors":[]}\n'
                    "```"
                )

        engine = AIScoringEngine(llm_client=MarkdownLLM())
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.scores.total == 65
        assert rec.grade == Grade.BUY

    def test_llm_malformed_json_falls_back(self) -> None:
        class BadJSONLLM:
            def complete(self, prompt: str) -> str:
                return "not json at all"

        engine = AIScoringEngine(llm_client=BadJSONLLM())
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.llm_used == "RuleBased"


# ---------------------------------------------------------------------------
# Recommendation Structure
# ---------------------------------------------------------------------------


class TestRecommendationStructure:
    def test_reasoning_length(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        rec = engine.score(cand)
        assert len(rec.reasoning) >= 50

    def test_trace_id_populated(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        rec = engine.score(cand, trace_id="abc123")
        assert rec.trace_id == "abc123"

    def test_timestamp_populated(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.timestamp
        assert "T" in rec.timestamp  # ISO format

    def test_scores_sum_equals_total(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        rec = engine.score(cand)
        assert rec.scores.total == (
            rec.scores.gap_quality
            + rec.scores.technical_setup
            + rec.scores.sentiment
            + rec.scores.liquidity
            + rec.scores.risk
            + rec.scores.market_context
        )


# ---------------------------------------------------------------------------
# LLM Client Units
# ---------------------------------------------------------------------------


class TestLLMClients:
    def test_deepseek_raises_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        client = DeepSeekClient(api_key="")
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
            client.complete("hello")

    def test_kimi_raises_without_key(self, monkeypatch) -> None:
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        client = KimiClient(api_key="")
        with pytest.raises(RuntimeError, match="KIMI_API_KEY"):
            client.complete("hello")

    def test_deepseek_env_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        client = DeepSeekClient()
        assert client._api_key == "test-key"

    def test_kimi_env_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("KIMI_API_KEY", "test-key")
        client = KimiClient()
        assert client._api_key == "test-key"


# ---------------------------------------------------------------------------
# Integration: End-to-End Scoring Flows
# ---------------------------------------------------------------------------


class TestEndToEndFlows:
    def test_strong_buy_flow(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(
            gap_pct=8.0, trend="RISING", cross_validated=True, pass_number=2
        )
        ctx = {
            "atr": 2.5,
            "pmh": 160.0,
            "pml": 150.0,
            "relative_volume": 3.0,
            "sentiment_score": 18,
        }
        rec = engine.score(cand, context=ctx)
        assert rec.grade == Grade.STRONG_BUY
        assert rec.conviction == Conviction.STRONG
        assert rec.trade_params.risk_reward_ratio >= 1.5
        assert 5.0 <= rec.trade_params.position_size_pct <= 15.0

    def test_avoid_flow(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(
            gap_pct=0.1,
            trend="FADING",
            cross_validated=False,
            pass_number=1,
            bid=149.0,
            ask=151.0,
        )
        rec = engine.score(cand)
        assert rec.grade == Grade.SKIP
        assert rec.trade_params.position_size_pct == 0.0

    def test_cautious_flow(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate(gap_pct=2.5, trend="STABLE", cross_validated=False)
        rec = engine.score(cand)
        assert rec.grade in (Grade.HOLD, Grade.SKIP)

    def test_full_context_enrichment(self) -> None:
        engine = AIScoringEngine()
        cand = _make_candidate()
        ctx = {
            "atr": 2.5,
            "pmh": 158.0,
            "pml": 152.0,
            "rsi": 55.0,
            "sector": "Technology",
            "relative_volume": 2.5,
            "dollar_volume": 500_000_000,
            "index_alignment": 0.2,
            "sentiment_score": 15,
            "headline_count": 8,
        }
        rec = engine.score(cand, context=ctx)
        assert rec.scores.total > 0
        assert rec.confidence >= 0.6
        assert rec.reasoning
        assert rec.timestamp
        assert rec.trace_id
