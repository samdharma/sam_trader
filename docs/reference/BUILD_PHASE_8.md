# Build Phase 8 — sam-services Container

> **Status:** ✅ Complete (Phase 8 EXIT validated 2026-05-23)  
> **Goal:** Operations container with CLI, cron, health checks, backup, quote fetcher, performance analysis, and production safeguards. Decoupled from sam-trader.  
> **Prev Phase:** [BUILD_PHASE_7.md](./BUILD_PHASE_7.md) — Strategy Library & Bundle System  
> **Next Phase:** [BUILD_PHASE_9.md](./BUILD_PHASE_9.md) — Pre-Market Pipeline  
> **Gap Analysis:** [GAP_ANALYSIS_RISK_STRATEGY_JOURNAL_PERF.md](./GAP_ANALYSIS_RISK_STRATEGY_JOURNAL_PERF.md)  
> **Phase 6 cross-connection:** Phase 8 actor wiring fix also completed Phase 6 EXIT — see [BUILD_PHASE_6.md](./BUILD_PHASE_6.md) §2 for main.py wiring details.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    sam-services Container                         │
│                    (independent lifecycle)                        │
├──────────────────────────────────────────────────────────────────┤
│  CLI (argparse)                                                   │
│    ├── sam status        →  docker ps + health                    │
│    ├── sam health        →  deep health check (PG, Redis, Futu)  │
│    ├── sam backup        →  pg_dump + config + volume backup      │
│    ├── sam restore       →  restore from date-specific archive    │
│    ├── sam logs          →  docker logs wrapper                   │
│    ├── sam restart       →  graceful restart via Redis state      │
│    ├── sam quote         →  Redis/Broker quote fetch               │
│    ├── sam performance   →  Nautilus-powered performance stats    │
│    ├── sam deploy        →  git pull + rebuild + restart          │
│    ├── sam hotfix        →  copy module + trigger reload          │
│    ├── sam update        →  git pull latest + rebuild + restart   │
│    ├── sam rollback      →  git checkout tag + rebuild + restart  │
│    └── sam version       →  show deployed version                 │
├──────────────────────────────────────────────────────────────────┤
│  Cron                                                             │
│    ├── Daily backup        @ 06:00 HKT weekdays                  │
│    ├── Log rotation        @ 03:00 HKT daily                     │
│    ├── Deploy window check @ every 30min, 04:00–09:00 HKT        │
│    ├── Pipeline slot        @ 08:00 HKT weekdays                 │
│    └── Performance analysis @ 02:00 HKT daily (NEW)              │
├──────────────────────────────────────────────────────────────────┤
│  PerformanceAnalyzer (NEW — Nautilus-native)                      │
│    └── Queries PG fills → Nautilus PortfolioAnalyzer → PG stats  │
├──────────────────────────────────────────────────────────────────┤
│  Quote Fetcher                                                    │
│    └── sam quote TSLA.NASDAQ → Redis cache lookup                │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  sam-trader container (runtime modifications — Phase 8 scope)     │
├──────────────────────────────────────────────────────────────────┤
│  LiveRiskEngine (NEW — Nautilus-native pre-trade filter)          │
│    ├── max_order_submit_rate    — prevents order flooding         │
│    ├── max_order_modify_rate    — prevents modify flooding        │
│    ├── max_notional_per_order   — per-currency notional caps      │
│    └── trading_state            — HALTED/RUNNING state machine    │
├──────────────────────────────────────────────────────────────────┤
│  New Actors (NEW — Phase 8 scope)                                 │
│    ├── PositionSnapshotActor    — writes PG positions table       │
│    └── TradeJournalActor update — slippage tracking column        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Docker Service (COMPLETE — ticket 9z3.9.1)

```yaml
# docker-compose.yml snippet
  sam-services:
    build:
      context: .
      dockerfile: Dockerfile.services
    container_name: sam-services
    profiles: ["services"]
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/opt/sam_trader/config:ro
      - ./logs:/opt/sam_trader/logs
      - ./backups:/opt/sam_trader/backups
    environment:
      - POSTGRES_HOST=sam-postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=sam_trader
      - POSTGRES_USER=sam
      - POSTGRES_PASSWORD=sam_secret
      - REDIS_HOST=sam-redis
      - REDIS_PORT=6379
      - DEPLOY_WINDOW=05:00-08:00
      - PIPELINE_SCHEDULE=08:00
      - TZ=Asia/Hong_Kong
    networks:
      - sam-net
```

**Key design decisions:**
- Docker socket mounted read-only for container inspection
- Environment variables passed through `.env_cron` file for cron jobs
- Non-root user `sam` for runtime; cron starts as root then drops to `sam`
- 3-layer health check: L1 pgrep python, L2 port 8080 TCP, L3 HTTP /health

---

## 3. CLI Implementation Pattern (ticket 9z3.9.2)

Use `argparse` (already established pattern in `services/cli.py`). All commands follow:

```python
def _cmd_<name>(args: argparse.Namespace) -> int:
    """..."""
    try:
        # Implementation
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
```

**Commands to implement:**
| Command | Implementation | Dependencies |
|---------|---------------|--------------|
| `sam status` | `docker ps --filter name=sam-` via subprocess | docker.sock |
| `sam health` | TCP connects to PG:5432, Redis:6379, Futu:11111 | socket |
| `sam backup` | delegates to `backup.py::run_backup()` | backup.py |
| `sam restore <date>` | delegates to `backup.py::run_restore()` | backup.py |
| `sam logs [svc]` | `docker logs -f sam-{svc}` via subprocess | docker.sock |
| `sam restart` | `docker compose restart sam-trader` | docker.sock |
| `sam quote <sym>` | delegates to quote fetcher module | 9z3.9.4 |
| `sam performance` | delegates to performance_analyzer.py + PG query | 9z3.9.11 |
| `sam deploy [--tag]` | git pull + `docker compose build --no-cache sam-trader` + restart | docker.sock, git |
| `sam hotfix <path>` | copy file + touch restart trigger file | docker.sock |
| `sam rollback <tag>` | `git checkout <tag>` + rebuild + restart | docker.sock, git |
| `sam version` | `git describe --tags` + build timestamp from file | git |
| `sam update` | `git pull` + rebuild + restart | docker.sock, git |

**Entry point wiring in pyproject.toml:**
```toml
[project.scripts]
sam = "sam_trader.services.cli:main"
```

---

## 4. Cron Schedule (ticket 9z3.9.3)

**Current crontab** (`src/sam_trader/services/crontab`):
```
0 6 * * 1-5 sam ... python3 -m sam_trader.services.backup backup
0 3 * * * sam ... python3 -m sam_trader.services.rotate_logs
*/30 4-9 * * * sam ... python3 -m sam_trader.services.deploy_window
0 8 * * 1-5 sam ... python3 -m sam_trader.services.pipeline
```

**Addition needed:** Performance analysis cron entry:
```
0 2 * * * sam . /opt/sam_trader/.env_cron && cd /opt/sam_trader && /usr/local/bin/python3 -m sam_trader.services.performance_analyzer >> /opt/sam_trader/logs/performance.log 2>&1
```

**Environment variable flow:** All cron jobs source `.env_cron` which is generated at container start:
```bash
env | grep -E '^(POSTGRES|REDIS|BACKUP|FUTU|CONFIG|SAM_|DEPLOY|PIPELINE|LOG_|TZ)' > /opt/sam_trader/.env_cron
```

---

## 5. Quote Fetcher (ticket 9z3.9.4)

```python
# Pattern: src/sam_trader/services/quote.py (or extend existing quote.py)
def get_quote(symbol: str) -> dict:
    """Get quote for symbol. Fast path: Redis cache. Fallback: broker query."""
    # 1. Try Redis: sam:quote:{symbol}
    # 2. Fallback: query via Futu OpenD or IB Gateway
    # 3. Return {"symbol": ..., "bid": ..., "ask": ..., "last": ..., "source": "cache|broker"}
```

---

## 6. PerformanceAnalyzer — Nautilus PortfolioAnalyzer (ticket 9z3.9.11) 🔴 NEW

### 6.1 Why Nautilus Native?

NautilusTrader ships a complete, **Rust-backed** performance analytics stack in `nautilus_trader.analysis`. We currently use **none of it**. Instead of building custom Sharpe/Sortino/drawdown math, we feed our PG fill data into Nautilus's battle-tested `PortfolioAnalyzer`.

### 6.2 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  sam-services: PerformanceAnalyzer (cron @ 02:00 HKT nightly)    │
│                                                                  │
│  1. Query PG fills (last N days)                                │
│     SELECT * FROM fills WHERE ts_event > NOW() - INTERVAL 'N days'
│                                                                  │
│  2. Convert to Nautilus Trade objects                           │
│     for each fill:                                               │
│       trade = Trade(                                             │
│         instrument_id=InstrumentId.from_str(row["instrument_id"]),
│         entry=OrderSide.BUY if row["side"]=="BUY" else SELL,    │
│         entry_price=Price.from_str(str(row["price"])),           │
│         entry_qty=Quantity.from_str(str(row["qty"])),            │
│         ...                                                      │
│       )                                                          │
│                                                                  │
│  3. Feed to Nautilus PortfolioAnalyzer                           │
│     analyzer = PortfolioAnalyzer()                               │
│     analyzer.add_trade(trade)  # for each trade                  │
│     analyzer.calculate_statistics()                              │
│                                                                  │
│  4. Read computed stats                                          │
│     stats = analyzer.get_performance_stats_general()             │
│     → CAGR, SharpeRatio, SortinoRatio, MaxDrawdown,              │
│       WinRate, ProfitFactor, Expectancy, etc.                    │
│                                                                  │
│  5. Store in PG performance_stats table                          │
│     INSERT INTO performance_stats (date, strategy_id,            │
│       stat_name, stat_value) VALUES (...)                        │
│     ON CONFLICT (date, strategy_id, stat_name) DO UPDATE         │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 PG Schema Addition

```sql
-- Add to docker/postgres/init/01_schema.sql
CREATE TABLE IF NOT EXISTS performance_stats (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    strategy_id     VARCHAR(128) NOT NULL,
    stat_name       VARCHAR(64) NOT NULL,
    stat_value      NUMERIC(24, 8),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, strategy_id, stat_name)
);

CREATE INDEX IF NOT EXISTS idx_perf_stats_date ON performance_stats(date);
CREATE INDEX IF NOT EXISTS idx_perf_stats_strategy ON performance_stats(strategy_id);
```

### 6.4 Implementation Pattern

```python
# src/sam_trader/services/performance_analyzer.py

import asyncio
import asyncpg
from datetime import datetime, timedelta, timezone
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.model.data import TradeTick  # or build Trade objects
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

class PerformanceAnalyzer:
    """Wraps NautilusTrader PortfolioAnalyzer for nightly stats computation."""

    NAUTILUS_STATS = [
        "CAGR", "SharpeRatio", "SortinoRatio", "MaxDrawdown",
        "CalmarRatio", "WinRate", "ProfitFactor", "Expectancy",
        "ReturnsVolatility", "RiskReturnRatio", "AvgWinner",
        "AvgLoser", "MaxWinner", "MaxLoser", "MinWinner", "MinLoser",
        "LongRatio", "ReturnsAverage",
    ]

    def __init__(self, pg_dsn: str):
        self._pg_dsn = pg_dsn

    async def compute_and_store(self, lookback_days: int = 365) -> dict:
        """Main entry point — query fills, compute stats, store in PG."""
        pool = await asyncpg.create_pool(self._pg_dsn, min_size=1, max_size=2)
        try:
            strategies = await self._get_strategies(pool, lookback_days)
            results = {}
            for strategy_id in strategies:
                fills = await self._get_fills(pool, strategy_id, lookback_days)
                if not fills:
                    continue
                trades = self._fills_to_trades(fills)
                analyzer = PortfolioAnalyzer()
                for trade in trades:
                    analyzer.add_trade(trade)
                analyzer.calculate_statistics()
                stats = analyzer.get_performance_stats_general()
                await self._store_stats(pool, strategy_id, stats)
                results[strategy_id] = stats
            return results
        finally:
            await pool.close()
```

### 6.5 Design Principles

- **ZERO custom performance math** — all statistics from NautilusTrader Rust-backed `PortfolioAnalyzer`
- Runs in sam-services (not sam-trader hot path) as a nightly cron job
- Reads from PG fills table (written by TradeJournalActor in sam-trader)
- Writes to new `performance_stats` PG table for CLI/dashboard consumption
- Handles empty fills gracefully (no crash, log warning)

---

## 7. PositionSnapshotActor (ticket 9z3.9.10) 🔴 NEW

### 7.1 Why This Exists

The PG `positions` table was created in Phase 6 with full schema but **nothing writes to it**. It's orphaned. This actor fills the gap, providing position data for:
- Phase 10 dashboard (position display)
- Performance analysis (position-level returns)
- Health monitoring (position reconciliation)

### 7.2 Implementation Pattern

```python
# src/sam_trader/actors/position_snapshot.py

class PositionSnapshotActorConfig(ActorConfig, frozen=True):
    postgres_host: str = "sam-postgres"
    postgres_port: int = 5432
    postgres_db: str = "sam_trader"
    postgres_user: str = "sam"
    postgres_password: str = "sam_secret"
    snapshot_interval_secs: int = 60


class PositionSnapshotActor(Actor):
    """Periodically snapshots Nautilus positions to PostgreSQL."""

    def __init__(self, config: PositionSnapshotActorConfig):
        super().__init__(config)
        self._pool = None
        self._task: asyncio.Task | None = None

    def on_start(self) -> None:
        # Create asyncpg pool, start periodic timer
        loop = asyncio.get_running_loop()
        self._pool_task = loop.create_task(self._create_pool())
        self._task = loop.create_task(self._snapshot_loop())

    async def _snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.snapshot_interval_secs)
            await self._snapshot_positions()

    async def _snapshot_positions(self) -> None:
        """Upsert current positions from self.cache.positions() into PG."""
        positions = self.cache.positions()  # Nautilus cache facade
        for pos in positions:
            sql = """
                INSERT INTO positions (strategy_id, instrument_id, venue,
                    net_quantity, avg_px, unrealized_pnl, realized_pnl, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (strategy_id, instrument_id, venue)
                DO UPDATE SET net_quantity=$4, avg_px=$5,
                    unrealized_pnl=$6, realized_pnl=$7, updated_at=NOW()
            """
            # Execute via asyncpg pool
```

### 7.3 Wiring in main.py

```python
# In build_trading_node(), after node creation:
if cfg.actor_position_snapshot_enabled:  # new config field
    pos_snapshot_config = PositionSnapshotActorConfig(
        postgres_host=cfg.postgres_host,
        postgres_port=cfg.postgres_port,
        postgres_db=cfg.postgres_db,
        postgres_user=cfg.postgres_user,
        postgres_password=cfg.postgres_password,
    )
    pos_snapshot_actor = PositionSnapshotActor(pos_snapshot_config)
    node.add_actor(pos_snapshot_actor)
```

---

## 8. LiveRiskEngine Integration (ticket 9z3.9.7) 🔴 NEW

### 8.1 Why Nautilus Native?

NautilusTrader ships a production-grade `LiveRiskEngine` that acts as a **pre-trade filter**. Orders that violate rate limits or notional caps are rejected **BEFORE** they reach the broker. We currently do NOT wire it — every order passes unfiltered.

### 8.2 Configuration

New env vars in `SamTraderConfig`:
```python
# config.py additions
risk_max_order_submit_rate: str = "100/00:00:01"    # RISK_MAX_ORDER_SUBMIT_RATE
risk_max_order_modify_rate: str = "100/00:00:01"    # RISK_MAX_ORDER_MODIFY_RATE
risk_max_notional_per_order: str = ""               # RISK_MAX_NOTIONAL_PER_ORDER (JSON)
risk_bypass: bool = False                           # RISK_BYPASS=1 for override
```

### 8.3 Wiring in main.py

```python
import json
from nautilus_trader.live.config import LiveRiskEngineConfig

# In build_trading_node():
notional_limits = {}
if cfg.risk_max_notional_per_order:
    notional_limits = json.loads(cfg.risk_max_notional_per_order)

risk_config = LiveRiskEngineConfig(
    bypass=cfg.risk_bypass,
    max_order_submit_rate=cfg.risk_max_order_submit_rate,
    max_order_modify_rate=cfg.risk_max_order_modify_rate,
    max_notional_per_order=notional_limits,
)

node_config = TradingNodeConfig(
    ...,
    risk_engine=risk_config,
)
```

### 8.4 Design Principles

- **ZERO custom risk logic** — 100% NautilusTrader standard `LiveRiskEngine`
- Pre-trade filtering: orders rejected before reaching broker
- `RISK_BYPASS=1` serves as emergency kill-switch override
- Phase 10 safety controls (kill switch, circuit breakers) will integrate with `trading_state`

---

## 9. Slippage Tracking (ticket 9z3.9.9) 🔴 NEW

### 9.1 PG Schema Addition

```sql
-- Add to fills table (idempotent)
ALTER TABLE fills ADD COLUMN IF NOT EXISTS slippage NUMERIC(24, 8);
```

### 9.2 TradeJournalActor Update

```python
# In _write_fill(), compute slippage:
expected_price = None
# 1. Try order limit price from cache
cached_order = self.cache.order(event.client_order_id)
if cached_order and hasattr(cached_order, 'price') and cached_order.price:
    expected_price = float(cached_order.price.as_double())
# 2. Fallback: signal price from strategy (if available)

slippage = None
if expected_price:
    fill_px = event.last_px.as_double()
    if event.order_side == OrderSide.BUY:
        slippage = fill_px - expected_price  # + = unfavorable
    else:
        slippage = expected_price - fill_px  # + = unfavorable

# Include in INSERT:
sql = """
    INSERT INTO fills (..., slippage)
    VALUES (..., $14)
"""
```

---

## 10. sam performance CLI (ticket 9z3.9.8) 🔴 NEW

### 10.1 Implementation

```python
def _cmd_performance(args: argparse.Namespace) -> int:
    """Display performance stats from Nautilus PortfolioAnalyzer results."""
    # Read from performance_stats PG table
    pool = await get_pg_pool()
    rows = await pool.fetch("""
        SELECT strategy_id, stat_name, stat_value
        FROM performance_stats
        WHERE date >= CURRENT_DATE - $1
        ORDER BY strategy_id, stat_name
    """, args.days)

    if args.json:
        # Output JSON
        print(json.dumps(grouped_stats))
    else:
        # Output formatted table
        print(tabulate(grouped_stats))
```

---

## 11. Complete Ticket Breakdown (Revised)

| # | Ticket ID | Title | Type | Dependencies | Nautilus? |
|---|-----------|-------|------|-------------|-----------|
| 1 | `9z3.9.1` | Dockerfile.services | task ✅ CLOSED | — | — |
| 2 | `9z3.9.2` | sam CLI — ops commands | task ○ | 9z3.9.1 | — |
| 3 | `9z3.9.3` | Cron scheduler + perf cron | task ○ | 9z3.9.2 | — |
| 4 | `9z3.9.4` | Quote fetcher | task ○ | 9z3.9.1 | — |
| 5 | `9z3.9.5` | Deployment capabilities | task ○ | 9z3.9.3 | — |
| 6 | `9z3.9.7` | **LiveRiskEngine integration** | task ○ | 9z3.9.1 | ✅ `LiveRiskEngine` |
| 7 | `9z3.9.9` | **Slippage tracking** | task ○ | 9z3.9.1 | — |
| 8 | `9z3.9.10` | **PositionSnapshotActor** | task ○ | 9z3.9.1 | ✅ Actor pattern |
| 9 | `9z3.9.11` | **PerformanceAnalyzer** | task ○ | 9z3.9.1 | ✅ `PortfolioAnalyzer` |
| 10 | `9z3.9.8` | **sam performance CLI** | task ○ | 9z3.9.2, 9z3.9.11 | — |
| 11 | `9z3.9.6` | [EXIT] Verify | exit ○ | 3,4,5,7,8,9,10 | — |

### 11.1 Build Order (Dependency Graph)

```
9z3.9.1 ✅ (Dockerfile — complete)
  │
  ├── 9z3.9.2 (CLI) ─────────────────────────────────────────────────┐
  │     │                                                              │
  │     ├── 9z3.9.3 (Cron) ──► 9z3.9.5 (Deployment) ─────────────────┤
  │     │                                                              │
  │     └── 9z3.9.8 (sam perf CLI) ────────────────────────────────────┤
  │           │                                                        │
  │           └── 9z3.9.11 (PerformanceAnalyzer)                       │
  │                                                                    │
  ├── 9z3.9.4 (Quote) ────────────────────────────────────────────────┤
  ├── 9z3.9.7 (LiveRiskEngine) ───────────────────────────────────────┤
  ├── 9z3.9.9 (Slippage) ─────────────────────────────────────────────┤
  └── 9z3.9.10 (PositionSnapshot) ────────────────────────────────────┤
                                                                       │
                                                                       ▼
                                                             9z3.9.6 (EXIT)
```

**Parallel tracks available:** 9z3.9.4, 9z3.9.7, 9z3.9.9, 9z3.9.10, 9z3.9.11 can all be built simultaneously after 9z3.9.1.

---

## 12. Key Design Decisions for Phase 8

| # | Decision | Rationale |
|---|----------|-----------|
| D8.1 | **Nautilus `PortfolioAnalyzer` for all statistics** | Rust-backed, battle-tested, zero custom math risk. 17 built-in statistics. |
| D8.2 | **Nautilus `LiveRiskEngine` for pre-trade filtering** | Production-grade rate limiting and notional caps. Already in Nautilus core — just needs wiring. |
| D8.3 | **Performance analysis in sam-services, not sam-trader** | Batch/analytics workload. Not on the hot trading path. Decoupled lifecycle. |
| D8.4 | **`PositionSnapshotActor` uses Nautilus cache facade** | Standard pattern. No custom position tracking — poll `self.cache.positions()`. |
| D8.5 | **`performance_stats` as separate PG table** | Normalized analytics data. Clean separation from operational `fills`/`orders` tables. |
| D8.6 | **Slippage as signed NUMERIC on fills** | Positive = unfavorable, Negative = favorable. Industry standard sign convention. |
| D8.7 | **All new actors reuse existing PG credentials** | No new connection configs needed. POSTGRES_* env vars already in place. |

---

*Last updated: 2026-05-23 — Phase 8 revised with Nautilus-native integrations (PerformanceAnalyzer, LiveRiskEngine, PositionSnapshot, Slippage tracking)*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** Planning — 4 tickets  
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps |
|-----------|-------|------|
| `sam_trader-9z3.9.28` | Restart orchestrator: market-switch docker compose restart | 9z3.7.18, 9z3.1.25 |
| `sam_trader-9z3.9.30` | SOD readiness CLI: sam readiness command | 9z3.7.17 |
| `sam_trader-9z3.9.31` | EOD report CLI: sam report command | 9z3.7.19 |
| `sam_trader-9z3.9.29` | Market-aware cron schedules | 9z3.9.28, 9z3.10.36 |

### Design Notes — Restart Orchestrator
- New `src/sam_trader/services/restart_orchestrator.py`
- Subscribes to Redis `sam:market_switch_request` channel
- Flow: wait for `sam:state_saved` → update `MARKET` in `.env` → `docker compose restart sam-trader` → poll `sam:state_loaded` (60s timeout)
- On failure: rollback MARKET env var, log CRITICAL
- CLI: `sam switch-market US` and `sam switch-market HK`

### Design Notes — SOD Readiness CLI
- `sam readiness --market US|HK [--json]`
- Reads `sam:readiness:{market}:{date}` from Redis
- Pass/fail table with per-check status
- Exit code 0 if all pass, 1 if any fail

### Design Notes — EOD Report CLI
- `sam report --market US|HK [--date YYYY-MM-DD] [--json]`
- Reads `sam:eod_report:{market}:{date}` from Redis + queries PG `daily_reports`
- Sections: P&L, fills, health, positions
- JSON output mode for scripting

### Design Notes — Market-Aware Cron
- US pipeline: 20:30 HKT weekdays (gated on `is_trading_day('US')`)
- HK pipeline: 07:30 HKT weekdays (gated on `is_trading_day('HK')`)
- Backup: 05:00 HKT weekdays (within maintenance window)
- Performance analysis: 02:00 HKT daily
- Log rotation: 03:00 HKT (unchanged)
- All pipeline cron entries check `MarketCalendarService.is_trading_day()` before execution

### Nautilus Types / Patterns Used
- Docker SDK (`docker compose restart`) — standard Docker pattern
- Redis pub/sub for inter-service communication (already in use)
- `MarketCalendarService` (already built in Phase 9)
- PG `daily_reports` table (new)

*Last updated: 2026-05-27 — Dynamic Multi-Market extensions planned*
