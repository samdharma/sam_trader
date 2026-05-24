# Build Phase 2 — Futu Market Data Adapter

> **Status:** ✅ Complete  
> **Goal:** `FutuLiveDataClient` streams QuoteTick, TradeTick, Bar, OrderBookDelta to Nautilus message bus. Subscription quota manager tracks usage.  
> **Prev Phase:** [BUILD_PHASE_1.md](./BUILD_PHASE_1.md) — Configuration & Bootstrap  
> **Next Phase:** [BUILD_PHASE_3.md](./BUILD_PHASE_3.md) — Futu Execution Adapter  
> **Feature Ticket:** `sam_trader-9z3.3` (closed)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    FutuLiveDataClient                          │
│  (subclass nautilus_trader.live.data_client.LiveMarketData)    │
├──────────────────────────────────────────────────────────────┤
│  Connection Layer                                              │
│    └── FutuClient (connection.py) — cached OpenQuoteContext  │
│    └── Shared per (host, port, trd_env) key                   │
│    └── Async connect, disconnect, context lifecycle           │
├──────────────────────────────────────────────────────────────┤
│  Push Handlers → asyncio.Queue → _run_push_loop               │
│    ├── StockQuoteHandler  → QuoteTick                        │
│    ├── TickerHandler      → TradeTick                        │
│    ├── CurKlineHandler    → Bar (per BarType)                │
│    └── OrderBookHandler   → OrderBookDelta                    │
├──────────────────────────────────────────────────────────────┤
│  Subscription Lifecycle                                        │
│    ├── subscribe()   → register with Futu + track in sets    │
│    ├── unsubscribe() → deregister + cleanup tracking         │
│    └── _restore_subscriptions() → reconnect resilience       │
├──────────────────────────────────────────────────────────────┤
│  Historical Backfill                                           │
│    └── _backfill_bars() → request_history_kline on connect   │
├──────────────────────────────────────────────────────────────┤
│  Subscription Quota Manager (subscription_manager.py)          │
│    ├── Per-DataType limits (QUOTE:100, KLINE:100, OB:50)     │
│    ├── Priority bundle instruments over ad-hoc               │
│    ├── Release unused after 1min idle                        │
│    └── WARNING at 80%, ERROR at 95%                          │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Module Map

| Module | Path | Purpose |
|--------|------|---------|
| `connection.py` | `src/sam_trader/adapters/futu/connection.py` | `FutuClient` wrapper with cached `OpenQuoteContext` / `OpenSecTradeContext` per `(host, port, trd_env)` key. Monkey-patch for `is_async_connect`. Disconnect invalidation. Context lifecycle. |
| `constants.py` | `src/sam_trader/adapters/futu/constants.py` | All Futu↔Nautilus enum mappings and constants. Venue, KLType→BarType, SecurityType→InstrumentClass, OrderType, Direction, OrderStatus, TrdMarket, TrdEnv, PositionSide, TimeInForce. |
| `config.py` | `src/sam_trader/adapters/futu/config.py` | `FutuDataClientConfig`, `FutuExecClientConfig` — frozen dataclasses inheriting from Nautilus `LiveDataClientConfig` / `LiveExecClientConfig`. |
| `parsing/market_data.py` | `src/sam_trader/adapters/futu/parsing/market_data.py` | Push handler subclasses + parsers: StockQuoteHandler→QuoteTick, CurKlineHandler→Bar, TickerHandler→TradeTick, OrderBookHandler→OrderBookDelta. `security_to_instrument_id()`. |
| `data.py` | `src/sam_trader/adapters/futu/data.py` | `FutuLiveDataClient` — push-loop architecture, subscription lifecycle, reconnect restoration, historical backfill. |
| `subscription_manager.py` | `src/sam_trader/adapters/futu/subscription_manager.py` | Quota tracking per `DataType`. Priority-based allocation. Idle release. Thread-safe. |
| `common.py` | `src/sam_trader/adapters/futu/common.py` | Symbology helpers: `instrument_id_to_futu_security()`, `futu_security_to_instrument_id()`. |

---

## 3. Pre-Discovered Reference — Nautilus Types

### 3.1 LiveMarketDataClient Base Class

```python
from nautilus_trader.live.data_client import LiveMarketDataClient

class FutuLiveDataClient(LiveMarketDataClient):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: OpenQuoteContext | None,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: InstrumentProvider,
        config: FutuDataClientConfig,
        subscription_manager: FutuSubscriptionManager | None = None,
    ) -> None:
        ...
```

**Required abstract methods to implement:**
- `_subscribe_quote_ticks(command: SubscribeQuoteTicks)`
- `_subscribe_trade_ticks(command: SubscribeTradeTicks)`
- `_subscribe_bars(command: SubscribeBars)`
- `_subscribe_order_book_deltas(command: SubscribeOrderBook)`
- Corresponding `_unsubscribe_*` methods

### 3.2 Data Types

```python
from nautilus_trader.model.data import QuoteTick, TradeTick, Bar, OrderBookDelta, BarType, BarSpecification
from nautilus_trader.data.messages import SubscribeBars, SubscribeQuoteTicks, SubscribeTradeTicks, SubscribeOrderBook
```

### 3.3 Identifiers

```python
from nautilus_trader.model.identifiers import InstrumentId, Venue, ClientId, Symbol
from nautilus_trader.model.enums import BarAggregation, PriceType, InstrumentClass
```

---

## 4. Pre-Discovered Reference — Futu SDK

### 4.1 Quote Context

```python
from futu import OpenQuoteContext, RET_OK, SubType

ctx = OpenQuoteContext(host=config.host, port=config.port)
```

**Key methods:**
- `ctx.subscribe(code_list, subtype_list)` → `(ret_code, data)`
- `ctx.unsubscribe(code_list, subtype_list)` → `(ret_code, data)`
- `ctx.unsubscribe_all()` → cleanup
- `ctx.set_handler(handler)` → register push handler
- `ctx.request_history_kline(code, ktype=..., max_count=100)` → `(ret_code, data, page_req_key)`

### 4.2 SubType Constants

| SubType | Value | Data Stream |
|---------|-------|-------------|
| `SubType.QUOTE` | `"QUOTE"` | Real-time bid/ask/last |
| `SubType.TICKER` | `"TICKER"` | Trade ticks |
| `SubType.K_1M` through `SubType.K_YEAR` | `"K_1M"` etc. | K-line (bar) data |
| `SubType.ORDER_BOOK` | `"ORDER_BOOK"` | Level 2 order book |

### 4.3 Push Handler Base Classes

```python
from futu import (
    StockQuoteHandlerBase,
    TickerHandlerBase,
    CurKlineHandlerBase,
    OrderBookHandlerBase,
)
```

Handler pattern: subclass → override `on_recv_rsp(rsp_pb)` → parse protobuf → push to asyncio.Queue.

---

## 5. Implementation Patterns

### 5.1 Push-Loop Architecture

```
Futu OpenD push ──► StockQuoteHandler.on_recv_rsp()
                         │
                         ▼
                    asyncio.Queue.put(item)
                         │
                         ▼
               _run_push_loop()  ← asyncio.Task
                    │
                    ▼
               _handle_data(item)
                    │
                    ▼
          MessageBus (QuoteTick, TradeTick, Bar, OrderBookDelta)
```

### 5.2 Shared Connection Pattern

One `FutuClient` per `(host, port, trd_env)` tuple. Cached globally. Both data client (Phase 2) and execution client (Phase 3) share the same underlying `OpenQuoteContext`.

### 5.3 Subscription Restoration

On reconnect:
1. Iterate all tracked subscription sets
2. Re-call `ctx.subscribe()` for each
3. Re-register K-line handlers
4. Run historical backfill for bars

### 5.4 Bar Type Mapping

`_BAR_SPEC_TO_FUTU_SUBTYPE` dict maps `(step, BarAggregation)` tuples to Futu SubType strings:

| Bar Spec | Futu SubType |
|----------|-------------|
| 1-MINUTE | `K_1M` |
| 3-MINUTE | `K_3M` |
| 5-MINUTE | `K_5M` |
| 10-MINUTE | `K_10M` |
| 15-MINUTE | `K_15M` |
| 30-MINUTE | `K_30M` |
| 1-HOUR | `K_60M` |
| 2-HOUR | `K_120M` |
| 4-HOUR | `K_240M` |
| 1-DAY | `K_DAY` |
| 1-WEEK | `K_WEEK` |
| 1-MONTH | `K_MON` |
| 1-YEAR | `K_YEAR` |

---

## 6. Subscription Quota Manager

### 6.1 Limits (Futu OpenD Hard Caps)

| Data Type | Max Subscriptions |
|-----------|-------------------|
| QUOTE | 100 |
| TRADE_TICK | 100 |
| KLINE | 100 |
| ORDER_BOOK | 50 |

### 6.2 Priority System

1. **Bundle-flagged instruments** — always accepted
2. **Ad-hoc subscriptions** — accepted if quota available
3. **80% warning**: log WARNING
4. **95% error**: log ERROR, reject new subscriptions

### 6.3 Idle Release

Subscriptions unused for 60 seconds are auto-released. Bundle-flagged instruments are exempt.

---

## 7. Symbology Mapping

```python
# Futu code format: "MARKET.SYMBOL"
# Nautilus format: Symbol("SYMBOL") @ Venue("VENUE")

# Forward: TSLA.NASDAQ → US.TSLA
def instrument_id_to_futu_security(instrument_id: InstrumentId) -> str:
    venue = instrument_id.venue.value  # "NASDAQ"
    market = FUTU_NAUTILUS_VENUE_REVERSE.get(Venue(venue), "US")
    return f"{market}.{instrument_id.symbol.value}"

# Reverse: US.AAPL → AAPL.NASDAQ
def security_to_instrument_id(security: str) -> InstrumentId:
    market, symbol = security.split(".", 1)
    venue = FUTU_TO_NAUTILUS_VENUE.get(market, NASDAQ_VENUE)
    return InstrumentId(Symbol(symbol), venue)
```

**Venue mapping (Futu market → Nautilus):**
| Futu Market | Nautilus Venue |
|-------------|---------------|
| HK | HKEX |
| US | NASDAQ |
| SH | SSE |
| SZ | SZSE |

---

## 8. Ticket Breakdown

| Ticket | Title | Scope | Status |
|--------|-------|-------|--------|
| `sam_trader-9z3.3.1` | Port Futu connection manager from v2 | `connection.py` — context caching, async connect, disconnect | ✅ Closed |
| `sam_trader-9z3.3.2` | Futu constants — venue definitions, enum mappings, type maps | `constants.py` — all Futu↔Nautilus mappings | ✅ Closed |
| `sam_trader-9z3.3.3` | Futu config dataclasses | `config.py` — `FutuDataClientConfig`, `FutuExecClientConfig` | ✅ Closed |
| `sam_trader-9z3.3.4` | Futu parsing module — market data | `parsing/market_data.py` — handlers + parsers | ✅ Closed |
| `sam_trader-9z3.3.5` | FutuLiveDataClient | `data.py` — push-loop, subscriptions, backfill | ✅ Closed |
| `sam_trader-9z3.3.6` | Futu subscription quota manager | `subscription_manager.py` — quota, priority, idle release | ✅ Closed |
| `sam_trader-9z3.3.7` | [EXIT] Market data subscription → QuoteTick flow | Integration test: subscribe → tick on bus → unsubscribe | ✅ Closed |

---

## 9. Commonly Used Imports

```python
# Nautilus core
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import QuoteTick, TradeTick, Bar, OrderBookDelta, BarType
from nautilus_trader.data.messages import (
    SubscribeBars, SubscribeQuoteTicks, SubscribeTradeTicks, SubscribeOrderBook,
    UnsubscribeBars, UnsubscribeQuoteTicks, UnsubscribeTradeTicks, UnsubscribeOrderBook,
)
from nautilus_trader.model.identifiers import ClientId, InstrumentId, Venue

# Futu SDK
from futu import OpenQuoteContext, RET_OK, SubType
from futu import StockQuoteHandlerBase, TickerHandlerBase, CurKlineHandlerBase, OrderBookHandlerBase

# Internal
from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.adapters.futu.connection import get_cached_futu_quote_context
from sam_trader.adapters.futu.constants import FUTU_VENUE
from sam_trader.adapters.futu.common import instrument_id_to_futu_security
from sam_trader.adapters.futu.parsing.market_data import (
    StockQuoteHandler, CurKlineHandler, TickerHandler, OrderBookHandler,
    parse_futu_bars,
)
from sam_trader.adapters.futu.subscription_manager import FutuSubscriptionManager
```

---

*Last updated: 2026-05-24 — Created from gap audit; Phase 2 implemented 2026-05-20*
