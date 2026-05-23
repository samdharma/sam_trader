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
│   ↑ depends on: phase-7 (beads gated via P7 EXIT → P8 Dockerfile; logically can follow phase-4)
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

| Label | Scope | Allowed On |
|-------|-------|------------|
| `phase-0` through `phase-11` | Roadmap phase marker | All tickets |
| `meta-grouping` | Container ticket (EPIC or FEATURE) | EPIC, FEATURE only |
| `epic` | Top-level epic or sub-epic | EPIC only |
| `feature` | Feature grouping ticket (parent of tasks) | FEATURE only |
| `task` | Atomic work ticket | CHILD tickets only |
| `exit` | Phase exit / validation gate | EXIT tickets only |

**Label Rules (see `AGENTS.md` §Beads Ticket Hierarchy for full details):**
- EPIC and FEATURE tickets MAY have multiple labels (`<phase-tag>` + `meta-grouping`).
- All CHILD work tickets (task, bug, test, docs) MUST have exactly **one** label: the phase tag.
- EXIT tickets have exactly two labels: `exit` + `<phase-tag>`.
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

Phase 0-H (Hardening) ────────────────────────────────────────────────────
  sam_trader-9z3.1.13 ──► sam_trader-9z3.1.14 ──► sam_trader-9z3.1.15 ──┬──► sam_trader-9z3.1.16 ──► sam_trader-9z3.1.17
                                                                          ├──► sam_trader-9z3.1.18
                                                                          └──► sam_trader-9z3.1.19
                                                                                      │
                                                                                      ▼
                                                                            sam_trader-9z3.1.20 ──► ═══ PHASE 0-H GATE ═══

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
  sam_trader-9z3.6.1 ──► sam_trader-9z3.6.2 ──► sam_trader-9z3.6.5 ──► sam_trader-9z3.6.6
                                                                                  │
                                                                                  ▼
                                                                        sam_trader-9z3.6.3 ──► sam_trader-9z3.6.7 ──► sam_trader-9z3.6.4
  sam_trader-9z3.6.8 (parallel — pre-flight IB permissions check)
                                                                                  │
                                                                                  ▼
                                                                        ═══ PHASE 5 GATE ═══

Phase 6 ──────────────────────────────────────────────────────────────────
  sam_trader-9z3.7.1 ──► sam_trader-9z3.7.2 ──► sam_trader-9z3.7.8 ──┐
  sam_trader-9z3.7.1 ──► sam_trader-9z3.7.3 ─────────────────────────┤
  sam_trader-9z3.7.1 ──► sam_trader-9z3.7.4 ─────────────────────────┤
  sam_trader-9z3.7.1 ──► sam_trader-9z3.7.5 ─────────────────────────┤
  sam_trader-9z3.7.7 ─────────────────────────────────────────────────┤
                                                                        ▼
                                                                sam_trader-9z3.7.6 ──► ═══ PHASE 6 GATE ═══

Phase 7 ──────────────────────────────────────────────────────────────────
  sam_trader-9z3.8.1 ──► sam_trader-9z3.8.4 ──► sam_trader-9z3.8.2 ──┐
  sam_trader-9z3.8.1 ──► sam_trader-9z3.8.4 ──► sam_trader-9z3.8.3 ──┤
  sam_trader-9z3.8.1 ──► sam_trader-9z3.8.5 ──────────────────────────┤
                                                                        ▼
                                                                sam_trader-9z3.8.6 ──► ═══ PHASE 7 GATE ═══

Phase 8 ──────────────────────────────────────────────────────────────────
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.2 ──► sam_trader-9z3.9.3 ──► sam_trader-9z3.9.5 ──┐
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.4 ────────────────────────────────────────────────┤
                                                                                              ▼
                                                                                    sam_trader-9z3.9.6 ──► ═══ PHASE 8 GATE ═══

Phase 9 ──────────────────────────────────────────────────────────────────
  sam_trader-9z3.10.1 ──► sam_trader-9z3.10.2 ──► sam_trader-9z3.10.7 ──► sam_trader-9z3.10.8 ──► sam_trader-9z3.10.9 ──┐
  sam_trader-9z3.10.4 ───────────────────────────────────────────────────────────────────────────────────────────────────┤
                                                                                                                         ▼
                                                                                                   sam_trader-9z3.10.10 ──► sam_trader-9z3.10.11 ──► sam_trader-9z3.10.12 ──► sam_trader-9z3.10.6
                                                                                                                                                                                                          │
                                                                                                                                                                                                          ▼
                                                                                                                                                                                                ═══ PHASE 9 GATE ═══

Phase 10 ─────────────────────────────────────────────────────────────────
  sam_trader-9z3.11.1 ──┬─────────────────────────────────────────────────┐
  sam_trader-9z3.11.2 ──┤                                                  │
                         ▼                                                  │
                  sam_trader-9z3.11.3 ──► sam_trader-9z3.11.4 ──► sam_trader-9z3.11.5
                                                                                  │
                                                                                  ▼
                                                                        ═══ PHASE 10 GATE ═══

Phase 11 ─────────────────────────────────────────────────────────────────
  sam_trader-9z3.12.1 ──► sam_trader-9z3.12.2 ──► sam_trader-9z3.12.3 ──► sam_trader-9z3.12.4
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

### Phase 0-H: Docker Stack Hardening

> **Goal:** Retrofit Phase 0 Docker foundation with operational robustness: lightweight base image, layered health checks, host monitoring with cooldown, standardized backup/restore, and Futu first-login documentation.
> **Parent:** `sam_trader-9z3.1` (Phase 0 feature). **Label:** `phase-0`.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 0.10 | `sam_trader-9z3.1.13` | Futu OpenD: switch to debian:stable-slim + tini init | task | FROM debian:stable-slim, tini init, <70MB image, build on Apple Silicon |
| 0.11 | `sam_trader-9z3.1.14` | Futu OpenD: Python XML startup replaces sed-based start.sh | task | xml.etree.ElementTree, validates XML, graceful error on missing env vars |
| 0.12 | `sam_trader-9z3.1.15` | Futu OpenD: layered health check | task | L1 pgrep, L2 TCP connect, L3 log scan, Dockerfile + compose aligned |
| 0.13 | `sam_trader-9z3.1.16` | Standardize 3-layer health checks across all containers | task | HEALTHCHECK_PATTERN.md, postgres/redis/trader/ib-gateway/services checks |
| 0.14 | `sam_trader-9z3.1.17` | Host-level container monitor with cooldown protection | task | docker ps polling, restart counter, 3-restart/15min cooldown, launchd plist |
| 0.15 | `sam_trader-9z3.1.18` | Backup/restore system via sam-services | task | pg_dump, Redis BGSAVE, Futu volume, config backup, HKT 06:00 weekdays, 30-day retention |
| 0.16 | `sam_trader-9z3.1.19` | Document Futu OpenD first-time login and terminal access | task | FUTU_FIRST_LOGIN.md, questionnaire URL, telnet access, MD5 generation, troubleshooting |
| 0.17 | `sam_trader-9z3.1.20` | Exit gate: hardened stack builds, health, monitor, backup | exit | All containers healthy within 2min, monitor detects restart, backup creates valid archive, no regression |

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
> **Original monolithic ticket `sam-p5-ib-port` was decomposed into sub-tickets 9z3.6.1, 9z3.6.5, 9z3.6.6.**
> **Build order (actual dependency chain):** 6.1 → 6.2 → 6.5 → 6.6 → 6.3 → 6.7 → 6.4  (6.8 runs in parallel)

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 5.1 | `sam_trader-9z3.6.1` | IBKR config wiring in main.py ✅ | task | Add `ib_enabled` flag. Construct `InteractiveBrokersDataClientConfig` and `InteractiveBrokersExecClientConfig` from `SamTraderConfig`. Wire IB env vars. Depends on P4 main-wire (`9z3.5.4`). |
| 5.2 | `sam_trader-9z3.6.8` | Pre-flight IB account trading permissions check ✅ | task | On IB connect, query trading permissions. If short-selling disabled and bundle requires it, log CRITICAL and disable strategy. Prevents v2 189-rejection scenario. Runs in parallel (no deps). |
| 5.3 | `sam_trader-9z3.6.2` | IB Gateway Docker service (profile: ib) | task | `ghcr.io/gnzsnz/ib-gateway:stable`. Port 4004, VNC 5900. Profile: `ib`. Env vars: TWS_USERID, TWS_PASSWORD, TRADING_MODE. Depends on P4 exit (`9z3.5.6`). Blocks factory registration. |
| 5.4 | `sam_trader-9z3.6.5` | IBKR factory registration in main.py | task | Register `InteractiveBrokersLiveDataClientFactory` and `InteractiveBrokersLiveExecClientFactory`. Conditional on `ib_enabled`. Lazy imports. Depends on IB Gateway Docker (`9z3.6.2`). Blocks instrument provider. |
| 5.5 | `sam_trader-9z3.6.6` | IBKR instrument provider wiring | task | Register `InteractiveBrokersInstrumentProvider`. Instrument resolution for IB venue. No conflicts with Futu. Depends on factory registration (`9z3.6.5`). Blocks enhance adapter. |
| 5.6 | `sam_trader-9z3.6.3` | Enhance IBKR adapter for v3 patterns | task | IB config uses `SamTraderConfig` fields. Venue aliasing consistent with Futu. SMART routing default (prevents v2 code-10311 warnings). No cross-venue contamination. Depends on instrument provider (`9z3.6.6`). Blocks post_only bug. |
| 5.7 | `sam_trader-9z3.6.7` | [BUG] IBKR post_only incompatibility — bracket orders rejected | bug | Adapter-level handling of Nautilus `post_only=True` default that IB doesn't support. Venue-aware order wrapper. Port fixes from v2 operational day 1 (108 rejections, 0 fills). Depends on enhance adapter (`9z3.6.3`). Blocks EXIT. |
| 5.8 | `sam_trader-9z3.6.4` | [EXIT] Dual-venue TradingNode (Futu + IB) | exit | Integration test: both Futu + IB factories, 1 Futu bundle + 1 IB bundle, both strategies instantiated, data flows from both venues, no cross-contamination. Depends on post_only bug (`9z3.6.7`). Blocks P6 schema (`9z3.7.1`) and P6 RejectionMonitor (`9z3.7.7`). |

---

### Phase 6: Actors & State Management

> **Goal:** TradeJournalActor, HealthMonitorActor, BarResubscriptionActor, RejectionMonitorActor, RealizedPnLTrackerActor. PostgreSQL schema with venue column. Redis state persistence.
> **Build order:** 7.1 (schema) is the single gateway — all actors depend on it. 7.2→7.8 form a chain (journal→realized pnl). 7.3/7.4/7.5 are parallel after schema. 7.7 has no actor deps (only P5 exit). All converge to EXIT.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 6.1 | `sam_trader-9z3.7.1` | PostgreSQL schema: fills, orders, positions with venue column | task | `docker/postgres/init/01_schema.sql`. `venue` and `trd_market` columns. Depends on P5 exit (`9z3.6.4`). Blocks all other P6 tickets. |
| 6.2 | `sam_trader-9z3.7.2` | TradeJournalActor: multi-venue fill journaling | task | Subclass Actor. Listen `OrderFilled` → write to PostgreSQL via asyncpg. Tag fills with venue. Depends on schema (`9z3.7.1`). Blocks RealizedPnL (`9z3.7.8`). |
| 6.3 | `sam_trader-9z3.7.3` | HealthMonitorActor: heartbeat + multi-venue metrics | task | Periodic heartbeat (30s). Report orders, positions, venue connection status. Depends on schema (`9z3.7.1`). Blocks EXIT. |
| 6.4 | `sam_trader-9z3.7.4` | BarResubscriptionActor: bar recovery on reconnect | task | Monitor bar subscriptions. Re-subscribe on disconnect/reconnect. Depends on schema (`9z3.7.1`). Blocks EXIT. |
| 6.5 | `sam_trader-9z3.7.5` | State persistence: Redis CacheConfig wiring | task | Wire `CacheConfig` in `main.py`. Save on shutdown, load on startup. Depends on schema (`9z3.7.1`). Blocks EXIT. |
| 6.6 | `sam_trader-9z3.7.7` | RejectionMonitorActor: per-instrument rejection circuit breaker | task | Subscribe `OrderRejected`. Track consecutive rejections per (instrument, strategy, reason). Emit `StrategyHaltRequest` at threshold (3). 15-min cooldown. Addresses v2 189-rejection no-self-halt issue. Depends on P5 exit (`9z3.6.4`). Blocks EXIT. |
| 6.7 | `sam_trader-9z3.7.8` | RealizedPnLTrackerActor: per-strategy realized P&L | task | Subscribe `OrderFilled`. FIFO matching per strategy. Persist to Redis (`sam:pnl:{strategy_id}:{date}`). Pure realized — no unrealized. Resets at 00:00 UTC. Addresses v2 ambiguous max_daily_loss. Depends on TradeJournal (`9z3.7.2`). Blocks EXIT. |
| 6.8 | `sam_trader-9z3.7.6` | [EXIT] Actors run, fills journaled, state persisted | exit | Integration test: fill appears in PG with venue tag. HealthMonitor heartbeat. State restored from Redis. Bar subscriptions restored on reconnect. Depends on 7.3/7.4/7.5/7.7/7.8. Blocks P7 BundleLoader (`9z3.8.1`). |

---

### Phase 7: Strategy Library & Bundle System

> **Goal:** OrbStrategy, MomentumStrategy, strategy template. Multi-venue bundle loader. Bundle validation.
> **Build order:** 8.1 (loader) → 8.4 (template) → 8.2 (orb) + 8.3 (momentum). 8.5 (validation) is parallel after loader. All converge to EXIT.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 7.1 | `sam_trader-9z3.8.1` | BundleLoader: multi-venue YAML → ImportableStrategyConfig | task | Port from v2. Validate `venue: FUTU` and `venue: IB`. Merge bracket+risk into strategy config. Depends on P6 exit (`9z3.7.6`). Blocks template and validation. |
| 7.2 | `sam_trader-9z3.8.4` | Strategy template: copy-paste template for new strategies | task | Port `_template.py` from v2. Document all hooks. Venue-aware `post_only=False` patterns for IB. Depends on loader (`9z3.8.1`). Blocks Orb and Momentum. |
| 7.3 | `sam_trader-9z3.8.2` | OrbStrategy: port from v2 with venue-aware config | task | Port `orb.py` from v2. Configurable entry order type (MARKET/LIMIT/STOP_MARKET). `tp_post_only=False` for IB. ATR range filter, bracket orders. Depends on template (`9z3.8.4`). Blocks EXIT. |
| 7.4 | `sam_trader-9z3.8.3` | MomentumStrategy: port from v2 with venue-aware config | task | Port `momentum.py` from v2. `allowed_directions` filter (LONG/SHORT). Configurable entry order type. `tp_post_only=False` for IB. Depends on template (`9z3.8.4`). Blocks EXIT. |
| 7.5 | `sam_trader-9z3.8.5` | Bundle validation: schema check + backtest gate | task | Schema validation. Strategy class existence check. Backtest gate before deployment. `sam validate-bundles` CLI. Depends on loader (`9z3.8.1`). Blocks EXIT. |
| 7.6 | `sam_trader-9z3.8.6` | [EXIT] Verify: strategy lifecycle with Futu data | exit | Integration test: OrbStrategy bundle loaded. Bar data from Futu. Breakout → bracket order → fill journaled to PG. State persists across restart. Depends on Orb/Momentum/Validation. Blocks P8 Dockerfile (`9z3.9.1`). |

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
> **Parent tickets `9z3.10.3` (Risk Manager) and `9z3.10.5` (Pipeline Orchestrator) are closed-superseded.** Work distributed to sub-tickets 10.7–10.12.
> **Build order:** Two parallel tracks converge at executor — Track A: scan → AI → MC sizer → pre-trade → heat → executor. Track B: regime → executor. Then: executor → bundle-gen → report → EXIT.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 9.1 | `sam_trader-9z3.10.1` | Gap scanner: pre-market gaps, filter rules, HK+US markets | task | Scan Futu data for gap candidates. Configurable thresholds, blacklist, trend-down filter. Output: ranked candidate list. Depends on P8 exit (`9z3.9.6`). Blocks AI scoring. |
| 9.2 | `sam_trader-9z3.10.2` | AI scoring engine: candidate evaluation, recommendation grading | task | Score candidates via LLM. Grades: STRONG_BUY, BUY, HOLD, SKIP. Rule-based fast path. Depends on gap scanner (`9z3.10.1`). Blocks MC sizer. |
| 9.3 | `sam_trader-9z3.10.7` | Monte Carlo position sizer | task | Monte Carlo simulation (default 10,000). VaR-based risk limit. Depends on AI scoring (`9z3.10.2`). Blocks pre-trade checks. |
| 9.4 | `sam_trader-9z3.10.8` | Pre-trade risk checks | task | Max exposure per venue. Daily loss limit. Margin check. Reject if any fails. Depends on MC sizer (`9z3.10.7`). Blocks heat monitor. |
| 9.5 | `sam_trader-9z3.10.9` | Portfolio heat monitor | task | Real-time heat tracking. Heat threshold warnings. Depends on pre-trade (`9z3.10.8`). Blocks executor. |
| 9.6 | `sam_trader-9z3.10.4` | Market regime detection: HMM-based classification | task | HMM classifier (trending/ranging/volatile). Regime-aware parameter adaptation. Depends on P8 exit (`9z3.9.6`). Blocks executor. |
| 9.7 | `sam_trader-9z3.10.10` | Pipeline sequential executor | task | Run scan→AI→risk→regime in sequence. Pass candidates between stages. Fail-fast error handling. Depends on heat monitor (`9z3.10.9`) + regime (`9z3.10.4`). Blocks bundle-gen. |
| 9.8 | `sam_trader-9z3.10.11` | Bundle YAML generator | task | Convert approved candidates to bundle YAML. Validate against schema. Write to `config/bundles.daily.yaml`. Depends on executor (`9z3.10.10`). Blocks report. |
| 9.9 | `sam_trader-9z3.10.12` | Readiness report | task | Daily report generation. Console table + optional webhook. Includes candidates, risks, recommendations. Depends on bundle-gen (`9z3.10.11`). Blocks EXIT. |
| 9.10 | `sam_trader-9z3.10.6` | [EXIT] Pipeline runs end-to-end, produces valid bundles | exit | Integration test: pipeline on pre-market data. ≥1 candidate, risk checks pass, bundle YAML valid, report saved. `sam pipeline run` completes. Depends on report (`9z3.10.12`). Blocks P10 safety + DB. |

> **Note:** Original parent tickets `9z3.10.3` (Risk Manager) and `9z3.10.5` (Pipeline Orchestrator) are **closed-superseded**. Their scope is covered by sub-tickets 10.7–10.9 and 10.10–10.12 respectively, plus the EXIT gate integration test.

---

### Phase 10: Safety & Dashboard

> **Goal:** Kill switch, circuit breakers, FastAPI backend, dashboard UI.
> **Circuit breaker expanded to 5 triggers** (was 3): adds REJECTION_STREAK (via RejectionMonitorActor) and REALIZED_LOSS_LIMIT (via RealizedPnLTrackerActor).

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 10.1 | `sam_trader-9z3.11.1` | Safety controls: kill switch, circuit breakers, emergency halt | task | 5 circuit breaker triggers: DAILY_PNL, MARGIN_LIMIT, CONNECTIVITY_LOSS, REJECTION_STREAK (from 9z3.7.7), REALIZED_LOSS_LIMIT (from 9z3.7.8). Kill switch cancels all order + stops trading. CLI: `sam kill`, `sam halt`. Depends on P9 exit (`9z3.10.6`). Blocks API. |
| 10.2 | `sam_trader-9z3.11.2` | Dashboard database: portfolio snapshots, scan history, alert log | task | New PG tables: `portfolio_snapshots`, `pipeline_runs`, `alert_log`. Populated by sam-services cron. Depends on P9 exit (`9z3.10.6`). Blocks API. |
| 10.3 | `sam_trader-9z3.11.3` | FastAPI backend: health, positions, fills, scan results endpoints | task | `GET /health`, `/api/positions`, `/api/fills`, `/api/scans/latest`, `/api/alerts`. CORS for localhost. Depends on safety (`9z3.11.1`) + DB (`9z3.11.2`). Blocks dashboard. |
| 10.4 | `sam_trader-9z3.11.4` | Static HTML dashboard: portfolio, fills, health, scans | task | Single-page auto-refreshing HTML. Portfolio table, fills table, health indicators, alert feed. No external CDN. Depends on API (`9z3.11.3`). Blocks EXIT. |
| 10.5 | `sam_trader-9z3.11.5` | [EXIT] Verify: dashboard shows live data, safety controls work | exit | Integration: dashboard loads, positions populated, fills populated. Kill switch → all orders cancelled. Circuit breaker trips at threshold. Depends on dashboard (`9z3.11.4`). Blocks P11 deploy (`9z3.12.1`). |

---

### Phase 11: Deploy Script & E2E Validation

> **Goal:** Single-script deploy. First-run wizard. All profiles work. Full E2E gate passes.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 11.1 | `sam_trader-9z3.12.1` | deploy.sh: single-script deploy with profiles | task | Portable bash. Profiles: `--with-futu`, `--with-ib`, `--with-services`. Git pull/clone. Sequential start with health gating. Under 300 lines. Depends on P10 exit (`9z3.11.5`). Blocks wizard. |
| 11.2 | `sam_trader-9z3.12.2` | First-run wizard: interactive .env generation | task | Interactive prompts for trader ID, env, Futu + IB credentials. Write `.env` from template. Validate inputs. Mask passwords. Depends on deploy.sh (`9z3.12.1`). Blocks docs. |
| 11.3 | `sam_trader-9z3.12.3` | User documentation: deploy guide, bundle guide, operator guide | task | `DEPLOY_GUIDE.md`, `BUNDLE_GUIDE.md`, `OPERATOR_GUIDE.md`. Prerequisites, daily ops, troubleshooting, incident response. Depends on wizard (`9z3.12.2`). Blocks E2E. |
| 11.4 | `sam_trader-9z3.12.4` | [GATE] Full E2E validation: fresh deploy, Futu live, 1-hour soak | exit | Fresh macOS: `git clone` + `./deploy.sh --with-futu`. Full stack healthy. QuoteTick arrives. Order → fill → journal. Dashboard shows data. 1-hour soak test. `./deploy.sh --stop` cleans up. Depends on docs (`9z3.12.3`). |

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
| Phase 0 | 17 | Foundation + Hardening |
| Phase 1 | 3 | Config + Bootstrap |
| Phase 2 | 7 | Futu Market Data |
| Phase 3 | 6 | Futu Execution (decomposed from 3) |
| Phase 4 | 6 | Futu Integration |
| Phase 5 | 8 | IBKR Re-integration (decomposed from 1; 2 bug fixes) |
| Phase 6 | 8 | Actors + State (2 gap-remediation actors added) |
| Phase 7 | 6 | Strategies + Bundles |
| Phase 8 | 6 | Services Container |
| Phase 9 | 12 | Pre-Market Pipeline (2 parent closed-superseded; 10 active) |
| Phase 10 | 5 | Safety + Dashboard |
| Phase 11 | 4 | Deploy + E2E |
| **Total** | **88** | |

---

*End of ticket plan. Next: create detailed per-phase spec documents upon approval.*
