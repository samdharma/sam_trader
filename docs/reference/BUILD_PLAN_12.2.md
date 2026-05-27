# BUILD_PLAN 12.2 — Strategy Inventory & Version Management

> **Status:** Planning  
> **Goal:** Managed inventory of strategies with full version lifecycle, performance tracking, comparison, and promotion rules. Shift from `bundles.yaml` flat config to a version registry backed by PostgreSQL + CLI + dashboard.  
> **Gates on:** Phase 12.1 EXIT — `sam_trader-9z3.13.1.10` (backtest results feed promotion decisions)  
> **Next:** [BUILD_PLAN_12.3.md](./BUILD_PLAN_12.3.md) (independent, runs in parallel)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│               12.2 — Strategy Inventory & Versions            │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ strategy_registry (PostgreSQL) — canonical catalog       │ │
│  │   family | class | instrument | venue | version | variant│ │
│  │   status | config_snapshot (JSONB) | parent | changelog │ │
│  │   UNIQUE(family, class, instrument, venue, version)      │ │
│  └─────────────────────────────────────────────────────────┘ │
│         │                                                     │
│         ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Registry CRUD Service (registry.py)                      │ │
│  │   register / query_by_family / query_by_status /         │ │
│  │   get_latest / compare_versions / get_performance        │ │
│  └─────────────────────────────────────────────────────────┘ │
│         │                                                     │
│    ┌────┴────────────┬──────────────────┐                    │
│    ▼                 ▼                   ▼                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
│  │ Lifecycle    │ │ sam strategy │ │ sam strategy sync    │ │
│  │ Engine       │ │ CLI group    │ │ registry → bundles.  │ │
│  │              │ │              │ │   yaml + git commit  │ │
│  │ promote()    │ │ list         │ │                      │ │
│  │ retire()     │ │ register     │ │ WHERE status IN      │ │
│  │ evaluate()   │ │ promote      │ │   ('paper','active') │ │
│  │              │ │ retire       │ │                      │ │
│  │ rules from   │ │ compare      │ │ active → enabled:true│ │
│  │ registry_    │ │ diff         │ │ paper  → enabled:false│ │
│  │ rules.yaml   │ │ perf         │ │                      │ │
│  └──────────────┘ └──────────────┘ └──────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Backtest → Promote Pipeline (bridges 12.1 ↔ 12.2)       │ │
│  │   sam strategy pipeline <id> --backtest-start X \       │ │
│  │       --sweep sl=5,10 --auto-promote                    │ │
│  │                                                          │ │
│  │   1. Run backtest (12.1) → 2. Evaluate vs rules →       │ │
│  │   3. Store results → 4. Promote if pass (auto)          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Dashboard (sam-services:8080)                            │ │
│  │   GET/POST /api/strategy/registry/*                     │ │
│  │   GET /api/strategy/compare?ids=1,2,3                   │ │
│  │   GET /api/strategy/<id>/performance?days=30            │ │
│  │   GET /api/strategy/leaderboard?metric=sharpe           │ │
│  │   POST /api/strategy/pipeline                           │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. What Already Exists — Foundation to Build On

| Capability | Location | Status |
|-----------|----------|--------|
| Version metadata in bundles | `bundles.yaml` — `family`, `version`, `variant` fields | Schema exists; validation enforces semver |
| Metadata pass-through | `bundle_loader.py` — passes `family`/`version`/`variant` to `ImportableStrategyConfig` | Wired |
| Per-strategy performance | `performance_stats` PG table — `(date, strategy_id, stat_name, stat_value)` | Populated nightly by Phase 8 |
| Bundle enable/disable | `bundles.yaml` `enabled: true/false` | Binary toggle only |
| Bundle validation | `bundle_validation.py` — schema + strategy class + backtest smoke test | Works |
| Backtest results | `backtest_results` PG table (built in Phase 12.1) | Available for pipeline |

### 2.1 Existing `bundles.yaml` Pattern (source of truth for live)

```yaml
# config/bundles.yaml — current flat format
bundles:
  - id: "orb-aggressive-tsla"
    enabled: true
    venue: FUTU
    family: ORB              # ← Used for grouping
    version: "1.3.0"         # ← Semver
    variant: aggressive      # ← Variant discriminator
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        trade_size: 10
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
```

### 2.2 `bundle_loader.py` — ImportableStrategyConfig Pattern

```python
# sam_trader/bundle_loader.py passes metadata through:
from nautilus_trader.trading.config import ImportableStrategyConfig

config = ImportableStrategyConfig(
    strategy_path=bundle["strategy"]["path"],
    config_path="",               # Not used; config passed inline
    config=bundle["strategy"]["config"],
)

# family, version, variant are attached as custom attrs or passed separately
# Phase 12.2 formalizes these into the registry
```

---

## 3. PostgreSQL Schema — `strategy_registry`

```sql
CREATE TABLE strategy_registry (
    id                  SERIAL PRIMARY KEY,
    -- Identity
    family              VARCHAR(64) NOT NULL,
    strategy_class      VARCHAR(256) NOT NULL,
    instrument_id       VARCHAR(128) NOT NULL,
    venue               VARCHAR(10) NOT NULL,
    version             VARCHAR(32) NOT NULL,
    variant             VARCHAR(64),
    -- Lifecycle
    status              VARCHAR(16) NOT NULL
        CHECK (status IN ('dev', 'backtest', 'paper', 'active', 'retired')),
    -- Configuration snapshot
    config_snapshot     JSONB NOT NULL,
    parent_version      VARCHAR(32),
    changelog           TEXT,
    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at        TIMESTAMPTZ,
    retired_at          TIMESTAMPTZ,
    -- Constraints
    UNIQUE (family, strategy_class, instrument_id, venue, version)
);

CREATE INDEX IF NOT EXISTS idx_registry_family ON strategy_registry(family);
CREATE INDEX IF NOT EXISTS idx_registry_status ON strategy_registry(status);
CREATE INDEX IF NOT EXISTS idx_registry_instrument ON strategy_registry(instrument_id);
```

---

## 4. Lifecycle State Machine

```
  ┌─────┐     ┌──────────┐     ┌───────┐     ┌────────┐     ┌─────────┐
  │ dev │ ──→ │ backtest │ ──→ │ paper │ ──→ │ active │ ──→ │ retired │
  └─────┘     └──────────┘     └───────┘     └────────┘     └─────────┘
     │              │               │              │               │
     │  Creating    │  Backtesting  │  Paper       │  Live         │  Archived
     │  & tuning    │  against      │  trading     │  trading      │  historical
     │              │  historical   │  simulation  │  real money   │  record only
     │              │  data         │              │               │
     └──────────────┴───────────────┴──────────────┴───────────────┘
                         Any state can go → retired
                         Retired → dev (reactivate — rare)
```

| State | In bundles.yaml? | Behavior |
|-------|------------------|----------|
| `dev` | No | Under development. Not deployed. |
| `backtest` | No | Registered, awaiting sweep/walk-forward results. |
| `paper` | Yes (`enabled: false`) | Deployed but uses paper fills. Trades journaled, no real money. |
| `active` | Yes (`enabled: true`) | Live trading. Full monitoring, risk limits enforced. |
| `retired` | No | Archived. Config + history preserved for analysis. |

### 4.1 Promotion Rules (`config/registry_rules.yaml`)

```yaml
promotion_rules:
  dev_to_backtest:
    min_backtest_runs: 0        # No barrier
  backtest_to_paper:
    min_sharpe: 1.0
    max_drawdown_pct: 15.0
    min_win_rate: 45.0
    min_trades: 50
    min_backtest_days: 90
  paper_to_active:
    min_paper_days: 10
    min_paper_trades: 20
    paper_sharpe: 0.5            # Lower bar — paper fills optimistic
    paper_max_drawdown_pct: 20.0
```

---

## 5. CLI Design — `sam strategy`

```bash
# Registration & Lifecycle
sam strategy list                                    # All versions, grouped by family
sam strategy list --family ORB                       # Only ORB variants
sam strategy list --status active                    # Only active strategies
sam strategy list --instrument TSLA.NASDAQ

sam strategy register --bundle orb-v1.3.yaml         # From bundle YAML snippet
sam strategy register --from-active orb-tsla-15m \   # Clone active as new dev
    --version 1.4.0 --changelog "Wider stop, tighter TP"

sam strategy promote orb-aggressive-tsla --to backtest
sam strategy promote orb-aggressive-tsla --to paper
sam strategy promote orb-aggressive-tsla --to active
sam strategy retire orb-aggressive-tsla
sam strategy reactivate orb-aggressive-tsla --to dev # Retired → dev

# Comparison & Analysis
sam strategy compare --family ORB                     # Ranked by Sharpe
sam strategy compare --versions 1.0.0 1.3.0 1.4.0
sam strategy diff orb-tsla-1.0.0 orb-tsla-1.3.0      # Parameter-level diff
sam strategy perf orb-aggressive-tsla                 # From performance_stats
sam strategy perf orb-aggressive-tsla --days 30

# Sync
sam strategy sync                                     # Registry → bundles.yaml + git commit
sam strategy sync --dry-run

# Pipeline (bridges 12.1 ↔ 12.2)
sam strategy pipeline orb-v1.4.0 \
    --backtest-start 2024-01-01 --backtest-end 2024-06-30 \
    --sweep stop_loss_ticks=5,10,15 \
    --sweep take_profit_ticks=20,30,40 \
    --auto-promote
```

---

## 6. bundles.yaml Sync Logic

```
strategy_registry (PG)          bundles.yaml (git-tracked)
┌──────────────────────┐       ┌─────────────────────────────┐
│ status = "active"    │       │ bundles:                     │
│ status = "paper"     │  ──→  │   - id: "orb-tsla-v1.3.0"  │
│                      │       │     enabled: true            │
│ config_snapshot JSONB│       │     venue: FUTU              │
│ family, version, etc │       │     strategy:                │
└──────────────────────┘       │       path: ...              │
                               │       config: ...            │
                               └─────────────────────────────┘
```

```python
# Conceptual sync logic:
def sync_registry_to_bundles():
    rows = db.query(
        "SELECT * FROM strategy_registry WHERE status IN ('paper', 'active')"
    )
    bundles = []
    for row in rows:
        cfg = row.config_snapshot
        bundles.append({
            "id": f"{cfg['family']}-{cfg['instrument_id'].split('.')[0]}-v{row.version}",
            "enabled": row.status == "active",
            "venue": row.venue,
            "family": row.family,
            "version": row.version,
            "strategy": {
                "path": row.strategy_class,
                "config": cfg.get("config", {}),
            },
            "bracket": cfg.get("bracket", {}),
        })
    write_bundles_yaml(bundles)
    git_commit(f"sync: {active_count} active, {paper_count} paper strategies")
```

---

## 7. Dashboard API Endpoints (12.2)

```
GET    /api/strategy/registry                       → [{family, version, status, instrument, venue, ...}]
GET    /api/strategy/registry/<id>                  → {full details including config_snapshot, changelog}
POST   /api/strategy/registry                       → {id}  (register new version)
PUT    /api/strategy/registry/<id>/promote          → {new_status}
PUT    /api/strategy/registry/<id>/retire           → {status: "retired"}
GET    /api/strategy/compare?ids=1,2,3              → {versions: [...], comparison_metrics: {...}}
GET    /api/strategy/<id>/diff?other=<id2>          → {parameter_diffs: [...], structural_diffs: [...]}
GET    /api/strategy/<id>/performance?days=30       → {dates: [...], metrics: {sharpe: [...], pnl: [...]}}
GET    /api/strategy/leaderboard?metric=sharpe      → [{strategy_id, version, value}]
POST   /api/strategy/pipeline                       → {run_id, status}  (start backtest→promote pipeline)
```

---

## 8. File Structure — New & Modified

```
src/sam_trader/
├── services/
│   └── strategy_registry/
│       ├── __init__.py               # NEW
│       ├── registry.py               # CRUD for strategy_registry PG table
│       ├── lifecycle.py              # Promotion/retirement logic + rule evaluation
│       ├── sync.py                   # Registry → bundles.yaml + git commit
│       └── comparison.py             # Cross-version comparison + ranking

docker/postgres/init/
└── 04_strategy_registry.sql          # NEW — DDL

config/
└── registry_rules.yaml               # NEW — promotion rule thresholds

sam-services/
└── dashboard/
    └── strategy/                     # NEW — matrix, comparison, perf history panels

tests/unit/services/strategy_registry/
├── test_registry.py
├── test_lifecycle.py
├── test_sync.py
└── test_comparison.py
```

---

## 9. Key Test Scenarios

| # | Test | What It Validates |
|---|------|-------------------|
| 1 | `register from bundle YAML snippet` | Config snapshot round-trip, UNIQUE constraint enforced |
| 2 | `register --from-active → new dev version` | Cloning preserves parent_version, increments version |
| 3 | `promote dev→backtest→paper→active` | Full lifecycle chain, timestamps set (created_at, activated_at) |
| 4 | `promote blocked by rules` | backtest_to_paper: Sharpe < 1.0 returns error |
| 5 | `retire active strategy` | Sets retired_at, returns OK |
| 6 | `compare --family ORB` | Ranks all ORB versions by Sharpe from performance_stats |
| 7 | `diff v1.0.0 v1.3.0` | Parameter-level: stop_loss_ticks changed, trade_size changed |
| 8 | `sync → bundles.yaml` | Only paper+active appear, active→enabled:true, paper→enabled:false |
| 9 | `sync --dry-run` | Preview output without modifying bundles.yaml or git |
| 10 | `pipeline --auto-promote` | Backtest → evaluate → store → promote (no human intervention) |
| 11 | `dashboard API: register → promote → compare` | Full API lifecycle through HTTP |
| 12 | `strategy with duplicate UNIQUE key rejected` | (family, class, instrument, venue, version) constraint |

---

*Last updated: 2026-05-27 — created from BUILD_PHASE_12_FUTURE.md §2*
