# Build Phase 3 — Futu Execution Adapter

> **Status:** In Progress (1 of 4 sub-tasks complete)  
> **Goal:** `FutuLiveExecutionClient` submits/modifies/cancels orders. OrderFilled events flow to message bus. Account auto-discovery.  
> **Prev Phase:** [BUILD_PHASE_2.md](./BUILD_PHASE_2.md) — Futu Market Data Adapter  
> **Next Phase:** [BUILD_PHASE_4.md](./BUILD_PHASE_4.md) — Futu Instrument Provider & TradingNode Integration

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    FutuLiveExecutionClient                   │
│  (subclass nautilus_trader.live.execution_client.LiveExec)   │
├─────────────────────────────────────────────────────────────┤
│  Connection Layer                                            │
│    ├── OpenSecTradeContext (futu-api)                        │
│    ├── unlock_trade() — trade unlock on connect              │
│    └── get_acc_list() — account auto-discovery               │
├─────────────────────────────────────────────────────────────┤
│  Order Methods                                               │
│    ├── _submit_order() → place_order                         │
│    ├── _modify_order() → modify_order                        │
│    └── _cancel_order() → cancel_order                        │
├─────────────────────────────────────────────────────────────┤
│  Push Handlers (asyncio.Queue → _run_push_loop)              │
│    ├── TradeOrderHandler → OrderStatusReport → msg bus       │
│    └── TradeDealHandler  → FillReport        → msg bus       │
├─────────────────────────────────────────────────────────────┤
│  Reconciliation                                              │
│    └── Position reconciliation on connect                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Existing Modules (from Phase 2)

| Module | Path | Purpose |
|--------|------|---------|
| `connection.py` | `src/sam_trader/adapters/futu/connection.py` | `FutuClient` wrapper with `OpenQuoteContext` / `OpenSecTradeContext` lifecycle |
| `constants.py` | `src/sam_trader/adapters/futu/constants.py` | Venue mappings, enum mappings, KLType→BarType, order status constants |
| `config.py` | `src/sam_trader/adapters/futu/config.py` | `FutuDataClientConfig`, `FutuExecClientConfig` (frozen msgspec Struct) |
| `parsing/market_data.py` | `src/sam_trader/adapters/futu/parsing/market_data.py` | `security_to_instrument_id()`, quote/trade/bar/orderbook parsers |
| `parsing/orders.py` | `src/sam_trader/adapters/futu/parsing/orders.py` | `TradeOrderHandler`, `TradeDealHandler`, `parse_futu_position_to_report` |
| `data.py` | `src/sam_trader/adapters/futu/data.py` | `FutuLiveDataClient` — push-loop, subscription lifecycle |
| `subscription_manager.py` | `src/sam_trader/adapters/futu/subscription_manager.py` | Quota tracking per `DataType` |

---

## 3. Pre-Discovered Reference — Nautilus Types

### 3.1 Execution Client Base Class

```python
from nautilus_trader.live.execution_client import LiveExecutionClient

class FutuLiveExecutionClient(LiveExecutionClient):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client_id: ClientId,
        venue: Venue,
        oms_type: OmsType,
        account_id: AccountId,
        account_type: AccountType,
        base_currency: Currency | None,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        logger: Logger,
        instrument_provider: InstrumentProvider,
        config: FutuExecClientConfig,
    ) -> None:
        ...
```

**Required abstract methods to implement:**
- `_submit_order(order: Order)` — submit to venue
- `_modify_order(order: Order)` — modify existing order
- `_cancel_order(order: Order)` — cancel existing order

**Optional overrides:**
- `connect()` / `disconnect()` — connection lifecycle
- `_run_after_connection()` — post-connect logic (account discovery, position reconciliation)

### 3.2 Order Report Types

```python
from nautilus_trader.execution.reports import (
    OrderStatusReport,   # account_id, instrument_id, venue_order_id, order_side, order_type, etc.
    FillReport,          # account_id, instrument_id, venue_order_id, trade_id, order_side, last_qty, last_px, commission
    PositionStatusReport, # account_id, instrument_id, position_side, quantity, avg_px_open
)
```

**Key `OrderStatusReport` fields:**
- `account_id: AccountId`
- `instrument_id: InstrumentId`
- `venue_order_id: VenueOrderId`
- `order_side: OrderSide`
- `order_type: OrderType`
- `time_in_force: TimeInForce`
- `order_status: OrderStatus`
- `quantity: Quantity`
- `filled_qty: Quantity`
- `avg_px: Price | None`
- `report_id: UUID4`
- `ts_accepted: int` (nanoseconds)
- `ts_init: int` (nanoseconds)

**Key `FillReport` fields:**
- `account_id: AccountId`
- `instrument_id: InstrumentId`
- `venue_order_id: VenueOrderId`
- `trade_id: TradeId`
- `order_side: OrderSide`
- `last_qty: Quantity`
- `last_px: Price`
- `commission: Money | None`
- `liquidity_side: LiquiditySide`
- `report_id: UUID4`
- `ts_event: int`
- `ts_init: int`

### 3.3 Order Events to Emit

```python
from nautilus_trader.model.events import (
    OrderSubmitted,
    OrderAccepted,
    OrderRejected,
    OrderCanceled,
    OrderExpired,
    OrderTriggered,
    OrderPendingUpdate,
    OrderPendingCancel,
    OrderModifyRejected,
    OrderCancelRejected,
    OrderUpdated,
    OrderFilled,
    OrderPartiallyFilled,
)
```

Use `self.generate_order_submitted(order)` etc. from base class.

### 3.4 Currency / Money

```python
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

# For commission reporting
Money(Decimal("1.50"), Currency.from_str("USD"))
```

**Note:** `Currency` is in `nautilus_trader.model.objects` (NOT `nautilus_trader.model.currency`).

---

## 4. Pre-Discovered Reference — Futu SDK

### 4.1 Trade Context

```python
from futu import OpenSecTradeContext, TrdEnv, TrdMarket

ctx = OpenSecTradeContext(
    host=config.host,
    port=config.port,
    filter_trdmarket=TrdMarket.US,  # or HK, CN, etc.
)
```

**Key methods:**
- `ctx.unlock_trade(password_md5: str)` — unlock trading
- `ctx.place_order(...)` → `(ret_code, data)` where `data` is a DataFrame
- `ctx.modify_order(...)` → `(ret_code, data)`
- `ctx.cancel_order(...)` → `(ret_code, data)`
- `ctx.get_acc_list()` → `(ret_code, data)` — account discovery
- `ctx.get_position_list(...)` → `(ret_code, data)` — position reconciliation
- `ctx.set_handler(handler)` — register push handlers

### 4.2 Order Status Enum Values (int)

Defined in `sam_trader.adapters.futu.constants`:

| Constant | Value | Nautilus Mapping |
|----------|-------|------------------|
| `FUTU_ORDER_STATUS_UNSUBMITTED` | 0 | `OrderStatus.INITIALIZED` |
| `FUTU_ORDER_STATUS_WAITING_SUBMIT` | 1 | `OrderStatus.SUBMITTED` |
| `FUTU_ORDER_STATUS_SUBMITTING` | 2 | `OrderStatus.SUBMITTED` |
| `FUTU_ORDER_STATUS_SUBMIT_FAILED` | 3 | `OrderStatus.REJECTED` |
| `FUTU_ORDER_STATUS_TIMEOUT` | 4 | `OrderStatus.REJECTED` |
| `FUTU_ORDER_STATUS_SUBMITTED` | 5 | `OrderStatus.ACCEPTED` |
| `FUTU_ORDER_STATUS_FILLED_PART` | 6 | `OrderStatus.PARTIALLY_FILLED` |
| `FUTU_ORDER_STATUS_FILLED_ALL` | 7 | `OrderStatus.FILLED` |
| `FUTU_ORDER_STATUS_CANCELLING_PART` | 8 | `OrderStatus.PENDING_CANCEL` |
| `FUTU_ORDER_STATUS_CANCELLING_ALL` | 9 | `OrderStatus.PENDING_CANCEL` |
| `FUTU_ORDER_STATUS_CANCELLED_PART` | 10 | `OrderStatus.CANCELED` |
| `FUTU_ORDER_STATUS_CANCELLED_ALL` | 11 | `OrderStatus.CANCELED` |
| `FUTU_ORDER_STATUS_DISABLED` | 12 | `OrderStatus.CANCELED` |
| `FUTU_ORDER_STATUS_DELETED` | 13 | `OrderStatus.CANCELED` |
| `FUTU_ORDER_STATUS_FILL_CANCELLED` | 14 | `OrderStatus.CANCELED` |

### 4.3 Trade Side Enum Values (int)

| Constant | Value | Meaning |
|----------|-------|---------|
| `FUTU_TRD_SIDE_BUY` | 0 | Buy |
| `FUTU_TRD_SIDE_SELL` | 1 | Sell |
| `FUTU_TRD_SIDE_SELL_SHORT` | 2 | Sell Short |
| `FUTU_TRD_SIDE_BUY_BACK` | 3 | Buy Back |

Maps to: BUY → `OrderSide.BUY`, SELL/SELL_SHORT → `OrderSide.SELL`

### 4.4 Order Type Enum Values (int)

| Constant | Value | Meaning |
|----------|-------|---------|
| `FUTU_ORDER_TYPE_NORMAL` | 0 | Limit order |
| `FUTU_ORDER_TYPE_MARKET` | 1 | Market order |

### 4.5 Position Side Enum Values (int)

| Constant | Value | Meaning |
|----------|-------|---------|
| `FUTU_POSITION_SIDE_LONG` | 0 | Long |
| `FUTU_POSITION_SIDE_SHORT` | 1 | Short |

### 4.6 Time In Force Mapping

```python
# Function already in constants.py:
def futu_time_in_force_to_nautilus(tif: int) -> TimeInForce:
    ...
```

Futu TIF values: `0=Day`, `1=GTC`, `3=IOC`

### 4.7 Push Handler Base Classes

```python
from futu import TradeOrderHandlerBase, TradeDealHandlerBase

class TradeOrderHandler(TradeOrderHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        ...  # returns (ret_code, order_dict or DataFrame)

class TradeDealHandler(TradeDealHandlerBase):
    def on_recv_rsp(self, rsp_pb):
        ...  # returns (ret_code, deal_dict or DataFrame)
```

**Important:** `parse_order()` / `parse_deal()` return DataFrames. Iterate with `for _, row in data.iterrows():` to get dict-like rows. Column names are camelCase (e.g., `orderID`, `trdSide`, `orderType`, `orderStatus`, `qty`, `dealtQty`, `dealtAvgPrice`, `createTime`).

### 4.8 Timestamp Fields

Futu protobuf provides BOTH:
- `createTime` / `updateTime` — string format (e.g., `"2026-05-20 14:30:00"`)
- `createTimestamp` / `updateTimestamp` — float (seconds since epoch)

**Parser pattern (from `parsing/orders.py`):**
```python
def _parse_timestamp(raw: Any) -> int:
    if isinstance(raw, (int, float)) and raw > 1_000_000_000_000:
        return int(raw)  # already nanoseconds
    if isinstance(raw, (int, float)):
        return int(raw * 1_000_000_000)  # seconds → nanoseconds
    if isinstance(raw, str):
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp() * 1_000_000_000)
    return 0
```

---

## 5. Implementation Patterns

### 5.1 Shared Client Pattern

Reuse the existing `FutuClient` from `connection.py`. The execution client should share the same `(host, port, trd_env)` key as the data client to avoid duplicate trade contexts.

```python
from sam_trader.adapters.futu.connection import FutuClient

client = FutuClient.get_or_create(config.client_key)
self._trade_ctx = client.trade_ctx  # OpenSecTradeContext
```

### 5.2 Push-Loop Architecture

Same pattern as `FutuLiveDataClient` (Phase 2):

```python
async def _run_push_loop(self) -> None:
    while self.is_running:
        try:
            report = await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        self._handle_report(report)
```

Handlers push to `asyncio.Queue` in their callbacks; the loop pops and forwards to the message bus.

### 5.3 Venue Account Aliases

```python
def _register_venue_account_aliases(self, accounts: list[dict]) -> None:
    for acc in accounts:
        market = acc.get("trdMarket")  # "US", "HK", etc.
        acc_id = acc.get("acc_id")
        # Map market → venue → AccountId
```

### 5.4 Order Factory for Bracket Orders

```python
from nautilus_trader.model.orders import BracketOrder

bracket = self._order_factory.bracket(
    instrument_id=order.instrument_id,
    side=order.side,
    quantity=order.quantity,
    entry=order.price,
    stop_loss=stop_loss_price,
    take_profit=take_profit_price,
)
```

---

## 6. Test Patterns

### 6.1 Unit Test Structure

```python
import pytest
from nautilus_trader.execution.reports import OrderStatusReport, FillReport
from nautilus_trader.model.identifiers import AccountId, InstrumentId, VenueOrderId

@pytest.fixture
def account_id() -> AccountId:
    return AccountId("FUTU-001")

@pytest.fixture
def instrument_id() -> InstrumentId:
    return InstrumentId.from_str("AAPL.NASDAQ")
```

### 6.2 Mocking Futu SDK

```python
from unittest.mock import MagicMock, patch

mock_ctx = MagicMock()
mock_ctx.place_order.return_value = (0, pd.DataFrame({"order_id": ["123"]}))
```

### 6.3 Integration Test Pattern

Use `pytest-asyncio` and mock the trade context. Verify:
1. `connect()` → unlock_trade called, get_acc_list called
2. `_submit_order()` → place_order called with correct params
3. Push handler → `OrderStatusReport` / `FillReport` generated
4. Message bus receives correct event types

---

## 7. Testing Nautilus Cython Components

> **Critical:** Nautilus v1.227.0 uses Cython extension classes. Compiled internals are opaque to standard Python introspection (`inspect.signature`, `help()`, etc.). **Do NOT spend steps probing Cython classes with runtime introspection.** Use this section instead.

### 7.1 Test Stub Factories

```python
from nautilus_trader.test_kit.stubs.commands import TestCommandStubs
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

# Commands — IMPORTANT: when passing BOTH `order` and `venue_order_id`,
# TestCommandStubs IGNORES `venue_order_id` and extracts it from `order.venue_order_id`
# (which is None for unstubbed orders). Pass `instrument_id` + `client_order_id`
# explicitly instead of `order` if you need a specific venue_order_id.

cmd = TestCommandStubs.submit_order_command(order=order)
cmd = TestCommandStubs.modify_order_command(
    price=Price.from_str("155.00"),
    quantity=Quantity.from_int(50),
    instrument_id=order.instrument_id,          # pass explicitly
    client_order_id=order.client_order_id,      # pass explicitly
    venue_order_id=VenueOrderId("12345"),       # now respected
)
cmd = TestCommandStubs.cancel_order_command(
    instrument_id=order.instrument_id,
    client_order_id=order.client_order_id,
    venue_order_id=VenueOrderId("12345"),
)

# Order lists
from nautilus_trader.model.identifiers import OrderListId
from nautilus_trader.model.orders.list import OrderList

order_list = OrderList(
    order_list_id=OrderListId("OL-001"),
    orders=[entry, sl, tp],
)
submit_list_cmd = TestCommandStubs.submit_order_list_command(order_list)

# Components
msgbus = TestComponentStubs.msgbus()
cache = TestComponentStubs.cache()
clock = LiveClock()
```

### 7.2 Cython Logger API (Strict Signatures)

The Nautilus `Logger` is a Cython extension. It does **NOT** accept printf-style extra arguments.

| Method | Signature | Wrong | Right |
|--------|-----------|-------|-------|
| `info` | `info(self, message: str, color=...)` | `self._log.info("x: %s", x)` | `self._log.info(f"x: {x}")` |
| `warning` | `warning(self, message: str, color=...)` | `self._log.warning("x: %s", x)` | `self._log.warning(f"x: {x}")` |
| `debug` | `debug(self, message: str, color=...)` | `self._log.debug("x: %s", x)` | `self._log.debug(f"x: {x}")` |
| `exception` | `exception(self, message: str, ex: Exception)` | `self._log.exception("msg")` | `except E as e: self._log.exception(f"msg: {e}", e)` |

### 7.3 ComponentId / ClientId Trap

- `ExecutionClient.id` returns a `ComponentId`, NOT a `ClientId`.
- For string comparison, use `self.id.value`.
- `account_id.get_issuer()` must match `self.id.value` for the client to accept its own events.

### 7.4 SubmitOrder Constructor

`SubmitOrder` does **not** accept `instrument_id` as a constructor argument — it derives it from `order.instrument_id`.

```python
# Wrong
SubmitOrder(
    instrument_id=order.instrument_id,  # TypeError: unexpected keyword
    order=order,
    ...
)

# Right
SubmitOrder(
    trader_id=command.trader_id,
    strategy_id=command.strategy_id,
    client_id=command.client_id,
    order=order,
    command_id=UUID4(),
    ts_init=clock.timestamp_ns(),
)
```

---

## 8. Ticket Breakdown

| Ticket | Title | Scope | Depends On |
|--------|-------|-------|------------|
| `sam_trader-9z3.4.1` | ✅ **CLOSED** — Order parsing module | `parsing/orders.py`, constants, tests | `9z3.3.7` |
| `sam_trader-9z3.4.2` | **OPEN** — Skeleton, connection, unlock, aliases | `execution.py` class, `connect()`, `_register_venue_account_aliases()`, `unlock_trade()` | `9z3.4.1` |
| `sam_trader-9z3.4.4` | **OPEN** — Order methods | `_submit_order`, `_modify_order`, `_cancel_order`, bracket support | `9z3.4.2` |
| `sam_trader-9z3.4.5` | **OPEN** — Push handler wiring | `TradeOrderHandler`, `TradeDealHandler`, `_run_push_loop` | `9z3.4.4` |
| `sam_trader-9z3.4.6` | **OPEN** — Account discovery & position reconciliation | `get_acc_list`, position reconciliation on connect | `9z3.4.5` |
| `sam_trader-9z3.4.3` | **OPEN** — Exit test | Full order lifecycle integration test | `9z3.4.6` |

---

## 9. Commonly Used Imports

```python
# Nautilus core
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.execution.reports import OrderStatusReport, FillReport, PositionStatusReport
from nautilus_trader.model.events import OrderSubmitted, OrderAccepted, OrderFilled, OrderCanceled
from nautilus_trader.model.enums import OrderSide, OrderType, OrderStatus, TimeInForce, PositionSide, LiquiditySide
from nautilus_trader.model.identifiers import AccountId, ClientId, InstrumentId, VenueOrderId, TradeId, Venue
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.core.uuid import UUID4

# Futu SDK
from futu import OpenSecTradeContext, TradeOrderHandlerBase, TradeDealHandlerBase

# Internal
from sam_trader.adapters.futu.config import FutuExecClientConfig
from sam_trader.adapters.futu.connection import FutuClient
from sam_trader.adapters.futu.parsing.orders import TradeOrderHandler, TradeDealHandler
from sam_trader.adapters.futu.constants import (
    FUTU_ORDER_STATUS_...,
    FUTU_TRD_SIDE_...,
    FUTU_ORDER_TYPE_...,
    FUTU_POSITION_SIDE_...,
)
```

---

## 10. Lint / Type-Check Notes

- `# type: ignore[misc]` needed for mypy when assigning to frozen dataclass fields inside `pytest.raises` blocks in tests.
- `pandas` import for DataFrame handling in push handlers — ensure `pandas` is in project deps.
- Use `Decimal(str(value))` when converting Futu float prices to avoid float precision issues.

---

*Last updated: 2026-05-21*
