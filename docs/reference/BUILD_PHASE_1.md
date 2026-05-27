# Build Phase 1 — Configuration & Bootstrap

> **Status:** ✅ Complete  
> **Goal:** `SamTraderConfig` loads from env vars with multi-broker fields. `main.py` bootstraps TradingNode with placeholder for both Futu and IB factories.  
> **Prev Phase:** [BUILD_PHASE_0.md](./BUILD_PHASE_0.md) — Foundation & Docker Stack  
> **Next Phase:** [BUILD_PHASE_2.md](./BUILD_PHASE_2.md) — Futu Market Data Adapter  
> **Feature Ticket:** `sam_trader-9z3.2` (closed)  
> **Bug Fix:** `sam_trader-9z3.2.1` — Remove dead `futu_account_id` field (closed)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      .env / Environment                        │
├──────────────────────────────────────────────────────────────┤
│  TRADER_ID, SAM_ENV, LOG_LEVEL                                │
│  FUTU_ENABLED, FUTU_OPEND_HOST, FUTU_OPEND_PORT              │
│  FUTU_TRD_ENV, FUTU_TRD_MARKET, FUTU_UNLOCK_PWD_MD5          │
│  IB_ENABLED, IB_GATEWAY_HOST, IB_GATEWAY_PORT                │
│  IB_CLIENT_ID, IB_ACCOUNT_ID, IB_SYMBOLS                     │
│  POSTGRES_*, REDIS_*, STATE_*, BUNDLES_PATH                  │
│  ACTOR_*, RISK_*, BACKUP_*, LOG_*, DEPLOY_WINDOW             │
│  PIPELINE_SCHEDULE                                            │
└──────────┬───────────────────────────────────────────────────┘
           │ from_env()
           ▼
┌──────────────────────────────────────────────────────────────┐
│                   SamTraderConfig (frozen)                     │
├──────────────────────────────────────────────────────────────┤
│  trader_id, environment, log_level                            │
│  ib_enabled, ib_gateway_host, ib_gateway_port, ...           │
│  futu_enabled, futu_opend_host, futu_opend_port, ...         │
│  actor_*_enabled (6 flags), state_*_enabled                   │
│  postgres_*, redis_*, bundles_path                            │
│  risk_max_order_submit_rate, risk_max_order_modify_rate, ...  │
└──────────┬───────────────────────────────────────────────────┘
           │ build_trading_node()
           ▼
┌──────────────────────────────────────────────────────────────┐
│                      TradingNode                               │
├──────────────────────────────────────────────────────────────┤
│  TradingNodeConfig with:                                       │
│    - data_clients (empty dict — filled by Phase 2+)           │
│    - exec_clients (empty dict — filled by Phase 3+)           │
│    - strategies (from BundleLoader — graceful fail on empty)   │
│    - actors (ImportableActorConfig — Phase 6/8)                │
│    - risk_engine (LiveRiskEngineConfig — Phase 8)              │
│    - cache (CacheConfig with Redis — when state enabled)       │
├──────────────────────────────────────────────────────────────┤
│  Factory registration:                                         │
│    - Futu: lazy import, conditional on futu_enabled            │
│    - IB: lazy import, conditional on ib_enabled                │
│    - ImportError → graceful degradation (log warning)          │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. SamTraderConfig (`src/sam_trader/config.py`)

### 2.1 Design

- **Frozen dataclass** — immutable after construction, thread-safe
- **`from_env()` classmethod** — single source of truth for env var parsing
- **Multi-broker** — IB and Futu fields side by side, feature-gated via `*_enabled` flags
- **Extensible** — Phase 6 actors and Phase 8 risk engine fields added incrementally

### 2.2 Field Groups

| Group | Fields | Source Env Vars |
|-------|--------|-----------------|
| Identity | `trader_id`, `environment`, `log_level` | `TRADER_ID`, `SAM_ENV`, `LOG_LEVEL` |
| IBKR | `ib_enabled`, `ib_gateway_host`, `ib_gateway_port`, `ib_client_id`, `ib_account_id`, `ib_symbols`, `ib_read_only_api`, `ib_market_data_type` | `IB_ENABLED`, `IB_GATEWAY_HOST`, `IB_GATEWAY_PORT`, `IB_GATEWAY_CLIENT_ID`, `IB_ACCOUNT_ID`, `IB_SYMBOLS`, `IB_READ_ONLY_API`, `IB_MARKET_DATA_TYPE` |
| Futu | `futu_enabled`, `futu_opend_host`, `futu_opend_port`, `futu_trd_env`, `futu_trd_market`, `futu_unlock_pwd_md5` | `FUTU_ENABLED`, `FUTU_OPEND_HOST`, `FUTU_OPEND_PORT`, `FUTU_TRD_ENV`, `FUTU_TRD_MARKET`, `FUTU_UNLOCK_PWD_MD5` |
| Actors (6 flags) | `actor_bar_resub_enabled`, `actor_journal_enabled`, `actor_health_enabled`, `actor_rejection_monitor_enabled`, `actor_realized_pnl_enabled`, `actor_position_snapshot_enabled` | `ACTOR_BAR_RESUB_ENABLED`, `ACTOR_JOURNAL_ENABLED`, `ACTOR_HEALTH_ENABLED`, `ACTOR_REJECTION_MONITOR_ENABLED`, `ACTOR_REALIZED_PNL_ENABLED`, `ACTOR_POSITION_SNAPSHOT_ENABLED` |
| State | `state_save_enabled`, `state_load_enabled` | `STATE_SAVE_ENABLED`, `STATE_LOAD_ENABLED` |
| Bundles | `bundles_path` | `BUNDLES_PATH` |
| PostgreSQL | `postgres_host`, `postgres_port`, `postgres_db`, `postgres_user`, `postgres_password` | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| Redis | `redis_host`, `redis_port`, `redis_password` | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` |
| Risk Engine | `risk_max_order_submit_rate`, `risk_max_order_modify_rate`, `risk_max_notional_per_order`, `risk_bypass` | `RISK_MAX_ORDER_SUBMIT_RATE`, `RISK_MAX_ORDER_MODIFY_RATE`, `RISK_MAX_NOTIONAL_PER_ORDER`, `RISK_BYPASS` |

### 2.3 Boolean Parsing Convention

All boolean fields use `os.environ.get("KEY", "").lower() in ("1", "true", "yes")`.

### 2.4 Removed Fields (Bug Fix `9z3.2.1`)

`futu_account_id` was removed — the field was read from `FUTU_ACCOUNT_ID` env var but never used by any code path. Futu OpenD handles account management independently via its own env vars.

---

## 3. main.py Bootstrap (`src/sam_trader/main.py`)

### 3.1 `build_trading_node()`

Constructs a `TradingNode` with lazy imports for broker adapters:

1. **Load `SamTraderConfig.from_env()`**
2. **Lazy-import Futu adapter** (if `futu_enabled`): `FutuDataClientConfig`, `FutuExecClientConfig`, factories. ImportError → log warning, skip.
3. **Lazy-import IB adapter** (if `ib_enabled`): `InteractiveBrokersDataClientConfig`, `InteractiveBrokersExecClientConfig`, `InteractiveBrokersInstrumentProviderConfig`, factories. ImportError → log warning, skip. Validates `IB_MARKET_DATA_TYPE` against `MarketDataTypeEnum` with silent fallback to `REALTIME`.
4. **Load bundles** via `load_bundles(cfg.bundles_path)`. Graceful fail on error → empty strategies, log warning. Filters bundles by enabled venue to prevent cross-venue contamination.
5. **Extract instrument IDs** from bundles for actor configuration.
6. **Build `CacheConfig`** with Redis when state persistence enabled.
7. **Build `LiveRiskEngineConfig`** with rate limits, notional caps, bypass.
8. **Assemble `ImportableActorConfig` list** for all 6 actors (Phase 6/8), conditional on feature flags.
9. **Construct `TradingNodeConfig`** with all subsystems.
10. **Register factories**: `node.add_data_client_factory(...)` and `node.add_exec_client_factory(...)` for enabled venues.

### 3.2 Helper Functions

| Function | Purpose |
|----------|---------|
| `_make_trader_id(value)` | Ensures `NAME-001` format for Nautilus TraderId |
| `_make_load_ids(symbols)` | Converts `TSLA.NASDAQ` → `InstrumentId` frozenset for IB instrument provider |

### 3.3 `_PortfolioErrorFilter`

Log filter that demotes `"no account registered"` ERRORs to WARNING during broker startup. This is a normal timing condition that self-resolves once the exec client finishes handshake.

### 3.4 `main()`

Standard lifecycle: `build_trading_node()` → `node.build()` → `node.run()` → `node.dispose()`.

---

## 4. Bundle Loader Integration (`src/sam_trader/bundle_loader.py`)

`load_bundles(path)` is called from `build_trading_node()`:
- Returns `list[ImportableStrategyConfig]`
- Raises `BundleLoaderError` or `BundleValidationError` on failure
- Calling code catches errors → logs warning, runs with empty strategies
- Venue filtering: bundles for disabled venues are skipped (log INFO)

---

## 5. .env.example (`config/` → `.env.example`)

Complete template with all env vars, grouped by subsystem (see §2.2 for field groups). All secrets blank — users fill in real values. Never committed (`.env` in `.gitignore`).

---

## 6. Ticket Summary

| Ticket | Title | Status |
|--------|-------|--------|
| `sam_trader-9z3.2` | Phase 1 FEATURE: Configuration & Bootstrap | ✅ Closed |
| `sam_trader-0f6` | SamTraderConfig — frozen dataclass with Futu + IB fields | ✅ Closed |
| (implicit) | main.py — TradingNode bootstrap with multi-broker placeholders | ✅ Closed |
| (implicit) | Phase 1 integration test | ✅ Closed |
| `sam_trader-9z3.2.1` | BUG: Remove dead `futu_account_id` field | ✅ Closed |

---

## 7. Commonly Used Imports

```python
# Config
from sam_trader.config import SamTraderConfig

# Bootstrap
from sam_trader.main import build_trading_node, main

# Nautilus core
from nautilus_trader.trading.node import TradingNode
from nautilus_trader.live.config import TradingNodeConfig
from nautilus_trader.config import RoutingConfig
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.config import DatabaseConfig, ImportableActorConfig, LoggingConfig
from nautilus_trader.live.config import LiveRiskEngineConfig
```

---

*Last updated: 2026-05-24 — Created from gap audit; Phase 1 implemented 2026-05-20*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** Planning — 3 tickets  
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps |
|-----------|-------|------|
| `sam_trader-9z3.2.3` | MarketConfig: frozen dataclass + market_config.yaml | None |
| `sam_trader-9z3.2.4` | MARKET env var → derived config fields | 9z3.2.3 |
| `sam_trader-9z3.2.5` | main.py: market-aware config propagation | 9z3.2.4 |

### Design Notes
- New `MarketConfig` frozen dataclass in `src/sam_trader/market_config.py`
- New `config/market_config.yaml` with US + HK entries (timezone, routing, session hours, pipeline times)
- `SamTraderConfig.from_env()` reads `MARKET` env var (default `US`), loads active market config
- Derives: `futu_trd_market`, `ib_enabled`, `futu_routing_venues`, actor timezone fields
- Backward compat: if `MARKET` not set, falls back to existing `FUTU_TRD_MARKET` + `IB_ENABLED` env vars
- main.py: remove all hardcoded `if futu_trd_market == "HK"` timezone ternary patterns
- Bundle filtering by `market` field — skip bundles for inactive market

### Nautilus Types / Patterns Used
- Frozen dataclass with `from_yaml()` (matches `SamTraderConfig` pattern)
- `RoutingConfig` for per-market venue routing
- `ImportableActorConfig` for actor timezone propagation

*Last updated: 2026-05-27 — Dynamic Multi-Market extensions planned*
