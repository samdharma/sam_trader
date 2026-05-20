"""Unit tests for FutuSubscriptionManager."""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import patch

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from sam_trader.adapters.futu.subscription_manager import (
    DataType,
    FutuSubscriptionManager,
)


@pytest.fixture
def manager():
    """Return a fresh subscription manager with small limits for testing."""
    return FutuSubscriptionManager(
        max_quote_subs=10,
        max_order_book_subs=5,
        max_klines_subs=10,
        max_trade_tick_subs=10,
        warning_threshold=0.80,
        error_threshold=0.95,
        idle_timeout_seconds=60,
    )


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# -----------------------------------------------------------------------------
# Track subscription
# -----------------------------------------------------------------------------


class TestTrackSubscription:
    """Tests for basic subscribe/unsubscribe tracking."""

    def test_subscribe_adds_entry(self, event_loop, manager):
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        result = event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))
        assert result is True
        assert manager.get_count(DataType.QUOTE) == 1
        assert manager.get_active(DataType.QUOTE) == [inst]

    def test_unsubscribe_removes_entry(self, event_loop, manager):
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))
        event_loop.run_until_complete(manager.unsubscribe(inst, DataType.QUOTE))
        assert manager.get_count(DataType.QUOTE) == 0
        assert manager.get_active(DataType.QUOTE) == []

    def test_subscribe_idempotent(self, event_loop, manager):
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))
        result = event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))
        assert result is True
        assert manager.get_count(DataType.QUOTE) == 1

    def test_track_multiple_data_types(self, event_loop, manager):
        inst = InstrumentId.from_str("TSLA.NASDAQ")
        event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))
        event_loop.run_until_complete(manager.subscribe(inst, DataType.ORDER_BOOK))
        event_loop.run_until_complete(manager.subscribe(inst, DataType.KLINE))
        assert manager.get_count(DataType.QUOTE) == 1
        assert manager.get_count(DataType.ORDER_BOOK) == 1
        assert manager.get_count(DataType.KLINE) == 1


# -----------------------------------------------------------------------------
# Quota warning
# -----------------------------------------------------------------------------


class TestQuotaWarning:
    """Tests for WARNING logging at 80% quota."""

    def test_quota_warning_at_80_percent(self, event_loop, manager, caplog):
        caplog.set_level(logging.WARNING)
        limit = manager.get_limit(DataType.QUOTE)
        warning_count = int(limit * 0.80)

        for i in range(warning_count):
            inst = InstrumentId.from_str(f"STK{i}.NASDAQ")
            event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))

        assert manager.get_count(DataType.QUOTE) == warning_count
        assert any(
            "subscription quota at 80%" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.WARNING
        )


# -----------------------------------------------------------------------------
# Quota error
# -----------------------------------------------------------------------------


class TestQuotaError:
    """Tests for ERROR logging at 95% quota."""

    def test_quota_error_at_95_percent(self, event_loop, caplog):
        caplog.set_level(logging.ERROR)
        # Use limit=20 so that 19 subs is exactly 95%.
        mgr = FutuSubscriptionManager(
            max_quote_subs=20,
            max_order_book_subs=5,
            max_klines_subs=10,
            max_trade_tick_subs=10,
            warning_threshold=0.80,
            error_threshold=0.95,
            idle_timeout_seconds=60,
        )

        for i in range(19):
            inst = InstrumentId.from_str(f"STK{i}.NASDAQ")
            event_loop.run_until_complete(mgr.subscribe(inst, DataType.QUOTE))

        assert mgr.get_count(DataType.QUOTE) == 19
        assert any(
            "subscription quota at 95%" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.ERROR
        )

    def test_subscribe_rejected_when_full(self, event_loop, manager, caplog):
        caplog.set_level(logging.ERROR)
        limit = manager.get_limit(DataType.ORDER_BOOK)

        for i in range(limit):
            inst = InstrumentId.from_str(f"STK{i}.NASDAQ")
            event_loop.run_until_complete(manager.subscribe(inst, DataType.ORDER_BOOK))

        extra = InstrumentId.from_str("EXTRA.NASDAQ")
        result = event_loop.run_until_complete(
            manager.subscribe(extra, DataType.ORDER_BOOK)
        )
        assert result is False
        assert any(
            "Subscription quota full" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.ERROR
        )


# -----------------------------------------------------------------------------
# Release idle
# -----------------------------------------------------------------------------


class TestReleaseIdle:
    """Tests for releasing unused subscriptions after idle timeout."""

    def test_release_idle_after_timeout(self, event_loop, manager):
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))

        with patch(
            "sam_trader.adapters.futu.subscription_manager.time.monotonic",
            return_value=time.monotonic() + 120,
        ):
            released = event_loop.run_until_complete(
                manager.release_idle(timeout_seconds=60)
            )

        assert len(released) == 1
        assert released[0].instrument_id == inst
        assert released[0].data_type == DataType.QUOTE
        assert manager.get_count(DataType.QUOTE) == 0

    def test_no_release_before_timeout(self, event_loop, manager):
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))

        released = event_loop.run_until_complete(
            manager.release_idle(timeout_seconds=60)
        )
        assert len(released) == 0
        assert manager.get_count(DataType.QUOTE) == 1

    def test_touch_prevents_release(self, event_loop, manager):
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(manager.subscribe(inst, DataType.QUOTE))

        with patch(
            "sam_trader.adapters.futu.subscription_manager.time.monotonic",
            return_value=time.monotonic() + 90,
        ):
            event_loop.run_until_complete(manager.touch(inst, DataType.QUOTE))
            released = event_loop.run_until_complete(
                manager.release_idle(timeout_seconds=60)
            )

        assert len(released) == 0
        assert manager.get_count(DataType.QUOTE) == 1


# -----------------------------------------------------------------------------
# Priority bundles
# -----------------------------------------------------------------------------


class TestPriorityBundles:
    """Tests for bundle priority over ad-hoc subscriptions."""

    def test_bundle_evicts_ad_hoc_when_full(self, event_loop, manager, caplog):
        caplog.set_level(logging.INFO)
        limit = manager.get_limit(DataType.QUOTE)

        # Fill quota with ad-hoc subscriptions
        for i in range(limit):
            inst = InstrumentId.from_str(f"ADHOC{i}.NASDAQ")
            event_loop.run_until_complete(
                manager.subscribe(inst, DataType.QUOTE, is_bundle=False)
            )

        assert manager.get_count(DataType.QUOTE) == limit

        # Bundle subscription should evict the oldest ad-hoc
        bundle_inst = InstrumentId.from_str("BUNDLE.NASDAQ")
        result = event_loop.run_until_complete(
            manager.subscribe(bundle_inst, DataType.QUOTE, is_bundle=True)
        )
        assert result is True
        assert manager.get_count(DataType.QUOTE) == limit
        assert bundle_inst in manager.get_active(DataType.QUOTE)
        assert any(
            "Evicted ad-hoc" in rec.message
            for rec in caplog.records
            if rec.levelno == logging.INFO
        )

    def test_bundle_rejected_when_only_bundles_present(self, event_loop, manager):
        limit = manager.get_limit(DataType.ORDER_BOOK)

        # Fill quota with bundle subscriptions
        for i in range(limit):
            inst = InstrumentId.from_str(f"BUNDLE{i}.NASDAQ")
            event_loop.run_until_complete(
                manager.subscribe(inst, DataType.ORDER_BOOK, is_bundle=True)
            )

        extra = InstrumentId.from_str("EXTRA.NASDAQ")
        result = event_loop.run_until_complete(
            manager.subscribe(extra, DataType.ORDER_BOOK, is_bundle=True)
        )
        assert result is False
        assert manager.get_count(DataType.ORDER_BOOK) == limit

    def test_ad_hoc_rejected_when_full(self, event_loop, manager):
        limit = manager.get_limit(DataType.KLINE)

        for i in range(limit):
            inst = InstrumentId.from_str(f"STK{i}.NASDAQ")
            event_loop.run_until_complete(
                manager.subscribe(inst, DataType.KLINE, is_bundle=False)
            )

        extra = InstrumentId.from_str("EXTRA.NASDAQ")
        result = event_loop.run_until_complete(
            manager.subscribe(extra, DataType.KLINE, is_bundle=False)
        )
        assert result is False
