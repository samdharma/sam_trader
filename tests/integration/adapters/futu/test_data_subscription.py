"""Integration tests for Futu data subscription flow.

These tests verify that push data from the Futu SDK flows through the
asyncio.Queue and reaches the Nautilus message bus via _handle_data.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from futu import RET_OK
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.parsing.market_data import parse_futu_quote_tick
from sam_trader.adapters.futu.subscription_manager import DataType as SubDataType
from sam_trader.adapters.futu.subscription_manager import (
    FutuSubscriptionManager,
)


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def make_client(event_loop):
    """Factory to create a FutuLiveDataClient with mocked quote context."""

    def _factory(subscription_manager: FutuSubscriptionManager | None = None):
        cfg = FutuDataClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="US",
            client_id=1,
        )
        clock = LiveClock()
        msgbus = TestComponentStubs.msgbus()
        cache = TestComponentStubs.cache()
        provider = MagicMock(spec=InstrumentProvider)
        quote_ctx = MagicMock()
        quote_ctx.subscribe.return_value = (RET_OK, "")
        quote_ctx.unsubscribe.return_value = (RET_OK, "")
        quote_ctx.set_handler.return_value = RET_OK
        quote_ctx.unsubscribe_all.return_value = None

        client = FutuLiveDataClient(
            loop=event_loop,
            client=quote_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=cfg,
            subscription_manager=subscription_manager,
        )
        return client

    return _factory


@pytest.mark.integration
class TestQuoteTickFlow:
    """End-to-end test: quote tick pushed through queue is handled."""

    def test_quote_tick_received(self, event_loop, make_client):
        """A QuoteTick placed on the internal queue is dispatched by the push loop."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        ts_init = 1_234_567_890_000_000_000
        tick = parse_futu_quote_tick(
            {"last_price": 150.25, "price_spread": 0.01, "volume": 1000},
            instrument_id,
            ts_init,
        )

        # Track what _handle_data sees
        handled_items = []

        def _capture_handle(data):
            handled_items.append(data)
            client._push_task.cancel()

        client._handle_data = _capture_handle  # type: ignore[method-assign]

        # Simulate a push from the Futu SDK by putting directly on the queue
        event_loop.run_until_complete(client._queue.put(tick))

        # Wait for push loop to process and cancel itself
        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=1.0)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        event_loop.run_until_complete(client._disconnect())

        assert len(handled_items) == 1
        received = handled_items[0]
        assert received.instrument_id == instrument_id
        assert str(received.bid_price) == "150.25"
        assert str(received.ask_price) == "150.26"

    def test_quote_tick_flow(self, event_loop, make_client):
        """Subscribe to TSLA.NASDAQ, receive QuoteTick, verify prices, unsubscribe."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        ts_init = 1_234_567_890_000_000_000
        tick = parse_futu_quote_tick(
            {
                "last_price": 250.05,
                "price_spread": 0.01,
                "volume": 5000,
            },
            instrument_id,
            ts_init,
        )

        # Subscribe
        sub_cmd = MagicMock()
        sub_cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._subscribe_quote_ticks(sub_cmd))
        assert instrument_id in client._quote_tick_subs

        # Capture handled ticks
        handled_items = []

        def _capture_handle(data):
            handled_items.append(data)
            if len(handled_items) >= 1:
                client._push_task.cancel()

        client._handle_data = _capture_handle  # type: ignore[method-assign]

        # Simulate push
        event_loop.run_until_complete(client._queue.put(tick))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=1.0)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        # Verify tick content
        assert len(handled_items) == 1
        received = handled_items[0]
        assert received.instrument_id == instrument_id
        assert str(received.bid_price) == "250.05"
        assert str(received.ask_price) == "250.06"
        assert str(received.ask_size) == "5000"

        # Unsubscribe
        unsub_cmd = MagicMock()
        unsub_cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._unsubscribe_quote_ticks(unsub_cmd))
        assert instrument_id not in client._quote_tick_subs

        event_loop.run_until_complete(client._disconnect())

    def test_unsubscribe_stops_ticks(self, event_loop, make_client):
        """After unsubscribing, the local subscription set is empty."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        sub_cmd = MagicMock()
        sub_cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._subscribe_quote_ticks(sub_cmd))
        assert instrument_id in client._quote_tick_subs

        unsub_cmd = MagicMock()
        unsub_cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._unsubscribe_quote_ticks(unsub_cmd))
        assert instrument_id not in client._quote_tick_subs

        event_loop.run_until_complete(client._disconnect())


@pytest.mark.integration
class TestMultipleInstruments:
    """Tests for multiple concurrent subscriptions."""

    def test_multiple_quote_tick_subscriptions(self, event_loop, make_client):
        """Multiple instruments can be subscribed concurrently."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        ids = [
            InstrumentId.from_str("TSLA.NASDAQ"),
            InstrumentId.from_str("AAPL.NASDAQ"),
            InstrumentId.from_str("MSFT.NASDAQ"),
        ]

        for instrument_id in ids:
            cmd = MagicMock()
            cmd.instrument_id = instrument_id
            event_loop.run_until_complete(client._subscribe_quote_ticks(cmd))

        assert client._quote_tick_subs == set(ids)
        event_loop.run_until_complete(client._disconnect())

    def test_mixed_subscription_types(self, event_loop, make_client):
        """Quote, trade tick, bar and order book subs coexist."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        aapl = InstrumentId.from_str("AAPL.NASDAQ")
        msft = InstrumentId.from_str("MSFT.NASDAQ")

        from nautilus_trader.model.data import BarType

        bar_type = BarType.from_str("NVDA.NASDAQ-5-MINUTE-LAST-EXTERNAL")

        event_loop.run_until_complete(
            client._subscribe_quote_ticks(MagicMock(instrument_id=aapl))
        )
        event_loop.run_until_complete(
            client._subscribe_trade_ticks(MagicMock(instrument_id=aapl))
        )
        event_loop.run_until_complete(
            client._subscribe_bars(MagicMock(bar_type=bar_type))
        )
        event_loop.run_until_complete(
            client._subscribe_order_book_deltas(MagicMock(instrument_id=msft))
        )

        assert len(client._quote_tick_subs) == 1
        assert len(client._trade_tick_subs) == 1
        assert len(client._bar_subs) == 1
        assert len(client._order_book_subs) == 1
        event_loop.run_until_complete(client._disconnect())


@pytest.mark.integration
class TestSubscriptionQuotaTracking:
    """Tests for subscription manager integration."""

    def test_quota_increments_on_subscribe(self, event_loop, make_client):
        """Subscription manager count increases after subscribe."""
        mgr = FutuSubscriptionManager()
        client = make_client(subscription_manager=mgr)
        event_loop.run_until_complete(client._connect())

        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        cmd = MagicMock()
        cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._subscribe_quote_ticks(cmd))

        assert mgr.get_count(SubDataType.QUOTE) == 1
        assert mgr.get_active(SubDataType.QUOTE) == [instrument_id]
        event_loop.run_until_complete(client._disconnect())

    def test_quota_decrements_on_unsubscribe(self, event_loop, make_client):
        """Subscription manager count decreases after unsubscribe."""
        mgr = FutuSubscriptionManager()
        client = make_client(subscription_manager=mgr)
        event_loop.run_until_complete(client._connect())

        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        cmd = MagicMock()
        cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._subscribe_quote_ticks(cmd))
        assert mgr.get_count(SubDataType.QUOTE) == 1

        unsub_cmd = MagicMock()
        unsub_cmd.instrument_id = instrument_id
        event_loop.run_until_complete(client._unsubscribe_quote_ticks(unsub_cmd))
        assert mgr.get_count(SubDataType.QUOTE) == 0

        event_loop.run_until_complete(client._disconnect())

    def test_quota_tracks_multiple_data_types(self, event_loop, make_client):
        """Different data types are tracked independently."""
        mgr = FutuSubscriptionManager()
        client = make_client(subscription_manager=mgr)
        event_loop.run_until_complete(client._connect())

        aapl = InstrumentId.from_str("AAPL.NASDAQ")
        tsla = InstrumentId.from_str("TSLA.NASDAQ")

        event_loop.run_until_complete(
            client._subscribe_quote_ticks(MagicMock(instrument_id=aapl))
        )
        event_loop.run_until_complete(
            client._subscribe_trade_ticks(MagicMock(instrument_id=tsla))
        )

        assert mgr.get_count(SubDataType.QUOTE) == 1
        assert mgr.get_count(SubDataType.TRADE_TICK) == 1
        assert mgr.get_count(SubDataType.ORDER_BOOK) == 0
        event_loop.run_until_complete(client._disconnect())
