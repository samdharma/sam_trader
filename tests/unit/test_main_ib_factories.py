"""Unit tests for IBKR factory registration in main.py."""

from __future__ import annotations

import asyncio

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.live.node import TradingNode

from sam_trader.main import build_trading_node


class TestIBFactoryRegistration:
    """Tests that IBKR factories are registered correctly in build_trading_node."""

    def test_ib_factories_registered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both IB data and exec factories are registered when IB_ENABLED=true."""
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "TSLA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            assert "IB" in node._builder._data_factories
            assert "IB" in node._builder._exec_factories
            assert (
                node._builder._data_factories["IB"]
                is InteractiveBrokersLiveDataClientFactory
            )
            assert (
                node._builder._exec_factories["IB"]
                is InteractiveBrokersLiveExecClientFactory
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_factories_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No IB factories are registered when IB_ENABLED=false."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            assert "IB" not in node._builder._data_factories
            assert "IB" not in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_exec_factory_not_registered_when_read_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Data factory is registered but exec factory is omitted in read-only mode."""
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "TSLA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "true")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            assert "IB" in node._builder._data_factories
            assert "IB" not in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)
