"""Unit tests for FutuLiveDataClient."""

from __future__ import annotations

import asyncio
import logging as _logging_mod
import time
from unittest.mock import MagicMock, patch

import pytest
from futu import RET_OK, ContextStatus, SubType
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import ClientId, InstrumentId, Venue
from nautilus_trader.model.objects import Price, Quantity
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
    ctx.status = ContextStatus.READY
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

    def test_connect_refreshes_stale_context(self, event_loop, make_client):
        """If _quote_ctx is not READY, _connect() must fetch a fresh context."""
        mock_stale = MagicMock()
        mock_stale.status = ContextStatus.CLOSED
        mock_stale.set_handler.return_value = RET_OK
        mock_stale.subscribe.return_value = (RET_OK, "")
        client = make_client(quote_ctx=mock_stale)

        mock_fresh = MagicMock()
        mock_fresh.status = ContextStatus.READY
        mock_fresh.set_handler.return_value = RET_OK
        mock_fresh.subscribe.return_value = (RET_OK, "")

        with patch(
            "sam_trader.adapters.futu.data.get_cached_futu_quote_context"
        ) as mock_get:
            mock_get.return_value = mock_fresh
            event_loop.run_until_complete(client._connect())

        mock_get.assert_called_once_with(
            "test-host", 11111, "SIMULATE", on_disconnect=client._on_futu_disconnect
        )
        assert client._quote_ctx is mock_fresh
        mock_stale.close.assert_called_once()

    def test_disconnect_sets_context_to_none(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())
        assert client._quote_ctx is not None

        event_loop.run_until_complete(client._disconnect())

        assert client._quote_ctx is None

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

    def test_keep_alive_task_starts_on_connect(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())

        assert client._keep_alive_task is not None
        assert not client._keep_alive_task.done()
        event_loop.run_until_complete(client._disconnect())

    def test_keep_alive_task_cancelled_on_disconnect(
        self, event_loop, make_client, mock_quote_ctx
    ):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())
        task = client._keep_alive_task
        assert task is not None

        event_loop.run_until_complete(client._disconnect())

        assert task.cancelled() or task.done()
        assert client._keep_alive_task is None

    def test_reconnect_metrics_after_disconnect(
        self, event_loop, make_client, mock_quote_ctx, caplog
    ):
        client = make_client(quote_ctx=mock_quote_ctx)
        event_loop.run_until_complete(client._connect())
        event_loop.run_until_complete(client._disconnect())

        # Simulate a disconnect callback firing
        client._on_futu_disconnect("RemoteClose", 3600.0)
        assert client._disconnect_reason == "RemoteClose"

        with patch(
            "sam_trader.adapters.futu.data.get_cached_futu_quote_context"
        ) as mock_get:
            mock_get.return_value = mock_quote_ctx
            event_loop.run_until_complete(client._connect())

        assert client._connect_time is not None
        assert client._disconnect_time is None
        assert client._disconnect_reason is None
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


# ---------------------------------------------------------------------------
# Bar Debug Logging
# ---------------------------------------------------------------------------


def _make_mock_bar(
    instrument_id: InstrumentId | None = None,
    bar_type: BarType | None = None,
    ts_event: int = 1_700_000_000_000_000_000,
    open_price: float = 150.0,
    high_price: float = 152.0,
    low_price: float = 149.0,
    close_price: float = 151.0,
    volume: int = 10000,
) -> Bar:
    """Create a Bar instance for testing."""
    if instrument_id is None:
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
    if bar_type is None:
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")
    return Bar(
        bar_type=bar_type,
        open=Price.from_str(str(open_price)),
        high=Price.from_str(str(high_price)),
        low=Price.from_str(str(low_price)),
        close=Price.from_str(str(close_price)),
        volume=Quantity.from_int(volume),
        ts_event=ts_event,
        ts_init=ts_event,
    )


class TestBarDebugLogging:
    """Tests for rate-limited DEBUG-level bar reception logging.

    NOTE: Nautilus Cython Logger attributes (_log, _log.debug) are read-only
    extension types and cannot be mocked. Tests verify internal state changes
    (_bar_log_timestamps, _bar_log_counts) and the guard condition
    (logger.isEnabledFor). The actual debug() call is self-evident f-string
    formatting — tested implicitly via state correctness.
    """

    @pytest.fixture(autouse=True)
    def _enable_debug_logging(self):
        """Set the Python logger to DEBUG so guard passes."""
        lgr = _logging_mod.getLogger("sam_trader.adapters.futu.data")
        old_level = lgr.level
        lgr.setLevel(_logging_mod.DEBUG)
        yield
        lgr.setLevel(old_level)

    def test_single_bar_updates_state(self, make_client):
        """AC1: A single bar at DEBUG level updates timestamp and resets count."""
        client = make_client()
        bar = _make_mock_bar()
        before = time.monotonic()

        client._log_bar_summary_if_debug(bar)

        assert "AAPL.NASDAQ" in client._bar_log_timestamps
        assert client._bar_log_timestamps["AAPL.NASDAQ"] >= before
        assert client._bar_log_counts["AAPL.NASDAQ"] == 0

    def test_non_bar_item_skipped(self, make_client):
        """Non-Bar items should leave state unchanged."""
        client = make_client()

        client._log_bar_summary_if_debug({"type": "quote_tick"})
        client._log_bar_summary_if_debug("some_string")
        client._log_bar_summary_if_debug(42)

        assert len(client._bar_log_timestamps) == 0
        assert len(client._bar_log_counts) == 0

    def test_rate_limit_same_instrument_within_window(self, make_client):
        """AC2: Multiple bars within 60s accumulate count, log only once."""
        client = make_client()
        bar = _make_mock_bar()

        client._log_bar_summary_if_debug(bar)
        # Count reset to 0 after first log. Accumulate 2 more within window.
        client._log_bar_summary_if_debug(bar)
        client._log_bar_summary_if_debug(bar)

        # Counter should show 2 (accumulated since last log)
        assert client._bar_log_counts["AAPL.NASDAQ"] == 2
        # Timestamp still from first call
        assert "AAPL.NASDAQ" in client._bar_log_timestamps

    def test_rate_limit_resets_after_window(self, make_client):
        """AC2: After 60s window expires, a new timestamp is recorded."""
        client = make_client()
        bar = _make_mock_bar()

        client._log_bar_summary_if_debug(bar)
        first_ts = client._bar_log_timestamps["AAPL.NASDAQ"]
        assert client._bar_log_counts["AAPL.NASDAQ"] == 0

        # Force the last log timestamp to be 61 seconds ago
        client._bar_log_timestamps["AAPL.NASDAQ"] = time.monotonic() - 61.0
        client._bar_log_counts["AAPL.NASDAQ"] = 0

        # Another bar after the window
        client._log_bar_summary_if_debug(bar)

        # Timestamp should be updated (>= now), counter reset
        assert client._bar_log_timestamps["AAPL.NASDAQ"] > first_ts
        assert client._bar_log_counts["AAPL.NASDAQ"] == 0

    def test_bar_count_accumulates_correctly(self, make_client):
        """Count accumulates across multiple bars within the same window."""
        client = make_client()
        bar = _make_mock_bar()

        # First bar triggers log, sets timestamp, resets count to 0
        client._log_bar_summary_if_debug(bar)
        assert client._bar_log_counts["AAPL.NASDAQ"] == 0

        # Send 4 more bars within the same window
        for _ in range(4):
            client._log_bar_summary_if_debug(bar)

        # Count should be 4 (accumulated, not yet logged)
        assert client._bar_log_counts["AAPL.NASDAQ"] == 4

    def test_multi_instrument_independent_counters(self, make_client):
        """Counters are per-instrument, not global."""
        client = make_client()
        bar_aapl = _make_mock_bar(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"),
        )
        bar_tsla = _make_mock_bar(
            instrument_id=InstrumentId.from_str("TSLA.NASDAQ"),
            bar_type=BarType.from_str("TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"),
        )

        client._log_bar_summary_if_debug(bar_aapl)
        client._log_bar_summary_if_debug(bar_tsla)

        # Each instrument has its own timestamp and counter
        assert "AAPL.NASDAQ" in client._bar_log_timestamps
        assert "TSLA.NASDAQ" in client._bar_log_timestamps
        assert client._bar_log_counts["AAPL.NASDAQ"] == 0
        assert client._bar_log_counts["TSLA.NASDAQ"] == 0

    def test_multi_instrument_rate_limit_isolated(self, make_client):
        """Rate limit windows are per-instrument: one can log while other suppressed."""
        client = make_client()
        bar_aapl = _make_mock_bar(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"),
        )
        bar_tsla = _make_mock_bar(
            instrument_id=InstrumentId.from_str("TSLA.NASDAQ"),
            bar_type=BarType.from_str("TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"),
        )

        # First bars: both instruments log
        client._log_bar_summary_if_debug(bar_aapl)
        client._log_bar_summary_if_debug(bar_tsla)
        # More AAPL bars within window: suppressed (counter increments)
        client._log_bar_summary_if_debug(bar_aapl)
        client._log_bar_summary_if_debug(bar_aapl)
        # More TSLA bars within window: suppressed
        client._log_bar_summary_if_debug(bar_tsla)

        # AAPL should have count of 2 (suppressed bars since last log)
        assert client._bar_log_counts["AAPL.NASDAQ"] == 2
        # TSLA should have count of 1
        assert client._bar_log_counts["TSLA.NASDAQ"] == 1

        # Force AAPL window expired; TSLA still within window
        client._bar_log_timestamps["AAPL.NASDAQ"] = time.monotonic() - 61.0
        client._log_bar_summary_if_debug(bar_aapl)
        client._log_bar_summary_if_debug(bar_tsla)

        # AAPL: new log was emitted (timestamp updated, count reset to 0)
        assert client._bar_log_timestamps["AAPL.NASDAQ"] > time.monotonic() - 1.0
        assert client._bar_log_counts["AAPL.NASDAQ"] == 0
        # TSLA: still within window, counter incremented
        assert client._bar_log_counts["TSLA.NASDAQ"] == 2

    def test_no_overhead_at_info_level(self, make_client):
        """AC4: When Python logger is at INFO, method body is skipped entirely."""
        lgr = _logging_mod.getLogger("sam_trader.adapters.futu.data")
        old_level = lgr.level
        lgr.setLevel(_logging_mod.INFO)
        try:
            client = make_client()
            bar = _make_mock_bar()
            client._log_bar_summary_if_debug(bar)
            # State should be unchanged — method returned early
            assert len(client._bar_log_timestamps) == 0
            assert len(client._bar_log_counts) == 0
        finally:
            lgr.setLevel(old_level)

    def test_initial_state_fields(self, make_client):
        """Verify the rate-limit state fields are initialized on the client."""
        client = make_client()
        assert hasattr(client, "_bar_log_timestamps")
        assert hasattr(client, "_bar_log_counts")
        assert isinstance(client._bar_log_timestamps, dict)
        assert isinstance(client._bar_log_counts, dict)
        assert len(client._bar_log_timestamps) == 0
        assert len(client._bar_log_counts) == 0
