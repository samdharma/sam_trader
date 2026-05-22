# Build Phase 10 — Safety & Dashboard

> **Status:** Not Started  
> **Goal:** Kill switch, circuit breakers, FastAPI backend, dashboard UI.  
> **Prev Phase:** [BUILD_PHASE_9.md](./BUILD_PHASE_9.md) — Pre-Market Pipeline  
> **Next Phase:** [BUILD_PHASE_11.md](./BUILD_PHASE_11.md) — Deploy Script & E2E Validation

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      Safety Layer                             │
├──────────────────────────────────────────────────────────────┤
│  Kill Switch                                                  │
│    └── Immediate: cancel-all + stop trading                  │
│  Circuit Breakers                                             │
│    └── Daily loss limit                                       │
│    └── Margin limit                                           │
│    └── Connection loss timeout                                │
│  Emergency Halt                                               │
│    └── Operator-triggered via API or CLI                     │
├──────────────────────────────────────────────────────────────┤
│                      Dashboard                                │
├──────────────────────────────────────────────────────────────┤
│  FastAPI Backend                                              │
│    ├── GET /health                                            │
│    ├── GET /api/positions                                     │
│    ├── GET /api/fills                                         │
│    ├── GET /api/scans/latest                                  │
│    └── GET /api/alerts                                        │
├──────────────────────────────────────────────────────────────┤
│  Static HTML Frontend                                         │
│    ├── Portfolio table (auto-refresh)                        │
│    ├── Recent fills table                                     │
│    ├── System health indicators                               │
│    ├── Pipeline results                                       │
│    └── Alert feed                                             │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — FastAPI

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import asyncpg

app = FastAPI()

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "services": {
            "sam-trader": await check_trader(),
            "sam-postgres": await check_postgres(),
            "sam-redis": await check_redis(),
        }
    }

@app.get("/api/fills")
async def get_fills(limit: int = 50):
    pool = await get_pg_pool()
    rows = await pool.fetch("SELECT * FROM fills ORDER BY ts_event DESC LIMIT $1", limit)
    return [dict(r) for r in rows]
```

---

## 3. Pre-Discovered Reference — Kill Switch

```python
class SafetyController:
    def __init__(self, trader: Trader):
        self.trader = trader
        self._halted = False

    def kill_switch(self) -> None:
        self._halted = True
        self.trader.cancel_all_orders()
        self.trader.close_all_positions()
        self.trader.stop()

    def check_circuit_breakers(self) -> bool:
        daily_pnl = self._calculate_daily_pnl()
        if daily_pnl < -self.max_daily_loss:
            self.kill_switch()
            return False
        return True
```

---

## 4. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam_trader-9z3.11.1` | Safety controls | Kill switch + circuit breakers + emergency halt + **Phase 6 actor integration** | ✅ Medium |
| `sam_trader-9z3.11.2` | Dashboard database | Portfolio snapshots, scan history tables | ✅ Small |
| `sam_trader-9z3.11.3` | FastAPI backend | Health, positions, fills, scans, alerts endpoints | ✅ Medium |
| `sam_trader-9z3.11.4` | Static HTML dashboard | Single-page auto-refreshing UI | ✅ Medium |
| `sam_trader-9z3.11.5` | [EXIT] Verify safety + dashboard | Integration test: kill switch, circuit breaker, dashboard data | ✅ Medium |

**No decomposition needed for Phase 10.** All tickets are well-scoped.

---

## 5. Dashboard HTML Template Pattern

```html
<!DOCTYPE html>
<html>
<head>
  <title>SAM Trader Dashboard</title>
  <meta http-equiv="refresh" content="30">
  <style>
    .healthy { color: green; }
    .unhealthy { color: red; }
  </style>
</head>
<body>
  <h1>SAM Trader Dashboard</h1>
  <div id="health"></div>
  <div id="positions"></div>
  <div id="fills"></div>
  <script>
    async function load() {
      const health = await fetch('/health').then(r => r.json());
      document.getElementById('health').innerHTML = JSON.stringify(health, null, 2);
    }
    load();
    setInterval(load, 30000);
  </script>
</body>
</html>
```

---

## 5. Integration with Phase 6 Actors (Gap Remediation)

> **v2 Post-Mortem (21-May):** 189 rejections with no self-halt; max_daily_loss triggered 9× with ambiguous unrealized P&L behavior.

**Circuit breaker trigger sources expanded from 3 to 5:**

1. `DAILY_PNL` (existing)
2. `MARGIN_LIMIT` (existing)
3. `CONNECTIVITY_LOSS` (existing)
4. **`REJECTION_STREAK`** (new) — triggered by `StrategyHaltRequest` from RejectionMonitorActor (`sam_trader-9z3.7.7`)
5. **`REALIZED_LOSS_LIMIT`** (new) — uses `RealizedPnLTrackerActor.get_realized_pnl()` (`sam_trader-9z3.7.8`)

**Key design points:**
- `REALIZED_LOSS_LIMIT` uses **purely realized** P&L, eliminating the v2 ambiguity where unrealized gains offset daily-loss calculations.
- `REJECTION_STREAK` auto-resets when RejectionMonitorActor cooldown expires.
- Defensive checks: if Phase 6 actors are not running, skip those trigger sources (no hard dependency).

**Beads ticket:** `sam_trader-9z3.11.1`

---

*Last updated: 2026-05-22*
