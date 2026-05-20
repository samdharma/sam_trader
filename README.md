# SAM Trader V3

**Production-grade autonomous trading platform on NautilusTrader.**
Multi-venue support: Futu (HK/US) + Interactive Brokers (IBKR).

> ⚠️ **Work in progress** — Phase 0: Foundation. No trading logic yet.

## Architecture

```
┌──────────┐   ┌──────────┐   ┌────────────┐   ┌────────────┐   ┌──────────┐
│ postgres │   │  redis   │   │futu-opend  │   │ ib-gateway │   │nautilus  │
│  :5432   │   │  :6379   │   │  :11111    │   │  :4004     │   │ (engine) │
└──────────┘   └──────────┘   └────────────┘   └────────────┘   └──────────┘
```

## Docs

| Document | Purpose |
|---|---|
| `docs/reference/SAM_TRADER_V3_PLAN.md` | Architecture, decisions, roadmap |
| `docs/agent/TICKET_PLAN_V3.md` | Ticket dependency tree & AC |
| `AGENTS.md` | Agent quick-reference |

## Build Status

- Phase 0: Skeleton & Docker Stack — in progress
