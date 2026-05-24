"""Unit tests for the readiness report generator."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

from sam_trader.services.ai_scoring import (
    AIRecommendation,
    Conviction,
    DimensionScores,
    Grade,
    TradeParameters,
)
from sam_trader.services.gap_scanner import GapCandidate
from sam_trader.services.heat_monitor import HeatMapEntry, HeatMonitorResult
from sam_trader.services.pipeline_executor import (
    PipelineCandidate,
    PipelineResult,
    PipelineStageRecord,
)
from sam_trader.services.readiness_report import ReadinessReportGenerator
from sam_trader.services.regime_detection import Regime, RegimePrediction
from sam_trader.services.risk_checks import RiskCheckResult
from sam_trader.services.risk_sizing import PositionSizeResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gap_candidate(
    instrument_id: str = "TSLA.NASDAQ",
    gap_pct: float = 3.33,
    quote_last: float = 155.0,
) -> GapCandidate:
    return GapCandidate(
        instrument_id=instrument_id,
        prev_close=150.0,
        quote_last=quote_last,
        gap_pct=gap_pct,
        bid=154.9,
        ask=155.1,
        volume=1_000_000.0,
        trend="STABLE",
        pass_number=1,
        cross_validated=True,
        cross_validation_note="",
    )


def _make_recommendation(
    instrument_id: str = "TSLA.NASDAQ",
    grade: Grade = Grade.BUY,
    total_score: int = 65,
) -> AIRecommendation:
    return AIRecommendation(
        instrument_id=instrument_id,
        grade=grade,
        conviction=Conviction.MODERATE,
        confidence=0.6,
        scores=DimensionScores(
            gap_quality=total_score,
            technical_setup=0,
            sentiment=0,
            liquidity=0,
            risk=0,
            market_context=0,
        ),
        trade_params=TradeParameters(
            entry=155.0,
            stop=150.0,
            target=162.5,
            position_size_pct=0.02,
        ),
        reasoning="Strong gap with technical support",
        key_factors=["gap", "support"],
        risk_factors=["earnings soon"],
        llm_used="RuleBased",
        trace_id="test-trace",
        timestamp="2026-05-24T08:00:00+00:00",
    )


def _make_approved_candidate(
    instrument_id: str = "TSLA.NASDAQ",
    grade: Grade = Grade.BUY,
    score: int = 65,
    position_size: int = 50,
    risk_passed: bool = True,
) -> PipelineCandidate:
    gap = _make_gap_candidate(instrument_id=instrument_id)
    rec = _make_recommendation(
        instrument_id=instrument_id, grade=grade, total_score=score
    )
    size = PositionSizeResult(
        position_size=position_size,
        max_risk_dollars=500.0,
        var_95=300.0,
    )
    risk = RiskCheckResult(
        passed=risk_passed,
        rejected_reasons=[],
        post_trade_exposure=0.0,
        estimated_risk_dollars=500.0,
        required_margin=0.0,
    )
    return PipelineCandidate(
        gap=gap,
        recommendation=rec,
        position_size=size,
        risk_check=risk,
        approved=True,
    )


def _make_rejected_candidate(
    instrument_id: str = "BAD.NASDAQ",
    reason: str = "Risk check failed: max exposure exceeded",
) -> PipelineCandidate:
    gap = _make_gap_candidate(instrument_id=instrument_id, gap_pct=-0.5)
    risk = RiskCheckResult(
        passed=False,
        rejected_reasons=[reason],
        post_trade_exposure=0.0,
        estimated_risk_dollars=0.0,
        required_margin=0.0,
    )
    return PipelineCandidate(
        gap=gap,
        risk_check=risk,
        approved=False,
        rejection_reason=reason,
    )


def _make_pipeline_result(
    approved: list[PipelineCandidate] | None = None,
    rejected: list[PipelineCandidate] | None = None,
    heat_result: Any | None = None,
    regime_prediction: Any | None = None,
    audit_trail: list[PipelineStageRecord] | None = None,
    trace_id: str = "test-trace",
) -> PipelineResult:
    return PipelineResult(
        approved=approved or [],
        rejected=rejected or [],
        heat_result=heat_result,
        regime_prediction=regime_prediction,
        audit_trail=audit_trail or [],
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReadinessReportGenerator:
    """Tests for ReadinessReportGenerator."""

    def test_generate_basic(self) -> None:
        approved = [
            _make_approved_candidate("TSLA.NASDAQ", Grade.STRONG_BUY, 85, 100),
            _make_approved_candidate("AAPL.NASDAQ", Grade.BUY, 62, 50),
        ]
        rejected = [_make_rejected_candidate("BAD.NASDAQ")]
        result = _make_pipeline_result(approved=approved, rejected=rejected)

        gen = ReadinessReportGenerator()
        report = gen.generate(
            result, bundle_path="config/bundles.daily.yaml", market="US"
        )

        assert report.market == "US"
        assert report.candidate_count == 3
        assert report.approved_count == 2
        assert report.rejected_count == 1
        assert report.bundles_generated == 2
        assert report.bundle_path == "config/bundles.daily.yaml"
        assert report.trace_id == "test-trace"
        assert len(report.top_recommendations) == 2
        assert report.top_recommendations[0]["symbol"] == "TSLA.NASDAQ"
        assert report.top_recommendations[0]["grade"] == "STRONG_BUY"

    def test_generate_empty_pipeline(self) -> None:
        result = _make_pipeline_result()
        gen = ReadinessReportGenerator()
        report = gen.generate(result)

        assert report.candidate_count == 0
        assert report.approved_count == 0
        assert report.rejected_count == 0
        assert report.top_recommendations == []
        assert report.bundles_generated == 0
        assert report.risk_summary["risk_checks_passed"] == 0
        assert report.risk_summary["risk_checks_total"] == 0

    def test_generate_with_heat_and_regime(self) -> None:
        approved = [_make_approved_candidate()]
        heat = HeatMonitorResult(
            total_heat_pct=0.045,
            total_notional=7_750.0,
            heat_map={
                "TSLA.NASDAQ": HeatMapEntry(
                    instrument_id="TSLA.NASDAQ",
                    risk_contribution=0.01,
                    notional=7_750.0,
                    concentration_pct=0.00775,
                    warning="",
                )
            },
            sector_map={"tech": 7_750.0},
            warnings=[],
            passed=True,
        )
        regime = RegimePrediction(
            regime=Regime.TRENDING,
            confidence=0.82,
            is_stable=True,
            model_version="20260524-5-1234",
        )
        audit = [
            PipelineStageRecord(
                stage="ai_scoring",
                timestamp="2026-05-24T08:00:00+00:00",
                input_count=10,
                output_count=5,
                errors=[],
                notes="",
            )
        ]
        result = _make_pipeline_result(
            approved=approved,
            heat_result=heat,
            regime_prediction=regime,
            audit_trail=audit,
        )

        gen = ReadinessReportGenerator()
        report = gen.generate(result)

        assert report.risk_summary["portfolio_heat_pct"] == 4.5
        assert report.risk_summary["heat_passed"] is True
        assert report.regime_state["regime"] == Regime.TRENDING.value
        assert report.regime_state["confidence"] == 0.82
        assert report.regime_state["stable"] is True
        assert report.audit_trail[0]["stage"] == "ai_scoring"

    def test_generate_warnings_from_rejections(self) -> None:
        rejected = [
            _make_rejected_candidate("XYZ.NASDAQ", "Too risky"),
            _make_rejected_candidate("ABC.NASDAQ", "Heat exceeded"),
        ]
        heat = HeatMonitorResult(
            total_heat_pct=0.055,
            total_notional=0.0,
            heat_map={},
            sector_map={},
            warnings=["Heat exceeded"],
            passed=False,
        )
        result = _make_pipeline_result(rejected=rejected, heat_result=heat)

        gen = ReadinessReportGenerator()
        report = gen.generate(result)

        warnings = report.risk_summary["warnings"]
        assert any("Portfolio heat" in w for w in warnings)
        assert any("XYZ.NASDAQ" in w for w in warnings)
        assert any("ABC.NASDAQ" in w for w in warnings)

    def test_format_table(self) -> None:
        approved = [
            _make_approved_candidate("TSLA.NASDAQ", Grade.STRONG_BUY, 85, 100),
            _make_approved_candidate("AAPL.NASDAQ", Grade.BUY, 62, 50),
        ]
        result = _make_pipeline_result(approved=approved)
        gen = ReadinessReportGenerator()
        report = gen.generate(result, bundle_path="config/bundles.daily.yaml")
        table = gen.format_table(report)

        assert "SAM Trader V3" in table
        assert "TSLA.NASDAQ" in table
        assert "AAPL.NASDAQ" in table
        assert "STRONG_BUY" in table
        assert "BUY" in table
        assert "config/bundles.daily.yaml" in table
        assert "Market Regime" in table
        assert "Risk Summary" in table
        assert "Bundle Generation" in table

    def test_format_table_empty(self) -> None:
        result = _make_pipeline_result()
        gen = ReadinessReportGenerator()
        report = gen.generate(result)
        table = gen.format_table(report)

        assert "SAM Trader V3" in table
        assert "(none)" in table

    def test_save_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            approved = [_make_approved_candidate()]
            result = _make_pipeline_result(approved=approved)
            gen = ReadinessReportGenerator(log_dir=tmpdir)
            report = gen.generate(result, bundle_path="config/bundles.daily.yaml")
            path = gen.save_audit(report)

            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
                data = json.loads(text)

            assert data["market"] == "US"
            assert data["approved_count"] == 1
            assert data["bundles_generated"] == 1
            assert data["bundle_path"] == "config/bundles.daily.yaml"
            assert "top_recommendations" in data
            assert "risk_summary" in data

    def test_save_audit_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, "nested", "readiness")
            gen = ReadinessReportGenerator(log_dir=log_dir)
            result = _make_pipeline_result()
            report = gen.generate(result)
            path = gen.save_audit(report)

            assert os.path.exists(path)
            assert os.path.isdir(log_dir)

    def test_send_webhook_generic_success(self) -> None:
        approved = [_make_approved_candidate()]
        result = _make_pipeline_result(approved=approved)
        gen = ReadinessReportGenerator(webhook_url="https://example.com/hook")
        report = gen.generate(result)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.getcode.return_value = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            ok = gen.send_webhook(report)
            assert ok is True

            call_args = mock_urlopen.call_args
            req = call_args[0][0]
            assert isinstance(req, urllib.request.Request)
            assert req.full_url == "https://example.com/hook"
            assert req.get_method() == "POST"
            assert req.get_header("Content-type") == "application/json"

            body = json.loads(req.data.decode("utf-8"))  # type: ignore[union-attr]
            assert body["approved_count"] == 1

    def test_send_webhook_slack_format(self) -> None:
        approved = [_make_approved_candidate()]
        result = _make_pipeline_result(approved=approved)
        gen = ReadinessReportGenerator(
            webhook_url="https://hooks.slack.com/services/xxx"
        )
        report = gen.generate(result)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.getcode.return_value = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            gen.send_webhook(report)
            req = mock_urlopen.call_args[0][0]
            body = json.loads(req.data)  # type: ignore[attr-defined]
            assert "text" in body
            assert "SAM Trader V3" in body["text"]

    def test_send_webhook_telegram_format(self) -> None:
        approved = [_make_approved_candidate()]
        result = _make_pipeline_result(approved=approved)
        gen = ReadinessReportGenerator(
            webhook_url="https://api.telegram.org/botxxx/sendMessage"
        )
        report = gen.generate(result)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.getcode.return_value = 200
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            gen.send_webhook(report)
            req = mock_urlopen.call_args[0][0]
            body = json.loads(req.data)  # type: ignore[attr-defined]
            assert "text" in body
            assert "parse_mode" in body
            assert body["parse_mode"] == "HTML"

    def test_send_webhook_failure(self) -> None:
        result = _make_pipeline_result()
        gen = ReadinessReportGenerator(webhook_url="https://example.com/hook")
        report = gen.generate(result)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
            ok = gen.send_webhook(report)
            assert ok is False

    def test_send_webhook_no_url(self) -> None:
        result = _make_pipeline_result()
        gen = ReadinessReportGenerator(webhook_url=None)
        report = gen.generate(result)
        assert gen.send_webhook(report) is False

    def test_top_n_limit(self) -> None:
        approved = [
            _make_approved_candidate(f"SYM{i}.NASDAQ", Grade.BUY, 60 + i, 10)
            for i in range(10)
        ]
        result = _make_pipeline_result(approved=approved)
        gen = ReadinessReportGenerator(top_n=3)
        report = gen.generate(result)

        assert len(report.top_recommendations) == 3
        # Highest scores first
        assert report.top_recommendations[0]["symbol"] == "SYM9.NASDAQ"

    def test_risk_reward_calculation(self) -> None:
        gap = _make_gap_candidate("TSLA.NASDAQ")
        rec = AIRecommendation(
            instrument_id="TSLA.NASDAQ",
            grade=Grade.BUY,
            conviction=Conviction.MODERATE,
            confidence=0.6,
            scores=DimensionScores(),
            trade_params=TradeParameters(entry=100.0, stop=95.0, target=110.0),
            reasoning="test",
        )
        size = PositionSizeResult(position_size=10, max_risk_dollars=50.0, var_95=30.0)
        pc = PipelineCandidate(
            gap=gap, recommendation=rec, position_size=size, approved=True
        )
        result = _make_pipeline_result(approved=[pc])

        gen = ReadinessReportGenerator()
        report = gen.generate(result)

        assert report.top_recommendations[0]["risk_reward"] == "2.0:1"
        assert report.top_recommendations[0]["entry"] == 100.0
        assert report.top_recommendations[0]["stop"] == 95.0
        assert report.top_recommendations[0]["target"] == 110.0

    def test_candidate_without_recommendation(self) -> None:
        """Approved candidates without AI recommendation should still appear."""
        gap = _make_gap_candidate("TSLA.NASDAQ")
        size = PositionSizeResult(position_size=10, max_risk_dollars=50.0, var_95=30.0)
        pc = PipelineCandidate(gap=gap, position_size=size, approved=True)
        result = _make_pipeline_result(approved=[pc])

        gen = ReadinessReportGenerator()
        report = gen.generate(result)

        assert len(report.top_recommendations) == 1
        assert report.top_recommendations[0]["grade"] == "N/A"
        assert report.top_recommendations[0]["score"] == 0
        assert report.top_recommendations[0]["risk_reward"] == "N/A"

    def test_audit_trail_conversion(self) -> None:
        audit = [
            PipelineStageRecord(
                stage="ai_scoring",
                timestamp="2026-05-24T08:00:00+00:00",
                input_count=10,
                output_count=5,
                errors=["err1"],
                notes="note1",
            ),
            PipelineStageRecord(
                stage="merge",
                timestamp="2026-05-24T08:01:00+00:00",
                input_count=5,
                output_count=3,
                errors=[],
                notes="regime=trending",
            ),
        ]
        result = _make_pipeline_result(audit_trail=audit)
        gen = ReadinessReportGenerator()
        report = gen.generate(result)

        assert len(report.audit_trail) == 2
        assert report.audit_trail[0]["stage"] == "ai_scoring"
        assert report.audit_trail[0]["errors"] == ["err1"]
        assert report.audit_trail[1]["notes"] == "regime=trending"
