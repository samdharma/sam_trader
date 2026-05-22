"""Bootstrap: TradingNode + BundleLoader + multi-broker placeholders."""

from __future__ import annotations

import logging

from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.config import DatabaseConfig, LoggingConfig
from nautilus_trader.config import RoutingConfig
from nautilus_trader.live.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue

from sam_trader.bundle_loader import (
    BundleLoaderError,
    BundleValidationError,
    load_bundles,
)
from sam_trader.config import SamTraderConfig

logger = logging.getLogger(__name__)


class _PortfolioErrorFilter(logging.Filter):
    """Demote transient 'no account registered' ERRORs to WARNING.

    During broker startup the portfolio may receive events before the account
    has been registered. This is a normal timing condition that
    self-resolves once the exec client finishes handshake.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.levelno == logging.ERROR and (
            "no account registered" in msg or "Cannot get" in msg
        ):
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


# Demote known-transient portfolio errors so operators aren't alarmed.
logging.getLogger("nautilus_trader.portfolio.portfolio").addFilter(
    _PortfolioErrorFilter()
)


def _make_trader_id(value: str) -> str:
    """Ensure value is a valid Nautilus TraderId format (NAME-001)."""
    if "-" not in value:
        return f"{value}-001"
    return value


def _make_load_ids(symbols: list[str]) -> frozenset[InstrumentId] | None:
    """Convert symbol strings to InstrumentIds where venue is explicit.

    Symbols without a '.' venue separator are skipped; they will be
    resolved dynamically by the instrument provider at runtime.
    """
    ids: list[InstrumentId] = []
    for s in symbols:
        if "." in s:
            sym, venue = s.split(".", 1)
            ids.append(InstrumentId(Symbol(sym), Venue(venue)))
    return frozenset(ids) if ids else None


def build_trading_node() -> TradingNode:
    """Build and return a TradingNode with multi-broker placeholders.

    Returns
    -------
    TradingNode
        Configured but unbuilt trading node.

    """
    cfg = SamTraderConfig.from_env()

    data_clients: dict[str, object] = {}
    exec_clients: dict[str, object] = {}

    # Lazy Futu adapter imports — built in later phases (Phase 2–4).
    # When missing, the node starts without Futu clients.
    futu_data_factory: type | None = None
    futu_exec_factory: type | None = None
    if cfg.futu_enabled:
        try:
            from sam_trader.adapters.futu.config import (
                FutuDataClientConfig,
                FutuExecClientConfig,
            )
            from sam_trader.adapters.futu.factories import (
                FutuLiveDataClientFactory,
                FutuLiveExecClientFactory,
            )

            data_clients["FUTU"] = FutuDataClientConfig(
                host=cfg.futu_opend_host,
                port=cfg.futu_opend_port,
                trd_env=cfg.futu_trd_env,
                trd_market=cfg.futu_trd_market,
            )

            exec_clients["FUTU"] = FutuExecClientConfig(
                host=cfg.futu_opend_host,
                port=cfg.futu_opend_port,
                trd_env=cfg.futu_trd_env,
                trd_market=cfg.futu_trd_market,
                unlock_pwd_md5=cfg.futu_unlock_pwd_md5,
            )

            futu_data_factory = FutuLiveDataClientFactory
            futu_exec_factory = FutuLiveExecClientFactory
            logger.info("Futu client configs registered")
        except ImportError as exc:
            logger.warning(
                "Futu adapter not available; Futu clients will not be registered: %s",
                exc,
            )

    # Lazy IB adapter imports — ibapi is only available in the Docker container.
    # When missing, the node still starts but without IB clients.
    ib_data_factory: type | None = None
    ib_exec_factory: type | None = None
    if cfg.ib_enabled:
        try:
            from ibapi.common import MarketDataTypeEnum as IBMarketDataTypeEnum
            from nautilus_trader.adapters.interactive_brokers.config import (
                InteractiveBrokersDataClientConfig,
                InteractiveBrokersExecClientConfig,
                InteractiveBrokersInstrumentProviderConfig,
                SymbologyMethod,
            )
            from nautilus_trader.adapters.interactive_brokers.factories import (
                InteractiveBrokersLiveDataClientFactory,
            )
            from sam_trader.adapters.ib.factories import (
                SamInteractiveBrokersLiveExecClientFactory,
            )
            from sam_trader.adapters.ib.permissions import (
                set_bundle_permission_requirements,
            )

            instrument_provider = InteractiveBrokersInstrumentProviderConfig(
                symbology_method=SymbologyMethod.IB_SIMPLIFIED,
                load_ids=_make_load_ids(cfg.ib_symbols),
            )

            market_data_type = getattr(
                IBMarketDataTypeEnum,
                cfg.ib_market_data_type,
                IBMarketDataTypeEnum.REALTIME,
            )

            data_clients["IB"] = InteractiveBrokersDataClientConfig(
                ibg_host=cfg.ib_gateway_host,
                ibg_port=cfg.ib_gateway_port,
                ibg_client_id=cfg.ib_client_id,
                instrument_provider=instrument_provider,
                market_data_type=market_data_type,
                dockerized_gateway=None,
            )

            if not cfg.ib_read_only_api:
                exec_clients["IB"] = InteractiveBrokersExecClientConfig(
                    ibg_host=cfg.ib_gateway_host,
                    ibg_port=cfg.ib_gateway_port,
                    ibg_client_id=cfg.ib_client_id,
                    account_id=cfg.ib_account_id or None,
                    instrument_provider=instrument_provider,
                    routing=RoutingConfig(default=True),
                    dockerized_gateway=None,
                )

            ib_data_factory = InteractiveBrokersLiveDataClientFactory
            ib_exec_factory = (
                SamInteractiveBrokersLiveExecClientFactory
                if not cfg.ib_read_only_api
                else None
            )
            logger.info("IB client configs registered")
        except ImportError as exc:
            logger.warning(
                "ibapi not available; IBKR clients will not be registered: %s",
                exc,
            )

    strategies: list = []
    try:
        strategies = load_bundles(cfg.bundles_path)
        logger.info(
            "Loaded %d strategy bundle(s) from %s",
            len(strategies),
            cfg.bundles_path,
        )
    except (BundleLoaderError, BundleValidationError) as exc:
        logger.warning(
            "Failed to load bundles from %s: %s. Running with no strategies.",
            cfg.bundles_path,
            exc,
        )

    if cfg.ib_enabled and ib_exec_factory is not None:
        set_bundle_permission_requirements(strategies)

    # Build CacheConfig with Redis database for state persistence.
    # Only wire Redis when state persistence is enabled to avoid unnecessary
    # connection attempts to an unavailable Redis instance.
    cache_config: CacheConfig | None = None
    if cfg.state_load_enabled or cfg.state_save_enabled:
        cache_db = DatabaseConfig(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
        )
        cache_config = CacheConfig(database=cache_db)

    node_config = TradingNodeConfig(
        trader_id=_make_trader_id(cfg.trader_id),
        logging=LoggingConfig(log_level=cfg.log_level.upper()),
        cache=cache_config,
        load_state=cfg.state_load_enabled,
        save_state=cfg.state_save_enabled,
        data_clients=data_clients,
        exec_clients=exec_clients,
        strategies=strategies,
    )

    node = TradingNode(config=node_config)

    if futu_data_factory is not None:
        node.add_data_client_factory("FUTU", futu_data_factory)
    if futu_exec_factory is not None:
        node.add_exec_client_factory("FUTU", futu_exec_factory)
    if ib_data_factory is not None:
        node.add_data_client_factory("IB", ib_data_factory)
    if ib_exec_factory is not None:
        node.add_exec_client_factory("IB", ib_exec_factory)

    return node


def main() -> None:
    """Main entry point for SAM Trader."""
    node = build_trading_node()
    node.build()
    node.run()


if __name__ == "__main__":
    main()
