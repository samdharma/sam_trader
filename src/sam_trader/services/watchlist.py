"""Pre-market watchlist service — config-driven symbol universe per market."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import yaml

from sam_trader.bundle_loader import load_bundles

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_PATH = "config/premarket_watchlist.yaml"
DEFAULT_BUNDLES_PATH = "config/bundles.yaml"

# Exchange suffixes that indicate US pre-market eligibility
_PREMARKET_ELIGIBLE_SUFFIXES = {
    ".NASDAQ",
    ".NYSE",
    ".AMEX",
    ".ARCA",
    ".BATS",
}

# Mapping from exchange suffix to market label
_MARKET_SUFFIX_MAP = {
    ".NASDAQ": "US",
    ".NYSE": "US",
    ".AMEX": "US",
    ".ARCA": "US",
    ".BATS": "US",
    ".HKEX": "HK",
}


@dataclass(frozen=True)
class MarketWatchlist:
    """Per-market watchlist configuration."""

    symbols: list[str]
    min_gap_pct: float
    max_candidates: int
    premarket_only: bool = True


class WatchlistError(Exception):
    """Raised when watchlist loading or building fails."""


def load_watchlist_config(
    path: str | os.PathLike[str],
) -> dict[str, MarketWatchlist]:
    """Load watchlist configuration from YAML.

    Parameters
    ----------
    path : str | os.PathLike[str]
        Path to the watchlist YAML file.

    Returns
    -------
    dict[str, MarketWatchlist]
        Mapping of market label (``US``, ``HK``) to configuration.

    Raises
    ------
    WatchlistError
        If the file does not exist or is invalid.

    """
    path_str = os.fspath(path)
    if not os.path.exists(path_str):
        raise WatchlistError(f"Watchlist file not found: {path_str}")

    try:
        with open(path_str, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise WatchlistError(f"Failed to parse watchlist YAML: {exc}") from exc

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise WatchlistError("Watchlist file must contain a mapping")

    watchlist_section = raw.get("watchlist", {})
    if not isinstance(watchlist_section, dict):
        raise WatchlistError("'watchlist' must be a mapping")

    result: dict[str, MarketWatchlist] = {}
    for market, cfg in watchlist_section.items():
        if not isinstance(cfg, dict):
            raise WatchlistError(f"Market '{market}' config must be a mapping")

        symbols = cfg.get("symbols", [])
        if symbols is None:
            symbols = []
        if not isinstance(symbols, list):
            raise WatchlistError(f"Market '{market}' symbols must be a list")

        result[market] = MarketWatchlist(
            symbols=[str(s) for s in symbols],
            min_gap_pct=float(cfg.get("min_gap_pct", 2.0)),
            max_candidates=int(cfg.get("max_candidates", 50)),
            premarket_only=bool(cfg.get("premarket_only", True)),
        )

    return result


def build_watchlist(
    config: dict[str, MarketWatchlist],
    bundles_path: str | os.PathLike[str] | None = None,
) -> dict[str, list[str]]:
    """Build the final watchlist per market.

    Static symbols (non-empty in config) override dynamic bundle extraction.
    Results are capped to ``max_candidates`` and filtered for pre-market
    eligibility when ``premarket_only`` is ``True``.

    Parameters
    ----------
    config : dict[str, MarketWatchlist]
        Loaded watchlist configuration.
    bundles_path : str | os.PathLike[str] | None
        Path to bundles YAML for dynamic mode.  If ``None``,
        defaults to ``config/bundles.yaml``.

    Returns
    -------
    dict[str, list[str]]
        Mapping of market label to resolved symbol list.

    """
    if bundles_path is None:
        bundles_path = DEFAULT_BUNDLES_PATH

    # Collect dynamic symbols from bundles if needed
    dynamic_symbols: dict[str, list[str]] = {}
    needs_dynamic = any(not cfg.symbols for cfg in config.values())
    if needs_dynamic:
        try:
            dynamic_symbols = _extract_symbols_from_bundles(bundles_path)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to extract symbols from bundles")
            dynamic_symbols = {}

    result: dict[str, list[str]] = {}
    for market, cfg in config.items():
        if cfg.symbols:
            symbols = list(cfg.symbols)
            source = "static"
        else:
            symbols = list(dynamic_symbols.get(market, []))
            source = "dynamic"

        # Pre-market filter
        if cfg.premarket_only:
            symbols = filter_premarket(symbols)

        # Cap candidates
        symbols = symbols[: cfg.max_candidates]

        logger.info(
            "Watchlist %s: %d symbols (%s)",
            market,
            len(symbols),
            source,
        )
        result[market] = symbols

    return result


def _extract_symbols_from_bundles(
    bundles_path: str | os.PathLike[str],
) -> dict[str, list[str]]:
    """Extract instrument IDs from enabled bundles grouped by market.

    Parameters
    ----------
    bundles_path : str | os.PathLike[str]
        Path to the bundles YAML file.

    Returns
    -------
    dict[str, list[str]]
        Mapping of market label to deduplicated instrument IDs.

    """
    bundles = load_bundles(bundles_path)

    symbols_by_market: dict[str, list[str]] = {"US": [], "HK": []}
    seen: set[str] = set()

    for bundle in bundles:
        cfg = getattr(bundle, "config", {})
        instrument_id = cfg.get("instrument_id")
        if not instrument_id or instrument_id in seen:
            continue
        seen.add(instrument_id)

        market = _market_from_instrument_id(str(instrument_id))
        if market:
            symbols_by_market[market].append(str(instrument_id))

    return symbols_by_market


def _market_from_instrument_id(instrument_id: str) -> str | None:
    """Derive market label from a Nautilus instrument ID string.

    Examples
    --------
    >>> _market_from_instrument_id("TSLA.NASDAQ")
    'US'
    >>> _market_from_instrument_id("00700.HKEX")
    'HK'

    """
    for suffix, market in _MARKET_SUFFIX_MAP.items():
        if instrument_id.endswith(suffix):
            return market
    return None


def filter_premarket(symbols: list[str]) -> list[str]:
    """Return only symbols that trade in the pre-market session.

    US exchange-listed equities are considered pre-market eligible.
    HK equities are excluded because the Hong Kong market does not
    offer a pre-market session.

    Parameters
    ----------
    symbols : list[str]
        Nautilus instrument ID strings.

    Returns
    -------
    list[str]
        Filtered symbols.

    """
    result = []
    for sym in symbols:
        if any(sym.endswith(suffix) for suffix in _PREMARKET_ELIGIBLE_SUFFIXES):
            result.append(sym)
    return result


def validate_symbols(
    symbols: list[str],
    provider: Any,
) -> tuple[list[str], list[str]]:
    """Validate that each symbol can be resolved by a Futu instrument provider.

    Parameters
    ----------
    symbols : list[str]
        Nautilus instrument ID strings to validate.
    provider : Any
        A ``FutuInstrumentProvider`` instance (or compatible mock).

    Returns
    -------
    tuple[list[str], list[str]]
        (valid_symbols, invalid_symbols)

    """
    valid: list[str] = []
    invalid: list[str] = []

    for sym in symbols:
        try:
            from nautilus_trader.model.identifiers import InstrumentId

            iid = InstrumentId.from_str(sym)
            instrument = provider.find(iid)
            if instrument is not None:
                valid.append(sym)
            else:
                # Trigger async load if the provider supports it
                if hasattr(provider, "load_async"):
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None

                    if loop is not None:
                        asyncio.create_task(provider.load_async(iid))
                    else:
                        asyncio.run(provider.load_async(iid))

                # Re-check after load attempt
                instrument = provider.find(iid)
                if instrument is not None:
                    valid.append(sym)
                else:
                    invalid.append(sym)
        except Exception:  # noqa: BLE001
            invalid.append(sym)

    return valid, invalid
