"""Integration test for full bootstrap without brokers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from nautilus_trader.live.node import TradingNode

from sam_trader.config import SamTraderConfig
from sam_trader.main import build_trading_node


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file and return key-value pairs."""
    env_vars: dict[str, str] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                env_vars[key] = value
    return env_vars


@pytest.mark.integration
class TestBootstrapNoBrokers:
    def test_full_bootstrap_no_brokers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Full bootstrap: .env.example → build_trading_node → node.build()."""
        # Load .env.example into environment
        dotenv_path = Path(".env.example")
        assert dotenv_path.exists(), ".env.example must exist"
        env_vars = _load_dotenv(dotenv_path)

        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        # Disable state persistence to avoid Redis dependency in test
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        # Point bundles to non-existent path so loader fails gracefully
        monkeypatch.setenv("BUNDLES_PATH", str(tmp_path / "nonexistent_bundles.yaml"))

        # 1. SamTraderConfig loads from .env.example defaults
        cfg = SamTraderConfig.from_env()
        assert cfg.trader_id == "sam_trader"
        assert cfg.environment == "paper"
        assert cfg.log_level == "INFO"
        assert cfg.ib_enabled is False
        assert cfg.futu_enabled is False
        assert cfg.ib_gateway_host == "sam-ib-gateway"
        assert cfg.ib_gateway_port == 4004
        assert cfg.futu_opend_host == "sam-futu-opend"
        assert cfg.futu_opend_port == 11111
        assert cfg.futu_trd_env == "SIMULATE"
        assert cfg.futu_trd_market == "US"
        assert cfg.postgres_host == "sam-postgres"
        assert cfg.postgres_port == 5432
        assert cfg.redis_host == "sam-redis"
        assert cfg.redis_port == 6379

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # 2. build_trading_node() returns TradingNode without errors
            node = build_trading_node()
            assert isinstance(node, TradingNode)

            # 3. node.build() succeeds (no clients registered)
            node.build()
            assert node.is_built() is True

            # 4. TradingNode readiness implied by successful build
            assert node.is_running() is False  # Not yet running, but built
        finally:
            loop.close()
            asyncio.set_event_loop(None)
