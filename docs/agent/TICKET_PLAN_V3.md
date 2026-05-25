# SAM Trader V3 - Ticket Plan & Dependency Hierarchy

> **Status:** Active (Phases 0–11 complete)
> **Date:** 2026-05-24 (Phase 0–8 complete; Phase 9 revamped with Nautilus-native architecture)
> **Source:** `docs/reference/SAM_TRADER_V3_PLAN.md` §6
> **Repo:** `github.com/samdharma/sam_trader`

---

## 1. Ticket Hierarchy Overview

```
EPIC: sam-v3 (SAM Trader V3)
│
├── FEATURE: phase-0 (Foundation - Skeleton & Docker Stack)
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
- `tests/unit/test_xxx.py::test_yyy` - validates criterion 1

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
  sam_trader-9z3.6.8 (parallel - pre-flight IB permissions check)
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
                                                                sam_trader-9z3.7.9 ──► ═══ PHASE 6 GATE ═══

Phase 7 ──────────────────────────────────────────────────────────────────
  sam_trader-9z3.8.1 ──► sam_trader-9z3.8.5 ──────────────────────────────┐
  sam_trader-9z3.8.2 ──┬──► sam_trader-9z3.8.4 ───────────────────────────┤
  sam_trader-9z3.8.3 ──┘                                                   │
                                                                            ▼
                                                                    sam_trader-9z3.8.6 ──► ═══ PHASE 7 GATE ═══

Phase 8 ───────────────────────────────────────────────────────────────────────────────────────────────────
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.2 ──► sam_trader-9z3.9.3 ──► sam_trader-9z3.9.5 ──────────────┐
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.4 ──────────────────────────────────────────────────────────┤
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.7 (LiveRiskEngine) ─────────────────────────────────────────┤
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.9 (Slippage) ───────────────────────────────────────────────┤
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.10 (PositionSnapshot) ─────────────────────────────────────┤
  sam_trader-9z3.9.1 ──► sam_trader-9z3.9.11 (PerformanceAnalyzer) ──► sam_trader-9z3.9.8 (perf CLI) ┤
                                                                                                      ▼
                                                                                            sam_trader-9z3.9.6 ──► ═══ PHASE 8 GATE ═══

Phase 9 ──────────────────────────────────────────────────────────────────
  sam_trader-9z3.10.16 ──┐
  sam_trader-9z3.10.17 ──┤
                          ▼
                   sam_trader-9z3.10.18 ──► sam_trader-9z3.10.20 ──► sam_trader-9z3.10.21 ──► sam_trader-9z3.10.22 ──► sam_trader-9z3.10.23 ──┐
                   sam_trader-9z3.10.19 ──────────────────────────────────────────────────────────────────────────────────────────────────────┤
                                                                                                                                               ▼
                                                                                     sam_trader-9z3.10.24 ──► sam_trader-9z3.10.25 ──► sam_trader-9z3.10.26 ──► sam_trader-9z3.10.27
                                                                                                                                                                                                                    │
                                                                                                                                                                                                                    ▼
                                                                                                                                                                                                          ═══ PHASE 9 GATE ═══

Phase 10 ─────────────────────────────────────────────────────────────────
  sam_trader-9z3.11.6 ──┬─────────────────────────────────────────────────┐
  sam_trader-9z3.11.7 ──┘                                                  │
                         ▼                                                  │
                  sam_trader-9z3.11.8
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

### Phase 0: Foundation - Skeleton & Docker Stack

> **Goal:** Empty repo with docker-compose defining all 6 services (sam-trader, sam-postgres, sam-redis, sam-futu-opend, sam-ib-gateway, sam-services). Port Ralph Loop. No trading logic yet.

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 0.1 | `sam-p0-repo` | Initialize repo: .gitignore, AGENTS.md, README.md, directory structure | task | - | Create all empty dirs per plan §5. Copy `.gitignore` from v2. Write `AGENTS.md` with agent instructions. Write `README.md` with placeholder. Copy Ralph Loop scripts to `scripts/ralph/`. |
| 0.2 | `sam-p0-scaffold` | Scaffold Python package: pyproject.toml, __init__.py files, .env.example | task | - | `pyproject.toml` with package name `sam_trader`, requires-python >=3.12. All `__init__.py` files. `.env.example` with all FUTU + IB vars. |
| 0.3 | `sam-p0-dockerfile` | Dockerfile for sam-trader (Nautilus TradingNode) | task | port | Pin Nautilus image tag (TBD - 1.227.x). Multi-stage: production. Copy source, install deps. Non-root user `sam`. |
| 0.4 | `sam-p0-compose` | docker-compose.yml with all 6 services defined | task | port | All services: sam-trader, sam-postgres, sam-redis, sam-futu-opend (profile: futu), sam-ib-gateway (profile: ib), sam-services (profile: services). Network: `sam-net`. Named volumes for data persistence. |
| 0.5 | `sam-p0-postgres` | PostgreSQL service definition + init SQL | task | port | `postgres:16-alpine`. Init SQL with `fills`, `orders`, `positions` tables. `venue` column on fills. |
| 0.6 | `sam-p0-redis` | Redis service definition | task | port | `redis:7-alpine`. Volume for data persistence. |
| 0.7 | `sam-p0-futu-opend` | Futu OpenD service definition | task | port | `ghcr.io/manhinhang/futu-opend-docker:ubuntu-stable`. Port 11111. Env vars: FUTU_ACCOUNT_ID, FUTU_ACCOUNT_PWD_MD5. Volume: `futu_opend_data`. Health check. Profile: `futu`. |
| 0.8 | `sam-p0-entrypoint` | Entrypoint script with multi-service wait logic | task | port | Wait for PostgreSQL, Redis. Conditional wait for Futu OpenD, IB Gateway. Python socket-based checks. |
| 0.9 | `sam-p0-verify` | Verify stack: all containers start healthy | exit | - | `docker compose up` → all always-on containers healthy. `--profile futu` starts OpenD. `docker compose down` cleans up. |

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
> **Status:** ✅ Complete (feature `9z3.2` closed; bug fix `9z3.2.1` closed)
> **Build ref:** [BUILD_PHASE_1.md](../reference/BUILD_PHASE_1.md)

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 1.1 | `sam_trader-0f6` | SamTraderConfig: frozen dataclass with Futu + IB fields | task | port+enhance | Port `config.py` from v2. 40+ env-var-driven fields across identity, IB, Futu, actors, state, PG, Redis, risk engine. `from_env()` classmethod. Boolean parsing via `lower() in ("1","true","yes")`. **Note:** `futu_account_id` removed by bug fix `9z3.2.1` — field was dead code. |
| 1.2 | *(implicit)* | main.py: TradingNode bootstrap with multi-broker placeholders | task | port+enhance | Port `main.py` from v2. `build_trading_node()`. Lazy imports for Futu + IB. Feature flags. Bundle loader with graceful fail. `ImportableActorConfig` pattern for all 6 actors. `LiveRiskEngineConfig` wiring. |
| 1.3 | *(implicit)* | Phase 1 integration test: config + bootstrap | exit | - | SamTraderConfig loads from `.env.example` defaults. `build_trading_node()` returns TradingNode without errors. `node.build()` succeeds (no clients registered). |
| 1.4 | `sam_trader-9z3.2.1` | BUG: Remove dead `futu_account_id` field from SamTraderConfig | bug | - | `futu_account_id` read from env but never used. Field + env read removed. No runtime impact — purely dead code. |

---

### Phase 2: Futu Market Data Adapter

> **Goal:** `FutuLiveDataClient` streams QuoteTick, TradeTick, Bar, OrderBookDelta to Nautilus message bus. Subscription quota manager tracks usage.
> **Status:** ✅ Complete (feature `9z3.3` closed; all 7 children closed)
> **Build ref:** [BUILD_PHASE_2.md](../reference/BUILD_PHASE_2.md)

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 2.1 | `sam_trader-9z3.3.1` | Port Futu connection manager from v2 | task | port | Port `connection.py` from v2. Context caching per (host, port, trd_env). Monkey-patch for `is_async_connect`. |
| 2.2 | `sam_trader-9z3.3.2` | Futu constants: venue definitions, enum mappings, type maps | task | new | `constants.py`. Venue, KLType→BarType, SecurityType→InstrumentClass, OrderType, Direction, OrderStatus, TrdMarket, TrdEnv, PositionSide, TimeInForce mappings. |
| 2.3 | `sam_trader-9z3.3.3` | Futu config dataclasses: FutuDataClientConfig, FutuExecClientConfig | task | new | `config.py`. Frozen dataclasses inheriting from Nautilus base configs. Shared client key pattern. |
| 2.4 | `sam_trader-9z3.3.4` | Futu parsing module: market data (QuoteTick, TradeTick, Bar, OrderBookDelta) | task | new | `parsing/market_data.py`. Handler subclasses + parsers. `security_to_instrument_id()`. |
| 2.5 | `sam_trader-9z3.3.5` | FutuLiveDataClient: push-loop architecture, subscription lifecycle | task | new | `data.py`. Subclass `LiveMarketDataClient`. Push-loop, subscribe/unsubscribe, restore on reconnect, historical backfill. |
| 2.6 | `sam_trader-9z3.3.6` | Futu subscription quota manager | task | new | `subscription_manager.py`. Per-DataType limits, priority bundles, idle release, 80%/95% thresholds. |
| 2.7 | `sam_trader-9z3.3.7` | [EXIT] Market data subscription → QuoteTick flow | exit | - | Integration test: subscribe TSLA.NASDAQ → QuoteTick on bus within 5s → unsubscribe → quota tracking verified. |

---

### Phase 3: Futu Execution Adapter

> **Goal:** `FutuLiveExecutionClient` submits/modifies/cancels orders. OrderFilled events flow to message bus. Account auto-discovery.
> **Status:** ✅ Complete (feature `9z3.4` closed; all 10 tickets closed incl bugs)
> **Build ref:** [BUILD_PHASE_3.md](../reference/BUILD_PHASE_3.md)

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 3.1 | `sam_trader-9z3.4.1` | Futu parsing module: orders (OrderStatusReport, FillReport, PositionStatusReport) | task | new | `parsing/orders.py`. TradeOrderHandler→OrderEvent mapping. TradeDealHandler→OrderFilled mapping. |
| 3.2a | `sam_trader-9z3.4.2` | FutuLiveExecutionClient skeleton, connection, trade unlock, venue aliases | task | new | `adapters/futu/execution.py` class skeleton. `connect()` / `disconnect()`. `unlock_trade()` on connect. `_register_venue_account_aliases()` for multi-market. |
| 3.2b | `sam_trader-9z3.4.4` | FutuLiveExecutionClient order methods - submit, modify, cancel | task | new | `_submit_order`, `_modify_order`, `_cancel_order` via `OpenSecTradeContext.place_order`. Bracket order support via `order_factory.bracket()`. Unit tests for each method. |
| 3.2c | `sam_trader-9z3.4.5` | FutuLiveExecutionClient push handler wiring | task | new | Wire `TradeOrderHandler` → `OrderStatusReport` → message bus. Wire `TradeDealHandler` → `FillReport` → message bus. `_run_push_loop()` integration. |
| 3.2d | `sam_trader-9z3.4.6` | FutuLiveExecutionClient account discovery and position reconciliation | task | new | Account auto-discovery via `get_acc_list`. Position reconciliation on connect. Integration test: `test_limit_order_lifecycle`. |
| 3.3 | `sam-p3-exit-exec` | Exit: order submission → fill → OrderFilled flow | exit | - | Integration test: submit LIMIT order (paper). Verify OrderAccepted event. Order fills → OrderFilled event with correct price/qty/commission. Order cancel → OrderCancelled event. Account auto-discovered. |

> **Note:** 3.2 was decomposed from a monolithic ticket into 4 sequential sub-tasks to fit the 100-step agent budget.

---

### Phase 4: Futu Instrument Provider & TradingNode Integration

> **Goal:** `FutuInstrumentProvider` resolves symbols. Factories wired into TradingNode. Futu bundles loadable. Full Futu-only TradingNode operational.
> **Status:** ✅ Complete (feature `9z3.5` closed; all 6 children closed incl EXIT)
> **Build ref:** [BUILD_PHASE_4.md](../reference/BUILD_PHASE_4.md)

| # | Ticket ID | Title | Type | Port | AC Highlights |
|---|-----------|-------|------|------|---------------|
| 4.1 | `sam_trader-9z3.5.1` | Futu parsing module: instruments | task | new | `parsing/instruments.py`. Equity, Option, Future parsers. |
| 4.2 | `sam_trader-9z3.5.2` | FutuInstrumentProvider: load HK+US instruments | task | new | `instrument_provider.py`. `load_all_async`, `load_ids_async`, caching. |
| 4.3 | `sam_trader-9z3.5.3` | Futu factories | task | new | `factories.py`. Shared client pattern. Factory classes. |
| 4.4 | `sam_trader-9z3.5.4` | Wire Futu factories into main.py | task | new | Register factories in `build_trading_node()`. Conditional on `futu_enabled`. |
| 4.5 | `sam_trader-9z3.5.5` | Bundle support for Futu venue | task | new | Extend `bundle_loader.py`. Update `bundles.example.yaml`. |
| 4.6 | `sam_trader-9z3.5.6` | [EXIT] Futu-only TradingNode | exit | - | Integration test: node starts, subscribes, receives data, instruments resolve. |

---

### Phase 5: IBKR Adapter Re-integration

> **Goal:** Port IBKR adapter from v2. Enhanced for multi-venue coexistence. Both Futu + IB work simultaneously in same TradingNode.
> **Status:** ✅ Complete (feature `9z3.6` closed; all 14 tickets closed incl EXIT + 7 bug fixes)
> **Build ref:** [BUILD_PHASE_5.md](../reference/BUILD_PHASE_5.md)
> **Build order (actual dependency chain):** 6.1 → 6.2 → 6.5 → 6.6 → 6.3 → 6.7 → 6.4  (6.8 runs in parallel)

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 5.1 | `sam_trader-9z3.6.1` | IBKR config wiring in main.py ✅ | task | Add `ib_enabled` flag. Construct `InteractiveBrokersDataClientConfig` and `InteractiveBrokersExecClientConfig` from `SamTraderConfig`. Wire IB env vars. Depends on P4 main-wire (`9z3.5.4`). |
| 5.2 | `sam_trader-9z3.6.8` | Pre-flight IB account trading permissions check ✅ | task | On IB connect, query trading permissions. If short-selling disabled and bundle requires it, log CRITICAL and disable strategy. Prevents v2 189-rejection scenario. Runs in parallel (no deps). |
| 5.3 | `sam_trader-9z3.6.2` | IB Gateway Docker service (profile: ib) | task | `ghcr.io/gnzsnz/ib-gateway:stable`. Port 4004, VNC 5900. Profile: `ib`. Env vars: TWS_USERID, TWS_PASSWORD, TRADING_MODE. Depends on P4 exit (`9z3.5.6`). Blocks factory registration. |
| 5.4 | `sam_trader-9z3.6.5` | IBKR factory registration in main.py | task | Register `InteractiveBrokersLiveDataClientFactory` and `InteractiveBrokersLiveExecClientFactory`. Conditional on `ib_enabled`. Lazy imports. Depends on IB Gateway Docker (`9z3.6.2`). Blocks instrument provider. |
| 5.5 | `sam_trader-9z3.6.6` | IBKR instrument provider wiring | task | Register `InteractiveBrokersInstrumentProvider`. Instrument resolution for IB venue. No conflicts with Futu. Depends on factory registration (`9z3.6.5`). Blocks enhance adapter. |
| 5.6 | `sam_trader-9z3.6.3` | Enhance IBKR adapter for v3 patterns | task | IB config uses `SamTraderConfig` fields. Venue aliasing consistent with Futu. SMART routing default (prevents v2 code-10311 warnings). No cross-venue contamination. Depends on instrument provider (`9z3.6.6`). Blocks post_only bug. |
| 5.7 | `sam_trader-9z3.6.7` | [BUG] IBKR post_only incompatibility - bracket orders rejected | bug | Adapter-level handling of Nautilus `post_only=True` default that IB doesn't support. Venue-aware order wrapper. Port fixes from v2 operational day 1 (108 rejections, 0 fills). Depends on enhance adapter (`9z3.6.3`). Blocks EXIT. |
| 5.8 | `sam_trader-9z3.6.4` | [EXIT] Dual-venue TradingNode (Futu + IB) | exit | Integration test: both Futu + IB factories, 1 Futu bundle + 1 IB bundle, both strategies instantiated, data flows from both venues, no cross-contamination. Depends on post_only bug (`9z3.6.7`). Blocks P6 schema (`9z3.7.1`) and P6 RejectionMonitor (`9z3.7.7`). |

---

### Phase 6: Actors & State Management

> **Goal:** TradeJournalActor, HealthMonitorActor, BarResubscriptionActor, RejectionMonitorActor, RealizedPnLTrackerActor. PostgreSQL schema with venue column. Redis state persistence.  
> **Status:** ✅ Complete (EXIT validated 2026-05-24). Actor wiring gap found and fixed during Phase 8 validation — all 5 Phase 6 actors were not registered in `main.py` (only Phase 8 PositionSnapshot was). Now wired via `ImportableActorConfig` pattern.  
> **Build order:** 7.1 (schema) is the single gateway — all actors depend on it. 7.2→7.8 form a chain (journal→realized pnl). 7.3/7.4/7.5 are parallel after schema. 7.7 has no internal deps (truly independent). All converge to EXIT. No cross-phase dependencies — Phase 6 is self-contained.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 6.1 | `sam_trader-9z3.7.1` | PostgreSQL schema: fills, orders, positions with venue column | task | `docker/postgres/init/01_schema.sql`. `venue` and `trd_market` columns. No cross-phase dependencies. Blocks all other P6 tickets. |
| 6.2 | `sam_trader-9z3.7.2` | TradeJournalActor: multi-venue fill journaling | task | Subclass Actor. Listen `OrderFilled` → write to PostgreSQL via asyncpg. Tag fills with venue. Depends on schema (`9z3.7.1`). Blocks RealizedPnL (`9z3.7.8`). |
| 6.3 | `sam_trader-9z3.7.3` | HealthMonitorActor: heartbeat + multi-venue metrics | task | Periodic heartbeat (30s). Report orders, positions, venue connection status. Depends on schema (`9z3.7.1`). Blocks EXIT. |
| 6.4 | `sam_trader-9z3.7.4` | BarResubscriptionActor: bar recovery on reconnect | task | Monitor bar subscriptions. Re-subscribe on disconnect/reconnect. Depends on schema (`9z3.7.1`). Blocks EXIT. |
| 6.5 | `sam_trader-9z3.7.5` | State persistence: Redis CacheConfig wiring | task | Wire `CacheConfig` in `main.py`. Save on shutdown, load on startup. Depends on schema (`9z3.7.1`). Blocks EXIT. |
| 6.6 | `sam_trader-9z3.7.7` | RejectionMonitorActor: per-instrument rejection circuit breaker | task | Subscribe `OrderRejected`. Track consecutive rejections per (instrument, strategy, reason). Emit `StrategyHaltRequest` at threshold (3). 15-min cooldown. Addresses v2 189-rejection no-self-halt issue. No internal deps. Blocks EXIT. |
| 6.7 | `sam_trader-9z3.7.8` | RealizedPnLTrackerActor: per-strategy realized P&L | task | Subscribe `OrderFilled`. FIFO matching per strategy. Persist to Redis (`sam:pnl:{strategy_id}:{date}`). Pure realized - no unrealized. Resets at 00:00 UTC. Addresses v2 ambiguous max_daily_loss. Depends on TradeJournal (`9z3.7.2`). Blocks EXIT. |
| 6.8 | `sam_trader-9z3.7.9` | [EXIT] Actors run, fills journaled, state persisted | exit | Integration test: fill appears in PG with venue tag. HealthMonitor heartbeat. State restored from Redis. Bar subscriptions restored on reconnect. RejectionMonitor halts on streaks. RealizedPnL computed. Depends on 7.3/7.4/7.5/7.7/7.8. Blocks P7 BundleLoader (`9z3.8.1`). |

---

### Phase 7: Strategy Library & Bundle System

> **Goal:** OrbStrategy, MomentumStrategy, strategy template. Multi-venue bundle loader. Bundle validation.
> **Build order:** 8.1 (loader), 8.2 (orb), 8.3 (momentum) are independent roots. 8.4 (template) extracted after strategies. 8.5 (validation) follows loader. All converge to EXIT. No cross-phase dependencies.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 7.1 | `sam_trader-9z3.8.1` | BundleLoader: multi-venue YAML → ImportableStrategyConfig | task | Port from v2. Validate `venue: FUTU` and `venue: IB`. Merge bracket+risk into strategy config. No cross-phase deps. Blocks validation. |
| 7.2 | `sam_trader-9z3.8.2` | OrbStrategy: port from v2 with venue-aware config | task | Port `orb.py` from v2. Configurable entry order type (MARKET/LIMIT/STOP_MARKET). `tp_post_only=False` for IB. ATR range filter, bracket orders. No internal deps. Blocks template and EXIT. |
| 7.3 | `sam_trader-9z3.8.3` | MomentumStrategy: port from v2 with venue-aware config | task | Port `momentum.py` from v2. `allowed_directions` filter (LONG/SHORT). Configurable entry order type. `tp_post_only=False` for IB. No internal deps. Blocks template and EXIT. |
| 7.4 | `sam_trader-9z3.8.4` | Strategy template: extracted from Orb + Momentum | task | Copy-paste starter. All hooks documented. Venue-aware `post_only=False` patterns. Depends on orb (`9z3.8.2`) + momentum (`9z3.8.3`). |
| 7.5 | `sam_trader-9z3.8.5` | Bundle validation: schema check + backtest gate | task | Schema validation. Strategy class existence check. Backtest gate before deployment. `sam validate-bundles` CLI. Depends on loader (`9z3.8.1`). Blocks EXIT. |
| 7.6 | `sam_trader-9z3.8.6` | [EXIT] Verify: strategy lifecycle with Futu data | exit | Integration test: OrbStrategy bundle loaded. Bar data from Futu. Breakout → bracket order → fill journaled to PG. State persists across restart. Depends on Orb/Momentum/Validation. Blocks P8 Dockerfile (`9z3.9.1`). |

---

### Phase 8: sam-services Container

> **Goal:** Operations container with CLI, cron, health checks, backup, quote fetcher, **performance analysis** (Nautilus-native PortfolioAnalyzer), and **production safeguards** (LiveRiskEngine, PositionSnapshot, Slippage). Decoupled from sam-trader.
> **Build order:** 9.1 (Dockerfile) is the single root. Five parallel tracks: (1) CLI→Cron→Deploy, (2) Quote, (3) LiveRiskEngine, (4) Slippage, (5) PositionSnapshot, (6) PerformanceAnalyzer→sam perf CLI. All converge to EXIT. No cross-phase dependencies.
> **Nautilus-native principle:** Performance stats via `PortfolioAnalyzer`, risk via `LiveRiskEngine`. Zero custom math/risk logic.
> **Revised 2026-05-23:** Expanded from 6 to 11 tickets. Added 5 Nautilus-native integrations per gap analysis.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 8.1 | `sam_trader-9z3.9.1` | Dockerfile.services: lightweight Python 3.12 for operations | task ✅ | `python:3.12-slim`. Docker CLI, buildx, cron, PG client, Redis tools. Non-root user `sam`. 3-layer health check. Port 8080. No deps. Blocks CLI, Quote, and all new tickets. **COMPLETE.** |
| 8.2 | `sam_trader-9z3.9.2` | sam CLI tool: 12 deploy + ops commands | task ○ | Python CLI (argparse). `sam status/health/backup/restore/logs/restart/quote/performance/deploy/hotfix/rollback/version/update`. Structured output (JSON/table). Depends on Dockerfile. Blocks Cron and sam perf CLI. **Reopened - commands not yet implemented.** |
| 8.3 | `sam_trader-9z3.9.3` | Cron scheduler: backup, log rotation, deploy window, pipeline, perf analysis | task ○ | Crontab entries: backup 06:00 HKT weekdays, log rotation 03:00 HKT, deploy window check, pipeline slot 08:00 HKT, **performance analysis 02:00 HKT (NEW)**. Env vars via .env_cron. Depends on CLI. Blocks Deploy. |
| 8.4 | `sam_trader-9z3.9.4` | Quote fetcher: extend for Futu cache support | task ○ | Port `quote.py` from v2. `sam quote TSLA.NASDAQ` → bid/ask/last from Redis cache. Fallback to broker query. Depends on Dockerfile. Blocks EXIT. |
| 8.5 | `sam_trader-9z3.9.5` | Deployment capabilities: deploy.sh decouple + hotfix/rollback | task ○ | Remove ops from deploy.sh. Keep setup, profiles, compose lifecycle. Stack hotfix + rollback via CLI. Depends on Cron. Blocks EXIT. |
| **8.7** | **`sam_trader-9z3.9.7`** | **LiveRiskEngine: Nautilus native pre-trade risk filtering** | task ○ | **New env vars:** RISK_MAX_ORDER_SUBMIT_RATE, RISK_MAX_ORDER_MODIFY_RATE, RISK_MAX_NOTIONAL_PER_ORDER (JSON), RISK_BYPASS. Wire `LiveRiskEngineConfig` into `main.py` → `TradingNodeConfig`. **ZERO custom risk logic.** Depends on Dockerfile. Blocks EXIT. |
| **8.10** | **`sam_trader-9z3.9.10`** | **PositionSnapshotActor: periodic PG positions writes** | task ○ | **New actor:** `PositionSnapshotActor(Actor)`. Polls `self.cache.positions()` every 60s. Upserts into existing PG `positions` table. Wired in `main.py` with `ACTOR_POSITION_SNAPSHOT_ENABLED` env var. Depends on Dockerfile. Blocks EXIT. |
| **8.11** | **`sam_trader-9z3.9.11`** | **PerformanceAnalyzer: Nautilus PortfolioAnalyzer integration** | task ○ | **New PG table:** `performance_stats`. Query fills → convert to Nautilus Trade objects → feed to `PortfolioAnalyzer` → `calculate_statistics()` → store all 17 Rust-backed stats. Nightly cron. **ZERO custom math.** Depends on Dockerfile. Blocks sam perf CLI. |
| **8.9** | **`sam_trader-9z3.9.9`** | **Slippage tracking: column + TradeJournalActor update** | task ○ | Add `slippage NUMERIC(24,8)` to fills table. Compute slippage = fill_price - expected_price. Signed value (+ = unfavorable). Depends on Dockerfile. Blocks EXIT. |
| **8.8** | **`sam_trader-9z3.9.8`** | **sam performance CLI: Nautilus-powered performance stats display** | task ○ | `sam performance [--strategy <id>] [--days 30] [--json]`. Reads `performance_stats` PG table. Displays: Sharpe, Sortino, CAGR, MaxDrawdown, WinRate, ProfitFactor, etc. Depends on CLI (`9z3.9.2`) + PerformanceAnalyzer (`9z3.9.11`). Blocks EXIT. |
| 8.6 | `sam_trader-9z3.9.6` | [EXIT] Verify: all Phase 8 components, Nautilus integrations, perf stats | exit | Expanded: `docker compose --profile services up -d`. `sam status/health/backup/performance` work. PerformanceAnalyzer writes stats. PositionSnapshotActor upserts positions. LiveRiskEngine enforces rate limits. Slippage tracked in fills. Restart sam-services → sam-trader unaffected. Depends on tickets 3,4,5,7,8,9,10. Blocks P9 gapscan + regime. |

---

### Phase 9: Pre-Market Pipeline

> **Goal:** Nautilus-native pre-market pipeline using broker real-time data feeds (Futu + IB). Gap scanner → AI analysis → risk manager → regime detection → bundle generator → readiness report.
> **Status:** ✅ Complete (all 12 tickets closed incl EXIT 9z3.10.27)
> **Build ref:** [BUILD_PHASE_9.md](../reference/BUILD_PHASE_9.md)
> **Build order:** Tickets numbered sequentially matching build order. Two parallel tracks converge at executor — Track A: watchlist → quote collector → gap scanner → AI → MC → pre-trade → heat → executor. Track B: regime → executor. Then: executor → bundle-gen → report → EXIT.

| # | Ticket ID | Title | Type | Ralph Order |
|---|-----------|-------|------|-------------|
| 1 | `sam_trader-9z3.10.16` | PreMarketWatchlist — config-driven symbol universe | task | **1st** (root) |
| 2 | `sam_trader-9z3.10.17` | QuoteCollectionService — reusable Nautilus data client wrapper | task | **2nd** (root, parallel with 16) |
| 3 | `sam_trader-9z3.10.18` | PreMarketGapScanner — Nautilus-native broker data scanner | task | **3rd** (depends on 16, 17) |
| 4 | `sam_trader-9z3.10.19` | Market Regime Detection — HMM classification | task | **4th** (independent root, parallel track) |
| 5 | `sam_trader-9z3.10.20` | AI Scoring Engine — LLM candidate evaluation | task | **5th** (depends on 18) |
| 6 | `sam_trader-9z3.10.21` | Monte Carlo Position Sizer | task | **6th** (depends on 20) |
| 7 | `sam_trader-9z3.10.22` | Pre-trade Risk Checks | task | **7th** (depends on 21) |
| 8 | `sam_trader-9z3.10.23` | Portfolio Heat Monitor | task | **8th** (depends on 22) |
| 9 | `sam_trader-9z3.10.24` | Pipeline Sequential Executor | task | **9th** (depends on 19, 23) |
| 10 | `sam_trader-9z3.10.25` | Bundle YAML Generator | task | **10th** (depends on 24) |
| 11 | `sam_trader-9z3.10.26` | Readiness Report | task | **11th** (depends on 25) |
| 12 | `sam_trader-9z3.10.27` | [EXIT] Pipeline E2E Validation | exit | **12th** (depends on 26) |

---

### Phase 10: Safety & Dashboard

> **Goal:** Operator safety controls (kill switch, circuit breakers) + basic read-only dashboard showing existing Phase 6/8 data. No new tables, no FastAPI.
> **Status:** ✅ Complete (all 3 tickets closed incl EXIT 9z3.11.8)
> **Build ref:** [BUILD_PHASE_10.md](../reference/BUILD_PHASE_10.md)
> **Build order:** Safety (11.6) and Dashboard (11.7) are parallel roots after Phase 9 EXIT. EXIT (11.8) depends on both.

| # | Ticket ID | Title | Type | Ralph Order |
|---|-----------|-------|------|-------------|
| 1 | `sam_trader-9z3.11.6` | Safety controls — kill switch, circuit breakers, emergency halt | task | **1st** (parallel with 7) |
| 2 | `sam_trader-9z3.11.7` | Basic dashboard — single HTML page: fills, positions, P&L, health | task | **2nd** (parallel with 6) |
| 3 | `sam_trader-9z3.11.8` | [EXIT] Verify safety controls + dashboard | exit | **3rd** (depends on 6, 7) |

**Simplification notes:**
- Removed: FastAPI backend (overkill — uses simple http.server)
- Removed: Dashboard database ticket (reads existing fills/orders/positions tables)
- Removed: Pipeline results + alert feed sections (Phase 9 not yet built)
- Circuit breakers consume existing actor data: RealizedPnLTrackerActor (Phase 6), RejectionMonitorActor (Phase 6), HealthMonitorActor (Phase 6)
- Kill switch uses LiveRiskEngine trading_state (already wired, Phase 8)

---

### Phase 11: Deploy Script & E2E Validation

> **Goal:** Single-script deploy. First-run wizard. All profiles work. Full E2E gate passes.
> **Status:** ✅ Complete (all 4 tickets closed incl EXIT 9z3.12.4)
> **Build order:** Linear chain - deploy.sh → wizard → docs → E2E. No cross-phase dependencies. Final phase, EXIT has no downstream blocks.

| # | Ticket ID | Title | Type | AC Highlights |
|---|-----------|-------|------|---------------|
| 11.1 | `sam_trader-9z3.12.1` | deploy.sh: single-script deploy with profiles | task | Portable bash. Profiles: `--with-futu`, `--with-ib`, `--with-services`. Git pull/clone. Sequential start with health gating. Under 300 lines. No deps. Blocks wizard. |
| 11.2 | `sam_trader-9z3.12.2` | First-run wizard: interactive .env generation | task | Interactive prompts for trader ID, env, Futu + IB credentials. Write `.env` from template. Validate inputs. Mask passwords. Depends on deploy.sh (`9z3.12.1`). Blocks docs. |
| 11.3 | `sam_trader-9z3.12.3` | User documentation: deploy guide, bundle guide, operator guide | task | `DEPLOY_GUIDE.md`, `BUNDLE_GUIDE.md`, `OPERATOR_GUIDE.md`. Prerequisites, daily ops, troubleshooting, incident response. Depends on wizard (`9z3.12.2`). Blocks E2E. |
| 11.4 | `sam_trader-9z3.12.4` | [GATE] Full E2E validation: fresh deploy, Futu live, 1-hour soak | exit | Fresh macOS: `git clone` + `./deploy.sh --with-futu`. Full stack healthy. QuoteTick arrives. Order → fill → journal. Dashboard shows data. 1-hour soak test. `./deploy.sh --stop` cleans up. Depends on docs (`9z3.12.3`). Terminal gate - no blocks. |

---

## 6. Phase Dependency Summary

```
Phase 0 ✅ ───► Phase 1 ✅ ───► Phase 2 ✅ ───► Phase 3 ✅ ───► Phase 4 ✅ ───┬──► Phase 5 ✅ ───► Phase 6 ✅ ───► Phase 7 ✅
                                                                                 │
                                                                                 └──► Phase 8 ✅ ───► Phase 9 ✅ ───► Phase 10 ✅
                                                                                                                              │
                                                                                                                              ▼
                                                                                                                        Phase 11 ✅
```

**Parallel tracks:**
- Phases 0-1-2-3-4 are strictly sequential (each builds on previous).
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
| Phase 6 | 9 | Actors + State (2 gap-remediation actors; EXIT renumbered 7.6→7.9) |
| Phase 7 | 6 | Strategies + Bundles |
| Phase 8 | 11 | Services Container (revised: +5 Nautilus-native integrations) |
| Phase 9 | 12 | Pre-Market Pipeline (renumbered: sequential 10.16–10.27 matching build order) |
| Phase 10 | 3 | Safety + Dashboard (simplified: no FastAPI, no new tables) |
| Phase 11 | 4 | Deploy + E2E |
| **Total** | **93** | (revised Phase 8: 6→11 tickets) |

---

*End of ticket plan. Next: create detailed per-phase spec documents upon approval.*
