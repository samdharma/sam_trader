"""SamTraderConfig — env-var driven frozen dataclass with multi-broker support."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sam_trader.market_config import MarketConfig

logger = logging.getLogger(__name__)


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
    futu_account_id: str
    futu_keep_alive_interval_secs: int

    # Actors
    actor_bar_resub_enabled: bool
    actor_journal_enabled: bool
    actor_health_enabled: bool
    actor_rejection_monitor_enabled: bool
    actor_realized_pnl_enabled: bool
    actor_position_snapshot_enabled: bool
    health_monitor_market: str
    bar_resub_market: str
    market_calendar_enabled: bool

    # State persistence
    state_save_enabled: bool
    state_load_enabled: bool
    state_save_handshake_timeout: int

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

    # Risk Engine
    risk_max_order_submit_rate: str
    risk_max_order_modify_rate: str
    risk_max_notional_per_order: str
    risk_bypass: bool

    # Market-aware fields (Dynamic Multi-Market) — must come last (have defaults)
    market: str = ""
    market_config: MarketConfig | None = None
    futu_routing_venues: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> SamTraderConfig:
        """Load configuration from environment variables.

        Reads ``MARKET`` env var to load per-market configuration from
        ``config/market_config.yaml``. When ``MARKET`` is set (e.g., ``US``,
        ``HK``), derives ``futu_trd_market``, ``ib_enabled``,
        ``futu_routing_venues``, ``health_monitor_market``, and
        ``bar_resub_market`` from the market config entry.

        Backward compatibility: when ``MARKET`` is empty or not set,
        falls back to the existing ``FUTU_TRD_MARKET``, ``IB_ENABLED``,
        ``HEALTH_MONITOR_MARKET``, and ``BAR_RESUB_MARKET`` env vars.

        If ``MARKET`` is set but ``market_config.yaml`` cannot be loaded,
        logs a warning and falls back to env vars.

        Returns
        -------
        SamTraderConfig
            Frozen configuration instance.

        """
        # ── Market-aware config loading ──────────────────────────
        market = os.environ.get("MARKET", "").strip()
        market_config: MarketConfig | None = None

        if market:
            # New path: load from market_config.yaml
            try:
                from sam_trader.market_config import MarketConfig

                market_config = MarketConfig.get_market(market)
                logger.info(
                    "Loaded market config for %s: timezone=%s ib_enabled=%s",
                    market,
                    market_config.session_timezone,
                    market_config.ib_enabled,
                )
            except FileNotFoundError:
                logger.warning(
                    "MARKET=%s but market_config.yaml not found — "
                    "falling back to env vars",
                    market,
                )
            except ValueError as e:
                logger.warning(
                    "MARKET=%s but invalid market config: %s — "
                    "falling back to env vars",
                    market,
                    e,
                )

        if market_config is not None:
            # Derive fields from market config
            futu_trd_market_val = market_config.futu_trd_market
            ib_enabled_val = market_config.ib_enabled
            futu_routing_venues_val = list(market_config.futu_routing_venues)
            health_monitor_val = market
            bar_resub_val = market
        else:
            # Backward compat: use existing env vars
            futu_trd_market_val = os.environ.get("FUTU_TRD_MARKET", "US")
            ib_enabled_val = os.environ.get("IB_ENABLED", "").lower() in (
                "1",
                "true",
                "yes",
            )
            futu_routing_venues_val = []
            health_monitor_val = os.environ.get("HEALTH_MONITOR_MARKET", "")
            bar_resub_val = os.environ.get("BAR_RESUB_MARKET", "")

        # ── Remaining env var parsing ────────────────────────────
        raw_symbols = os.environ.get("IB_SYMBOLS", "")
        symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]

        return cls(
            trader_id=os.environ.get("TRADER_ID", "sam_trader"),
            environment=os.environ.get("SAM_ENV", "paper"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            ib_enabled=ib_enabled_val,
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
            futu_trd_market=futu_trd_market_val,
            futu_account_id=os.environ.get("FUTU_ACCOUNT_ID", ""),
            futu_unlock_pwd_md5=os.environ.get("FUTU_UNLOCK_PWD_MD5", ""),
            futu_keep_alive_interval_secs=int(
                os.environ.get("FUTU_KEEP_ALIVE_INTERVAL_SECS", "1800")
            ),
            actor_bar_resub_enabled=os.environ.get(
                "ACTOR_BAR_RESUB_ENABLED", ""
            ).lower()
            in ("1", "true", "yes"),
            actor_journal_enabled=os.environ.get("ACTOR_JOURNAL_ENABLED", "").lower()
            in ("1", "true", "yes"),
            actor_health_enabled=os.environ.get("ACTOR_HEALTH_ENABLED", "").lower()
            in ("1", "true", "yes"),
            actor_rejection_monitor_enabled=os.environ.get(
                "ACTOR_REJECTION_MONITOR_ENABLED", ""
            ).lower()
            in ("1", "true", "yes"),
            actor_realized_pnl_enabled=os.environ.get(
                "ACTOR_REALIZED_PNL_ENABLED", ""
            ).lower()
            in ("1", "true", "yes"),
            actor_position_snapshot_enabled=(
                os.environ.get("ACTOR_POSITION_SNAPSHOT_ENABLED", "").lower()
                in ("1", "true", "yes")
                if os.environ.get("ACTOR_POSITION_SNAPSHOT_ENABLED")
                else os.environ.get("ACTOR_JOURNAL_ENABLED", "").lower()
                in ("1", "true", "yes")
            ),
            health_monitor_market=health_monitor_val,
            bar_resub_market=bar_resub_val,
            market_calendar_enabled=os.environ.get(
                "MARKET_CALENDAR_ENABLED", "true"
            ).lower()
            in ("1", "true", "yes"),
            market=market,
            market_config=market_config,
            futu_routing_venues=futu_routing_venues_val,
            state_save_enabled=os.environ.get("STATE_SAVE_ENABLED", "").lower()
            in ("1", "true", "yes"),
            state_load_enabled=os.environ.get("STATE_LOAD_ENABLED", "").lower()
            in ("1", "true", "yes"),
            state_save_handshake_timeout=int(
                os.environ.get("STATE_SAVE_HANDSHAKE_TIMEOUT", "30")
            ),
            bundles_path=os.environ.get("BUNDLES_PATH", "config/bundles.yaml"),
            postgres_host=os.environ.get("POSTGRES_HOST", "sam-postgres"),
            postgres_port=int(os.environ.get("POSTGRES_PORT", "5432")),
            postgres_db=os.environ.get("POSTGRES_DB", "sam_trader"),
            postgres_user=os.environ.get("POSTGRES_USER", "sam"),
            postgres_password=os.environ.get("POSTGRES_PASSWORD", "sam_secret"),
            redis_host=os.environ.get("REDIS_HOST", "sam-redis"),
            redis_port=int(os.environ.get("REDIS_PORT", "6379")),
            redis_password=os.environ.get("REDIS_PASSWORD", ""),
            risk_max_order_submit_rate=os.environ.get(
                "RISK_MAX_ORDER_SUBMIT_RATE", "100/00:00:01"
            ),
            risk_max_order_modify_rate=os.environ.get(
                "RISK_MAX_ORDER_MODIFY_RATE", "100/00:00:01"
            ),
            risk_max_notional_per_order=os.environ.get(
                "RISK_MAX_NOTIONAL_PER_ORDER", ""
            ),
            risk_bypass=os.environ.get("RISK_BYPASS", "").lower()
            in ("1", "true", "yes"),
        )
