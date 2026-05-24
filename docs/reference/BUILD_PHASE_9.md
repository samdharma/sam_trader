# Build Phase 9 — Pre-Market Pipeline

> **Status:** Not Started (revamped 2026-05-24 — Nautilus-native architecture)  
> **Goal:** Nautilus-native pre-market pipeline using broker real-time data feeds (Futu + IB). Gap scanner → AI analysis → risk manager → regime detection → bundle generator → readiness report. Full autonomous pre-market pipeline.  
> **Prev Phase:** [BUILD_PHASE_8.md](./BUILD_PHASE_8.md) — sam-services Container  
> **Next Phase:** [BUILD_PHASE_10.md](./BUILD_PHASE_10.md) — Safety & Dashboard

---

## 1. Architecture Overview (Revamped)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Pre-Market Pipeline (sam-services)                      │
│                    (cron-triggered: 04:30 ET, 08:30 ET, 09:00 ET)         │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 1. PreMarketWatchlist (9z3.10.14)                                    │ │
│  │    └── config/premarket_watchlist.yaml                              │ │
│  │    └── Dynamic: auto-generate from active bundles                   │ │
│  │    └── Static: hand-curated symbol list override                    │ │
│  │    └── US + HK market separation                                    │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 2. QuoteCollectionService (9z3.10.15) — REUSABLE                    │ │
│  │    └── Wraps Nautilus data infrastructure for sam-services          │ │
│  │    └── Creates: MessageBus + Cache + LiveClock                      │ │
│  │    └── Reuses: FutuLiveDataClient (Phase 2)                         │ │
│  │    └── Optional: IB data client for cross-validation                │ │
│  │    └── Returns: dict[InstrumentId, QuoteTick]                       │ │
│  │    └── Used by: GapScanner, sam quote CLI, Regime Detection         │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 3. PreMarketGapScanner (9z3.10.1)                                    │ │
│  │    └── Connects to Futu OpenD via FutuLiveDataClient (reused)       │ │
│  │    └── Subscribes QuoteTick for watchlist symbols                   │ │
│  │    └── Collects real-time quotes for 30-60 seconds                  │ │
│  │    └── Computes: gap_pct = (quote.last - prev_close) / prev_close   │ │
│  │    └── Filters: threshold, blacklist, OTC/ETF, price, volume        │ │
│  │    └── Multi-pass: Pass 1 (04:30) + Pass 2 (08:30) with trends     │ │
│  │    └── Output: Redis sam:gapscan:{date}:{pass}                     │ │
│  │    └── ZERO web scraping — all data from broker feeds               │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 4. AI Scoring Engine (9z3.10.2)                                      │ │
│  │    └── Consumes gap candidates from Redis                           │ │
│  │    └── Evaluates 6 dimensions: gap quality, technical setup,        │ │
│  │        sentiment, liquidity, risk, market context                   │ │
│  │    └── LLM: DeepSeek / Moonshot Kimi K2.6                           │ │
│  │    └── Grades: STRONG_BUY, BUY, HOLD, SKIP                         │ │
│  │    └── Rule-based fast path fallback                                │ │
│  │    └── Output: Per-symbol recommendation + confidence + reasoning   │ │
│  └──────────────────────────────┬──────────────────────────────────────┘ │
│                                 │                                         │
│                     ┌───────────┴───────────┐                             │
│                     ▼                       ▼                             │
│  ┌──────────────────────────┐  ┌──────────────────────────────────────┐ │
│  │ 5. Market Regime         │  │ 6. Risk Manager                       │ │
│  │    Detection (9z3.10.4)  │  │    ├── Monte Carlo Sizer (9z3.10.7)   │ │
│  │    └── HMM classifier    │  │    ├── Pre-trade Checks (9z3.10.8)    │ │
│  │    └── Regime labels:    │  │    └── Portfolio Heat (9z3.10.9)      │ │
│  │      TRENDING, RANGING,  │  │                                        │ │
│  │      VOLATILE, BEARISH   │  │  Consumes AI recommendations           │ │
│  │    └── Uses              │  │  Produces position sizes + risk flags  │ │
│  │      QuoteCollectionSvc  │  │                                        │ │
│  └────────────┬─────────────┘  └──────────────────┬───────────────────┘ │
│               │                                   │                      │
│               └───────────────┬───────────────────┘                      │
│                               ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 7. Pipeline Orchestrator (9z3.10.10 → 10.11 → 10.12)                │ │
│  │    └── Sequential executor: scan → AI → risk → regime → bundles     │ │
│  │    └── Bundle YAML generator → config/bundles.daily.yaml            │ │
│  │    └── Readiness report → console + webhook                         │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │ 8. EXIT: End-to-end pipeline validation (9z3.10.13)                  │ │
│  │    └── Pipeline produces ≥1 valid candidate                         │ │
│  │    └── Bundle YAML passes schema validation                         │ │
│  │    └── Readiness report generated                                   │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Key Architecture Decision: Broker Feeds Over Web Scraping

| Aspect | Old (FinViz Scraping) | New (Nautilus + Broker Feeds) |
|--------|----------------------|-------------------------------|
| Data source | FinViz website (HTML scraping) | Futu OpenD + IB Gateway (protobuf/msgspec) |
| Data delay | 15-20 min | **Real-time** (< 1 second) |
| Quote type | Last price only | Bid, Ask, Last — full L1 |
| Reliability | Fragile (website changes break parser) | Protocol-level (stable API) |
| Multi-source validation | 2-3 websites (same data origin) | **Futu + IB cross-validation** (independent brokers) |
| Volume data | Delayed webpage value | **Real-time TradeTick** from broker |
| Data freshness guarantee | Website-dependent | **ts_event from broker protocol** |
| Architecture consistency | Only web-scraping component in v3 | **100% Nautilus-native** |
| HK market | Separate website | Same API, different venue mapping |

---

## 3. Pre-Discovered Reference — Nautilus Components in sam-services

### 3.1 Lightweight Data Infrastructure (no TradingNode required)

The Phase 8 PerformanceAnalyzer proved Nautilus components work in sam-services. The gap scanner follows the same pattern:

```python
from nautilus_trader.common.component import MessageBus, LiveClock
from nautilus_trader.cache.cache import Cache

# Create in-process data infrastructure
msgbus = MessageBus()
cache = Cache()
clock = LiveClock()

# Reuse Phase 2 FutuLiveDataClient
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.config import FutuDataClientConfig

config = FutuDataClientConfig(
    host="sam-futu-opend",
    port=11111,
    trd_env="SIMULATE",
    trd_market="US",
)

client = FutuLiveDataClient(
    loop=asyncio.get_running_loop(),
    client=None,  # auto-created on connect via FutuClient cache
    msgbus=msgbus,
    cache=cache,
    clock=clock,
    instrument_provider=instrument_provider,
    config=config,
)

await client.connect()
# ... subscribe, collect quotes, disconnect
```

### 3.2 QuoteCollectionService Pattern

```python
from dataclasses import dataclass
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId

@dataclass
class QuoteCollectionResult:
    quotes: dict[InstrumentId, QuoteTick]
    collection_duration_secs: float
    symbols_scanned: int
    symbols_with_quotes: int
    errors: list[str]

class QuoteCollectionService:
    """Reusable Nautilus data client wrapper for sam-services."""

    def __init__(
        self,
        broker: str,  # "FUTU" or "IB"
        host: str,
        port: int,
        watchlist: list[str],  # e.g., ["TSLA.NASDAQ", "AAPL.NASDAQ"]
        collection_period_secs: int = 60,
    ) -> None: ...

    async def collect(self) -> QuoteCollectionResult:
        """Connect, subscribe, collect quotes, disconnect, return results."""
        ...
```

### 3.3 Data Flow: QuoteTick → Gap Computation

```python
# Gap computation
prev_close = get_prev_close(symbol)  # from PG fills or Parquet
quote = result.quotes[instrument_id]
gap_pct = (quote.last.as_double() - prev_close) / prev_close * 100
gap_dollar = quote.last.as_double() - prev_close

# Filter
if abs(gap_pct) < min_gap_pct:
    continue  # below threshold
if symbol in blacklist:
    continue  # excluded
```

### 3.4 Previous Close Sources (priority order)

| Priority | Source | Query |
|----------|--------|-------|
| 1 | PostgreSQL fills table | `SELECT price FROM fills WHERE instrument_id=$1 ORDER BY ts_event DESC LIMIT 1` |
| 2 | Parquet catalog | Read daily bar from `data/catalog/bars/{instrument_id}/` |
| 3 | Futu historical k-line | `ctx.request_history_kline(code, ktype=K_DAY, max_count=1)` |

---

## 4. Ticket Breakdown (Revised)

| # | Ticket ID | Title | Type | Dependencies | Assessment |
|---|-----------|-------|------|-------------|------------|
| 1 | `9z3.10.14` | **PreMarketWatchlist** — config-driven symbol universe | task ○ NEW | — | ✅ Small |
| 2 | `9z3.10.15` | **QuoteCollectionService** — reusable Nautilus data client wrapper | task ○ NEW | — | ✅ Medium |
| 3 | `9z3.10.1` | **PreMarketGapScanner** — Nautilus-native real-time broker scanner | task ○ REVAMPED | 10.14, 10.15 | ✅ Medium |
| 4 | `9z3.10.2` | AI scoring engine — LLM candidate evaluation | task ○ | 10.1 | ✅ Medium |
| 5 | `9z3.10.4` | Market regime detection — HMM + adaptation | task ○ | — (uses QuoteCollector) | ✅ Medium |
| 6 | `9z3.10.7` | Monte Carlo position sizer | task ○ | 10.2 | ✅ Small |
| 7 | `9z3.10.8` | Pre-trade risk checks | task ○ | 10.7 | ✅ Small |
| 8 | `9z3.10.9` | Portfolio heat monitor | task ○ | 10.8 | ✅ Small |
| 9 | `9z3.10.10` | Pipeline sequential executor | task ○ | 10.9, 10.4 | ✅ Small |
| 10 | `9z3.10.11` | Bundle YAML generator | task ○ | 10.10 | ✅ Small |
| 11 | `9z3.10.12` | Readiness report | task ○ | 10.11 | ✅ Small |
| 12 | `9z3.10.13` | [EXIT] Pipeline e2e validation | exit ○ | 10.12 | ✅ Medium |

### 4.1 Build Order (Dependency Graph)

```
                     ┌──────────────────────┐
                     │  9z3.10.14           │
                     │  PreMarketWatchlist  │──────┐
                     └──────────────────────┘      │
                                                   │
                     ┌──────────────────────┐      │
                     │  9z3.10.15           │      │
                     │  QuoteCollector      │──────┤
                     └──────────────────────┘      │
                                                   ▼
                                          ┌──────────────────────┐
                                          │  9z3.10.1            │
                                          │  PreMarketGapScanner │
                                          └──────────┬───────────┘
                                                     │
                                                     ▼
                                          ┌──────────────────────┐
                                          │  9z3.10.2            │
                     ┌───────────────────│  AI Scoring Engine    │
                     │                    └──────────┬───────────┘
                     │                               │
                     │                    ┌──────────┴───────────┐
                     │                    ▼                      ▼
                     │           ┌──────────────┐    ┌──────────────────┐
                     │           │  9z3.10.7    │    │  9z3.10.4        │
                     │           │  MC Sizer    │    │  Regime Detection│
                     │           └──────┬───────┘    └────────┬─────────┘
                     │                  │                      │
                     │                  ▼                      │
                     │           ┌──────────────┐              │
                     │           │  9z3.10.8    │              │
                     │           │  Pre-trade   │              │
                     │           └──────┬───────┘              │
                     │                  │                      │
                     │                  ▼                      │
                     │           ┌──────────────┐              │
                     │           │  9z3.10.9    │              │
                     │           │  Heat Monitor│              │
                     │           └──────┬───────┘              │
                     │                  │                      │
                     │                  └──────────┬───────────┘
                     │                             ▼
                     │                    ┌──────────────────┐
                     │                    │  9z3.10.10       │
                     │                    │  Pipeline Exec   │
                     │                    └────────┬─────────┘
                     │                             │
                     │                             ▼
                     │                    ┌──────────────────┐
                     │                    │  9z3.10.11       │
                     │                    │  Bundle Gen      │
                     │                    └────────┬─────────┘
                     │                             │
                     │                             ▼
                     │                    ┌──────────────────┐
                     │                    │  9z3.10.12       │
                     │                    │  Readiness Rpt   │
                     │                    └────────┬─────────┘
                     │                             │
                     │                             ▼
                     │                    ┌──────────────────┐
                     │                    │  9z3.10.13       │
                     │                    │  [EXIT] P9       │
                     │                    └──────────────────┘
                     │
                     └── (Regime Detection is independent root,
                          can run parallel to AI scoring chain)
```

**Parallel tracks:**
- **Track A:** 10.14 → 10.15 → 10.1 → 10.2 → 10.7 → 10.8 → 10.9 → 10.10 → 10.11 → 10.12 → 10.13
- **Track B:** 10.4 (Regime — independent, uses QuoteCollector, runs parallel to AI chain)
- Both converge at 10.10 (Pipeline Executor)

---

## 5. Key Design Notes

### 5.1 Why Broker Feeds Not Web Scraping

| Reason | Detail |
|--------|--------|
| **Real-time data** | Futu OpenD provides live QuoteTick with < 1 second delay. FinViz is 15-20 min delayed. |
| **Bid/Ask spread** | Broker feeds include full L1 (bid, ask, last). Websites only show last price. |
| **Cross-validation** | Futu + IB are independent brokers. If both report same quote, confidence is high. Two websites may share the same delayed data feed. |
| **Architecture consistency** | Every other v3 component uses Nautilus. The gap scanner should too. |
| **Code reuse** | FutuLiveDataClient (Phase 2, 400+ lines) is already built and tested. Zero new broker connection code. |
| **Subscription management** | FutuSubscriptionManager (Phase 2) tracks quota. Scanner respects limits. |

### 5.2 Pre-Market Timeline (ET)

```
04:30 → Gap Scan Pass 1 (early discovery, 30-60 sec collection)
08:30 → Gap Scan Pass 2 (morning refresh, trend detection)
09:00 → AI Scoring Engine (up to 50 candidates, < 10 min)
09:10 → Risk Manager (Monte Carlo + pre-trade checks)
09:15 → Market Regime Detection (HMM classification)
09:20 → Pipeline Orchestrator (bundle gen + sanity check)
09:25 → Readiness Report (console + webhook)
09:30 → MARKET OPEN — handoff to sam-trader
```

### 5.3 HK Market Timeline (HKT)

Parallel timeline shifted to HKT. Same architecture, different market parameter via venue mapping (HK → HKEX).

### 5.4 QuoteCollectionService Reuse

This service is NOT just for the gap scanner. It is the general-purpose bridge between Nautilus data clients and sam-services:

| Consumer | Purpose |
|----------|---------|
| PreMarketGapScanner (9z3.10.1) | Collect pre-market quotes for gap computation |
| Market Regime Detection (9z3.10.4) | Collect bar data for HMM classifier |
| sam quote CLI (Phase 8 enhancement) | Broker-fallback when Redis cache misses |
| Future: Intraday health monitor | Spot-check quote quality during trading hours |

---

## 6. AI Scoring Engine Context

The AI scoring engine (9z3.10.2) is the bridge between raw gap data and actionable recommendations. With real-time broker data, it receives:

| Input Field | Source (Old) | Source (New) |
|-------------|-------------|--------------|
| gap_pct | FinViz delayed | **Futu real-time QuoteTick** |
| bid / ask | Not available | **Futu L1 quote** |
| volume | Delayed webpage | **Futu real-time TradeTick** |
| prev_close | Web scrape | **PG fills / Parquet** |
| data_source | "FINVIZ_DELAYED" | **"FUTU_REALTIME"** or **"IB_NASDAQ_REALTIME"** |
| cross_validated | No | **Yes** (when Futu + IB both report) |

Full AI spec at `docs/reference/pre-market_req/2. premarket-ai-analysis.md`.

---

## 7. Commonly Used Imports

```python
# Nautilus core (in-process, no TradingNode)
from nautilus_trader.common.component import MessageBus, LiveClock
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import QuoteTick, TradeTick, Bar
from nautilus_trader.model.identifiers import InstrumentId, Venue

# Phase 2 reuse
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.adapters.futu.connection import get_cached_futu_quote_context
from sam_trader.adapters.futu.subscription_manager import FutuSubscriptionManager

# Phase 4 reuse
from sam_trader.adapters.futu.instrument_provider import FutuInstrumentProvider

# Services
from sam_trader.services.quote_collector import QuoteCollectionService, QuoteCollectionResult
from sam_trader.services.gap_scanner import PreMarketGapScanner, GapCandidate
```

---

*Last updated: 2026-05-24 — Complete architecture revamp: web scraping replaced with Nautilus-native broker data feeds. 2 new tickets added (Watchlist + QuoteCollector). GapScanner ticket rewritten.*
