"""Unit tests for main.py bootstrap."""

from __future__ import annotations

import asyncio
import logging
import pathlib
from unittest.mock import MagicMock

import pytest
from nautilus_trader.live.node import TradingNode

import sam_trader.main as main_module
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
        monkeypatch.setenv("FUTU_KEEP_ALIVE_INTERVAL_SECS", "900")
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
            assert data_cfg.keep_alive_interval_secs == 900
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


class TestRoutingVenues:
    """Tests for routing venue derivation from FUTU_TRD_MARKET."""

    def test_routing_venues_for_hk_market(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When FUTU_TRD_MARKET=HK, routing contains only HKEX."""
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
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

            data_cfg = node._config.data_clients["FUTU"]
            exec_cfg = node._config.exec_clients["FUTU"]
            assert data_cfg.routing.venues == frozenset({"HKEX"})
            assert exec_cfg.routing.venues == frozenset({"HKEX"})
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_routing_venues_for_us_market(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When FUTU_TRD_MARKET=US, routing contains NASDAQ and NYSE."""
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_TRD_MARKET", "US")
        monkeypatch.setenv("FUTU_UNLOCK_PWD_MD5", "deadbeef")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            data_cfg = node._config.data_clients["FUTU"]
            exec_cfg = node._config.exec_clients["FUTU"]
            assert data_cfg.routing.venues == frozenset({"NASDAQ", "NYSE"})
            assert exec_cfg.routing.venues == frozenset({"NASDAQ", "NYSE"})
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_routing_venues_logs_at_info(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Startup log confirms routing venues at INFO level."""
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_TRD_MARKET", "HK")
        monkeypatch.setenv("FUTU_UNLOCK_PWD_MD5", "deadbeef")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with caplog.at_level(logging.INFO):
                build_trading_node()

            info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
            assert any("Futu routing venues" in m for m in info_msgs)
            assert any("HKEX" in m for m in info_msgs)
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


class TestPositionSnapshotActorWiring:
    """Tests for PositionSnapshotActor config wiring in build_trading_node."""

    def test_position_snapshot_actor_registered_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PositionSnapshotActor is in actors list when env var enabled."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("ACTOR_POSITION_SNAPSHOT_ENABLED", "true")
        monkeypatch.setenv("POSTGRES_HOST", "test-pg-host")
        monkeypatch.setenv("POSTGRES_PORT", "6543")
        monkeypatch.setenv("POSTGRES_DB", "test_db")
        monkeypatch.setenv("POSTGRES_USER", "test_user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "test_pass")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            assert len(actors) == 1
            actor_cfg = actors[0]
            assert (
                actor_cfg.actor_path
                == "sam_trader.actors.position_snapshot:PositionSnapshotActor"
            )
            assert (
                actor_cfg.config_path
                == "sam_trader.actors.position_snapshot:PositionSnapshotActorConfig"
            )
            assert actor_cfg.config["postgres_host"] == "test-pg-host"
            assert actor_cfg.config["postgres_port"] == 6543
            assert actor_cfg.config["postgres_db"] == "test_db"
            assert actor_cfg.config["postgres_user"] == "test_user"
            assert actor_cfg.config["postgres_password"] == "test_pass"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_position_snapshot_actor_disabled_when_env_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PositionSnapshotActor is NOT in actors list when explicitly disabled."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("ACTOR_POSITION_SNAPSHOT_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            assert len(actors) == 0
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_position_snapshot_defaults_to_journal_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ACTOR_POSITION_SNAPSHOT_ENABLED is unset, defaults to
        ACTOR_JOURNAL_ENABLED."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.delenv("ACTOR_POSITION_SNAPSHOT_ENABLED", raising=False)
        monkeypatch.setenv("ACTOR_JOURNAL_ENABLED", "true")
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "false")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "false")
        monkeypatch.setenv("ACTOR_REJECTION_MONITOR_ENABLED", "false")
        monkeypatch.setenv("ACTOR_REALIZED_PNL_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            actor_paths = [a.actor_path for a in actors]
            assert len(actors) == 2
            assert "sam_trader.actors.trade_journal:TradeJournalActor" in actor_paths
            assert (
                "sam_trader.actors.position_snapshot:PositionSnapshotActor"
                in actor_paths
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_position_snapshot_defaults_to_journal_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When both env vars are unset, actor is disabled."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.delenv("ACTOR_POSITION_SNAPSHOT_ENABLED", raising=False)
        monkeypatch.delenv("ACTOR_JOURNAL_ENABLED", raising=False)
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "false")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "false")
        monkeypatch.setenv("ACTOR_REJECTION_MONITOR_ENABLED", "false")
        monkeypatch.setenv("ACTOR_REALIZED_PNL_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            assert len(actors) == 0
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestLiveRiskEngineWiring:
    """Tests for LiveRiskEngine config wiring in build_trading_node."""

    def test_live_risk_engine_config_wired(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LiveRiskEngineConfig is passed to TradingNodeConfig with env values."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("RISK_MAX_ORDER_SUBMIT_RATE", "50/00:00:01")
        monkeypatch.setenv("RISK_MAX_ORDER_MODIFY_RATE", "20/00:00:05")
        monkeypatch.setenv("RISK_MAX_NOTIONAL_PER_ORDER", '{"AAPL.NASDAQ": 100000}')
        monkeypatch.setenv("RISK_BYPASS", "1")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            risk_cfg = node._config.risk_engine
            assert risk_cfg is not None
            assert risk_cfg.bypass is True
            assert risk_cfg.max_order_submit_rate == "50/00:00:01"
            assert risk_cfg.max_order_modify_rate == "20/00:00:05"
            assert risk_cfg.max_notional_per_order == {"AAPL.NASDAQ": 100000}
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_live_risk_engine_defaults_when_no_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """LiveRiskEngineConfig uses Nautilus defaults when env vars are unset."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.delenv("RISK_MAX_ORDER_SUBMIT_RATE", raising=False)
        monkeypatch.delenv("RISK_MAX_ORDER_MODIFY_RATE", raising=False)
        monkeypatch.delenv("RISK_MAX_NOTIONAL_PER_ORDER", raising=False)
        monkeypatch.delenv("RISK_BYPASS", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            risk_cfg = node._config.risk_engine
            assert risk_cfg is not None
            assert risk_cfg.bypass is False
            assert risk_cfg.max_order_submit_rate == "100/00:00:01"
            assert risk_cfg.max_order_modify_rate == "100/00:00:01"
            assert risk_cfg.max_notional_per_order == {}
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_live_risk_engine_empty_notional_skips_json_parse(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty RISK_MAX_NOTIONAL_PER_ORDER results in empty dict."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("RISK_MAX_NOTIONAL_PER_ORDER", "")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            risk_cfg = node._config.risk_engine
            assert risk_cfg is not None
            assert risk_cfg.max_notional_per_order == {}
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestEmptyBundlesWarning:
    """Tests for CRITICAL log when bundles.yaml is empty and FUTU is enabled."""

    def test_critical_log_when_empty_bundles_and_futu_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A CRITICAL log is emitted when zero bundles are loaded but
        FUTU_ENABLED=true.
        """
        empty_bundles = tmp_path / "empty_bundles.yaml"
        empty_bundles.write_text("bundles: []\n")

        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "22222")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("FUTU_TRD_MARKET", "US")
        monkeypatch.setenv("FUTU_UNLOCK_PWD_MD5", "deadbeef")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", str(empty_bundles))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with caplog.at_level(logging.CRITICAL):
                node = build_trading_node()

            assert isinstance(node, TradingNode)
            assert node._config.strategies == []

            critical_records = [
                r for r in caplog.records if r.levelno == logging.CRITICAL
            ]
            assert len(critical_records) == 1
            assert (
                "ZERO strategies loaded but FUTU is enabled"
                in critical_records[0].message
            )
            assert "bundles.example.yaml" in critical_records[0].message
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_no_critical_log_when_empty_bundles_and_futu_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No CRITICAL log when zero bundles are loaded and FUTU_ENABLED=false."""
        empty_bundles = tmp_path / "empty_bundles.yaml"
        empty_bundles.write_text("bundles: []\n")

        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", str(empty_bundles))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with caplog.at_level(logging.CRITICAL):
                node = build_trading_node()

            assert isinstance(node, TradingNode)
            critical_records = [
                r for r in caplog.records if r.levelno == logging.CRITICAL
            ]
            assert len(critical_records) == 0
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class TestStateLoadGuard:
    """Tests for stale-order guard when no execution clients are available."""

    def test_skip_state_load_when_no_exec_clients(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When load_state=True but no exec clients: CRITICAL log + load_state=False."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "true")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "true")
        monkeypatch.setenv("REDIS_HOST", "test-redis")
        monkeypatch.setenv("REDIS_PORT", "6380")
        # Avoid real TradingNode instantiation that connects to Redis
        monkeypatch.setattr(
            main_module,
            "TradingNode",
            lambda config: MagicMock(_config=config),
        )

        with caplog.at_level(logging.CRITICAL):
            node = build_trading_node()

        assert node._config.load_state is False
        assert node._config.save_state is True  # save_state unaffected

        critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_records) == 1
        assert "STATE LOAD ABORTED" in critical_records[0].message
        assert "ZERO execution clients" in critical_records[0].message


class TestMarketConfigPropagation:
    """Tests that MARKET env var drives config propagation in main.py."""

    # ── Routing Venues ────────────────────────────────────────────

    def test_routing_venues_from_market_config_hk(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=HK → routing venues come from market_config (HKEX only)."""
        monkeypatch.setenv("MARKET", "HK")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        # Clear backward-compat env vars so MARKET is the sole driver
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            data_cfg = node._config.data_clients["FUTU"]
            exec_cfg = node._config.exec_clients["FUTU"]
            assert data_cfg.routing.venues == frozenset({"HKEX"})
            assert exec_cfg.routing.venues == frozenset({"HKEX"})
            # Verify trd_market was derived from market config too
            assert data_cfg.trd_market == "HK"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_routing_venues_from_market_config_us(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=US → routing venues come from market_config (NASDAQ+NYSE)."""
        monkeypatch.setenv("MARKET", "US")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            data_cfg = node._config.data_clients["FUTU"]
            exec_cfg = node._config.exec_clients["FUTU"]
            assert data_cfg.routing.venues == frozenset({"NASDAQ", "NYSE"})
            assert exec_cfg.routing.venues == frozenset({"NASDAQ", "NYSE"})
            assert data_cfg.trd_market == "US"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    # ── Health Actor Timezone ─────────────────────────────────────

    def test_health_actor_timezone_from_market_config_hk(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=HK → health actor gets Asia/Hong_Kong timezone."""
        monkeypatch.setenv("MARKET", "HK")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "true")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "false")
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            health_actor = [a for a in actors if "HealthMonitor" in a.actor_path][0]
            assert health_actor.config["market_timezone"] == "Asia/Hong_Kong"
            assert health_actor.config["market"] == "HK"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_health_actor_timezone_from_market_config_us(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=US → health actor gets America/New_York timezone."""
        monkeypatch.setenv("MARKET", "US")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "true")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "false")
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            health_actor = [a for a in actors if "HealthMonitor" in a.actor_path][0]
            assert health_actor.config["market_timezone"] == "America/New_York"
            assert health_actor.config["market"] == "US"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    # ── Bar Resub Timezone ────────────────────────────────────────

    def test_bar_resub_timezone_from_market_config_hk(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=HK → bar resub actor gets Asia/Hong_Kong timezone."""
        monkeypatch.setenv("MARKET", "HK")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "false")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "true")
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            bar_actor = [a for a in actors if "BarResubscription" in a.actor_path][0]
            assert bar_actor.config["market_open_tz"] == "Asia/Hong_Kong"
            assert bar_actor.config["market"] == "HK"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_bar_resub_timezone_from_market_config_us(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=US → bar resub actor gets America/New_York timezone."""
        monkeypatch.setenv("MARKET", "US")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "false")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "true")
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            actors = node._config.actors
            bar_actor = [a for a in actors if "BarResubscription" in a.actor_path][0]
            assert bar_actor.config["market_open_tz"] == "America/New_York"
            assert bar_actor.config["market"] == "US"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    # ── IB Enablement ─────────────────────────────────────────────

    def test_ib_registered_when_market_us(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=US → IB is registered (ib_enabled=true from market config)."""
        monkeypatch.setenv("MARKET", "US")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.delenv("IB_ENABLED", raising=False)
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            assert "IB" in node._config.data_clients
            assert "IB" in node._config.exec_clients
            assert "IB" in node._builder._data_factories
            assert "IB" in node._builder._exec_factories
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_ib_not_registered_when_market_hk(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MARKET=HK → IB is NOT registered (ib_enabled=false from market config)."""
        monkeypatch.setenv("MARKET", "HK")
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("FUTU_OPEND_HOST", "test-futu-host")
        monkeypatch.setenv("FUTU_OPEND_PORT", "11111")
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.delenv("IB_ENABLED", raising=False)
        monkeypatch.delenv("FUTU_TRD_MARKET", raising=False)
        monkeypatch.delenv("HEALTH_MONITOR_MARKET", raising=False)
        monkeypatch.delenv("BAR_RESUB_MARKET", raising=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            node = build_trading_node()

            # IB should NOT be registered for HK market
            assert "IB" not in node._config.data_clients
            assert "IB" not in node._config.exec_clients
            assert "IB" not in node._builder._data_factories
            assert "IB" not in node._builder._exec_factories
            # Futu should still be registered
            assert "FUTU" in node._config.data_clients
        finally:
            loop.close()
            asyncio.set_event_loop(None)
