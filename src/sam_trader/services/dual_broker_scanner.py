"""DualBrokerGapScanner — Futu primary + IB cross-validation.

Wraps two :class:`QuoteCollectionService` instances and runs them in parallel
for the US market.  Cross-validates mid-price discrepancies and annotates gap
candidates with a ``cross_validated`` flag.

For the HK market only the primary broker (Futu) is used because IB does not
support HK equities.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import yaml

from sam_trader.services.gap_scanner import (
    GapCandidate,
    GapScannerConfig,
    PreMarketGapScanner,
)
from sam_trader.services.quote_collector import (
    QuoteCollectionResult,
    QuoteCollectionService,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DualBrokerScannerConfig:
    """Configuration for dual-broker gap scanning.

    Parameters
    ----------
    market : str
        ``"US"`` or ``"HK"``.
    primary_broker : str
        Broker code for the primary data source (``"FUTU"`` or ``"IB"``).
    secondary_broker : str | None
        Broker code for the secondary / cross-validation source.
        ``None`` disables cross-validation (used for HK).
    cross_validation_threshold_pct : float
        Discrepancy threshold in percent.  Default ``1.0``.
    collection_period_secs : int
        Seconds to collect quotes per broker.  Default ``30``.
    connection_timeout_secs : int
        Broker connection timeout.  Default ``10``.
    min_gap_pct : float
        Minimum absolute gap percent to include a candidate.  Default ``2.0``.
    max_gap_pct : float
        Maximum absolute gap percent.  Default ``20.0``.
    min_price : float
        Minimum price filter.  Default ``1.0``.
    max_price : float
        Maximum price filter.  Default ``5000.0``.
    min_volume : float | None
        Minimum volume filter (``None`` = disabled).  Default ``None``.
    blacklist : tuple[str, ...]
        Instrument IDs to exclude.  Default ``()``.
    exclude_otc : bool
        Exclude OTC instruments.  Default ``True``.
    exclude_etf : bool
        Exclude ETFs.  Default ``True``.

    """

    market: str = "US"
    primary_broker: str = "FUTU"
    secondary_broker: str | None = "IB"
    cross_validation_threshold_pct: float = 1.0
    collection_period_secs: int = 30
    connection_timeout_secs: int = 10
    min_gap_pct: float = 2.0
    max_gap_pct: float = 20.0
    min_price: float = 1.0
    max_price: float = 5000.0
    min_volume: float | None = None
    blacklist: tuple[str, ...] = ()
    exclude_otc: bool = True
    exclude_etf: bool = True


class GapScannerConfigError(Exception):
    """Raised when gap-scanner configuration loading fails."""


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_gap_scanner_config(
    path: str | os.PathLike[str] = "config/gap_scanner.yaml",
) -> dict[str, DualBrokerScannerConfig]:
    """Load dual-broker gap-scanner configuration from YAML.

    Parameters
    ----------
    path : str or os.PathLike
        Path to the gap-scanner YAML file.

    Returns
    -------
    dict[str, DualBrokerScannerConfig]
        Mapping of market label (``US``, ``HK``) to config.

    Raises
    ------
    GapScannerConfigError
        If the file does not exist or is invalid.

    """
    path_str = os.fspath(path)
    if not os.path.exists(path_str):
        raise GapScannerConfigError(f"Gap scanner config not found: {path_str}")

    try:
        with open(path_str, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise GapScannerConfigError(f"Failed to parse gap scanner YAML: {exc}") from exc

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise GapScannerConfigError("Gap scanner file must contain a mapping")

    section = raw.get("gap_scanner", {})
    if not isinstance(section, dict):
        raise GapScannerConfigError("'gap_scanner' must be a mapping")

    result: dict[str, DualBrokerScannerConfig] = {}
    for market, cfg in section.items():
        if not isinstance(cfg, dict):
            raise GapScannerConfigError(f"Market '{market}' config must be a mapping")

        # Convert list → tuple for blacklist
        blacklist = cfg.get("blacklist", ())
        if isinstance(blacklist, list):
            blacklist = tuple(blacklist)

        # Normalize null / missing secondary_broker
        secondary = cfg.get("secondary_broker", "IB")
        if secondary is None or str(secondary).lower() in ("none", "null", ""):
            secondary = None

        result[market] = DualBrokerScannerConfig(
            market=market,
            primary_broker=cfg.get("primary_broker", "FUTU"),
            secondary_broker=secondary,
            cross_validation_threshold_pct=float(
                cfg.get("cross_validation_threshold_pct", 1.0)
            ),
            collection_period_secs=int(cfg.get("collection_period_secs", 30)),
            connection_timeout_secs=int(cfg.get("connection_timeout_secs", 10)),
            min_gap_pct=float(cfg.get("min_gap_pct", 2.0)),
            max_gap_pct=float(cfg.get("max_gap_pct", 20.0)),
            min_price=float(cfg.get("min_price", 1.0)),
            max_price=float(cfg.get("max_price", 5000.0)),
            min_volume=cfg.get("min_volume"),
            blacklist=blacklist,
            exclude_otc=bool(cfg.get("exclude_otc", True)),
            exclude_etf=bool(cfg.get("exclude_etf", True)),
        )

    return result


def get_gap_scanner_config(
    market: str,
    path: str | os.PathLike[str] = "config/gap_scanner.yaml",
) -> DualBrokerScannerConfig:
    """Load gap-scanner config and return the entry for a specific market.

    Falls back to :class:`DualBrokerScannerConfig` defaults if the file or
    market entry is missing.

    """
    try:
        all_cfg = load_gap_scanner_config(path)
        if market in all_cfg:
            return all_cfg[market]
    except GapScannerConfigError as exc:
        logger.debug("Gap scanner config load failed: %s", exc)

    # Sensible defaults per market
    if market == "HK":
        return DualBrokerScannerConfig(
            market="HK",
            secondary_broker=None,
            min_gap_pct=1.5,
        )
    return DualBrokerScannerConfig(market=market)


# ---------------------------------------------------------------------------
# Dual-broker scanner
# ---------------------------------------------------------------------------


class DualBrokerGapScanner:
    """Wraps two :class:`QuoteCollectionService` instances for dual-broker scanning.

    For the **US** market both the primary and secondary brokers are queried in
    parallel via :func:`asyncio.gather`.  After collection the quote mid-prices
    are cross-validated; candidates with a discrepancy larger than
    *cross_validation_threshold_pct* are flagged.

    For the **HK** market only the primary broker is used (IB is not available
    for HK equities).

    Parameters
    ----------
    config : DualBrokerScannerConfig
        Scanner behaviour configuration.
    watchlist : list[str]
        Nautilus instrument ID strings to scan.
    prev_close_loader : PrevCloseLoader | None
        Optional loader for previous closing prices.
    redis_client : Any | None
        Optional Redis client for persisting scan results.
    instrument_provider : Any | None
        Optional instrument provider for OTC/ETF metadata checks.

    """

    def __init__(
        self,
        config: DualBrokerScannerConfig,
        watchlist: list[str],
        prev_close_loader: Any | None = None,
        redis_client: Any | None = None,
        instrument_provider: Any | None = None,
    ) -> None:
        self._config = config
        self._watchlist = list(watchlist)
        self._prev_close_loader = prev_close_loader
        self._redis = redis_client
        self._instrument_provider = instrument_provider
        self._pass_1_candidates: dict[str, GapCandidate] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(self, pass_number: int = 1) -> list[GapCandidate]:
        """Run a single scan pass over the configured watchlist.

        Parameters
        ----------
        pass_number : int, default 1
            1 = early pass, 2 = late pass (enables trend detection).

        Returns
        -------
        list[GapCandidate]
            Filtered, sorted, and cross-validated candidates.

        """
        if pass_number < 1:
            raise ValueError("pass_number must be >= 1")

        # 1. Build quote collection services
        primary_svc = self._build_quote_service(self._config.primary_broker)
        secondary_svc: QuoteCollectionService | None = None
        if self._config.secondary_broker is not None:
            secondary_svc = self._build_quote_service(self._config.secondary_broker)

        # 2. Collect quotes (parallel when secondary is enabled)
        primary_result, secondary_result = await self._collect_quotes(
            primary_svc, secondary_svc
        )
        logger.info(
            "quote_collected primary=%d secondary=%d",
            len(primary_result.quotes),
            len(secondary_result.quotes) if secondary_result else 0,
        )

        # 3. Cross-validate when both sources are available
        discrepancies: dict[str, str] = {}
        if secondary_result is not None:
            primary_quotes = {str(k): v for k, v in primary_result.quotes.items()}
            secondary_quotes = {str(k): v for k, v in secondary_result.quotes.items()}
            discrepancies = PreMarketGapScanner.cross_validate(
                primary_quotes,
                secondary_quotes,
                threshold_pct=self._config.cross_validation_threshold_pct,
            )
            if discrepancies:
                logger.warning(
                    "cross_validation_discrepancies=%d %s",
                    len(discrepancies),
                    list(discrepancies.keys()),
                )
            else:
                logger.info("cross_validation_passed")

        # 4. Run gap scan on primary quotes
        gap_config = GapScannerConfig(
            min_gap_pct=self._config.min_gap_pct,
            max_gap_pct=self._config.max_gap_pct,
            min_price=self._config.min_price,
            max_price=self._config.max_price,
            min_volume=self._config.min_volume,
            blacklist=self._config.blacklist,
            exclude_otc=self._config.exclude_otc,
            exclude_etf=self._config.exclude_etf,
            market=self._config.market,
            collection_period_secs=self._config.collection_period_secs,
            connection_timeout_secs=self._config.connection_timeout_secs,
            cross_validation_threshold_pct=self._config.cross_validation_threshold_pct,
        )

        # Wrap the already-collected primary result so PreMarketGapScanner
        # can treat it like a quote service.
        primary_wrapper = _StaticQuoteService(primary_result)

        scanner = PreMarketGapScanner(
            config=gap_config,
            quote_service=primary_wrapper,
            prev_close_loader=self._prev_close_loader,
            redis_client=self._redis,
            instrument_provider=self._instrument_provider,
        )

        # Seed Pass-1 cache for trend detection
        if pass_number >= 2 and self._pass_1_candidates:
            scanner._pass_1_candidates = self._pass_1_candidates

        candidates = await scanner.scan(self._watchlist, pass_number=pass_number)

        # 5. Annotate candidates with cross-validation results
        annotated = self._annotate_candidates(candidates, discrepancies)

        # 6. Cache Pass-1 results
        if pass_number == 1:
            self._pass_1_candidates = {c.instrument_id: c for c in annotated}

        return annotated

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_quote_service(self, broker: str) -> QuoteCollectionService:
        return QuoteCollectionService(
            broker=broker,
            watchlist=self._watchlist,
            collection_period_secs=self._config.collection_period_secs,
            connection_timeout_secs=self._config.connection_timeout_secs,
        )

    async def _collect_quotes(
        self,
        primary_svc: QuoteCollectionService,
        secondary_svc: QuoteCollectionService | None,
    ) -> tuple[QuoteCollectionResult, QuoteCollectionResult | None]:
        """Collect quotes from primary (and optionally secondary) broker.

        When *secondary_svc* is provided both collections run in parallel.
        Secondary failures are logged but do not abort the scan.

        """
        if secondary_svc is None:
            result = await primary_svc.collect()
            return result, None

        # Run both in parallel
        results = await asyncio.gather(
            primary_svc.collect(),
            secondary_svc.collect(),
            return_exceptions=True,
        )

        primary_result = results[0]
        if isinstance(primary_result, BaseException):
            raise RuntimeError(
                f"Primary broker {self._config.primary_broker} collection failed"
            ) from primary_result

        secondary_result: QuoteCollectionResult | None = None
        if len(results) > 1:
            sec = results[1]
            if isinstance(sec, BaseException):
                logger.warning(
                    "Secondary broker %s collection failed: %s",
                    self._config.secondary_broker,
                    sec,
                )
            else:
                secondary_result = sec

        return primary_result, secondary_result

    def _annotate_candidates(
        self,
        candidates: list[GapCandidate],
        discrepancies: dict[str, str],
    ) -> list[GapCandidate]:
        """Add ``cross_validated`` / ``cross_validation_note`` to each candidate."""
        annotated: list[GapCandidate] = []
        for cand in candidates:
            note = discrepancies.get(cand.instrument_id, "")
            validated = cand.instrument_id not in discrepancies
            annotated.append(
                GapCandidate(
                    instrument_id=cand.instrument_id,
                    prev_close=cand.prev_close,
                    quote_last=cand.quote_last,
                    gap_pct=cand.gap_pct,
                    bid=cand.bid,
                    ask=cand.ask,
                    volume=cand.volume,
                    trend=cand.trend,
                    pass_number=cand.pass_number,
                    cross_validated=validated,
                    cross_validation_note=note,
                )
            )
        return annotated


# ---------------------------------------------------------------------------
# Helper — wraps an already-collected result
# ---------------------------------------------------------------------------


class _StaticQuoteService:
    """Tiny adapter that makes a :class:`QuoteCollectionResult` quack like a
    quote service (has an async ``collect()`` method).
    """

    def __init__(self, result: QuoteCollectionResult) -> None:
        self._result = result

    async def collect(self) -> QuoteCollectionResult:
        return self._result
