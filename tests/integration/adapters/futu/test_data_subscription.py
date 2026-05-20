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


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def make_client(event_loop):
    """Factory to create a FutuLiveDataClient with mocked quote context."""

    def _factory():
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
