"""Bootstrap: TradingNode + BundleLoader + multi-broker placeholders."""

from __future__ import annotations

import json
import logging

from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.config import (
    DatabaseConfig,
    ImportableActorConfig,
    LoggingConfig,
)
from nautilus_trader.config import RoutingConfig
from nautilus_trader.live.config import LiveRiskEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue

from sam_trader.bundle_loader import (
    BundleLoaderError,
    BundleValidationError,
    load_bundles,
)
from sam_trader.config import SamTraderConfig
from sam_trader.kill_switch_subscriber import KillSwitchSubscriber
from sam_trader.restart_subscriber import RestartSubscriber

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


def _routing_venues_for_market(trd_market: str) -> frozenset[str]:
    """Return the set of Nautilus venues that Futu should route for a given market."""
    return {
        "US": frozenset({"NASDAQ", "NYSE"}),
        "HK": frozenset({"HKEX"}),
        "CN": frozenset({"SHFE", "SZSE"}),
    }.get(trd_market, frozenset({"NASDAQ", "NYSE"}))


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

    # ── Load bundles FIRST so we know which instruments to pre-load ──
    # into Futu's instrument provider before clients connect.
    strategies: list = []
    instrument_ids: list[str] = []
    bar_type_strs: list[str] = []
    futu_load_ids: frozenset | None = None
    try:
        all_bundles = load_bundles(cfg.bundles_path)
        # Extract instrument IDs from bundles for actors that need them
        for bundle in all_bundles:
            ins_id = bundle.config.get("instrument_id")
            if ins_id and isinstance(ins_id, str) and ins_id not in instrument_ids:
                instrument_ids.append(ins_id)
            bts = bundle.config.get("bar_type")
            if bts and isinstance(bts, str) and bts not in bar_type_strs:
                bar_type_strs.append(bts)
        futu_load_ids = _make_load_ids(instrument_ids)
        # Filter bundles by enabled venue to prevent cross-venue contamination.
        # A bundle for a disabled venue would try to subscribe through a
        # non-existent client and raise runtime errors.
        skipped: list[str] = []
        for bundle in all_bundles:
            venue = bundle.config.get("venue")
            if venue == "FUTU" and not cfg.futu_enabled:
                skipped.append(bundle.config.get("bundle_id", "unknown"))
                continue
            if venue == "IB" and not cfg.ib_enabled:
                skipped.append(bundle.config.get("bundle_id", "unknown"))
                continue
            strategies.append(bundle)

        if skipped:
            logger.info(
                "Skipped %d bundle(s) for disabled venue(s): %s",
                len(skipped),
                skipped,
            )
        logger.info(
            "Loaded %d strategy bundle(s) from %s",
            len(strategies),
            cfg.bundles_path,
        )
        if not strategies and cfg.futu_enabled:
            logger.critical(
                "ZERO strategies loaded but FUTU is enabled. "
                "Check %s — copy from bundles.example.yaml if empty.",
                cfg.bundles_path,
            )
    except (BundleLoaderError, BundleValidationError) as exc:
        logger.warning(
            "Failed to load bundles from %s: %s. Running with no strategies.",
            cfg.bundles_path,
            exc,
        )

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

            # Use market_config routing venues when available; fallback to
            # _routing_venues_for_market() for backward compat.
            if cfg.market_config is not None:
                futu_routing_venues = frozenset(cfg.market_config.futu_routing_venues)
            elif cfg.futu_routing_venues:
                futu_routing_venues = frozenset(cfg.futu_routing_venues)
            else:
                futu_routing_venues = _routing_venues_for_market(cfg.futu_trd_market)
            logger.info("Futu routing venues: %s", futu_routing_venues)

            data_clients["FUTU"] = FutuDataClientConfig(
                host=cfg.futu_opend_host,
                port=cfg.futu_opend_port,
                trd_env=cfg.futu_trd_env,
                trd_market=cfg.futu_trd_market,
                load_ids=futu_load_ids,
                keep_alive_interval_secs=cfg.futu_keep_alive_interval_secs,
                routing=RoutingConfig(venues=futu_routing_venues),
            )

            exec_clients["FUTU"] = FutuExecClientConfig(
                host=cfg.futu_opend_host,
                port=cfg.futu_opend_port,
                trd_env=cfg.futu_trd_env,
                trd_market=cfg.futu_trd_market,
                unlock_pwd_md5=cfg.futu_unlock_pwd_md5,
                routing=RoutingConfig(venues=futu_routing_venues),
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
                InteractiveBrokersLiveExecClientFactory,
            )

            instrument_provider = InteractiveBrokersInstrumentProviderConfig(
                symbology_method=SymbologyMethod.IB_SIMPLIFIED,
                load_ids=_make_load_ids(cfg.ib_symbols),
            )

            if hasattr(IBMarketDataTypeEnum, cfg.ib_market_data_type):
                market_data_type = getattr(
                    IBMarketDataTypeEnum, cfg.ib_market_data_type
                )
            else:
                logger.warning(
                    "IB_MARKET_DATA_TYPE=%r is not a valid MarketDataTypeEnum value. "
                    "Valid: %s. Falling back to REALTIME.",
                    cfg.ib_market_data_type,
                    list(IBMarketDataTypeEnum.idx2name.values()),
                )
                market_data_type = IBMarketDataTypeEnum.REALTIME

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
                InteractiveBrokersLiveExecClientFactory
                if not cfg.ib_read_only_api
                else None
            )
            logger.info("IB client configs registered")
        except ImportError as exc:
            logger.warning(
                "ibapi not available; IBKR clients will not be registered: %s",
                exc,
            )

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

    notional_limits: dict[str, int] = {}
    if cfg.risk_max_notional_per_order:
        notional_limits = json.loads(cfg.risk_max_notional_per_order)

    risk_config = LiveRiskEngineConfig(
        bypass=cfg.risk_bypass,
        max_order_submit_rate=cfg.risk_max_order_submit_rate,
        max_order_modify_rate=cfg.risk_max_order_modify_rate,
        max_notional_per_order=notional_limits,
    )

    actors: list[ImportableActorConfig] = []

    # --- Phase 6 actors ---

    if cfg.actor_journal_enabled:
        actors.append(
            ImportableActorConfig(
                actor_path="sam_trader.actors.trade_journal:TradeJournalActor",
                config_path="sam_trader.actors.trade_journal:TradeJournalActorConfig",
                config={
                    "postgres_host": cfg.postgres_host,
                    "postgres_port": cfg.postgres_port,
                    "postgres_db": cfg.postgres_db,
                    "postgres_user": cfg.postgres_user,
                    "postgres_password": cfg.postgres_password,
                    "instrument_ids": instrument_ids,
                },
            )
        )
        logger.info(
            "TradeJournalActor registered (%d instruments)", len(instrument_ids)
        )

    if cfg.actor_health_enabled:
        # Use market_config session_timezone when available;
        # backward-compat ternary fallback when MARKET not set.
        health_tz = (
            cfg.market_config.session_timezone
            if cfg.market_config is not None
            else (
                "Asia/Hong_Kong" if cfg.futu_trd_market == "HK" else "America/New_York"
            )
        )
        actors.append(
            ImportableActorConfig(
                actor_path="sam_trader.actors.health_monitor:HealthMonitorActor",
                config_path="sam_trader.actors.health_monitor:HealthMonitorActorConfig",
                config={
                    "futu_enabled": cfg.futu_enabled,
                    "ib_enabled": cfg.ib_enabled,
                    "redis_host": cfg.redis_host,
                    "redis_port": cfg.redis_port,
                    "redis_password": cfg.redis_password,
                    "market_timezone": health_tz,
                    "market": cfg.health_monitor_market,
                    "market_calendar_enabled": cfg.market_calendar_enabled,
                    "bar_type_strs": bar_type_strs,
                },
            )
        )
        logger.info("HealthMonitorActor registered (tz=%s)", health_tz)

    if cfg.actor_bar_resub_enabled:
        bar_resub_actor = "sam_trader.actors.bar_resubscription:BarResubscriptionActor"
        bar_resub_config = (
            "sam_trader.actors.bar_resubscription:BarResubscriptionActorConfig"
        )
        # Use market_config session_timezone when available;
        # backward-compat ternary fallback when MARKET not set.
        bar_resub_tz = (
            cfg.market_config.session_timezone
            if cfg.market_config is not None
            else (
                "Asia/Hong_Kong" if cfg.futu_trd_market == "HK" else "America/New_York"
            )
        )
        actors.append(
            ImportableActorConfig(
                actor_path=bar_resub_actor,
                config_path=bar_resub_config,
                config={
                    "market_open_tz": bar_resub_tz,
                    "market": cfg.bar_resub_market,
                    "market_calendar_enabled": cfg.market_calendar_enabled,
                },
            )
        )
        logger.info("BarResubscriptionActor registered (tz=%s)", bar_resub_tz)

    if cfg.actor_rejection_monitor_enabled:
        rej_actor = "sam_trader.actors.rejection_monitor:RejectionMonitorActor"
        rej_config = "sam_trader.actors.rejection_monitor:RejectionMonitorActorConfig"
        actors.append(
            ImportableActorConfig(
                actor_path=rej_actor,
                config_path=rej_config,
                config={
                    "redis_host": cfg.redis_host,
                    "redis_port": cfg.redis_port,
                    "redis_password": cfg.redis_password,
                },
            )
        )
        logger.info("RejectionMonitorActor registered")

    if cfg.actor_realized_pnl_enabled:
        pnl_actor = "sam_trader.actors.realized_pnl:RealizedPnLTrackerActor"
        pnl_config = "sam_trader.actors.realized_pnl:RealizedPnLTrackerActorConfig"
        actors.append(
            ImportableActorConfig(
                actor_path=pnl_actor,
                config_path=pnl_config,
                config={
                    "redis_host": cfg.redis_host,
                    "redis_port": cfg.redis_port,
                    "redis_password": cfg.redis_password,
                    "instrument_ids": instrument_ids,
                },
            )
        )
        logger.info(
            "RealizedPnLTrackerActor registered (%d instruments)", len(instrument_ids)
        )

    # --- Phase 8 actors ---

    if cfg.actor_position_snapshot_enabled:
        actors.append(
            ImportableActorConfig(
                actor_path=(
                    "sam_trader.actors.position_snapshot:PositionSnapshotActor"
                ),
                config_path=(
                    "sam_trader.actors.position_snapshot:PositionSnapshotActorConfig"
                ),
                config={
                    "postgres_host": cfg.postgres_host,
                    "postgres_port": cfg.postgres_port,
                    "postgres_db": cfg.postgres_db,
                    "postgres_user": cfg.postgres_user,
                    "postgres_password": cfg.postgres_password,
                },
            )
        )
        logger.info("PositionSnapshotActor registered")

    # Guard: prevent stale order replay when no execution clients exist.
    # If the broker is disconnected, strategies may have generated orders
    # that were saved to Redis. Loading them without an exec client would
    # create orphan orders that the ExecEngine rejects.
    load_state = cfg.state_load_enabled
    if load_state and not exec_clients:
        logger.critical(
            "STATE LOAD ABORTED: load_state=True but ZERO execution clients "
            "are registered (futu_enabled=%s, ib_enabled=%s). "
            "Stale orders from Redis will NOT be replayed. "
            "To clear stale state manually: sam flush-cache",
            cfg.futu_enabled,
            cfg.ib_enabled,
        )
        load_state = False

    node_config = TradingNodeConfig(
        trader_id=_make_trader_id(cfg.trader_id),
        logging=LoggingConfig(log_level=cfg.log_level.upper()),
        cache=cache_config,
        load_state=load_state,
        save_state=cfg.state_save_enabled,
        data_clients=data_clients,
        exec_clients=exec_clients,
        risk_engine=risk_config,
        actors=actors,
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


def _notify_state_loaded(cfg: SamTraderConfig) -> None:
    """Publish ``sam:state_loaded`` so the ops CLI knows the node is ready."""
    try:
        import redis  # type: ignore[import-untyped]

        r = redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        payload = json.dumps({"status": "loaded", "trader_id": cfg.trader_id})
        r.publish("sam:state_loaded", payload)
        r.setex("sam:state_loaded", 60, "1")
    except Exception:
        pass


def main() -> None:
    """Main entry point for SAM Trader."""
    node = build_trading_node()
    node.build()

    cfg = SamTraderConfig.from_env()
    _notify_state_loaded(cfg)

    restart_sub = RestartSubscriber(node, cfg)
    restart_sub.start()

    kill_switch_sub = KillSwitchSubscriber(node, cfg)
    kill_switch_sub.start()

    try:
        node.run()
    finally:
        kill_switch_sub.stop()
        restart_sub.stop()
        node.dispose()


if __name__ == "__main__":
    main()
