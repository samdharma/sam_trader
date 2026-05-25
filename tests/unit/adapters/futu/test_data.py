"""Unit tests for FutuLiveDataClient."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from futu import RET_OK, SubType
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import ClientId, InstrumentId, Venue
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.adapters.futu.data import FutuLiveDataClient


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_quote_ctx() -> MagicMock:
    """Return a mock OpenQuoteContext."""
    ctx = MagicMock()
    ctx.subscribe.return_value = (RET_OK, "")
    ctx.unsubscribe.return_value = (RET_OK, "")
    ctx.unsubscribe_all.return_value = None
    ctx.set_handler.return_value = RET_OK
    return ctx


@pytest.fixture
def make_client(event_loop, mock_quote_ctx):
    """Factory to create a FutuLiveDataClient with mocked dependencies."""

    def _factory(
        config: FutuDataClientConfig | None = None,
        quote_ctx: MagicMock | None = None,
    ) -> FutuLiveDataClient:
        cfg = config or FutuDataClientConfig(
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
        client = FutuLiveDataClient(
            loop=event_loop,
            client=quote_ctx or mock_quote_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=cfg,
        )
        return client

    return _factory


def _make_sub_cmd(**attrs) -> MagicMock:
    """Create a mock Nautilus subscribe/unsubscribe command with given attrs."""
    cmd = MagicMock()
    for k, v in attrs.items():
        setattr(cmd, k, v)
    return cmd


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------


class TestSubscribeUnsubscribe:
    """Tests for individual subscribe/unsubscribe operations."""

    def test_subscribe_quote_tick(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        cmd = _make_sub_cmd(instrument_id=instrument_id)

        event_loop.run_until_complete(client._subscribe_quote_ticks(cmd))

        mock_quote_ctx.subscribe.assert_called_once_with(["US.AAPL"], [SubType.QUOTE])
        assert instrument_id in client._quote_tick_subs

    def test_unsubscribe_quote_tick(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        client._quote_tick_subs.add(instrument_id)
        cmd = _make_sub_cmd(instrument_id=instrument_id)

        event_loop.run_until_complete(client._unsubscribe_quote_ticks(cmd))

        mock_quote_ctx.unsubscribe.assert_called_once_with(["US.AAPL"], [SubType.QUOTE])
        assert instrument_id not in client._quote_tick_subs

    def test_subscribe_trade_tick(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        cmd = _make_sub_cmd(instrument_id=instrument_id)

        event_loop.run_until_complete(client._subscribe_trade_ticks(cmd))

        mock_quote_ctx.subscribe.assert_called_once_with(["US.TSLA"], [SubType.TICKER])
        assert instrument_id in client._trade_tick_subs

    def test_unsubscribe_trade_tick(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        client._trade_tick_subs.add(instrument_id)
        cmd = _make_sub_cmd(instrument_id=instrument_id)

        event_loop.run_until_complete(client._unsubscribe_trade_ticks(cmd))

        mock_quote_ctx.unsubscribe.assert_called_once_with(
            ["US.TSLA"], [SubType.TICKER]
        )
        assert instrument_id not in client._trade_tick_subs

    def test_subscribe_bars(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        cmd = _make_sub_cmd(bar_type=bar_type)

        event_loop.run_until_complete(client._subscribe_bars(cmd))

        mock_quote_ctx.subscribe.assert_called_once_with(["US.AAPL"], [SubType.K_5M])
        assert bar_type in client._bar_subs
        assert client._bar_subs[bar_type] == bar_type.instrument_id

    def test_unsubscribe_bars(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        client._bar_subs[bar_type] = bar_type.instrument_id
        cmd = _make_sub_cmd(bar_type=bar_type)

        event_loop.run_until_complete(client._unsubscribe_bars(cmd))

        mock_quote_ctx.unsubscribe.assert_called_once_with(["US.AAPL"], [SubType.K_5M])
        assert bar_type not in client._bar_subs

    def test_subscribe_order_book(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        cmd = _make_sub_cmd(instrument_id=instrument_id)

        event_loop.run_until_complete(client._subscribe_order_book_deltas(cmd))

        mock_quote_ctx.subscribe.assert_called_once_with(
            ["US.AAPL"], [SubType.ORDER_BOOK]
        )
        assert instrument_id in client._order_book_subs

    def test_unsubscribe_order_book(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        client._order_book_subs.add(instrument_id)
        cmd = _make_sub_cmd(instrument_id=instrument_id)

        event_loop.run_until_complete(client._unsubscribe_order_book_deltas(cmd))

        mock_quote_ctx.unsubscribe.assert_called_once_with(
            ["US.AAPL"], [SubType.ORDER_BOOK]
        )
        assert instrument_id not in client._order_book_subs


# ---------------------------------------------------------------------------
# Multiple subscriptions
# ---------------------------------------------------------------------------


class TestMultipleSubscriptions:
    """Tests for tracking multiple concurrent subscriptions."""

    def test_multiple_quote_tick_subscriptions(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client()
        ids = [
            InstrumentId.from_str("AAPL.NASDAQ"),
            InstrumentId.from_str("TSLA.NASDAQ"),
            InstrumentId.from_str("MSFT.NASDAQ"),
        ]

        for instrument_id in ids:
            cmd = _make_sub_cmd(instrument_id=instrument_id)
            event_loop.run_until_complete(client._subscribe_quote_ticks(cmd))

        assert client._quote_tick_subs == set(ids)
        assert mock_quote_ctx.subscribe.call_count == 3

    def test_mixed_subscription_types(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()

        event_loop.run_until_complete(
            client._subscribe_quote_ticks(
                _make_sub_cmd(instrument_id=InstrumentId.from_str("AAPL.NASDAQ"))
            )
        )
        event_loop.run_until_complete(
            client._subscribe_trade_ticks(
                _make_sub_cmd(instrument_id=InstrumentId.from_str("AAPL.NASDAQ"))
            )
        )
        bar_type = BarType.from_str("TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL")
        event_loop.run_until_complete(
            client._subscribe_bars(_make_sub_cmd(bar_type=bar_type))
        )
        event_loop.run_until_complete(
            client._subscribe_order_book_deltas(
                _make_sub_cmd(instrument_id=InstrumentId.from_str("MSFT.NASDAQ"))
            )
        )

        assert len(client._quote_tick_subs) == 1
        assert len(client._trade_tick_subs) == 1
        assert len(client._bar_subs) == 1
        assert len(client._order_book_subs) == 1
        assert mock_quote_ctx.subscribe.call_count == 4

    def test_subscription_restoration_on_connect(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client()
        client._quote_tick_subs.add(InstrumentId.from_str("AAPL.NASDAQ"))
        client._trade_tick_subs.add(InstrumentId.from_str("TSLA.NASDAQ"))
        bar_type = BarType.from_str("MSFT.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        client._bar_subs[bar_type] = bar_type.instrument_id
        client._order_book_subs.add(InstrumentId.from_str("NVDA.NASDAQ"))

        event_loop.run_until_complete(client._restore_subscriptions())

        calls = mock_quote_ctx.subscribe.call_args_list
        assert len(calls) == 4
        sub_types = {call[0][1][0] for call in calls}
        assert SubType.QUOTE in sub_types
        assert SubType.TICKER in sub_types
        assert SubType.K_5M in sub_types
        assert SubType.ORDER_BOOK in sub_types


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Tests for connection lifecycle and push loop."""

    def test_connect_starts_push_loop(self, event_loop, make_client, mock_quote_ctx):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())

        assert client._push_task is not None
        assert not client._push_task.done()
        event_loop.run_until_complete(client._disconnect())

    def test_connect_sets_up_handlers(self, event_loop, make_client, mock_quote_ctx):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())

        assert len(client._handlers) >= 3
        assert mock_quote_ctx.set_handler.call_count >= 3
        event_loop.run_until_complete(client._disconnect())

    def test_disconnect_cancels_push_loop(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())
        task = client._push_task
        assert task is not None

        event_loop.run_until_complete(client._disconnect())

        assert task.cancelled() or task.done()
        assert client._push_task is None
        mock_quote_ctx.unsubscribe_all.assert_called_once()

    def test_disconnect_clears_handlers(self, event_loop, make_client, mock_quote_ctx):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())
        assert len(client._handlers) > 0

        event_loop.run_until_complete(client._disconnect())

        assert len(client._handlers) == 0

    def test_push_loop_processes_queue_items(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())

        handled = []

        def _mock_handle(data):
            handled.append(data)
            client._push_task.cancel()

        client._handle_data = _mock_handle  # type: ignore[method-assign]

        test_item = {"type": "test"}
        event_loop.run_until_complete(client._queue.put(test_item))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=0.5)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert len(handled) == 1
        assert handled[0] == test_item
        event_loop.run_until_complete(client._disconnect())


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


class TestBackfill:
    """Tests for historical bar backfill."""

    def test_backfill_requests_history_for_bar_subs(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client()
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        client._bar_subs[bar_type] = bar_type.instrument_id

        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.to_dict.return_value = [
            {
                "open": 150.0,
                "high": 151.0,
                "low": 149.0,
                "close": 150.5,
                "volume": 1000,
                "timestamp": 1_234_567_890.0,
            }
        ]
        mock_quote_ctx.request_history_kline.return_value = (
            RET_OK,
            mock_df,
            None,
        )

        event_loop.run_until_complete(client._backfill_bars())

        mock_quote_ctx.request_history_kline.assert_called_once_with(
            "US.AAPL",
            ktype=SubType.K_5M,
            max_count=100,
        )


# ---------------------------------------------------------------------------
# Request bars
# ---------------------------------------------------------------------------


class TestRequestBars:
    """Tests for on-demand historical bar requests."""

    def test_request_bars_success(self, event_loop, make_client, mock_quote_ctx):
        client = make_client()
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        request = RequestBars(
            bar_type=bar_type,
            start=None,
            end=None,
            limit=50,
            client_id=ClientId("FUTU-1"),
            venue=Venue("FUTU"),
            callback=None,
            request_id=UUID4(),
            ts_init=0,
            params=None,
        )

        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.to_dict.return_value = [
            {
                "open": 150.0,
                "high": 151.0,
                "low": 149.0,
                "close": 150.5,
                "volume": 1000,
                "timestamp": 1_234_567_890.0,
            }
        ]
        mock_quote_ctx.request_history_kline.return_value = (
            RET_OK,
            mock_df,
            None,
        )

        handled = []
        original_handle = client._handle_data

        def _capture_handle(data):
            handled.append(data)
            original_handle(data)

        client._handle_data = _capture_handle  # type: ignore[method-assign]

        event_loop.run_until_complete(client._request_bars(request))

        mock_quote_ctx.request_history_kline.assert_called_once_with(
            "US.AAPL",
            ktype=SubType.K_5M,
            max_count=50,
        )
        assert len(handled) == 1
        assert handled[0].bar_type == bar_type

    def test_request_bars_no_context(self, event_loop, make_client):
        client = make_client()
        client._quote_ctx = None
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        request = RequestBars(
            bar_type=bar_type,
            start=None,
            end=None,
            limit=50,
            client_id=ClientId("FUTU-1"),
            venue=Venue("FUTU"),
            callback=None,
            request_id=UUID4(),
            ts_init=0,
            params=None,
        )

        # Should return gracefully without raising
        event_loop.run_until_complete(client._request_bars(request))

    def test_request_bars_unsupported_bar_type(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client()
        # Create a bar type with an unsupported aggregation (e.g., SECOND)
        bar_type = BarType.from_str("AAPL.NASDAQ-5-SECOND-LAST-EXTERNAL")
        request = RequestBars(
            bar_type=bar_type,
            start=None,
            end=None,
            limit=50,
            client_id=ClientId("FUTU-1"),
            venue=Venue("FUTU"),
            callback=None,
            request_id=UUID4(),
            ts_init=0,
            params=None,
        )

        event_loop.run_until_complete(client._request_bars(request))

        mock_quote_ctx.request_history_kline.assert_not_called()
