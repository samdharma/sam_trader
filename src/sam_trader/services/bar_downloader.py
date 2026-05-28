"""BarDownloader — Futu OpenD historical bars → Nautilus Parquet catalog.

Usage::

    downloader = BarDownloader(catalog_path="data/catalog")
    result = await downloader.download(
        instrument_ids=["TSLA.NASDAQ"],
        bar_type_spec="5-MINUTE",
        lookback_days=365,
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from futu import RET_OK, KLType
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sam_trader.adapters.futu.common import instrument_id_to_futu_security
from sam_trader.adapters.futu.connection import get_cached_futu_quote_context
from sam_trader.adapters.futu.parsing.market_data import parse_futu_bars

logger = logging.getLogger(__name__)

# Futu free-tier rate limit
DEFAULT_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_MAX_COUNT = 1000

_BAR_TYPE_TO_KL_TYPE: dict[str, KLType] = {
    "1-MINUTE": KLType.K_1M,
    "5-MINUTE": KLType.K_5M,
    "15-MINUTE": KLType.K_15M,
    "1-HOUR": KLType.K_60M,
    "DAY": KLType.K_DAY,
}

_BAR_TYPE_TO_BAR_SPEC: dict[str, BarSpecification] = {
    "1-MINUTE": BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
    "5-MINUTE": BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST),
    "15-MINUTE": BarSpecification(15, BarAggregation.MINUTE, PriceType.LAST),
    "1-HOUR": BarSpecification(1, BarAggregation.HOUR, PriceType.LAST),
    "DAY": BarSpecification(1, BarAggregation.DAY, PriceType.LAST),
}


@dataclass(frozen=True)
class DownloadResult:
    """Result of a bar download operation."""

    instrument_id: str
    bar_type: str
    bars_downloaded: int = 0
    bars_written: int = 0
    start_date: str = ""
    end_date: str = ""
    error: str | None = None


@dataclass(frozen=True)
class BatchDownloadResult:
    """Aggregated result for multiple instruments."""

    results: list[DownloadResult] = field(default_factory=list)
    total_bars_downloaded: int = 0
    total_bars_written: int = 0
    instruments_failed: list[str] = field(default_factory=list)


class BarDownloaderError(Exception):
    """Raised when bar download fails."""


class BarDownloader:
    """Download historical OHLCV bars from Futu OpenD and write to Parquet catalog.

    Parameters
    ----------
    catalog_path : str | os.PathLike[str]
        Path to the Nautilus ParquetDataCatalog directory.
    host : str, optional
        Futu OpenD host (default ``sam-futu-opend``).
    port : int, optional
        Futu OpenD port (default 11111).
    rate_limit_per_minute : int, optional
        Maximum requests per minute (default 30 for Futu free tier).
    """

    def __init__(
        self,
        catalog_path: str | os.PathLike[str],
        host: str | None = None,
        port: int | None = None,
        rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
    ) -> None:
        self._catalog_path = Path(catalog_path)
        self._host = host or os.getenv("FUTU_OPEND_HOST", "sam-futu-opend")
        self._port = port or int(os.getenv("FUTU_OPEND_PORT", "11111"))
        self._rate_limit_delay = 60.0 / max(rate_limit_per_minute, 1)
        self._catalog: ParquetDataCatalog | None = None

    @property
    def catalog(self) -> ParquetDataCatalog:
        """Lazy-initialised ParquetDataCatalog."""
        if self._catalog is None:
            self._catalog = ParquetDataCatalog(path=str(self._catalog_path))
        return self._catalog

    async def download(
        self,
        instrument_ids: list[str],
        bar_type_spec: str = "5-MINUTE",
        lookback_days: int = 365,
    ) -> BatchDownloadResult:
        """Download bars for multiple instruments.

        Parameters
        ----------
        instrument_ids : list[str]
            Nautilus instrument IDs (e.g., ``["TSLA.NASDAQ"]``).
        bar_type_spec : str, optional
            One of ``1-MINUTE``, ``5-MINUTE``, ``15-MINUTE``, ``1-HOUR``, ``DAY``.
        lookback_days : int, optional
            Number of calendar days to look back from today.

        Returns
        -------
        BatchDownloadResult
            Aggregated download results.

        Raises
        ------
        BarDownloaderError
            If the bar type spec is unsupported.

        """
        if bar_type_spec not in _BAR_TYPE_TO_KL_TYPE:
            raise BarDownloaderError(
                f"Unsupported bar_type_spec: {bar_type_spec!r}. "
                f"Supported: {list(_BAR_TYPE_TO_KL_TYPE.keys())}"
            )

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days)

        results: list[DownloadResult] = []
        total_downloaded = 0
        total_written = 0
        failed: list[str] = []

        for instrument_id in instrument_ids:
            try:
                result = await self._download_single(
                    instrument_id=instrument_id,
                    bar_type_spec=bar_type_spec,
                    start_date=start_date,
                    end_date=end_date,
                )
                results.append(result)
                total_downloaded += result.bars_downloaded
                total_written += result.bars_written
                if result.error:
                    failed.append(instrument_id)
            except Exception as exc:
                logger.exception("Download failed for %s", instrument_id)
                results.append(
                    DownloadResult(
                        instrument_id=instrument_id,
                        bar_type=bar_type_spec,
                        error=str(exc),
                    )
                )
                failed.append(instrument_id)

        return BatchDownloadResult(
            results=results,
            total_bars_downloaded=total_downloaded,
            total_bars_written=total_written,
            instruments_failed=failed,
        )

    async def _download_single(
        self,
        instrument_id: str,
        bar_type_spec: str,
        start_date: date,
        end_date: date,
    ) -> DownloadResult:
        """Download bars for a single instrument."""
        kl_type = _BAR_TYPE_TO_KL_TYPE[bar_type_spec]
        bar_spec = _BAR_TYPE_TO_BAR_SPEC[bar_type_spec]
        iid = InstrumentId.from_str(instrument_id)
        bar_type = BarType(iid, bar_spec)
        futu_code = instrument_id_to_futu_security(iid)

        # Incremental update: check catalog for latest bar
        effective_start = self._get_effective_start(start_date, bar_type)
        effective_end = end_date

        if effective_start > effective_end:
            logger.info(
                "Catalog already up-to-date for %s %s (latest: %s)",
                instrument_id,
                bar_type_spec,
                effective_start,
            )
            return DownloadResult(
                instrument_id=instrument_id,
                bar_type=bar_type_spec,
                bars_downloaded=0,
                bars_written=0,
                start_date=str(effective_start),
                end_date=str(effective_end),
            )

        logger.info(
            "Downloading %s %s from %s to %s",
            instrument_id,
            bar_type_spec,
            effective_start,
            effective_end,
        )

        all_bars: list[Bar] = []
        page_req_key: Any = None
        requests_made = 0

        # Use a dedicated quote context (not cached) so we don't interfere
        # with the live data client.  The connection cache is fine for
        # short-lived downloads because the context is closed on exit.
        quote_ctx = get_cached_futu_quote_context(
            host=self._host or "sam-futu-opend",
            port=self._port,
            trade_env="DOWNLOAD",
        )

        try:
            while True:
                ret, data, page_req_key = quote_ctx.request_history_kline(
                    code=futu_code,
                    start=str(effective_start),
                    end=str(effective_end),
                    ktype=kl_type,
                    max_count=DEFAULT_MAX_COUNT,
                    page_req_key=page_req_key,
                )
                requests_made += 1
                await asyncio.sleep(self._rate_limit_delay)

                if ret != RET_OK:
                    error_msg = f"Futu API error for {instrument_id}: {data}"
                    logger.error(error_msg)
                    return DownloadResult(
                        instrument_id=instrument_id,
                        bar_type=bar_type_spec,
                        error=error_msg,
                        start_date=str(effective_start),
                        end_date=str(effective_end),
                    )

                if data is None or data.empty:
                    break

                records = data.to_dict("records")
                bars = parse_futu_bars(records, bar_type)
                all_bars.extend(bars)
                logger.debug(
                    "Fetched %d bars for %s (page %d)",
                    len(bars),
                    instrument_id,
                    requests_made,
                )

                if not page_req_key:
                    break
        finally:
            # Close the cached context so it doesn't leak; the cache key
            # uses trade_env='DOWNLOAD' so it won't affect live clients.
            quote_ctx.close()

        if not all_bars:
            logger.info("No bars returned for %s %s", instrument_id, bar_type_spec)
            return DownloadResult(
                instrument_id=instrument_id,
                bar_type=bar_type_spec,
                bars_downloaded=0,
                bars_written=0,
                start_date=str(effective_start),
                end_date=str(effective_end),
            )

        # Write to catalog
        self.catalog.write_data(all_bars)
        logger.info(
            "Wrote %d bars to catalog for %s %s",
            len(all_bars),
            instrument_id,
            bar_type_spec,
        )

        return DownloadResult(
            instrument_id=instrument_id,
            bar_type=bar_type_spec,
            bars_downloaded=len(all_bars),
            bars_written=len(all_bars),
            start_date=str(effective_start),
            end_date=str(effective_end),
        )

    def _get_effective_start(
        self,
        requested_start: date,
        bar_type: BarType,
    ) -> date:
        """Return the later of *requested_start* or the day after the latest
        catalog bar.

        This implements incremental updates: only download bars newer than what
        is already stored in the Parquet catalog.
        """
        try:
            last_ts = self.catalog.query_last_timestamp(Bar, bar_type)
            if last_ts is not None and not pd.isna(last_ts):
                # Add 1 day overlap to ensure continuity
                _latest: date = last_ts.date()  # type: ignore[no-any-return]
                latest_date = _latest + timedelta(days=1)
                return max(requested_start, latest_date)
        except Exception:
            logger.debug(
                "Could not query catalog for latest bar, using requested start"
            )
        return requested_start


def get_instruments_from_bundles(path: Path) -> list[str]:
    """Return instrument IDs from enabled bundles in a YAML file.

    Parameters
    ----------
    path : Path
        Path to ``bundles.yaml``.

    Returns
    -------
    list[str]
        Unique instrument IDs for FUTU venue bundles.

    """
    import yaml

    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        bundles = raw.get("bundles", [])
        if not isinstance(bundles, list):
            return []
        instruments: set[str] = set()
        for b in bundles:
            if not isinstance(b, dict) or not b.get("enabled", True):
                continue
            if b.get("venue") != "FUTU":
                continue
            instrument_id = b.get("strategy", {}).get("config", {}).get("instrument_id")
            if instrument_id and isinstance(instrument_id, str):
                instruments.add(instrument_id)
        return sorted(instruments)
    except Exception:
        logger.exception("Failed to read bundles from %s", path)
        return []
