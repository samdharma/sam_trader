# Build Phase 7 — Strategy Library & Bundle System

> **Status:** ✅ Complete (all 6 tickets closed incl EXIT 9z3.8.6)  
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
| `sam_trader-9z3.8.1` | BundleLoader | Multi-venue YAML → `ImportableStrategyConfig` (independent root) | ✅ Medium |
| `sam_trader-9z3.8.2` | OrbStrategy | Port from v2, venue-aware, **configurable entry order type** (independent root) | ✅ Medium |
| `sam_trader-9z3.8.3` | MomentumStrategy | Port from v2, venue-aware, **direction filter + entry order type** (independent root) | ✅ Medium |
| `sam_trader-9z3.8.4` | Strategy template | Extracted from Orb + Momentum after both ported (depends on 8.2, 8.3) | ✅ Small |
| `sam_trader-9z3.8.5` | Bundle validation | Schema + backtest gate (depends on loader 8.1) | ✅ Medium |
| `sam_trader-9z3.8.6` | [EXIT] Verify strategy lifecycle | Integration test: full strategy → order → fill (depends on 8.2, 8.3, 8.5) | ✅ Medium |

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

## 6. Venue-Specific Order Defaults

### 6.1 IBKR `post_only` Incompatibility

Interactive Brokers **does not support** the `post_only` order attribute. NautilusTrader defaults:

- `OrderFactory.bracket(..., tp_post_only=True)` — take-profit limit leg
- `OrderFactory.limit(..., post_only=True)` — standalone limit orders

If left at default, IB rejects the TP leg, which cascades through OCA/OUO and kills the entire bracket order. This caused **100% execution failure** on v2's first operational day.

**Required pattern for IB venue:**

```python
# Bracket order — MUST set tp_post_only=False for IB
bracket = self.order_factory.bracket(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_int(100),
    entry=Price.from_str("150.00"),
    stop_loss=Price.from_str("145.00"),
    take_profit=Price.from_str("160.00"),
    tp_post_only=False,  # <-- REQUIRED for IB
)

# Standalone take-profit limit — MUST set post_only=False for IB
tp_order = self.order_factory.limit(
    instrument_id=self.instrument_id,
    order_side=OrderSide.SELL,
    quantity=Quantity.from_int(50),
    price=Price.from_str("160.00"),
    post_only=False,  # <-- REQUIRED for IB
)
```

### 6.2 Venue-Aware Wrapper (Recommended)

To centralize the fix and prevent future strategies from hitting this, consider a wrapper in `strategies/common.py`:

```python
def make_bracket_ib(order_factory, **kwargs):
    """Build a bracket order with IB-safe defaults."""
    kwargs.setdefault("tp_post_only", False)
    return order_factory.bracket(**kwargs)
```

**Reference:** Fix doc `~/Documents/ai_agent_docs/csam_trader_post_only_fix_2026-05-20.md`

---

## 7. Configurable Entry Order Type (Gap Remediation)

> **v2 Post-Mortem (21-May):** MARKET entries caused slippage. No LIMIT entry option existed.

**Added to `OrbConfig` and `MomentumConfig`:**
```python
entry_order_type: Literal["MARKET", "LIMIT", "STOP_MARKET"] = "MARKET"
```

**Behavior:**
- `"MARKET"` (default) — identical to v2
- `"LIMIT"` — submit `LimitOrder` at breakout level ± 1 tick
- `"STOP_MARKET"` — submit `StopMarketOrder` at breakout level

**Beads tickets:** `sam_trader-9z3.8.2` (Orb), `sam_trader-9z3.8.3` (Momentum)

---

## 8. Direction Filter for MomentumStrategy (Gap Remediation)

> **v2 Post-Mortem (21-May):** 189 short rejections because paper account blocked shorts. No long-only fallback.

**Added to `MomentumConfig`:**
```python
allowed_directions: list[str] = ["LONG", "SHORT"]
```

**Behavior:**
- `["LONG", "SHORT"]` (default) — both directions trade
- `["LONG"]` — short signals skipped, long signals execute
- `["SHORT"]` — long signals skipped, short signals execute

This enables immediate fallback to long-only without code changes.

**Beads ticket:** `sam_trader-9z3.8.3`

---

---

## 9. Ticket Summary

| Ticket | Title | Status |
|--------|-------|--------|
| `sam_trader-9z3.8.1` | BundleLoader — multi-venue YAML → ImportableStrategyConfig | ✅ Closed |
| `sam_trader-9z3.8.2` | OrbStrategy — port from v2 with venue-aware config | ✅ Closed |
| `sam_trader-9z3.8.3` | MomentumStrategy — port from v2 with venue-aware config | ✅ Closed |
| `sam_trader-9z3.8.4` | Strategy template — copy-paste starter | ✅ Closed |
| `sam_trader-9z3.8.5` | Bundle validation — schema check + backtest gate | ✅ Closed |
| `sam_trader-9z3.8.6` | [EXIT] Verify strategy lifecycle with Futu data | ✅ Closed |

---

*Last updated: 2026-05-24 — Status updated to Complete during gap audit; ticket summary added*
