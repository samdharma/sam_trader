# Build Phase 7 — Strategy Library & Bundle System

> **Status:** Not Started  
> **Goal:** OrbStrategy, MomentumStrategy, strategy template. Multi-venue bundle loader. Bundle validation.  
> **Prev Phase:** [BUILD_PHASE_6.md](./BUILD_PHASE_6.md) — Actors & State Management  
> **Next Phase:** [BUILD_PHASE_8.md](./BUILD_PHASE_8.md) — sam-services Container

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      Bundle Loader                            │
├──────────────────────────────────────────────────────────────┤
│  bundles.yaml                                                 │
│    └── venue: FUTU / IB                                       │
│    └── strategy: sam_trader.strategies.orb.OrbStrategy       │
│    └── instrument_id: TSLA.NASDAQ                            │
│    └── parameters: {...}                                      │
├──────────────────────────────────────────────────────────────┤
│  Strategies                                                   │
│    ├── OrbStrategy (Opening Range Breakout)                  │
│    ├── MomentumStrategy                                       │
│    └── _template.py (copy-paste starter)                     │
├──────────────────────────────────────────────────────────────┤
│  Validation                                                   │
│    └── Schema check → Backtest gate → Live approval          │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — Nautilus Strategy API

```python
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.model.data import Bar, QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading import Position

class OrbStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    atr_period: int = 14
    breakout_threshold: float = 0.5

class OrbStrategy(Strategy):
    def __init__(self, config: OrbStrategyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        ...

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
```

### 2.1 StrategyConfig Pattern

- Must be `frozen=True` (msgspec Struct or dataclass)
- Fields must be serializable (str, int, float, bool)
- `instrument_id` is typically passed as `str` and parsed inside `on_start`

### 2.2 Order Submission from Strategy

```python
from nautilus_trader.model.orders import MarketOrder

order = self.order_factory.market(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_int(100),
)
self.submit_order(order)
```

### 2.3 Bracket Orders

```python
bracket = self.order_factory.bracket(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_int(100),
    entry=Price.from_str("150.00"),
    stop_loss=Price.from_str("145.00"),
    take_profit=Price.from_str("160.00"),
)
self.submit_order_list(bracket)
```

---

## 3. Pre-Discovered Reference — Bundle Loader

```python
from nautilus_trader.trading.config import ImportableStrategyConfig

def load_bundles(path: str) -> list[ImportableStrategyConfig]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    configs = []
    for bundle in raw["bundles"]:
        configs.append(ImportableStrategyConfig(
            strategy_path=bundle["strategy"],
            config_path=bundle["config_path"],
            config=bundle["parameters"],
        ))
    return configs
```

---

## 4. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p7-loader` | BundleLoader | Multi-venue YAML → `ImportableStrategyConfig` | ✅ Medium |
| `sam-p7-orb` | OrbStrategy | Port from v2, venue-aware | ✅ Medium |
| `sam-p7-momentum` | MomentumStrategy | Port from v2, venue-aware | ✅ Medium |
| `sam-p7-template` | Strategy template | `_template.py` copy-paste starter | ✅ Small |
| `sam-p7-bundle-validate` | Bundle validation | Schema + backtest gate | ✅ Medium |
| `sam-p7-verify` | Exit: strategy lifecycle | Integration test: full strategy → order → fill | ✅ Medium |

**No decomposition needed for Phase 7.** All tickets are within healthy step budgets.

---

## 5. Venue-Aware Order Routing

```python
# Inside strategy
venue = self.instrument_id.venue
if venue == Venue("FUTU"):
    # Futu-specific logic (e.g., lot size constraints)
    ...
elif venue == Venue("IB"):
    # IB-specific logic
    ...
```

---

*Last updated: 2026-05-21*
