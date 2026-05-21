"""Unit tests for Futu live client factories."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.common.component import LiveClock
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.adapters.futu.config import FutuDataClientConfig, FutuExecClientConfig
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.execution import FutuLiveExecutionClient
from sam_trader.adapters.futu.factories import (
    FutuLiveDataClientFactory,
    FutuLiveExecClientFactory,
    _get_shared_quote_context,
    _get_shared_trade_context,
)


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def data_config() -> FutuDataClientConfig:
    """Return a standard Futu data client config."""
    return FutuDataClientConfig(
        host="test-host",
        port=11111,
        trd_env="SIMULATE",
        trd_market="US",
        client_id=1,
    )


@pytest.fixture
def exec_config() -> FutuExecClientConfig:
    """Return a standard Futu execution client config."""
    return FutuExecClientConfig(
        host="test-host",
        port=11111,
        trd_env="SIMULATE",
        trd_market="US",
        client_id=1,
    )


@pytest.fixture
def factory_deps(event_loop):
    """Return common factory dependencies."""
    return {
        "loop": event_loop,
        "name": "FUTU-1",
        "msgbus": TestComponentStubs.msgbus(),
        "cache": TestComponentStubs.cache(),
        "clock": LiveClock(),
    }


class TestFutuLiveDataClientFactory:
    """Tests for FutuLiveDataClientFactory."""

    def test_create_data_client(self, data_config, factory_deps):
        """Factory should create a FutuLiveDataClient with shared quote context."""
        mock_quote_ctx = MagicMock()

        with patch(
            "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
            return_value=mock_quote_ctx,
        ) as mock_get_quote:
            client = FutuLiveDataClientFactory.create(
                name=factory_deps["name"],
                config=data_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert isinstance(client, FutuLiveDataClient)
        assert client._quote_ctx is mock_quote_ctx
        mock_get_quote.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
        )


class TestFutuLiveExecClientFactory:
    """Tests for FutuLiveExecClientFactory."""

    def test_create_exec_client(self, exec_config, factory_deps):
        """Factory should create a FutuLiveExecutionClient with shared trade context."""
        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        with (
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ) as mock_get_quote,
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ) as mock_get_trade,
        ):
            client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=exec_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert isinstance(client, FutuLiveExecutionClient)
        assert client._trade_ctx is mock_trade_ctx
        mock_get_quote.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
        )
        mock_get_trade.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
            trd_market="US",
        )
        assert client._account_id == AccountId("FUTU-1")


class TestSharedContext:
    """Tests for the shared client caching pattern."""

    def test_shared_quote_context(self, data_config):
        """_get_shared_quote_context should return the same context for same config."""
        mock_ctx = MagicMock()

        with patch(
            "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
            return_value=mock_ctx,
        ) as mock_get:
            ctx1 = _get_shared_quote_context(data_config)
            ctx2 = _get_shared_quote_context(data_config)

        assert ctx1 is mock_ctx
        assert ctx2 is mock_ctx
        # The underlying cache function is called each time,
        # but returns the same cached object
        assert mock_get.call_count == 2
        mock_get.assert_called_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
        )

    def test_shared_trade_context(self, exec_config):
        """_get_shared_trade_context should return the same context for same config."""
        mock_ctx = MagicMock()

        with patch(
            "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
            return_value=mock_ctx,
        ) as mock_get:
            ctx1 = _get_shared_trade_context(exec_config)
            ctx2 = _get_shared_trade_context(exec_config)

        assert ctx1 is mock_ctx
        assert ctx2 is mock_ctx
        assert mock_get.call_count == 2
        mock_get.assert_called_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
            trd_market="US",
        )

    def test_factories_share_quote_context(
        self, data_config, exec_config, factory_deps
    ):
        """Data and exec factories should share the same quote context for same key."""
        shared_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        with (
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=shared_quote_ctx,
            ) as mock_get_quote,
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ),
        ):
            data_client = FutuLiveDataClientFactory.create(
                name=factory_deps["name"],
                config=data_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )
            exec_client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=exec_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        # Both factories should have called get_cached_futu_quote_context
        assert mock_get_quote.call_count == 2
        # The underlying connection cache ensures the same object is returned
        assert data_client._quote_ctx is shared_quote_ctx
        # Exec factory also creates an instrument provider with the same quote context
        assert exec_client._trade_ctx is mock_trade_ctx
