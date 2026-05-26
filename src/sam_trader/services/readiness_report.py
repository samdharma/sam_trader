"""Readiness report generator for pre-market pipeline output.

Produces a daily summary table, optional webhook notification,
and JSON audit trail.

Usage
-----
    from sam_trader.services.readiness_report import (
        ReadinessReport,
        ReadinessReportGenerator,
    )

    generator = ReadinessReportGenerator()
    report = generator.generate(
        pipeline_result, bundle_path="config/bundles.daily.yaml"
    )
    print(generator.format_table(report))
    generator.save_audit(report)
    generator.send_webhook(report)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sam_trader.services.market_calendar import MarketCalendarService
from sam_trader.services.pipeline_executor import PipelineCandidate, PipelineResult

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = "logs/readiness"
_DEFAULT_TOP_N = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadinessReport:
    """Structured daily pre-market readiness report."""

    scan_timestamp: str
    market: str
    candidate_count: int
    approved_count: int
    rejected_count: int
    top_recommendations: list[dict[str, Any]]
    risk_summary: dict[str, Any]
    regime_state: dict[str, Any]
    bundles_generated: int
    bundle_path: str | None
    audit_trail: list[dict[str, Any]]
    trace_id: str = ""
    data_pipeline: dict[str, Any] = field(default_factory=dict)
    holiday_skipped: bool = False
    holiday_name: str = ""
    next_trading_day: str = ""


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class ReadinessReportGenerator:
    """Generate readiness reports from pipeline results.

    Parameters
    ----------
    log_dir : str
        Directory for JSON audit files.  Created if missing.
    top_n : int
        Number of top recommendations to include in the report.
    webhook_url : str | None
        Optional webhook URL.  Falls back to ``READINESS_WEBHOOK_URL`` env var.
    """

    def __init__(
        self,
        log_dir: str = _DEFAULT_LOG_DIR,
        top_n: int = _DEFAULT_TOP_N,
        webhook_url: str | None = None,
        redis_client: Any | None = None,
        bar_stale_threshold_seconds: int = 300,
        market_calendar: MarketCalendarService | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.top_n = top_n
        self.webhook_url = webhook_url or os.getenv("READINESS_WEBHOOK_URL")
        self._redis = redis_client
        self.bar_stale_threshold_seconds = bar_stale_threshold_seconds
        self._calendar = market_calendar

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        pipeline_result: PipelineResult,
        bundle_path: str | None = None,
        market: str = "US",
    ) -> ReadinessReport:
        """Build a :class:`ReadinessReport` from a completed pipeline run.

        Parameters
        ----------
        pipeline_result
            Output of :class:`PipelineExecutor.run`.
        bundle_path
            Path to the generated bundle YAML file, if any.
        market
            Market identifier (e.g. ``"US"`` or ``"HK"``).

        Returns
        -------
        ReadinessReport
        """
        approved = pipeline_result.approved
        rejected = pipeline_result.rejected

        # Holiday handling
        holiday_skipped = pipeline_result.holiday_skipped
        holiday_name = pipeline_result.holiday_name
        next_trading_day_str = ""
        if holiday_skipped:
            calendar = self._calendar or MarketCalendarService.from_env()
            try:
                today = date.today()
                next_day = calendar.next_trading_day(market, today)
                next_trading_day_str = next_day.isoformat()
            except Exception as exc:
                logger.debug("Could not compute next_trading_day: %s", exc)

        top_recs = self._build_top_recommendations(approved)
        risk_summary = self._build_risk_summary(
            approved, rejected, pipeline_result.heat_result
        )
        regime_state = self._build_regime_state(pipeline_result.regime_prediction)
        audit_trail = self._build_audit_trail(pipeline_result.audit_trail)
        data_pipeline = self._build_data_pipeline_health(pipeline_result)

        return ReadinessReport(
            scan_timestamp=self._now_iso(),
            market=market.upper(),
            candidate_count=len(approved) + len(rejected),
            approved_count=len(approved),
            rejected_count=len(rejected),
            top_recommendations=top_recs,
            risk_summary=risk_summary,
            regime_state=regime_state,
            bundles_generated=len(approved),  # one bundle per approved candidate
            bundle_path=bundle_path,
            audit_trail=audit_trail,
            trace_id=pipeline_result.trace_id,
            data_pipeline=data_pipeline,
            holiday_skipped=holiday_skipped,
            holiday_name=holiday_name,
            next_trading_day=next_trading_day_str,
        )

    def format_table(self, report: ReadinessReport) -> str:
        """Format *report* as a human-readable summary table.

        Returns
        -------
        str
            Multi-line text suitable for console output.
        """
        lines: list[str] = []

        # Header
        lines.append("=" * 60)
        lines.append("SAM Trader V3 — Daily Pre-Market Readiness Report")
        lines.append("=" * 60)
        lines.append(f"Scan Time : {report.scan_timestamp}")
        lines.append(f"Market    : {report.market}")
        lines.append(f"Trace ID  : {report.trace_id or 'N/A'}")
        lines.append("")

        # Holiday banner
        if report.holiday_skipped:
            lines.append("!" * 60)
            lines.append(
                f"TODAY IS A MARKET HOLIDAY: {report.holiday_name} "
                f"({report.market}) — No trading session"
            )
            if report.next_trading_day:
                lines.append(f"Next Trading Day : {report.next_trading_day}")
            lines.append("!" * 60)
            lines.append("")

            lines.append("Candidate Summary")
            lines.append("-" * 60)
            lines.append("  N/A — market holiday")
            lines.append("")

            lines.append("Risk Summary")
            lines.append("-" * 60)
            lines.append("  N/A — market holiday")
            lines.append("")

            lines.append("Market Regime")
            lines.append("-" * 60)
            lines.append("  N/A — market holiday")
            lines.append("")

            lines.append("Bundle Generation")
            lines.append("-" * 60)
            lines.append("  Bundles Generated : 0")
            lines.append("  Bundle File       : N/A")
            lines.append("")
            lines.append("=" * 60)
            return "\n".join(lines)

        # Candidate summary
        lines.append("Candidate Summary")
        lines.append("-" * 60)
        lines.append(
            f"  Total: {report.candidate_count}  |  "
            f"Approved: {report.approved_count}  |  "
            f"Rejected: {report.rejected_count}"
        )
        lines.append("")

        # Top recommendations
        lines.append(f"Top {self.top_n} Recommendations")
        lines.append("-" * 60)
        if report.top_recommendations:
            header = (
                f"{'#':<4} {'Symbol':<14} {'Grade':<10} {'Score':<6} "
                f"{'Size':<8} {'Risk $':<10} {'R:R':<6}"
            )
            lines.append(header)
            lines.append("-" * 60)
            for idx, rec in enumerate(report.top_recommendations, start=1):
                lines.append(
                    f"{idx:<4} "
                    f"{rec['symbol']:<14} "
                    f"{rec['grade']:<10} "
                    f"{rec['score']:<6} "
                    f"{rec['size']:<8} "
                    f"{rec['risk_dollars']:<10} "
                    f"{rec['risk_reward']:<6}"
                )
        else:
            lines.append("  (none)")
        lines.append("")

        # Risk summary
        lines.append("Risk Summary")
        lines.append("-" * 60)
        rs = report.risk_summary
        lines.append(f"  Portfolio Heat : {rs.get('portfolio_heat_pct', 'N/A')}%")
        lines.append(f"  Heat Passed    : {rs.get('heat_passed', 'N/A')}")
        lines.append(
            f"  Risk Checks    : {rs.get('risk_checks_passed', 0)} passed / "
            f"{rs.get('risk_checks_total', 0)} total"
        )
        warnings = rs.get("warnings", [])
        if warnings:
            lines.append("  Warnings:")
            for w in warnings:
                lines.append(f"    • {w}")
        lines.append("")

        # Regime state
        lines.append("Market Regime")
        lines.append("-" * 60)
        reg = report.regime_state
        lines.append(f"  Regime     : {reg.get('regime', 'N/A')}")
        lines.append(f"  Confidence : {reg.get('confidence', 'N/A')}")
        lines.append(f"  Stable     : {reg.get('stable', 'N/A')}")
        lines.append("")

        # Bundles
        lines.append("Bundle Generation")
        lines.append("-" * 60)
        lines.append(f"  Bundles Generated : {report.bundles_generated}")
        if report.bundle_path:
            lines.append(f"  Bundle File       : {report.bundle_path}")
        else:
            lines.append("  Bundle File       : N/A")
        lines.append("")

        # Data pipeline
        lines.append("Data Pipeline")
        lines.append("-" * 60)
        dp = report.data_pipeline
        if dp:
            venue_conn = dp.get("venue_connection_state", {})
            for v, s in venue_conn.items():
                lines.append(f"  {v:<18} : {s}")
            sub = dp.get("subscription_health", {})
            lines.append(
                f"  Subscriptions    : {sub.get('active', 0)} active / "
                f"{sub.get('expected', 0)} expected"
            )
            passed = dp.get("data_pipeline_passed", False)
            lines.append(f"  Pipeline Pass    : {'PASS' if passed else 'FAIL'}")
            dp_warnings = dp.get("warnings", [])
            if dp_warnings:
                lines.append("  Warnings:")
                for w in dp_warnings:
                    lines.append(f"    • {w}")
            bar_flow = dp.get("bar_flow", [])
            if bar_flow:
                lines.append("  Bar Flow:")
                for b in bar_flow[:5]:
                    status = b.get("status", "UNKNOWN")
                    last = b.get("last_bar_time", "N/A") or "N/A"
                    if len(last) > 19:
                        last = last[:19]
                    lines.append(f"    {b['instrument_id']:<20} {status:<8} ({last})")
                if len(bar_flow) > 5:
                    lines.append(f"    ... and {len(bar_flow) - 5} more")
        else:
            lines.append("  (not available)")
        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)

    def send_webhook(self, report: ReadinessReport) -> bool:
        """Send the report to the configured webhook URL.

        Supports generic HTTP POST, Slack incoming webhooks, and
        Telegram Bot API.  Returns *True* on apparent success.
        """
        if not self.webhook_url:
            logger.debug("No webhook URL configured; skipping notification")
            return False

        payload = self._webhook_payload(report)
        body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "sam-trader-readiness/3.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = int(resp.getcode())
                logger.info("Webhook POST %s -> %d", self.webhook_url, status)
                return 200 <= status < 300
        except Exception as exc:
            logger.warning("Webhook delivery failed: %s", exc)
            return False

    def save_audit(self, report: ReadinessReport) -> str:
        """Save the report as JSON to ``logs/readiness/YYYY-MM-DD.json``.

        Returns
        -------
        str
            Absolute path of the written file.
        """
        self.log_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.log_dir / f"{date_str}.json"

        payload = self._report_to_dict(report)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        logger.info("Readiness audit saved to %s", path)
        return str(path.absolute())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _build_top_recommendations(
        self, approved: list[PipelineCandidate]
    ) -> list[dict[str, Any]]:
        """Extract top-N recommendation summaries from approved candidates."""
        recs: list[dict[str, Any]] = []

        # Sort by score descending (recommendation.scores.total if available)
        def _score_key(pc: PipelineCandidate) -> int:
            if pc.recommendation and pc.recommendation.scores:
                return pc.recommendation.scores.total
            return 0

        sorted_cands = sorted(approved, key=_score_key, reverse=True)

        for pc in sorted_cands[: self.top_n]:
            rec = pc.recommendation
            gap = pc.gap
            size = pc.position_size

            grade = rec.grade.value if rec else "N/A"
            score = rec.scores.total if rec and rec.scores else 0
            size_val = size.position_size if size else 0
            risk_dollars = size.max_risk_dollars if size else 0.0

            rr = 0.0
            if rec and rec.trade_params:
                rr = rec.trade_params.risk_reward_ratio

            recs.append(
                {
                    "symbol": gap.instrument_id,
                    "grade": grade,
                    "score": score,
                    "size": size_val,
                    "risk_dollars": f"${risk_dollars:,.0f}",
                    "risk_reward": f"{rr:.1f}:1" if rr > 0 else "N/A",
                    "entry": (
                        rec.trade_params.entry
                        if rec and rec.trade_params
                        else gap.quote_last
                    ),
                    "stop": rec.trade_params.stop if rec and rec.trade_params else 0.0,
                    "target": (
                        rec.trade_params.target if rec and rec.trade_params else 0.0
                    ),
                }
            )

        return recs

    @staticmethod
    def _build_risk_summary(
        approved: list[PipelineCandidate],
        rejected: list[PipelineCandidate],
        heat_result: Any | None,
    ) -> dict[str, Any]:
        """Build the risk-summary section."""
        risk_passed = 0
        risk_total = 0
        for pc in approved:
            if pc.risk_check is not None:
                risk_total += 1
                if pc.risk_check.passed:
                    risk_passed += 1

        heat_pct = 0.0
        heat_passed = True
        if heat_result is not None:
            heat_pct = round(getattr(heat_result, "total_heat_pct", 0.0) * 100, 2)
            heat_passed = getattr(heat_result, "passed", True)

        warnings: list[str] = []
        if not heat_passed:
            warnings.append(f"Portfolio heat {heat_pct}% exceeds threshold")
        for pc in rejected:
            if pc.rejection_reason:
                warnings.append(f"{pc.gap.instrument_id}: {pc.rejection_reason}")

        return {
            "portfolio_heat_pct": heat_pct,
            "heat_passed": heat_passed,
            "risk_checks_passed": risk_passed,
            "risk_checks_total": risk_total,
            "warnings": warnings[:5],  # cap at 5
        }

    @staticmethod
    def _build_regime_state(
        regime_prediction: Any | None,
    ) -> dict[str, Any]:
        """Build the regime-state section."""
        if regime_prediction is None:
            return {
                "regime": "unknown",
                "confidence": "N/A",
                "stable": "N/A",
                "model_version": "",
            }

        regime_val = getattr(regime_prediction, "regime", "unknown")
        # Convert Regime enum to string if necessary
        if hasattr(regime_val, "value"):
            regime_val = regime_val.value

        return {
            "regime": regime_val,
            "confidence": getattr(regime_prediction, "confidence", 0.0),
            "stable": getattr(regime_prediction, "is_stable", False),
            "model_version": getattr(regime_prediction, "model_version", ""),
        }

    @staticmethod
    def _build_audit_trail(
        audit_trail: list[Any],
    ) -> list[dict[str, Any]]:
        """Convert stage records to plain dicts."""
        result: list[dict[str, Any]] = []
        for record in audit_trail:
            result.append(
                {
                    "stage": getattr(record, "stage", ""),
                    "timestamp": getattr(record, "timestamp", ""),
                    "input_count": getattr(record, "input_count", 0),
                    "output_count": getattr(record, "output_count", 0),
                    "errors": getattr(record, "errors", []),
                    "notes": getattr(record, "notes", ""),
                }
            )
        return result

    def _build_data_pipeline_health(
        self, pipeline_result: PipelineResult
    ) -> dict[str, Any]:
        """Query Redis for venue connection state and bar flow health.

        Returns a dict with:
        - venue_connection_state: {FUTU: UP/DOWN/UNKNOWN, IB: UP/DOWN/UNKNOWN}
        - bar_flow: list of per-instrument bar status dicts
        - subscription_health: {active, expected}
        - data_pipeline_passed: bool
        - warnings: list of human-readable warnings
        """
        now = datetime.now(timezone.utc)
        date_str = now.date().isoformat()
        redis = self._redis

        # Venue connection state
        venue_state: dict[str, str] = {}
        for venue in ("FUTU", "IB"):
            if redis is not None:
                try:
                    raw = redis.get(f"sam:venue:conn:{venue}")
                    if raw:
                        venue_state[venue] = raw.split(":")[0]
                    else:
                        venue_state[venue] = "UNKNOWN"
                except Exception:
                    venue_state[venue] = "UNKNOWN"
            else:
                venue_state[venue] = "UNKNOWN"

        # Expected instruments from pipeline candidates
        instrument_ids = sorted(
            {
                str(pc.gap.instrument_id)
                for pc in (pipeline_result.approved + pipeline_result.rejected)
            }
        )

        bar_flow: list[dict[str, Any]] = []
        active_subs = 0
        all_fresh = True

        for inst_id in instrument_ids:
            last_bar_time: str | None = None
            bars_today = 0
            status = "MISSING"

            if redis is not None:
                try:
                    bar_raw = redis.get(f"sam:bars:last:{inst_id}")
                    if bar_raw:
                        last_ts = datetime.fromisoformat(bar_raw)
                        age_seconds = int((now - last_ts).total_seconds())
                        last_bar_time = last_ts.isoformat()
                        if age_seconds > self.bar_stale_threshold_seconds:
                            status = "STALE"
                            all_fresh = False
                        else:
                            status = "OK"
                            active_subs += 1
                    else:
                        all_fresh = False

                    count_raw = redis.hget(f"sam:bars:count:{date_str}", inst_id)
                    if count_raw:
                        bars_today = int(count_raw)
                except Exception:
                    status = "ERROR"
                    all_fresh = False
            else:
                all_fresh = False

            bar_flow.append(
                {
                    "instrument_id": inst_id,
                    "bar_type": None,
                    "last_bar_time": last_bar_time,
                    "bars_today": bars_today,
                    "status": status,
                }
            )

        futu_expected = os.getenv("FUTU_ENABLED", "false").lower() == "true"
        ib_expected = os.getenv("IB_ENABLED", "false").lower() == "true"

        venue_issues: list[str] = []
        if futu_expected and venue_state.get("FUTU") == "DOWN":
            venue_issues.append("FUTU venue connection is DOWN")
            all_fresh = False
        if ib_expected and venue_state.get("IB") == "DOWN":
            venue_issues.append("IB venue connection is DOWN")
            all_fresh = False

        # With no candidates, pipeline passes unless an expected venue is DOWN.
        data_pipeline_passed = all_fresh
        if len(instrument_ids) == 0:
            data_pipeline_passed = True
            if futu_expected and venue_state.get("FUTU") == "DOWN":
                data_pipeline_passed = False
            if ib_expected and venue_state.get("IB") == "DOWN":
                data_pipeline_passed = False

        warnings: list[str] = []
        if not data_pipeline_passed:
            if venue_issues:
                warnings.extend(venue_issues)
            stale_or_missing = [
                b["instrument_id"]
                for b in bar_flow
                if b["status"] in ("STALE", "MISSING", "ERROR")
            ]
            if stale_or_missing:
                warnings.append(
                    f"Stale or missing bars for: {', '.join(stale_or_missing)}"
                )

        return {
            "venue_connection_state": venue_state,
            "bar_flow": bar_flow,
            "subscription_health": {
                "active": active_subs,
                "expected": len(instrument_ids),
            },
            "data_pipeline_passed": data_pipeline_passed,
            "warnings": warnings,
        }

    @staticmethod
    def _report_to_dict(report: ReadinessReport) -> dict[str, Any]:
        """Convert a frozen dataclass to a plain dict for JSON serialization."""
        return {
            "scan_timestamp": report.scan_timestamp,
            "market": report.market,
            "candidate_count": report.candidate_count,
            "approved_count": report.approved_count,
            "rejected_count": report.rejected_count,
            "top_recommendations": report.top_recommendations,
            "risk_summary": report.risk_summary,
            "regime_state": report.regime_state,
            "bundles_generated": report.bundles_generated,
            "bundle_path": report.bundle_path,
            "audit_trail": report.audit_trail,
            "trace_id": report.trace_id,
            "data_pipeline": report.data_pipeline,
            "holiday_skipped": report.holiday_skipped,
            "holiday_name": report.holiday_name,
            "next_trading_day": report.next_trading_day,
        }

    def _webhook_payload(self, report: ReadinessReport) -> dict[str, Any]:
        """Build a JSON payload appropriate for the webhook target."""
        url = self.webhook_url or ""
        dp_passed = report.data_pipeline.get("data_pipeline_passed", True)
        dp_warnings = report.data_pipeline.get("warnings", [])

        # Holiday handling
        if report.holiday_skipped:
            holiday_msg = (
                f"TODAY IS A MARKET HOLIDAY: {report.holiday_name} "
                f"({report.market}) — No trading session"
            )
            if report.next_trading_day:
                holiday_msg += f"\nNext Trading Day: {report.next_trading_day}"

            if "slack.com" in url or "hooks.slack" in url:
                return {
                    "text": (
                        "*SAM Trader V3 — Pre-Market Readiness Report*\n"
                        f":warning: *{holiday_msg}*"
                    ),
                }
            if "telegram" in url:
                return {
                    "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
                    "text": (
                        "<b>SAM Trader V3 — Readiness Report</b>\n"
                        f"⚠️ <b>{holiday_msg}</b>"
                    ),
                    "parse_mode": "HTML",
                }
            payload = self._report_to_dict(report)
            payload["holiday_alert"] = holiday_msg
            return payload

        # Slack formatting
        if "slack.com" in url or "hooks.slack" in url:
            lines = [
                "*SAM Trader V3 — Pre-Market Readiness Report*",
            ]
            if not dp_passed:
                lines.insert(1, ":warning: *DATA PIPELINE ISSUE DETECTED*")
                for w in dp_warnings:
                    lines.append(f"• Warning: {w}")
            lines.extend(
                [
                    f"• Market: {report.market}",
                    (
                        f"• Candidates: {report.candidate_count} "
                        f"(approved {report.approved_count}, "
                        f"rejected {report.rejected_count})"
                    ),
                    (
                        f"• Regime: {report.regime_state.get('regime', 'N/A')} "
                        f"(confidence: {report.regime_state.get('confidence', 'N/A')})"
                    ),
                    f"• Bundles: {report.bundles_generated}",
                ]
            )
            if report.top_recommendations:
                lines.append("*Top Recommendations:*")
                for rec in report.top_recommendations[:3]:
                    lines.append(
                        f"  • {rec['symbol']} — {rec['grade']} "
                        f"(score {rec['score']}, size {rec['size']})"
                    )
            return {"text": "\n".join(lines)}

        # Telegram formatting
        if "telegram" in url:
            lines = [
                "<b>SAM Trader V3 — Readiness Report</b>",
            ]
            if not dp_passed:
                lines.insert(1, "⚠️ <b>DATA PIPELINE ISSUE</b>")
                for w in dp_warnings:
                    lines.append(f"⚠️ {w}")
            lines.extend(
                [
                    f"Market: {report.market}",
                    (
                        f"Candidates: {report.candidate_count} "
                        f"ok {report.approved_count} "
                        f"no {report.rejected_count}"
                    ),
                    f"Regime: {report.regime_state.get('regime', 'N/A')}",
                    f"Bundles: {report.bundles_generated}",
                ]
            )
            return {
                "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
                "text": "\n".join(lines),
                "parse_mode": "HTML",
            }

        # Generic
        return self._report_to_dict(report)
