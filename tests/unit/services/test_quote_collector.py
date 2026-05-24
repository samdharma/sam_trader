"""Unit tests for QuoteCollectionService."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from sam_trader.services.quote_collector import (
    QuoteCollectionResult,
    QuoteCollectionService,
)


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def patch_futu_context():
    """Patch Futu connection so tests never hit the real network."""
    with patch(
        "sam_trader.services.quote_collector.get_cached_futu_quote_context"
    ) as mock_ctx:
        mock_ctx.return_value = MagicMock()
        yield mock_ctx


class TestInfrastructure:
    """Tests that in-process Nautilus infrastructure is created."""

    def test_msgbus_cache_clock_created(self, event_loop):
        """MessageBus, Cache, and LiveClock are instantiated on collect."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            assert svc._msgbus is not None
            assert svc._cache is not None
            assert svc._clock is not None
            await svc._teardown()

        event_loop.run_until_complete(_run())

    def test_unsupported_broker_raises(self, event_loop):
        """Only FUTU and IB are accepted."""
        svc = QuoteCollectionService(
            broker="UNKNOWN",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
        )

        with pytest.raises(RuntimeError, match="Unsupported broker"):
            event_loop.run_until_complete(svc.collect())

    def test_ib_not_implemented(self, event_loop):
        """IB broker raises NotImplementedError."""
        svc = QuoteCollectionService(
            broker="IB",
            host="test-host",
            port=4004,
            watchlist=["TSLA.NASDAQ"],
        )

        with pytest.raises(NotImplementedError):
            event_loop.run_until_complete(svc.collect())


class TestSubscribeAndCollect:
    """Tests for subscription and quote collection flow."""

    def test_single_quote_tick_collected(self, event_loop):
        """A QuoteTick published on the msgbus is captured."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            tick = QuoteTick(
                instrument_id=InstrumentId.from_str("TSLA.NASDAQ"),
                bid_price=Price.from_str("150.00"),
                ask_price=Price.from_str("150.05"),
                bid_size=Quantity.from_int(100),
                ask_size=Quantity.from_int(100),
                ts_event=0,
                ts_init=0,
            )
            svc._on_data(tick)
            result = await svc.collect()
            return result

        # Patch _connect_with_timeout and _subscribe_all so we don't
        # need a real Futu connection.
        with patch.object(svc, "_connect_with_timeout", return_value=None):
            with patch.object(svc, "_subscribe_all", return_value=None):
                result = event_loop.run_until_complete(_run())

        assert len(result.quotes) == 1
        assert InstrumentId.from_str("TSLA.NASDAQ") in result.quotes
        quote = result.quotes[InstrumentId.from_str("TSLA.NASDAQ")]
        assert str(quote.bid_price) == "150.00"
        assert str(quote.ask_price) == "150.05"

    def test_multiple_symbols(self, event_loop):
        """QuoteTicks for multiple symbols are all captured."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ", "AAPL.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            for sym in ("TSLA.NASDAQ", "AAPL.NASDAQ"):
                tick = QuoteTick(
                    instrument_id=InstrumentId.from_str(sym),
                    bid_price=Price.from_str("100.00"),
                    ask_price=Price.from_str("100.05"),
                    bid_size=Quantity.from_int(10),
                    ask_size=Quantity.from_int(10),
                    ts_event=0,
                    ts_init=0,
                )
                svc._on_data(tick)
            result = await svc.collect()
            return result

        with patch.object(svc, "_connect_with_timeout", return_value=None):
            with patch.object(svc, "_subscribe_all", return_value=None):
                result = event_loop.run_until_complete(_run())

        assert len(result.quotes) == 2

    def test_latest_quote_overwrites(self, event_loop):
        """Multiple ticks for the same symbol keep the latest."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            for price in ("150.00", "151.00", "152.00"):
                tick = QuoteTick(
                    instrument_id=InstrumentId.from_str("TSLA.NASDAQ"),
                    bid_price=Price.from_str(price),
                    ask_price=Price.from_str(price),
                    bid_size=Quantity.from_int(100),
                    ask_size=Quantity.from_int(100),
                    ts_event=0,
                    ts_init=0,
                )
                svc._on_data(tick)
            result = await svc.collect()
            return result

        with patch.object(svc, "_connect_with_timeout", return_value=None):
            with patch.object(svc, "_subscribe_all", return_value=None):
                result = event_loop.run_until_complete(_run())

        quote = result.quotes[InstrumentId.from_str("TSLA.NASDAQ")]
        assert str(quote.bid_price) == "152.00"

    def test_non_quote_data_ignored(self, event_loop):
        """Only QuoteTick instances are stored; other data types ignored."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            svc._on_data("not a tick")
            svc._on_data(42)
            result = await svc.collect()
            return result

        with patch.object(svc, "_connect_with_timeout", return_value=None):
            with patch.object(svc, "_subscribe_all", return_value=None):
                result = event_loop.run_until_complete(_run())

        assert len(result.quotes) == 0


class TestCleanup:
    """Tests for resource cleanup."""

    def test_teardown_nulls_references(self, event_loop):
        """After teardown all internal references are cleared."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            await svc._teardown()

        event_loop.run_until_complete(_run())

        assert svc._msgbus is None
        assert svc._cache is None
        assert svc._clock is None
        assert svc._data_client is None
        assert svc._instrument_provider is None
        assert svc._subscription_manager is None

    def test_teardown_safe_when_never_setup(self, event_loop):
        """Teardown is a no-op if setup was never called."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
        )
        event_loop.run_until_complete(svc._teardown())
        assert svc._msgbus is None


class TestTimeout:
    """Tests for connection timeout handling."""

    def test_connection_timeout_raises(self, event_loop):
        """ConnectionError raised when connect exceeds timeout."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            connection_timeout_secs=0,
        )

        async def _slow_connect():
            await asyncio.sleep(10)

        async def _run():
            await svc._setup()
            svc._data_client._connect = _slow_connect  # type: ignore[method-assign]
            await svc._connect_with_timeout()

        with pytest.raises(ConnectionError, match="Timed out"):
            event_loop.run_until_complete(_run())
        event_loop.run_until_complete(svc._teardown())


class TestPartialFailures:
    """Tests for partial subscription failures."""

    def test_invalid_symbol_recorded(self, event_loop):
        """Invalid instrument IDs are recorded as partial failures."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ", "BAD_SYMBOL"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            with patch.object(svc, "_connect_with_timeout", return_value=None):
                await svc._subscribe_all()
            result = await svc.collect()
            return result

        with patch.object(svc, "_connect_with_timeout", return_value=None):
            result = event_loop.run_until_complete(_run())

        assert "BAD_SYMBOL" in result.partial_failures

    def test_zero_quotes_empty_result(self, event_loop):
        """No ticks arriving yields an empty quotes dict."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            result = await svc.collect()
            return result

        with patch.object(svc, "_connect_with_timeout", return_value=None):
            with patch.object(svc, "_subscribe_all", return_value=None):
                result = event_loop.run_until_complete(_run())

        assert result.quotes == {}


class TestQuota:
    """Tests for FutuSubscriptionManager quota integration."""

    def test_quota_rejection_recorded(self, event_loop):
        """Symbols rejected by the quota manager are partial failures."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ", "AAPL.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            # Force quota rejection for every symbol
            svc._subscription_manager.subscribe = AsyncMock(  # type: ignore[method-assign]  # noqa: E501
                return_value=False
            )
            await svc._subscribe_all()
            return svc._partial_failures

        event_loop.run_until_complete(_run())

        assert "TSLA.NASDAQ" in svc._partial_failures
        assert "AAPL.NASDAQ" in svc._partial_failures
        event_loop.run_until_complete(svc._teardown())

    def test_quota_accepted_symbols_subscribed(self, event_loop):
        """Symbols within quota are subscribed successfully."""
        svc = QuoteCollectionService(
            broker="FUTU",
            host="test-host",
            port=11111,
            watchlist=["TSLA.NASDAQ"],
            collection_period_secs=0,
        )

        async def _run():
            await svc._setup()
            mock_client = MagicMock()
            svc._data_client = mock_client
            svc._subscription_manager.subscribe = AsyncMock(  # type: ignore[method-assign]  # noqa: E501
                return_value=True
            )
            await svc._subscribe_all()
            return mock_client._subscribe_quote_ticks.call_count

        count = event_loop.run_until_complete(_run())
        assert count == 1
        event_loop.run_until_complete(svc._teardown())


class TestResult:
    """Tests for QuoteCollectionResult."""

    def test_result_defaults(self):
        """Result dataclass has sensible defaults."""
        result = QuoteCollectionResult()
        assert result.quotes == {}
        assert result.partial_failures == []
        assert result.elapsed_secs == 0.0

    def test_result_immutable(self):
        """Result is frozen and cannot be mutated."""
        result = QuoteCollectionResult()
        with pytest.raises(AttributeError):
            result.elapsed_secs = 1.0  # type: ignore[misc]
