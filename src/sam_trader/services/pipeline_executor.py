"""Pipeline sequential executor — orchestrates pre-market pipeline stages.

Runs candidates through scan → AI scoring → position sizing → risk checks
→ heat monitor, merges with regime detection, and produces approved
candidates for bundle generation.

Usage
-----
    from sam_trader.services.pipeline_executor import (
        PipelineExecutor,
        PipelineExecutorConfig,
        PipelineResult,
    )

    executor = PipelineExecutor(config=PipelineExecutorConfig())
    result = executor.run(
        candidates=gap_candidates,
        portfolio_states={"FUTU": portfolio_state},
        regime_bars=historical_bars,
    )
    # result.approved -> list of PipelineCandidate with full metadata
    # result.audit_trail -> list of stage records with timestamps
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from sam_trader.services.ai_scoring import (
    AIRecommendation,
    AIScoringConfig,
    AIScoringEngine,
    Grade,
)
from sam_trader.services.gap_scanner import GapCandidate
from sam_trader.services.heat_monitor import (
    HeatMapEntry,
    HeatMonitorConfig,
    HeatMonitorResult,
    PortfolioHeatMonitor,
    ProposedPosition,
)
from sam_trader.services.regime_detection import (
    HMMRegimeClassifier,
    Regime,
    RegimeAdapter,
    RegimePrediction,
)
from sam_trader.services.risk_checks import (
    PortfolioState,
    PreTradeRiskChecker,
    RiskCheckResult,
    VenueRiskLimits,
)
from sam_trader.services.risk_sizing import (
    MonteCarloPositionSizer,
    PositionSizeResult,
    SizerConfig,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineStageRecord:
    """Single entry in the pipeline audit trail."""

    stage: str
    timestamp: str
    input_count: int
    output_count: int
    errors: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class PipelineCandidate:
    """A candidate that has progressed through pipeline stages.

    Each field accumulates metadata from the stage that produced it.
    A value of *None* means that stage has not yet run (or failed).
    """

    gap: GapCandidate
    recommendation: AIRecommendation | None = None
    position_size: PositionSizeResult | None = None
    risk_check: RiskCheckResult | None = None
    heat_entry: HeatMapEntry | None = None
    approved: bool = False
    rejection_reason: str = ""


@dataclass(frozen=True)
class PipelineExecutorConfig:
    """Configuration for the pipeline executor."""

    # Stage toggles
    enable_ai_scoring: bool = True
    enable_position_sizing: bool = True
    enable_risk_checks: bool = True
    enable_heat_monitor: bool = True
    enable_regime_detection: bool = True

    # AI scoring filter — grades below this are rejected
    min_grade: Grade = Grade.HOLD

    # Capital / sizing defaults
    capital_per_venue: dict[str, float] = field(
        default_factory=lambda: {"FUTU": 100_000.0}
    )
    risk_per_trade_pct: float = 0.01  # 1 % of capital
    stop_loss_pct: float = 0.02
    daily_volatility: float = 0.015

    # Heat monitor
    heat_nav: float = 1_000_000.0
    heat_threshold_pct: float = 0.05
    max_symbol_concentration_pct: float = 0.10
    max_sector_concentration_pct: float = 0.25

    # Risk limits per venue
    venue_risk_limits: dict[str, VenueRiskLimits] = field(default_factory=dict)

    # Regime
    regime_venue: str = "US"

    # Inner component configs (optional overrides)
    ai_scoring_config: AIScoringConfig | None = None
    sizer_config: SizerConfig | None = None


@dataclass(frozen=True)
class PipelineResult:
    """Final output of a pipeline run."""

    approved: list[PipelineCandidate]
    rejected: list[PipelineCandidate]
    heat_result: HeatMonitorResult | None = None
    regime_prediction: RegimePrediction | None = None
    audit_trail: list[PipelineStageRecord] = field(default_factory=list)
    trace_id: str = ""


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class PipelineExecutor:
    """Orchestrate pre-market pipeline stages sequentially.

    Parameters
    ----------
    config : PipelineExecutorConfig | None
        Top-level configuration.  If *None*, permissive defaults are used.
    """

    def __init__(self, config: PipelineExecutorConfig | None = None) -> None:
        self.config = config or PipelineExecutorConfig()
        self._ai = AIScoringEngine(config=self.config.ai_scoring_config)
        self._sizer = MonteCarloPositionSizer(config=self.config.sizer_config)
        self._risk = PreTradeRiskChecker(limits=self.config.venue_risk_limits)
        self._heat = PortfolioHeatMonitor(
            config=HeatMonitorConfig(
                nav=self.config.heat_nav,
                heat_threshold_pct=self.config.heat_threshold_pct,
                max_symbol_concentration_pct=self.config.max_symbol_concentration_pct,
                max_sector_concentration_pct=self.config.max_sector_concentration_pct,
            )
        )
        self._regime_classifier: HMMRegimeClassifier | None = None
        self._regime_adapter = RegimeAdapter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(  # noqa: PLR0913
        self,
        candidates: list[GapCandidate],
        portfolio_states: dict[str, PortfolioState] | None = None,
        regime_bars: list[dict[str, Any]] | None = None,
        trace_id: str = "",
    ) -> PipelineResult:
        """Execute the full pipeline.

        Parameters
        ----------
        candidates
            Gap candidates from the scanner.
        portfolio_states
            Per-venue portfolio snapshots for risk checks.
        regime_bars
            Historical bars for regime classification.
        trace_id
            Optional audit trace identifier.

        Returns
        -------
        PipelineResult
        """
        tid = trace_id or self._now_iso()
        audit: list[PipelineStageRecord] = []

        # -- Stage 1: AI scoring --------------------------------------
        scored, record = self._stage_ai_scoring(candidates, tid)
        audit.append(record)

        # -- Stage 2: Position sizing ---------------------------------
        sized, record = self._stage_position_sizing(scored, tid)
        audit.append(record)

        # -- Stage 3: Pre-trade risk checks ----------------------------
        risk_checked, record = self._stage_risk_checks(
            sized, portfolio_states or {}, tid
        )
        audit.append(record)

        # -- Stage 4: Heat monitor -------------------------------------
        heat_result, record = self._stage_heat_monitor(risk_checked, tid)
        audit.append(record)

        # -- Stage 5: Regime detection (parallel track) ---------------
        regime_pred, record = self._stage_regime_detection(regime_bars, tid)
        audit.append(record)

        # -- Stage 6: Merge & final approval ---------------------------
        approved, rejected = self._stage_merge(
            risk_checked, heat_result, regime_pred, tid
        )
        audit.append(
            PipelineStageRecord(
                stage="merge",
                timestamp=self._now_iso(),
                input_count=len(risk_checked),
                output_count=len(approved),
                notes=(
                    f"regime={regime_pred.regime.value if regime_pred else 'skipped'}"
                ),
            )
        )

        logger.info(
            "Pipeline %s complete: %d approved, %d rejected, %d stages",
            tid,
            len(approved),
            len(rejected),
            len(audit),
        )

        return PipelineResult(
            approved=approved,
            rejected=rejected,
            heat_result=heat_result,
            regime_prediction=regime_pred,
            audit_trail=audit,
            trace_id=tid,
        )

    # ------------------------------------------------------------------
    # Individual stages
    # ------------------------------------------------------------------

    def _stage_ai_scoring(
        self, candidates: list[GapCandidate], trace_id: str
    ) -> tuple[list[PipelineCandidate], PipelineStageRecord]:
        """Score each candidate; filter out SKIP grades."""
        if not self.config.enable_ai_scoring:
            pcs = [PipelineCandidate(gap=c) for c in candidates]
            return pcs, PipelineStageRecord(
                stage="ai_scoring",
                timestamp=self._now_iso(),
                input_count=len(candidates),
                output_count=len(pcs),
                notes="disabled",
            )

        out: list[PipelineCandidate] = []
        errors: list[str] = []
        for cand in candidates:
            try:
                rec = self._ai.score(cand, trace_id=trace_id)
                if rec.grade == Grade.SKIP:
                    continue
                if self._grade_rank(rec.grade) < self._grade_rank(
                    self.config.min_grade
                ):
                    continue
                out.append(PipelineCandidate(gap=cand, recommendation=rec))
            except Exception as exc:  # pragma: no cover  # fail-fast: log & skip
                msg = f"AI scoring failed for {cand.instrument_id}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        return out, PipelineStageRecord(
            stage="ai_scoring",
            timestamp=self._now_iso(),
            input_count=len(candidates),
            output_count=len(out),
            errors=errors,
        )

    def _stage_position_sizing(
        self, candidates: list[PipelineCandidate], trace_id: str
    ) -> tuple[list[PipelineCandidate], PipelineStageRecord]:
        """Run Monte-Carlo sizer on each candidate."""
        if not self.config.enable_position_sizing:
            return candidates, PipelineStageRecord(
                stage="position_sizing",
                timestamp=self._now_iso(),
                input_count=len(candidates),
                output_count=len(candidates),
                notes="disabled",
            )

        out: list[PipelineCandidate] = []
        errors: list[str] = []
        for pc in candidates:
            rec = pc.recommendation
            entry = rec.trade_params.entry if rec else pc.gap.quote_last
            stop = (
                rec.trade_params.stop
                if rec
                else entry * (1 - self.config.stop_loss_pct)
            )
            venue = self._infer_venue(pc.gap.instrument_id)
            capital = self.config.capital_per_venue.get(venue, 100_000.0)
            risk_per_trade = capital * self.config.risk_per_trade_pct
            stop_loss_pct = (
                abs(entry - stop) / entry if entry > 0 else self.config.stop_loss_pct
            )

            try:
                size_result = self._sizer.size(
                    capital=capital,
                    risk_per_trade=risk_per_trade,
                    stop_loss_pct=stop_loss_pct,
                    daily_volatility=self.config.daily_volatility,
                    entry_price=entry,
                )
                out.append(replace(pc, position_size=size_result))
            except Exception as exc:
                msg = f"Position sizing failed for {pc.gap.instrument_id}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        return out, PipelineStageRecord(
            stage="position_sizing",
            timestamp=self._now_iso(),
            input_count=len(candidates),
            output_count=len(out),
            errors=errors,
        )

    def _stage_risk_checks(
        self,
        candidates: list[PipelineCandidate],
        portfolio_states: dict[str, PortfolioState],
        trace_id: str,
    ) -> tuple[list[PipelineCandidate], PipelineStageRecord]:
        """Run per-candidate risk checks."""
        if not self.config.enable_risk_checks:
            return candidates, PipelineStageRecord(
                stage="risk_checks",
                timestamp=self._now_iso(),
                input_count=len(candidates),
                output_count=len(candidates),
                notes="disabled",
            )

        out: list[PipelineCandidate] = []
        errors: list[str] = []
        for pc in candidates:
            venue = self._infer_venue(pc.gap.instrument_id)
            portfolio = portfolio_states.get(venue) or PortfolioState(venue=venue)
            entry = (
                pc.recommendation.trade_params.entry
                if pc.recommendation
                else pc.gap.quote_last
            )
            stop = (
                pc.recommendation.trade_params.stop
                if pc.recommendation
                else entry * (1 - self.config.stop_loss_pct)
            )
            size = pc.position_size.position_size if pc.position_size else 0

            if size <= 0:
                # Cannot check zero-size; pass through as-is (will be filtered later)
                out.append(pc)
                continue

            try:
                result = self._risk.check(
                    venue=venue,
                    instrument_id=pc.gap.instrument_id,
                    position_size=size,
                    entry_price=entry,
                    stop_price=stop,
                    portfolio=portfolio,
                )
                out.append(replace(pc, risk_check=result))
            except Exception as exc:
                msg = f"Risk check failed for {pc.gap.instrument_id}: {exc}"
                logger.warning(msg)
                errors.append(msg)

        return out, PipelineStageRecord(
            stage="risk_checks",
            timestamp=self._now_iso(),
            input_count=len(candidates),
            output_count=len(out),
            errors=errors,
        )

    def _stage_heat_monitor(
        self, candidates: list[PipelineCandidate], trace_id: str
    ) -> tuple[HeatMonitorResult | None, PipelineStageRecord]:
        """Compute portfolio heat across all candidates that passed risk."""
        if not self.config.enable_heat_monitor:
            return None, PipelineStageRecord(
                stage="heat_monitor",
                timestamp=self._now_iso(),
                input_count=len(candidates),
                output_count=0,
                notes="disabled",
            )

        positions: list[ProposedPosition] = []
        for pc in candidates:
            notional = 0.0
            risk = 0.0
            if pc.position_size and pc.recommendation:
                notional = (
                    pc.position_size.position_size
                    * pc.recommendation.trade_params.entry
                )
                risk = pc.position_size.max_risk_dollars
            elif pc.position_size:
                notional = pc.position_size.position_size * pc.gap.quote_last
                risk = pc.position_size.max_risk_dollars
            positions.append(
                ProposedPosition(
                    instrument_id=pc.gap.instrument_id,
                    sector="",  # sector unknown at this layer
                    notional=notional,
                    estimated_risk=risk,
                    venue=self._infer_venue(pc.gap.instrument_id),
                )
            )

        try:
            result = self._heat.compute(positions)
        except Exception as exc:
            logger.warning("Heat monitor failed: %s", exc)
            result = None

        return result, PipelineStageRecord(
            stage="heat_monitor",
            timestamp=self._now_iso(),
            input_count=len(candidates),
            output_count=len(positions),
            notes=f"heat_pct={result.total_heat_pct if result else 'N/A'}",
        )

    def _stage_regime_detection(
        self, bars: list[dict[str, Any]] | None, trace_id: str
    ) -> tuple[RegimePrediction | None, PipelineStageRecord]:
        """Classify market regime from historical bars."""
        if not self.config.enable_regime_detection or not bars:
            return None, PipelineStageRecord(
                stage="regime_detection",
                timestamp=self._now_iso(),
                input_count=len(bars) if bars else 0,
                output_count=0,
                notes="disabled or no bars",
            )

        try:
            clf = HMMRegimeClassifier(venue=self.config.regime_venue)
            clf.fit(bars)
            pred = clf.predict(bars)
        except Exception as exc:
            logger.warning("Regime detection failed: %s", exc)
            pred = RegimePrediction(
                regime=Regime.UNKNOWN,
                confidence=0.0,
                model_version="fallback",
            )

        return pred, PipelineStageRecord(
            stage="regime_detection",
            timestamp=self._now_iso(),
            input_count=len(bars),
            output_count=1,
            notes=f"regime={pred.regime.value} confidence={pred.confidence:.2f}",
        )

    def _stage_merge(
        self,
        candidates: list[PipelineCandidate],
        heat_result: HeatMonitorResult | None,
        regime_pred: RegimePrediction | None,
        trace_id: str,
    ) -> tuple[list[PipelineCandidate], list[PipelineCandidate]]:
        """Final approval: risk check must pass, heat must pass."""
        approved: list[PipelineCandidate] = []
        rejected: list[PipelineCandidate] = []

        # Apply regime sizing multiplier if available
        regime_multiplier = 1.0
        if regime_pred:
            regime_multiplier, _ = self._regime_adapter.adjust_size(
                base_size=1.0, prediction=regime_pred
            )

        for pc in candidates:
            reasons: list[str] = []

            # Must have passed risk check (or risk checks disabled)
            if pc.risk_check is not None and not pc.risk_check.passed:
                reasons.extend(pc.risk_check.rejected_reasons)

            # Must have non-zero position size
            if pc.position_size is not None and pc.position_size.position_size <= 0:
                reasons.append("Zero position size after sizing")

            # Heat check (global)
            if heat_result is not None and not heat_result.passed:
                # Only reject if this specific candidate contributes to heat
                entry = heat_result.heat_map.get(pc.gap.instrument_id)
                if entry and entry.warning:
                    reasons.append(entry.warning)

            if reasons:
                rejected.append(
                    replace(pc, approved=False, rejection_reason="; ".join(reasons))
                )
            else:
                # Regime-aware adjustment: reduce size if regime is bearish/volatile
                if pc.position_size and regime_multiplier != 1.0:
                    adjusted_size = max(
                        1, int(pc.position_size.position_size * regime_multiplier)
                    )
                    adjusted_risk = (
                        adjusted_size
                        * pc.position_size.max_risk_dollars
                        / max(1, pc.position_size.position_size)
                    )
                    adjusted_var = (
                        adjusted_size
                        * pc.position_size.var_95
                        / max(1, pc.position_size.position_size)
                    )
                    new_size = replace(
                        pc.position_size,
                        position_size=adjusted_size,
                        max_risk_dollars=round(adjusted_risk, 2),
                        var_95=round(adjusted_var, 2),
                    )
                    pc = replace(pc, position_size=new_size)
                approved.append(replace(pc, approved=True))

        return approved, rejected

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _infer_venue(instrument_id: str) -> str:
        """Infer venue from instrument_id suffix."""
        if ".HKEX" in instrument_id:
            return "FUTU"  # HK goes through Futu
        return "FUTU"

    @staticmethod
    def _grade_rank(grade: Grade) -> int:
        """Numeric rank for grade comparison (higher = more bullish)."""
        return {
            Grade.STRONG_BUY: 4,
            Grade.BUY: 3,
            Grade.HOLD: 2,
            Grade.SKIP: 1,
        }.get(grade, 0)
