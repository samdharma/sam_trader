# Regression Checklist — Phase 6 & Phase 8 Cross-Phase Validation

> **Date:** 2026-05-24 (Gap audit — Phases 0–8 confirmed complete)  
> **Purpose:** Comprehensive smoke-test checklist for all phases affected by Phase 6 actor wiring fix and Phase 8 Nautilus-native integrations.  
> **Trigger:** Phase 6 EXIT validation revealed 5 actors were not wired into `main.py`. Wiring fix + Phase 8 additions touch config, bootstrap, all order flows, and PostgreSQL schema.  
> **Audience:** AI agents and human operators performing regression validation.  
> **Context:** Gap audit confirmed all Phase 0–8 implementations complete. Phases 4-5 items marked VERIFIED per code review.

---

## What Changed (Root Causes)

| Change | File(s) | Impact Radius |
|--------|---------|---------------|
| **5 actors wired into `main.py`** (TradeJournal, HealthMonitor, BarResub, RejectionMonitor, RealizedPnl) | `main.py` | TradingNode bootstrap, all order fills, all strategies, all venues |
| **2 new config fields** (`actor_rejection_monitor_enabled`, `actor_realized_pnl_enabled`) | `config.py` | `SamTraderConfig.from_env()`, manual config construction |
| **Instrument IDs extracted from bundles before actor config** | `main.py` | Bundle loading must succeed before actors register |
| **`actors` + `risk_engine` params on `TradingNodeConfig`** | `main.py` | TradingNode lifecycle, LiveRiskEngine pre-trade filtering |
| **Slippage column on fills** | `trade_journal.py`, `01_schema.sql` | PG schema, TradeJournalActor writes, PerformanceAnalyzer reads |
| **`performance_stats` table** | `01_schema.sql`, `performance_analyzer.py` | PG schema, nightly cron, `sam performance` CLI |
| **12 CLI commands** | `cli.py` | `sam` entry point, deploy.sh is now ops-free |
| **Cron: performance entry @02:00 HKT** | `crontab` | Cron daemon in sam-services |

---

## Phase 0 — Docker Stack

- [ ] **0.1** `docker-compose.yml` — sam-trader starts with all env vars from `.env.example`. New `ACTOR_REJECTION_MONITOR_ENABLED`, `ACTOR_REALIZED_PNL_ENABLED`, `ACTOR_POSITION_SNAPSHOT_ENABLED`, `RISK_*` vars must pass through.
- [ ] **0.2** `Dockerfile` — sam-trader image builds. Verify `asyncpg`, `redis` already present (no new deps added, but confirm).
- [ ] **0.3** `entrypoint.sh` — waits for PG, Redis, Futu, IB before starting. Actors connect to PG/Redis on startup. If PG not ready → actor pool creation fails silently (log check).
- [ ] **0.4** `Dockerfile.services` — sam-services image builds. Verify `performance_analyzer` module importable inside container.

## Phase 1 — Configuration & Bootstrap

- [ ] **1.1** `SamTraderConfig.from_env()` — all 6 actor flags + 4 risk flags parse correctly. `ACTOR_REJECTION_MONITOR_ENABLED=true` → `True`. Empty string → `False`.
- [ ] **1.2** `build_trading_node()` — returns TradingNode with **empty bundles** (`bundles: []`). `load_bundles` returns empty → `instrument_ids=[]` → actors get empty IDs. Should NOT crash.
- [ ] **1.3** `build_trading_node()` — both `FUTU_ENABLED=false` and `IB_ENABLED=false`. All data/exec factories `None`. Actors still register (they don't depend on broker).
- [ ] **1.4** `build_trading_node()` — `risk_engine=` always populated. Even default config → LiveRiskEngineConfig passed with rate limits. Verify `node._config.risk_engine` is not None.
- [ ] **1.5** `main()` entrypoint — `node.run()` starts without import errors. All 6 actor dotted paths resolve: `sam_trader.actors.trade_journal:TradeJournalActor` etc.
- [ ] **1.6** `.env.example` completeness — verify `ACTOR_REJECTION_MONITOR_ENABLED`, `ACTOR_REALIZED_PNL_ENABLED`, `ACTOR_POSITION_SNAPSHOT_ENABLED`, `RISK_MAX_ORDER_SUBMIT_RATE`, `RISK_MAX_ORDER_MODIFY_RATE`, `RISK_MAX_NOTIONAL_PER_ORDER`, `RISK_BYPASS` are all present.

## Phase 2 — Futu Market Data

- [ ] **2.1** `FutuLiveDataClient` starts with actors registered. Actors consume message bus events. Verify no topic collisions or message flooding.
- [ ] **2.2** Bar subscription → bar data flows to strategies AND `BarResubscriptionActor`. Verify actor doesn't interfere with strategy bar consumption.
- [ ] **2.3** Quote subscription → `PositionSnapshotActor` can read `self.cache.quote_tick()` for unrealized PnL computation. Verify cache populated for subscribed instruments.

## Phase 3 — Futu Execution

- [ ] **3.1** Order submit → `OrderAccepted` → `OrderFilled` → TradeJournalActor writes `slippage` to PG. Verify `slippage` NOT NULL for limit orders, computed correctly.
- [ ] **3.2** Order reject → `OrderRejected` → RejectionMonitorActor increments counter. Verify actor sees event on message bus.
- [ ] **3.3** Order fill → RealizedPnLTrackerActor computes FIFO P&L → Redis. Verify `sam:pnl:{strategy_id}:{date}` key exists after fill.
- [ ] **3.4** `LiveRiskEngine` rate limits — submit >100 orders/sec → verify some rejected pre-broker.
- [ ] **3.5** `LiveRiskEngine` notional limits — set `RISK_MAX_NOTIONAL_PER_ORDER={"USD":1000}` → order >$1K notional rejected.
- [ ] **3.6** `RISK_BYPASS=1` disables all checks → all orders pass through.

## Phase 4 — Futu Integration ✅ VERIFIED (all 6 tickets closed)

- [x] **4.1** `FutuLiveDataClientFactory` + `FutuLiveExecClientFactory` register in TradingNode alongside actors. Verify no registration-order dependency.
- [x] **4.2** `FutuInstrumentProvider` loads instruments → `instrument_ids` from bundles match provider output. Actor `instrument_ids` come from same bundles.
- [x] **4.3** Bundle `venue: FUTU` → fills flow to TradeJournalActor with `venue="FUTU"` in PG. Verify venue column populated correctly.

## Phase 5 — IBKR Adapter ✅ VERIFIED (all 14 tickets closed)

- [x] **5.1** Same as 3.1–3.6 but for IB venue. Dual-venue fills should both journal with correct `venue` column.
- [x] **5.2** IB pre-flight permissions check + RejectionMonitorActor. Permission-based rejections should NOT trigger rejection circuit breaker.
- [x] **5.3** IB `post_only=False` bracket orders → fills journaled with correct slippage.

## Phase 6 — Actors & State Management

- [ ] **6.1** TradeJournalActor writes `slippage` column — NOT NULL for limit orders. Verify `_write_fill` SQL includes `slippage` param.
- [ ] **6.2** RejectionMonitorActor wired — `StrategyHaltRequest` emitted on bus after 3 identical rejections.
- [ ] **6.3** RealizedPnLTrackerActor wired — P&L in Redis after fill. Redis key format `sam:pnl:{strategy_id}:{date}`.
- [ ] **6.4** HealthMonitorActor wired — heartbeat logs contain venue status.
- [ ] **6.5** BarResubscriptionActor wired — bar types auto-discovered from strategy configs.
- [ ] **6.6** State persistence via Redis — `on_save`/`on_load` called on all actors + strategies.
- [ ] **6.7** PG schema has `slippage` column + `performance_stats` table. Run `test_postgres_schema.py`.
- [ ] **6.8** Actors disable via env vars — set `ACTOR_JOURNAL_ENABLED=false` → TradeJournalActor not in `node._config.actors`.

## Phase 7 — Strategy Library & Bundle System

- [ ] **7.1** Bundle loading — `load_bundles()` called once, result used for both `instrument_ids` and `strategies`. Verify no double-call or mutation.
- [ ] **7.2** OrbStrategy order → fill → actors: bar → breakout → bracket → fill → TradeJournal writes PG with slippage → RealizedPnl computes FIFO → RejectionMonitor watching.
- [ ] **7.3** MomentumStrategy same chain as 7.2.
- [ ] **7.4** `sam validate-bundles` still works — backtest gate creates BacktestEngine. Verify no actor interference.
- [ ] **7.5** Bundle `risk:` (max_position, max_daily_loss) enforced in strategy AND LiveRiskEngine pre-trade layer. Both layers active, not conflicting.
- [ ] **7.6** Bundle `bracket:` (stop_loss_ticks, take_profit_ticks) applied. SL/TP fills have correct slippage from limit price.

## Phase 8 — Cross-Service Integration

- [ ] **8.1** `sam status` — shows all sam-* containers healthy.
- [ ] **8.2** `sam health` — PG, Redis, Futu, Nautilus all return UP.
- [ ] **8.3** `sam performance` — reads `performance_stats`. Graceful "no data" when table empty.
- [ ] **8.4** `sam quote TSLA.NASDAQ` — cache hit fast, cache miss → broker fallback.
- [ ] **8.5** `sam backup` / `sam restore` — PG dump includes `performance_stats` + `slippage` column.
- [ ] **8.6** `sam deploy` / `sam rollback` cycle — after restart: all actors re-register, state reloaded, fills resume.
- [ ] **8.7** Cron: `performance_analyzer.py` @02:00 HKT runs successfully. Check `logs/performance.log`.
- [ ] **8.8** Cron: backup @06:00 HKT includes new PG tables.
- [ ] **8.9** Deploy decoupling — `deploy.sh` has no ops commands. `sam status` shows containers after `./deploy.sh --with-futu start`.

---

## Quick-Run Validation Script

```bash
# Run all phase-6 and phase-8 related tests (skip state_persists — Python 3.14 BacktestEngine limitation)
python3 -m pytest \
  tests/unit/test_config.py \
  tests/unit/test_main.py \
  tests/unit/actors/ \
  tests/unit/services/ \
  tests/integration/test_phase6_exit.py \
  tests/integration/test_phase8_exit.py \
  -q --tb=short -k "not state_persists"

# Expected: ~150 tests, all pass
```

## Risk Summary

| Risk | Count | Areas |
|------|-------|-------|
| 🔴 HIGH — direct code path change | 5 | 1.2 (empty bundles), 1.5 (actor import paths), 3.1 (fill→journal→slippage), 6.1 (slippage write), 7.1 (bundle loading + instrument extraction) |
| 🟡 MEDIUM — indirect interaction | 7 | 1.3 (no broker), 3.4–3.6 (risk engine), 6.2–6.5 (individual actors), 7.2–7.3 (strategy e2e), 8.3 (performance CLI) |
| 🟢 LOW — verify, unlikely to break | 29 | Docker, Futu/IB unchanged paths, ops/CLI commands |


*Generated from Phase 6 EXIT validation session. Cross-reference with [BUILD_PHASE_6.md](./BUILD_PHASE_6.md) and [BUILD_PHASE_8.md](./BUILD_PHASE_8.md).*
