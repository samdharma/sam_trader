# Build Phase 11 — Deploy Script & E2E Validation

> **Status:** Not Started  
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
