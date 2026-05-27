# Build Phase 5 — IBKR Adapter Re-integration

> **Status:** ✅ Complete (all 14 tickets closed incl EXIT 9z3.6.4)  
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
| `sam_trader-9z3.6.1` | IBKR config wiring in main.py ✅ | `ib_enabled` flag, `InteractiveBrokers*Config` from `SamTraderConfig` | ✅ Small |
| `sam_trader-9z3.6.8` | Pre-flight IB account permission check ✅ | Query IB trading permissions, disable strategies if shorts blocked | ✅ Medium |
| `sam_trader-9z3.6.2` | IB Gateway Docker service | `docker-compose.yml` service definition, profile `ib` | ✅ Small |
| `sam_trader-9z3.6.5` | IBKR factory registration | Register `InteractiveBrokersLiveDataClientFactory` and `InteractiveBrokersLiveExecClientFactory` | ✅ Small |
| `sam_trader-9z3.6.6` | IBKR instrument provider wiring | Register `InteractiveBrokersInstrumentProvider` | ✅ Small |
| `sam_trader-9z3.6.3` | Enhance IB adapter for v3 | Multi-venue config, venue aliasing, **SMART routing default** | ✅ Medium |
| `sam_trader-9z3.6.7` | [BUG] IBKR post_only incompatibility | Adapter-level handling; venue-aware order wrapper | ✅ Medium |
| `sam_trader-9z3.6.4` | [EXIT] Dual-venue TradingNode | Integration test: Futu + IB simultaneously | ✅ Medium |

### 4.1 Build Order (Actual Dependency Chain)

```
9z3.5.4 (P4 main-wire) ──► 9z3.6.1 (config wiring) ✅ DONE
9z3.5.6 (P4 exit) ──► 9z3.6.2 (IB Gateway Docker) ──► 9z3.6.5 (factory reg) ──► 9z3.6.6 (provider wiring)
                                                                                       │
                                                                                       ▼
                                                                             9z3.6.3 (enhance adapter) ──► 9z3.6.7 (post_only bug) ──► 9z3.6.4 (EXIT)
9z3.6.8 (pre-flight perms) ✅ DONE — runs in parallel (no deps)
```

### 4.2 Decomposition: `sam-p5-ib-port`

> **Original monolithic ticket `sam-p5-ib-port` was too large.** It was decomposed into 3 sub-tickets plus an IB Gateway Docker ticket inserted between config wiring and factory registration (because factories need a running gateway to connect to).

| New Ticket | Title | Scope | Depends On |
|------------|-------|-------|------------|
| `sam_trader-9z3.6.1` | IBKR config wiring in main.py | Add `ib_enabled` flag, `InteractiveBrokers*Config` construction | `sam-p4-main-wire` (9z3.5.4) |
| `sam_trader-9z3.6.2` | IB Gateway Docker service | `docker-compose.yml` service for `sam-ib-gateway` | P4 exit (9z3.5.6) |
| `sam_trader-9z3.6.5` | IBKR factory registration | Register IB data/exec factories in `build_trading_node()` | `sam_trader-9z3.6.2` |
| `sam_trader-9z3.6.6` | IBKR instrument provider wiring | Register `InteractiveBrokersInstrumentProvider` | `sam_trader-9z3.6.5` |

---

## 5. Multi-Venue Coexistence Rules

1. **AccountId prefixes:** `FUTU-001` vs `IB-001` — prevents collision.
2. **Subscription isolation:** Futu subscription manager tracks only Futu subs. IB subs managed internally by Nautilus IB adapter.
3. **Bundle venue field:** Each bundle declares `venue: FUTU` or `venue: IB`. Loader routes to correct factory.
4. **No cross-venue order routing:** An order for `AAPL.NASDAQ` (Futu) must never be routed to IB gateway.

---

## 6.3 SMART Routing Default (Gap Remediation)

> **v2 Post-Mortem (21-May):** 52 IB code 10311 warnings — "directly routed to NASDAQ" — caused higher per-trade fees.

**Fix:** Default IB order routing to `SMART` when `venue: IB` and no explicit `exchange` override in bundle.

```yaml
# bundles.yaml — IB bundle with SMART default
- id: "nvda-momentum-5m-ib"
  venue: IB
  strategy:
    path: sam_trader.strategies.momentum:MomentumStrategy
    config:
      instrument_id: "NVDA.NASDAQ"
      # exchange omitted → defaults to SMART
  bracket:
    stop_loss_ticks: 15
```

**Explicit opt-in for direct routing:**
```yaml
  strategy:
    config:
      exchange: "NASDAQ"  # opt-in; logs WARNING
```

**Implementation:** Update IB exec client order submission to inject `exchange="SMART"` into Nautilus `Order` when bundle omits `exchange`.

---

## 6.4 Pre-Flight Account Permission Check (Gap Remediation)

> **v2 Post-Mortem (21-May):** 189 short order rejections because paper account lacked short-selling permissions. No pre-flight check.

**Fix:** On IB exec client connect, query account trading permissions. If short-selling is disabled and an active bundle requires it, emit CRITICAL log and disable the strategy.

**Beads ticket:** `sam_trader-9z3.6.8`

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

---

## 8. Ticket Summary

| Ticket | Title | Status |
|--------|-------|--------|
| `sam_trader-9z3.6.1` | IBKR config wiring in main.py | ✅ Closed |
| `sam_trader-9z3.6.8` | Pre-flight IB account trading permissions check | ✅ Closed |
| `sam_trader-9z3.6.2` | IB Gateway Docker service (profile: ib) | ✅ Closed |
| `sam_trader-9z3.6.5` | IBKR factory registration in main.py | ✅ Closed |
| `sam_trader-9z3.6.6` | IBKR instrument provider wiring | ✅ Closed |
| `sam_trader-9z3.6.3` | Enhance IBKR adapter for v3 patterns | ✅ Closed |
| `sam_trader-9z3.6.7` | [BUG] IBKR post_only incompatibility | ✅ Closed |
| `sam_trader-9z3.6.4` | [EXIT] Dual-venue TradingNode (Futu + IB) | ✅ Closed |
| `sam_trader-9z3.6.9` | Remove dead permission checking infrastructure | ✅ Closed |
| `sam_trader-9z3.6.10` | Fix .env.example WAIT_FOR broker defaults mismatch | ✅ Closed |
| `sam_trader-9z3.6.11` | Fix silent fallback on invalid IB_MARKET_DATA_TYPE | ✅ Closed |
| `sam_trader-9z3.6.12` | Remove dead ib_trading_mode field from SamTraderConfig | ✅ Closed |
| `sam_trader-9z3.6.13` | Integration test for standard IB execution path post-cleanup | ✅ Closed |
| `sam_trader-9z3.6.14` | Remove non-standard custom IB exec client and factory | ✅ Closed |

---

*Last updated: 2026-05-24 — Status updated to Complete during gap audit; ticket summary added*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** Planning — 1 ticket  
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps |
|-----------|-------|------|
| `sam_trader-9z3.6.15` | IB: conditional enable/disable via MarketConfig | 9z3.2.5 |

### Design Notes
- IB factories registered only when `cfg.market_config.ib_enabled is True`
- `MARKET=US` → IB data + exec factories registered normally
- `MARKET=HK` → IB factories NOT registered, log INFO "IB disabled for HK market"
- IB Gateway container stays running 24/7 (docker-compose always-on from Phase 0 DM)
- Only the Nautilus client registration is conditional — the gateway is unaffected
- Backward compat: if `MARKET` not set, uses existing `IB_ENABLED` env var

### Nautilus Types / Patterns Used
- `InteractiveBrokersLiveDataClientFactory` / `InteractiveBrokersLiveExecClientFactory`
- `TradingNode.add_data_client_factory()` / `add_exec_client_factory()`
- Conditional factory registration — already supported by Nautilus (lazy import pattern)

*Last updated: 2026-05-27 — Dynamic Multi-Market extensions planned*
