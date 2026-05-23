"""Unit tests for IBKR instrument provider wiring in main.py."""

from __future__ import annotations

import asyncio

import pytest
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.config import (
    SymbologyMethod as IBSymbologyMethod,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId

from sam_trader.main import build_trading_node


class TestIBProviderWiring:
    """Tests that the IB instrument provider is wired in build_trading_node."""

    def test_ib_provider_registered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """IB instrument provider config is registered when IB_ENABLED=true."""
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
            assert "IB" in node._config.data_clients
            assert "IB" in node._config.exec_clients

            data_cfg = node._config.data_clients["IB"]
            exec_cfg = node._config.exec_clients["IB"]

            # Data client has instrument provider config
            assert isinstance(
                data_cfg.instrument_provider,
                InteractiveBrokersInstrumentProviderConfig,
            )
            assert (
                data_cfg.instrument_provider.symbology_method
                == IBSymbologyMethod.IB_SIMPLIFIED
            )
            assert data_cfg.instrument_provider.load_ids is not None
            load_ids = set(data_cfg.instrument_provider.load_ids)
            assert InstrumentId.from_str("TSLA.NASDAQ") in load_ids
            assert InstrumentId.from_str("AAPL.NASDAQ") in load_ids

            # Exec client shares the same instrument provider config
            assert isinstance(
                exec_cfg.instrument_provider,
                InteractiveBrokersInstrumentProviderConfig,
            )
            assert (
                exec_cfg.instrument_provider.symbology_method
                == IBSymbologyMethod.IB_SIMPLIFIED
            )
            assert exec_cfg.instrument_provider.load_ids is not None
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_provider_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No IB instrument provider when IB_ENABLED=false."""
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
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_dual_venue_no_conflict(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Futu and IB instrument providers coexist without conflicts."""
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_TRD_MARKET", "US")
        monkeypatch.setenv("FUTU_UNLOCK_PWD_MD5", "")
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "NVDA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert isinstance(node, TradingNode)

            # Both venues present
            assert "FUTU" in node._config.data_clients
            assert "IB" in node._config.data_clients
            assert "FUTU" in node._config.exec_clients
            assert "IB" in node._config.exec_clients

            # Futu has its own config with a default InstrumentProviderConfig,
            # not an InteractiveBrokersInstrumentProviderConfig.
            futu_data = node._config.data_clients["FUTU"]
            assert hasattr(futu_data, "instrument_provider")
            assert not isinstance(
                futu_data.instrument_provider,
                InteractiveBrokersInstrumentProviderConfig,
            )

            # IB has instrument provider config
            ib_data = node._config.data_clients["IB"]
            assert isinstance(
                ib_data.instrument_provider,
                InteractiveBrokersInstrumentProviderConfig,
            )
            assert ib_data.instrument_provider.load_ids is not None
            assert InstrumentId.from_str("NVDA.NASDAQ") in set(
                ib_data.instrument_provider.load_ids
            )

            # Factory registrations are independent
            assert "FUTU" in node._builder._data_factories
            assert "IB" in node._builder._data_factories
            assert "FUTU" in node._builder._exec_factories
            assert "IB" in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)
