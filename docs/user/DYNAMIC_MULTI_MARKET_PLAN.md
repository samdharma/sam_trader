# SAM Trader V3 — Dynamic Multi-Market Operations Plan

> **Status:** Planning — Awaiting Review  
> **Date:** 2026-05-27  
> **Purpose:** Extend SAM Trader V3 to operate dynamically across HK and US markets using Nautilus-standard patterns. Zero custom engine code.  
> **Scope:** 24 tickets across 9 existing phases (0, 1, 2, 5, 6, 7, 8, 9, 11)

---

## 1. Why This Matters

SAM Trader V3 currently runs **one market at a time** — selected at startup via `FUTU_TRD_MARKET=US` or `FUTU_TRD_MARKET=HK`. To trade both HK and US in a 24-hour cycle, an operator must manually reconfigure and restart. The system lacks awareness of when to switch, whether it's safe to switch, and how to manage strategies across markets without downtime.

This plan makes the system **market-aware and self-orchestrating**: it knows what market is active, when to switch, and how to load/unload strategies dynamically — all using NautilusTrader's built-in patterns (`LiveClock` time alerts, `TraderController` for runtime strategy management, `Actor` for scheduled checks).

---

## 2. Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Nautilus-standard only** | No custom engine, no monkey-patching. All patterns from `nautilus_trader` v1.227.0 API. |
| **Broker containers always-on** | `sam-futu-opend` and `sam-ib-gateway` run 24/7. Only `sam-trader` restarts on market switch. |
| **Container restart for market switch** | `TradingNode` is single-lifecycle. Docker restart (~5-10s) is the documented Nautilus graceful restart pattern. |
| **Dynamic strategy loading** | `TraderController` (Nautilus-standard) adds/removes strategies at runtime. No node restart needed for strategy changes. |
| **Maintenance window 04:00–07:00 HKT** | All restarts, backups, and disruptive ops gated to this window. |
| **Config-driven, not hardcoded** | `market_config.yaml` defines per-market parameters. Zero `if HK then X else Y` scattered across code. |

---

## 3. Target Architecture

### 3.1 Container Landscape (Post-Change)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Docker Compose — ALL 6 containers always running                      │
│                                                                       │
│  sam-postgres     sam-redis     sam-futu-opend    sam-ib-gateway     │
│  (always)          (always)     (always, US+HK)   (always, US only)   │
│       │                │              │                  │             │
│       └────────────────┴──────┬───────┴──────────────────┘             │
│                               │                                       │
│                      ┌────────┴──────────┐                            │
│                      │   sam-trader      │                            │
│                      │   (per-market)    │                            │
│                      │                   │                            │
│                      │  TradingNode      │                            │
│                      │  ├─ Futu Data/Exec Clients (US+HK capable)     │
│                      │  ├─ IB Data/Exec Clients (US only)             │
│                      │  ├─ BundleController (dynamic load/unload)     │
│                      │  ├─ MarketSchedulerActor (switch + window)     │
│                      │  ├─ ReadinessCheckerActor (SOD check)          │
│                      │  └─ EndOfDayReporterActor (EOD report)         │
│                      └──────────────────┘                             │
│                                                                       │
│                      ┌──────────────────┐                             │
│                      │   sam-services   │                             │
│                      │                   │                            │
│                      │  ├─ Pre-market pipeline (per-market schedule)  │
│                      │  ├─ Dual-broker gap scanner (Futu + IB x-val)  │
│                      │  ├─ Restart orchestrator                       │
│                      │  ├─ CLI: sam readiness, sam report             │
│                      │  ├─ Dashboard (port 8080)                      │
│                      │  └─ Market-aware cron                          │
│                      └──────────────────┘                             │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 Per-Market Configuration

```yaml
# config/market_config.yaml
markets:
  HK:
    futu_trd_market: "HK"
    futu_routing_venues: ["HKEX"]
    ib_enabled: false
    session_timezone: "Asia/Hong_Kong"
    session_open: "09:30"
    session_close: "16:00"
    lunch_start: "12:00"
    lunch_end: "13:00"
    premarket_pipeline_time: "07:30"    # HKT — 2 hours before open
    sod_readiness_time: "07:00"         # HKT — 2.5 hours before open
    eod_report_time: "16:05"            # HKT — 5 min after close

  US:
    futu_trd_market: "US"
    futu_routing_venues: ["NASDAQ", "NYSE"]
    ib_enabled: true
    session_timezone: "America/New_York"
    session_open: "09:30"
    session_close: "16:00"
    lunch_start: ""                     # No lunch break for US
    lunch_end: ""
    premarket_pipeline_time: "08:30"    # ET — 1 hour before US open (~20:30/21:30 HKT)
    sod_readiness_time: "08:00"         # ET — 1.5 hours before US open
    eod_report_time: "16:05"            # ET — 5 min after US close (~04:05/05:05 HKT)
```

### 3.3 Bundle Schema — Four-Dimensional Key

```yaml
# config/bundles.yaml
bundles:
  # HK market — Futu broker — Tencent ORB
  - id: "tencent-orb-5m-futu"
    enabled: true
    venue: FUTU
    market: HK                    # NEW FIELD
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "00700.HKEX"
        bar_type: "00700.HKEX-5-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 5
        trade_size: 100
        lunch_pause_enabled: true    # NEW: configurable lunch pause
    bracket:
      stop_loss_ticks: 20
      take_profit_ticks: 60
    risk:
      max_position: 1000
      max_daily_loss: 5000

  # US market — Futu broker — TSLA ORB
  - id: "tsla-orb-15m-futu"
    enabled: true
    venue: FUTU
    market: US                    # NEW FIELD
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 15
        trade_size: 5
        lunch_pause_enabled: false
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30

  # US market — IB broker — NVDA Momentum
  - id: "nvda-momentum-5m-ib"
    enabled: true
    venue: IB
    market: US
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
        trade_size: 50
        lunch_pause_enabled: false
    bracket:
      stop_loss_ticks: 15
      take_profit_ticks: 45
```

**Each bundle = instrument × strategy × market × broker.** Backward compatible: bundles without `market` field default to `"US"`.

---

## 4. Daily Trading Cycle

```
HKT Time   Event                                              Actor/Service
────────   ─────────────────────────────────────────────────  ─────────────────────
04:00      US market closes                                   —
04:00      MarketScheduler triggers US→HK switch              MarketSchedulerActor
04:00      US EOD report generated                            EndOfDayReporterActor
04:00–07:00 *** MAINTENANCE WINDOW ***                        MarketSchedulerActor
04:05      sam-trader restarts with MARKET=HK                 Restart Orchestrator
04:15      Log rotation                                       Cron (sam-services)
04:30      Daily backup                                       Cron (sam-services)
07:00      HK SOD readiness check                             ReadinessCheckerActor
07:30      HK pre-market pipeline (gap scan → AI → bundles)   sam-services pipeline
07:35      BundleController loads HK bundles                  BundleController
09:30      HK MARKET OPENS — strategies active                —
12:00–13:00 HK lunch break (strategies pause if configured)   Strategy.on_pause()
13:00      HK resumes                                         Strategy.on_resume()
16:00      HK market closes                                   —
16:00      MarketScheduler triggers HK→US switch              MarketSchedulerActor
16:00      HK EOD report generated                            EndOfDayReporterActor
16:05      sam-trader restarts with MARKET=US                 Restart Orchestrator
16:10      US pre-market pipeline (gap scan → AI → bundles)   sam-services pipeline
20:00 ET   US SOD readiness check                             ReadinessCheckerActor
21:30 HKT  US MARKET OPENS — strategies active                —
...
04:00 HKT  (next day) cycle repeats                           —
```

**Weekends:** `MarketCalendarService.is_trading_day()` returns `False`. MarketSchedulerActor skips all alerts. Last-active market config stays loaded but strategies are paused by Controller.

---

## 5. Ticket Plan — 24 Tickets Across 9 Phases

### Phase 0 — Docker Always-On (3 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.1.21` | Remove Docker profiles — all containers always-on | `sam_trader-9z3.1` | None |
| `sam_trader-9z3.1.22` | Entrypoint: unconditional multi-broker wait logic | `sam_trader-9z3.1` | `9z3.1.21` |
| `sam_trader-9z3.1.23` | IB Gateway: US-market-only environment label | `sam_trader-9z3.1` | `9z3.1.21` |

### Phase 1 — Configuration & Bootstrap (3 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.2.2` | MarketConfig: frozen dataclass + market_config.yaml | `sam_trader-9z3.2` | None |
| `sam_trader-9z3.2.3` | MARKET env var → derived config fields (trd_market, ib, routing, timezone) | `sam_trader-9z3.2` | `9z3.2.2` |
| `sam_trader-9z3.2.4` | main.py: market-aware config propagation (remove hardcoded tz if/else) | `sam_trader-9z3.2` | `9z3.2.3` |

### Phase 2 — Futu Adapter Verification (1 ticket)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.3.8` | Futu: verify per-market connection context coexistence (US + HK) | `sam_trader-9z3.3` | `9z3.2.4` |

### Phase 5 — IBKR Adapter (1 ticket)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.6.9` | IB: conditional enable/disable via MarketConfig (US only) | `sam_trader-9z3.6` | `9z3.2.4` |

### Phase 6 — Actors & State Management (4 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.7.10` | MarketSchedulerActor: LiveClock alerts for market-switch + maintenance window | `sam_trader-9z3.7` | `9z3.2.4` |
| `sam_trader-9z3.7.11` | ReadinessCheckerActor: SOD operational readiness check | `sam_trader-9z3.7` | `9z3.2.4` |
| `sam_trader-9z3.7.12` | EndOfDayReporterActor: EOD aggregated P&L + fills + health report | `sam_trader-9z3.7` | `9z3.2.4` |
| `sam_trader-9z3.7.13` | Actor timezone refactor: existing actors use MarketConfig, remove hardcoded ternary | `sam_trader-9z3.7` | `9z3.2.4`, `9z3.7.10` |

### Phase 7 — Strategy Library & Bundle System (3 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.8.7` | Bundle schema: add `market` field with backward compat (default US) | `sam_trader-9z3.8` | `9z3.2.4` |
| `sam_trader-9z3.8.8` | BundleController: Nautilus Controller for dynamic strategy load/unload at runtime | `sam_trader-9z3.8` | `9z3.8.7`, `9z3.6.9` |
| `sam_trader-9z3.8.9` | Strategy: configurable HK lunch pause (on_pause/on_resume via LiveClock alerts) | `sam_trader-9z3.8` | `9z3.8.7` |

### Phase 8 — sam-services Container (4 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.9.12` | Restart orchestrator: listens for market_switch_request, docker compose restart | `sam_trader-9z3.9` | `9z3.7.10`, `9z3.1.21` |
| `sam_trader-9z3.9.13` | SOD readiness CLI: `sam readiness --market US|HK` from Redis | `sam_trader-9z3.9` | `9z3.7.11` |
| `sam_trader-9z3.9.14` | EOD report CLI: `sam report --market US|HK` from Redis + PG | `sam_trader-9z3.9` | `9z3.7.12` |
| `sam_trader-9z3.9.15` | Market-aware cron: per-market pipeline times, backup in maintenance window | `sam_trader-9z3.9` | `9z3.9.12`, `9z3.10.29` |

### Phase 9 — Pre-Market Pipeline (3 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.10.28` | Dual-broker gap scanner: Futu primary + IB cross-validation for US market | `sam_trader-9z3.10` | `9z3.6.9`, `9z3.2.4` |
| `sam_trader-9z3.10.29` | Market-aware pipeline scheduling: US at 08:30 ET, HK at 07:30 HKT | `sam_trader-9z3.10` | `9z3.2.4` |
| `sam_trader-9z3.10.30` | Pipeline → BundleController integration (Redis pub/sub for bundle load) | `sam_trader-9z3.10` | `9z3.10.29`, `9z3.8.8` |

### Phase 11 — Deploy Script & E2E (2 tickets)

| ID | Title | Parent | Deps |
|----|-------|--------|------|
| `sam_trader-9z3.12.5` | deploy.sh: always-on brokers (no profiles), MARKET env var, updated wizard | `sam_trader-9z3.12` | `9z3.1.21`, `9z3.2.3` |
| `sam_trader-9z3.12.6` | [EXIT] E2E: full daily cycle simulation (HK open→close→US open→close→HK) | `sam_trader-9z3.12` | All above |

---

## 6. Dependency Graph

```
P0-DM1 (9z3.1.21) ──► P0-DM2 (9z3.1.22)
  │
  ├──► P0-DM3 (9z3.1.23)
  │
  ▼
P1-DM1 (9z3.2.2) ──► P1-DM2 (9z3.2.3) ──► P1-DM3 (9z3.2.4)
                                                │
              ┌─────────────────────────────────┼──────────────────────────┐
              ▼                                 ▼                          ▼
        P2-DM1 (9z3.3.8)                  P5-DM1 (9z3.6.9)          P6-DM1 (9z3.7.10)
        (Futu verify)                     (IB conditional)          (MarketScheduler)
              │                                 │                          │
              │                                 ▼                          ├──► P6-DM2 (9z3.7.11)
              │                           P7-DM1 (9z3.8.7)                 │    (Readiness)
              │                           (market field)                   │
              │                                 │                          ├──► P6-DM3 (9z3.7.12)
              │                                 ▼                          │    (EOD Report)
              │                           P7-DM2 (9z3.8.8)                 │
              │                           (BundleController)               ├──► P6-DM4 (9z3.7.13)
              │                                 │                          │    (TZ refactor)
              │                                 ▼                          │
              │                           P7-DM3 (9z3.8.9)                 │
              │                           (lunch pause)                    │
              │                                 │                          │
              ├─────────────────────────────────┤                          │
              │                                 ▼                          │
              │                           P9-DM1 (9z3.10.28)               │
              │                           (dual scanner)                   │
              │                                 │                          │
              │                                 ▼                          │
              │                           P9-DM2 (9z3.10.29)               │
              │                           (market pipeline)                │
              │                                 │                          │
              │                                 ▼                          │
              │                           P9-DM3 (9z3.10.30) ──────────────┘
              │                      (pipeline→controller)
              │                                 │
              └─────────────────────────────────┤
                                                ▼
                                      ┌─────────────────────┐
                                      │ P8-DM1 (9z3.9.12)   │
                                      │ P8-DM2 (9z3.9.13)   │
                                      │ P8-DM3 (9z3.9.14)   │
                                      │ P8-DM4 (9z3.9.15)   │
                                      │ (all parallel)       │
                                      └──────────┬──────────┘
                                                 │
                                                 ▼
                                      ┌─────────────────────┐
                                      │ P11-DM1 (9z3.12.5)  │
                                      │    → P11-DM2        │
                                      │      (9z3.12.6)     │
                                      │ (deploy)  (e2e)     │
                                      └─────────────────────┘
```

---

## 7. Nautilus-Standard Patterns Used

| What | Nautilus Component | Custom? |
|------|-------------------|---------|
| Runtime strategy add/remove | `TraderController.create_strategy_from_config()` / `remove_strategy_from_id()` | No |
| Strategy pause/resume | `Controller.stop_strategy_from_id()` / `start_strategy_from_id()` | No |
| Market switch timing | `LiveClock.set_time_alert()` | No |
| Scheduled health checks | `Actor` subclass + `LiveClock` alerts | No |
| State save/load on restart | `Trader.save()`/`load()` + Redis `CacheConfig` | No (already built) |
| Graceful restart handshake | `RestartSubscriber` (Redis pub/sub) | Yes (follows Nautilus pattern) |
| Pre-market quote collection | `LiveMarketDataClient` temporary instances | No (already built) |
| Per-market config | `ImportableConfig` (YAML → frozen dataclass) | No |
| Bundle schema | `ImportableStrategyConfig` | No (already built) |
| Market calendar | `MarketCalendarService` (holidays library + Redis cache) | No (already built) |
| Container restart | Docker `docker compose restart` | No (already built into deploy) |

---

## 8. Key Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Container restart for market switch** | `TradingNode` is single-lifecycle (build→run→dispose). Docker restart is the documented Nautilus graceful restart pattern. RestartSubscriber already handles state save. |
| D2 | **Controller for dynamic strategy loading** | `TraderController` is the Nautilus-standard way to manage strategies at runtime. It handles all internal wiring (clock, portfolio, msgbus, cache, OMS). |
| D3 | **Broker containers always-on, sam-trader per-market** | Futu OpenD and IB Gateway survive node restarts. Only sam-trader is market-specific. Downtime is ~5-10s per switch. |
| D4 | **MarketConfig as YAML, not env vars** | Per-market parameters are structural, not key-value. YAML with frozen dataclass follows the existing `bundles.yaml` / `SamTraderConfig` pattern. |
| D5 | **Pipeline runs in sam-services, results pushed to sam-trader via Redis** | Decoupled. Pipeline can fail without affecting trading. BundleController subscribes to Redis, loads strategies on demand. |
| D6 | **Weekend = pause, not shutdown** | MarketSchedulerActor skips alerts on non-trading days. Last market config stays loaded, strategies paused. No unnecessary restarts. |
| D7 | **HK lunch pause = configurable per strategy** | Some strategies work through lunch, others don't. `lunch_pause_enabled: bool` in strategy config. Timezone-aware via LiveClock alerts. |
| D8 | **US pre-market at 08:30 ET (~20:30/21:30 HKT)** | 1 hour before US open. DST-aware via `zoneinfo`. Pipeline time specified in ET, converted to HKT dynamically. |
| D9 | **IB = US-only** | HK trading through IB is not in scope. IB Gateway container stays up but `main.py` skips IB client registration when `MARKET=HK`. |
| D10 | **Maintenance window 04:00–07:00 HKT** | Natural gap between US close and HK open. MarketSchedulerActor publishes window events. All disruptive ops gated. |
| D11 | **Paper account auto-discovery with config-driven sim_acc_type** | `market_config.yaml` defines expected `sim_acc_type` per market (STOCK for HK, STOCK_AND_OPTION for US). `FUTU_ACCOUNT_ID` is the OpenD login account only — never used as a paper trading fallback. `FUTU_PAPER_ACCOUNT_ID` provides explicit override when auto-discovery fails. See `docs/reference/ACCOUNT_DISCOVERY.md`. |

---

## 9. Files Changed / Created

### New Files

| File | Purpose |
|------|---------|
| `config/market_config.yaml` | Per-market configuration (US + HK entries, including `futu_paper_acc_type`) |
| `docs/reference/ACCOUNT_DISCOVERY.md` | Futu paper account auto-discovery reference |
| `config/gap_scanner.yaml` | Dual-broker scanner config |
| `src/sam_trader/market_config.py` | `MarketConfig` frozen dataclass with `from_yaml()` |
| `src/sam_trader/actors/market_scheduler.py` | `MarketSchedulerActor` — Nautilus Actor |
| `src/sam_trader/actors/readiness_checker.py` | `ReadinessCheckerActor` — Nautilus Actor |
| `src/sam_trader/actors/eod_reporter.py` | `EndOfDayReporterActor` — Nautilus Actor |
| `src/sam_trader/controllers/__init__.py` | Controller package |
| `src/sam_trader/controllers/bundle_controller.py` | `BundleController` — Nautilus Controller |
| `src/sam_trader/services/dual_broker_scanner.py` | Futu + IB cross-validation scanner |
| `src/sam_trader/services/restart_orchestrator.py` | Market-switch docker restart orchestration |

### Modified Files

| File | Change |
|------|--------|
| `docker/docker-compose.yml` | Remove profiles, always-on brokers |
| `docker/entrypoint.sh` | Unconditional multi-broker waits |
| `src/sam_trader/config.py` | `MARKET` env var, market_config integration |
| `src/sam_trader/main.py` | Market-aware config propagation, Controller wiring |
| `src/sam_trader/bundle_loader.py` | `market` field validation |
| `src/sam_trader/bundle_validation.py` | `market` field in schema |
| `src/sam_trader/strategies/orb.py` | `lunch_pause_enabled`, `lunch_start`, `lunch_end` |
| `src/sam_trader/strategies/momentum.py` | Same lunch pause fields |
| `src/sam_trader/services/pipeline.py` | Market-aware scheduling, dual-broker scanner |
| `src/sam_trader/services/gap_scanner.py` | Config-driven broker selection |
| `src/sam_trader/services/cli.py` | `sam readiness`, `sam report`, `sam switch-market` |
| `config/bundles.example.yaml` | Add HK examples with `market` field |
| `.env.example` | Add `MARKET` env var, remove `FUTU_TRD_MARKET` as primary |
| `deploy.sh` | Remove profiles, add MARKET wizard prompt |

---

## 10. Success Criteria

| # | Criterion | Verified By |
|---|-----------|------------|
| S1 | `docker compose up -d` starts all 6 containers. No flags needed. | Manual test |
| S2 | `MARKET=HK` → Futu connected to HK, IB not registered, HK bundles loaded | E2E test |
| S3 | `MARKET=US` → Futu connected to US, IB registered, US bundles loaded | E2E test |
| S4 | MarketSchedulerActor triggers switch at HK close (16:00 HKT) and US close (04:00 HKT) | Unit test with TestClock |
| S5 | BundleController loads strategies at runtime without node restart | Integration test |
| S6 | ReadinessCheckerActor reports pass/fail for all checks before market open | Integration test |
| S7 | EndOfDayReporterActor generates complete EOD report after market close | Integration test |
| S8 | Dual-broker gap scanner cross-validates Futu vs IB quotes for US market | Integration test |
| S9 | HK lunch pause activates/deactivates strategies at 12:00/13:00 HKT | Unit test |
| S10 | Full 24-hour cycle: HK→US switch → US→HK switch, state preserved | E2E test |
| S11 | Maintenance window operations (backup, restart) only between 04:00-07:00 HKT | Integration test |

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Switch during active positions | Positions left open in wrong market | Pre-switch gate: zero net positions required. If open, log CRITICAL and skip switch. Operator alerted. |
| Redis unavailable during state save | State lost on restart | `RestartSubscriber` logs warning, skips save. Node doesn't restart until save confirmed. |
| Futu OpenD market mismatch | Wrong market data flows | `FutuLiveDataClient` verifies subscription data matches expected market on connect. |
| DST transition (US clock changes) | Pipeline runs at wrong time | `zoneinfo` handles DST automatically. Pipeline times stored in ET; converted to HKT at runtime. |
| IB Gateway 2FA during restart | IB disconnected | IB Gateway has `EXISTING_SESSION_DETECTED_ACTION=primary` to survive restarts. Already configured. |
| Pipeline fails to generate bundles | No strategies for market open | ReadinessCheckerActor detects empty strategy list, logs CRITICAL, alerts operator. Fallback: load static `bundles.yaml` entries. |
| Controller fails to load strategy | Individual strategy missing | Controller logs ERROR per strategy, continues loading others. EOD report flags missing strategies. |

---

*End of plan. For implementation tickets, see beads under phase-0 through phase-11 labels.*
