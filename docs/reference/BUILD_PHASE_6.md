# Build Phase 6 — Actors & State Management

> **Status:** ✅ Complete (Phase 6 EXIT validated 2026-05-24)  
> **Goal:** TradeJournalActor, HealthMonitorActor, BarResubscriptionActor, RejectionMonitorActor, RealizedPnLTrackerActor. PostgreSQL schema with venue column. Redis state persistence.  
> **Prev Phase:** [BUILD_PHASE_5.md](./BUILD_PHASE_5.md) — IBKR Adapter Re-integration  
> **Next Phase:** [BUILD_PHASE_7.md](./BUILD_PHASE_7.md) — Strategy Library & Bundle System  
> **EXIT Ticket:** `sam_trader-9z3.7.9` — all 6 AC validated  
> **Phase 8 cross-connection:** [BUILD_PHASE_8.md](./BUILD_PHASE_8.md) added slippage tracking to TradeJournalActor, PositionSnapshotActor, and LiveRiskEngine wiring.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                      TradingNode Actors                           │
├──────────────────────────────────────────────────────────────────┤
│  TradeJournalActor                                                │
│    └── Listens: OrderFilled                                       │
│    └── Writes: PostgreSQL (fills + orders tables)                │
│    └── Phase 8: writes slippage column (fill_px - expected_px)   │
├──────────────────────────────────────────────────────────────────┤
│  HealthMonitorActor                                               │
│    └── Periodic heartbeat (every 30s)                            │
│    └── Reports: orders, positions, venue connections             │
├──────────────────────────────────────────────────────────────────┤
│  BarResubscriptionActor                                           │
│    └── Monitors: bar subscriptions                                │
│    └── Action: re-subscribe on disconnect/reconnect              │
├──────────────────────────────────────────────────────────────────┤
│  RejectionMonitorActor (Phase 6 gap remediation)                  │
│    └── Listens: OrderRejected                                     │
│    └── Tracks: per-(instrument, strategy, reason) streaks        │
│    └── Emits: StrategyHaltRequest at threshold (default 3)       │
│    └── Cooldown: 15 min then auto-retry                          │
├──────────────────────────────────────────────────────────────────┤
│  RealizedPnLTrackerActor (Phase 6 gap remediation)                │
│    └── Listens: OrderFilled                                       │
│    └── FIFO matches lots per (strategy, instrument)              │
│    └── Persists: Redis key sam:pnl:{strategy_id}:{date}          │
│    └── Resets: 00:00 UTC daily                                   │
│    └── Pure realized P&L — no unrealized contamination           │
├──────────────────────────────────────────────────────────────────┤
│  PositionSnapshotActor (Phase 8)                                  │
│    └── Polls: self.cache.positions() every 60s                  │
│    └── Upserts: PG positions table (strategy, instrument, venue) │
│    └── Computes: unrealized PnL from mid-price via cache         │
├──────────────────────────────────────────────────────────────────┤
│  State Persistence                                                │
│    └── Redis: load_state=True, save_state=True                   │
│    └── Wired via CacheConfig in main.py                          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Wiring in main.py (CRITICAL — added 2026-05-24)

> **IMPORTANT:** All 6 actors are wired via `ImportableActorConfig` in `build_trading_node()`.  
> **Prior state:** Only PositionSnapshotActor (Phase 8) was wired. The 5 Phase 6 actors existed as `.py` files but were NEVER registered with TradingNode.

```python
# In build_trading_node() — after bundle loading:

instrument_ids = []  # Extracted from bundles for actors that need them
actors: list[ImportableActorConfig] = []

# --- Phase 6 actors ---
if cfg.actor_journal_enabled:
    actors.append(ImportableActorConfig(
        actor_path="sam_trader.actors.trade_journal:TradeJournalActor",
        config_path="sam_trader.actors.trade_journal:TradeJournalActorConfig",
        config={"postgres_host": ..., "instrument_ids": instrument_ids},
    ))

if cfg.actor_health_enabled:
    actors.append(ImportableActorConfig(
        actor_path="sam_trader.actors.health_monitor:HealthMonitorActor",
        ...
    ))

if cfg.actor_bar_resub_enabled:
    actors.append(...)  # BarResubscriptionActor

if cfg.actor_rejection_monitor_enabled:
    actors.append(...)  # RejectionMonitorActor

if cfg.actor_realized_pnl_enabled:
    actors.append(...)  # RealizedPnLTrackerActor

# --- Phase 8 actor ---
if cfg.actor_position_snapshot_enabled:
    actors.append(...)  # PositionSnapshotActor

node_config = TradingNodeConfig(actors=actors, ...)
```

**Feature flags (from `SamTraderConfig` / env vars):**
| Flag | Default | Actor |
|------|---------|-------|
| `ACTOR_JOURNAL_ENABLED` | `true` | TradeJournalActor |
| `ACTOR_HEALTH_ENABLED` | `true` | HealthMonitorActor |
| `ACTOR_BAR_RESUB_ENABLED` | `true` | BarResubscriptionActor |
| `ACTOR_REJECTION_MONITOR_ENABLED` | `true` | RejectionMonitorActor |
| `ACTOR_REALIZED_PNL_ENABLED` | `true` | RealizedPnLTrackerActor |
| `ACTOR_POSITION_SNAPSHOT_ENABLED` | defaults to `ACTOR_JOURNAL_ENABLED` | PositionSnapshotActor |

**Instrument ID extraction:** `instrument_ids` is extracted from bundles BEFORE actor config is built. This is because TradeJournalActor and RealizedPnLTrackerActor need instrument IDs in `on_start()` to subscribe to fill events. When bundles are empty, `instrument_ids=[]` — actors log a warning but do not crash.

---

## 3. PostgreSQL Schema

```sql
-- orders table (upsert by client_order_id)
CREATE TABLE orders (
    client_order_id VARCHAR(64) UNIQUE,
    venue_order_id  VARCHAR(64),
    strategy_id, instrument_id, venue, side, order_type,
    quantity, price, status, ts_submitted, ts_updated
);

-- fills table (insert by trade_id, Phase 8 adds slippage column)
CREATE TABLE fills (
    trade_id        VARCHAR(64) UNIQUE,
    client_order_id VARCHAR(64) REFERENCES orders(client_order_id),
    venue_order_id, strategy_id, instrument_id, venue, trd_market,
    side, qty, price, commission, currency,
    slippage        NUMERIC(24, 8),   -- Phase 8 addition (+ = unfavorable)
    ts_event, ts_init
);

-- positions table (upsert by PositionSnapshotActor, Phase 8)
CREATE TABLE positions (
    strategy_id, instrument_id, venue,
    net_quantity, avg_px, unrealized_pnl, realized_pnl,
    UNIQUE(strategy_id, instrument_id, venue)
);

-- performance_stats table (populated by PerformanceAnalyzer, Phase 8)
CREATE TABLE performance_stats (
    date, strategy_id, stat_name, stat_value,
    UNIQUE(date, strategy_id, stat_name)
);
```

**Full schema:** `docker/postgres/init/01_schema.sql`

---

## 4. Redis State Persistence

```python
# In build_trading_node():
if cfg.state_load_enabled or cfg.state_save_enabled:
    cache_db = DatabaseConfig(
        host=cfg.redis_host,
        port=cfg.redis_port,
        password=cfg.redis_password or None,
    )
    cache_config = CacheConfig(database=cache_db)

node_config = TradingNodeConfig(
    cache=cache_config,
    load_state=cfg.state_load_enabled,
    save_state=cfg.state_save_enabled,
)
```

**Redis keys used by Phase 6/8:**
| Key Pattern | Writer | Purpose |
|-------------|--------|---------|
| `sam:pnl:{strategy_id}:{date}` | RealizedPnLTrackerActor | Realized P&L per strategy per day |
| `sam:restart_request` | sam CLI | Graceful restart signal |
| `sam:quote:{symbol}` | Quote fetcher (Phase 8 cache fallback) | Cached quote data |

---

## 5. RejectionMonitorActor (Gap Remediation)

> **v2 Post-Mortem:** 189 rejections over 9 hours with no self-halt.

**Config:** `RejectionMonitorActorConfig(max_consecutive=3, cooldown_seconds=900)`

**Logic:**
1. Subscribe to `OrderRejected` events on message bus
2. Track per `(instrument_id, strategy_id, reason)` counter
3. At threshold (default 3 identical rejections): emit `StrategyHaltRequest` on bus
4. Cooldown (default 15 min): streak resets automatically, allowing retry

**Key files:**
- `src/sam_trader/actors/rejection_monitor.py`
- `tests/unit/actors/test_rejection_monitor.py`
- `tests/integration/test_phase6_exit.py::test_rejection_monitor_config`
- `tests/integration/test_phase6_exit.py::test_rejection_monitor_counter_logic`

---

## 6. RealizedPnLTrackerActor (Gap Remediation)

> **v2 Post-Mortem:** max_daily_loss triggered 9× with ambiguous unrealized P&L offset.

**Config:** `RealizedPnLTrackerActorConfig(redis_host=..., key_prefix="sam:pnl", instrument_ids=...)`

**Logic:**
1. Subscribe to `OrderFilled` events
2. FIFO match lots per `(strategy_id, instrument_id)`
3. Compute realized P&L per trade: `(exit_price - entry_price) * qty * lot_side`
4. Persist running total to Redis: `sam:pnl:{strategy_id}:{date}`
5. Reset at 00:00 UTC daily
6. Pure realized — no unrealized contamination

**Key files:**
- `src/sam_trader/actors/realized_pnl.py`
- `tests/unit/actors/test_realized_pnl.py`
- `tests/integration/test_phase6_exit.py::test_realized_pnl_config`
- `tests/integration/test_phase6_exit.py::test_realized_pnl_fifo_unit`

---

## 7. Stale State Guard & Phase 6 EXIT Integration Test

### 7.1 Startup Guard: Skip State Load When No Exec Clients

> **Ticket:** `sam_trader-9z3.7.14` — Stale orders persist in Redis across restarts

**Problem:** When a broker connection fails, strategies continue generating orders.
These orders are serialized to Redis via `save_state()` and replayed on restart via
`load_state()`, creating a growing pool of orphaned orders that the `ExecEngine` rejects.

**Guard implemented in `main.py`:**

```python
load_state = cfg.state_load_enabled
if load_state and not exec_clients:
    logger.critical(
        "STATE LOAD ABORTED: load_state=True but ZERO execution clients ..."
    )
    load_state = False
```

**Behavior:**
- If `STATE_LOAD_ENABLED=true` but **zero** execution clients are registered
  (both Futu and IB disabled or unavailable), the node logs a **CRITICAL** message
  and sets `load_state=False` for this session.
- `save_state` is **unaffected** — the node will still persist state on shutdown
  once exec clients are available again.
- The operator can clear stale state manually with the emergency CLI:
  `sam flush-cache --force`

**Unit test:** `tests/unit/test_main.py::test_skip_state_load_when_no_exec_clients`

### 7.2 Phase 6 EXIT Integration Test

**File:** `tests/integration/test_phase6_exit.py` (created 2026-05-24)

**Tests all 6 acceptance criteria:**
| AC | Test | Validates |
|----|------|-----------|
| 1. Fill with venue | `test_trade_produces_fill_with_venue` + `test_trade_journal_actor_config` | Backtest engine generates fills with venue tags; TradeJournalActorConfig instantiable |
| 2. HealthMonitor heartbeat | `test_health_monitor_config_and_instantiation` | Config correct, actor instantiable without errors |
| 3. State persistence | `test_strategy_state_persists_across_restarts` | Strategy saves bar_count, reloads correctly |
| 4. Bar resubscription | `test_bar_resubscription_config_and_instantiation` | Config correct, actor instantiable |
| 5. Rejection monitoring | `test_rejection_monitor_config` + `test_rejection_monitor_counter_logic` | Config correct; internal counter tracks streaks to threshold |
| 6. Realized P&L | `test_realized_pnl_config` + `test_realized_pnl_fifo_unit` | Config correct; FIFO: buy 100@150 → sell 100@155 = +$500, lot cleared |

**All 9 integration tests pass** (1 skipped due to Python 3.14 BacktestEngine reuse limitation).

---

## 8. Phase 8 Cross-Connections (Implemented)

All Phase 8 features are now implemented. Key interactions with Phase 6 actors:

| Phase 8 Feature | Affects Phase 6 | File | Status |
|-----------------|----------------|------|--------|
| **Slippage tracking** | TradeJournalActor._write_fill() writes `slippage` column | `trade_journal.py` | ✅ Implemented (9z3.9.9) |
| **PositionSnapshotActor** | New actor wired alongside Phase 6 actors in `main.py` | `position_snapshot.py` | ✅ Implemented (9z3.9.10) |
| **LiveRiskEngine** | Pre-trade risk filter active; RejectionMonitor handles post-trade rejections | `main.py` | ✅ Implemented (9z3.9.7) |
| **PerformanceAnalyzer** | Reads `fills` table (written by TradeJournalActor) for nightly stats | `performance_analyzer.py` | ✅ Implemented (9z3.9.11) |
| **sam performance CLI** | Reads `performance_stats` which depends on fills | `cli.py` | ✅ Implemented (9z3.9.8) |

---

## 9. Ticket Summary

| Ticket | Title | Status |
|--------|-------|--------|
| `sam_trader-9z3.7.1` | PostgreSQL schema | ✅ |
| `sam_trader-9z3.7.2` | TradeJournalActor | ✅ |
| `sam_trader-9z3.7.3` | HealthMonitorActor | ✅ |
| `sam_trader-9z3.7.4` | BarResubscriptionActor | ✅ |
| `sam_trader-9z3.7.5` | Redis state wiring | ✅ |
| `sam_trader-9z3.7.7` | RejectionMonitorActor | ✅ |
| `sam_trader-9z3.7.8` | RealizedPnLTrackerActor | ✅ |
| `sam_trader-9z3.7.9` | [EXIT] Verify actors | ✅ (2026-05-24) |

---

---

## 10. Known Issues

### 10.1 HealthMonitorActor — TypeError in Redis Heartbeat Write

**Symptom:** `TypeError('an integer is required')` in `_write_heartbeat_to_redis`
(line 191) when the heartbeat callback fires. The error originates in NautilusTrader's
Cython `Logger.warning()` wrapper.

**Root cause (TBC):** The `exc` variable captured in the `except Exception` block is
passed to `self.log.warning("... %s", exc)`. The Cython logger may not handle
`%s` formatting with Exception types correctly. Converting to `str(exc)` should fix.

**Impact:** Redis heartbeat keys (`sam:heartbeat:last`) are not persisted.
HealthMonitorActor continues to log heartbeats to console (L1), but the Redis-based
safety dashboard (L3) is degraded.

**Fix (applied 2026-05-25):**
```python
# In health_monitor.py, _write_heartbeat_to_redis, line 189:
# Changed from %s C-style formatting to f-string — Nautilus Cython
# Logger.warning() does not support %s with Exception arguments.
self.log.warning(
    f"HealthMonitorActor: Redis write failed for heartbeat: {exc}"
)
```

**Secondary issue:** After the TypeError fix, the Redis write still fails with
`no running event loop` because `_on_heartbeat` is a synchronous callback. The
`asyncio.get_running_loop()` call fails. The loop reference should be stored
at init time and reused.

---

*Last updated: 2026-05-25 — Added Known Issues from sandbox deployment*
