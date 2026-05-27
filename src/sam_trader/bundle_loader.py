"""Bundle loader — YAML bundles → list[ImportableStrategyConfig]."""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.adapters.futu.common import instrument_id_to_futu_security

logger = logging.getLogger(__name__)

VALID_VENUES = {"FUTU", "IB"}


class BundleLoaderError(Exception):
    """Raised when bundle loading fails."""


class BundleValidationError(Exception):
    """Raised when bundle validation fails."""


def _derive_config_path(strategy_path: str) -> str:
    """Derive config class path from strategy class path.

    ``sam_trader.strategies.orb:OrbStrategy`` →
    ``sam_trader.strategies.orb:OrbStrategyConfig``

    Parameters
    ----------
    strategy_path : str
        Fully-qualified strategy class path.

    Returns
    -------
    str
        Fully-qualified config class path.

    """
    module, class_name = strategy_path.split(":", 1)
    return f"{module}:{class_name}Config"


def _nautilus_to_futu_code(instrument_id: str) -> str:
    """Convert Nautilus instrument_id to Futu security code.

    ``TSLA.NASDAQ`` → ``US.TSLA``
    ``00700.HKEX`` → ``HK.00700``

    Parameters
    ----------
    instrument_id : str
        Nautilus instrument identifier string.

    Returns
    -------
    str
        Futu security code string.

    """
    iid = InstrumentId.from_str(instrument_id)
    return instrument_id_to_futu_security(iid)


def _load_bundle(bundle: dict[str, Any]) -> ImportableStrategyConfig:
    """Convert a single bundle dict to an ImportableStrategyConfig.

    Parameters
    ----------
    bundle : dict[str, Any]
        Raw bundle mapping from YAML.

    Returns
    -------
    ImportableStrategyConfig
        Nautilus strategy configuration.

    Raises
    ------
    BundleValidationError
        If the bundle structure or venue is invalid.

    """
    venue = bundle.get("venue", "IB")
    if venue not in VALID_VENUES:
        raise BundleValidationError(f"Unknown venue: {venue}")

    strategy = bundle.get("strategy", {})
    strategy_path = strategy.get("path")
    if not strategy_path:
        raise BundleValidationError("Bundle missing strategy.path")

    config_path = strategy.get("config_path") or _derive_config_path(strategy_path)
    config: dict[str, Any] = dict(strategy.get("config", {}))

    # Merge bracket params into strategy config
    for key, value in bundle.get("bracket", {}).items():
        config.setdefault(key, value)

    # Merge risk params into strategy config
    for key, value in bundle.get("risk", {}).items():
        config.setdefault(key, value)

    # Futu-specific symbology mapping
    if venue == "FUTU":
        instrument_id = config.get("instrument_id")
        if instrument_id and isinstance(instrument_id, str):
            try:
                config["futu_code"] = _nautilus_to_futu_code(instrument_id)
            except ValueError as exc:
                raise BundleValidationError(
                    f"Invalid instrument_id for Futu: {instrument_id}"
                ) from exc

    # IB-specific: default exchange to SMART to prevent direct-routing fees
    # (v2 post-mortem: 52 code-10311 warnings from direct NASDAQ routing)
    if venue == "IB":
        config.setdefault("exchange", "SMART")

    # Extract market field (default "US" for backward compat)
    market = bundle.get("market", "US")
    config.setdefault("market", market)

    # Ensure venue is available to the strategy for routing decisions
    config.setdefault("venue", venue)

    # Preserve bundle ID so permission checks and logs can reference it
    config.setdefault("bundle_id", bundle.get("id", "unknown"))

    # Pass through optional metadata fields so strategies can access them
    for meta_key in ("family", "version", "variant"):
        value = bundle.get(meta_key)
        if value is not None:
            config.setdefault(meta_key, value)

    return ImportableStrategyConfig(
        strategy_path=strategy_path,
        config_path=config_path,
        config=config,
    )


def load_bundles(path: str | os.PathLike[str]) -> list[ImportableStrategyConfig]:
    """Load strategy bundles from a YAML file.

    Parameters
    ----------
    path : str | os.PathLike[str]
        Path to the bundles YAML file.

    Returns
    -------
    list[ImportableStrategyConfig]
        List of strategy configs.

    Raises
    ------
    BundleLoaderError
        If the file does not exist or cannot be read.
    BundleValidationError
        If the file content is invalid.

    """
    path_str = os.fspath(path)
    if not os.path.exists(path_str):
        raise BundleLoaderError(f"Bundles file not found: {path_str}")

    try:
        with open(path_str, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise BundleLoaderError(f"Failed to parse YAML: {exc}") from exc

    if raw is None:
        return []

    if not isinstance(raw, dict):
        raise BundleValidationError("Bundles file must contain a mapping")

    bundles = raw.get("bundles", [])
    if not isinstance(bundles, list):
        raise BundleValidationError("'bundles' must be a list")

    result: list[ImportableStrategyConfig] = []
    seen_ids: set[str] = set()
    for bundle in bundles:
        if not isinstance(bundle, dict):
            raise BundleValidationError("Each bundle must be a mapping")

        bundle_id = bundle.get("id", "unknown")
        if not bundle.get("enabled", True):
            logger.info(
                "Skipping disabled bundle: %s",
                bundle_id,
            )
            continue

        if bundle_id in seen_ids:
            raise BundleValidationError(f"Duplicate bundle id: {bundle_id!r}")
        seen_ids.add(bundle_id)

        try:
            result.append(_load_bundle(bundle))
        except (BundleValidationError, ValueError) as exc:
            raise BundleValidationError(f"Bundle {bundle_id!r}: {exc}") from exc

    return result
