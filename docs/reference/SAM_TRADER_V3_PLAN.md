# SAM Trader V3 - Architecture & Build Plan

> **Status:** Active (Phases 0–11 complete; Dynamic Multi-Market extensions planned)
> **Last Updated:** 2026-05-27
> **Purpose:** Single source of truth for SAM Trader V3. Written for AI agents and humans.
> **Repo:** `github.com/samdharma/sam_trader`
> **Predecessor:** CSAM Trader V2 (`~/Trading/csam_trader/`)
> **Dynamic Multi-Market Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

---

## 1. Goals

1. **Ground-up rebuild** of the trading platform as **sam_trader v3**, selectively porting proven components from csam_trader v2.
2. **FUTUBULL as primary broker** - free US real-time market data via Futu OpenD API. IBKR as secondary (re-integrated after Futu is validated).
3. **Production-grade autonomous trading** on NautilusTrader (v1.227+). Standard Nautilus components and recommended patterns. No custom implementations unless no standard alternative exists.
4. **Multi-venue, multi-strategy** - Futu first, IBKR second. Additional brokers added as needed.
5. **Pluggable strategies via bundle config** - YAML-specified bundles combining strategy type, instrument, venue, parameters, bracket orders, risk/reward criteria. Bundles are the unit of deployment.
6. **Decoupled operations** - `sam-trader` container runs TradingNode 24/7. `sam-services` container runs pre-market pipeline, dashboard, cron, CLI independently. Restart services without touching trading.
7. **Graceful restart for hot-loading** - strategies/bundles/configs loaded at node build time. Graceful restart (state save → stop → build → run → state restore) for config changes. Maintenance window: 5am-8am HKT daily.
8. **Single-script deployable** - portable bash script. First-run wizard, generates config, pulls from GitHub, runs `docker compose up`.
9. **Self-contained deployment** - sensible defaults for Futu, IBKR, and all services. No Linux host plan required at this stage.

---

## 2. Key Architecture Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **NautilusTrader as sole engine** | Rust-native, event-driven, backtest-to-live parity. Standard components for data, execution, risk, strategies. Version pinned (not `:latest`). |
| D2 | **futu-api SDK for Futu protocol** (not nautilus-futu) | Officially maintained by Futu. Eliminates Rust build pipeline. Single `pip install futu-api`. We adopt nautilus-futu's architecture *patterns* but use futu-api as the protocol layer. See §12 Decision Record. |
| D3 | **Futu OpenD in separate Docker container** | Community image (`ghcr.io/manhinhang/futu-opend-docker:ubuntu-stable`). Independent lifecycle, survives Nautilus restarts, auth persistence via volume. |
| D4 | **IB Gateway in separate Docker container** | Existing gnzsnz/ib-gateway image. Profile-based (optional). Survives Nautilus restarts, 2FA via VNC. |
| D5 | **sam-services in separate Docker container** | Operations (pipeline, dashboard, cron, CLI) decoupled from trading. Restart independently. Zero trading downtime for ops changes. |
| D6 | **Parquet for historical data** | Required by `BacktestNode`. DuckDB/Polars for ad-hoc SQL queries. Zero extra infrastructure. |
| D7 | **PostgreSQL for relational data** | Trade journal (fills, orders), portfolio snapshots, dashboard queries. Lightweight Alpine image. |
| D8 | **Redis for Nautilus cache state** | Required for `load_state`/`save_state` (actor/strategy state persistence). Minimal footprint (~30MB Alpine). |
| D9 | **YAML bundle registry** | Single config file defines all active strategies. Multi-venue from day 1 (`venue: FUTU` and `venue: IB`). |
| D10 | **ImportableStrategyConfig pattern** | Nautilus-recommended. Strategy classes referenced by dotted path. No hardcoded strategies in bootstrap. |
| D11 | **Graceful restart for config changes** | `save_state()` → `stop()` → `build(new_config)` → `run()` → state restored. ~5-10s downtime. |
| D12 | **Maintenance window 5am-8am HKT** | All restarts, updates, strategy changes happen only in this window. System is read-only otherwise. |
| D13 | **Futu subscription quota manager** | Futu-specific constraint. Tracks active subscriptions per data type, releases unused subs, warns at limits. Not needed for IBKR. |

---

## 3. What We Port from CSAM Trader V2

| Asset | Source (v2) | Destination (v3) | Changes |
|-------|------------|-------------------|---------|
| `connection.py` | `adapters/futu/connection.py` | `adapters/futu/connection.py` | Minimal - update module references to `sam_trader` |
| `config.py` | `config.py` | `config.py` | Major - add Futu fields, multi-broker support, `SamTraderConfig` |
| `bundle_loader.py` | `bundle_loader.py` | `bundle_loader.py` | Moderate - multi-venue support (`FUTU` + `IB`) |
| `main.py` | `main.py` | `main.py` | Major - multi-broker factory registration, Futu-first wiring |
| OrbStrategy | `strategies/orb.py` | `strategies/orb.py` | Minor - venue-aware config, Futu-compatible params |
| MomentumStrategy | `strategies/momentum.py` | `strategies/momentum.py` | Minor - same |
| Strategy template | `strategies/_template.py` | `strategies/_template.py` | Minor |
| TradeJournalActor | `actors/trade_journal.py` | `actors/trade_journal.py` | Minor - add venue column to fills |
| HealthMonitorActor | `actors/health_monitor.py` | `actors/health_monitor.py` | None |
| BarResubscriptionActor | `actors/bar_resubscription.py` | `actors/bar_resubscription.py` | Minor |
| `docker-compose.yml` | Pattern, profiles, networks | Rewritten for v3 naming | All service/network/volume names changed |
| `entrypoint.sh` | Service-wait logic | Enhanced | Add conditional Futu + IB broker waits |
| `deploy.sh` | Wizard, git pull, orchestration | Significantly restructured | Decouple into deploy + services; add `--with-futu` |
| `Dockerfile` | FROM nautilus, user setup | Same pattern, pinned tag | Pin to specific Nautilus version |
| PG schema | `docker/postgres/init/` | Same | Add `venue` column to fills |
| `bundles.yaml` | Schema, examples | Enhanced | Add Futu bundles, multi-venue examples |
| `.env.example` | Template | Enhanced | Add Futu vars, rename prefixes |
| Ralph Loop | `scripts/ralph/` | `scripts/ralph/` | As-is |
| Tests | Unit + integration patterns | Port and extend | Adapt to new paths, add Futu-specific tests |
| `quote.py` | Quote fetcher | Enhanced | Add Futu quote cache support |

---

## 4. Target Architecture

### 4.1 Container Landscape

```
┌──────────────────────────────────────────────────────────────────────┐
│                     Docker Compose Stack (v3)                         │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐       │
│  │ sam-postgres     │  │ sam-redis    │  │ sam-futu-opend   │       │
│  │ postgres:16-alpine│  │ redis:7-alpine│  │ futuopen/futu-   │       │
│  │ port 5432        │  │ port 6379    │  │   opend:latest   │       │
│  │                  │  │              │  │ port 11111       │       │
│  │ Trade journal    │  │ Cache state  │  │ (profile: futu)  │       │
│  │ Portfolio state  │  │ Actor/strat  │  │ Auth vol mounted │       │
│  │ Dashboard data   │  │ state        │  │                  │       │
│  └────────┬─────────┘  └──────┬───────┘  └────────┬─────────┘       │
│           │                   │                    │                  │
│           │ sam-net           │ sam-net            │ sam-net          │
│           │                   │                    │                  │
│  ┌────────┴───────────────────┴────────────────────┴──────────┐      │
│  │ sam-trader (TradingNode container)                           │      │
│  │ FROM ghcr.io/nautechsystems/nautilus_trader:<pinned>        │      │
│  │                                                               │      │
│  │  ┌─────────────────────────────────────────────────────────┐ │      │
│  │  │ TradingNode                                              │ │      │
│  │  │  ├─ Futu DataClient    → sam-futu-opend:11111           │ │      │
│  │  │  ├─ Futu ExecClient    → sam-futu-opend:11111           │ │      │
│  │  │  ├─ IB DataClient      → sam-ib-gateway:4004 (profile)  │ │      │
│  │  │  ├─ IB ExecClient      → sam-ib-gateway:4004 (profile)  │ │      │
│  │  │  ├─ Strategies (N instances from bundles.yaml)          │ │      │
│  │  │  └─ Actors (Journal, Health, BarResub)                  │ │      │
│  │  └─────────────────────────────────────────────────────────┘ │      │
│  │                                                               │      │
│  │  Mounted volumes:                                             │      │
│  │   ./config/    → /opt/sam_trader/config/    (ro)             │      │
│  │   ./data/catalog/ → /opt/sam_trader/data/catalog/ (rw)       │      │
│  │   ./logs/      → /opt/sam_trader/logs/      (rw)             │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                         │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐        │
│  │ sam-ib-gateway   │  │ sam-services (operations container)   │        │
│  │ ib-gateway:stable│  │ python:3.12-slim                       │        │
│  │ port 4004        │  │ port 8080                              │        │
│  │ VNC :5900        │  │                                        │        │
│  │ (profile: ib)    │  │  ├─ sam CLI (status, health, backup)  │        │
│  │                  │  │  ├─ Cron scheduler (pipeline, backup) │        │
│  └──────────────────┘  │  ├─ Gap scanner                       │        │
│                        │  ├─ AI analysis pipeline              │        │
│                        │  ├─ Risk manager / position sizer     │        │
│                        │  ├─ Bundle YAML generator             │        │
│                        │  ├─ Pipeline orchestrator             │        │
│                        │  └─ Dashboard (FastAPI → static HTML)  │        │
│                        │                                        │        │
│                        │  Mounted: config/, logs/, backups/,   │        │
│                        │           /var/run/docker.sock (ro)   │        │
│                        │  (profile: services)                   │        │
│                        └────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 Data Architecture

| Store | Technology | Contents | Consumer |
|-------|-----------|----------|----------|
| Parquet Catalog | Parquet files on host volume | Bars, trade ticks, quote ticks, instruments, order book snapshots | `BacktestNode`, DuckDB, pandas/polars, Jupyter, ML pipelines |
| PostgreSQL | PostgreSQL 16 Alpine | Trade fills, orders, positions, portfolio snapshots, config audit log | `TradeJournalActor`, dashboards, compliance queries |
| Redis | Redis 7 Alpine | Actor/strategy state cache, message bus backing | `TradingNode` cache database |

### 4.3 Strategy & Bundle Architecture

```
┌──────────────────────┐
│ config/bundles.yaml   │  Single source of truth
│ (versioned in git)    │  Multi-venue from day 1
└──────────┬───────────┘
           │ read at build time by BundleLoader
           ▼
┌──────────────────────┐
│ BundleLoader          │  Validates, converts to
│ (bundle_loader.py)    │  list[ImportableStrategyConfig]
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ TradingNodeConfig     │  strategies = [...]
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│ Running TradingNode                                       │
│                                                            │
│  OrbStrategy("TSLA", venue=FUTU, first_candle=15m)        │
│  OrbStrategy("BABA", venue=FUTU, first_candle=5m)         │
│  MomentumStrategy("QQQ", venue=FUTU, window=60m)          │
│  OrbStrategy("NVDA", venue=IB, first_candle=5m)           │
│  ...                                                       │
│                                                            │
│  Each strategy:                                            │
│   - Has its own instrument subscriptions                  │
│   - Is isolated from other strategies                      │
│   - Reads bracket/risk params from its StrategyConfig     │
│   - Uses order_factory.bracket() for bracket orders       │
│   - Routes orders to correct venue (Futu or IB)           │
└──────────────────────────────────────────────────────────┘
```

### 4.4 Bundle Schema (Multi-Venue)

```yaml
bundles:
  # Futu bundle (US market)
  - id: "tsla-orb-15m-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 15
        trade_size: 5
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
    risk:
      max_position: 500
      max_daily_loss: 1000

  # Futu bundle (HK market)
  - id: "tencent-orb-5m-futu"
    enabled: false
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "00700.HKEX"
        bar_type: "00700.HKEX-5-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 5
        trade_size: 100
    bracket:
      stop_loss_ticks: 20
      take_profit_ticks: 60
    risk:
      max_position: 1000
      max_daily_loss: 5000

  # IBKR bundle (legacy, disabled by default)
  - id: "nvda-momentum-5m-ib"
    enabled: false
    venue: IB
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
        window: 20
        session_start: "09:30:00"
        session_end: "16:00:00"
        trade_size: 50
    bracket:
      stop_loss_ticks: 15
      take_profit_ticks: 45
    risk:
      max_position: 200
      max_daily_loss: 500
```

---

## 5. Project Directory Structure (Target)

```
sam_trader/
├── deploy.sh                      # Single-script deploy (portable bash)
├── .env                           # Secrets (never committed, in .gitignore)
├── .env.example                   # Template with dummy values
├── .gitignore
├── AGENTS.md                      # AI agent instructions
├── README.md                      # Human-readable project overview
├── pyproject.toml                 # Python package config
│
├── docker/
│   ├── docker-compose.yml         # Service definitions (all containers)
│   ├── Dockerfile                 # sam-trader (Nautilus TradingNode)
│   ├── Dockerfile.services        # sam-services (operations)
│   ├── entrypoint.sh              # Service wait logic
│   └── requirements.txt          # Extra Python deps for Nautilus
│
├── config/
│   ├── bundles.yaml               # Strategy bundle registry
│   ├── bundles.example.yaml       # Example with all supported fields
│   └── orb_presets.yaml           # Per-instrument ORB presets
│
├── src/
│   └── sam_trader/
│       ├── __init__.py
│       ├── main.py                # Bootstrap: TradingNode + BundleLoader + actors
│       ├── config.py              # SamTraderConfig (env-var driven, multi-broker)
│       ├── bundle_loader.py       # YAML → ImportableStrategyConfig (multi-venue)
│       ├── quote.py               # Quote fetcher (Futu + IB)
│       ├── adapters/
│       │   ├── __init__.py
│       │   └── futu/
│       │       ├── __init__.py
│       │       ├── config.py       # FutuDataClientConfig, FutuExecClientConfig
│       │       ├── constants.py    # Futu enum values + Nautilus mappings
│       │       ├── common.py       # instrument_id ↔ futu_security helpers
│       │       ├── connection.py   # Shared FutuClient (quote + trade contexts)
│       │       ├── data.py         # FutuLiveDataClient
│       │       ├── execution.py    # FutuLiveExecutionClient
│       │       ├── factories.py    # FutuLiveDataClientFactory, FutuLiveExecClientFactory
│       │       ├── instrument_provider.py  # FutuInstrumentProvider
│       │       ├── subscription_manager.py # Subscription quota tracking/release
│       │       └── parsing/
│       │           ├── __init__.py
│       │           ├── market_data.py  # QuoteTick, TradeTick, Bar, OrderBookDeltas
│       │           ├── orders.py       # OrderStatusReport, FillReport, PositionStatusReport
│       │           └── instruments.py  # Equity, OptionContract, FuturesContract
│       ├── strategies/
│       │   ├── __init__.py
│       │   ├── orb.py              # Opening Range Breakout
│       │   ├── momentum.py         # Momentum at open
│       │   └── _template.py        # Copy-paste template for new strategies
│       └── actors/
│           ├── __init__.py
│           ├── trade_journal.py    # PostgreSQL fill/order journaling
│           ├── health_monitor.py   # Heartbeat + metrics
│           └── bar_resubscription.py # Bar subscription recovery
│
├── data/
│   └── catalog/                    # Parquet data catalog (mounted volume)
│
├── logs/                           # Runtime logs (mounted volume)
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_bundle_loader.py
│   │   ├── adapters/
│   │   │   └── futu/
│   │   │       ├── test_connection.py
│   │   │       ├── test_constants.py
│   │   │       ├── test_parsing.py
│   │   │       ├── test_subscription_manager.py
│   │   │       └── test_data.py
│   │   ├── strategies/
│   │   │   ├── test_orb.py
│   │   │   └── test_momentum.py
│   │   └── actors/
│   │       ├── test_trade_journal.py
│   │       └── test_health_monitor.py
│   └── integration/
│       ├── conftest.py
│       ├── adapters/
│       │   └── futu/
│       │       ├── test_connection_lifecycle.py
│       │       ├── test_data_subscription.py
│       │       ├── test_execution_flow.py
│       │       └── test_instrument_provider.py
│       ├── test_dual_venue.py
│       └── test_e2e_futu.py
│
├── docs/
│   ├── agent/                      # Agent-facing docs
│   │   ├── TICKET_PLAN_V3.md       # Ticket hierarchy + dependency tree
│   │   └── SESSION_NOTES.md        # Session-by-session progress log
│   ├── reference/                  # Reference documents
│   │   ├── SAM_TRADER_V3_PLAN.md   # THIS FILE
│   │   └── futubull_opend_assessment.html  # Community adapter analysis
│   └── user/                       # User-facing docs
│       ├── DEPLOY_GUIDE.md
│       ├── BUNDLE_GUIDE.md
│       └── OPERATOR_GUIDE.md
│
└── scripts/
    └── ralph/                      # Ralph Wiggum loop (ported from v2)
        ├── ralph_loop.sh
        ├── ralph_validate.sh
        ├── ralph_health.sh
        └── ...
```

---

## 6. Roadmap - Build Phases

### Phase 0: Foundation - Skeleton & Docker Stack
> **Goal:** Empty repo with docker-compose defining all services. No trading logic yet.
> **Status:** ✅ Complete (all 20 tickets closed incl 2 EXIT gates)
> **Depends on:** Nothing

### Phase 1: Configuration & Bootstrap
> **Goal:** `SamTraderConfig` loads from env vars. `main.py` bootstraps TradingNode with multi-broker placeholders.
> **Status:** ✅ Complete (config + bootstrap + integration test)
> **Depends on:** Phase 0

### Phase 2: Futu Adapter - Market Data
> **Goal:** `FutuLiveDataClient` streams QuoteTick, TradeTick, Bar, OrderBookDelta to Nautilus message bus.
> **Status:** ✅ Complete (all 7 tickets closed incl EXIT 9z3.3.7)
> **Depends on:** Phase 1

### Phase 3: Futu Adapter - Execution
> **Goal:** `FutuLiveExecutionClient` submits/modifies/cancels orders. OrderFilled events flow to message bus.
> **Status:** ✅ Complete (all 10 tickets closed incl EXIT 9z3.4.3)
> **Depends on:** Phase 2

### Phase 4: Futu Adapter - Instrument Provider & Integration
> **Goal:** `FutuInstrumentProvider` resolves symbols. Factories wired into TradingNode. Futu bundles loadable.
> **Status:** ✅ Complete (all 6 tickets closed incl EXIT 9z3.5.6)
> **Depends on:** Phase 3

### Phase 5: IBKR Adapter (Re-integration)
> **Goal:** Port IBKR adapter from v2. Enhanced for multi-venue coexistence. Both Futu + IB work simultaneously.
> **Status:** ✅ Complete (all 14 tickets closed incl EXIT 9z3.6.4)
> **Depends on:** Phase 4

### Phase 6: Actors & State Management
> **Goal:** TradeJournalActor, HealthMonitorActor, BarResubscriptionActor, RejectionMonitorActor, RealizedPnLTrackerActor. PostgreSQL schema with venue column. Redis state persistence.
> **Status:** ✅ Complete (EXIT validated 2026-05-24). All 6 actors wired into `main.py` via `ImportableActorConfig` pattern. Integration test `tests/integration/test_phase6_exit.py` validates all 6 AC.
> **Depends on:** Phase 5 (needs fills from both venues to flow)

### Phase 7: Strategy Library & Bundle System
> **Goal:** OrbStrategy, MomentumStrategy, strategy template. Multi-venue bundle loader. Bundle validation.
> **Status:** ✅ Complete (all 6 tickets closed incl EXIT 9z3.8.6)
> **Depends on:** Phase 6

### Phase 8: sam-services Container
> **Goal:** Operations container with CLI, cron, health checks, backup, quote fetcher, **performance analysis** (Nautilus-native PortfolioAnalyzer), and **production safeguards** (LiveRiskEngine, PositionSnapshot, Slippage). Decoupled from sam-trader.  
> **Status:** ✅ Complete (EXIT validated 2026-05-24). 11 tickets all closed. 110 unit + 6 integration tests passing. Phase 6 actor wiring gap discovered and fixed during validation.  
> **Revised 2026-05-23:** Expanded from 6 to 11 tickets with 5 Nautilus-native integrations per gap analysis.  
> **Depends on:** Phase 7 (needs journal actors, bundle system, strategies for full integration test)

### Phase 9: Pre-Market Pipeline
> **Goal:** Gap scanner → AI analysis → risk manager → bundle generator → readiness report.
> **Status:** ✅ Complete (all 12 tickets closed incl EXIT 9z3.10.27)
> **Depends on:** Phase 8

### Phase 10: Safety & Dashboard
> **Goal:** Kill switch, circuit breakers, basic read-only dashboard showing existing Phase 6/8 data.
> **Status:** ✅ Complete (all 3 tickets closed incl EXIT 9z3.11.8)  
> **Depends on:** Phase 9

### Phase 11: Deploy Script & E2E Validation
> **Goal:** Single-script deploy. First-run wizard. All profiles work. Full E2E gate passes.
> **Status:** ✅ Complete (all 4 tickets closed incl EXIT 9z3.12.4)  
> **Depends on:** Phase 10

---

### Dynamic Multi-Market Extensions (in progress)
> **Goal:** Always-on brokers (Futu + IB), MARKET env var, dynamic market switching via Nautilus Controller, dual-broker gap scanner, SOD readiness checks, EOD reports, HK lunch pause, per-market config.
> **Status:** Planned — 24 tickets across phases 0, 1, 2, 5, 6, 7, 8, 9, 11
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`
> **Depends on:** Phases 0–11 complete

---

## 7. Coding Conventions

1. **All strategies** are standard Nautilus `Strategy` subclasses with frozen `StrategyConfig` dataclasses.
2. **All adapters** follow the Nautilus adapter pattern: `LiveMarketDataClient` / `LiveExecutionClient` subclasses + factory classes.
3. **Configuration** is env-var-driven with frozen dataclass defaults. No hardcoded values.
4. **Bundles** are the ONLY way strategies are loaded. No strategy imports in `main.py`.
5. **State** is persisted via Nautilus built-in state management. Redis for actor/strategy cache state. PostgreSQL for relational state. Parquet for data.
6. **Docker** follows one-process-per-container. Nautilus runs as PID 1 (via entrypoint).
7. **Secrets** in `.env` file only. Never committed. `.env.example` provides template.
8. **Beads** (`bd`) for all issue tracking. Ralph Wiggum loop for agent-driven development.
9. **Python 3.12.x**. Type hints on all public APIs. Ruff for linting, Black for formatting.
10. **Package name**: `sam_trader`. Docker image prefix: `sam-`. Network: `sam-net`.

---

## 8. Naming Convention (v2 → v3 Mapping)

| v2 Name | v3 Name |
|---------|---------|
| `csam_trader` (package) | `sam_trader` |
| `CsamTraderConfig` | `SamTraderConfig` |
| `csam-nautilus` (container) | `sam-trader` |
| `csam-postgres` | `sam-postgres` |
| `csam-redis` | `sam-redis` |
| `csam-ib-gateway` | `sam-ib-gateway` |
| `csam-futu-opend` | `sam-futu-opend` |
| `csam-net` (network) | `sam-net` |
| `csam-services` (future) | `sam-services` |
| `config/bundles.yaml` | `config/bundles.yaml` (same path) |
| `TRADER_ID=csam_trader` | `TRADER_ID=sam_trader` |

---

## 9. Rollback & Safety

1. **Bundle rollback:** Git version `bundles.yaml`. Revert commit + restart → previous state restored.
2. **Nautilus rollback:** Pin Nautilus image tag in docker-compose. Do not use `:latest` in production.
3. **Database rollback:** PostgreSQL volume is persistent. Schema migrations are additive only.
4. **Futu OpenD rollback:** Pin OpenD image tag. Auth session persists in volume.
5. **State rollback:** Redis persists strategy state. Graceful restart preserves state.

---

## 10. Success Criteria

| # | Criterion | How Verified |
|---|-----------|-------------|
| S1 | `deploy.sh` brings up full stack on clean macOS | Manual test |
| S2 | sam-trader connects to sam-futu-opend, streams US market data | Logs + subscription verification |
| S3 | Adding a Futu bundle to `bundles.yaml` + restart → strategy trades | Watch fills in PostgreSQL |
| S4 | Both Futu + IBKR strategies coexist in same TradingNode | Dual-venue integration test |
| S5 | Restart cycle < 30 seconds | Timed test |
| S6 | System runs 24h without intervention | Overnight soak test |
| S7 | sam-services restart does not affect sam-trader | Process isolation test |

---

## 11. Open Questions

1. **Nautilus exact version pin?** `ghcr.io/nautechsystems/nautilus_trader:1.227.0` or `nightly`? Need to verify tag availability.
2. **Python 3.12.x in Nautilus base image?** Need to confirm the pinned Nautilus image ships with Python 3.12.x.
3. **AI integration channel:** How does AI write to `bundles.yaml`? Via GitHub API (PR), direct filesystem, or API endpoint?
4. **Alerting channel:** What channel for disconnection, margin warnings, strategy errors? Email, Slack, Telegram?
5. **Multi-account:** Will there be multiple Futu accounts? If so, how are they separated at config/bundle level?

---

## 12. Decision Record: Why futu-api SDK over nautilus-futu

**Context:** The community project `nautilus-futu` by loadstarCN provides a complete Rust+PyO3 adapter for Nautilus ↔ Futu OpenD. We evaluated it against building our own adapter using the official `futu-api` Python SDK.

**Options considered:**
1. Use `nautilus-futu` as-is
2. Use `futu-api` SDK + build our own adapter (adopting nautilus-futu patterns)
3. Build our own TCP/protobuf client

**Decision:** Option 2 - use `futu-api` SDK + build our own adapter.

**Rationale:**
- `futu-api` is officially maintained by Futu. Protocol updates are handled upstream.
- No Rust build pipeline required. Single `pip install futu-api==10.5.6508`.
- `nautilus-futu` has excellent architecture patterns (shared client, push channel isolation, reconnection with subscription restoration, multi-market venue aliases, account auto-discovery) - we adopt these patterns but implement against `futu-api`.
- Nautilus core uses Rust/msgspec internally; we avoid Rust version conflicts by keeping our adapter in pure Python.
- Full control over the codebase. We can tune for our subscription quota constraints, venue symbology, and config patterns.

**What we specifically adopt from nautilus-futu:**
- Push data architecture: callback → asyncio.Queue → _run_push_loop
- Shared client pattern: one OpenQuoteContext + OpenSecTradeContext per (host, port, env)
- Reconnection with subscription restoration
- Multi-market venue aliases (HKEX, NASDAQ, NYSE)
- Account auto-discovery via `get_acc_list`
- Instrument auto-loading for unknown instruments from positions
- Parsing patterns for market data, orders, and instruments

---

*End of plan. Next: create ticket hierarchy and dependency tree in TICKET_PLAN_V3.md.*
