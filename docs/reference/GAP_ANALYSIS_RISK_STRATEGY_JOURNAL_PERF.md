# Gap Analysis — Risk Management, Strategy Library, Trade Journals & Performance Analysis

> **Date:** 2026-05-23  
> **Status:** Phase 7 Complete — 8 of 12 phases built  
> **Audience:** Sam Dharma — review before Phase 8 start  
> **Purpose:** Holistic gap assessment across four pillars against the roadmap + NautilusTrader's native capabilities.

---

## Executive Summary

SAM Trader V3 has built a solid foundation (phases 0–7): dual-venue connectivity (Futu + IB), two strategies (ORB + Momentum), multi-venue bundle system, journal actors, rejection monitoring, and realized P&L tracking. The remaining phases (8–11) add operations, pipeline, safety, and deployment.

**However, the roadmap has significant blind spots in three of the four pillars this report examines.** The most critical gap is that **NautilusTrader's native `LiveRiskEngine` and `PortfolioAnalyzer` are entirely unused**, meaning we're building parallel (and less capable) infrastructure where production-grade solutions already exist.

---

## 1. Risk Management

### 1.1 What We Have (Implemented — Phases 0–7)

| Component | Location | Capability |
|-----------|----------|------------|
| In-strategy loss limit | `strategies/orb.py`, `momentum.py` | `_max_daily_loss_exceeded()` — simple accumulating loss counter reset on restart. Uses unrealized P&L logic. |
| In-strategy position limit | `strategies/orb.py`, `momentum.py` | `_position_allowed()` — static `max_position` cap per strategy. |
| Rejection circuit breaker | `actors/rejection_monitor.py` | Per-(instrument, strategy, reason) streak counting. Emits `StrategyHaltRequest` at threshold (default 3). 15-min cooldown. |
| Realized P&L tracking | `actors/realized_pnl.py` | FIFO lot matching. Per-strategy total persisted to Redis. Resets at 00:00 UTC. Pure realized — no unrealized noise. |
| Bundle risk config | `config/bundles.yaml` schema | `risk:` section with `max_position`, `max_daily_loss` per bundle. |

### 1.2 What's in the Remaining Roadmap (Phases 8–11)

| Phase | Ticket | Capability |
|-------|--------|------------|
| 9 | `9z3.10.21` | Monte Carlo position sizer — VaR-based, 10K simulations |
| 9 | `9z3.10.22` | Pre-trade risk checks — max exposure, daily loss, margin |
| 9 | `9z3.10.23` | Portfolio heat monitor — real-time heat tracking |
| 10 | `9z3.11.1` | Kill switch + 5 circuit breakers: DAILY_PNL, MARGIN_LIMIT, CONNECTIVITY_LOSS, REJECTION_STREAK, REALIZED_LOSS_LIMIT |

### 1.3 What NautilusTrader Provides Natively (UNUSED)

NautilusTrader ships with a production-grade **`LiveRiskEngine`** that acts as a **pre-trade filter** on every order before it reaches the execution client. It is configured via `LiveRiskEngineConfig` (a `RiskEngineConfig` subclass):

```python
from nautilus_trader.live.config import LiveRiskEngineConfig

RiskEngineConfig(
    bypass: bool = False,                         # Skip all checks
    max_order_submit_rate: str = "100/00:00:01",  # 100 orders per second
    max_order_modify_rate: str = "100/00:00:01",  # 100 mods per second
    max_notional_per_order: dict[str, int] = {},   # e.g., {"USD": 100000}
)
```

**Key capabilities we are NOT using:**
- **Order rate limiting** — prevents runaway strategy loops from flooding the broker
- **Per-order notional limits** — caps any single order's notional value by currency
- **`trading_state`** — built-in HALTED/RUNNING state machine that the engine enforces
- **`bypass`** — emergency override (equivalent to a master kill switch, but at the engine level)

The `LiveRiskEngine` sits in the order pipeline between strategy and execution. An order that fails a risk check is **rejected before it ever leaves the platform**. This is the correct place for circuit-breaker logic — not in a separate actor.

**Architecture gap:** We currently wire NO `LiveRiskEngineConfig` in `main.py`. The `TradingNode` accepts a `LiveRiskEngineConfig` parameter. If we don't pass one, the default (which has no notional limits set) is used — but at minimum we should be configuring it.

### 1.4 What's Missing (Gap Assessment)

| Gap | Severity | Notes |
|-----|----------|-------|
| **`LiveRiskEngine` integration** | 🔴 HIGH | The pre-trade risk filter is the correct home for circuit breakers, kill switches, and notional limits. Our Phase 10 safety controls should integrate with it, not sit beside it. |
| **Order rate limiting** | 🔴 HIGH | No protection against strategy bugs firing hundreds of orders. Nautilus has this built-in. |
| **Per-order notional limits** | 🟡 MEDIUM | Especially important for small accounts or high-priced instruments (e.g., one BRK.A order = $700K). |
| **Dynamic position sizing** (volatility-based) | 🟡 MEDIUM | Both strategies use static `trade_size`. OrbStrategy already computes ATR — it could use it for Kelly/vol-target sizing. |
| **Correlation risk** | 🟡 MEDIUM | No visibility into whether two strategies are both long the same sector. Multi-strategy portfolio needs correlation awareness. |
| **Gap risk / overnight risk** | 🟡 MEDIUM | No mechanism to reduce position size before market close or avoid holding through earnings. |
| **Margin / buying power check** | 🟢 LOW | Phase 9 adds pre-trade margin check. Broker-level margin is the authoritative source. |
| **Per-venue exposure caps** | 🟢 LOW | Phase 9 adds max exposure per venue. |
| **Slippage guard** | 🟢 LOW | No configurable max slippage on market orders. Market orders can get terrible fills in fast markets. |
| **Max drawdown stop** (multi-day) | 🟢 LOW | We only have `max_daily_loss`. No mechanism to stop trading after a -10% account drawdown over multiple days. |

### 1.5 Recommendations — Risk Management

**Immediate (can do during Phase 8):**

1. **Wire `LiveRiskEngineConfig` in `main.py`** — add `max_order_submit_rate`, `max_notional_per_order` from `SamTraderConfig` env vars. This is ~20 lines of code and provides instant protection against order flooding.

2. **Integrate Phase 10's SafetyController with LiveRiskEngine** — the kill switch should call `risk_engine.set_trading_state(HALTED)` rather than implementing its own state. The circuit breaker checks should feed into the engine's pre-trade filter.

**Short-term (Phase 9–10 refinements):**

3. **Add ATR-based dynamic position sizing to strategies** — `trade_size` should be `max(1, floor(capital_pct * account_value / (ATR * multiplier)))`. OrbStrategy already has ATR cached.

4. **Add max slippage configuration** — `max_slippage_ticks` on market/stop orders, enforced before submission.

5. **Add daily drawdown limit (multi-day)** — track peak-to-trough drawdown across days in Redis. Halt all trading if -X% from peak equity.

**Future (post-v3):**

6. Portfolio-level VaR and correlation matrix
7. Kelly Criterion / optimal-f position sizing
8. Black-swan hedging (tail risk)

---

## 2. Strategy Library

### 2.1 What We Have (Implemented — Phases 0–7)

| Strategy | File | Key Features |
|----------|------|-------------|
| **OrbStrategy** | `strategies/orb.py` | Opening Range Breakout. Configurable candles, confirmation bars, ATR range quality filter, 3 entry types (MARKET/LIMIT/STOP_MARKET), bracket orders, session time guards, state persistence. |
| **MomentumStrategy** | `strategies/momentum.py` | Momentum at open. Configurable window, session window, allowed directions filter, 3 entry types, bracket orders, state persistence. |
| **TestEchoStrategy** | `strategies/test_echo.py` | Echo test strategy for integration testing. |
| **Template** | `strategies/_template.py` | Copy-paste starter with all hooks documented. |

Both strategies share patterns: bracket orders, venue-aware IB `post_only=False`, SL/TP in ticks, venue/bundle_id injection.

### 2.2 What's in the Remaining Roadmap

| Phase | Ticket | Capability |
|-------|--------|------------|
| 9 | `9z3.10.18` | Gap scanner — pre-market gap candidates |
| 9 | `9z3.10.20` | AI scoring engine — LLM grades candidates |
| 9 | `9z3.10.19` | Market regime detection — HMM (trending/ranging/volatile) → parameter adaptation |
| 9 | `9z3.10.25` | Bundle YAML generator — AI → bundles |

**Note:** No new strategy types are planned in phases 8–11. The Phase 9 pipeline generates bundles for *existing* strategies (ORB + Momentum), not new strategy types.

### 2.3 What NautilusTrader Provides Natively

- `Strategy` base class with full lifecycle: `on_start`, `on_stop`, `on_reset`, `on_save`, `on_load`, `on_dispose`
- `StrategyConfig` frozen dataclass — used correctly in our strategies
- `ImportableStrategyConfig` + `StrategyFactory` — hot-loading strategies from dotted paths
- Order factory: `bracket()`, `oco()`, `limit()`, `market()`, `stop_market()`, `stop_limit()`, `trailing_stop()`
- Portfolio + Cache + Clock access from any strategy
- `subscribe_bars()`, `subscribe_quote_ticks()`, `subscribe_trade_ticks()`, `subscribe_order_book()`

### 2.4 What's Missing (Gap Assessment)

| Gap | Severity | Notes |
|-----|----------|-------|
| **MeanReversionStrategy** | 🟡 MEDIUM | Listed in the target directory structure (§5 of Plan) but never ticket-ized. The plan says "3 strategies" but only 2 were built + 1 template. |
| **HK market specialization** | 🟡 MEDIUM | Our strategies are US-market-centric (pre-market gaps, ET session times). HK market opens differently (no gap-up mechanics, different volatility profile, no pre-market in same way). |
| **Multi-timeframe strategies** | 🟡 MEDIUM | No strategy can watch a higher timeframe for trend and a lower timeframe for entry. |
| **Volume-based strategies** | 🟢 LOW | No VWAP, volume profile, or accumulation/distribution strategies. |
| **Mean Reversion (pairs/stat arb)** | 🟢 LOW | No pairs trading or basket trading capability. |
| **News/event-driven** | 🟢 LOW | Phase 9's AI scoring could incorporate news — but no dedicated news-based strategy exists. |
| **Strategy warm-up** | 🟢 LOW | Strategies start cold. No mechanism to pre-load historical bars to compute indicators before first signal. MomentumStrategy needs `window+1` bars before first signal — currently just waits. |
| **Parameter optimization framework** | 🟢 LOW | No walk-forward optimization, grid search, or parameter stability analysis. |

### 2.5 Recommendations — Strategy Library

**Immediate (can do during Phase 8):**

1. **Build MeanReversionStrategy** — fill the gap promised in the plan's directory structure. Simple Bollinger Band or RSI-based mean reversion. Should share bracket-order patterns with ORB/Momentum.

2. **Add strategy warm-up configuration** — allow strategies to request N historical bars at startup for indicator priming. NautilusTrader supports historical data feeding before live starts.

**Short-term (Phase 9 integration):**

3. **HK market strategy variant** — adapt ORB for HK market: no pre-market gap (use prior day close + first N minutes of regular session). HK stocks are more news-driven and have different intraday patterns.

4. **Composite strategy** — a meta-strategy that combines regime detection output with entry strategy (e.g., "only trade ORB breakouts if regime is TRENDING").

**Future (post-v3):**

5. Strategy marketplace / versioned strategy registry
6. Automated parameter optimization pipeline
7. Strategy ensemble / meta-labeling (use one strategy to filter another's signals)

---

## 3. Trade Journals

### 3.1 What We Have (Implemented — Phases 0–7)

| Component | Location | Capability |
|-----------|----------|------------|
| **TradeJournalActor** | `actors/trade_journal.py` | Subscribes to `OrderFilled` events. Persists fills + orders to PostgreSQL via asyncpg connection pool. Upsert pattern for orders, insert for fills. Venue column. |
| **PG Schema** | `docker/postgres/init/01_schema.sql` | 3 tables: `orders` (id, client_order_id, venue, side, type, qty, price, status, timestamps), `fills` (trade_id, FK→orders, venue, trd_market, side, qty, price, commission, currency, timestamps), `positions` (strategy_id, instrument_id, venue, net_qty, avg_px, unrealized_pnl, realized_pnl, timestamps) |
| **Indexes** | Schema | `idx_fills_ts_event`, `idx_fills_instrument`, `idx_fills_venue`, `idx_fills_strategy`, `idx_orders_instrument`, `idx_orders_venue`, `idx_orders_strategy`, `idx_positions_instrument`, `idx_positions_venue`, `idx_positions_strategy` |

### 3.2 What's in the Remaining Roadmap

| Phase | Ticket | Capability |
|-------|--------|------------|
| 10 | `9z3.11.2` | Dashboard database — new tables: `portfolio_snapshots`, `pipeline_runs`, `alert_log` |
| 10 | `9z3.11.3` | FastAPI endpoints: `/api/positions`, `/api/fills`, `/api/scans/latest`, `/api/alerts` |
| 10 | `9z3.11.4` | Static HTML dashboard — positions table, fills table |

### 3.3 What NautilusTrader Provides Natively

- Event-driven architecture — all `OrderFilled`, `OrderRejected`, `PositionChanged` events flow on the message bus
- `Cache` facade — in-memory order/position/strategy cache accessible to any actor
- Redis-backed `CacheConfig` for state persistence across restarts
- `OrderFilled` events carry: `trade_id`, `client_order_id`, `venue_order_id`, `strategy_id`, `instrument_id`, `last_qty`, `last_px`, `commission`, `currency`, `ts_event` (ns precision), `ts_init`
- **No built-in PostgreSQL journaling** — our `TradeJournalActor` fills a real gap

### 3.4 What's Missing (Gap Assessment)

| Gap | Severity | Notes |
|-----|----------|-------|
| **Position snapshot writer** | 🔴 HIGH | The PG `positions` table exists but **nothing writes to it**. There's no `PositionSnapshotActor` to periodically snapshot positions. The schema is orphaned. |
| **Execution quality metrics** | 🟡 MEDIUM | No tracking of slippage (fill price vs signal price), fill ratio (filled qty / ordered qty), or latency (ts_init - ts_event). These are critical for broker evaluation. |
| **Trade tags / annotations** | 🟡 MEDIUM | Can't tag trades with strategy version, market regime, or manual review notes. Makes post-mortem analysis harder. |
| **Daily P&L summary** | 🟡 MEDIUM | No automated daily summary: total P&L, win rate, top winners/losers, strategy breakdown. Must be queried manually. |
| **Config audit log** | 🟢 LOW | When `bundles.yaml` changes are deployed, no record in the DB of what changed, when, and by whom. |
| **Broker reconciliation** | 🟢 LOW | No automated comparison of our fills table vs broker's trade confirmations to detect missing or duplicate fills. |
| **Tax-lot tracking** | 🟢 LOW | Simple average price for cost basis. No FIFO/LIFO/HIFO lot tracking for tax optimization. |
| **Order update tracking** | 🟢 LOW | The `orders` table uses upsert but `ts_updated` is always set to `ts_submitted`. Order modifications (price changes, qty changes, cancels) are not tracked as separate events. |

### 3.5 Recommendations — Trade Journals

**Immediate (can do during Phase 8):**

1. **Build `PositionSnapshotActor`** — subscribe to `PositionChanged` events (or poll every 60s) and upsert into the PG `positions` table. This makes the dashboard useful. ~40 lines of code.

2. **Add slippage tracking to TradeJournalActor** — for each fill, record `slippage = fill_price - signal_price` (signal price available from the order or strategy). Add `slippage` column to fills table.

**Short-term (Phase 9–10):**

3. **Add `OrderUpdated` event handling** — track order modifications in a separate `order_updates` table.

4. **Daily P&L summary table** — materialized summary updated at session close: `daily_pnl` table with strategy_id, date, realized_pnl, unrealized_pnl, trades, winners, losers, total_commission.

5. **Trade tags** — add `tags: jsonb` column to fills table for extensible metadata.

**Future (post-v3):**

6. Automated broker reconciliation
7. Tax-lot optimization
8. Custom reporting engine

---

## 4. Performance Analysis

### 4.1 What We Have (Implemented — Phases 0–7)

| Component | Location | Capability |
|-----------|----------|------------|
| **RealizedPnLTrackerActor** | `actors/realized_pnl.py` | FIFO lot matching. Per-strategy realized P&L in Redis. Day-reset at 00:00 UTC. Pure realized. |
| **PostgreSQL fills** | PG schema | Raw data for manual SQL analysis. |
| **Strategy-level loss** | `orb.py`, `momentum.py` | `_daily_loss` accumulation for in-strategy decisions only. |

### 4.2 What's in the Remaining Roadmap

| Phase | Ticket | Capability |
|-------|--------|------------|
| 9 | `9z3.10.19` | Market regime detection (HMM) — performance context, not analysis |
| 9 | `9z3.10.26` | Readiness report — daily pre-market summary, not post-trade analysis |
| 10 | `9z3.11.3` | FastAPI `/api/positions`, `/api/fills` — raw data endpoints |
| 10 | `9z3.11.4` | Static HTML dashboard — positions + fills tables |

### 4.3 What NautilusTrader Provides Natively (ENTIRELY UNUSED)

**This is the single biggest gap in the entire v3 roadmap.** NautilusTrader ships a world-class performance analysis stack via the `nautilus_trader.analysis` package:

#### PortfolioAnalyzer
```python
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer

analyzer = PortfolioAnalyzer()
analyzer.add_trade(trade)           # Feed OrderFilled events
analyzer.add_return(return_value)   # Or feed returns directly
analyzer.calculate_statistics()     # Compute all registered stats

# Access individual stats:
stats = analyzer.get_performance_stats_general()
# → CAGR, SharpeRatio, SortinoRatio, MaxDrawdown, WinRate,
#   ProfitFactor, Expectancy, AvgWinner, AvgLoser, LongRatio,
#   ReturnsVolatility, RiskReturnRatio, CalmarRatio

pnls = analyzer.get_performance_stats_pnls()
returns = analyzer.get_performance_stats_returns()
pos_returns = analyzer.get_performance_stats_position_returns()
```

#### Built-in Statistics (Rust-backed via Python bindings)
| Statistic | What It Measures |
|-----------|-----------------|
| `CAGR` | Compound Annual Growth Rate |
| `SharpeRatio` | Risk-adjusted return (vs risk-free rate) |
| `SortinoRatio` | Risk-adjusted return (downside deviation only) |
| `MaxDrawdown` | Maximum peak-to-trough decline |
| `CalmarRatio` | CAGR / MaxDrawdown |
| `WinRate` | Percentage of winning trades |
| `ProfitFactor` | Gross profit / Gross loss |
| `Expectancy` | Average profit per trade (risk-weighted) |
| `ReturnsAverage` | Mean periodic return |
| `ReturnsVolatility` | Standard deviation of periodic returns |
| `RiskReturnRatio` | Return / Volatility |
| `AvgWinner` / `AvgLoser` | Average winning / losing trade |
| `MaxWinner` / `MaxLoser` | Best / worst single trade |
| `MinWinner` / `MinLoser` | Smallest win / loss |
| `LongRatio` | Percentage of long vs short trades |

#### Tear Sheets (Visual Analytics)
- `create_equity_curve()` — equity growth over time
- `create_drawdown_chart()` — underwater plot
- `create_monthly_returns_heatmap()` — calendar heatmap
- `create_yearly_returns()` — annual bar chart
- `create_returns_distribution()` — histogram of returns
- `create_rolling_sharpe()` — rolling Sharpe ratio over time
- `create_tearsheet()` / `create_tearsheet_from_stats()` — full multi-chart report
- `TearsheetConfig` — customizable layout, colors, sections

#### Custom Statistic Registration
```python
class MyCustomStat(PortfolioStatistic):
    name = "My Custom Metric"
    def calculate_from_realized_pnls(self, pnls: list[Money]) -> ...:
        ...

analyzer.register_statistic(MyCustomStat())
```

### 4.4 What's Missing (Gap Assessment)

| Gap | Severity | Notes |
|-----|----------|-------|
| **`PortfolioAnalyzer` integration** | 🔴 CRITICAL | We have NO performance analysis beyond simple realized P&L tracked in Redis. NautilusTrader has a **complete** analytics stack — all Rust-backed, all battle-tested — and we're not using any of it. |
| **Rolling performance windows** | 🔴 HIGH | No 7-day, 30-day, 90-day rolling Sharpe/Sortino/drawdown. Essential for detecting strategy degradation. |
| **Trade-level MAE/MFE analysis** | 🟡 MEDIUM | Maximum Adverse Excursion (how far against you before winning) and Maximum Favorable Excursion (how far in your favor before losing) are the best diagnostics for stop-loss and take-profit tuning. |
| **Strategy comparison / ranking** | 🟡 MEDIUM | No way to rank strategies by Sharpe, drawdown, win rate, or P&L across time periods. |
| **Benchmark comparison** | 🟢 LOW | No comparison against buy-and-hold SPY, QQQ, or sector benchmarks. |
| **Time-of-day P&L heatmap** | 🟢 LOW | No analysis of which hours are most profitable. Critical for intraday strategies. |
| **Parameter sensitivity analysis** | 🟢 LOW | How does P&L change as stop_loss_ticks varies from 5 to 20? No tooling for this. |
| **Capital allocation efficiency** | 🟢 LOW | If running 10 strategies with limited capital, how should capital be allocated? Kelly? Equal weight? Risk parity? |

### 4.5 Recommendations — Performance Analysis

**Immediate (can do during Phase 8):**

1. **Wire `PortfolioAnalyzer` in the sam-services container** — create a `PerformanceAnalyzer` service that:
   - Queries PostgreSQL fills from `TradeJournalActor`
   - Feeds them into NautilusTrader's `PortfolioAnalyzer`
   - Runs `calculate_statistics()` nightly
   - Stores results in a new PG `performance_stats` table (date, strategy_id, stat_name, stat_value)
   - Provides a `sam performance` CLI command
   - This is **~150 lines of code** and unlocks the entire NautilusTrader analysis stack

2. **Add performance endpoints to dashboard** — `/api/performance`, `/api/performance/{strategy_id}` serving the stats from the `performance_stats` table.

**Short-term (Phase 10):**

3. **Add tearsheet generation** — `sam report` command that generates an HTML tearsheet using NautilusTrader's `create_tearsheet()` and saves it to the dashboard directory.

4. **Add MAE/MFE tracking** — a lightweight actor that records per-trade MAE/MFE to PostgreSQL (add `mae`, `mfe` columns to fills table or a new `trade_analytics` table).

5. **Rolling performance window views** — PG materialized views: `performance_rolling_7d`, `performance_rolling_30d`, `performance_rolling_90d`.

**Future (post-v3):**

6. Walk-forward optimization framework
7. Strategy allocation optimization (Kelly, risk-parity)
8. Automated strategy retirement (degrade → disable → archive pipeline)

---

## 5. Cross-Cutting Recommendations

### 5.1 Phase 8 Should Include These Quick Wins

| # | Win | Effort | Impact |
|---|-----|--------|--------|
| 1 | Wire `LiveRiskEngineConfig` in `main.py` | 15 min | Prevents order flooding, adds notional limits |
| 2 | Build `PositionSnapshotActor` | 30 min | Makes PG positions table functional |
| 3 | Build `PerformanceAnalyzer` service (Nautilus `PortfolioAnalyzer` wrapper) | 2 hrs | Unlocks entire analytics stack |
| 4 | Add `slippage` column to fills + track in TradeJournalActor | 30 min | Execution quality tracking |
| 5 | Add `sam performance` CLI command | 30 min | Human-accessible performance data |

**Total: ~4 hours of work that dramatically improves the system.**

### 5.2 Roadmap Amendments

The following should be added to the roadmap (new tickets or scope expansion):

| Priority | Area | Suggestion |
|----------|------|------------|
| P0 | Risk | `LiveRiskEngine` integration — config wiring + env vars in `SamTraderConfig` |
| P0 | Journal | `PositionSnapshotActor` — make the orphaned PG table functional |
| P0 | Performance | `PerformanceAnalyzer` service — wrap Nautilus `PortfolioAnalyzer` |
| P1 | Strategy | `MeanReversionStrategy` — fill the gap promised in the directory structure |
| P1 | Strategy | Strategy warm-up / bar priming configuration |
| P1 | Performance | Rolling performance windows + dashboard integration |
| P1 | Journal | Slippage tracking in fills |
| P2 | Risk | Dynamic position sizing (ATR/Kelly-based) |
| P2 | Performance | MAE/MFE tracking |
| P2 | Strategy | HK-market strategy variant |
| P2 | Journal | Trade tags (jsonb column) |
| P3 | Risk | Multi-day drawdown halt |
| P3 | Performance | Tear sheet auto-generation |
| P3 | Journal | Config audit log |

### 5.3 Architecture Note: Where Analysis Belongs

```
┌─────────────────────────────────────────────────────────────────┐
│  sam-services container                                          │
│                                                                  │
│  ┌──────────────────┐   ┌───────────────────────────────────┐  │
│  │ PerformanceAnalyzer│   │ Dashboard (FastAPI + static HTML) │  │
│  │                     │   │                                    │  │
│  │ 1. Query PG fills  │   │ GET /api/performance               │  │
│  │ 2. Feed to Nautilus│──►│ GET /api/performance/{strategy}     │  │
│  │    PortfolioAnalyzer│  │ GET /health                        │  │
│  │ 3. Store stats in PG│  │ GET /api/fills                     │  │
│  │ 4. Generate tearsheet│ │ GET /api/positions                  │  │
│  └──────────────────┘   └───────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────┐   ┌──────────────────────────────────────┐   │
│  │ CLI: sam perf │   │ Cron: nightly stats + weekly tearsheet │   │
│  └──────────────┘   └──────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  sam-trader container (runtime)                                  │
│                                                                  │
│  ┌──────────────────┐   ┌───────────────────────────────────┐  │
│  │ LiveRiskEngine    │   │ Actors (write to PG/Redis)        │  │
│  │  - rate limiting  │   │  - TradeJournalActor → PG fills   │  │
│  │  - notional limits│   │  - RealizedPnLTracker → Redis     │  │
│  │  - trading state  │   │  - RejectionMonitorActor          │  │
│  │  - pre-trade check│   │  - PositionSnapshotActor (NEW)    │  │
│  └──────────────────┘   └───────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Key principle:** Performance analysis is NOT a hot-path concern. It runs on sampled/post-hoc data in sam-services. Risk management IS a hot-path concern and must run pre-trade in sam-trader via `LiveRiskEngine`.

---

## 6. Summary Table

| Pillar | Implemented (P0–P7) | Planned (P8–P11) | Nautilus Native (Unused) | Gaps |
|--------|---------------------|------------------|--------------------------|------|
| **Risk Mgmt** | Strategy-level loss/position limits, RejectionMonitor, RealizedPnL tracking | MC sizer, pre-trade checks, heat monitor, kill switch, 5 circuit breakers | `LiveRiskEngine` (rate limits, notional limits, trading state, pre-trade filter) | 🔴 LiveRiskEngine unwired, no rate limiting, no dynamic sizing |
| **Strategy Library** | ORB, Momentum, Echo, Template | Gap scanner, AI scoring, regime detection (no new strat types) | Strategy base class, order factory | 🟡 MeanReversion missing, no HK specialization, no warm-up, no multi-TF |
| **Trade Journals** | PG fills+orders tables, TradeJournalActor, proper indexes | Dashboard DB tables, API endpoints | Event-driven fill events | 🔴 Position snapshot not written, no slippage tracking, no trade tags |
| **Performance Analysis** | Simple realized P&L in Redis | Dashboard (raw data display only) | `PortfolioAnalyzer` (17 built-in stats), tear sheets, custom stats | 🔴 CRITICAL: entire Nautilus analytics stack unused, no Sharpe/Sortino/drawdown, no rolling windows |

---

*End of gap analysis. Recommendations should be reviewed and prioritized before Phase 8 begins.*
