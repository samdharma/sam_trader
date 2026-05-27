"""Unit tests for Futu live client factories."""

from __future__ import annotations

import asyncio
import os
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


class TestExecClientFactoryAccountId:
    """Tests for account_id construction via FUTU_ACCOUNT_ID env var."""

    def test_uses_futu_account_id_env_when_set(self, exec_config, factory_deps):
        """Factory should use FUTU_ACCOUNT_ID env var for account_id when set."""
        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        with (
            patch.dict(os.environ, {"FUTU_ACCOUNT_ID": "234387941"}),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ),
        ):
            client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=exec_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert client._account_id == AccountId("FUTU-234387941")

    def test_falls_back_to_client_id_when_env_not_set(self, exec_config, factory_deps):
        """Factory should fall back to config.client_id when FUTU_ACCOUNT_ID unset."""
        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        with (
            patch.dict(os.environ, {"FUTU_ACCOUNT_ID": ""}),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ),
        ):
            client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=exec_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert client._account_id == AccountId("FUTU-1")

    def test_uses_env_when_config_client_id_different(self, exec_config, factory_deps):
        """FUTU_ACCOUNT_ID env var takes precedence over config.client_id."""
        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        exec_config_hk = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            client_id=7,
        )

        with (
            patch.dict(os.environ, {"FUTU_ACCOUNT_ID": "234387941"}),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ),
        ):
            client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=exec_config_hk,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        # Should use env var, not config.client_id=7
        assert client._account_id == AccountId("FUTU-234387941")


class TestPerMarketFactoryCoexistence:
    """Verify factories correctly isolate US and HK configs for multi-market support."""

    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def factory_deps(self, event_loop):
        return {
            "loop": event_loop,
            "name": "FUTU-1",
            "msgbus": TestComponentStubs.msgbus(),
            "cache": TestComponentStubs.cache(),
            "clock": LiveClock(),
        }

    def test_exec_factory_passes_trd_market_hk_to_trade_context(
        self, factory_deps
    ) -> None:
        """Exec factory with trd_market='HK' passes 'HK' to trade context getter."""
        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        hk_config = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            client_id=1,
        )

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
            FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=hk_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        # Quote context getter does NOT receive market (shared across markets)
        mock_get_quote.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
        )
        # Trade context getter DOES receive market for per-market isolation
        mock_get_trade.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
            trd_market="HK",
        )

    def test_exec_factory_hk_gets_synthetic_venue(self, factory_deps) -> None:
        """HK market exec client gets FUTU_HK venue (not FUTU).

        This allows Nautilus to register multiple Futu exec clients
        simultaneously without venue collision.
        """
        from nautilus_trader.model.identifiers import Venue

        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        hk_config = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            client_id=1,
        )

        with (
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ),
        ):
            client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=hk_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert client.venue == Venue("FUTU_HK")

    def test_exec_factory_us_gets_futu_venue(self, factory_deps) -> None:
        """US market exec client gets FUTU venue (backward-compatible default)."""
        from nautilus_trader.model.identifiers import Venue

        mock_quote_ctx = MagicMock()
        mock_trade_ctx = MagicMock()

        us_config = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="US",
            client_id=1,
        )

        with (
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories.get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ),
        ):
            client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=us_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert client.venue == Venue("FUTU")

    def test_data_factory_does_not_pass_market_to_quote_context(
        self, factory_deps
    ) -> None:
        """Data factory never passes market to quote context — quote is shared."""
        mock_quote_ctx = MagicMock()

        hk_data_config = FutuDataClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            client_id=1,
        )

        with patch(
            "sam_trader.adapters.futu.factories.get_cached_futu_quote_context",
            return_value=mock_quote_ctx,
        ) as mock_get_quote:
            FutuLiveDataClientFactory.create(
                name=factory_deps["name"],
                config=hk_data_config,
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        # Quote context getter does NOT include market parameter
        mock_get_quote.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
        )
