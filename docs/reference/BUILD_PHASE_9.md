# Build Phase 9 — Pre-Market Pipeline

> **Status:** ✅ Complete (all 12 tickets closed incl EXIT 9z3.10.27)  
> **Goal:** Nautilus-native pre-market pipeline using broker real-time data feeds (Futu + IB). Gap scanner → AI analysis → risk manager → regime detection → bundle generator → readiness report. Full autonomous pre-market pipeline.  
> **Prev Phase:** [BUILD_PHASE_8.md](./BUILD_PHASE_8.md) — sam-services Container  
> **Next Phase:** [BUILD_PHASE_10.md](./BUILD_PHASE_10.md) — Safety & Dashboard

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Pre-Market Pipeline (sam-services)                      │
│                    (cron-triggered: 04:30 ET, 08:30 ET, 09:00 ET)         │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 1. PreMarketWatchlist (10.16)                                        │ │
│  │    └── config/premarket_watchlist.yaml                              │ │
│  │    └── Dynamic: auto-generate from active bundles                   │ │
│  │    └── Static: hand-curated symbol list override                    │ │
│  │    └── US + HK market separation                                    │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│  ┌──────────────────────────────┴──────────────────────────────────────┐ │
│  │ 2. QuoteCollectionService (10.17) — REUSABLE                        │ │
│  │    └── Wraps Nautilus data infrastructure for sam-services          │ │
│  │    └── Reuses: FutuLiveDataClient (Phase 2)                         │ │
│  │    └── IB data client supported for cross-validation (Phase 5)      │ │
│  │    └── Returns: dict[InstrumentId, QuoteTick]                       │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 3. PreMarketGapScanner (10.18)                                       │ │
│  │    └── Real-time QuoteTick from Futu OpenD via FutuLiveDataClient   │ │
│  │    └── Computes gaps vs previous close (PG fills / Parquet)         │ │
│  │    └── Filters: threshold, blacklist, OTC/ETF, price, volume        │ │
│  │    └── Multi-pass: Pass 1 (04:30) + Pass 2 (08:30) with trends     │ │
│  │    └── ZERO web scraping — all data from broker feeds               │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                    ┌────────────┴────────────┐                            │
│                    ▼                         ▼                            │
│  ┌──────────────────────────┐  ┌──────────────────────────────────────┐ │
│  │ 4. Market Regime         │  │ 5. AI Scoring Engine (10.20)          │ │
│  │    Detection (10.19)     │  │    └── LLM: DeepSeek / Kimi K2.6     │ │
│  │    └── HMM classifier    │  │    └── 6-dimension scoring            │ │
│  │    └── Parallel track    │  │    └── Grades: STRONG_BUY → SKIP     │ │
│  │    └── Independent root  │  │    └── Rule-based fallback            │ │
│  └────────────┬─────────────┘  └──────────────────┬───────────────────┘ │
│               │                                   │                      │
│               │                    ┌──────────────┴──────────────┐       │
│               │                    ▼                             ▼       │
│               │           ┌──────────────┐    ┌──────────────────┐      │
│               │           │ 6. MC Sizer  │    │  7. Pre-trade    │      │
│               │           │   (10.21)    │───▶│     (10.22)      │      │
│               │           └──────────────┘    └────────┬─────────┘      │
│               │                                        │                 │
│               │                                        ▼                 │
│               │                               ┌──────────────────┐      │
│               │                               │ 8. Heat Monitor  │      │
│               │                               │     (10.23)      │      │
│               │                               └────────┬─────────┘      │
│               │                                        │                 │
│               └────────────────────────────────────────┤                 │
│                                                        ▼                 │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 9. Pipeline Executor (10.24) — merges both tracks                   │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 10. Bundle YAML Generator (10.25) → 11. Readiness (10.26)           │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 12. EXIT: E2E Validation (10.27)                                     │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Ticket Breakdown (Sequential Build Order)

| # | Ticket ID | Title | Type | Dependencies | Ralph Order |
|---|-----------|-------|------|-------------|-------------|
| 1 | `9z3.10.16` | PreMarketWatchlist — config-driven symbol universe | task | — | **1st** |
| 2 | `9z3.10.17` | QuoteCollectionService — reusable Nautilus data client wrapper | task | — | **2nd** (parallel with 16) |
| 3 | `9z3.10.18` | PreMarketGapScanner — Nautilus-native broker data scanner | task | 10.16, 10.17 | **3rd** |
| 4 | `9z3.10.19` | Market Regime Detection — HMM classification | task | — | **4th** (parallel root) |
| 5 | `9z3.10.20` | AI Scoring Engine — LLM candidate evaluation | task | 10.18 | **5th** |
| 6 | `9z3.10.21` | Monte Carlo Position Sizer | task | 10.20 | **6th** |
| 7 | `9z3.10.22` | Pre-trade Risk Checks | task | 10.21 | **7th** |
| 8 | `9z3.10.23` | Portfolio Heat Monitor | task | 10.22 | **8th** |
| 9 | `9z3.10.24` | Pipeline Sequential Executor | task | 10.19, 10.23 | **9th** |
| 10 | `9z3.10.25` | Bundle YAML Generator | task | 10.24 | **10th** |
| 11 | `9z3.10.26` | Readiness Report | task | 10.25 | **11th** |
| 12 | `9z3.10.27` | [EXIT] Pipeline E2E Validation | exit | 10.26 | **12th** |

### 2.1 Dependency Graph

```
10.16 ──┐
10.17 ──┤
        ▼
      10.18 ──► 10.20 ──► 10.21 ──► 10.22 ──► 10.23 ──┐
      10.19 ───────────────────────────────────────────┤
                                                       ▼
                                                     10.24 ──► 10.25 ──► 10.26 ──► 10.27 (EXIT)
```

**Ralph deterministic selection:** When 10.16 and 10.17 are both ready, Ralph picks 10.16 (lower number). Both are independent roots — either build order works. After both complete, 10.18 becomes ready, then 10.20, 10.21, etc. in perfect sequential order.

---

## 3. Pre-Discovered Reference — Nautilus Components in sam-services

### 3.1 Lightweight Data Infrastructure

```python
from nautilus_trader.common.component import MessageBus, LiveClock
from nautilus_trader.cache.cache import Cache

msgbus = MessageBus()
cache = Cache()
clock = LiveClock()
```

### 3.2 QuoteCollectionService Pattern

```python
class QuoteCollectionService:
    def __init__(
        self,
        broker: str,  # "FUTU" or "IB"
        watchlist: list[str],
        host: str | None = None,   # defaults to FUTU_OPEND_HOST or IB_GATEWAY_HOST
        port: int | None = None,   # defaults to FUTU_OPEND_PORT or IB_GATEWAY_PORT
        collection_period_secs: int = 60,
        connection_timeout_secs: int = 10,
        client_id: int = 1,        # IB session ID
    ) -> None: ...

    async def collect(self) -> QuoteCollectionResult:
        """Connect, subscribe, collect, disconnect, return."""
        ...
```

**IB data client supported** — `broker="IB"` wires `InteractiveBrokersLiveDataClientFactory`
with `InteractiveBrokersDataClientConfig` + `InteractiveBrokersInstrumentProviderConfig`.
Graceful `RuntimeError` if `ibapi` is not installed.

### 3.3 HK Watchlist Setup

The pre-market watchlist (`config/premarket_watchlist.yaml`) must contain static
HK symbols because Hong Kong does **not** have a pre-market session, so dynamic
bundle extraction is insufficient.

Required HK symbols (minimum):

```yaml
watchlist:
  HK:
    symbols:
      - "00700.HKEX"    # Tencent
      - "09988.HKEX"    # Alibaba
      - "09618.HKEX"    # JD.com
      - "01810.HKEX"    # Xiaomi
    min_gap_pct: 1.5
    max_candidates: 30
    premarket_only: false   # HK has no pre-market session
```

Set `MARKET=HK` when scanning the HK market. The pipeline schedule is read
from `market_config.yaml` (`premarket_pipeline_time`). The gap scanner emits
per-stage diagnostic counts (`quote_collected=N`, `prev_close_success=N`,
`raw_gaps=N`, `after_filters=N`) for observability.

### 3.3 Key Nautilus Types

```python
from nautilus_trader.model.data import QuoteTick, TradeTick, Bar
from nautilus_trader.model.identifiers import InstrumentId, Venue
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.services.quote_collector import QuoteCollectionService
```

---

## 4. Phase 10/11 Gating

| Phase | Blocked By |
|-------|-----------|
| Phase 10 (9z3.11.1, 9z3.11.2) | Phase 9 EXIT (9z3.10.27) |
| Phase 11 (9z3.12.1) | Phase 10 EXIT (9z3.11.5) |

---

*Last updated: 2026-05-24 — Renumbered: 12 sequential tickets 10.16–10.27 matching build order.*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** Planning — 3 tickets  
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps |
|-----------|-------|------|
| `sam_trader-9z3.10.37` | Dual-broker gap scanner: Futu primary + IB cross-validation | 9z3.6.15, 9z3.2.5 |
| `sam_trader-9z3.10.36` | Market-aware pipeline scheduling | 9z3.2.5 |
| `sam_trader-9z3.10.35` | Pipeline → BundleController integration via Redis | 9z3.10.36, 9z3.8.12 |

### Design Notes — Dual-Broker Scanner
- New `src/sam_trader/services/dual_broker_scanner.py`
- New `config/gap_scanner.yaml` with per-market primary/secondary broker config
- Wraps two `QuoteCollectionService` instances (Futu + IB)
- US market: runs both in parallel via `asyncio.gather()`, cross-validates mid-price discrepancies > threshold_pct (default 1.0%)
- HK market: Futu only (IB QuoteCollectionService not created — IB disabled for HK)
- Results include `cross_validated` flag and `cross_validation_note` per candidate

### Design Notes — Market-Aware Pipeline
- Removes `PIPELINE_MARKET` env var dependency
- Pipeline reads `MARKET` env var or `market_config.yaml` to determine active market
- US pipeline: gap scan at 08:30 ET (converted to HKT dynamically via `zoneinfo` for DST)
- HK pipeline: gap scan at 07:30 HKT
- Cron entries call `python -m sam_trader.services.pipeline --market US|HK`
- Holiday check via `MarketCalendarService` before execution
- CLI: `sam pipeline --market US` and `sam pipeline --market HK`

### Design Notes — Pipeline → BundleController Integration
- After pipeline generates approved bundles, publish each to Redis channel `sam:bundle:load` as JSON
- Publish `sam:bundle:load_complete {market, count}` after all bundles sent
- `BundleController.subscribe()` in sam-trader receives and calls `create_strategy_from_config()`
- `bundles.yaml` still read at node startup as initial strategy set (fallback)
- Error handling: if Controller fails to load a bundle, log ERROR, continue with remaining

### Nautilus Types / Patterns Used
- `QuoteCollectionService` — already built, wraps `FutuLiveDataClient` / `InteractiveBrokersDataClient`
- `PreMarketGapScanner` — already built, extended with cross-validation
- `asyncio.gather()` — parallel quote collection (standard async pattern)
- Redis pub/sub — inter-service communication between sam-services and sam-trader
- `zoneinfo` — DST-aware timezone conversion (Python 3.12 standard library)
- `MarketCalendarService` — already built in Phase 9

*Last updated: 2026-05-27 — Dynamic Multi-Market extensions planned*
