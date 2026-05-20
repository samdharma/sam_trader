# Build Phase 5 — IBKR Adapter Re-integration

> **Status:** Not Started  
> **Goal:** Port IBKR adapter from v2. Enhanced for multi-venue coexistence. Both Futu + IB work simultaneously in same TradingNode.  
> **Prev Phase:** [BUILD_PHASE_4.md](./BUILD_PHASE_4.md) — Futu Instrument Provider & TradingNode Integration  
> **Next Phase:** [BUILD_PHASE_6.md](./BUILD_PHASE_6.md) — Actors & State Management

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      TradingNode (main.py)                    │
├──────────────────────────────────────────────────────────────┤
│  Futu Side (already built)                                    │
│    ├── FutuLiveDataClientFactory                             │
│    ├── FutuLiveExecClientFactory                             │
│    └── FutuInstrumentProvider                                │
├──────────────────────────────────────────────────────────────┤
│  IBKR Side (ported from v2)                                   │
│    ├── InteractiveBrokersLiveDataClientFactory               │
│    ├── InteractiveBrokersLiveExecClientFactory               │
│    └── InteractiveBrokersInstrumentProvider                  │
├──────────────────────────────────────────────────────────────┤
│  Coexistence Rules                                            │
│    ├── No shared subscription IDs                            │
│    ├── Venue-specific AccountId prefixes                     │
│    └── Independent factory registration                      │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — Nautilus IB Types

NautilusTrader ships with a first-party IB adapter in `nautilus_trader.adapters.interactive_brokers`.

```python
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.adapters.interactive_brokers.providers import InteractiveBrokersInstrumentProvider
```

**Config fields (from Nautilus):**
- ` InteractiveBrokersDataClientConfig`: host, port, client_id, trading_mode
- `InteractiveBrokersExecClientConfig`: host, port, client_id, trading_mode, account_id

---

## 3. What We Port from v2

| v2 File | v3 Destination | Changes |
|---------|---------------|---------|
| `main.py` IB config section | `main.py` | Wrap in `ib_enabled` flag, use `SamTraderConfig` |
| `docker-compose.yml` ib-gateway | `docker-compose.yml` | Rename to `sam-ib-gateway`, profile `ib` |
| `.env.example` IB vars | `.env.example` | Add alongside Futu vars |

**No custom IB adapter code needed** — Nautilus provides the official adapter. We only wire it.

---

## 4. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p5-ib-port` | Port IBKR adapter from v2 | Config, factories, instrument provider wiring in `main.py` | ⚠️ **LARGE** — ports 3 major components |
| `sam-p5-ib-gateway` | IB Gateway Docker service | `docker-compose.yml` service definition | ✅ Small |
| `sam-p5-ib-enhance` | Enhance IB adapter for v3 | Multi-venue config, venue aliasing | ✅ Medium |
| `sam-p5-exit-ib` | Exit: dual-venue TradingNode | Integration test: Futu + IB simultaneously | ✅ Medium |

### 4.1 Decomposition: `sam-p5-ib-port`

This ticket is **too large** (config + factories + instrument provider + wiring). Decompose into:

| New Ticket | Title | Scope | Depends On |
|------------|-------|-------|------------|
| `sam_trader-9z3.6.1` | IBKR config wiring in main.py | Add `ib_enabled` flag, `InteractiveBrokers*Config` construction from `SamTraderConfig` | `sam-p4-main-wire` |
| `sam_trader-9z3.6.5` | IBKR factory registration | Register `InteractiveBrokersLiveDataClientFactory` and `InteractiveBrokersLiveExecClientFactory` in `build_trading_node()` | `sam_trader-9z3.6.1` |
| `sam_trader-9z3.6.6` | IBKR instrument provider wiring | Register `InteractiveBrokersInstrumentProvider` | `sam_trader-9z3.6.5` |

**Action:** Close/repurpose original `sam-p5-ib-port` as `sam_trader-9z3.6.1` (first sub-task), create `sam_trader-9z3.6.5` and `sam_trader-9z3.6.6` as siblings.

---

## 5. Multi-Venue Coexistence Rules

1. **AccountId prefixes:** `FUTU-001` vs `IB-001` — prevents collision.
2. **Subscription isolation:** Futu subscription manager tracks only Futu subs. IB subs managed internally by Nautilus IB adapter.
3. **Bundle venue field:** Each bundle declares `venue: FUTU` or `venue: IB`. Loader routes to correct factory.
4. **No cross-venue order routing:** An order for `AAPL.NASDAQ` (Futu) must never be routed to IB gateway.

---

## 6. Known v2 Operational Issue — IBKR `post_only` Rejection

> ⚠️ **CRITICAL:** During v2's first operational day (2026-05-20), IBKR rejected **100% of bracket orders** because NautilusTrader defaults `tp_post_only=True` on the take-profit limit leg (and `post_only=True` on standalone limit orders). IB does not support this attribute. The TP leg rejection cascaded through OCA/OUO linkage and killed the entire bracket — 108 rejections, 0 fills.
>
> **Fix:** All `order_factory.bracket()` calls targeting IB must explicitly pass `tp_post_only=False`. All standalone `order_factory.limit()` TP orders must pass `post_only=False`.
>
> **Reference:** `~/Documents/ai_agent_docs/csam_trader_post_only_fix_2026-05-20.md`
>
> **Beads:** Bug ticket `sam_trader-9z3.6.7` tracks adapter-level handling. Phase 7 strategy tickets (`sam_trader-9z3.8.2`, `sam_trader-9z3.8.3`, `sam_trader-9z3.8.4`) mandate the fix in ported strategy code.

---

## 7. Commonly Used Imports

```python
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.adapters.interactive_brokers.providers import InteractiveBrokersInstrumentProvider
```

---

*Last updated: 2026-05-21*
