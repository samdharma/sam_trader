"""Unit tests for main.py bootstrap."""

from __future__ import annotations

import asyncio

import pytest
from nautilus_trader.live.node import TradingNode

from sam_trader.main import build_trading_node


class TestMainBootstrap:
    def test_build_trading_node_no_brokers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that TradingNode builds successfully with no brokers enabled."""
        # Ensure both brokers are disabled
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        # Point bundles to non-existent path so loader fails gracefully
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        # Disable state persistence to avoid Redis dependency
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            assert node.trader_id.value == "sam_trader-001"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_build_trading_node_config_loaded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that config values are properly loaded into the TradingNode."""
        monkeypatch.setenv("TRADER_ID", "test_node")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
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
            assert node.trader_id.value == "test_node-001"
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestFutuFactoryWiring:
    """Tests for Futu factory registration in build_trading_node."""

    def test_futu_factories_registered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Futu factories and configs are registered when FUTU_ENABLED=true."""
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "22222")
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
        monkeypatch.setenv("FUTU_TRD_MARKET", "HK")
        monkeypatch.setenv("FUTU_UNLOCK_PWD_MD5", "deadbeef")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)

            # Configs injected into TradingNodeConfig
            assert "FUTU" in node._config.data_clients
            assert "FUTU" in node._config.exec_clients

            data_cfg = node._config.data_clients["FUTU"]
            exec_cfg = node._config.exec_clients["FUTU"]
            assert data_cfg.host == "test-futu-host"
            assert data_cfg.port == 22222
            assert data_cfg.trd_env == "REAL"
            assert data_cfg.trd_market == "HK"
            assert exec_cfg.host == "test-futu-host"
            assert exec_cfg.port == 22222
            assert exec_cfg.trd_env == "REAL"
            assert exec_cfg.trd_market == "HK"
            assert exec_cfg.unlock_pwd_md5 == "deadbeef"

            # Factories registered on the node builder
            assert "FUTU" in node._builder._data_factories
            assert "FUTU" in node._builder._exec_factories

            from sam_trader.adapters.futu.factories import (
                FutuLiveDataClientFactory,
                FutuLiveExecClientFactory,
            )

            assert node._builder._data_factories["FUTU"] is FutuLiveDataClientFactory
            assert node._builder._exec_factories["FUTU"] is FutuLiveExecClientFactory
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_futu_disabled_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Futu factories are NOT registered when FUTU_ENABLED=false."""
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            assert "FUTU" not in node._config.data_clients
            assert "FUTU" not in node._config.exec_clients
            assert "FUTU" not in node._builder._data_factories
            assert "FUTU" not in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)
