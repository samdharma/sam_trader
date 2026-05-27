# Build Phase 11 — Deploy Script & E2E Validation

> **Status:** ✅ Complete (all 4 tickets closed incl EXIT 9z3.12.4)  
> **Goal:** Single-script deploy. First-run wizard. All profiles work. Full E2E gate passes.  
> **Prev Phase:** [BUILD_PHASE_10.md](./BUILD_PHASE_10.md) — Safety & Dashboard

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      deploy.sh                                │
├──────────────────────────────────────────────────────────────┤
│  Setup                                                        │
│    ├── git clone / git pull                                   │
│    ├── .env generation (wizard or existing)                  │
│    └── Docker network creation                                │
├──────────────────────────────────────────────────────────────┤
│  Profiles                                                     │
│    ├── --with-futu  →  sam-futu-opend + sam-trader           │
│    ├── --with-ib    →  sam-ib-gateway + sam-trader           │
│    └── --with-services → sam-services                        │
├──────────────────────────────────────────────────────────────┤
│  Orchestration                                                │
│    └── Sequential start with health gating:                  │
│        postgres → redis → futu-opend → sam-trader → optional │
├──────────────────────────────────────────────────────────────┤
│  Operational Commands                                         │
│    ├── --status                                               │
│    ├── --stop                                                 │
│    ├── --restart                                              │
│    └── --logs <service>                                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — deploy.sh Pattern

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE=""

usage() {
  echo "Usage: ./deploy.sh [--with-futu] [--with-ib] [--with-services] [--stop] [--status]"
  exit 1
}

start_stack() {
  cd "$SCRIPT_DIR"
  docker compose up -d sam-postgres sam-redis
  wait_for_healthy sam-postgres
  wait_for_healthy sam-redis

  if [[ "$PROFILE" == *"futu"* ]]; then
    docker compose --profile futu up -d sam-futu-opend
    wait_for_healthy sam-futu-opend
  fi

  docker compose up -d sam-trader
  wait_for_healthy sam-trader
}

wait_for_healthy() {
  local service="$1"
  for i in {1..30}; do
    if docker compose ps "$service" | grep -q "healthy"; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: $service failed to become healthy"
  exit 1
}
```

---

## 3. Pre-Discovered Reference — First-Run Wizard

```python
# scripts/wizard.py
import os

def main():
    print("SAM Trader V3 — First Run Wizard")
    trader_id = input("Trader ID [sam_trader]: ") or "sam_trader"
    env = input("Environment [paper]: ") or "paper"
    futu_account = input("Futu account (email/phone): ")
    futu_pwd_md5 = input("Futu password MD5: ")

    with open(".env", "w") as f:
        f.write(f"TRADER_ID={trader_id}\n")
        f.write(f"ENV={env}\n")
        f.write(f"FUTU_ACCOUNT_ID={futu_account}\n")
        f.write(f"FUTU_ACCOUNT_PWD_MD5={futu_pwd_md5}\n")

if __name__ == "__main__":
    main()
```

---

## 4. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p11-deploy` | deploy.sh | Single-script deploy with profiles | ✅ Medium-Large but well-scoped as one script |
| `sam-p11-wizard` | First-run wizard | Interactive `.env` generation | ✅ Medium |
| `sam-p11-docs` | Documentation | Deploy guide, bundle guide, operator guide | ✅ Medium |
| `sam-p11-e2e` | E2E validation gate | Full system soak test | ✅ Large but justified as final gate |

**No decomposition needed for Phase 11.** `deploy.sh` is large but is a single cohesive script. The E2E gate is intentionally comprehensive.

---

## 5. E2E Validation Checklist

| Step | Validation |
|------|------------|
| 1 | Fresh clone + `./deploy.sh --with-futu` succeeds |
| 2 | All containers healthy within 60s |
| 3 | `sam-trader` connects to `sam-futu-opend` |
| 4 | QuoteTick arrives on message bus |
| 5 | Order submits → fills → journals to PostgreSQL |
| 6 | `sam-services` starts, dashboard shows data |
| 7 | 1-hour soak test: no crashes, no memory leaks |
| 8 | `./deploy.sh --stop` cleans up all containers |

---

*Last updated: 2026-05-21*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** Planning — 2 tickets (1 task + 1 EXIT)  
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps |
|-----------|-------|------|
| `sam_trader-9z3.12.8` | deploy.sh: always-on brokers, MARKET env var, updated wizard | 9z3.1.25, 9z3.2.4 |
| `sam_trader-9z3.12.9` | [EXIT] E2E: full daily cycle simulation | 9z3.12.8 |

### Design Notes — Deploy Script
- Remove `--with-futu`, `--with-ib`, `--with-services` flags
- `docker compose up -d` starts all 6 containers (no profile flags)
- Wizard: prompt "Default market? [auto/US/HK]" (default: auto-detect from HKT time)
- Auto-detect: if current HKT time between 16:00-03:59 → MARKET=US, else MARKET=HK
- Generate `.env` with `MARKET=US` or `MARKET=HK` (+ backward compat `FUTU_TRD_MARKET`)
- Under 300 lines (existing constraint)
- `deploy.sh --stop` cleans up all 6 containers

### Design Notes — E2E Daily Cycle Test
- Test file: `tests/integration/test_daily_cycle_e2e.py`
- 15-test suite:
  1. Start MARKET=HK → Futu HK, no IB, HK bundles
  2. HK SOD readiness passes all 7 checks
  3. HK lunch pause at 12:00 → strategies paused
  4. HK lunch resume at 13:00 → strategies resume
  5. HK close (16:00) → MarketSchedulerActor triggers switch
  6. State saved to Redis, sam:state_saved published
  7. Restart orchestrator updates MARKET=US, restarts sam-trader
  8. After restart: Futu US, IB registered, US bundles loaded
  9. US SOD readiness passes (includes IB connectivity)
  10. US EOD report with correct P&L, fills, health
  11. US close (04:00) → switch back to HK
  12. Weekend: scheduler skips alerts, strategies paused
  13. Dual-broker gap scanner cross-validates Futu vs IB
  14. HK EOD report after full cycle
  15. State preserved across all restarts

### Nautilus Types / Patterns Used
- `TestClock` for time simulation in integration tests
- `TradingNode` full lifecycle (build → run → save → stop → rebuild → run → load)
- Redis state persistence verification
- Docker Compose lifecycle via deploy.sh
- All actors verified: MarketScheduler, ReadinessChecker, EndOfDayReporter, BundleController

*Last updated: 2026-05-27 — Dynamic Multi-Market extensions planned*
