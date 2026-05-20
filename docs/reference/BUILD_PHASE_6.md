# Build Phase 6 — Actors & State Management

> **Status:** Not Started  
> **Goal:** TradeJournalActor, HealthMonitorActor, BarResubscriptionActor. PostgreSQL schema with venue column. Redis state persistence.  
> **Prev Phase:** [BUILD_PHASE_5.md](./BUILD_PHASE_5.md) — IBKR Adapter Re-integration  
> **Next Phase:** [BUILD_PHASE_7.md](./BUILD_PHASE_7.md) — Strategy Library & Bundle System

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      TradingNode Actors                       │
├──────────────────────────────────────────────────────────────┤
│  TradeJournalActor                                            │
│    └── Listens: OrderFilled                                   │
│    └── Writes: PostgreSQL (fills table + venue column)       │
├──────────────────────────────────────────────────────────────┤
│  HealthMonitorActor                                           │
│    └── Periodic heartbeat (every 30s)                        │
│    └── Reports: orders, positions, venue connections         │
├──────────────────────────────────────────────────────────────┤
│  BarResubscriptionActor                                       │
│    └── Monitors: bar subscriptions                            │
│    └── Action: re-subscribe on disconnect/reconnect          │
├──────────────────────────────────────────────────────────────┤
│  State Persistence                                            │
│    └── Redis: load_state=True, save_state=True               │
│    └── CacheDatabaseAdapter                                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — Nautilus Actor API

```python
from nautilus_trader.common.actor import Actor
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.msgbus.bus import MessageBus

class TradeJournalActor(Actor):
    def on_start(self) -> None:
        self.subscribe_event(OrderFilled, handler=self.on_order_filled)

    def on_order_filled(self, event: OrderFilled) -> None:
        venue = event.instrument_id.venue.value
        ...
```

### 2.1 Actor Lifecycle Hooks

- `on_start()` — register subscriptions
- `on_stop()` — cleanup
- `on_reset()` — reset state
- `on_dispose()` — final cleanup

### 2.2 Periodic Actions

```python
from nautilus_trader.common.clock import LiveClock

self.clock.set_interval_ns(
    name="health_heartbeat",
    interval=30_000_000_000,  # 30s in ns
    callback=self._on_heartbeat,
)
```

---

## 3. Pre-Discovered Reference — PostgreSQL / asyncpg

```python
import asyncpg

pool = await asyncpg.create_pool(dsn="postgresql://user:pass@sam-postgres:5432/sam")

# Insert fill
await pool.execute(
    """
    INSERT INTO fills (venue, instrument_id, side, quantity, price, commission, ts_event)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    """,
    venue, symbol, side, qty, price, commission, ts_event,
)
```

### 3.1 Schema Additions

```sql
-- Add venue column to existing fills table
ALTER TABLE fills ADD COLUMN IF NOT EXISTS venue VARCHAR(16);
ALTER TABLE fills ADD COLUMN IF NOT EXISTS trd_market VARCHAR(8);
```

---

## 4. Pre-Discovered Reference — Redis State

```python
from nautilus_trader.system.config import CacheConfig

# In TradingNode config
cache_config = CacheConfig(
    database=RedisCacheDatabase(
        host="sam-redis",
        port=6379,
        db=0,
    ),
    save_state=True,
    load_state=True,
)
```

---

## 5. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p6-pg-schema` | PostgreSQL schema | Add venue columns to fills | ✅ Small |
| `sam-p6-journal` | TradeJournalActor | Port from v2, multi-venue fills | ✅ Medium |
| `sam-p6-health` | HealthMonitorActor | Port from v2, heartbeat | ✅ Small |
| `sam-p6-bar-resub` | BarResubscriptionActor | Port from v2, re-subscribe logic | ✅ Small |
| `sam-p6-state` | Redis state wiring | `CacheConfig` in `main.py` | ✅ Small |
| `sam-p6-verify` | Verify actors | Integration test: fill → PG, state → Redis | ✅ Medium |

**No decomposition needed for Phase 6.** All tickets are well-scoped.

---

## 6. Commonly Used Imports

```python
from nautilus_trader.common.actor import Actor
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.system.config import CacheConfig
import asyncpg
```

---

*Last updated: 2026-05-21*
