# Build Phase 12 — Backtesting, Strategy Management & Analytics Enrichment (FUTURE)

> **Status:** Planning / Reference Only — NOT for current build  
> **Purpose:** Three-part Phase 12 covering backtesting framework, strategy inventory & version management, and analytics enrichment (indicators, patterns, strategy library, dashboard).  
> **Prerequisite:** Phases 0–11 complete (Platform fully operational with live trading)  
> **Rule:** ALL implementations follow NautilusTrader recommended patterns. Zero code copied from Lean. Functional logic only — implemented in Nautilus-native Python.

---

## Phase Structure

Phase 12 is split into three independent FEATUREs. They share infrastructure (PG, Parquet catalog, dashboard) but can be built in parallel once data prerequisites are met.

| Phase | Title | Focus | Depends On |
|-------|-------|-------|------------|
| **12.1** | Backtesting Framework | Run backtests, sweep parameters, walk-forward optimize | Parquet catalog with historical bars |
| **12.2** | Strategy Inventory & Version Management | Version registry, lifecycle, promotion, comparison | Phase 12.1 (backtest results feed promotion decisions) |
| **12.3** | Strategy Library & Analytics Enrichment | Indicators, candlestick patterns, Lean strategy ports, dashboard analytics | Phase 10 dashboard, Phase 7 bundle system |

---

## 1. Phase 12.1 — Backtesting Framework

> **Status:** Not started  
> **Goal:** Production-grade backtesting using NautilusTrader's native `BacktestEngine` + `BacktestNode`. Historical data pipeline, CLI, dashboard runner, parameter sweep, and walk-forward optimization.  
> **Key insight:** NautilusTrader ships a Rust-backed backtesting engine — `BacktestEngine`, `BacktestNode`, `BacktestRunConfig`, `BacktestResult` — already installed but only used as a smoke test in `bundle_validation.py`. Phase 12.1 wires it for real use.

### 1.1 What We Already Have

| Component | Location | Current Use |
|-----------|----------|-------------|
| `BacktestEngine` | Nautilus (Rust/pyo3) | Used in `bundle_validation.py` — smoke test with 20 synthetic flat bars |
| `BacktestNode` | Nautilus | Not used |
| `BacktestRunConfig` | Nautilus | Not used |
| `BacktestDataConfig` | Nautilus | Not used |
| `BacktestVenueConfig` | Nautilus | Not used |
| `BacktestResult` | Nautilus | Not used |
| `ParquetDataCatalog` | `data/catalog/` on host volume | Empty — no historical data pipeline exists |
| `PortfolioAnalyzer` | Nautilus `analysis` package | Used offline in `PerformanceAnalyzer` service (Phase 8) |
| `performance_stats` table | PostgreSQL | Stores nightly stats for live strategies |

### 1.2 Nautilus Backtesting Stack (Reference)

```
BacktestRunConfig ──────────────────────────────────────────────┐
│  venues: [BacktestVenueConfig]      # OMS type, balances,     │
│  data: [BacktestDataConfig]         #   fill/latency/fee models│
│  engine: BacktestEngineConfig       # catalog paths, bar types,│
│                                     #   date ranges, instruments│
│                                     # kernel: cache, risk,     │
│                                     #   data, exec engines     │
└────────────────────────────────────────────────────────────────┘
        │
        ▼
BacktestNode(configs: list[BacktestRunConfig])
  ├── .build()       # Creates one BacktestEngine per config
  ├── .run()         # Executes all engines
  └── .get_engine(id) → BacktestEngine
        │
        ▼
BacktestEngine
  ├── add_venue(...)           # Venue with fill/latency/fee models
  ├── add_instrument(...)      # Instruments from Parquet catalog
  ├── add_strategy(...)        # ImportableStrategyConfig (same as live!)
  ├── add_data(bars/trades)    # Historical data from catalog
  ├── run()                    # Execute backtest
  └── get_result() → BacktestResult
        │
        ▼
BacktestResult
  ├── stats_pnls: dict[str, dict[str, float]]     # Per-strategy P&L breakdown
  ├── stats_returns: dict[str, float]             # Sharpe, Sortino, drawdown, etc.
  ├── total_events, total_orders, total_positions
  ├── elapsed_time, iterations
  └── run_started, run_finished, backtest_start, backtest_end
```

**Key capability:** `BacktestEngineConfig.run_analysis = True` (default) auto-runs `PortfolioAnalyzer` at engine completion, producing all 17 Nautilus statistics for free.

**BacktestVenueConfig** supports:
- Bar-adaptive high/low ordering (realistic intra-bar execution)
- Liquidity consumption tracking (fills consume available book depth)
- Queue position simulation (limit orders only fill when ahead-of-you qty clears)
- Configurable fill probability, slippage probability, latency models
- Maker/taker fee models, margin models
- OTO trigger modes (partial vs full), position ID generation

### 1.3 Stage A — Historical Data Pipeline & Basic Backtest

#### 1.3.1 Historical Bar Downloader

Futu OpenD provides `request_history_kline()` for OHLCV bars. Build a service that pulls bars per instrument and writes them to the Nautilus Parquet catalog.

```
src/sam_trader/services/bar_downloader.py    # New service
```

```python
# Conceptual API
class BarDownloader:
    """Download historical bars from Futu OpenD → Nautilus Parquet catalog."""

    async def download(
        self,
        instrument_ids: list[str],         # ["TSLA.NASDAQ", "AAPL.NASDAQ"]
        bar_type_spec: str = "5-MINUTE",   # 1-MINUTE, 5-MINUTE, 15-MINUTE, 1-HOUR, DAY
        lookback_days: int = 365,
        rate_limit_per_minute: int = 30,    # Futu free-tier: 30 req/min
    ) -> DownloadResult:
        ...
```

**Design decisions:**
- **Throttled downloading** — Futu free-tier rate limit is 30 requests/minute. For 250 trading days of 5-min bars (78 bars/day), 10 instruments = ~2,000 requests = ~67 minutes. The downloader is designed to run over 1–2 days as a background cron job, respecting rate limits.
- **Incremental updates** — only download bars newer than the latest bar already in the catalog. Daily cron keeps catalog current.
- **Bar types:** Start with `5-MINUTE` (most useful for intraday strategies). Add `1-MINUTE` and `15-MINUTE` later.
- **Output:** Nautilus `ParquetDataCatalog` format — directly ingestible by `BacktestDataConfig`.

CLI:
```bash
sam download-bars                           # Download for all instruments in bundles.yaml
sam download-bars --instrument TSLA.NASDAQ  # Single instrument
sam download-bars --bar-type 1-MINUTE       # Specific bar type
sam download-bars --lookback 180            # 6 months
```

#### 1.3.2 Backtest CLI (Stage A)

```bash
sam backtest <bundle-id>                     # Single bundle backtest
sam backtest --bundles config/bundles.yaml   # All enabled bundles
sam backtest --start 2024-01-01 --end 2024-06-30
sam backtest --strategy orb-aggressive-tsla  # Backtest a specific registered version
sam backtest --compare <run-id-1> <run-id-2> # Compare two backtest results
```

Implementation pattern:
```python
# Conceptual — uses Nautilus BacktestNode + BacktestRunConfig

async def run_backtest(
    bundles: list[ImportableStrategyConfig],
    instrument_ids: list[str],
    bar_type: str,
    start_date: str,
    end_date: str,
) -> BacktestResult:
    # 1. Build BacktestDataConfig pointing to Parquet catalog
    data_config = BacktestDataConfig(
        catalog_path="data/catalog",
        data_cls="nautilus_trader.model.data.Bar",
        instrument_ids=instrument_ids,
        bar_types=[f"{iid}-{bar_type}-LAST-EXTERNAL" for iid in instrument_ids],
        start_time=start_date,
        end_time=end_date,
    )

    # 2. Build venue config
    venue_config = BacktestVenueConfig(
        name="SIM",
        oms_type="NETTING",
        account_type="MARGIN",
        starting_balances=["100000 USD"],
    )

    # 3. Build engine config with strategies
    engine_config = BacktestEngineConfig(
        strategies=bundles,
        run_analysis=True,  # Auto-compute all Nautilus stats
    )

    # 4. Run
    run_config = BacktestRunConfig(
        venues=[venue_config],
        data=[data_config],
        engine=engine_config,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    node.run()
    return node.get_engine(run_config.id).get_result()
```

#### 1.3.3 Backtest Results Storage

New PostgreSQL table to persist backtest results for historical comparison:

```sql
CREATE TABLE backtest_results (
    id              SERIAL PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL UNIQUE,    -- Nautilus run_id
    run_config_id   VARCHAR(64) NOT NULL,           -- Which config produced this
    strategy_id     VARCHAR(128) NOT NULL,          -- Bundle ID or registry version
    instrument_id   VARCHAR(128) NOT NULL,
    bar_type        VARCHAR(64) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          VARCHAR(16) NOT NULL            -- 'running', 'completed', 'failed'
                        CHECK (status IN ('running', 'completed', 'failed')),
    -- Core metrics (denormalized for fast comparison queries)
    total_events    INTEGER,
    total_orders    INTEGER,
    total_positions INTEGER,
    elapsed_secs    NUMERIC(12, 3),
    -- Full stats blob
    stats_pnls      JSONB,                          -- Per-strategy P&L breakdown
    stats_returns   JSONB,                          -- Sharpe, Sortino, drawdown, etc.
    -- Equity curve (for charting)
    equity_curve    JSONB,                          -- [{date, equity}] time series
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Metadata for filtering
    strategy_family VARCHAR(64),                    -- ORB, MOMENTUM, RSI
    strategy_version VARCHAR(32),                   -- semver from registry
    tags            JSONB                           -- User-defined tags
);

CREATE INDEX idx_bt_results_strategy ON backtest_results(strategy_id);
CREATE INDEX idx_bt_results_date ON backtest_results(start_date, end_date);
CREATE INDEX idx_bt_results_family ON backtest_results(strategy_family);
```

### 1.4 Stage B — Parameter Sweep

Leverage `BacktestNode`'s multi-config orchestration to run parameter grids:

```bash
sam backtest --sweep stop_loss_ticks=5,10,15,20 \
             --sweep take_profit_ticks=20,30,40 \
             --bundle tsla-orb-15m-futu \
             --start 2024-01-01 --end 2024-06-30
```

This generates 4×3 = 12 `BacktestRunConfig` instances, builds them via `BacktestNode`, runs in parallel, and outputs a ranked comparison table:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Parameter Sweep: tsla-orb-15m-futu | 2024-01-01 → 2024-06-30                │
├──────────────┬───────────────┬──────────┬──────────┬───────────┬─────────────┤
│ Stop Loss    │ Take Profit   │ Net P&L  │ Sharpe   │ Max DD    │ Win Rate    │
├──────────────┼───────────────┼──────────┼──────────┼───────────┼─────────────┤
│ 5 ticks      │ 20 ticks      │ $1,234   │ 0.85     │ -12.3%    │ 42%         │
│ 10 ticks     │ 30 ticks      │ $2,456   │ 1.42     │ -8.7%     │ 48%         │
│ 10 ticks     │ 40 ticks      │ $3,120   │ 1.68     │ -7.1%     │ 51%   ★     │
│ 15 ticks     │ 30 ticks      │ $1,890   │ 1.12     │ -10.2%    │ 45%         │
│ ...          │ ...           │ ...      │ ...      │ ...       │ ...         │
└──────────────┴───────────────┴──────────┴──────────┴───────────┴─────────────┘
```

**Multi-strategy sweeps:** When multiple strategies are swept, results are grouped by strategy class with a ranking table.

### 1.5 Stage C — Walk-Forward Optimization

Train/test split with rolling windows:

```bash
sam backtest --walk-forward \
             --bundle orb-aggressive-tsla \
             --train 90d --test 30d \
             --sweep stop_loss_ticks=5,10,15 \
             --sweep take_profit_ticks=20,30,40
```

```
Window 1: Train 2024-01-01→2024-03-31 | Test 2024-04-01→2024-04-30
  Best on train: sl=10, tp=40  (Sharpe 1.68)
  Test result:    Sharpe 1.45, P&L $890   ✓

Window 2: Train 2024-02-01→2024-04-30 | Test 2024-05-01→2024-05-31
  Best on train: sl=15, tp=30  (Sharpe 1.52)
  Test result:    Sharpe 1.21, P&L $620   ✓
...
Overall walk-forward: Sharpe 1.33, P&L $2,340  (3/4 windows profitable)
Parameter stability:    sl=10 preferred in 2/4 windows, tp=40 preferred in 3/4
```

### 1.6 Dashboard — Backtest Runner & Results Viewer

Integrated into the Phase 10 dashboard at `sam-services:8080`:

#### 1.6.1 Backtest Runner Panel

- **Instrument selector:** Multi-select from catalog
- **Strategy selector:** Pick bundle(s) or registered strategy versions
- **Date range picker:** Calendar widgets for start/end
- **Parameter sweep toggle:** Enable grid with min/max/step inputs
- **Walk-forward toggle:** Enable with train/test window sizes
- **Run button:** Triggers async backtest via API
- **Progress bar:** Live status during multi-run sweeps

#### 1.6.2 Backtest Results Viewer

- **Results table:** Sortable, filterable list of past backtest runs (from `backtest_results` PG table)
- **Comparison mode:** Select 2+ runs → side-by-side metric table + overlaid equity curves
- **Equity curve chart:** Interactive line chart (zoom, pan, hover for values). Overlay with drawdown shaded region.
- **Monthly returns heatmap:** Calendar grid for any selected backtest run
- **Trade list:** Individual trade details (entry/exit time, price, P&L, MAE/MFE)
- **Export:** Download results as JSON/CSV

#### 1.6.3 Dashboard API Endpoints (12.1)

```
GET  /api/backtest/runs                  → [{run_id, strategy_id, start_date, end_date, status, ...}]
GET  /api/backtest/runs/<run_id>         → {result: BacktestResult, equity_curve, stats}
POST /api/backtest/run                   → {run_id, status: "started"}
     Body: {bundles: [...], start: "...", end: "...", sweep: {...}}
GET  /api/backtest/run/<run_id>/status   → {status, progress_pct, elapsed}
GET  /api/backtest/compare?runs=id1,id2  → {runs: [{...}], comparison: {...}}
GET  /api/backtest/catalog/instruments   → [{instrument_id, bar_types, date_range}]
GET  /api/backtest/catalog/status        → {total_instruments, oldest_bar, newest_bar, total_bars}
```

### 1.7 File Structure — Phase 12.1

```
src/sam_trader/
├── services/
│   ├── bar_downloader.py           # NEW — Futu OpenD → Parquet catalog
│   └── backtest/
│       ├── __init__.py             # NEW
│       ├── engine.py               # BacktestEngine + BacktestNode wrapper
│       ├── sweep.py                # Parameter sweep orchestration
│       ├── walk_forward.py         # Walk-forward optimization
│       └── results.py              # BacktestResult → PG storage

tests/
├── unit/services/
│   ├── test_bar_downloader.py      # NEW
│   └── backtest/
│       ├── test_engine.py
│       ├── test_sweep.py
│       └── test_walk_forward.py
└── integration/
    └── test_backtest_e2e.py        # Full backtest → store → retrieve flow
```

---

## 2. Phase 12.2 — Strategy Inventory & Version Management

> **Status:** Not started  
> **Goal:** Managed inventory of strategies with full version lifecycle, performance tracking, comparison, and promotion rules. Shift from `bundles.yaml` as a flat config file to a proper version registry backed by PostgreSQL + CLI + dashboard.  
> **Key insight:** `bundles.yaml` already supports `family`, `version`, `variant` metadata fields, and `performance_stats` already tracks per-strategy-date results. The gap is the *management layer* — querying, comparing, promoting, retiring versions.

### 2.1 What We Already Have

| Capability | Location | Status |
|-----------|----------|--------|
| Version metadata in bundles | `bundles.yaml` (`family`, `version`, `variant` fields) | ✅ Schema exists; validation enforces semver |
| Metadata pass-through | `bundle_loader.py` — passes `family`/`version`/`variant` to strategy config | ✅ Wired |
| Per-strategy performance tracking | `performance_stats` PG table — `(date, strategy_id, stat_name, stat_value)` | ✅ Populated nightly |
| Bundle enable/disable toggle | `bundles.yaml` `enabled: true/false` | ✅ Works, but only binary |
| Bundle validation gate | `bundle_validation.py` — schema + strategy class + backtest smoke test | ✅ Works |

### 2.2 Strategy Registry — Database Schema

New PostgreSQL table as the canonical catalog of all strategy versions:

```sql
CREATE TABLE strategy_registry (
    id                  SERIAL PRIMARY KEY,
    -- Identity
    family              VARCHAR(64) NOT NULL,           -- "ORB", "MOMENTUM", "RSI"
    strategy_class      VARCHAR(256) NOT NULL,          -- "sam_trader.strategies.orb:OrbStrategy"
    instrument_id       VARCHAR(128) NOT NULL,          -- "TSLA.NASDAQ"
    venue               VARCHAR(10) NOT NULL,           -- "FUTU" | "IB"
    version             VARCHAR(32) NOT NULL,           -- semver "1.3.0"
    variant             VARCHAR(64),                    -- "aggressive", "bearish", "default"
    -- Lifecycle
    status              VARCHAR(16) NOT NULL            -- 'dev','backtest','paper','active','retired'
        CHECK (status IN ('dev', 'backtest', 'paper', 'active', 'retired')),
    -- Configuration
    config_snapshot     JSONB NOT NULL,                 -- Full frozen config at registration
    parent_version      VARCHAR(32),                    -- Which version this was derived from
    changelog           TEXT,                           -- Human description of what changed
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

### 2.3 Lifecycle States

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
                         Retired → dev (reactivate research — rare)
```

| State | Meaning | Behavior |
|-------|---------|----------|
| `dev` | Under development | Not deployed. Can be backtested locally. Does NOT appear in `bundles.yaml`. |
| `backtest` | Undergoing systematic backtesting | Registered in catalog. Awaiting parameter sweep / walk-forward results. Does NOT appear in `bundles.yaml`. |
| `paper` | Paper trading (simulated fills against live data) | Deployed to sam-trader but uses paper account or simulation mode. Trades journaled, P&L tracked, no real money at risk. |
| `active` | Live trading with real capital | In `bundles.yaml` with `enabled: true`. Full monitoring, alerts, risk limits enforced. |
| `retired` | Permanently decommissioned | Archived. Config + all performance history preserved for analysis. Does NOT appear in `bundles.yaml`. |

**Promotion rules (configurable):**
```yaml
# config/registry_rules.yaml
promotion_rules:
  dev_to_backtest:
    min_backtest_runs: 0           # No barrier — dev can go straight to backtesting
  backtest_to_paper:
    min_sharpe: 1.0
    max_drawdown_pct: 15.0
    min_win_rate: 45.0
    min_trades: 50
    min_backtest_days: 90
  paper_to_active:
    min_paper_days: 10
    min_paper_trades: 20
    paper_sharpe: 0.5               # Lower bar — paper fills are optimistic
    paper_max_drawdown_pct: 20.0
```

### 2.4 CLI Commands

```bash
# Registration & Lifecycle
sam strategy list                                    # All versions, grouped by family
sam strategy list --family ORB                       # Only ORB variants
sam strategy list --status active                    # Only active strategies
sam strategy list --instrument TSLA.NASDAQ           # All strategies on TSLA
sam strategy register --bundle orb-v1.3.yaml         # Register from a bundle YAML snippet
sam strategy register --from-active orb-tsla-15m \   # Clone active version as new dev
    --version 1.4.0 --changelog "Wider stop, tighter TP"

sam strategy promote orb-aggressive-tsla --to backtest
sam strategy promote orb-aggressive-tsla --to paper
sam strategy promote orb-aggressive-tsla --to active
sam strategy retire orb-aggressive-tsla               # Move to retired
sam strategy reactivate orb-aggressive-tsla --to dev  # Retired → dev (rare)

# Comparison & Analysis
sam strategy compare --family ORB                     # All ORB versions ranked by Sharpe
sam strategy compare --family ORB --metric max_drawdown
sam strategy compare --versions 1.0.0 1.3.0 1.4.0    # Specific versions
sam strategy diff orb-tsla-1.0.0 orb-tsla-1.3.0      # Parameter-level diff
sam strategy perf orb-aggressive-tsla                 # Performance history (from performance_stats)
sam strategy perf orb-aggressive-tsla --days 30       # Last 30 days

# Sync
sam strategy sync                                     # Registry → bundles.yaml + git commit
sam strategy sync --dry-run                           # Preview what would change
```

### 2.5 Backtest → Promote Pipeline

Automated workflow that connects Phase 12.1 backtesting with Phase 12.2 promotion:

```bash
sam strategy pipeline orb-aggressive-tsla-v1.4.0 \
    --backtest-start 2024-01-01 \
    --backtest-end 2024-06-30 \
    --sweep stop_loss_ticks=5,10,15 \
    --sweep take_profit_ticks=20,30,40 \
    --auto-promote                          # If results pass rules, auto-promote to paper
```

Pipeline steps:
1. **Backtest** — run parameter sweep via Phase 12.1 engine
2. **Evaluate** — compare best parameter combination against promotion rules
3. **Store** — persist `BacktestResult` to `backtest_results` table
4. **Promote** — if auto-promote enabled and rules pass, change status: `dev` → `backtest` (or `backtest` → `paper`)
5. **Report** — generate HTML report summarizing sweep results + promotion decision

### 2.6 Dashboard — Strategy Matrix & Version Comparison

Integrated into the Phase 10 dashboard at `sam-services:8080`:

#### 2.6.1 Strategy Inventory Matrix

- **Family tree view:** Hierarchical display — Family → Strategy Class → Instrument → Versions
- **Status badges:** Color-coded (dev=gray, backtest=blue, paper=yellow, active=green, retired=red)
- **Key metrics per version:** Current Sharpe, Max DD, Win Rate, Days Active, Total P&L
- **Quick actions:** Promote, Retire, Backtest, Compare, Diff
- **Filter bar:** By family, instrument, venue, status, date range

#### 2.6.2 Version Comparison View

- **Side-by-side metric cards:** Select 2–4 versions → compare Sharpe, Sortino, Max DD, Win Rate, Profit Factor, Expectancy
- **Overlaid equity curves:** Multi-line chart with each version's cumulative P&L over time
- **Parameter diff table:** What changed between versions (trade_size, stop_loss_ticks, take_profit_ticks, etc.)
- **Performance timeline:** When each version was active, with P&L context

#### 2.6.3 Strategy Performance History

- **Per-version time series:** Sharpe over time, drawdown timeline, daily P&L bars
- **Ranking leaderboard:** All active strategies ranked by chosen metric (Sharpe, P&L, Win Rate)
- **Degradation alerts:** Flag strategies where 30-day Sharpe has dropped >50% from 90-day average

#### 2.6.4 Dashboard API Endpoints (12.2)

```
GET  /api/strategy/registry                       → [{family, version, status, instrument, venue, ...}]
GET  /api/strategy/registry/<id>                  → {full details including config_snapshot, changelog}
POST /api/strategy/registry                       → {id}  (register new version)
PUT  /api/strategy/registry/<id>/promote          → {new_status}
PUT  /api/strategy/registry/<id>/retire           → {status: "retired"}
GET  /api/strategy/compare?ids=1,2,3              → {versions: [...], comparison_metrics: {...}}
GET  /api/strategy/<id>/diff?other=<id2>          → {parameter_diffs: [...], structural_diffs: [...]}
GET  /api/strategy/<id>/performance?days=30       → {dates: [...], metrics: {sharpe: [...], pnl: [...]}}
GET  /api/strategy/leaderboard?metric=sharpe      → [{strategy_id, version, value}]
POST /api/strategy/pipeline                       → {run_id, status}  (start backtest→promote pipeline)
```

### 2.7 bundles.yaml Sync

The `bundles.yaml` file remains the **active execution config** for `sam-trader`. It is generated from the registry:

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

```bash
sam strategy sync
# Reads strategy_registry WHERE status IN ('paper', 'active')
# Generates bundles.yaml with enabled: true for 'active', enabled: false for 'paper'
# Git commits with message: "sync: 3 active, 2 paper strategies"
```

### 2.8 File Structure — Phase 12.2

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
└── 02_strategy_registry.sql          # NEW — strategy_registry table DDL

config/
└── registry_rules.yaml               # NEW — promotion rule thresholds

tests/unit/services/strategy_registry/
├── test_registry.py
├── test_lifecycle.py
├── test_sync.py
└── test_comparison.py
```

---

## 3. Phase 12.3 — Strategy Library & Analytics Enrichment

> **Status:** Planning  
> **Goal:** Close indicator/pattern/strategy gaps identified during QuantConnect Lean analysis. Add 10 missing indicators, 8 candlestick patterns, 5 Lean strategy ports, and dashboard analytics enhancements (Tier 1 + Tier 2).  
> **Rule:** ALL implementations follow NautilusTrader recommended patterns. Zero code copied from Lean. Functional logic only — implemented in Nautilus-native Python.

### 3.1 Missing Indicators — Critical Gap Closure

Nautilus covers ~60% of the top-30 most-used indicators. We identified 10 indicators that are missing and materially impact strategy development. All are "trivially formulaic" — the logic is well-documented, the implementation is straightforward on Nautilus.

#### 3.1.1 Priority Ranking

| # | Indicator | Why Missing Hurts | Complexity | Lean Reference |
|---|-----------|-------------------|------------|---------------|
| 1 | **ADX** (Average Directional Index) | Trend strength filter. Nautilus has `DirectionalMovement` (+DI/-DI only). ADX = smoothed DX. Used in 40%+ of trend strategies. | Low | [AverageDirectionalIndex.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/AverageDirectionalIndex.cs) |
| 2 | **Parabolic SAR** | Trailing stop + trend reversal. Extremely common in breakout/trend-following. | Low | [ParabolicStopAndReverse.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/ParabolicStopAndReverse.cs) |
| 3 | **SuperTrend** | Popular ATR-based trend indicator. Single-line buy/sell signal. Used heavily by retail algos. | Low | [SuperTrend.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SuperTrend.cs) |
| 4 | **Williams %R** | Simple overbought/oversold (-100 to 0). Widely taught, commonly used as confirmation. | Trivial | [WilliamsPercentR.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/WilliamsPercentR.cs) |
| 5 | **MFI** (Money Flow Index) | Volume-weighted RSI. Stronger signal than RSI alone for confirming breakouts. | Low | [MoneyFlowIndex.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/MoneyFlowIndex.cs) |
| 6 | **Heikin-Ashi** | Smoother OHLCV transform. Removes noise, makes trends visually obvious. | Trivial | [HeikinAshi.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/HeikinAshi.cs) |
| 7 | **TRIX** | Triple-smoothed momentum. Filters whipsaws, popular in crypto and mean-reversion. | Low | [Trix.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/Trix.cs) |
| 8 | **ZigZag** | Swing high/low detection. Essential for pattern-based strategies (support/resistance, Elliott Wave, harmonic patterns). | Medium | [ZigZag.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/ZigZag.cs) |
| 9 | **Beta** (rolling) | Market sensitivity. Required for hedging calculations, pairs-trade ratio sizing. | Medium | [Beta.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/Beta.cs) |
| 10 | **Rolling Sharpe / Sortino** | Real-time risk-adjusted return monitoring. Essential for live strategy evaluation dashboard. | Low | [SharpeRatio.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SharpeRatio.cs) · [SortinoRatio.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SortinoRatio.cs) |

#### 3.1.2 Implementation Pattern (Nautilus-Native)

Nautilus indicators may be compiled (Rust/pyo3). Custom indicators in Python should follow one of these patterns:

**Option A — Python function wrapper:**
```python
def adx(high: list[float], low: list[float], close: list[float], period: int = 14) -> float:
    """Compute ADX from arrays. Use with on_bar() or on_quote_tick()."""
    ...
```

**Option B — If Nautilus supports Python `Indicator` subclass:**
```python
from nautilus_trader.indicators.base import Indicator

class AverageDirectionalIndex(Indicator):
    """ADX trend strength indicator."""
    def __init__(self, period: int = 14):
        super().__init__()
        self._period = period
        ...

    def handle_bar(self, bar):
        """Update from OHLCV bar."""
        ...
```

**Verify at implementation time** which pattern Nautilus v1.227+ supports for custom Python indicators. Refer to:
- Nautilus docs: `docs/indicators.md` (in Nautilus documentation)
- Existing pattern: `sam_trader/strategies/orb.py` for indicator usage pattern

#### 3.1.3 File Structure

```
src/sam_trader/indicators/       # New package
├── __init__.py                   # Re-exports all custom indicators
├── trend.py                      # ADX, SuperTrend, ParabolicSAR, HeikinAshi
├── momentum.py                   # WilliamsR, MFI, TRIX, ZigZag
└── risk.py                       # Beta, RollingSharpe, RollingSortino
```

Tests:
```
tests/unit/indicators/
├── test_trend.py
├── test_momentum.py
└── test_risk.py
```

### 3.2 Candlestick Pattern Recognition — Top 8

Nautilus has ZERO candlestick pattern recognition. Lean has 40+. We need the 8 most-used single/multi-candle patterns.

#### 3.2.1 Top 8 Patterns (Most Useful)

| # | Pattern | Type | Signal | Candle Count | Lean Reference |
|---|---------|------|--------|-------------|---------------|
| 1 | **Doji** | Single | Indecision / potential reversal | 1 | [Doji.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Doji.cs) |
| 2 | **Hammer** | Single | Bullish reversal (downtrend) | 1 | [Hammer.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Hammer.cs) |
| 3 | **Shooting Star** | Single | Bearish reversal (uptrend) | 1 | [ShootingStar.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/ShootingStar.cs) |
| 4 | **Engulfing** | Double | Bullish/Bearish reversal | 2 | [Engulfing.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Engulfing.cs) |
| 5 | **Morning Star** | Triple | Bullish reversal (downtrend bottom) | 3 | [MorningStar.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/MorningStar.cs) |
| 6 | **Evening Star** | Triple | Bearish reversal (uptrend top) | 3 | [EveningStar.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/EveningStar.cs) |
| 7 | **Harami** | Double | Reversal/continuation (bullish/bearish) | 2 | [Harami.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Harami.cs) |
| 8 | **Piercing / Dark Cloud** | Double | Bullish reversal (piercing) / Bearish reversal (dark cloud) | 2 | [Piercing.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Piercing.cs) · [DarkCloudCover.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/DarkCloudCover.cs) |

> **Note on InvertedHammer / HangingMan / DragonflyDoji / GravestoneDoji:**  
> These are shape-identical to Hammer/Shooting Star/Doji — only context (trend location) differs.  
> Implement Hammer and Shooting Star as base recognizers; add context checks to classify sub-types.

#### 3.2.2 Recognition Logic Pattern

All patterns share the same structural logic. Implement a base utility class:

```python
# Pattern recognition from OHLCV data
# Format: each pattern is a function that takes list of Bars and returns bool + direction

def is_doji(open_: float, high: float, low: float, close: float, 
            body_threshold: float = 0.05) -> bool:
    """Doji: body <= threshold% of (high-low) range."""
    body = abs(close - open_)
    range_ = high - low
    if range_ == 0:
        return True
    return (body / range_) <= body_threshold

def is_hammer(prev_bars: list, current: Bar, body_ratio: float = 0.3,
              lower_shadow_ratio: float = 2.0) -> int:
    """Hammer in downtrend → bullish reversal signal.
    Returns: 1 = bullish hammer, -1 = hanging man (bearish), 0 = no pattern.
    
    Rules:
      - Small real body at upper end of range
      - Long lower shadow (>= 2x body)
      - Little/no upper shadow
      - Preceded by downtrend (check prev_bars)
    """
    ...
```

#### 3.2.3 File Structure

```
src/sam_trader/indicators/
├── __init__.py
├── candles.py                   # Base candle utilities + single-candle patterns
│     is_doji()                  #   Doji detection + sub-types
│     is_hammer()                #   Hammer / Hanging Man / Inverted Hammer
│     is_shooting_star()         #   Shooting Star
├── candle_patterns.py           # Multi-candle patterns
│     is_engulfing()             #   Bullish / Bearish engulfing
│     is_morning_star()          #   Morning star (3-candle)
│     is_evening_star()          #   Evening star (3-candle)
│     is_harami()                #   Harami (bullish/bearish)
│     is_dark_cloud_cover()      #   Dark cloud cover (bearish)
│     is_piercing_line()         #   Piercing line (bullish)
└── ...
```

Tests:
```
tests/unit/indicators/
├── test_candles.py
└── test_candle_patterns.py
```

### 3.3 Core Strategy Library — Top 10 Strategies

From a survey of QuantConnect Lean (32 framework models), TradingView Pine Script, MetaTrader 4/5 EA community, and institutional desk strategies, we identified the **top 25 most popular strategies** for equities (intra-day + swing). See §3.5 for the full survey.

From those 25, we target **10 for Phase 12.3** — 5 ported from Lean's algorithm framework, plus 5 high-impact Nautilus-native wrappers where indicators already exist. These are NOT code copies — we extract functional signal logic and implement using Nautilus `Strategy` patterns.

> **Note:** This section was previously called "Strategy Registry" — renamed to avoid confusion with the Phase 12.2 Strategy Version Registry. These are *strategy types* (new Python classes), whereas 12.2 manages *version instances* of strategies.

#### 3.3.1 Top 10 — Ranked by Impact

**Lean Ports (5 strategies):**

| # | Model | Type | Signal Logic | Intraday | Swing | Lean Reference |
|---|-------|------|-------------|----------|-------|---------------|
| 1 | **RSI Strategy** | Mean Rev | RSI < 30 → BUY; RSI > 70 → SELL | ✅ | ✅ | [RsiAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/RsiAlphaModel.py) |
| 2 | **EMA Cross Strategy** | Trend | Fast EMA crosses Slow EMA → BUY/SELL | ✅ | ✅ | [EmaCrossAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/EmaCrossAlphaModel.py) |
| 3 | **MACD Strategy** | Trend | MACD crosses signal line → BUY/SELL | ✅ | ✅ | [MacdAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py) |
| 4 | **Momentum Rank Strategy** | Momentum | Rank N symbols by returns; buy top K | ✅ | ✅ | [HistoricalReturnsAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/HistoricalReturnsAlphaModel.py) |
| 5 | **Bollinger Band MR** | Mean Rev | Price touches band → reversion to mean | ✅ | ✅ | [BollingerBands.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/BollingerBands.cs) |

**Nautilus-Native Wrappers (5 strategies — all indicators already in Nautilus, ~155 lines total):**

| # | Model | Type | Signal Logic | Intraday | Swing | Nautilus Indicators |
|---|-------|------|-------------|----------|-------|--------------------|
| 6 | **SuperTrend** | Trend | ATR-based trailing stop flips above/below price; single-line buy/sell signal | ✅ | ✅ | ATR (native) + custom SuperTrend formula |
| 7 | **VWAP Reversion** | Mean Rev | Price extended above/below VWAP → reversion to VWAP; 2σ band touch entry | ✅ | ❌ | VWAP (native) |
| 8 | **Donchian Channel Breakout** | Trend | Price breaks N-day high → BUY; breaks N-day low → SELL (Turtle system) | ✅ | ✅ | DonchianChannel (native) |
| 9 | **Stochastic Oscillator** | Mean Rev | %K crosses %D; <20 oversold → BUY, >80 overbought → SELL | ✅ | ✅ | Stochastics (native) |
| 10 | **Z-Score Mean Reversion** | Mean Rev | (Price - SMA) / StdDev crosses ±2.0 → mean reversion trade | ✅ | ✅ | SMA, StandardDeviation (native) |

#### 3.3.2 Nautilus Strategy Pattern

Each ported strategy follows this Nautilus-native pattern:

```python
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.model.data import Bar
from dataclasses import dataclass

@dataclass(frozen=True)
class RsiStrategyConfig(StrategyConfig):
    """Configuration for RSI threshold strategy."""
    instrument_id: str
    bar_type: str
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0
    trade_size: int = 100
    # bracket order config inherited from StrategyConfig

class RsiStrategy(Strategy):
    """RSI overbought/oversold strategy — Nautilus-native implementation.
    
    Functional logic from Lean's RsiAlphaModel, implemented per
    Nautilus Strategy subclass pattern (on_bar, submit_order, etc.).
    """
    
    def __init__(self, config: RsiStrategyConfig):
        super().__init__(config)
        self._config = config
        self._rsi = None  # Initialized in on_start()
    
    def on_start(self):
        """Subscribe to bar data, initialize RSI indicator."""
        self.subscribe_bars(self._config.bar_type)
        # Initialize RSI from nautilus_trader.indicators
    
    def on_bar(self, bar: Bar):
        """Process each bar. Check RSI crossing thresholds."""
        ...
        if rsi_value < self._config.oversold:
            self.submit_order(...)  # Buy bracket order
        elif rsi_value > self._config.overbought:
            self.submit_order(...)  # Sell bracket order
```

#### 3.3.3 File Structure

```
src/sam_trader/strategies/registry/    # New package (strategy implementations)
├── __init__.py
├── _base.py                           # Shared utilities (bracket orders, risk checks)
│
│   # Lean ports (5 strategies — ~15-25 hrs total)
├── rsi_strategy.py                    # RSI overbought/oversold
├── ema_cross_strategy.py              # EMA crossover
├── macd_strategy.py                   # MACD signal cross
├── momentum_rank_strategy.py          # Top-K by returns
├── bollinger_mean_reversion.py        # Bollinger band mean reversion
│
│   # Nautilus-native wrappers (5 strategies — ~8-10 hrs total)
├── supertrend_strategy.py             # SuperTrend ATR trailing
├── vwap_reversion_strategy.py         # VWAP mean reversion
├── donchian_breakout_strategy.py      # Donchian Channel (Turtle)
├── stochastic_strategy.py             # Stochastic oscillator
└── zscore_reversion_strategy.py       # Z-Score / StdDev bands
```

Tests:
```
tests/unit/strategies/registry/
├── test_rsi_strategy.py
├── test_ema_cross_strategy.py
├── test_macd_strategy.py
├── test_momentum_rank_strategy.py
├── test_bollinger_mean_reversion.py
├── test_supertrend_strategy.py
├── test_vwap_reversion_strategy.py
├── test_donchian_breakout_strategy.py
├── test_stochastic_strategy.py
└── test_zscore_reversion_strategy.py
```

Bundle config entries:
```yaml
# config/bundles.yaml — example registry strategy entries
bundles:
  - id: "tsla-rsi-14-futu"
    enabled: false
    venue: FUTU
    family: RSI
    version: "1.0.0"
    variant: default
    strategy:
      path: sam_trader.strategies.registry.rsi_strategy:RsiStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        period: 14
        oversold: 30
        overbought: 70
        trade_size: 10
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
```

### 3.4 Dashboard Analytics Enhancement

The Phase 10 dashboard (basic HTML) is extended with richer analytics inspired by Lean's `Report` module. This section is separate from the Phase 12.1 backtest dashboard and Phase 12.2 strategy management dashboard — it focuses on live/post-hoc performance analytics.

#### 3.4.1 Data Sources Already Available (Phase 6–8)

These data points ALREADY exist in our infrastructure. The dashboard just needs to read and display them:

| Data Point | Source | Location | Phase Built |
|------------|--------|----------|-------------|
| Realized P&L (per strategy, per day) | Redis | `sam:pnl:{strategy}:{date}` | Phase 6 (RealizedPnLTrackerActor) |
| Trade fills (all venues) | PostgreSQL | `fills` table (ts_event, instrument_id, venue, side, qty, price, commission) | Phase 6 (TradeJournalActor) |
| Current positions | PostgreSQL | `positions` table (instrument_id, venue, net_qty, avg_px) | Phase 8 (PositionSnapshotActor) |
| Order history | PostgreSQL | `orders` table (status, type, side, qty, price, filled_qty) | Phase 6 (TradeJournalActor) |
| Performance stats | PostgreSQL | `performance_stats` table | Phase 8 (PerformanceAnalyzer) |
| Service health | Docker | `docker inspect` / health check endpoints | Phase 0 |

#### 3.4.2 Dashboard Sections — Tier 1

Inspired by Lean's `Report/ReportElements/`. Each section has a reference to the Lean implementation for logic, formula, and rendering ideas.

| Section | What It Shows | Lean Reference | Data Needed | Complexity |
|---------|-------------|---------------|-------------|------------|
| **System Health** | Green/red per service (PG, Redis, Futu, Trader, Services, IB) | N/A (Docker health checks) | Docker inspect | Easy |
| **Equity Curve** | Cumulative P&L over time with benchmark overlay. Line chart. | [CumulativeReturnsReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/CumulativeReturnsReportElement.cs) | PG `fills` → daily P&L aggregation | Easy |
| **Drawdown Chart** | Peak-to-trough drawdown timeline. Red shaded area. | [DrawdownReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/DrawdownReportElement.cs) · [MaxDrawdownReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/MaxDrawdownReportElement.cs) | Equity curve computation | Medium |
| **Performance Summary** | Net P&L, Win Rate, Sharpe (20d), Max DD, Expectancy — top-row KPI cards | [SharpeRatioReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/SharpeRatioReportElement.cs) · [CAGRReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/CAGRReportElement.cs) | PG `fills` + computation | Medium |
| **Current Positions** | Table: symbol, venue, qty, avg px, mark price, unrealized P&L, P&L% | N/A (live positions) | PG `positions` + PG `fills.latest_price` | Easy |
| **Recent Fills** | Last 20 fills: timestamp, symbol, side, qty, price, venue. BUY green, SELL red. | N/A (live fills feed) | PG `fills` ORDER BY ts_event DESC LIMIT 20 | Trivial |
| **Strategy P&L Table** | Per-strategy: realized P&L today, win rate, total trades | N/A (strategy breakdown) | Redis `sam:pnl:{strategy}:{date}` + PG query | Easy |
| **Drawdown Recovery** | Days to recover from each drawdown event. List of DD events. | [MaxDrawdownRecoveryReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/MaxDrawdownRecoveryReportElement.cs) | Drawdown computation | Easy |
| **Total Fees** | Commissions by venue, month-to-date | N/A (fee summary) | PG `fills.commission` SUM | Trivial |

#### 3.4.3 Dashboard Sections — Tier 2

| Section | What It Shows | Lean Reference | Complexity |
|---------|-------------|---------------|------------|
| **Monthly Returns Heatmap** | Calendar-grid heatmap (green = profit, red = loss) | [MonthlyReturnsReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/MonthlyReturnsReportElement.cs) | Medium |
| **Annual Returns** | Year-by-year return bar chart | [AnnualReturnsReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/AnnualReturnsReportElement.cs) | Easy |
| **Rolling Sharpe** | 20-day rolling Sharpe ratio line chart | [RollingSharpeReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/RollingSharpeReportElement.cs) | Medium |
| **Rolling Beta** | 20-day rolling beta vs benchmark line chart | [RollingPortfolioBetaReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/RollingPortfolioBetaReportElement.cs) | Medium (needs benchmark data) |
| **Asset Allocation** | Pie/donut chart: position sizing by instrument | [AssetAllocationReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/AssetAllocationReportElement.cs) | Easy |
| **Trades Per Day** | Bar chart: number of trades by day/week | [TradesPerDayReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/TradesPerDayReportElement.cs) | Easy |
| **Returns Per Trade** | Histogram of individual trade P&L | [ReturnsPerTradeReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/ReturnsPerTradeReportElement.cs) | Medium |
| **Exposure Over Time** | Long/short ratio time series | [ExposureReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/ExposureReportElement.cs) | Medium |
| **Sortino Ratio** | Downside-only risk-adjusted return | [SortinoRatioReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/SortinoRatioReportElement.cs) | Easy |
| **Information Ratio** | Excess return per unit of tracking error | [InformationRatioReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/InformationRatioReportElement.cs) | Medium |

#### 3.4.4 Dashboard API Endpoints (12.3)

```
sam-services (port 8080)

Tier 1:
  GET /api/equity-curve?days=30     → [{date, equity, benchmark}]          # Daily equity points
  GET /api/drawdown                 → {current_dd_pct, max_dd_pct, events: [{start, end, depth, recovery_days}]}
  GET /api/performance              → {net_pnl, win_rate, sharpe_20d, expectancy, total_fees, total_trades}
  GET /api/strategy-pnl             → [{strategy_id, pnl_today, win_rate, trades}]
  GET /api/positions               → [{symbol, venue, qty, avg_px, mark, unrealized_pnl, pnl_pct}]

Tier 2:
  GET /api/monthly-returns          → [[{year, month, return_pct}]]        # Calendar heatmap data
  GET /api/rolling-sharpe?window=20 → [{date, sharpe}]                     # Rolling risk metric
  GET /api/asset-allocation         → [{symbol, weight_pct, market_value}] # Pie chart data
  GET /api/trade-distribution       → [{pnl_bucket, count}]                # Returns-per-trade histogram
  GET /api/exposure                 → [{date, long_pct, short_pct}]        # Long/short over time
```

#### 3.4.5 `sam report` CLI Command

Inspired by Lean's `Report.cs`, add a report generation command:

```bash
sam report                    # Generate 30-day HTML performance report
sam report --days 60          # Custom lookback
sam report --json             # Machine-readable JSON output
sam report --compare-backtest # Overlay backtest equity vs live
sam report --strategy orb-15m # Single-strategy report
```

---

### 3.5 Platform Strategy Survey & Coverage Map

> **Survey Date:** 2026-05-25  
> **Sources:** QuantConnect Lean (32 framework models), TradingView Pine Script library, MetaTrader 4/5 EA community, NautilusTrader examples, Quantopian archive, institutional desk strategies, academic quant finance literature.  
> **Purpose:** Map the top 25 most popular equities strategies to identify what SAM Trader already has, what's planned, and what's missing.

#### 3.5.1 Top 25 Strategies — Master Survey

SAM Status legend: ✅ BUILT (in Phase 7) | 🔵 PLANNED (in Phase 12.3) | ⬜ GAP (not yet planned)

| # | Strategy | Type | Intraday | Swing | Nautilus Indicators | Lines | SAM |
|---|----------|------|----------|-------|--------------------|-------|-----|
| 1 | **EMA/SMA Crossover** | Trend | ✅ | ✅ | EMA, SMA (native) | ~30 | 🔵 |
| 2 | **MACD Signal Cross** | Trend | ✅ | ✅ | MACD (native) | ~40 | 🔵 |
| 3 | **RSI Overbought/Oversold** | Mean Rev | ✅ | ✅ | RSI (native) | ~30 | 🔵 |
| 4 | **Bollinger Band Reversion** | Mean Rev | ✅ | ✅ | BollingerBands (native) | ~35 | 🔵 |
| 5 | **Opening Range Breakout** | Momentum | ✅ | ❌ | ATR (native) | ~350 | ✅ |
| 6 | **SuperTrend** | Trend | ✅ | ✅ | ATR (native) + custom | ~30 | 🔵 |
| 7 | **Gap and Go / Gap Fill** | Momentum | ✅ | ❌ | Prev close, ATR | ~80 | ⬜ |
| 8 | **VWAP Reversion** | Mean Rev | ✅ | ❌ | VWAP (native) | ~40 | 🔵 |
| 9 | **Donchian Channel Breakout** | Trend | ✅ | ✅ | DonchianChannel (native) | ~25 | 🔵 |
| 10 | **Parabolic SAR Trailing** | Trend | ✅ | ✅ | ParabolicSAR (native) | ~30 | ⬜ |
| 11 | **Ichimoku Cloud** | Trend | ✅ | ✅ | IchimokuCloud (native) | ~60 | ⬜ |
| 12 | **Opening Drive / 1H Momentum** | Momentum | ✅ | ❌ | RateOfChange (native) | ~40 | ⬜ |
| 13 | **Stochastic Oscillator** | Mean Rev | ✅ | ✅ | Stochastics (native) | ~35 | 🔵 |
| 14 | **ADX Trend Filter + Entry** | Trend | ✅ | ✅ | DirectionalMovement (native) + ADX custom | ~50 | ⬜ |
| 15 | **Keltner Channel Reversion** | Mean Rev | ✅ | ✅ | KeltnerChannel (native) | ~30 | ⬜ |
| 16 | **Pairs Trading / Stat Arb** | Mean Rev | ✅ | ✅ | Regression, correlation | ~200 | ⬜ |
| 17 | **Support/Resistance Bounce** | Pattern | ✅ | ✅ | ZigZag, Swings (native) | ~70 | ⬜ |
| 18 | **Dual Thrust Breakout** | Momentum | ✅ | ❌ | Rolling OHLC | ~50 | ⬜ |
| 19 | **Pivot Point Bounce** | Pattern | ✅ | ✅ | Prior day OHLC | ~30 | ⬜ |
| 20 | **Volume-Weighted Momentum** | Momentum | ✅ | ✅ | OBV (native), VWAP | ~60 | ⬜ |
| 21 | **Z-Score / StdDev Bands** | Mean Rev | ✅ | ✅ | SMA, StdDev (native) | ~25 | 🔵 |
| 22 | **MA Ribbon (Guppy)** | Trend/Swing | ❌ | ✅ | 12× EMA (native) | ~70 | ⬜ |
| 23 | **Elder Ray (Bull/Bear Power)** | Swing | ❌ | ✅ | EMA (native) | ~25 | ⬜ |
| 24 | **20-Day High/Low Breakout** | Swing | ❌ | ✅ | DonchianChannel (native) | ~25 | ⬜ |
| 25 | **Candlestick Pattern Trigger** | Pattern | ✅ | ✅ | Custom patterns (§3.2) | ~80 | 🔵 |

#### 3.5.2 Coverage Summary

```
SAM Trader Today (2 strategies — Phase 7):
  ✅ ORB (#5)
  ✅ Momentum (below top 25, in library)

Phase 12.3 Planned (8 strategies — §3.3):
  🔵 EMA Cross (#1), MACD (#2), RSI (#3), Bollinger (#4)
  🔵 SuperTrend (#6), VWAP (#8), Donchian (#9), Stochastic (#13), Z-Score (#21)

After 12.3: 10 of top 25 covered (40%)
Still unmatched: 15 strategies

Top Priority Gaps (all ≤60 lines, all indicators native):
  ⬜ Parabolic SAR (#10)         — 30 lines, ParabolicSAR native
  ⬜ Keltner Channel (#15)        — 30 lines, KeltnerChannel native
  ⬜ Pivot Point (#19)            — 30 lines, prior day OHLC
  ⬜ Elder Ray (#23)              — 25 lines, EMA native
  ⬜ 20-Day Breakout (#24)        — 25 lines, DonchianChannel native
  ⬜ ADX + Entry (#14)            — 50 lines, DirectionalMovement native
  ⬜ Opening Drive (#12)          — 40 lines, RateOfChange native
  ⬜ Gap and Go (#7)              — 80 lines, complements Phase 9 pipeline

Medium Effort Gaps (recommend Phase 12.4):
  ⬜ Ichimoku Cloud (#11)         — 60 lines, IchimokuCloud native
  ⬜ Dual Thrust (#18)            — 50 lines, rolling OHLC
  ⬜ Volume-Weighted Momentum (#20) — 60 lines, OBV + VWAP native
  ⬜ Support/Resistance (#17)     — 70 lines, needs ZigZag from §3.1
  ⬜ MA Ribbon (#22)              — 70 lines, 12× EMA native

High Effort Gaps (recommend Phase 12.5+):
  ⬜ Pairs Trading (#16)          — 200 lines, needs cointegration testing
```

#### 3.5.3 Platform Popularity Notes

| Platform | Top Strategies Observed |
|----------|------------------------|
| **QuantConnect** | EMA Cross, MACD, RSI, Bollinger, Pairs Trading, Momentum Rank, Mean Reversion, Cointegration |
| **TradingView (Pine Script)** | SuperTrend, Ichimoku, MACD+RSI combo, Bollinger+RSI combo, VWAP, EMA Ribbon, Pivot Points, Support/Resistance |
| **MetaTrader 4/5** | Stochastic, Parabolic SAR, ADX, Elder Ray, MA Cross, Bollinger, Ichimoku, RSI, MACD, Grid Trading |
| **Institutional Desks** | VWAP, TWAP, Implementation Shortfall, Pairs Trading, Statistical Arbitrage, Market Making, Delta Hedging |
| **Retail Day Traders (US)** | ORB, Gap and Go, VWAP Reversion, First Hour Momentum, Support/Resistance Bounce |

#### 3.5.4 Strategy Type Distribution

| Type | Count | Strategies | Best Market Condition |
|------|-------|-----------|-----------------------|
| **Trend Following** | 7 | #1, #2, #6, #9, #10, #11, #14 | Trending markets (ADX > 25) |
| **Mean Reversion** | 8 | #3, #4, #8, #13, #15, #16, #17, #21 | Range-bound / choppy markets |
| **Momentum / Breakout** | 6 | #5, #7, #12, #18, #20, #24 | Strong directional moves, volume confirmation |
| **Pattern-Based** | 2 | #19, #25 | Works in all conditions with proper context |
| **Swing-Specific** | 2 | #22, #23 | Multi-day holding periods, daily bars |

**Key insight:** A diversified strategy portfolio should include representatives from all 4 main types. Trend strategies win in trending markets and lose in chop. Mean reversion wins in chop and loses in trends. Having both + a regime filter (Phase 9) creates a robust system.

---

## 4. Phase 12 Summary — All Deliverables

### 4.1 Phase 12.1 — Backtesting Framework

| # | Deliverable | Effort | Depends On |
|---|-------------|--------|-----------|
| 1.1 | Historical bar downloader (Futu OpenD → Parquet catalog) | 3–4 hrs | Futu connection (Phase 2) |
| 1.2 | `sam download-bars` CLI | 1 hr | 1.1 |
| 1.3 | Backtest engine wrapper (Nautilus `BacktestNode` + `BacktestRunConfig`) | 3–4 hrs | Parquet catalog with data |
| 1.4 | `sam backtest` CLI (single run) | 2–3 hrs | 1.3 |
| 1.5 | `backtest_results` PG table + storage service | 1–2 hrs | 1.3 |
| 1.6 | Backtest dashboard panels (runner, results viewer, equity curves) | 8–10 hrs | 1.3, Phase 10 dashboard |
| 1.7 | Parameter sweep (`sam backtest --sweep`) | 3–4 hrs | 1.3 |
| 1.8 | Walk-forward optimization (`sam backtest --walk-forward`) | 4–6 hrs | 1.7 |
| 1.9 | Backtest dashboard API endpoints | 2–3 hrs | 1.3, 1.5 |
| — | Tests (unit + integration) | 8–12 hrs | Per-deliverable |
| **Total** | | **35–49 hrs** | |

### 4.2 Phase 12.2 — Strategy Inventory & Version Management

| # | Deliverable | Effort | Depends On |
|---|-------------|--------|-----------|
| 2.1 | `strategy_registry` PG table + migration | 1 hr | PostgreSQL (Phase 0) |
| 2.2 | Registry CRUD service (`registry.py`) | 2–3 hrs | 2.1 |
| 2.3 | Lifecycle engine (`lifecycle.py`) — promotion/retirement rules | 2–3 hrs | 2.2 |
| 2.4 | `sam strategy` CLI group (list, register, promote, retire, compare, diff) | 4–5 hrs | 2.2, 2.3 |
| 2.5 | `sam strategy sync` — registry → bundles.yaml + git commit | 1–2 hrs | 2.2 |
| 2.6 | Backtest → promote pipeline (`sam strategy pipeline`) | 3–4 hrs | 1.3, 2.3 |
| 2.7 | Strategy dashboard panels (matrix, comparison, performance history) | 6–8 hrs | Phase 10 dashboard, 2.2 |
| 2.8 | Strategy dashboard API endpoints | 2–3 hrs | 2.2 |
| — | Tests (unit + integration) | 6–10 hrs | Per-deliverable |
| **Total** | | **27–39 hrs** | |

### 4.3 Phase 12.3 — Strategy Library & Analytics Enrichment

| # | Deliverable | Effort | Depends On |
|---|-------------|--------|-----------|
| 3.1 | 10 missing indicators | 15–20 hrs | None (formula-based) |
| 3.2 | 8 candlestick patterns + base utilities | 10–15 hrs | Bar data from data engine |
| 3.3a | 5 Lean strategy ports (RSI, EMA Cross, MACD, Momentum Rank, Bollinger MR) | 15–25 hrs | Phase 9 bundle system, indicator library (3.1) |
| 3.3b | 5 Nautilus-native strategy wrappers (SuperTrend, VWAP Reversion, Donchian Channel, Stochastic, Z-Score) | 8–10 hrs | Phase 9 bundle system, Nautilus native indicators (no custom indicators needed) |
| 3.4 | Dashboard Tier 1 (9 sections: health, equity, drawdown, performance, positions, fills, strategy P&L, drawdown recovery, fees) | 20–30 hrs | Phase 10 dashboard, PG/Redis data |
| 3.5 | Dashboard Tier 2 (8 sections: monthly heatmap, annual returns, rolling Sharpe, rolling beta, allocation, trades/day, trade distribution, exposure) | 20–30 hrs | 3.4 (Tier 1) |
| 3.6 | `sam report` CLI command with HTML/JSON output | 10–15 hrs | Dashboard API |
| — | Tests (unit + integration) | 15–25 hrs | Per-deliverable |
| **Total** | | **113–170 hrs** | |

### 4.4 Combined Totals

| Phase | Range |
|-------|-------|
| 12.1 — Backtesting Framework | 35–49 hrs |
| 12.2 — Strategy Inventory & Version Management | 27–39 hrs |
| 12.3 — Strategy Library & Analytics Enrichment | 113–170 hrs |
| **Grand Total** | **175–258 hrs** |

---

## 5. Dashboard Integration Map

All three phases extend the Phase 10 dashboard. Here's how they fit together without overlap:

```
Dashboard (sam-services:8080)
├── [Phase 10 — existing] System Health, Positions, Recent Fills
│
├── [Phase 12.1 — Backtesting]
│   ├── Backtest Runner          (configure + launch backtests)
│   ├── Backtest Results         (view past runs, compare, equity curves)
│   └── Backtest API endpoints   (GET/POST /api/backtest/*)
│
├── [Phase 12.2 — Strategy Management]
│   ├── Strategy Matrix          (family tree, status badges, quick actions)
│   ├── Version Comparison       (side-by-side metrics, parameter diff)
│   ├── Performance History      (time series per version, leaderboard)
│   └── Strategy API endpoints   (GET/POST/PUT /api/strategy/*)
│
└── [Phase 12.3 — Analytics Enrichment]
    ├── Equity Curve + Drawdown  (Tier 1 — live P&L tracking)
    ├── Performance KPI Cards    (Tier 1 — Sharpe, Win Rate, etc.)
    ├── Monthly Returns Heatmap  (Tier 2 — calendar grid)
    ├── Rolling Metrics          (Tier 2 — Sharpe, Beta)
    ├── Trade Distribution       (Tier 2 — histogram)
    └── Analytics API endpoints  (GET /api/equity-curve, /api/drawdown, etc.)
```

---

## 6. Reference Index

### 6.1 NautilusTrader — Backtesting Stack

| Component | Module |
|-----------|--------|
| `BacktestEngine` | `nautilus_trader.backtest` |
| `BacktestNode` | `nautilus_trader.backtest.node` |
| `BacktestRunConfig` | `nautilus_trader.backtest.config` |
| `BacktestVenueConfig` | `nautilus_trader.backtest.config` |
| `BacktestDataConfig` | `nautilus_trader.backtest.config` |
| `BacktestEngineConfig` | `nautilus_trader.backtest.config` |
| `BacktestResult` | `nautilus_trader.backtest.results` |
| `PortfolioAnalyzer` | `nautilus_trader.analysis.analyzer` |
| `ParquetDataCatalog` | `nautilus_trader.persistence.catalog.parquet` |

### 6.2 NautilusTrader — Engine Methods (Key)

| Engine | Key Methods |
|--------|------------|
| `BacktestEngine` | `add_venue`, `add_instrument`, `add_strategy`, `add_data`, `run`, `get_result`, `reset`, `dispose` |
| `BacktestNode` | `add_data_client_factory`, `build`, `run`, `get_engine`, `get_engines`, `download_data`, `load_catalog` |
| `BacktestResult` | `trader_id`, `run_id`, `stats_pnls`, `stats_returns`, `total_events`, `total_orders`, `elapsed_time` |

### 6.3 Lean Source Files — Strategy Ports (Phase 12.3)

| Model | Lean Source |
|-------|------------|
| RSI Alpha | [RsiAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/RsiAlphaModel.py) |
| EMA Cross Alpha | [EmaCrossAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/EmaCrossAlphaModel.py) |
| MACD Alpha | [MacdAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py) |
| Historical Returns Alpha | [HistoricalReturnsAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/HistoricalReturnsAlphaModel.py) |

### 6.4 Lean Source Files — Remaining Framework Models (Future Reference)

| Model | Lean Source |
|-------|------------|
| Base Pairs Trading | [BasePairsTradingAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/BasePairsTradingAlphaModel.py) |
| Pearson Pairs Trading | [PearsonCorrelationPairsTradingAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/PearsonCorrelationPairsTradingAlphaModel.py) |
| Constant Alpha | [ConstantAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/ConstantAlphaModel.py) |
| Equal Weighting PC | [EqualWeightingPortfolioConstructionModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Portfolio/EqualWeightingPortfolioConstructionModel.py) |
| Confidence Weighted PC | [ConfidenceWeightedPortfolioConstructionModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Portfolio/ConfidenceWeightedPortfolioConstructionModel.py) |
| Risk Parity PC | [RiskParityPortfolioConstructionModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Portfolio/RiskParityPortfolioConstructionModel.py) |
| Max Drawdown Risk (per security) | [MaximumDrawdownPercentPerSecurity.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/MaximumDrawdownPercentPerSecurity.py) |
| Max Drawdown Risk (portfolio) | [MaximumDrawdownPercentPortfolio.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/MaximumDrawdownPercentPortfolio.py) |
| Trailing Stop Risk | [TrailingStopRiskManagementModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/TrailingStopRiskManagementModel.py) |

### 6.5 Lean Performance Metrics (Report Elements)

Full list: [Report/ReportElements/](https://github.com/QuantConnect/Lean/tree/master/Report/ReportElements)

| Report Element | Source |
|---------------|--------|
| Statistics Builder (all formulas) | [StatisticsBuilder.cs](https://github.com/QuantConnect/Lean/blob/master/Common/Statistics/StatisticsBuilder.cs) |
| Performance Metrics (metric names) | [PerformanceMetrics.cs](https://github.com/QuantConnect/Lean/blob/master/Common/Statistics/PerformanceMetrics.cs) |
| Report Engine (HTML generation) | [Report.cs](https://github.com/QuantConnect/Lean/blob/master/Report/Report.cs) |
| Report Template (HTML structure) | [template.html](https://github.com/QuantConnect/Lean/blob/master/Report/template.html) |

### 6.6 SAM Trader Reference Docs

| Doc | Path |
|-----|------|
| SAM Trader V3 Plan | `docs/reference/SAM_TRADER_V3_PLAN.md` |
| Gap Analysis (Risk, Strategy, Journal, Perf) | `docs/reference/GAP_ANALYSIS_RISK_STRATEGY_JOURNAL_PERF.md` |
| Roadmap Gaps & TODO | `docs/reference/roadmap_gaps_todo.md` |
| Build Phase 7 (Strategy Library) | `docs/reference/BUILD_PHASE_7.md` |
| Build Phase 8 (sam-services) | `docs/reference/BUILD_PHASE_8.md` |
| Build Phase 9 (Pre-Market) | `docs/reference/BUILD_PHASE_9.md` |
| Build Phase 10 (Safety & Dashboard) | `docs/reference/BUILD_PHASE_10.md` |
| Bundle Guide (User) | `docs/user/BUNDLE_GUIDE.md` |

### 6.7 NautilusTrader Extension Points

> **⚠️ Verify at implementation time** — Nautilus APIs may change between versions.

| Extension | Nautilus Pattern | Reference |
|-----------|-----------------|-----------|
| Custom indicator | Subclass `Indicator` or function-based | Check Nautilus docs for `indicators` module |
| Strategy subclass | `from nautilus_trader.trading.strategy import Strategy` | See `sam_trader/strategies/orb.py` for pattern |
| Strategy config | `@dataclass(frozen=True)` subclass of `StrategyConfig` | See OrbStrategy pattern |
| Order submission | `self.submit_order()` / `self.submit_order_list()` | Nautilus Strategy API |
| Bracket orders | `order_factory.bracket()` pattern | See bundle config `bracket:` section |
| Backtest engine | `BacktestEngine` + `BacktestNode` | See `sam_trader/bundle_validation.py` for existing usage |
| Portfolio analysis | `PortfolioAnalyzer` | See `sam_trader/services/performance_analyzer.py` for existing usage |

---

*Last updated: 2026-05-25 — Phase 12 restructured into three FEATUREs (12.1 Backtesting, 12.2 Strategy Management, 12.3 Analytics Enrichment).*
*All Lean references point to exact source files for implementation-time lookup. ZERO code shall be copied — functional logic only, implemented per Nautilus patterns.*
