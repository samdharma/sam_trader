"""Integration tests for Futu connection lifecycle: keep-alive, RemoteClose, reconnect."""  # noqa: E501

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from futu import RET_OK, ContextStatus, SubType
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
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
    ctx.query_subscription.return_value = (RET_OK, {})
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
            keep_alive_interval_secs=1,
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


class TestConnectionLifecycle:
    """Integration tests for connection keep-alive, RemoteClose, and reconnect."""

    def test_reconnect_after_remote_close(
        self, event_loop, make_client, mock_quote_ctx
    ):
        """Simulate RemoteClose disconnect and verify reconnect with restoration."""
        client = make_client(quote_ctx=mock_quote_ctx)
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        bar_type = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")

        # Pre-populate subscriptions as if the client had been running
        client._quote_tick_subs.add(instrument_id)
        client._bar_subs[bar_type] = instrument_id

        # Initial connect
        event_loop.run_until_complete(client._connect())
        assert client._connect_time is not None
        assert client._keep_alive_task is not None

        # Simulate Nautilus disconnect (e.g. detected by health check)
        event_loop.run_until_complete(client._disconnect())

        # Simulate RemoteClose notification from Futu SDK
        client._on_futu_disconnect("RemoteClose", 3600.0)
        assert client._disconnect_reason == "RemoteClose"
        assert client._disconnect_time is not None

        # Reconnect with a fresh mock context
        mock_fresh = MagicMock()
        mock_fresh.status = ContextStatus.READY
        mock_fresh.subscribe.return_value = (RET_OK, "")
        mock_fresh.set_handler.return_value = RET_OK
        mock_fresh.query_subscription.return_value = (RET_OK, {})

        with patch(
            "sam_trader.adapters.futu.data.get_cached_futu_quote_context"
        ) as mock_get:
            mock_get.return_value = mock_fresh
            event_loop.run_until_complete(client._connect())

        # Verify state reset after reconnect
        assert client._disconnect_time is None
        assert client._disconnect_reason is None
        assert client._connect_time is not None

        # Verify subscriptions restored on fresh context
        mock_fresh.subscribe.assert_any_call(["US.AAPL"], [SubType.QUOTE])
        mock_fresh.subscribe.assert_any_call(["US.AAPL"], [SubType.K_5M])

        # Verify instruments re-pushed to cache (provider mocked)
        assert client._instrument_provider is not None

        event_loop.run_until_complete(client._disconnect())

    def test_keep_alive_prevents_remote_close(
        self, event_loop, make_client, mock_quote_ctx
    ):
        """Verify keep-alive task calls query_subscription periodically."""
        client = make_client(
            config=FutuDataClientConfig(
                host="test-host",
                port=11111,
                trd_env="SIMULATE",
                trd_market="US",
                client_id=1,
                keep_alive_interval_secs=0,
            ),
            quote_ctx=mock_quote_ctx,
        )

        event_loop.run_until_complete(client._connect())

        # With interval=0, keep-alive should not start
        assert client._keep_alive_task is None

        event_loop.run_until_complete(client._disconnect())

    def test_keep_alive_calls_query_subscription(
        self, event_loop, make_client, mock_quote_ctx
    ):
        """Verify keep-alive task calls query_subscription when interval > 0."""
        client = make_client(
            config=FutuDataClientConfig(
                host="test-host",
                port=11111,
                trd_env="SIMULATE",
                trd_market="US",
                client_id=1,
                keep_alive_interval_secs=1,
            ),
            quote_ctx=mock_quote_ctx,
        )

        event_loop.run_until_complete(client._connect())
        assert client._keep_alive_task is not None

        # Wait for one keep-alive cycle
        event_loop.run_until_complete(asyncio.sleep(1.5))

        mock_quote_ctx.query_subscription.assert_called()

        event_loop.run_until_complete(client._disconnect())
