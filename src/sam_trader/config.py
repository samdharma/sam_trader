"""SamTraderConfig — env-var driven frozen dataclass with multi-broker support."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SamTraderConfig:
    """Top-level configuration for SAM Trader V3, loaded from environment variables."""

    # Identity
    trader_id: str
    environment: str
    log_level: str

    # IBKR
    ib_enabled: bool
    ib_gateway_host: str
    ib_gateway_port: int
    ib_client_id: int
    ib_account_id: str
    ib_symbols: list[str]
    ib_read_only_api: bool
    ib_market_data_type: str

    # Futu
    futu_enabled: bool
    futu_opend_host: str
    futu_opend_port: int
    futu_trd_env: str
    futu_trd_market: str
    futu_unlock_pwd_md5: str

    # Actors
    actor_bar_resub_enabled: bool
    actor_journal_enabled: bool
    actor_health_enabled: bool

    # State persistence
    state_save_enabled: bool
    state_load_enabled: bool

    # Bundles
    bundles_path: str

    # PostgreSQL
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    # Redis
    redis_host: str
    redis_port: int
    redis_password: str

    @classmethod
    def from_env(cls) -> SamTraderConfig:
        """Load configuration from environment variables.

        Returns
        -------
        SamTraderConfig
            Frozen configuration instance.

        """
        raw_symbols = os.environ.get("IB_SYMBOLS", "")
        symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]

        return cls(
            trader_id=os.environ.get("TRADER_ID", "sam_trader"),
            environment=os.environ.get("SAM_ENV", "paper"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            ib_enabled=os.environ.get("IB_ENABLED", "").lower() in ("1", "true", "yes"),
            ib_gateway_host=os.environ.get("IB_GATEWAY_HOST", "sam-ib-gateway"),
            ib_gateway_port=int(os.environ.get("IB_GATEWAY_PORT", "4004")),
            ib_client_id=int(os.environ.get("IB_GATEWAY_CLIENT_ID", "11")),
            ib_account_id=os.environ.get("IB_ACCOUNT_ID", ""),
            ib_symbols=symbols,
            ib_read_only_api=os.environ.get("IB_READ_ONLY_API", "").lower()
            in ("1", "true", "yes"),
            ib_market_data_type=os.environ.get(
                "IB_MARKET_DATA_TYPE", "REALTIME"
            ).upper(),
            futu_enabled=os.environ.get("FUTU_ENABLED", "").lower()
            in ("1", "true", "yes"),
            futu_opend_host=os.environ.get("FUTU_OPEND_HOST", "sam-futu-opend"),
            futu_opend_port=int(os.environ.get("FUTU_OPEND_PORT", "11111")),
            futu_trd_env=os.environ.get("FUTU_TRD_ENV", "SIMULATE"),
            futu_trd_market=os.environ.get("FUTU_TRD_MARKET", "US"),
            futu_unlock_pwd_md5=os.environ.get("FUTU_UNLOCK_PWD_MD5", ""),
            actor_bar_resub_enabled=os.environ.get(
                "ACTOR_BAR_RESUB_ENABLED", ""
            ).lower()
            in ("1", "true", "yes"),
            actor_journal_enabled=os.environ.get("ACTOR_JOURNAL_ENABLED", "").lower()
            in ("1", "true", "yes"),
            actor_health_enabled=os.environ.get("ACTOR_HEALTH_ENABLED", "").lower()
            in ("1", "true", "yes"),
            state_save_enabled=os.environ.get("STATE_SAVE_ENABLED", "").lower()
            in ("1", "true", "yes"),
            state_load_enabled=os.environ.get("STATE_LOAD_ENABLED", "").lower()
            in ("1", "true", "yes"),
            bundles_path=os.environ.get("BUNDLES_PATH", "config/bundles.yaml"),
            postgres_host=os.environ.get("POSTGRES_HOST", "sam-postgres"),
            postgres_port=int(os.environ.get("POSTGRES_PORT", "5432")),
            postgres_db=os.environ.get("POSTGRES_DB", "sam_trader"),
            postgres_user=os.environ.get("POSTGRES_USER", "sam"),
            postgres_password=os.environ.get("POSTGRES_PASSWORD", "sam_secret"),
            redis_host=os.environ.get("REDIS_HOST", "sam-redis"),
            redis_port=int(os.environ.get("REDIS_PORT", "6379")),
            redis_password=os.environ.get("REDIS_PASSWORD", ""),
        )
