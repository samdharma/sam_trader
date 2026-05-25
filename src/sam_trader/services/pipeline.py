"""Pre-market pipeline — autonomous gap scan → AI scoring → bundles → readiness report.

Entry-point for both ``sam pipeline`` CLI and cron
(``python -m sam_trader.services.pipeline``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sam_trader.services.bundle_generator import generate_bundles, write_bundles
from sam_trader.services.gap_scanner import (
    CompositePrevCloseLoader,
    FutuKLinePrevCloseLoader,
    GapScannerConfig,
    PGFillPrevCloseLoader,
    PreMarketGapScanner,
)
from sam_trader.services.pipeline_executor import (
    PipelineExecutor,
    PipelineExecutorConfig,
)
from sam_trader.services.quote import _redis_client
from sam_trader.services.quote_collector import QuoteCollectionService
from sam_trader.services.readiness_report import ReadinessReportGenerator
from sam_trader.services.watchlist import build_watchlist, load_watchlist_config

PIPELINE_SCHEDULE: str = os.getenv("PIPELINE_SCHEDULE", "08:30")
PIPELINE_MARKET: str = os.getenv("PIPELINE_MARKET", "US")

logger = logging.getLogger("sam_trader.pipeline")


def run_pipeline(
    market: str | None = None,
    schedule: str = PIPELINE_SCHEDULE,
    pass_number: int = 1,
) -> dict[str, Any]:
    """Run the full pre-market pipeline.

    Parameters
    ----------
    market
        Market to scan (``"US"`` or ``"HK"``).  Defaults to ``PIPELINE_MARKET`` env var.
    schedule
        Schedule label (HH:MM).  Defaults to ``PIPELINE_SCHEDULE`` env var.
    pass_number
        Scan pass (1=early gap, 2=trended, 3=final).  Default 1.

    Returns
    -------
    dict[str, Any]
        Result payload with ``status``, counts, ``bundle_path``, etc.
    """
    market = (market or PIPELINE_MARKET).upper()
    logger.info(
        "Pre-market pipeline starting (market=%s, schedule=%s)",
        market,
        schedule,
    )

    # 1. Load watchlist
    try:
        wl_cfg = load_watchlist_config("config/premarket_watchlist.yaml")
        universe = build_watchlist(wl_cfg)
    except Exception as exc:
        logger.error("Failed to load watchlist: %s", exc)
        return {
            "command": "pipeline",
            "status": "error",
            "market": market,
            "schedule": schedule,
            "error": f"Failed to load watchlist: {exc}",
        }

    symbols = universe.get(market, [])
    if not symbols:
        msg = f"No symbols in watchlist for market={market}"
        logger.warning(msg)
        return {
            "command": "pipeline",
            "status": "success",
            "market": market,
            "schedule": schedule,
            "candidate_count": 0,
            "approved_count": 0,
            "rejected_count": 0,
            "bundles_generated": 0,
            "bundle_path": None,
            "note": msg,
        }

    # 2. Gap scan
    market_config = wl_cfg.get(market)
    min_gap = market_config.min_gap_pct if market_config else 2.0

    scanner_cfg = GapScannerConfig(
        market=market,
        min_gap_pct=min_gap,
        collection_period_secs=30,
    )
    quote_svc = QuoteCollectionService(
        broker="FUTU",
        host=os.getenv("FUTU_OPEND_HOST", "sam-futu-opend"),
        port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        watchlist=symbols,
        collection_period_secs=scanner_cfg.collection_period_secs,
        connection_timeout_secs=scanner_cfg.connection_timeout_secs,
    )
    prev_loader = CompositePrevCloseLoader(
        [PGFillPrevCloseLoader(), FutuKLinePrevCloseLoader()]
    )
    redis = _redis_client()
    scanner = PreMarketGapScanner(
        config=scanner_cfg,
        quote_service=quote_svc,
        prev_close_loader=prev_loader,
        redis_client=redis,
    )

    try:
        candidates = asyncio.run(scanner.scan(symbols, pass_number=pass_number))
    except Exception as exc:
        logger.error("Gap scan failed: %s", exc)
        return {
            "command": "pipeline",
            "status": "error",
            "market": market,
            "schedule": schedule,
            "error": f"Gap scan failed: {exc}",
        }

    # 3. Pipeline executor
    executor = PipelineExecutor(config=PipelineExecutorConfig())
    pipeline_result = executor.run(
        candidates=candidates,
        trace_id=f"pipeline-{market}-{datetime.now(timezone.utc).isoformat()}",
    )

    # 4. Bundle generation
    bundle_path: str | None = None
    if pipeline_result.approved:
        try:
            bundles = generate_bundles(pipeline_result.approved)
            bundle_path = write_bundles(bundles)
        except Exception as exc:
            logger.warning("Bundle generation failed: %s", exc)

    # 5. Readiness report
    gen = ReadinessReportGenerator()
    report = gen.generate(
        pipeline_result,
        bundle_path=bundle_path,
        market=market,
    )

    try:
        gen.save_audit(report)
    except Exception as exc:
        logger.warning("Failed to save readiness audit: %s", exc)

    logger.info(
        "Pipeline complete: %d candidates, %d approved, %d rejected, bundles=%s",
        report.candidate_count,
        report.approved_count,
        report.rejected_count,
        bundle_path or "none",
    )

    return {
        "command": "pipeline",
        "status": "success",
        "market": market,
        "schedule": schedule,
        "candidate_count": report.candidate_count,
        "approved_count": report.approved_count,
        "rejected_count": report.rejected_count,
        "bundles_generated": report.bundles_generated,
        "bundle_path": bundle_path,
        "regime": report.regime_state.get("regime"),
        "trace_id": report.trace_id,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="SAM Trader V3 Pre-Market Pipeline")
    parser.add_argument(
        "--schedule",
        default=PIPELINE_SCHEDULE,
        help="Expected schedule time (HH:MM)",
    )
    parser.add_argument(
        "--market",
        default=PIPELINE_MARKET,
        help="Market to scan (US or HK)",
    )
    parser.add_argument(
        "--pass",
        type=int,
        default=1,
        dest="pass_number",
        help="Scan pass number (1=early gap, 2=trended, 3=final)",
    )
    args = parser.parse_args()
    result = run_pipeline(
        market=args.market,
        schedule=args.schedule,
        pass_number=args.pass_number,
    )
    print(
        f"pipeline_status={result['status']} "
        f"market={result['market']} "
        f"schedule={result['schedule']}"
    )
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
