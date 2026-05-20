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
