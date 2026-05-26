"""Unit tests for SamTraderConfig."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sam_trader.config import SamTraderConfig


class TestSamTraderConfig:
    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that from_env() returns sensible defaults when no env vars are set."""
        # Clear all relevant env vars
        for key in (
            "TRADER_ID",
            "SAM_ENV",
            "LOG_LEVEL",
            "IB_ENABLED",
            "IB_GATEWAY_HOST",
            "IB_GATEWAY_PORT",
            "IB_GATEWAY_CLIENT_ID",
            "IB_ACCOUNT_ID",
            "IB_SYMBOLS",
            "IB_READ_ONLY_API",
            "IB_MARKET_DATA_TYPE",
            "FUTU_ENABLED",
            "FUTU_OPEND_HOST",
            "FUTU_OPEND_PORT",
            "FUTU_TRD_ENV",
            "FUTU_TRD_MARKET",
            "FUTU_UNLOCK_PWD_MD5",
            "ACTOR_BAR_RESUB_ENABLED",
            "ACTOR_JOURNAL_ENABLED",
            "ACTOR_HEALTH_ENABLED",
            "ACTOR_POSITION_SNAPSHOT_ENABLED",
            "STATE_SAVE_ENABLED",
            "STATE_LOAD_ENABLED",
            "STATE_SAVE_HANDSHAKE_TIMEOUT",
            "BUNDLES_PATH",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "REDIS_HOST",
            "REDIS_PORT",
            "REDIS_PASSWORD",
            "RISK_MAX_ORDER_SUBMIT_RATE",
            "RISK_MAX_ORDER_MODIFY_RATE",
            "RISK_MAX_NOTIONAL_PER_ORDER",
            "RISK_BYPASS",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = SamTraderConfig.from_env()

        assert cfg.trader_id == "sam_trader"
        assert cfg.environment == "paper"
        assert cfg.log_level == "INFO"
        assert cfg.ib_enabled is False
        assert cfg.ib_gateway_host == "sam-ib-gateway"
        assert cfg.ib_gateway_port == 4004
        assert cfg.ib_client_id == 11
        assert cfg.ib_account_id == ""
        assert cfg.ib_symbols == []
        assert cfg.ib_read_only_api is False
        assert cfg.ib_market_data_type == "REALTIME"
        assert cfg.futu_enabled is False
        assert cfg.futu_opend_host == "sam-futu-opend"
        assert cfg.futu_opend_port == 11111
        assert cfg.futu_trd_env == "SIMULATE"
        assert cfg.futu_trd_market == "US"
        assert cfg.futu_unlock_pwd_md5 == ""
        assert cfg.futu_keep_alive_interval_secs == 1800
        assert cfg.actor_bar_resub_enabled is False
        assert cfg.actor_journal_enabled is False
        assert cfg.actor_health_enabled is False
        assert cfg.actor_position_snapshot_enabled is False
        assert cfg.state_save_enabled is False
        assert cfg.state_load_enabled is False
        assert cfg.state_save_handshake_timeout == 30
        assert cfg.bundles_path == "config/bundles.yaml"
        assert cfg.postgres_host == "sam-postgres"
        assert cfg.postgres_port == 5432
        assert cfg.postgres_db == "sam_trader"
        assert cfg.postgres_user == "sam"
        assert cfg.postgres_password == "sam_secret"
        assert cfg.redis_host == "sam-redis"
        assert cfg.redis_port == 6379
        assert cfg.redis_password == ""
        assert cfg.risk_max_order_submit_rate == "100/00:00:01"
        assert cfg.risk_max_order_modify_rate == "100/00:00:01"
        assert cfg.risk_max_notional_per_order == ""
        assert cfg.risk_bypass is False

    def test_from_env_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that from_env() reads custom env vars correctly."""
        monkeypatch.setenv("TRADER_ID", "test_trader")
        monkeypatch.setenv("SAM_ENV", "live")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("IB_GATEWAY_HOST", "custom-ib")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "99")
        monkeypatch.setenv("IB_ACCOUNT_ID", "U1234567")
        monkeypatch.setenv("IB_SYMBOLS", "AAPL.NASDAQ, TSLA.NASDAQ")
        monkeypatch.setenv("IB_READ_ONLY_API", "1")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "DELAYED")
        monkeypatch.setenv("FUTU_ENABLED", "yes")
        monkeypatch.setenv("FUTU_OPEND_HOST", "custom-futu")
        monkeypatch.setenv("FUTU_OPEND_PORT", "22222")
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
        monkeypatch.setenv("FUTU_TRD_MARKET", "HK")
        monkeypatch.setenv("FUTU_UNLOCK_PWD_MD5", "abc123")
        monkeypatch.setenv("FUTU_KEEP_ALIVE_INTERVAL_SECS", "900")
        monkeypatch.setenv("ACTOR_BAR_RESUB_ENABLED", "true")
        monkeypatch.setenv("ACTOR_JOURNAL_ENABLED", "true")
        monkeypatch.setenv("ACTOR_HEALTH_ENABLED", "true")
        monkeypatch.setenv("ACTOR_POSITION_SNAPSHOT_ENABLED", "true")
        monkeypatch.setenv("STATE_SAVE_ENABLED", "true")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "true")
        monkeypatch.setenv("STATE_SAVE_HANDSHAKE_TIMEOUT", "60")
        monkeypatch.setenv("BUNDLES_PATH", "custom/bundles.yaml")
        monkeypatch.setenv("POSTGRES_HOST", "custom-pg")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_DB", "test_db")
        monkeypatch.setenv("POSTGRES_USER", "test_user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "test_pass")
        monkeypatch.setenv("REDIS_HOST", "custom-redis")
        monkeypatch.setenv("REDIS_PORT", "6380")
        monkeypatch.setenv("REDIS_PASSWORD", "redis_pass")
        monkeypatch.setenv("RISK_MAX_ORDER_SUBMIT_RATE", "50/00:00:05")
        monkeypatch.setenv("RISK_MAX_ORDER_MODIFY_RATE", "20/00:00:10")
        monkeypatch.setenv("RISK_MAX_NOTIONAL_PER_ORDER", '{"USD": 100000}')
        monkeypatch.setenv("RISK_BYPASS", "1")

        cfg = SamTraderConfig.from_env()

        assert cfg.trader_id == "test_trader"
        assert cfg.environment == "live"
        assert cfg.log_level == "DEBUG"
        assert cfg.ib_enabled is True
        assert cfg.ib_gateway_host == "custom-ib"
        assert cfg.ib_gateway_port == 4001
        assert cfg.ib_client_id == 99
        assert cfg.ib_account_id == "U1234567"
        assert cfg.ib_symbols == ["AAPL.NASDAQ", "TSLA.NASDAQ"]
        assert cfg.ib_read_only_api is True
        assert cfg.ib_market_data_type == "DELAYED"
        assert cfg.futu_enabled is True
        assert cfg.futu_opend_host == "custom-futu"
        assert cfg.futu_opend_port == 22222
        assert cfg.futu_trd_env == "REAL"
        assert cfg.futu_trd_market == "HK"
        assert cfg.futu_unlock_pwd_md5 == "abc123"
        assert cfg.futu_keep_alive_interval_secs == 900
        assert cfg.actor_bar_resub_enabled is True
        assert cfg.actor_journal_enabled is True
        assert cfg.actor_health_enabled is True
        assert cfg.actor_position_snapshot_enabled is True
        assert cfg.state_save_enabled is True
        assert cfg.state_load_enabled is True
        assert cfg.state_save_handshake_timeout == 60
        assert cfg.bundles_path == "custom/bundles.yaml"
        assert cfg.postgres_host == "custom-pg"
        assert cfg.postgres_port == 5433
        assert cfg.postgres_db == "test_db"
        assert cfg.postgres_user == "test_user"
        assert cfg.postgres_password == "test_pass"
        assert cfg.redis_host == "custom-redis"
        assert cfg.redis_port == 6380
        assert cfg.redis_password == "redis_pass"
        assert cfg.risk_max_order_submit_rate == "50/00:00:05"
        assert cfg.risk_max_order_modify_rate == "20/00:00:10"
        assert cfg.risk_max_notional_per_order == '{"USD": 100000}'
        assert cfg.risk_bypass is True

    def test_futu_fields_present(self) -> None:
        """Test that all required Futu fields exist on the dataclass."""
        cfg = SamTraderConfig(
            trader_id="sam_trader",
            environment="paper",
            log_level="INFO",
            ib_enabled=False,
            ib_gateway_host="sam-ib-gateway",
            ib_gateway_port=4004,
            ib_client_id=11,
            ib_account_id="",
            ib_symbols=[],
            ib_read_only_api=False,
            ib_market_data_type="REALTIME",
            futu_enabled=True,
            futu_opend_host="sam-futu-opend",
            futu_opend_port=11111,
            futu_trd_env="SIMULATE",
            futu_trd_market="US",
            futu_unlock_pwd_md5="",
            futu_keep_alive_interval_secs=1800,
            actor_bar_resub_enabled=False,
            actor_journal_enabled=False,
            actor_health_enabled=False,
            actor_rejection_monitor_enabled=False,
            actor_realized_pnl_enabled=False,
            actor_position_snapshot_enabled=False,
            state_save_enabled=False,
            state_load_enabled=False,
            state_save_handshake_timeout=30,
            bundles_path="config/bundles.yaml",
            postgres_host="sam-postgres",
            postgres_port=5432,
            postgres_db="sam_trader",
            postgres_user="sam",
            postgres_password="sam_secret",
            redis_host="sam-redis",
            redis_port=6379,
            redis_password="",
            risk_max_order_submit_rate="100/00:00:01",
            risk_max_order_modify_rate="100/00:00:01",
            risk_max_notional_per_order="",
            risk_bypass=False,
        )

        assert cfg.futu_enabled is True
        assert cfg.futu_opend_host == "sam-futu-opend"
        assert cfg.futu_opend_port == 11111
        assert cfg.futu_trd_env == "SIMULATE"
        assert cfg.futu_trd_market == "US"
        assert cfg.futu_unlock_pwd_md5 == ""

    def test_frozen_dataclass(self) -> None:
        """Test that the dataclass is frozen (immutable)."""
        cfg = SamTraderConfig(
            trader_id="sam_trader",
            environment="paper",
            log_level="INFO",
            ib_enabled=False,
            ib_gateway_host="sam-ib-gateway",
            ib_gateway_port=4004,
            ib_client_id=11,
            ib_account_id="",
            ib_symbols=[],
            ib_read_only_api=False,
            ib_market_data_type="REALTIME",
            futu_enabled=False,
            futu_opend_host="sam-futu-opend",
            futu_opend_port=11111,
            futu_trd_env="SIMULATE",
            futu_trd_market="US",
            futu_unlock_pwd_md5="",
            futu_keep_alive_interval_secs=1800,
            actor_bar_resub_enabled=False,
            actor_journal_enabled=False,
            actor_health_enabled=False,
            actor_rejection_monitor_enabled=False,
            actor_realized_pnl_enabled=False,
            actor_position_snapshot_enabled=False,
            state_save_enabled=False,
            state_load_enabled=False,
            state_save_handshake_timeout=30,
            bundles_path="config/bundles.yaml",
            postgres_host="sam-postgres",
            postgres_port=5432,
            postgres_db="sam_trader",
            postgres_user="sam",
            postgres_password="sam_secret",
            redis_host="sam-redis",
            redis_port=6379,
            redis_password="",
            risk_max_order_submit_rate="100/00:00:01",
            risk_max_order_modify_rate="100/00:00:01",
            risk_max_notional_per_order="",
            risk_bypass=False,
        )

        with pytest.raises(FrozenInstanceError):
            cfg.trader_id = "hacker"  # type: ignore[misc]

    def test_risk_config_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Risk engine env vars are parsed correctly with all combinations."""
        monkeypatch.setenv("RISK_MAX_ORDER_SUBMIT_RATE", "10/00:00:01")
        monkeypatch.setenv("RISK_MAX_ORDER_MODIFY_RATE", "5/00:00:05")
        monkeypatch.setenv(
            "RISK_MAX_NOTIONAL_PER_ORDER", '{"USD": 50000, "HKD": 200000}'
        )
        monkeypatch.setenv("RISK_BYPASS", "true")

        cfg = SamTraderConfig.from_env()
        assert cfg.risk_max_order_submit_rate == "10/00:00:01"
        assert cfg.risk_max_order_modify_rate == "5/00:00:05"
        assert cfg.risk_max_notional_per_order == '{"USD": 50000, "HKD": 200000}'
        assert cfg.risk_bypass is True

        # Test bypass with "1"
        monkeypatch.setenv("RISK_BYPASS", "1")
        cfg = SamTraderConfig.from_env()
        assert cfg.risk_bypass is True

        # Test bypass off with empty string
        monkeypatch.setenv("RISK_BYPASS", "")
        cfg = SamTraderConfig.from_env()
        assert cfg.risk_bypass is False
