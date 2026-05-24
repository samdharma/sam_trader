# Build Phase 10 — Safety & Dashboard

> **Status:** Not Started (simplified 2026-05-24 — 3 tickets, no new tables, no FastAPI)  
> **Goal:** Operator safety controls (kill switch, circuit breakers) + basic read-only dashboard showing existing Phase 6/8 data.  
> **Prev Phase:** [BUILD_PHASE_9.md](./BUILD_PHASE_9.md) — Pre-Market Pipeline  
> **Next Phase:** [BUILD_PHASE_11.md](./BUILD_PHASE_11.md) — Deploy Script & E2E Validation

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  sam-services: Safety + Dashboard                             │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Safety Controls (11.6)                                    │ │
│  │                                                           │ │
│  │  CLI: sam kill / sam halt / sam resume                   │ │
│  │    └── Sets LiveRiskEngine trading_state (Phase 8)       │ │
│  │    └── Publishes to Redis sam:kill_switch                │ │
│  │                                                           │ │
│  │  Circuit Breakers (automated):                            │ │
│  │    ├── DAILY_PNL → reads sam:pnl:* Redis keys            │ │
│  │    │   (RealizedPnLTrackerActor, Phase 6, already built) │ │
│  │    ├── REJECTION_STREAK → listens StrategyHaltRequest    │ │
│  │    │   (RejectionMonitorActor, Phase 6, already built)   │ │
│  │    └── CONNECTIVITY_LOSS → polls HealthMonitorActor      │ │
│  │        (Phase 6, already built)                           │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Basic Dashboard (11.7)                                    │ │
│  │                                                           │ │
│  │  Single dashboard.html on port 8080                       │ │
│  │  Auto-refresh 30s (meta tag)                              │ │
│  │                                                           │ │
│  │  Sections:                                                │ │
│  │  ┌─ System Health ─────────────────────────────────────┐ │ │
│  │  │ PG ● UP  Redis ● UP  Futu ● UP  Trader ● UP        │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  │  ┌─ Today's Fills (last 20) ───────────────────────────┐ │ │
│  │  │ Time       Symbol Side Qty  Price   Venue Slippage  │ │ │
│  │  │ 09:35:12   TSLA   BUY  100  245.30  FUTU  +0.02    │ │ │
│  │  │ 09:32:05   NVDA   SELL  50  178.15  IB    -0.01   │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  │  ┌─ Current Positions ─────────────────────────────────┐ │ │
│  │  │ Symbol Venue Qty   Avg Px   Unreal P&L  Strategy    │ │ │
│  │  │ TSLA   FUTU  100   245.30   +125.00     tsla-orb    │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  │  ┌─ P&L Summary ───────────────────────────────────────┐ │ │
│  │  │ tsla-orb-15m-futu:  +$342.50                        │ │ │
│  │  │ nvda-mom-5m-ib:     -$87.30                         │ │ │
│  │  │ TOTAL REALIZED:     +$255.20                        │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. What Phase 6 + 8 Already Built (Dashboard Data Sources)

| Data | Writer | Table/Key | Freshness |
|------|--------|-----------|-----------|
| Fills | TradeJournalActor (Phase 6) | `fills` PG table | Real-time |
| Orders | TradeJournalActor (Phase 6) | `orders` PG table | Real-time |
| Positions | PositionSnapshotActor (Phase 8) | `positions` PG table | Every 60s |
| Realized P&L | RealizedPnLTrackerActor (Phase 6) | `sam:pnl:{strategy}:{date}` Redis | Real-time |
| Rejections | RejectionMonitorActor (Phase 6) | MessageBus events | Real-time |
| Pre-trade risk | LiveRiskEngine (Phase 8) | trading_state | Active |
| System health | `sam health` CLI (Phase 8) | docker inspect | On-demand |

**The dashboard reads existing data — zero new tables, zero new writers.**

---

## 3. Dashboard Backend (No FastAPI)

The dashboard uses a simple Python HTTP server — **no new dependencies**:

```python
# src/sam_trader/services/dashboard.py
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncpg

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json_response(get_health())
        elif self.path == "/api/dashboard":
            self._json_response(get_dashboard_data())
        else:
            self._serve_html("dashboard.html")

def get_dashboard_data():
    """Query PG fills + positions, Redis P&L, docker health."""
    return {
        "health": check_all_services(),
        "fills": query_fills(limit=20),
        "positions": query_positions(),
        "pnl": query_pnl_from_redis(),
    }
```

---

## 4. Safety Controls (Glue Over Existing Infrastructure)

### 4.1 What Already Exists

| Component | Phase | What It Does |
|-----------|-------|-------------|
| `LiveRiskEngine` | 8 | Pre-trade rate limits, notional caps, `trading_state` (HALTED/RUNNING) |
| `RejectionMonitorActor` | 6 | Tracks rejection streaks, emits `StrategyHaltRequest` |
| `RealizedPnLTrackerActor` | 6 | Tracks realized P&L per strategy in Redis |
| `HealthMonitorActor` | 6 | Periodic heartbeat with venue connection status |
| `sam restart` CLI | 8 | Graceful restart via Redis signal |

### 4.2 What Phase 10 Adds

```python
# Safety CLI commands (thin wrappers)
def cmd_kill():
    """Cancel all orders, halt trading, publish to Redis."""
    redis.publish("sam:kill_switch", "HALTED")
    # LiveRiskEngine reads trading_state from Redis

def cmd_halt():
    """Position-close-only mode. No new entries."""
    redis.publish("sam:kill_switch", "CLOSE_ONLY")

def cmd_resume():
    """Clear halt, re-enable trading."""
    redis.publish("sam:kill_switch", "RUNNING")

# Circuit breaker monitor (runs as periodic task in sam-services)
async def monitor_circuit_breakers():
    while True:
        await check_daily_pnl_breaker()      # reads sam:pnl:* Redis
        await check_rejection_streak()        # reads MessageBus events
        await check_connectivity()            # reads HealthMonitorActor heartbeat
        await asyncio.sleep(10)
```

**Zero new risk math.** All computation is in existing Nautilus components and actors. Safety controls are just thresholds + triggers.

---

## 5. Ticket Breakdown

| # | Ticket ID | Title | Type | Dependencies | Ralph Order |
|---|-----------|-------|------|-------------|-------------|
| 1 | `9z3.11.6` | Safety controls — kill switch, circuit breakers, emergency halt | task | Phase 9 EXIT (10.27) | **1st** |
| 2 | `9z3.11.7` | Basic dashboard — single HTML page with fills, positions, P&L, health | task | Phase 9 EXIT (10.27) | **2nd** (parallel with 6) |
| 3 | `9z3.11.8` | [EXIT] Verify safety controls + dashboard | exit | 11.6, 11.7 | **3rd** |

### 5.1 Dependency Graph

```
Phase 9 EXIT (10.27)
       │
       ├──► 11.6 (Safety Controls) ──┐
       │                              ├──► 11.8 (EXIT) ──► Phase 11
       └──► 11.7 (Dashboard) ────────┘
```

---

## 6. What Was Removed (vs Original Phase 10)

| Removed | Why |
|---------|-----|
| Dashboard DB ticket (old 11.2) | No new tables needed — reads existing PG tables |
| FastAPI backend (old 11.3) | Overkill — simple http.server handles 2 endpoints + static HTML |
| `/api/scans` endpoint | No scans exist (Phase 9 not built) |
| `/api/alerts` endpoint | No alert system exists |
| Pipeline results section | Pipeline doesn't exist yet |
| Alert feed section | No alerts |
| `portfolio_snapshots` table | PositionSnapshotActor already writes `positions` |
| Complex SafetyController class | LiveRiskEngine + actors already handle risk — just needs triggers |

---

## 7. Future Enhancement Path

After core mechanics are proven and refined:
- Upgrade to FastAPI for richer API
- Add scan results section (Phase 9 data)
- Add alert/notification system
- Add order-management capability
- Add pipeline status
- Real-time WebSocket updates instead of 30s refresh

---

*Last updated: 2026-05-24 — Simplified from 5 to 3 tickets. Zero new tables, zero new frameworks.*
