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
from zoneinfo import ZoneInfo

from sam_trader.services.bundle_generator import (
    generate_bundles,
    publish_bundles_to_redis,
)
from sam_trader.services.gap_scanner import (
    CompositePrevCloseLoader,
    FutuKLinePrevCloseLoader,
    GapScannerConfig,
    PGFillPrevCloseLoader,
    PreMarketGapScanner,
)
from sam_trader.services.market_calendar import MarketCalendarService
from sam_trader.services.pipeline_executor import (
    PipelineExecutor,
    PipelineExecutorConfig,
    PipelineResult,
)
from sam_trader.services.quote import _redis_client
from sam_trader.services.quote_collector import QuoteCollectionService
from sam_trader.services.readiness_report import ReadinessReportGenerator
from sam_trader.services.watchlist import build_watchlist, load_watchlist_config

PIPELINE_SCHEDULE: str = os.getenv("PIPELINE_SCHEDULE", "08:30")

logger = logging.getLogger("sam_trader.pipeline")


def _get_active_market() -> str:
    """Read active market from ``MARKET`` env var, default to ``US``."""
    return os.getenv("MARKET", "US").strip().upper() or "US"


def _get_pipeline_schedule(market: str) -> str:
    """Return the pipeline schedule time for *market* (market-local HH:MM).

    Reads ``premarket_pipeline_time`` from ``config/market_config.yaml``.
    Falls back to ``08:30`` for US and ``07:30`` for HK.
    """
    try:
        from sam_trader.market_config import MarketConfig

        cfg = MarketConfig.get_market(market)
        return cfg.premarket_pipeline_time
    except Exception:  # noqa: BLE001
        return "08:30" if market == "US" else "07:30"


def _convert_pipeline_time_to_hkt(market: str, local_time: str) -> str:
    """Convert a market-local pipeline time to HKT (HH:MM).

    For ``US`` the *local_time* is interpreted as America/New_York and
    converted to Asia/Hong_Kong using ``zoneinfo``, so DST is handled
    automatically.

    For ``HK`` the time is already HKT and returned unchanged.
    """
    if market != "US":
        return local_time

    hour, minute = map(int, local_time.split(":"))
    now = datetime.now(timezone.utc)
    et_tz = ZoneInfo("America/New_York")
    hkt_tz = ZoneInfo("Asia/Hong_Kong")
    et_now = now.astimezone(et_tz)
    et_dt = et_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    hkt_dt = et_dt.astimezone(hkt_tz)
    return f"{hkt_dt.hour:02d}:{hkt_dt.minute:02d}"


def run_pipeline(
    market: str | None = None,
    schedule: str | None = None,
    pass_number: int = 1,
) -> dict[str, Any]:
    """Run the full pre-market pipeline.

    Parameters
    ----------
    market
        Market to scan (``"US"`` or ``"HK"``).  Defaults to ``MARKET`` env var.
    schedule
        Schedule label (HH:MM).  Defaults to market config
        (``premarket_pipeline_time``).
    pass_number
        Scan pass (1=early gap, 2=trended, 3+=final).  Default 1.

    Returns
    -------
    dict[str, Any]
        Result payload with ``status``, counts, ``bundles_published``, etc.
    """
    market = (market or _get_active_market()).upper()
    schedule = schedule or _get_pipeline_schedule(market)
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
            "bundles_published": 0,
            "bundle_path": None,
            "note": msg,
        }

    # 2. Market holiday check
    calendar = MarketCalendarService.from_env()
    today = datetime.now(timezone.utc).date()
    if not calendar.is_trading_day(market, today):
        holiday_name = calendar.holiday_name(market, today)
        if holiday_name is None:
            holiday_name = f"{market} market holiday"
        logger.info(
            "%s market holiday (%s), skipping gap scan",
            market,
            holiday_name,
        )
        pipeline_result = PipelineResult(
            approved=[],
            rejected=[],
            holiday_skipped=True,
            holiday_name=holiday_name,
            trace_id=f"pipeline-{market}-{datetime.now(timezone.utc).isoformat()}",
        )
        # Skip to readiness report
        redis_client = _redis_client()
        gen = ReadinessReportGenerator(redis_client=redis_client)
        report = gen.generate(
            pipeline_result,
            bundle_path=None,
            market=market,
        )
        try:
            gen.save_audit(report)
        except Exception as exc:
            logger.warning("Failed to save readiness audit: %s", exc)
        return {
            "command": "pipeline",
            "status": "success",
            "market": market,
            "schedule": schedule,
            "candidate_count": 0,
            "approved_count": 0,
            "rejected_count": 0,
            "bundles_generated": 0,
            "bundles_published": 0,
            "bundle_path": None,
            "regime": None,
            "trace_id": pipeline_result.trace_id,
            "holiday_skipped": True,
            "holiday_name": holiday_name,
        }

    # 3. Gap scan
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

    if not candidates:
        logger.info("0 candidates (market closed)")

    # 4. Pipeline executor
    executor = PipelineExecutor(config=PipelineExecutorConfig(regime_venue=market))
    pipeline_result = executor.run(
        candidates=candidates,
        trace_id=f"pipeline-{market}-{datetime.now(timezone.utc).isoformat()}",
    )

    # 5. Bundle generation → Redis pub/sub
    bundles_published = 0
    if pipeline_result.approved:
        try:
            bundles = generate_bundles(pipeline_result.approved)
            pub_result = publish_bundles_to_redis(bundles, market=market)
            bundles_published = pub_result.get("published", 0)
        except Exception as exc:
            logger.warning("Bundle publish failed: %s", exc)

    # 6. Readiness report
    redis_client = _redis_client()
    gen = ReadinessReportGenerator(redis_client=redis_client)
    report = gen.generate(
        pipeline_result,
        bundle_path=None,
        market=market,
    )

    try:
        gen.save_audit(report)
    except Exception as exc:
        logger.warning("Failed to save readiness audit: %s", exc)

    logger.info(
        "Pipeline complete: %d candidates, %d approved, %d rejected, "
        "bundles_published=%d",
        report.candidate_count,
        report.approved_count,
        report.rejected_count,
        bundles_published,
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
        "bundles_published": bundles_published,
        "bundle_path": None,
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
        default=None,
        help="Expected schedule time (HH:MM).  Defaults to market config.",
    )
    parser.add_argument(
        "--market",
        default=None,
        help="Market to scan (US or HK).  Defaults to MARKET env var.",
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
