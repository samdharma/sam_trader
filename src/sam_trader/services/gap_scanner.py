"""PreMarketGapScanner — Nautilus-native real-time broker data scanner.

Creates a temporary Nautilus data client, streams real-time ``QuoteTick``
for *N* seconds, computes gaps vs. previous close, applies filters, and
persists results to Redis.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId

# Optional Futu imports — module level for testability
_futu_open_quote_ctx: Any = None
_futu_ret_ok: Any = None
_instrument_id_to_futu_security_fn: Any = None

try:
    from futu import RET_OK as _RET_OK  # type: ignore[import-untyped]
    from futu import (
        OpenQuoteContext as _OpenQuoteContext,  # type: ignore[import-untyped]
    )

    _futu_open_quote_ctx = _OpenQuoteContext
    _futu_ret_ok = _RET_OK
except ImportError:  # pragma: no cover
    pass

try:
    from sam_trader.adapters.futu.common import (
        instrument_id_to_futu_security as _instrument_id_to_futu_security,
    )

    _instrument_id_to_futu_security_fn = _instrument_id_to_futu_security
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class Trend(str, Enum):
    """Trend classification for Pass-2 gap candidates."""

    RISING = "RISING"
    FADING = "FADING"
    STABLE = "STABLE"
    LATE_BREAKER = "LATE_BREAKER"


@dataclass(frozen=True)
class GapScannerConfig:
    """Configuration for the pre-market gap scanner."""

    min_gap_pct: float = 2.0
    max_gap_pct: float = 20.0
    min_price: float = 1.0
    max_price: float = 5000.0
    min_volume: float | None = None
    blacklist: tuple[str, ...] = ()
    exclude_otc: bool = True
    exclude_etf: bool = True
    market: str = "US"  # "US" or "HK"
    collection_period_secs: int = 60
    connection_timeout_secs: int = 10
    cross_validation_threshold_pct: float = 1.0  # warn if Futu vs IB gap differs > 1%


@dataclass(frozen=True)
class GapCandidate:
    """A single gap candidate after filtering."""

    instrument_id: str
    prev_close: float
    quote_last: float
    gap_pct: float
    bid: float
    ask: float
    volume: float | None
    trend: str = Trend.STABLE.value
    pass_number: int = 1
    cross_validated: bool = False
    cross_validation_note: str = ""


# ---------------------------------------------------------------------------
# Previous-close loaders
# ---------------------------------------------------------------------------


class PrevCloseLoader(Protocol):
    """Protocol for loading the previous closing price of an instrument."""

    async def load(self, instrument_id: str) -> float | None:
        """Return previous close or *None* if unavailable."""
        ...


class PGFillPrevCloseLoader:
    """Load previous close from the PostgreSQL ``fills`` table."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self._dsn = (
            f"postgresql://{user or os.getenv('POSTGRES_USER', 'sam')}:"
            f"{password or os.getenv('POSTGRES_PASSWORD', 'sam_secret')}"
            f"@{host or os.getenv('POSTGRES_HOST', 'sam-postgres')}:"
            f"{port or int(os.getenv('POSTGRES_PORT', '5432'))}/"
            f"{database or os.getenv('POSTGRES_DB', 'sam_trader')}"
        )

    async def load(self, instrument_id: str) -> float | None:
        try:
            import asyncpg
        except ImportError:  # pragma: no cover
            logger.debug("asyncpg not installed; skipping PG prev-close lookup")
            return None

        try:
            conn = await asyncpg.connect(self._dsn)
            try:
                row = await conn.fetchrow(
                    """
                    SELECT fill_price
                    FROM fills
                    WHERE instrument_id = $1
                    ORDER BY ts_init DESC
                    LIMIT 1
                    """,
                    instrument_id,
                )
                if row and row["fill_price"] is not None:
                    return float(row["fill_price"])
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("PG prev-close lookup failed: %s", exc)
        return None


class FutuKLinePrevCloseLoader:
    """Load previous close from Futu historical k-line (daily)."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self._host = host or os.getenv("FUTU_OPEND_HOST", "sam-futu-opend")
        self._port = port or int(os.getenv("FUTU_OPEND_PORT", "11111"))

    def load(self, instrument_id: str) -> float | None:  # noqa: PLR0911
        if _futu_open_quote_ctx is None or _futu_ret_ok is None:
            logger.debug("futu-api not installed; skipping Futu k-line lookup")
            return None

        if _instrument_id_to_futu_security_fn is None:
            logger.debug("Futu common helpers not available")
            return None

        try:
            futu_code = _instrument_id_to_futu_security_fn(
                InstrumentId.from_str(instrument_id)
            )
        except Exception as exc:
            logger.warning("Cannot map %s to Futu code: %s", instrument_id, exc)
            return None

        try:
            ctx = _futu_open_quote_ctx(host=self._host, port=self._port)
            try:
                ret, data = ctx.get_cur_kline(
                    code=futu_code,
                    num=2,
                    ktype="K_DAY",
                )
                if ret == _futu_ret_ok and data is not None and len(data) >= 2:
                    close = data.iloc[-2].get("close")
                    if close is not None:
                        return float(close)
            finally:
                ctx.close()
        except Exception as exc:
            logger.warning("Futu k-line lookup failed: %s", exc)
        return None


class CompositePrevCloseLoader:
    """Try a list of loaders in order until one succeeds."""

    def __init__(self, loaders: list[Any]) -> None:
        self._loaders = loaders

    async def load(self, instrument_id: str) -> float | None:
        for loader in self._loaders:
            try:
                if inspect.iscoroutinefunction(loader.load):
                    result = await loader.load(instrument_id)
                else:
                    # Run synchronous loaders in thread pool so we don't block
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, loader.load, instrument_id
                    )
                if result is not None:
                    return result  # type: ignore[no-any-return]
            except Exception as exc:
                logger.debug(
                    "PrevCloseLoader %s failed for %s: %s",
                    type(loader).__name__,
                    instrument_id,
                    exc,
                )
        return None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class PreMarketGapScanner:
    """Nautilus-native real-time broker data gap scanner.

    Parameters
    ----------
    config : GapScannerConfig
        Filter and behaviour configuration.
    quote_service : Any
        An object with an async ``collect()`` method returning a result that
        has a ``quotes`` dict (e.g. :class:`QuoteCollectionService`).
    prev_close_loader : PrevCloseLoader | None
        Optional loader for previous closing prices.
    redis_client : Any | None
        Optional Redis client for persisting scan results.
    instrument_provider : Any | None
        Optional instrument provider for OTC/ETF metadata checks.

    """

    def __init__(
        self,
        config: GapScannerConfig,
        quote_service: Any,
        prev_close_loader: PrevCloseLoader | None = None,
        redis_client: Any | None = None,
        instrument_provider: Any | None = None,
    ) -> None:
        self._config = config
        self._quote_service = quote_service
        self._prev_close_loader = prev_close_loader
        self._redis = redis_client
        self._instrument_provider = instrument_provider
        self._pass_1_candidates: dict[str, GapCandidate] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(
        self,
        watchlist: list[str],
        pass_number: int = 1,
    ) -> list[GapCandidate]:
        """Run a single scan pass over *watchlist*.

        Parameters
        ----------
        watchlist : list[str]
            Nautilus instrument ID strings.
        pass_number : int, default 1
            1 = early pass, 2 = late pass (enables trend detection).
            Passes >= 3 are treated as final passes (trend detection enabled,
            no Pass-1 caching).

        Returns
        -------
        list[GapCandidate]
            Filtered and sorted candidates (descending by absolute gap).

        """
        if pass_number < 1:
            raise ValueError("pass_number must be >= 1")

        # 1. Collect real-time quotes
        quotes = await self._collect_quotes(watchlist)
        logger.info("quote_collected=%d", len(quotes))

        if not quotes:
            logger.info("0 candidates (market closed)")
            return []

        # 2. Load previous closes
        prev_closes = await self._load_prev_closes(watchlist)
        logger.info(
            "prev_close_success=%d/%d",
            len(prev_closes),
            len(watchlist),
        )

        # 3. Compute raw gap candidates
        candidates = self._compute_gaps(quotes, prev_closes, pass_number)
        logger.info("raw_gaps=%d", len(candidates))

        # 4. Apply filters
        filtered = self._apply_filters(candidates)
        logger.info("after_filters=%d", len(filtered))

        # 5. Trend detection (Pass 2+)
        if pass_number >= 2:
            filtered = self._apply_trend_detection(filtered)

        # 6. Persist
        await self._save_to_redis(filtered, pass_number)

        # 7. Cache Pass-1 results for trend detection
        if pass_number == 1:
            self._pass_1_candidates = {c.instrument_id: c for c in filtered}

        # Sort by absolute gap descending
        filtered.sort(key=lambda c: abs(c.gap_pct), reverse=True)
        return filtered

    @staticmethod
    def compute_gap_pct(last_price: float, prev_close: float) -> float:
        """Compute gap percentage.

        Returns
        -------
        float
            ``((last_price - prev_close) / prev_close) * 100``
            rounded to 4 decimals.

        """
        return round(((last_price - prev_close) / prev_close) * 100, 4)

    @staticmethod
    def cross_validate(
        futu_quotes: dict[str, QuoteTick],
        ib_quotes: dict[str, QuoteTick],
        threshold_pct: float = 1.0,
    ) -> dict[str, str]:
        """Cross-validate quote mid-prices between Futu and IB.

        Returns a mapping of instrument ID → discrepancy note for symbols
        where the mid-price differs by more than *threshold_pct*.

        """
        discrepancies: dict[str, str] = {}
        for sym, futu_tick in futu_quotes.items():
            ib_tick = ib_quotes.get(sym)
            if ib_tick is None:
                continue
            futu_mid = float(futu_tick.extract_price(PriceType.MID))
            ib_mid = float(ib_tick.extract_price(PriceType.MID))
            if futu_mid <= 0 or ib_mid <= 0:
                continue
            diff_pct = abs(futu_mid - ib_mid) / ((futu_mid + ib_mid) / 2) * 100
            if diff_pct > threshold_pct:
                discrepancies[sym] = (
                    f"Futu mid={futu_mid:.4f} vs IB mid={ib_mid:.4f} "
                    f"(diff={diff_pct:.2f}%)"
                )
        return discrepancies

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _collect_quotes(self, watchlist: list[str]) -> dict[str, QuoteTick]:
        """Delegate to *quote_service* and normalise keys to strings."""
        if hasattr(self._quote_service, "collect"):
            result = await self._quote_service.collect()
            return {str(k): v for k, v in result.quotes.items()}

        if callable(self._quote_service):
            raw = await self._quote_service()
            if isinstance(raw, dict):
                return {str(k): v for k, v in raw.items()}
            return {str(k): v for k, v in raw.quotes.items()}

        raise RuntimeError(
            "quote_service must have an async collect() method or be callable"
        )

    async def _load_prev_closes(self, watchlist: list[str]) -> dict[str, float]:
        """Fetch previous closes for every symbol in *watchlist*."""
        if self._prev_close_loader is None:
            return {}

        results: dict[str, float] = {}
        for sym in watchlist:
            try:
                pc = await self._prev_close_loader.load(sym)
                if pc is not None:
                    results[sym] = pc
            except Exception as exc:
                logger.warning("Prev-close lookup failed for %s: %s", sym, exc)
        return results

    def _compute_gaps(
        self,
        quotes: dict[str, QuoteTick],
        prev_closes: dict[str, float],
        pass_number: int,
    ) -> list[GapCandidate]:
        candidates: list[GapCandidate] = []
        for sym, tick in quotes.items():
            prev_close = prev_closes.get(sym)
            if prev_close is None or prev_close <= 0:
                logger.debug("No prev close for %s; skipping gap calc", sym)
                continue

            last_price = float(tick.extract_price(PriceType.MID))
            gap_pct = self.compute_gap_pct(last_price, prev_close)

            candidates.append(
                GapCandidate(
                    instrument_id=sym,
                    prev_close=prev_close,
                    quote_last=last_price,
                    gap_pct=gap_pct,
                    bid=float(tick.bid_price),
                    ask=float(tick.ask_price),
                    volume=None,  # volume not available on QuoteTick
                    trend=Trend.STABLE.value,
                    pass_number=pass_number,
                )
            )
        return candidates

    def _apply_filters(self, candidates: list[GapCandidate]) -> list[GapCandidate]:
        """Apply price, gap, blacklist, and metadata filters."""
        blacklist = set(self._config.blacklist)
        filtered: list[GapCandidate] = []

        for cand in candidates:
            if cand.instrument_id in blacklist:
                continue

            if not (
                self._config.min_price <= cand.quote_last <= self._config.max_price
            ):
                continue

            gap_abs = abs(cand.gap_pct)
            if not (self._config.min_gap_pct <= gap_abs <= self._config.max_gap_pct):
                continue

            if self._config.min_volume is not None and cand.volume is not None:
                if cand.volume < self._config.min_volume:
                    continue

            if self._config.exclude_otc or self._config.exclude_etf:
                if self._is_otc_or_etf(cand.instrument_id):
                    continue

            filtered.append(cand)

        return filtered

    def _is_otc_or_etf(self, instrument_id: str) -> bool:
        """Return *True* if the instrument is OTC or ETF (when configured)."""
        if self._instrument_provider is None:
            return False

        try:
            iid = InstrumentId.from_str(instrument_id)
            instrument = self._instrument_provider.find(iid)

            # Try instrument_type enum when instrument is resolved
            if instrument is not None:
                itype = getattr(instrument, "instrument_type", None)
                if itype is not None:
                    name = itype.name if hasattr(itype, "name") else str(itype).upper()
                    if self._config.exclude_otc and "OTC" in name:
                        return True
                    if self._config.exclude_etf and "ETF" in name:
                        return True

            # Venue-based OTC heuristic (always checked)
            venue_str = (
                iid.venue.value
                if hasattr(iid.venue, "value")
                else str(iid.venue).upper()
            )
            if self._config.exclude_otc and venue_str in ("OTC", "OTCBB", "PINK"):
                return True

        except Exception as exc:
            logger.warning(
                "Instrument metadata check failed for %s: %s", instrument_id, exc
            )

        return False

    def _apply_trend_detection(
        self,
        pass_2_candidates: list[GapCandidate],
    ) -> list[GapCandidate]:
        """Compare Pass-2 gaps against cached Pass-1 gaps."""
        updated: list[GapCandidate] = []

        for cand in pass_2_candidates:
            p1 = self._pass_1_candidates.get(cand.instrument_id)
            if p1 is None:
                updated.append(replace(cand, trend=Trend.LATE_BREAKER.value))
                continue

            gap_change = cand.gap_pct - p1.gap_pct
            if p1.gap_pct != 0:
                relative_change = (gap_change / abs(p1.gap_pct)) * 100
            else:
                relative_change = 0.0

            if abs(relative_change) < 5.0:
                trend = Trend.STABLE
            elif (cand.gap_pct > 0 and gap_change > 0) or (
                cand.gap_pct < 0 and gap_change < 0
            ):
                trend = Trend.RISING
            else:
                trend = Trend.FADING

            updated.append(replace(cand, trend=trend.value))

        return updated

    async def _save_to_redis(
        self,
        candidates: list[GapCandidate],
        pass_number: int,
    ) -> None:
        if self._redis is None:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"sam:gapscan:{today}:{pass_number}"

        payload = [
            {
                "instrument_id": c.instrument_id,
                "prev_close": c.prev_close,
                "quote_last": c.quote_last,
                "gap_pct": c.gap_pct,
                "bid": c.bid,
                "ask": c.ask,
                "volume": c.volume,
                "trend": c.trend,
                "pass_number": c.pass_number,
                "cross_validated": c.cross_validated,
                "cross_validation_note": c.cross_validation_note,
            }
            for c in candidates
        ]

        try:
            self._redis.set(key, json.dumps(payload), ex=86400)
            logger.info("Saved %d gap candidates to %s", len(candidates), key)
        except Exception as exc:
            logger.warning("Failed to write gap scan to Redis: %s", exc)
