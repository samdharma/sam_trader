"""Bundle YAML generator — converts approved pipeline candidates to bundle YAML.

Usage
-----
    from sam_trader.services.bundle_generator import (
        BundleGenerator,
        BundleGeneratorConfig,
        generate_bundles,
        write_bundles,
        publish_bundles_to_redis,
    )

    generator = BundleGenerator(BundleGeneratorConfig())
    path = generator.run(approved_candidates)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml

from sam_trader.bundle_validation import _validate_bundle_schema
from sam_trader.services.pipeline_executor import PipelineCandidate

logger = logging.getLogger(__name__)

_DEFAULT_STRATEGY_PATH = "sam_trader.strategies.orb:OrbStrategy"
_DEFAULT_BAR_AGGREGATION = "5-MINUTE-LAST-EXTERNAL"
_DEFAULT_IB_BAR_AGGREGATION = "5-MINUTE-LAST-INTERNAL"
_DEFAULT_TICK_SIZE = 0.01

# Redis channel names (mirror those in bundle_controller.py)
CHANNEL_BUNDLE_LOAD = "sam:bundle:load"
CHANNEL_BUNDLE_LOAD_COMPLETE = "sam:bundle:load_complete"


@dataclass(frozen=True)
class BundleGeneratorConfig:
    """Configuration for the bundle YAML generator."""

    output_path: str = "config/bundles.daily.yaml"
    strategy_path: str = _DEFAULT_STRATEGY_PATH
    bar_aggregation: str = _DEFAULT_BAR_AGGREGATION
    ib_bar_aggregation: str = _DEFAULT_IB_BAR_AGGREGATION
    tick_size: float = _DEFAULT_TICK_SIZE
    default_first_candle_minutes: int = 5
    default_entry_order_type: str = "MARKET"
    default_max_daily_loss: int = 1000
    default_max_position: int = 500


class BundleGeneratorError(Exception):
    """Raised when bundle generation fails."""


def _infer_venue(instrument_id: str) -> str:
    """Infer venue from instrument_id suffix.

    Parameters
    ----------
    instrument_id : str
        Nautilus instrument identifier.

    Returns
    -------
    str
        ``"FUTU"`` for all symbols (HK via Futu, US via Futu primary).

    """
    if ".HKEX" in instrument_id:
        return "FUTU"
    return "FUTU"


def _make_bar_type(
    instrument_id: str,
    venue: str,
    config: BundleGeneratorConfig,
) -> str:
    """Construct a bar type string from instrument_id and venue."""
    agg = config.ib_bar_aggregation if venue == "IB" else config.bar_aggregation
    return f"{instrument_id}-{agg}"


def _price_to_ticks(price_diff: float, tick_size: float) -> int:
    """Convert a price difference to an integer tick count.

    Parameters
    ----------
    price_diff : float
        Absolute price distance.
    tick_size : float
        Minimum price increment.

    Returns
    -------
    int
        Tick count, at least 1.

    """
    if tick_size <= 0:
        return 10
    return max(1, int(round(price_diff / tick_size)))


def _candidate_to_bundle(
    candidate: PipelineCandidate,
    config: BundleGeneratorConfig,
) -> dict[str, Any] | None:
    """Convert a single approved :class:`PipelineCandidate` to a bundle dict.

    Parameters
    ----------
    candidate : PipelineCandidate
        Approved pipeline candidate.
    config : BundleGeneratorConfig
        Generator configuration.

    Returns
    -------
    dict[str, Any] | None
        Valid bundle dict, or *None* if schema validation fails.

    """
    gap = candidate.gap
    instrument_id = gap.instrument_id
    venue = _infer_venue(instrument_id)

    bar_type = _make_bar_type(instrument_id, venue, config)

    # Trade parameters from AI recommendation, or sensible fallbacks
    rec = candidate.recommendation
    trade_params = rec.trade_params if rec is not None else None

    entry = trade_params.entry if trade_params else gap.quote_last
    stop = trade_params.stop if trade_params else entry * 0.98
    target = trade_params.target if trade_params else entry * 1.03

    stop_loss_ticks = _price_to_ticks(abs(entry - stop), config.tick_size)
    take_profit_ticks = _price_to_ticks(abs(target - entry), config.tick_size)

    # Position sizing
    if candidate.position_size is not None:
        trade_size = candidate.position_size.position_size
        max_risk = candidate.position_size.max_risk_dollars
    else:
        trade_size = config.default_max_position
        max_risk = float(config.default_max_daily_loss)

    # Bundle ID: symbol-strategy-date-venue
    symbol = instrument_id.split(".")[0].lower()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    bundle_id = f"{symbol}-orb-{today}-{venue.lower()}"

    bundle: dict[str, Any] = {
        "id": bundle_id,
        "enabled": True,
        "venue": venue,
        "strategy": {
            "path": config.strategy_path,
            "config": {
                "instrument_id": instrument_id,
                "bar_type": bar_type,
                "first_candle_minutes": config.default_first_candle_minutes,
                "trade_size": trade_size,
                "entry_order_type": config.default_entry_order_type,
            },
        },
        "bracket": {
            "stop_loss_ticks": stop_loss_ticks,
            "take_profit_ticks": take_profit_ticks,
        },
        "risk": {
            "max_position": trade_size,
            "max_daily_loss": (
                int(max_risk) if max_risk > 0 else config.default_max_daily_loss
            ),
        },
    }

    # Validate against bundle schema
    errors, _ = _validate_bundle_schema(bundle)
    if errors:
        logger.warning(
            "Bundle schema validation failed for %s: %s",
            instrument_id,
            errors,
        )
        return None

    return bundle


def generate_bundles(
    candidates: list[PipelineCandidate],
    config: BundleGeneratorConfig | None = None,
) -> list[dict[str, Any]]:
    """Convert approved candidates to valid bundle dicts.

    Only candidates with ``approved=True`` are processed.  Each bundle is
    validated against the bundle schema; invalid bundles are dropped with a
    log warning.

    Parameters
    ----------
    candidates : list[PipelineCandidate]
        Pipeline candidates (typically ``result.approved``).
    config : BundleGeneratorConfig | None
        Generator configuration.  Defaults to permissive defaults.

    Returns
    -------
    list[dict[str, Any]]
        Schema-valid bundle dicts.

    """
    cfg = config or BundleGeneratorConfig()
    bundles: list[dict[str, Any]] = []

    for candidate in candidates:
        if not getattr(candidate, "approved", False):
            continue

        bundle = _candidate_to_bundle(candidate, cfg)
        if bundle is not None:
            bundles.append(bundle)

    logger.info(
        "Generated %d bundles from %d approved candidates",
        len(bundles),
        len(candidates),
    )
    return bundles


def write_bundles(
    bundles: list[dict[str, Any]],
    output_path: str | os.PathLike[str] | None = None,
) -> str:
    """Write bundle dicts to a YAML file.

    Parameters
    ----------
    bundles
        List of bundle dicts.
    output_path
        Destination path.  Defaults to ``config/bundles.daily.yaml``.

    Returns
    -------
    str
        Absolute path of the written file.

    Raises
    ------
    BundleGeneratorError
        If the file cannot be written.

    """
    path = os.fspath(output_path or BundleGeneratorConfig().output_path)

    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    payload = {"bundles": bundles}

    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                payload,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
    except OSError as exc:
        raise BundleGeneratorError(f"Failed to write bundles to {path}: {exc}") from exc

    logger.info("Wrote %d bundles to %s", len(bundles), path)
    return os.path.abspath(path)


def publish_bundles_to_redis(
    bundles: list[dict[str, Any]],
    market: str = "",
    redis_client: Any = None,
) -> dict[str, Any]:
    """Publish bundle dicts to Redis ``sam:bundle:load`` channel.

    Each bundle is published as JSON to the load channel. After all bundles
    are sent, a completion message is published to
    ``sam:bundle:load_complete`` with ``{"market": ..., "count": ...}``.

    Parameters
    ----------
    bundles
        List of bundle dicts (from :func:`generate_bundles`).
    market
        Market label (``"US"``, ``"HK"``, etc.) for the completion message.
    redis_client
        Optional Redis client.  If *None*, a client is built from env vars.

    Returns
    -------
    dict[str, Any]
        ``{"published": int, "channel": str, "market": str, "errors": int}``.

    """
    if redis_client is None:
        from sam_trader.services.quote import _redis_client

        redis_client = _redis_client()

    if redis_client is None:
        logger.warning("publish_bundles_to_redis: Redis not available, skipping")
        return {
            "published": 0,
            "channel": CHANNEL_BUNDLE_LOAD,
            "market": market,
            "errors": 0,
        }

    published = 0
    errors = 0

    try:
        for bundle in bundles:
            try:
                redis_client.publish(CHANNEL_BUNDLE_LOAD, json.dumps(bundle))
                published += 1
            except Exception as exc:
                logger.warning(
                    "Failed to publish bundle %s: %s",
                    bundle.get("id", "?"),
                    exc,
                )
                errors += 1

        try:
            redis_client.publish(
                CHANNEL_BUNDLE_LOAD_COMPLETE,
                json.dumps({"market": market, "count": published}),
            )
        except Exception as exc:
            logger.warning("Failed to publish load_complete: %s", exc)
            errors += 1
    except Exception as exc:
        logger.warning("Redis publish failed: %s", exc)
        errors += 1

    logger.info(
        "Published %d bundles to Redis channel %s (errors=%d)",
        published,
        CHANNEL_BUNDLE_LOAD,
        errors,
    )
    return {
        "published": published,
        "channel": CHANNEL_BUNDLE_LOAD,
        "market": market,
        "errors": errors,
    }


class BundleGenerator:
    """High-level bundle generator that combines generation + writing.

    Parameters
    ----------
    config : BundleGeneratorConfig | None
        Generator configuration.

    """

    def __init__(self, config: BundleGeneratorConfig | None = None) -> None:
        self.config = config or BundleGeneratorConfig()

    def run(self, candidates: list[PipelineCandidate]) -> str:
        """Generate bundles from *candidates* and write to the configured path.

        Parameters
        ----------
        candidates : list[PipelineCandidate]
            Approved pipeline candidates.

        Returns
        -------
        str
            Absolute path of the written YAML file.

        """
        bundles = generate_bundles(candidates, self.config)
        return write_bundles(bundles, self.config.output_path)
