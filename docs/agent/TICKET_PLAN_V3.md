# SAM Trader V3 — Ticket Plan & Dependency Hierarchy

> **Status:** Planning  
> **Date:** 2026-05-20  
> **Source:** `docs/reference/SAM_TRADER_V3_PLAN.md` §6  
> **Repo:** `github.com/samdharma/sam_trader`

---

## 1. Ticket Hierarchy Overview

```
EPIC: sam-v3 (SAM Trader V3)
│
├── FEATURE: phase-0 (Foundation — Skeleton & Docker Stack)
│
├── FEATURE: phase-1 (Configuration & Bootstrap)
│   ↑ depends on: phase-0
│
├── FEATURE: phase-2 (Futu Market Data Adapter)
│   ↑ depends on: phase-1
│
├── FEATURE: phase-3 (Futu Execution Adapter)
│   ↑ depends on: phase-2
│
├── FEATURE: phase-4 (Futu Instrument Provider & TradingNode Integration)
│   ↑ depends on: phase-3
│
├── FEATURE: phase-5 (IBKR Adapter Re-integration)
│   ↑ depends on: phase-4
│
├── FEATURE: phase-6 (Actors & State Management)
│   ↑ depends on: phase-5
│
├── FEATURE: phase-7 (Strategy Library & Bundle System)
│   ↑ depends on: phase-6
│
├── FEATURE: phase-8 (sam-services Container)
│   ↑ depends on: phase-4 (can start after Futu is wired)
│
├── FEATURE: phase-9 (Pre-Market Pipeline)
│   ↑ depends on: phase-8
│
├── FEATURE: phase-10 (Safety & Dashboard)
│   ↑ depends on: phase-9
│
└── FEATURE: phase-11 (Deploy Script & E2E Validation)
    ↑ depends on: phase-10
```

---

## 2. Labels

| Label | Scope |
|-------|-------|
| `phase-0` through `phase-11` | Roadmap phase marker |
| `epic` | Top-level epic or sub-epic |
| `feature` | Feature grouping ticket (parent of tasks) |
| `task` | Atomic work ticket |
| `exit` | Integration/exit test gate for a feature |
| `blocked` | Depends on incomplete work |
| `docs` | Documentation task |
| `test` | Testing/validation task |
| `deploy` | Deploy script update |
| `port` | Code being ported/adapted from v2 |
| `new` | Net-new code (not in v2) |
| `e2e-gate` | Human validation gate at phase end |

---

## 3. Acceptance Criteria Format

Every task ticket uses this structure:

```
## Acceptance Criteria
- [ ] Criterion 1: specific, measurable outcome
- [ ] Criterion 2: ...
- [ ] Criterion 3: ...

## Tests
- `tests/unit/test_xxx.py::test_yyy` — validates criterion 1

## Port from v2
- `~/Trading/csam_trader/src/csam_trader/path/to/file.py` → adapt for v3
```

---

## 4. Dependency Tree (Visual)

```
Phase 0 ──────────────────────────────────────────────────────────────────
  sam-p0-repo ──► sam-p0-scaffold ──┬──► sam-p0-dockerfile ──► sam-p0-compose ──► sam-p0-entrypoint
                                     ├──► sam-p0-postgres
                                     ├──► sam-p0-redis
                                     └──► sam-p0-futu-opend
                                                      │
                                                      ▼
                                            sam-p0-verify ──► ═══ PHASE 0 GATE ═══

Phase 1 ──────────────────────────────────────────────────────────────────
  sam-p1-config ──► sam-p1-main ──► sam-p1-integration ──► ═══ PHASE 1 GATE ═══

Phase 2 ──────────────────────────────────────────────────────────────────
  sam-p2-connection ──► sam-p2-constants ──┐
  sam-p2-config-dc   ──────────────────────┤
                                            ▼
                                     sam-p2-parsing ──► sam-p2-data-client ──► sam-p2-sub-mgr
                                                                                     │
                                                                                     ▼
                                                                           sam-p2-exit-data ──► ═══ PHASE 2 GATE ═══

Phase 3 ──────────────────────────────────────────────────────────────────
  sam-p3-parsing-orders ──► sam-p3-exec-client ──► sam-p3-exit-exec ──► ═══ PHASE 3 GATE ═══

Phase 4 ──────────────────────────────────────────────────────────────────
  sam-p4-parsing-inst ──► sam-p4-provider ──┐
  sam-p4-factories ─────────────────────────┤
  sam-p4-main-wire ─────────────────────────┤
  sam-p4-bundle ────────────────────────────┤
                                              ▼
                                      sam-p4-exit-dual ──► ═══ PHASE 4 GATE ═══

Phase 5 ──────────────────────────────────────────────────────────────────
  sam-p5-ib-port ──► sam-p5-ib-enhance ──► sam-p5-exit-ib ──► ═══ PHASE 5 GATE ═══

Phase 6 ──────────────────────────────────────────────────────────────────
  sam-p6-pg-schema ──► sam-p6-journal ──┐
  sam-p6-health ────────────────────────┤
  sam-p6-bar-resub ─────────────────────┤
  sam-p6-state ─────────────────────────┤
                                          ▼
                                  sam-p6-verify ──► ═══ PHASE 6 GATE ═══

Phase 7 ──────────────────────────────────────────────────────────────────
  sam-p7-loader ──► sam-p7-orb ──┐
  sam-p7-momentum ───────────────┤
  sam-p7-template ───────────────┤
  sam-p7-bundle-validate ────────┤
                                  ▼
                          sam-p7-verify ──► ═══ PHASE 7 GATE ═══

Phase 8 ──────────────────────────────────────────────────────────────────
  sam-p8-dockerfile ──► sam-p8-cli ──┐
  sam-p8-cron ───────────────────────┤
  sam-p8-quote ──────────────────────┤
                                      ▼
                              sam-p8-verify ──► ═══ PHASE 8 GATE ═══

Phase 9 ──────────────────────────────────────────────────────────────────
  sam-p9-gapscan ──► sam-p9-ai ──► sam-p9-risk ──► sam-p9-orch ──► sam-p9-verify
                                                                            │
                                                                            ▼
                                                                  ═══ PHASE 9 GATE ═══

Phase 10 ─────────────────────────────────────────────────────────────────
  sam-p10-safety ──► sam-p10-api ──► sam-p10-dashboard ──► sam-p10-verify
                                                                   │
                                                                   ▼
                                                         ═══ PHASE 10 GATE ═══

Phase 11 ─────────────────────────────────────────────────────────────────
  sam-p11-deploy ──► sam-p11-wizard ──► sam-p11-docs ──► sam-p11-e2e
                                                                  │
                                                                  ▼
                                                        ═══ PHASE 11 GATE ═══
                                                        (FULL SYSTEM VALIDATED)
```

---

## 5. Detailed Phase Ticket Plans

### Phase 0: Foundation — Skeleton & Docker Stack

> **Goal:** Empty repo with docker-compose defining all 6 services (sam-trader, sam-postgres, sam-redis, sam-futu-opend, sam-ib-gateway, sam-services). Port Ralph Loop. No trading logic yet.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 0.1 | `sam-p0-repo` | Initialize repo: .gitignore, AGENTS.md, README.md, directory structure | task | — | Create all empty dirs per plan §5. Copy `.gitignore` from v2. Write `AGENTS.md` with agent instructions. Write `README.md` with placeholder. Copy Ralph Loop scripts to `scripts/ralph/`. |
| 0.2 | `sam-p0-scaffold` | Scaffold Python package: pyproject.toml, __init__.py files, .env.example | task | — | `pyproject.toml` with package name `sam_trader`, requires-python >=3.12. All `__init__.py` files. `.env.example` with all FUTU + IB vars. |
| 0.3 | `sam-p0-dockerfile` | Dockerfile for sam-trader (Nautilus TradingNode) | task | port | Pin Nautilus image tag (TBD — 1.227.x). Multi-stage: production. Copy source, install deps. Non-root user `sam`. |
| 0.4 | `sam-p0-compose` | docker-compose.yml with all 6 services defined | task | port | All services: sam-trader, sam-postgres, sam-redis, sam-futu-opend (profile: futu), sam-ib-gateway (profile: ib), sam-services (profile: services). Network: `sam-net`. Named volumes for data persistence. |
| 0.5 | `sam-p0-postgres` | PostgreSQL service definition + init SQL | task | port | `postgres:16-alpine`. Init SQL with `fills`, `orders`, `positions` tables. `venue` column on fills. |
| 0.6 | `sam-p0-redis` | Redis service definition | task | port | `redis:7-alpine`. Volume for data persistence. |
| 0.7 | `sam-p0-futu-opend` | Futu OpenD service definition | task | port | `ghcr.io/manhinhang/futu-opend-docker:ubuntu-stable`. Port 11111. Env vars: FUTU_ACCOUNT_ID, FUTU_ACCOUNT_PWD_MD5. Volume: `futu_opend_data`. Health check. Profile: `futu`. |
| 0.8 | `sam-p0-entrypoint` | Entrypoint script with multi-service wait logic | task | port | Wait for PostgreSQL, Redis. Conditional wait for Futu OpenD, IB Gateway. Python socket-based checks. |
| 0.9 | `sam-p0-verify` | Verify stack: all containers start healthy | exit | — | `docker compose up` → all always-on containers healthy. `--profile futu` starts OpenD. `docker compose down` cleans up. |

---

### Phase 1: Configuration & Bootstrap

> **Goal:** `SamTraderConfig` loads from env vars with multi-broker fields. `main.py` bootstraps TradingNode with placeholder for both Futu and IB factories.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 1.1 | `sam-p1-config` | SamTraderConfig: frozen dataclass with Futu + IB fields | task | port+enhance | Port `config.py` from v2. Add fields: `futu_opend_host`, `futu_opend_port`, `futu_trd_env`, `futu_trd_market`, `futu_unlock_pwd_md5`, `futu_account_id`, `futu_enabled`, `ib_enabled`. `from_env()` classmethod. |
| 1.2 | `sam-p1-main` | main.py: TradingNode bootstrap with multi-broker placeholders | task | port+enhance | Port `main.py` from v2. `build_trading_node()`. Lazy imports for Futu + IB. Feature flags `futu_enabled` / `ib_enabled`. Empty data/exec client dicts (filled in later phases). Bundle loader call (may fail gracefully). |
| 1.3 | `sam-p1-integration` | Phase 1 integration test: config + bootstrap | exit | — | SamTraderConfig loads from `.env.example` defaults. `build_trading_node()` returns TradingNode without errors. `node.build()` succeeds (no clients registered). |

---

### Phase 2: Futu Market Data Adapter

> **Goal:** `FutuLiveDataClient` streams QuoteTick, TradeTick, Bar, OrderBookDelta to Nautilus message bus. Subscription quota manager tracks usage.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 2.1 | `sam-p2-connection` | Port Futu connection manager from v2 | task | port | Port `connection.py` from v2. Re-verify monkey-patch for `is_async_connect`. Update module refs to `sam_trader`. Add `FUTU_ACCOUNT_ID` env→context wiring. |
| 2.2 | `sam-p2-constants` | Futu constants: venue definitions, enum mappings, type maps | task | new | `adapters/futu/constants.py`. `FUTU_TO_NAUTILUS_VENUE` (HK→HKEX, US→NASDAQ, SH→SSE, SZ→SZSE). KLType→BarType, SecurityType→InstrumentClass, OrderType, Direction, OrderStatus mappings. Adapt from nautilus-futu constants.py (MIT). |
| 2.3 | `sam-p2-config-dc` | Futu config dataclasses: FutuDataClientConfig, FutuExecClientConfig | task | new | `adapters/futu/config.py`. Frozen dataclasses inheriting from Nautilus `LiveDataClientConfig` / `LiveExecClientConfig`. Fields: host, port, trd_env, trd_market. Shared client key pattern from nautilus-futu. |
| 2.4 | `sam-p2-parsing` | Futu parsing module: market data (QuoteTick, TradeTick, Bar, OrderBookDelta) | task | new | `adapters/futu/parsing/market_data.py`. Handler subclasses: StockQuoteHandler→QuoteTick, CurKlineHandler→Bar, TickerHandler→TradeTick, OrderBookHandler→OrderBookDelta. Subscribe/push handlers. `security_to_instrument_id` helper using venue mapping. Adapt from nautilus-futu parsing/market_data.py (MIT). |
| 2.5 | `sam-p2-data-client` | FutuLiveDataClient: push-loop architecture, subscription lifecycle | task | new | `adapters/futu/data.py`. Subclass `LiveMarketDataClient`. `_run_push_loop` (asyncio.Queue from callbacks). `subscribe`/`unsubscribe` for quote ticks, trade ticks, bars, order book. Reconnection with subscription restoration. Historical bar backfill at connect. Adapt push-loop pattern from nautilus-futu data.py. |
| 2.6 | `sam-p2-sub-mgr` | Futu subscription quota manager | task | new | `adapters/futu/subscription_manager.py`. Tracks active subscriptions per data type. `MAX_QUOTE_SUBS`, `MAX_ORDER_BOOK_SUBS`, `MAX_KLINES_SUBS` from Futu limits. Prioritize enabled-bundle instruments. Release unused after 1min idle. Log warning at 80% limit, error at 95%. |
| 2.7 | `sam-p2-exit-data` | Exit: market data subscription → QuoteTick flow | exit | — | Integration test: subscribe to TSLA.NASDAQ via Futu. Receive QuoteTick on message bus within 5s. Verify bid/ask/last prices populated. Unsubscribe → no more ticks. Subscription quota manager increments/decrements correctly. |

---

### Phase 3: Futu Execution Adapter

> **Goal:** `FutuLiveExecutionClient` submits/modifies/cancels orders. OrderFilled events flow to message bus. Account auto-discovery.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 3.1 | `sam-p3-parsing-orders` | Futu parsing module: orders (OrderStatusReport, FillReport, PositionStatusReport) | task | new | `adapters/futu/parsing/orders.py`. TradeOrderHandler→OrderEvent mapping. TradeDealHandler→OrderFilled mapping. Position status → PositionStatusReport. Adapt from nautilus-futu parsing/orders.py. |
| 3.2a | `sam_trader-9z3.4.2` | FutuLiveExecutionClient skeleton, connection, trade unlock, venue aliases | task | new | `adapters/futu/execution.py` class skeleton. `connect()` / `disconnect()`. `unlock_trade()` on connect. `_register_venue_account_aliases()` for multi-market. |
| 3.2b | `sam_trader-9z3.4.4` | FutuLiveExecutionClient order methods — submit, modify, cancel | task | new | `_submit_order`, `_modify_order`, `_cancel_order` via `OpenSecTradeContext.place_order`. Bracket order support via `order_factory.bracket()`. Unit tests for each method. |
| 3.2c | `sam_trader-9z3.4.5` | FutuLiveExecutionClient push handler wiring | task | new | Wire `TradeOrderHandler` → `OrderStatusReport` → message bus. Wire `TradeDealHandler` → `FillReport` → message bus. `_run_push_loop()` integration. |
| 3.2d | `sam_trader-9z3.4.6` | FutuLiveExecutionClient account discovery and position reconciliation | task | new | Account auto-discovery via `get_acc_list`. Position reconciliation on connect. Integration test: `test_limit_order_lifecycle`. |
| 3.3 | `sam-p3-exit-exec` | Exit: order submission → fill → OrderFilled flow | exit | — | Integration test: submit LIMIT order (paper). Verify OrderAccepted event. Order fills → OrderFilled event with correct price/qty/commission. Order cancel → OrderCancelled event. Account auto-discovered. |

> **Note:** 3.2 was decomposed from a monolithic ticket into 4 sequential sub-tasks to fit the 100-step agent budget.

---

### Phase 4: Futu Instrument Provider & TradingNode Integration

> **Goal:** `FutuInstrumentProvider` resolves symbols. Factories wired into TradingNode. Futu bundles loadable. Full Futu-only TradingNode operational.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 4.1 | `sam-p4-parsing-inst` | Futu parsing module: instruments (Equity, OptionContract, FuturesContract) | task | new | `adapters/futu/parsing/instruments.py`. `_parse_futu_equity`, `_parse_futu_option`, `_parse_futu_future`. `_precision_from_spread` for tick sizes. Lot size from `stock_basicinfo`. Adapt from nautilus-futu parsing/instruments.py. |
| 4.2 | `sam-p4-provider` | FutuInstrumentProvider: load HK+US instruments from Futu | task | new | `adapters/futu/instrument_provider.py`. Subclass `InstrumentProvider`. `load_all_async` and `load_ids_async` via `get_static_info`. `security_to_instrument_id` using venue mapping (HK.00700→00700.HKEX, US.AAPL→AAPL.NASDAQ). Caching. Instrument auto-loading from positions. |
| 4.3 | `sam-p4-factories` | Futu factories: FutuLiveDataClientFactory, FutuLiveExecClientFactory | task | new | `adapters/futu/factories.py`. Shared client pattern — one `FutuClient` per (host, port, env). `_get_shared_quote_context`, `_get_shared_trade_context`. Adapt from nautilus-futu factories.py. |
| 4.4 | `sam-p4-main-wire` | Wire Futu factories into main.py TradingNode | task | new | Register Futu factories in `build_trading_node()`. Conditional on `futu_enabled`. Inject Futu config from `SamTraderConfig`. `node.add_data_client_factory("FUTU", ...)`. |
| 4.5 | `sam-p4-bundle` | Bundle support for Futu venue | task | new | Extend `bundle_loader.py` to validate `venue: FUTU`. Update `bundles.example.yaml` with Futu examples. Futu symbology: `instrument_id: "TSLA.NASDAQ"` maps to Futu `US.TSLA` internally. Add Futu-specific risk params if needed. |
| 4.6 | `sam-p4-exit-dual` | Exit: Futu-only TradingNode starts, subscribes, and receives data | exit | — | Integration test: TradingNode with Futu factories. Load 1 Futu bundle. Verify strategy instantiated. Quote ticks arrive on message bus. Instrument resolution works (TSLA.NASDAQ → US.TSLA → QuoteTick). |

---

### Phase 5: IBKR Adapter Re-integration

> **Goal:** Port IBKR adapter from v2. Enhanced for multi-venue coexistence. Both Futu + IB work simultaneously in same TradingNode.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 5.1a | `sam_trader-9z3.6.1` | IBKR config wiring in main.py | task | port | Add `ib_enabled` flag. Construct `InteractiveBrokersDataClientConfig` and `InteractiveBrokersExecClientConfig` from `SamTraderConfig`. Wire IB env vars into `main.py`. |
| 5.1b | `sam_trader-9z3.6.5` | IBKR factory registration in main.py | task | port | Register `InteractiveBrokersLiveDataClientFactory` and `InteractiveBrokersLiveExecClientFactory` in `build_trading_node()`. Conditional on `ib_enabled`. Lazy imports. |
| 5.1c | `sam_trader-9z3.6.6` | IBKR instrument provider wiring | task | port | Register `InteractiveBrokersInstrumentProvider` in `build_trading_node()`. Instrument resolution for IB venue. No conflicts with Futu provider. |

> **Note:** 5.1 was decomposed from a monolithic ticket into 3 sequential sub-tasks.
| 5.2 | `sam-p5-ib-gateway` | IB Gateway Docker service (profile: ib) | task | port | Port `ib-gateway` service from v2 `docker-compose.yml`. `ghcr.io/gnzsnz/ib-gateway:stable`. Env vars from `.env`. VNC port. |
| 5.3 | `sam-p5-ib-enhance` | Enhance IB adapter for v3 patterns (multi-venue, new config) | task | enhance | Update IB config to use `SamTraderConfig` fields. Venue aliasing consistent with Futu. Ensure no conflicts between Futu and IB subscriptions. |
| 5.4 | `sam-p5-exit-ib` | Exit: dual-venue TradingNode (Futu + IB) | exit | — | Integration test: start TradingNode with both Futu + IB factories. Load 1 Futu bundle + 1 IB bundle. Both strategies instantiated. Data flows from both venues. No cross-venue contamination. |

---

### Phase 6: Actors & State Management

> **Goal:** TradeJournalActor, HealthMonitorActor, BarResubscriptionActor. PostgreSQL schema with venue column. Redis state persistence.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 6.1 | `sam-p6-pg-schema` | PostgreSQL schema: fills, orders, positions with venue column | task | port+enhance | Port schema from v2 `docker/postgres/init/`. Add `venue VARCHAR` to `fills` table. Add `trd_market VARCHAR` for Futu market code. |
| 6.2 | `sam-p6-journal` | TradeJournalActor: multi-venue fill journaling | task | port+enhance | Port `actors/trade_journal.py` from v2. Listen for `OrderFilled` events. Write to PostgreSQL via asyncpg. Tag fills with venue from `instrument_id.venue`. Handle both Futu and IB fill events. |
| 6.3 | `sam-p6-health` | HealthMonitorActor: heartbeat + metrics | task | port | Port `actors/health_monitor.py` from v2. Periodic heartbeat. Report: total orders, positions, venue connection status (both Futu and IB). |
| 6.4 | `sam-p6-bar-resub` | BarResubscriptionActor: bar recovery on reconnect | task | port | Port `actors/bar_resubscription.py` from v2. Monitor bar subscriptions. Re-subscribe on disconnect/reconnect. Handle both Futu and IB bar types. |
| 6.5 | `sam-p6-state` | State persistence: Redis cache database wiring | task | port | Wire Redis `CacheConfig` in `main.py`. `load_state=True, save_state=True` from env. Test: save state → restart → verify state restored. |
| 6.6 | `sam-p6-verify` | Verify: actors run, fills journaled, state persisted | exit | — | Integration test: execute trade → fill appears in PostgreSQL. HealthMonitorActor logs heartbeat. Restart with state → strategy state reloaded. |

---

### Phase 7: Strategy Library & Bundle System

> **Goal:** OrbStrategy, MomentumStrategy, strategy template. Multi-venue bundle loader. Bundle validation.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 7.1 | `sam-p7-loader` | BundleLoader: multi-venue YAML → ImportableStrategyConfig | task | port+enhance | Port `bundle_loader.py` from v2. Add `venue: FUTU` support. Validate Futu-specific config fields. `instrument_id` validation for Futu symbology. Merge bracket+risk into strategy config. |
| 7.2 | `sam-p7-orb` | OrbStrategy: port from v2 with venue-aware config | task | port+enhance | Port `strategies/orb.py` from v2. Update to use `instrument_id.venue` for order routing. ATR range filter. Breakout confirmation. Bracket orders. |
| 7.3 | `sam-p7-momentum` | MomentumStrategy: port from v2 with venue-aware config | task | port+enhance | Port `strategies/momentum.py` from v2. Venue-aware order routing. Session time guards. Always-in-market behavior. |
| 7.4 | `sam-p7-template` | Strategy template: copy-paste template for new strategies | task | port | Port `strategies/_template.py` from v2. Document all hooks. Factory usage patterns. Bracket order patterns. |
| 7.5 | `sam-p7-bundle-validate` | Bundle validation: schema check + backtest gate | task | new | Extend bundle loader with validation layer. Schema validation via Pydantic or manual. Backtest gate: run backtest before allowing live deployment. Validation report with pass/fail criteria. |
| 7.6 | `sam-p7-verify` | Verify: strategy lifecycle with Futu data | exit | — | Integration test: load OrbStrategy bundle. Strategy starts. Receives bar data from Futu. Detects breakout → submits bracket order. Order flows through execution → fills journaled. |

---

### Phase 8: sam-services Container

> **Goal:** Operations container with CLI, cron, health checks, backup, quote fetcher. Decoupled from sam-trader.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 8.1 | `sam-p8-dockerfile` | Dockerfile.services: lightweight Python 3.12 image for operations | task | new | `python:3.12-slim`. Install: fastapi, uvicorn, httpx, asyncpg, cron, pyyaml. Mount: Docker socket (ro), config/, logs/, backups/. Port 8080. |
| 8.2 | `sam-p8-cli` | sam CLI tool: status, health, backup, restore, logs | task | new | Python CLI (click or argparse). `sam status` → docker ps + health. `sam health` → deep health check. `sam backup` → pg_dump + config backup. `sam restore` → restore from backup. `sam logs [service]` → docker logs. `sam restart` → graceful restart via Redis state. |
| 8.3 | `sam-p8-cron` | Cron scheduler: daily backup, log rotation, future pipeline schedules | task | new | Cron daemon inside sam-services. Crontab: daily backup at 16:30 ET, log rotation at 03:00 HKT. Configurable via env vars. Crontab file mounted or templated. |
| 8.4 | `sam-p8-quote` | Quote fetcher: extend quote.py for Futu cache support | task | port+enhance | Port `quote.py` from v2. Add Futu quote cache query. `sam quote TSLA.NASDAQ` → bid/ask/last from Redis cache (populated by FutuLiveDataClient). Support both venues. |
| 8.5 | `sam-p8-deploy-decouple` | Deploy decoupling: move operational commands from deploy.sh to sam-services | task | refactor | Identify operational commands in deploy.sh that should live in sam-services. Move: --status, --health, --backup, --restore, --quote, --logs. Keep in deploy.sh: setup, profiles, git pull, docker compose lifecycle. |
| 8.6 | `sam-p8-verify` | Verify: sam-services starts, CLI works, cron runs | exit | — | `docker compose --profile services up -d`. `sam status` works. `sam health` reports all containers. `sam backup` creates backup. Cron daemon running. Restart sam-services → sam-trader unaffected. |

---

### Phase 9: Pre-Market Pipeline

> **Goal:** Gap scanner → AI analysis → risk manager → bundle generator → readiness report. Full autonomous pre-market pipeline.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 9.1 | `sam-p9-gapscan` | Gap scanner: scan pre-market gaps, filter rules, HK+US markets | task | new | Scan Futu market data for gap candidates. Configurable gap thresholds. Blacklist support. Trend-down filter. Deduplication. Output: ranked candidate list. |
| 9.2 | `sam-p9-ai` | AI scoring engine: candidate evaluation, recommendation grading | task | new | Score gap candidates via AI (LLM). Portfolio manager context. Recommendation grades (STRONG_BUY, BUY, HOLD, SKIP). Rule-based fast path for clear signals. |
| 9.3a | `sam_trader-9z3.10.7` | Monte Carlo position sizer | task | new | Monte Carlo simulation for position sizing. Configurable simulations (default 10,000). VaR-based risk limit. |
| 9.3b | `sam_trader-9z3.10.8` | Pre-trade risk checks | task | new | Max exposure check per venue. Daily loss limit check. Margin check. Reject trade if any check fails. |
| 9.3c | `sam_trader-9z3.10.9` | Portfolio heat monitor | task | new | Real-time portfolio heat tracking. Heat threshold warnings. Heat dashboard metric. |

> **Note:** 9.3 was decomposed from a monolithic ticket into 3 sequential sub-tasks.
| 9.4 | `sam-p9-regime` | Market regime detection: HMM-based classification, regime-aware adaptation | task | new | HMM regime classifier (trending, ranging, volatile). Regime-aware parameter adaptation (e.g., tighter stops in volatile regime). Output: regime label + adapted params. |
| 9.5a | `sam_trader-9z3.10.10` | Pipeline sequential executor | task | new | Run scan → AI → risk → regime in sequence. Pass candidate list between stages. Error handling: fail fast, log stage errors. |
| 9.5b | `sam_trader-9z3.10.11` | Bundle YAML generator | task | new | Convert approved candidates to bundle YAML. Validate against schema. Write to `config/bundles.daily.yaml`. |
| 9.5c | `sam_trader-9z3.10.12` | Readiness report | task | new | Daily readiness report generation. Console output formatted table. Optional webhook notification. Includes candidates, risks, recommendations. |

> **Note:** 9.5 was decomposed from a monolithic ticket into 3 sequential sub-tasks.
| 9.6 | `sam-p9-verify` | Verify: pipeline runs end-to-end, produces valid bundles | exit | — | Integration test: run pipeline on pre-market data. Pipeline produces ≥1 candidate. Risk checks pass. Bundle YAML generated and passes validation. Readiness report saved. |

---

### Phase 10: Safety & Dashboard

> **Goal:** Kill switch, circuit breakers, FastAPI backend, dashboard UI.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 10.1 | `sam-p10-safety` | Safety controls: kill switch, circuit breakers, emergency halt | task | new | Kill switch — immediate cancel-all + stop trading. Circuit breakers — daily loss limit, margin limit, connection loss. Emergency halt — operator-triggered. All via TradingNode signals. |
| 10.2 | `sam-p10-db` | Dashboard database: portfolio snapshots, scan history | task | new | Tables for portfolio snapshots, pipeline run history, alert log. Populated by sam-services cron jobs. |
| 10.3 | `sam-p10-api` | FastAPI backend: health, positions, fills, scan results endpoints | task | new | `GET /health` → all services status. `GET /api/positions` → current positions. `GET /api/fills?limit=N` → recent fills. `GET /api/scans/latest` → latest pipeline results. `GET /api/alerts` → active alerts. |
| 10.4 | `sam-p10-dashboard` | Static HTML dashboard: portfolio, fills, health, scans | task | new | Single-page HTML dashboard. Auto-refreshing via API calls. Portfolio table. Recent fills table. System health indicators. Pipeline results display. Alert feed. |
| 10.5 | `sam-p10-verify` | Verify: dashboard shows live data, safety controls work | exit | — | Integration test: dashboard loads. Positions table populated. Fills table populated. Kill switch triggered → all orders cancelled. Circuit breaker trips at threshold. |

---

### Phase 11: Deploy Script & E2E Validation

> **Goal:** Single-script deploy. First-run wizard. All profiles work. Full E2E gate passes.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 11.1 | `sam-p11-deploy` | deploy.sh: single-script deploy with profiles | task | port+refactor | Portable bash. Profiles: `--with-futu`, `--with-ib`, `--with-services`. Git pull/clone. Docker compose orchestration with health gating. Sequential start: postgres → redis → futu-opend → sam-trader → (optional) ib-gateway, sam-services. |
| 11.2 | `sam-p11-wizard` | First-run wizard: interactive .env generation | task | port+enhance | Interactive prompts for: trader ID, environment, Futu credentials (account, password MD5, trade password), IB credentials. Write `.env` from template. Validate inputs. |
| 11.3 | `sam-p11-docs` | Documentation: deploy guide, bundle guide, operator guide | task | new | `docs/user/DEPLOY_GUIDE.md` — prerequisites, flags, first-time walkthrough, daily ops, troubleshooting. `docs/user/BUNDLE_GUIDE.md` — bundle schema, examples, validation. `docs/user/OPERATOR_GUIDE.md` — daily checklist, monitoring, incident response. |
| 11.4 | `sam-p11-e2e` | [GATE] Phase 11 E2E validation | e2e-gate | — | Fresh macOS. `git clone` + `./deploy.sh --with-futu`. Full stack healthy. sam-trader connects to sam-futu-opend. QuoteTick arrives. Order submits + fills + journals. sam-services starts. Dashboard shows data. 1-hour soak test. `./deploy.sh --stop` cleans up. |

---

## 6. Phase Dependency Summary

```
Phase 0 ───► Phase 1 ───► Phase 2 ───► Phase 3 ───► Phase 4 ───┬──► Phase 5 ───► Phase 6 ───► Phase 7
                                                                │
                                                                └──► Phase 8 ───► Phase 9 ───► Phase 10
                                                                                                         │
                                                                                                         ▼
                                                                                                   Phase 11
```

**Parallel tracks:**
- Phases 0–1–2–3–4 are strictly sequential (each builds on previous).
- After Phase 4 (Futu fully integrated), the path splits:
  - **Track A:** Phase 5 → 6 → 7 (IBKR + actors + strategies)
  - **Track B:** Phase 8 → 9 → 10 (services + pipeline + dashboard)
- Both tracks merge before Phase 11 (final deploy + E2E).
- Track A and Track B CAN be built in parallel by different agents.

---

## 7. Ticket Count Summary

| Phase | Tickets | Type |
|-------|---------|------|
| Phase 0 | 9 | Foundation |
| Phase 1 | 3 | Config + Bootstrap |
| Phase 2 | 7 | Futu Market Data |
| Phase 3 | 6 | Futu Execution (decomposed from 3) |
| Phase 4 | 6 | Futu Integration |
| Phase 5 | 6 | IBKR Re-integration (decomposed from 4) |
| Phase 6 | 6 | Actors + State |
| Phase 7 | 6 | Strategies + Bundles |
| Phase 8 | 6 | Services Container |
| Phase 9 | 10 | Pre-Market Pipeline (decomposed from 6) |
| Phase 10 | 5 | Safety + Dashboard |
| Phase 11 | 4 | Deploy + E2E |
| **Total** | **71** | |

---

*End of ticket plan. Next: create detailed per-phase spec documents upon approval.*
