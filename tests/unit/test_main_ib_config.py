"""Unit tests for IBKR config wiring in main.py."""

from __future__ import annotations

import asyncio

import pytest
from nautilus_trader.live.node import TradingNode

from sam_trader.main import build_trading_node


class TestIBConfigWiring:
    """Tests that IBKR config is correctly wired from SamTraderConfig into main.py."""

    def test_ib_config_loads(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """IB configs and factories are registered when IB_ENABLED=true."""
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "TSLA.NASDAQ,AAPL.NASDAQ")
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

            # Configs injected into TradingNodeConfig
            assert "IB" in node._config.data_clients
            assert "IB" in node._config.exec_clients

            data_cfg = node._config.data_clients["IB"]
            exec_cfg = node._config.exec_clients["IB"]

            assert data_cfg.ibg_host == "test-ib-gateway"
            assert data_cfg.ibg_port == 4001
            assert data_cfg.ibg_client_id == 42
            assert data_cfg.market_data_type == 1  # REALTIME

            assert exec_cfg.ibg_host == "test-ib-gateway"
            assert exec_cfg.ibg_port == 4001
            assert exec_cfg.ibg_client_id == 42
            assert exec_cfg.account_id == "DU12345"

            # Factories registered on the node builder
            assert "IB" in node._builder._data_factories
            assert "IB" in node._builder._exec_factories

            from nautilus_trader.adapters.interactive_brokers.factories import (
                InteractiveBrokersLiveDataClientFactory,
                InteractiveBrokersLiveExecClientFactory,
            )

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

    def test_ib_disabled_flag(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """IB factories are NOT registered when IB_ENABLED=false."""
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
            assert "IB" not in node._config.data_clients
            assert "IB" not in node._config.exec_clients
            assert "IB" not in node._builder._data_factories
            assert "IB" not in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_invalid_market_data_type_logs_warning_and_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Invalid IB_MARKET_DATA_TYPE logs WARNING and falls back to REALTIME."""
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "TSLA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "INVALID")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with caplog.at_level("WARNING", logger="sam_trader.main"):
                node = build_trading_node()

            assert isinstance(node, TradingNode)
            data_cfg = node._config.data_clients["IB"]
            assert data_cfg.market_data_type == 1  # REALTIME fallback

            warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any(
                "IB_MARKET_DATA_TYPE='INVALID' is not a valid MarketDataTypeEnum value"
                in r.message
                for r in warning_records
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_delayed_market_data_type_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Valid DELAYED IB_MARKET_DATA_TYPE uses DELAYED with no WARNING."""
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "TSLA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "DELAYED")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with caplog.at_level("WARNING", logger="sam_trader.main"):
                node = build_trading_node()

            assert isinstance(node, TradingNode)
            data_cfg = node._config.data_clients["IB"]
            assert data_cfg.market_data_type == 3  # DELAYED

            warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
            assert not any("MarketDataTypeEnum" in r.message for r in warning_records)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_read_only_no_exec_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When IB_READ_ONLY_API=true, exec client is not registered."""
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
            assert "IB" in node._config.data_clients
            # Exec client should NOT be registered in read-only mode
            assert "IB" not in node._config.exec_clients
            assert "IB" not in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)
