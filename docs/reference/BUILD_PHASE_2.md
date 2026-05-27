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

### 2.1 Version Alignment Note

The Futu OpenD binary version **must** match the `futu-api` SDK version exactly.
A mismatch causes a SHA protocol handshake failure on `proto_id:1001`:

```
init connect fail: conn=0(1) msg=proto_id:1001 conn_id:0 check sha error!
```

**Keep these three values in sync:**
- `ARG FUTU_OPEND_VER` in `docker/Dockerfile.futu-opend`
- `futu-api==X.Y.Z` in `docker/requirements.txt` and `pyproject.toml`
- Default fallback in `docker/futu-opend/start.py`

Both `sam-trader` and `sam-services` receive `FUTU_OPEND_VER` via `docker-compose.yml`
so that `connection.py` can log both SDK and OpenD versions on every connect.

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

### 6.4 Keep-Alive and Reconnect Resilience

**Problem:** Futu OpenD enforces an idle timeout of ~3600s. During pre-market hours with no live data, the connection is closed with `reason=RemoteClose`.

**Solution:**

1. **Keep-alive task** (`FutuLiveDataClient._run_keep_alive`):
   - asyncio task inside `LiveMarketDataClient`, not a separate thread
   - Calls `OpenQuoteContext.query_subscription()` every `keep_alive_interval_secs`
   - Default interval: 1800s (configurable via `FutuDataClientConfig.keep_alive_interval_secs`)
   - Skips when context is not `READY`

2. **RemoteClose handling** (`_FutuDisconnectHandler.on_recv_rsp`):
   - Explicitly detects `notify_type="GTW_EVENT"` with `sub_type="RemoteClose"`
   - Also checks `data.get("reason") == "RemoteClose"` for robustness
   - Invalidates cached context immediately

3. **Structured disconnect logging**:
   - `_FutuDisconnectHandler` logs: `event=disconnect reason=<reason> duration_seconds=<float> is_trade=<bool>`
   - `FutuLiveDataClient._on_futu_disconnect()` stores `_disconnect_time` and `_disconnect_reason`
   - On reconnect, `_connect()` logs: `event=reconnect reason=<reason> reconnect_time_seconds=<float>`

4. **Reconnect recovery** (in `FutuLiveDataClient._connect()`):
   - Fetches fresh context from shared cache if old one is stale
   - Re-pushes pre-loaded instruments to Nautilus cache via `self._handle_data(instrument)`
   - Restores all tracked subscriptions (`_restore_subscriptions()`)
   - Backfills historical bars (`_backfill_bars()`)
   - Restarts keep-alive task

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

---

## 10. Post-Deployment Fixes (2026-05-25)

> **Discovered during sandbox paper-trading deployment.** Three gaps found in the Futu
> adapter → TradingNode integration that prevented strategies from finding instruments
> and routing subscriptions correctly.

### 10.1 `FutuDataClientConfig.load_ids` Missing

**Problem:** `FutuLiveDataClientFactory` created `InstrumentProviderConfig()` with no
`load_ids`, so instruments were never pre-loaded from Futu. Strategies failed with
`Could not find instrument for TSLA.NASDAQ` on start.

**Fix — `config.py`:** Added `load_ids: frozenset | None = None` field:

```python
class FutuDataClientConfig(LiveDataClientConfig, frozen=True):
    # ...
    load_ids: frozenset | None = None
```

**Fix — `factories.py`:** Pass `load_ids` through to `InstrumentProviderConfig`:

```python
load_ids = getattr(config, "load_ids", None)
instrument_provider = FutuInstrumentProvider(
    quote_context=quote_ctx,
    config=InstrumentProviderConfig(load_ids=load_ids),
)
```

### 10.2 Instruments Not Pushed to Nautilus Cache

**Problem:** Even with `load_ids` set, `InstrumentProvider.add()` only stores
instruments in the provider's local dict — they are never pushed to the Nautilus
cache. Strategies still fail because `cache.instrument(id)` returns `None`.

**Fix — `data.py`:** In `FutuLiveDataClient._connect()`, after `load_ids_async()`
completes, push each instrument via `self._handle_data(instrument)` to route it
through the data pipeline (MessageBus → DataEngine → Cache):

```python
async def _connect(self) -> None:
    # ... existing connection code ...
    if self._instrument_provider is not None:
        load_ids = getattr(self._config, "load_ids", None)
        if load_ids:
            await self._instrument_provider.load_ids_async(list(load_ids))
            for iid in load_ids:
                instrument = self._instrument_provider.find(iid)
                if instrument is not None:
                    self._handle_data(instrument)  # Push to Nautilus cache
```

### 10.3 Venue Routing — `NASDAQ` Not Mapped to `FUTU` Client

**Problem:** The `FutuLiveDataClient` is registered with `venue=FUTU_VENUE` ("FUTU"),
but strategies request data for exchange venues like `NASDAQ`. The DataEngine returned
`Cannot execute command: no data client configured for NASDAQ`.

**Fix — `main.py`:** Add explicit venue routing on the `FutuDataClientConfig` so the
DataEngine maps NASDAQ/NYSE/HKEX requests to the Futu client:

```python
data_clients["FUTU"] = FutuDataClientConfig(
    # ...
    routing=RoutingConfig(venues={"NASDAQ", "NYSE", "HKEX"}),
)
```

---

*Last updated: 2026-05-25 — Added Post-Deployment Fixes from sandbox paper-trading*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** ✅ Complete — 1 ticket  
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps |
|-----------|-------|------|
| `sam_trader-9z3.3.11` | Futu: verify per-market connection context coexistence | 9z3.2.5 | ✅ Closed |

### Design Notes
- Verification ticket. Connection caching already keys trade contexts by `trd_market` (see `connection.py` `get_cached_futu_trade_context` which uses `(host, port, trade_env, market_str)` cache key)
- Quote contexts keyed by `(host, port, trade_env)` — verify this is sufficient for multi-market (different markets share same quote context since OpenD serves all markets from one connection)
- Integration test: connect to HK market → verify HK instruments resolve. Connect to US market → verify US instruments resolve
- No code changes expected — verification + integration test only
- If issues found, fix in this ticket

### Nautilus Types / Patterns Used
- `OpenQuoteContext` — single connection serves all markets
- `OpenSecTradeContext` — per-market trade context (filtered by `TrdMarket`)
- Existing `get_cached_futu_quote_context()` / `get_cached_futu_trade_context()`

*Last updated: 2026-05-27 — Dynamic Multi-Market extensions planned*
