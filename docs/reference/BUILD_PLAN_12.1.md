# BUILD_PLAN 12.1 — Backtesting Framework

> **Status:** Planning  
> **Goal:** Production-grade backtesting using NautilusTrader's native `BacktestEngine` + `BacktestNode`. Historical data pipeline (Futu → Parquet), CLI, dashboard runner, parameter sweep, walk-forward optimization.  
> **Prev Phase:** Phase 11 EXIT — `sam_trader-9z3.12.9`  
> **Next:** [BUILD_PLAN_12.2.md](./BUILD_PLAN_12.2.md) (gates on 12.1 EXIT)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    12.1 — Backtesting                         │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  Stage A — Basic Backtest                                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ BarDownloader (Futu OpenD → Parquet catalog)             │ │
│  │   sam download-bars --instrument TSLA.NASDAQ            │ │
│  │   sam download-bars --bar-type 5-MINUTE --lookback 180  │ │
│  └─────────────────────────────────────────────────────────┘ │
│         │                                                     │
│         ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ BacktestEngine wrapper (BacktestNode + BacktestRunConfig)│ │
│  │   sam backtest <bundle-id> --start X --end Y            │ │
│  │   sam backtest --bundles config/bundles.yaml            │ │
│  └─────────────────────────────────────────────────────────┘ │
│         │                                                     │
│         ▼                                                     │
│  ┌─────────────────────┐  ┌────────────────────────────────┐ │
│  │ Backtest CLI output  │  │ backtest_results (PG + JSONB)  │ │
│  │ (table summary)      │  │   stats_pnls, stats_returns,   │ │
│  └─────────────────────┘  │   equity_curve, metadata        │ │
│                            └────────────────────────────────┘ │
│                                                               │
│  Stage B — Parameter Sweep                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ sam backtest --sweep stop_loss_ticks=5,10,15,20 \       │ │
│  │              --sweep take_profit_ticks=20,30,40 \       │ │
│  │              --bundle tsla-orb-15m-futu                 │ │
│  │                                                          │ │
│  │  Multi-config BacktestNode → ranked comparison table    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  Stage C — Walk-Forward                                       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ sam backtest --walk-forward --train 90d --test 30d \    │ │
│  │              --sweep stop_loss_ticks=5,10,15            │ │
│  │                                                          │ │
│  │  Rolling train/test windows → stability report          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  Dashboard                                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  POST /api/backtest/run          (launch backtest)       │ │
│  │  GET  /api/backtest/runs         (list past runs)        │ │
│  │  GET  /api/backtest/runs/<id>    (view result + equity)  │ │
│  │  GET  /api/backtest/compare?runs=id1,id2  (side-by-side)│ │
│  │  GET  /api/backtest/catalog/instruments  (data catalog)  │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — Nautilus Backtesting Stack

> **Source:** `nautilus_trader.backtest` (Rust/pyo3 — installed, only used in `bundle_validation.py` smoke test)

### 2.1 Core Types

```python
from nautilus_trader.backtest.config import (
    BacktestRunConfig,
    BacktestVenueConfig,
    BacktestDataConfig,
    BacktestEngineConfig,
)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
```

### 2.2 BacktestRunConfig — Full Wiring

```python
# BacktestRunConfig is the top-level orchestrator config
run_config = BacktestRunConfig(
    venues=[BacktestVenueConfig(
        name="SIM",
        oms_type="NETTING",       # or "HEDGING"
        account_type="MARGIN",    # or "CASH"
        starting_balances=["100000 USD"],
        # Optional: fill model, fee model, latency model, slippage
    )],
    data=[BacktestDataConfig(
        catalog_path="data/catalog",
        data_cls="nautilus_trader.model.data.Bar",
        instrument_ids=["TSLA.NASDAQ", "AAPL.NASDAQ"],
        bar_types=[
            "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
        ],
        start_time="2024-01-01",
        end_time="2024-06-30",
    )],
    engine=BacktestEngineConfig(
        strategies=bundles,        # list[ImportableStrategyConfig]
        run_analysis=True,         # Auto-runs PortfolioAnalyzer → free stats
        # Optional: cache, risk engine, data engine configs
    ),
    # Optional: batch_id, chunk_size for parallelization
)
```

### 2.3 BacktestNode — Build & Run Pattern

```python
# BacktestNode manages multiple BacktestEngine instances
node = BacktestNode(configs=[run_config])
node.build()           # Creates one BacktestEngine per config
node.run()             # Executes all engines (parallel per config)
engine = node.get_engine(run_config.id)  # Single engine access
result: BacktestResult = engine.get_result()

# BacktestResult fields:
result.stats_pnls        # dict[str, dict[str, float]]  — per-strategy P&L
result.stats_returns     # dict[str, float]             — Sharpe, Sortino, etc.
result.total_events      # int
result.total_orders      # int
result.total_positions   # int
result.elapsed_time      # float (seconds)
result.iterations        # int
result.run_started       # datetime
result.run_finished      # datetime
result.backtest_start    # datetime
result.backtest_end      # datetime
```

### 2.4 BacktestEngine — Low-Level API (Alternative)

```python
# Direct BacktestEngine usage (for smoke tests / simple runs)
engine = BacktestEngine()
engine.add_venue(client=sim_venue)
engine.add_instrument(tsla_instrument)
engine.add_strategy(strategy_config)
engine.add_data(bars)            # Add historical bars
engine.run()
result = engine.get_result()
engine.reset()                    # Reuse engine for next config
engine.dispose()
```

### 2.5 BacktestVenueConfig — Fill & Latency Models

```python
BacktestVenueConfig(
    name="SIM",
    oms_type="NETTING",
    account_type="MARGIN",
    starting_balances=["100000 USD"],
    # Fill model: controls how orders match against historical bars
    fill_model="bar_adaptive",     # Realistic intra-bar high/low ordering
    # Book / liquidity:
    book_type="L2_MBP",            # Level 2 market-by-price
    # Latency:
    latency_model="no_latency",    # or custom latency distribution
    # Fees:
    maker_fee=0.0001,              # 1 bp
    taker_fee=0.0003,              # 3 bp
    # Slippage:
    slippage_model="gaussian",     # or "fixed", "no_slippage"
    # OTO (one-triggers-other) mode:
    oto_trigger_mode="partial",    # or "full"
)
```

### 2.6 ParquetDataCatalog — Read/Write

```python
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

catalog = ParquetDataCatalog(path="data/catalog")

# Write bars
catalog.write_data(bars)

# Query what's available
instruments = catalog.instruments()          # list[InstrumentId]
bar_types = catalog.bar_types()              # list[BarType]
date_range = catalog.bar_range(instrument_id, bar_type)

# Read bars
bars = catalog.bars(
    instrument_ids=["TSLA.NASDAQ"],
    bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
    start=pd.Timestamp("2024-01-01"),
    end=pd.Timestamp("2024-06-30"),
)
```

### 2.7 PortfolioAnalyzer — Auto-Stats (via run_analysis=True)

```python
# When BacktestEngineConfig.run_analysis=True, 
# BacktestEngine.run() auto-computes all 17 Nautilus statistics:
#   - Sharpe Ratio, Sortino Ratio
#   - Max Drawdown, Max Drawdown Duration
#   - Win Rate, Profit Factor, Expectancy
#   - CAGR, Volatility, Calmar Ratio
#   - Total P&L, Avg Win, Avg Loss
#   - etc.
# Results flow into BacktestResult.stats_returns and stats_pnls
```

---

## 3. Pre-Discovered Reference — Futu SDK for Bar Download

> **Source:** `futu-api` SDK (already installed, used in Phase 2 market data adapter)

```python
from futu import OpenQuoteContext, KLType, SubType, RET_OK

# Futu historical bar request
quote_ctx = OpenQuoteContext(host="sam-futu-opend", port=11111)
ret, data, page_req_key = quote_ctx.request_history_kline(
    code="US.TSLA",              # Futu instrument code format
    ktype=KLType.K_5M,           # 1M, 5M, 15M, 30M, 60M, DAY, WEEK, MONTH
    start="2024-01-01",
    end="2024-06-30",
    max_count=1000,              # Max bars per request
)

# data is a pandas DataFrame with columns:
#   time_key, open, close, high, low, volume, turnover

# Rate limit: Futu free tier = 30 requests/minute
# For N instruments × D days of 5-min bars: N × ceil(D*78/1000) requests
# 10 instruments × 250 trading days ≈ 2,000 requests ≈ 67 minutes
```

**Bar type mapping:**
| Futu `KLType` | Nautilus bar type string |
|---------------|--------------------------|
| `K_1M` | `{IID}-1-MINUTE-LAST-EXTERNAL` |
| `K_5M` | `{IID}-5-MINUTE-LAST-EXTERNAL` |
| `K_15M` | `{IID}-15-MINUTE-LAST-EXTERNAL` |
| `K_30M` | `{IID}-30-MINUTE-LAST-EXTERNAL` |
| `K_60M` | `{IID}-1-HOUR-LAST-EXTERNAL` |
| `K_DAY` | `{IID}-1-DAY-LAST-EXTERNAL` |

---

## 4. What Already Exists

| Component | Location | Current Use |
|-----------|----------|-------------|
| `BacktestEngine` | Nautilus (Rust/pyo3) | `bundle_validation.py` — smoke test, 20 synthetic flat bars |
| `BacktestNode` | Nautilus | Not used |
| `BacktestRunConfig` | Nautilus | Not used |
| `BacktestResult` | Nautilus | Not used |
| `ParquetDataCatalog` | `data/catalog/` on host volume | Exists but empty (no data pipeline) |
| `PortfolioAnalyzer` | `nautilus_trader.analysis` | Used offline in `PerformanceAnalyzer` service (Phase 8) |
| Futu OpenD connection | `sam-futu-opend` container | Phase 2 — live market data |

---

## 5. PostgreSQL Schema — `backtest_results`

```sql
CREATE TABLE backtest_results (
    id              SERIAL PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL UNIQUE,
    run_config_id   VARCHAR(64) NOT NULL,
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    bar_type        VARCHAR(64) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          VARCHAR(16) NOT NULL
                        CHECK (status IN ('running', 'completed', 'failed')),
    total_events    INTEGER,
    total_orders    INTEGER,
    total_positions INTEGER,
    elapsed_secs    NUMERIC(12, 3),
    stats_pnls      JSONB,
    stats_returns   JSONB,
    equity_curve    JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy_family VARCHAR(64),
    strategy_version VARCHAR(32),
    tags            JSONB
);

CREATE INDEX idx_bt_results_strategy ON backtest_results(strategy_id);
CREATE INDEX idx_bt_results_date ON backtest_results(start_date, end_date);
```

---

## 6. Parameter Sweep Pattern

```python
# Generate grid → one BacktestRunConfig per combination
from itertools import product

def _build_sweep_configs(
    base_config: BacktestRunConfig,
    param_grid: dict[str, list],
) -> list[BacktestRunConfig]:
    configs = []
    keys = list(param_grid.keys())
    for values in product(*param_grid.values()):
        combo = dict(zip(keys, values))
        # Deep-clone base_config, patch strategy config with combo params
        configs.append(patch_config(base_config, combo))
    return configs

# Run all configs via single BacktestNode
node = BacktestNode(configs=sweep_configs)
node.build()
node.run()

# Collect results → ranked comparison table
results = {
    run_config.id: node.get_engine(run_config.id).get_result()
    for run_config in sweep_configs
}
```

**Iteration limit note:** If `max_count` per Futu request is limited or sweep has many combinations, batch the workload. BacktestNode chunking is built-in for large grids.

---

## 7. Walk-Forward Pattern

```python
def walk_forward(
    base_config_template,
    train_days: int,
    test_days: int,
    sweep_grid: dict,
    data_start: str,
    data_end: str,
) -> list[dict]:
    """Rolling train/test windows. For each window:
       1. Sweep parameters on train period
       2. Select best params by Sharpe
       3. Run single backtest on test period with best params
       4. Record window results
    """
    windows = _generate_windows(data_start, data_end, train_days, test_days)
    results = []
    for train_start, train_end, test_start, test_end in windows:
        # Sweep on train
        train_configs = _build_sweep_configs(
            patch_dates(base_config_template, train_start, train_end),
            sweep_grid,
        )
        sweep_results = _run_sweep(train_configs)
        best_params = _select_best(sweep_results, metric="sharpe_ratio")

        # Test on out-of-sample
        test_config = patch_config(
            patch_dates(base_config_template, test_start, test_end),
            best_params,
        )
        test_result = _run_single(test_config)

        results.append({
            "train_window": (train_start, train_end),
            "test_window": (test_start, test_end),
            "best_params": best_params,
            "train_sharpe": sweep_results[best_params_key]["sharpe"],
            "test_sharpe": test_result.stats_returns.get("sharpe_ratio"),
            "test_pnl": test_result.stats_returns.get("total_pnl"),
        })
    return results
```

---

## 8. File Structure — New & Modified

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

docker/postgres/init/
└── 03_backtest_results.sql         # NEW — DDL

sam-services/
└── dashboard/
    └── backtest/                   # NEW — dashboard panels + API handlers

tests/
├── unit/services/
│   ├── test_bar_downloader.py
│   └── backtest/
│       ├── test_engine.py
│       ├── test_sweep.py
│       └── test_walk_forward.py
└── integration/
    └── test_backtest_e2e.py        # download → backtest → store → retrieve
```

---

## 9. Key Test Scenarios

| # | Test | What It Validates |
|---|------|-------------------|
| 1 | `BarDownloader.download(TSLA, 5-MINUTE, 30d)` | Futu request → Parquet write, rate limit respect, incremental update |
| 2 | `sam download-bars --instrument AAPL.NASDAQ` | CLI arg parsing, error on invalid instrument, progress output |
| 3 | `BacktestEngine wrapper with single bundle` | Correct BacktestRunConfig assembly, engine runs, result returned |
| 4 | `sam backtest tsla-orb-15m-futu --start 2024-01-01 --end 2024-03-31` | CLI → engine → result summary table, exit code 0 on success |
| 5 | `sam backtest --bundles config/bundles.yaml --start X --end Y` | Multi-bundle backtest, each strategy gets stats |
| 6 | `BacktestResult → PG storage → query by strategy_id` | JSONB stats round-trip, equity curve serialization |
| 7 | `sam backtest --sweep sl=5,10 --sweep tp=20,30` | 4 configs built, all run, ranked table output |
| 8 | `sam backtest --walk-forward --train 30d --test 10d` | Rolling windows correct, best params selected per window, stability report |
| 9 | `POST /api/backtest/run → GET status → GET result` | Async backtest lifecycle via dashboard API |
| 10 | `GET /api/backtest/compare?runs=id1,id2` | Side-by-side metric comparison + overlaid equity curves |

---

## 10. Phase D — Dashboard UX & Walk-Forward Integration (NEW)

> **Status:** Planned — 8 tickets  
> **Depends on:** Phase 12.1 A–C (complete)

### 10.1 Ticket Breakdown

| # | Ticket ID | Ticket | Type | Dependencies |
|---|-----------|--------|------|-------------|
| 12.1.13 | \`sam_trader-9z3.13.1.13\` | Strategy catalog API + dashboard \`<select>\` | task | — (root) |
| 12.1.14 | \`sam_trader-9z3.13.1.14\` | Empty catalog graceful error messaging | task | — (root, parallel) |
| 12.1.15 | \`sam_trader-9z3.13.1.15\` | Date pre-fill from catalog range | task | — (root, parallel) |
| 12.1.16 | \`sam_trader-9z3.13.1.16\` | Wire walk-forward to dashboard API | task | 12.1.13 |
| 12.1.17 | \`sam_trader-9z3.13.1.17\` | Wire parameter sweep to dashboard API | task | 12.1.16 |
| 12.1.18 | \`sam_trader-9z3.13.1.18\` | WF/Sweep result display panels | task | 12.1.17 |
| 12.1.19 | \`sam_trader-9z3.13.1.19\` | Fix \`_discover_bar_types\` bar type naming | bug | — (root, parallel) |
| 12.1.20 | \`sam_trader-9z3.13.1.20\` | [E2E] Download AAPL 3yr + validate backtest | task | 12.1.15, 12.1.18, 12.1.19 |

### 10.2 Build Order (Dependency Graph)

```
Track A (Usability):
  12.1.13 (strategy dropdown) ──► 12.1.16 (wire WF) ──► 12.1.17 (wire sweep) ──► 12.1.18 (panels)
                                                                                         │
Track B (Data UX):                                                                       │
  12.1.14 (empty catalog msg) ───────────────────────────────────────────────────────────┤
  12.1.15 (date pre-fill) ───────────────────────────────────────────────────────────────┤
                                                                                         │
Track C (Bug fix):                                                                       │
  12.1.19 (bar type naming) ─────────────────────────────────────────────────────────────┤
                                                                                         ▼
                                                                              12.1.20 (E2E AAPL)
```

### 10.3 Key Design Decisions

| # | Decision | Rationale |
|---|----------|----------|
| D1 | Strategy dropdown reads from \`bundles.yaml\` via \`load_bundles()\` | Single source of truth. No duplication. |
| D2 | Walk-forward sends \`train_days\`/\`test_days\` from UI to backend | User controls OOS duration. Defaults: 90/30. |
| D3 | Parameter sweep rows dynamically added/removed in UI | Flexible grid search without config files. |
| D4 | WF + Sweep combined mode supported | WalkForward.run() already accepts param_grid. Most powerful analysis mode. |
| D5 | AAPL 3yr as E2E validation standard | Liquid, well-known instrument. 3yr covers bull/bear/sideways regimes. |
| D6 | Date pre-fill from \`oldest_bar\`/\`newest_bar\` | Uses existing catalog status endpoint. User can still override. |

### 10.4 Files — New & Modified

| File | Change |
|------|--------|
| \`src/sam_trader/services/backtest/dashboard_api.py\` | New handlers: strategies list, WF routing, sweep routing |
| \`src/sam_trader/services/backtest/results.py\` | WF/sweep result persistence |
| \`src/sam_trader/services/dashboard.py\` | UI: dropdown, date pre-fill, WF/sweep panels, error messages |
| \`src/sam_trader/services/backtest/walk_forward.py\` | Wire async/thread for dashboard API |
| \`src/sam_trader/services/backtest/sweep.py\` | Wire async/thread for dashboard API |
| \`config/bundles.yaml\` | Add \`aapl-orb-5m-test\` bundle (12.1.20 only) |
| \`tests/unit/services/backtest/test_dashboard_api.py\` | New tests for strategies, bar types |

---

*Last updated: 2026-05-30 — Phase D planned (8 tickets), E2E AAPL validation added*
