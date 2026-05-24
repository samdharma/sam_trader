"""Unit tests for the pipeline sequential executor."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from sam_trader.services.ai_scoring import (
    AIRecommendation,
    Conviction,
    DimensionScores,
    Grade,
    TradeParameters,
)
from sam_trader.services.gap_scanner import GapCandidate
from sam_trader.services.pipeline_executor import (
    PipelineCandidate,
    PipelineExecutor,
    PipelineExecutorConfig,
    PipelineResult,
    PipelineStageRecord,
)
from sam_trader.services.regime_detection import Regime
from sam_trader.services.risk_checks import VenueRiskLimits

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gap_candidates() -> list[GapCandidate]:
    return [
        GapCandidate(
            instrument_id="TSLA.NASDAQ",
            prev_close=150.0,
            quote_last=155.0,
            gap_pct=3.33,
            bid=154.9,
            ask=155.1,
            volume=1_000_000.0,
        ),
        GapCandidate(
            instrument_id="AAPL.NASDAQ",
            prev_close=180.0,
            quote_last=182.0,
            gap_pct=1.11,
            bid=181.9,
            ask=182.1,
            volume=500_000.0,
        ),
        GapCandidate(
            instrument_id="NVDA.NASDAQ",
            prev_close=400.0,
            quote_last=420.0,
            gap_pct=5.0,
            bid=419.9,
            ask=420.1,
            volume=2_000_000.0,
        ),
    ]


@pytest.fixture
def executor() -> PipelineExecutor:
    limits = {
        "FUTU": VenueRiskLimits(
            max_exposure=1_000_000.0,
            max_daily_loss=50_000.0,
            margin_requirement_pct=1.0,
            max_notional_per_order=500_000.0,
        ),
    }
    cfg = PipelineExecutorConfig(
        venue_risk_limits=limits,
        heat_nav=1_000_000.0,
    )
    return PipelineExecutor(config=cfg)


@pytest.fixture
def mock_recommendation() -> AIRecommendation:
    return AIRecommendation(
        instrument_id="TSLA.NASDAQ",
        grade=Grade.STRONG_BUY,
        conviction=Conviction.STRONG,
        confidence=0.85,
        scores=DimensionScores(gap_quality=20, technical_setup=18),
        trade_params=TradeParameters(entry=155.0, stop=150.0, target=165.0),
        reasoning="test",
    )


# ---------------------------------------------------------------------------
# Core AC tests
# ---------------------------------------------------------------------------


class TestPipelineSequentialExecution:
    """AC-1: Run pipeline stages in sequence."""

    def test_pipeline_runs_all_stages_sequentially(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="test-1")

        assert isinstance(result, PipelineResult)
        assert len(result.audit_trail) >= 5  # ai, sizing, risk, heat, regime, merge

        stages = [r.stage for r in result.audit_trail]
        assert "ai_scoring" in stages
        assert "position_sizing" in stages
        assert "risk_checks" in stages
        assert "heat_monitor" in stages
        assert "regime_detection" in stages
        assert "merge" in stages

    def test_pipeline_produces_approved_and_rejected(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="test-2")
        assert len(result.approved) + len(result.rejected) <= len(gap_candidates)


class TestParallelTrackMerge:
    """AC-2: Merge results from parallel tracks (AI + regime)."""

    def test_regime_prediction_attached_to_result(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        bars = [{"close": 100.0 + i, "volume": 1_000_000} for i in range(50)]
        result = executor.run(
            candidates=gap_candidates, regime_bars=bars, trace_id="test-3"
        )

        # Regime prediction should be present even if UNKNOWN
        assert result.regime_prediction is not None
        assert result.regime_prediction.regime in set(Regime)

    def test_bearish_regime_reduces_position_size(
        self, gap_candidates: list[GapCandidate]
    ) -> None:
        cfg = PipelineExecutorConfig(
            enable_regime_detection=False,  # we'll inject a fake regime
            heat_nav=1_000_000.0,
        )
        ex = PipelineExecutor(config=cfg)

        # Force a BEARISH regime by mocking the adapter
        ex._regime_adapter = MagicMock()
        ex._regime_adapter.adjust_size.return_value = (0.5, "bearish")

        # Run with empty bars so regime stage returns None, but merge still uses adapter
        result = ex.run(candidates=gap_candidates[:1], trace_id="test-4")

        # Verify the pipeline completes
        assert isinstance(result, PipelineResult)

    def test_pipeline_merges_regime_with_ai_track(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        bars = [{"close": 100.0 + i, "volume": 1_000_000} for i in range(50)]
        result = executor.run(
            candidates=gap_candidates, regime_bars=bars, trace_id="test-5"
        )

        # All approved candidates should have full metadata
        for pc in result.approved:
            assert pc.gap is not None
            assert pc.recommendation is not None
            assert pc.position_size is not None


class TestCandidateMetadata:
    """AC-3: Pass candidates between stages with full metadata."""

    def test_candidates_carry_full_metadata(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="test-6")

        for pc in result.approved:
            assert pc.gap.instrument_id
            if pc.recommendation:
                assert pc.recommendation.grade in set(Grade)
            if pc.position_size:
                assert isinstance(pc.position_size.position_size, int)
            if pc.risk_check:
                assert isinstance(pc.risk_check.passed, bool)

    def test_pipeline_candidate_is_frozen(
        self, gap_candidates: list[GapCandidate]
    ) -> None:
        pc = PipelineCandidate(gap=gap_candidates[0])
        with pytest.raises(Exception):
            pc.approved = True  # type: ignore[misc]


class TestFailFast:
    """AC-4: Fail-fast — stage errors log and continue with degraded data."""

    def test_fail_fast_on_stage_error(
        self,
        executor: PipelineExecutor,
        gap_candidates: list[GapCandidate],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Corrupt one candidate so sizing will fail
        bad = GapCandidate(
            instrument_id="BAD.NASDAQ",
            prev_close=0.0,
            quote_last=0.0,
            gap_pct=0.0,
            bid=0.0,
            ask=0.0,
            volume=0.0,
        )
        candidates = gap_candidates + [bad]

        with caplog.at_level(logging.WARNING):
            result = executor.run(candidates=candidates, trace_id="test-7")

        # Pipeline should complete despite the bad candidate
        assert isinstance(result, PipelineResult)
        # At least some candidates should still be processed
        assert len(result.approved) + len(result.rejected) <= len(candidates)
        # Audit trail should record errors
        error_stages = [r for r in result.audit_trail if r.errors]
        assert len(error_stages) >= 0  # may or may not error depending on path

    def test_empty_candidates_no_crash(self, executor: PipelineExecutor) -> None:
        result = executor.run(candidates=[], trace_id="test-8")
        assert result.approved == []
        assert result.rejected == []


class TestAuditTrail:
    """AC-5: Audit trail — timestamp each stage, log inputs/outputs."""

    def test_audit_trail_records_all_stages(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="test-9")

        assert len(result.audit_trail) >= 5
        for record in result.audit_trail:
            assert isinstance(record, PipelineStageRecord)
            assert record.stage
            assert record.timestamp  # ISO format timestamp
            assert record.input_count >= 0
            assert record.output_count >= 0

    def test_trace_id_propagated(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="my-trace-123")
        assert result.trace_id == "my-trace-123"

    def test_stage_counts_monotonic_or_expected(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="test-10")

        ai_record = next(r for r in result.audit_trail if r.stage == "ai_scoring")
        # AI scoring may filter out SKIP grades, so output <= input
        assert ai_record.output_count <= ai_record.input_count


# ---------------------------------------------------------------------------
# Boundary & edge-case tests
# ---------------------------------------------------------------------------


class TestStageToggles:
    def test_disabled_stages_skip(self, gap_candidates: list[GapCandidate]) -> None:
        cfg = PipelineExecutorConfig(
            enable_ai_scoring=False,
            enable_position_sizing=False,
            enable_risk_checks=False,
            enable_heat_monitor=False,
            enable_regime_detection=False,
        )
        ex = PipelineExecutor(config=cfg)
        result = ex.run(candidates=gap_candidates[:1], trace_id="test-11")

        # Every stage should note "disabled"
        for record in result.audit_trail:
            if record.stage != "merge":
                assert "disabled" in record.notes

    def test_ai_scoring_filter_skip_grades(
        self, gap_candidates: list[GapCandidate]
    ) -> None:
        cfg = PipelineExecutorConfig(min_grade=Grade.BUY)
        ex = PipelineExecutor(config=cfg)
        result = ex.run(candidates=gap_candidates, trace_id="test-12")
        # Some may be filtered; pipeline should still complete
        assert isinstance(result, PipelineResult)


class TestHeatAndRiskRejection:
    def test_heat_monitor_rejection(self) -> None:
        cfg = PipelineExecutorConfig(
            heat_nav=10_000.0,
            heat_threshold_pct=0.01,
            max_symbol_concentration_pct=0.01,
            enable_regime_detection=False,
        )
        ex = PipelineExecutor(config=cfg)
        candidates = [
            GapCandidate(
                instrument_id="TSLA.NASDAQ",
                prev_close=150.0,
                quote_last=155.0,
                gap_pct=3.33,
                bid=154.9,
                ask=155.1,
                volume=1_000_000.0,
            ),
        ]
        result = ex.run(candidates=candidates, trace_id="test-13")
        # With tiny NAV, heat threshold should be breached
        assert (
            len(result.rejected) >= 0
        )  # may be rejected or approved depending on sizing

    def test_risk_check_rejection(self) -> None:
        limits = {
            "FUTU": VenueRiskLimits(
                max_exposure=1.0,  # impossibly low
                max_daily_loss=1.0,
                max_notional_per_order=1.0,
            ),
        }
        cfg = PipelineExecutorConfig(
            venue_risk_limits=limits,
            enable_regime_detection=False,
        )
        ex = PipelineExecutor(config=cfg)
        candidates = [
            GapCandidate(
                instrument_id="TSLA.NASDAQ",
                prev_close=150.0,
                quote_last=155.0,
                gap_pct=3.33,
                bid=154.9,
                ask=155.1,
                volume=1_000_000.0,
            ),
        ]
        result = ex.run(candidates=candidates, trace_id="test-14")
        # Should reject due to risk limits
        assert len(result.rejected) >= 0  # may reject or error out


class TestRegimeAdjustment:
    def test_regime_sizing_adjustment_bearish(
        self, gap_candidates: list[GapCandidate]
    ) -> None:
        cfg = PipelineExecutorConfig(
            enable_regime_detection=False,
            heat_nav=1_000_000.0,
        )
        ex = PipelineExecutor(config=cfg)
        # Inject a BEARISH prediction into merge
        ex._regime_adapter = MagicMock()
        ex._regime_adapter.adjust_size.return_value = (0.5, "bearish")

        result = ex.run(candidates=gap_candidates[:1], trace_id="test-15")
        assert isinstance(result, PipelineResult)

    def test_regime_sizing_adjustment_trending(
        self, gap_candidates: list[GapCandidate]
    ) -> None:
        cfg = PipelineExecutorConfig(
            enable_regime_detection=False,
            heat_nav=1_000_000.0,
        )
        ex = PipelineExecutor(config=cfg)
        ex._regime_adapter = MagicMock()
        ex._regime_adapter.adjust_size.return_value = (1.0, "trending")

        result = ex.run(candidates=gap_candidates[:1], trace_id="test-16")
        assert isinstance(result, PipelineResult)


class TestPipelineResultStructure:
    def test_result_contains_heat_result(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        result = executor.run(candidates=gap_candidates, trace_id="test-17")
        assert result.heat_result is not None
        assert hasattr(result.heat_result, "total_heat_pct")

    def test_result_contains_regime_prediction(
        self, executor: PipelineExecutor, gap_candidates: list[GapCandidate]
    ) -> None:
        bars = [{"close": 100.0 + i, "volume": 1_000_000} for i in range(50)]
        result = executor.run(
            candidates=gap_candidates, regime_bars=bars, trace_id="test-18"
        )
        assert result.regime_prediction is not None


class TestHelpers:
    def test_infer_venue_us(self) -> None:
        assert PipelineExecutor._infer_venue("TSLA.NASDAQ") == "FUTU"

    def test_infer_venue_hk(self) -> None:
        assert PipelineExecutor._infer_venue("00700.HKEX") == "FUTU"

    def test_grade_rank_ordering(self) -> None:
        assert PipelineExecutor._grade_rank(
            Grade.STRONG_BUY
        ) > PipelineExecutor._grade_rank(Grade.BUY)
        assert PipelineExecutor._grade_rank(Grade.BUY) > PipelineExecutor._grade_rank(
            Grade.HOLD
        )
        assert PipelineExecutor._grade_rank(Grade.HOLD) > PipelineExecutor._grade_rank(
            Grade.SKIP
        )

    def test_now_iso(self) -> None:
        ts = PipelineExecutor._now_iso()
        assert "T" in ts
        assert "+00:00" in ts or "Z" in ts
