"""Unit tests for Redis CacheConfig wiring in main.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nautilus_trader.cache.config import CacheConfig

import sam_trader.main as main_module
from sam_trader.main import build_trading_node


class _FakeNode:
    """Lightweight stand-in for TradingNode.

    Captures config without instantiating the real Nautilus kernel (which
    would attempt Redis connections when cache.database is set).
    """

    def __init__(self, config: object) -> None:
        self._config = config


class TestCacheConfigWiring:
    """Tests that Redis CacheConfig is correctly wired from env vars."""

    def test_redis_cache_config_wired_when_state_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CacheConfig with DatabaseConfig created when state persistence enabled."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "true")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "true")
        monkeypatch.setenv("REDIS_HOST", "test-redis")
        monkeypatch.setenv("REDIS_PORT", "6380")
        monkeypatch.setenv("REDIS_PASSWORD", "secret123")
        monkeypatch.setattr(main_module, "TradingNode", _FakeNode)

        node = build_trading_node()

        assert node._config.cache is not None
        assert isinstance(node._config.cache, CacheConfig)
        assert node._config.cache.database is not None
        assert node._config.cache.database.type == "redis"
        assert node._config.cache.database.host == "test-redis"
        assert node._config.cache.database.port == 6380
        assert node._config.cache.database.password == "secret123"
        assert node._config.load_state is True
        assert node._config.save_state is True

    def test_no_cache_config_when_state_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CacheConfig is None when state persistence is disabled."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setattr(main_module, "TradingNode", _FakeNode)

        node = build_trading_node()

        assert node._config.cache is None
        assert node._config.load_state is False
        assert node._config.save_state is False

    def test_redis_password_empty_becomes_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty REDIS_PASSWORD is converted to None in DatabaseConfig."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "true")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "true")
        monkeypatch.setenv("REDIS_PASSWORD", "")
        monkeypatch.setattr(main_module, "TradingNode", _FakeNode)

        node = build_trading_node()

        assert node._config.cache is not None
        assert node._config.cache.database is not None
        assert node._config.cache.database.password is None

    def test_load_state_save_state_partial_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only save_state enabled still creates CacheConfig with correct flags."""
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("FUTU_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", "config/nonexistent_bundles.yaml")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "true")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setattr(main_module, "TradingNode", _FakeNode)

        node = build_trading_node()

        assert node._config.cache is not None
        assert node._config.load_state is False
        assert node._config.save_state is True


class TestMainGracefulShutdown:
    """Tests that main() follows Nautilus graceful shutdown pattern."""

    def test_main_disposes_node_after_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """main() calls node.dispose() after node.run() returns normally."""
        mock_node = MagicMock()
        monkeypatch.setattr(main_module, "build_trading_node", lambda: mock_node)
        monkeypatch.setattr(
            main_module, "RestartSubscriber", lambda node, cfg: MagicMock()
        )

        main_module.main()

        mock_node.build.assert_called_once()
        mock_node.run.assert_called_once()
        mock_node.dispose.assert_called_once()

    def test_main_disposes_node_even_on_run_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """main() calls node.dispose() even when node.run() raises an exception."""
        mock_node = MagicMock()
        mock_node.run.side_effect = RuntimeError("boom")
        monkeypatch.setattr(main_module, "build_trading_node", lambda: mock_node)
        monkeypatch.setattr(
            main_module, "RestartSubscriber", lambda node, cfg: MagicMock()
        )

        with pytest.raises(RuntimeError, match="boom"):
            main_module.main()

        mock_node.build.assert_called_once()
        mock_node.run.assert_called_once()
        mock_node.dispose.assert_called_once()
