# Build Phase 4 — Futu Instrument Provider & TradingNode Integration

> **Status:** ✅ Complete (all 6 tickets closed incl EXIT 9z3.5.6)  
> **Goal:** `FutuInstrumentProvider` resolves symbols. Factories wired into TradingNode. Futu bundles loadable. Full Futu-only TradingNode operational.  
> **Prev Phase:** [BUILD_PHASE_3.md](./BUILD_PHASE_3.md) — Futu Execution Adapter  
> **Next Phase:** [BUILD_PHASE_5.md](./BUILD_PHASE_5.md) — IBKR Adapter Re-integration

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      TradingNode (main.py)                    │
├──────────────────────────────────────────────────────────────┤
│  Factories                                                    │
│    ├── FutuLiveDataClientFactory  →  FutuLiveDataClient      │
│    └── FutuLiveExecClientFactory  →  FutuLiveExecutionClient │
├──────────────────────────────────────────────────────────────┤
│  Instrument Provider                                          │
│    └── FutuInstrumentProvider  →  get_static_info → Equity   │
│                                                      Option  │
│                                                      Future  │
├──────────────────────────────────────────────────────────────┤
│  Bundle Loader                                                │
│    └── venue: FUTU  →  instrument_id: "TSLA.NASDAQ"         │
│                       →  maps to Futu "US.TSLA"              │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — Nautilus Types

### 2.1 InstrumentProvider

```python
from nautilus_trader.common.providers import InstrumentProvider

class FutuInstrumentProvider(InstrumentProvider):
    async def load_all_async(self, filters: dict | None = None) -> None:
        ...

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        ...
```

### 2.2 Instrument Types

```python
from nautilus_trader.model.instruments import Equity, FuturesContract, OptionContract
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity, Currency
```

**Equity constructor fields:**
- `instrument_id: InstrumentId`
- `raw_symbol: Symbol`
- `currency: Currency`
- `price_precision: int`
- `size_precision: int`
- `price_increment: Price`
- `size_increment: Quantity`
- `lot_size: Quantity | None`
- `max_quantity: Quantity | None`
- `min_quantity: Quantity | None`
- `max_notional: Money | None`
- `min_notional: Money | None`
- `margin_init: Decimal`
- `margin_maint: Decimal`
- `maker_fee: Decimal`
- `taker_fee: Decimal`
- `ts_event: int`
- `ts_init: int`

### 2.3 Live Client Factories

```python
from nautilus_trader.system.kernel import NautilusKernel
from nautilus_trader.trading.trader import Trader

# Factory signature (data):
def create_data_client(
    loop: asyncio.AbstractEventLoop,
    client_id: ClientId,
    venue: Venue | None,
    oms_type: OmsType,
    account_id: AccountId | None,
    account_type: AccountType,
    base_currency: Currency | None,
    msgbus: MessageBus,
    cache: Cache,
    clock: LiveClock,
    logger: Logger,
    instrument_provider: InstrumentProvider,
    config: LiveDataClientConfig,
) -> LiveMarketDataClient:
    ...
```

### 2.4 TradingNode Wiring

```python
from nautilus_trader.trading.node import TradingNode
from nautilus_trader.system.config import NautilusConfig, TradingNodeConfig

node = TradingNode(config=trading_node_config)
node.add_data_client_factory("FUTU", FutuLiveDataClientFactory)
node.add_exec_client_factory("FUTU", FutuLiveExecClientFactory)
```

---

## 3. Pre-Discovered Reference — Futu SDK

### 3.1 Static Info Query

```python
from futu import OpenQuoteContext

ctx = OpenQuoteContext(host=..., port=...)
ret, data = ctx.get_static_info(code_list=["US.AAPL", "HK.00700"])
# ret == 0 → success
# data is DataFrame with columns:
#   code, name, lot_size, stock_type, stock_child_type, 
#   base_currency, exchange, listing_date, stock_id, 
#   delisting, stock_owner
```

### 3.2 Market State / Basic Info

```python
ret, data = ctx.get_stock_basicinfo(market=Market.US, stock_type=SecurityType.STOCK)
# Returns DataFrame with:
#   code, name, lot_size, stock_type, stock_child_type,
#   base_currency, exchange, listing_date
```

### 3.3 Precision from Spread

Futu does not expose tick size directly. Derive from quote spread or use a default table:

```python
# US equities: typically 0.01 (2 decimals)
# HK equities: typically 0.001 (3 decimals) for most, 0.01 for some
_PRECISION_MAP: dict[str, int] = {
    "US": 2,
    "HK": 3,
    "SH": 2,
    "SZ": 2,
}
```

---

## 4. Implementation Patterns

### 4.1 Shared Client in Factories

One `FutuClient` instance per `(host, port, trd_env)` tuple. Both data and exec factories reference the same shared client.

```python
_client_cache: dict[tuple[str, int, str], FutuClient] = {}

def _get_shared_client(config: FutuDataClientConfig | FutuExecClientConfig) -> FutuClient:
    key = (config.host, config.port, config.trd_env)
    if key not in _client_cache:
        _client_cache[key] = FutuClient(host=config.host, port=config.port, ...)
    return _client_cache[key]
```

### 4.2 Instrument Provider Caching

The base `InstrumentProvider` already maintains `_instruments: dict[InstrumentId, Instrument]`. Just call `self.add(instrument)` after parsing.

### 4.3 Bundle Venue Validation

```python
# In bundle_loader.py
VALID_VENUES = {"FUTU", "IB"}

if bundle["venue"] not in VALID_VENUES:
    raise ValueError(f"Unknown venue: {bundle['venue']}")
```

### 4.4 Symbology Mapping

```python
def nautilus_to_futu_code(instrument_id: InstrumentId) -> str:
    """TSLA.NASDAQ → US.TSLA"""
    symbol = instrument_id.symbol.value
    venue = instrument_id.venue.value
    market = _VENUE_TO_FUTU_MARKET.get(venue, "US")
    return f"{market}.{symbol}"
```

---

## 5. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p4-parsing-inst` | Futu parsing module: instruments | `parsing/instruments.py` — Equity, Option, Future parsers | ✅ Well-scoped (~40-50 steps) |
| `sam-p4-provider` | FutuInstrumentProvider | `instrument_provider.py` — `load_all_async`, `load_ids_async`, caching | ✅ Well-scoped (~50-60 steps) |
| `sam-p4-factories` | Futu factories | `factories.py` — shared client pattern, factory classes | ✅ Well-scoped (~40-50 steps) |
| `sam-p4-main-wire` | Wire Futu factories into main.py | Register factories in `build_trading_node()` | ✅ Small (~20-30 steps) |
| `sam-p4-bundle` | Bundle support for Futu venue | Extend `bundle_loader.py`, update `bundles.example.yaml` | ✅ Small (~20-30 steps) |
| `sam-p4-exit-dual` | Exit: Futu-only TradingNode | Integration test: full node start → data flow | ✅ Well-scoped (~40-50 steps) |

**No decomposition needed for Phase 4.** All tickets are within a healthy step budget.

---

## 6. Commonly Used Imports

```python
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.instruments import Equity, FuturesContract, OptionContract
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity, Currency, Money
from nautilus_trader.system.kernel import NautilusKernel

from sam_trader.adapters.futu.parsing.market_data import security_to_instrument_id
from sam_trader.adapters.futu.connection import FutuClient
from sam_trader.adapters.futu.config import FutuDataClientConfig, FutuExecClientConfig
```

---

---

## 7. Ticket Summary

| Ticket | Title | Status |
|--------|-------|--------|
| `sam_trader-9z3.5.1` | Futu parsing module: instruments | ✅ Closed |
| `sam_trader-9z3.5.2` | FutuInstrumentProvider | ✅ Closed |
| `sam_trader-9z3.5.3` | Futu factories | ✅ Closed |
| `sam_trader-9z3.5.4` | Wire Futu factories into main.py | ✅ Closed |
| `sam_trader-9z3.5.5` | Bundle support for Futu venue | ✅ Closed |
| `sam_trader-9z3.5.6` | [EXIT] Futu-only TradingNode | ✅ Closed |

---

*Last updated: 2026-05-24 — Status updated to Complete during gap audit; ticket summary added*
