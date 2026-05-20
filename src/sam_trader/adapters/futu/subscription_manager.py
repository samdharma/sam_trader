"""Futu subscription quota manager.

Tracks active subscriptions per data type, enforces Futu OpenD limits,
prioritises bundle instruments over ad-hoc subscriptions, and releases
idle subscriptions after a configurable timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from nautilus_trader.model.identifiers import InstrumentId

logger = logging.getLogger(__name__)


class DataType(Enum):
    """Futu subscription data types."""

    QUOTE = "quote"
    TRADE_TICK = "trade_tick"
    ORDER_BOOK = "order_book"
    KLINE = "kline"


@dataclass
class SubscriptionEntry:
    """Active subscription metadata."""

    instrument_id: InstrumentId
    data_type: DataType
    is_bundle: bool
    last_access_time: float = field(default_factory=time.monotonic)


class FutuSubscriptionManager:
    """Manages Futu OpenD subscription quotas.

    Parameters
    ----------
    max_quote_subs : int, default 100
        Maximum quote (SubType.QUOTE) subscriptions.
    max_order_book_subs : int, default 50
        Maximum order-book subscriptions.
    max_klines_subs : int, default 100
        Maximum k-line subscriptions.
    max_trade_tick_subs : int, default 100
        Maximum trade-tick (SubType.TICKER) subscriptions.
    warning_threshold : float, default 0.80
        Fraction of limit at which a WARNING is logged.
    error_threshold : float, default 0.95
        Fraction of limit at which an ERROR is logged.
    idle_timeout_seconds : int, default 60
        Seconds after which an untouched subscription is considered idle.

    """

    def __init__(
        self,
        max_quote_subs: int = 100,
        max_order_book_subs: int = 50,
        max_klines_subs: int = 100,
        max_trade_tick_subs: int = 100,
        warning_threshold: float = 0.80,
        error_threshold: float = 0.95,
        idle_timeout_seconds: int = 60,
    ) -> None:
        self._max_quote_subs = max_quote_subs
        self._max_order_book_subs = max_order_book_subs
        self._max_klines_subs = max_klines_subs
        self._max_trade_tick_subs = max_trade_tick_subs
        self._warning_threshold = warning_threshold
        self._error_threshold = error_threshold
        self._idle_timeout_seconds = idle_timeout_seconds

        self._subs: dict[DataType, dict[InstrumentId, SubscriptionEntry]] = {
            dt: {} for dt in DataType
        }
        self._locks: dict[DataType, asyncio.Lock] = {
            dt: asyncio.Lock() for dt in DataType
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        instrument_id: InstrumentId,
        data_type: DataType,
        is_bundle: bool = False,
    ) -> bool:
        """Attempt to register a subscription.

        Returns ``True`` if the subscription is accepted (within quota or
        an ad-hoc subscription was evicted to make room for a bundle
        subscription). Returns ``False`` if the quota is full and the
        subscription cannot be accommodated.

        """
        async with self._locks[data_type]:
            subs = self._subs[data_type]

            # Already subscribed – just refresh the timestamp.
            if instrument_id in subs:
                subs[instrument_id].last_access_time = time.monotonic()
                return True

            limit = self._limit(data_type)
            if len(subs) < limit:
                subs[instrument_id] = SubscriptionEntry(
                    instrument_id=instrument_id,
                    data_type=data_type,
                    is_bundle=is_bundle,
                )
                self._check_thresholds(data_type, len(subs), limit)
                return True

            # Quota full – if this is a bundle subscription, evict the
            # oldest ad-hoc subscription first.
            if is_bundle:
                evicted = self._evict_oldest_ad_hoc(data_type)
                if evicted is not None:
                    logger.info(
                        "Evicted ad-hoc %s sub for %s to make room " "for bundle %s",
                        data_type.value,
                        evicted.instrument_id,
                        instrument_id,
                    )
                    subs[instrument_id] = SubscriptionEntry(
                        instrument_id=instrument_id,
                        data_type=data_type,
                        is_bundle=is_bundle,
                    )
                    self._check_thresholds(data_type, len(subs), limit)
                    return True

            # Cannot accommodate.
            logger.error(
                "Subscription quota full for %s (%d/%d). Rejecting %s",
                data_type.value,
                len(subs),
                limit,
                instrument_id,
            )
            return False

    async def unsubscribe(
        self,
        instrument_id: InstrumentId,
        data_type: DataType,
    ) -> None:
        """Remove a subscription."""
        async with self._locks[data_type]:
            self._subs[data_type].pop(instrument_id, None)

    async def touch(
        self,
        instrument_id: InstrumentId,
        data_type: DataType,
    ) -> None:
        """Update the last-access time for an active subscription."""
        async with self._locks[data_type]:
            entry = self._subs[data_type].get(instrument_id)
            if entry is not None:
                entry.last_access_time = time.monotonic()

    async def release_idle(
        self,
        timeout_seconds: int | None = None,
    ) -> list[SubscriptionEntry]:
        """Return and remove subscriptions idle longer than *timeout_seconds*.

        The caller is responsible for unsubscribing from Futu OpenD.

        """
        timeout = timeout_seconds or self._idle_timeout_seconds
        cutoff = time.monotonic() - timeout
        released: list[SubscriptionEntry] = []

        for data_type in DataType:
            async with self._locks[data_type]:
                subs = self._subs[data_type]
                to_remove = [
                    instrument_id
                    for instrument_id, entry in subs.items()
                    if entry.last_access_time < cutoff
                ]
                for instrument_id in to_remove:
                    released.append(subs.pop(instrument_id))

        if released:
            logger.info(
                "Released %d idle subscription(s) after %ds timeout",
                len(released),
                timeout,
            )
        return released

    def get_active(self, data_type: DataType) -> list[InstrumentId]:
        """Return a snapshot of active instrument IDs for *data_type*."""
        return list(self._subs[data_type].keys())

    def get_count(self, data_type: DataType) -> int:
        """Return the current subscription count for *data_type*."""
        return len(self._subs[data_type])

    def get_limit(self, data_type: DataType) -> int:
        """Return the quota limit for *data_type*."""
        return self._limit(data_type)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _limit(self, data_type: DataType) -> int:
        if data_type is DataType.QUOTE:
            return self._max_quote_subs
        if data_type is DataType.TRADE_TICK:
            return self._max_trade_tick_subs
        if data_type is DataType.ORDER_BOOK:
            return self._max_order_book_subs
        if data_type is DataType.KLINE:
            return self._max_klines_subs
        raise ValueError(f"Unknown data type: {data_type}")

    def _check_thresholds(self, data_type: DataType, count: int, limit: int) -> None:
        ratio = count / limit
        if ratio >= self._error_threshold:
            logger.error(
                "Futu %s subscription quota at %.0f%% (%d/%d)",
                data_type.value,
                ratio * 100,
                count,
                limit,
            )
        elif ratio >= self._warning_threshold:
            logger.warning(
                "Futu %s subscription quota at %.0f%% (%d/%d)",
                data_type.value,
                ratio * 100,
                count,
                limit,
            )

    def _evict_oldest_ad_hoc(self, data_type: DataType) -> SubscriptionEntry | None:
        """Evict and return the oldest ad-hoc subscription, or None."""
        subs = self._subs[data_type]
        candidates = [entry for entry in subs.values() if not entry.is_bundle]
        if not candidates:
            return None
        oldest = min(candidates, key=lambda e: e.last_access_time)
        subs.pop(oldest.instrument_id)
        return oldest
