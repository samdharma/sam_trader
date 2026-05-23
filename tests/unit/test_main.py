"""Unit tests for main.py bootstrap."""

from __future__ import annotations

import asyncio
import pathlib

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


class TestDualVenueNoCrossContamination:
    """Tests that Futu and IB venues remain cleanly separated."""

    def test_dual_venue_no_cross_contamination(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both venues enabled: configs clean, bundles filtered, no leakage."""
        bundles_yaml = """\
bundles:
  - id: "tsla-echo-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
  - id: "nvda-echo-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
"""
        bundles_path = tmp_path / "bundles.yaml"
        bundles_path.write_text(bundles_yaml)

        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_TRD_MARKET", "US")
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "NVDA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("BUNDLES_PATH", str(bundles_path))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)

            # Both venue configs present
            assert "FUTU" in node._config.data_clients
            assert "IB" in node._config.data_clients
            assert "FUTU" in node._config.exec_clients
            assert "IB" in node._config.exec_clients

            # Both factories registered
            assert "FUTU" in node._builder._data_factories
            assert "IB" in node._builder._data_factories
            assert "FUTU" in node._builder._exec_factories
            assert "IB" in node._builder._exec_factories

            # Configs are venue-specific: Futu has no IB fields
            futu_data = node._config.data_clients["FUTU"]
            assert hasattr(futu_data, "host")
            assert hasattr(futu_data, "trd_env")
            assert not hasattr(futu_data, "ibg_host")

            # Configs are venue-specific: IB has no Futu fields
            ib_data = node._config.data_clients["IB"]
            assert hasattr(ib_data, "ibg_host")
            assert not hasattr(ib_data, "host")
            assert not hasattr(ib_data, "trd_env")

            # Both bundles loaded as strategies
            strategies = node._config.strategies
            assert len(strategies) == 2

            futu_strategy = [s for s in strategies if s.config.get("venue") == "FUTU"][
                0
            ]
            ib_strategy = [s for s in strategies if s.config.get("venue") == "IB"][0]

            # Futu bundle has futu_code, no exchange
            assert futu_strategy.config.get("futu_code") == "US.TSLA"
            assert "exchange" not in futu_strategy.config

            # IB bundle has SMART exchange, no futu_code
            assert ib_strategy.config.get("exchange") == "SMART"
            assert "futu_code" not in ib_strategy.config
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_futu_bundles_filtered_when_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Futu bundles are skipped when FUTU_ENABLED=false."""
        bundles_yaml = """\
bundles:
  - id: "tsla-echo-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
  - id: "nvda-echo-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
"""
        bundles_path = tmp_path / "bundles.yaml"
        bundles_path.write_text(bundles_yaml)

        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "NVDA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("BUNDLES_PATH", str(bundles_path))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            # Only IB bundle loaded
            strategies = node._config.strategies
            assert len(strategies) == 1
            assert strategies[0].config.get("venue") == "IB"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_bundles_filtered_when_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """IB bundles are skipped when IB_ENABLED=false."""
        bundles_yaml = """\
bundles:
  - id: "tsla-echo-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
  - id: "nvda-echo-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
"""
        bundles_path = tmp_path / "bundles.yaml"
        bundles_path.write_text(bundles_yaml)

        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_TRD_MARKET", "US")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", str(bundles_path))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)
            # Only Futu bundle loaded
            strategies = node._config.strategies
            assert len(strategies) == 1
            assert strategies[0].config.get("venue") == "FUTU"
        finally:
            loop.close()
            asyncio.set_event_loop(None)
