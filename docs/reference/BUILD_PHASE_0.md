# Build Phase 0 — Foundation & Docker Stack Hardening

> **Status:** ✅ Complete (all 20 tickets closed, incl 2 EXIT gates)  
> **Scope:** Original Phase 0 (skeleton) + Phase 0-H (hardening)

---

## 1. Architecture Overview

```
sam-trader (Nautilus TradingNode)
sam-postgres (PostgreSQL 16)
sam-redis (Redis 7)
sam-futu-opend (Futu OpenD broker)
sam-ib-gateway (IB Gateway, optional)
sam-services (operations container)
```

Network: `sam-net`  
Deploy: `./deploy.sh --with-futu`

---

## 2. Docker Patterns

### Base Image
- `debian:stable-slim` (not ubuntu:22.04)
- `tini` as PID 1 for signal handling and zombie reaping
- Compressed image size: ~46 MB (Futu binary is downloaded at runtime to persistent volume)

### Health Checks (3-Layer)
| Layer | Check | Futu OpenD | PostgreSQL | Redis | sam-trader |
|-------|-------|------------|------------|-------|------------|
| L1 | Process | `pgrep FutuOpenD` | `pgrep postgres` | `pgrep redis-server` | `pgrep python` |
| L2 | Socket | TCP connect 11111 | `pg_isready` | `redis-cli ping` | `/proc/1/cmdline` |
| L3 | Protocol | Log scan for login failure | `SELECT 1` | `INFO server` | Optional port check |

Interval: 30s, Timeout: 10s, Start-period: 60s, Retries: 3

### Host Monitor
- Poll all `sam-*` containers every 60s
- Restart counter per container in `/tmp/sam-monitor/`
- Cooldown: 3 restarts in 15min → 30min backoff
- macOS: `launchd` plist template
- Linux: systemd service / cron documented

---

## 3. Backup & Restore

- **Schedule:** HKT 06:00, weekdays only (skip US + HK holidays)
- **Target:** `~/Documents/ai_agent_docs/backup-sam_trader_v3/`
- **Format:** `sam_trader_backup_YYYYMMDD_HHmmss.tar.gz`
- **Contents:** PostgreSQL dump, Redis RDB, Futu volume, config/
- **Retention:** 30 days (configurable via `BACKUP_RETENTION_DAYS`)

---

## 4. Futu OpenD First-Time Login

- Extract questionnaire URL from `docker logs sam-futu-opend`
- Telnet access: `docker exec -it sam-futu-opend telnet localhost 22222`
- MD5 password: `echo -n password | md5sum`
- Verify health before starting `sam-trader`

See `docs/user/FUTU_FIRST_LOGIN.md` (created by `sam_trader-9z3.1.19`).

---

## 5. Commonly Used Commands

```bash
# Build Futu OpenD image (compressed ~46 MB; binary downloaded at runtime)
docker build -f docker/Dockerfile.futu-opend -t sam-futu-opend .

# Start stack
docker compose up -d

# Check health
docker compose ps

# View monitor logs
tail -f logs/host-monitor.log

# Run backup manually (inside sam-services container)
docker exec sam-services python -m sam_trader.services.backup backup
```

---

*Last updated: 2026-05-22*

---

## Dynamic Multi-Market Extensions (Planned)

> **Status:** In Progress — 1 of 3 tickets completed
> **Plan:** `docs/user/DYNAMIC_MULTI_MARKET_PLAN.md`

### Tickets

| Ticket ID | Title | Deps | Status |
|-----------|-------|------|--------|
| `sam_trader-9z3.1.25` | Remove Docker profiles — all 6 containers always-on | None | ✅ Complete |
| `sam_trader-9z3.1.26` | Entrypoint: unconditional multi-broker wait logic | 9z3.1.25 | ✅ Complete |
| `sam_trader-9z3.1.27` | IB Gateway: US-market-only environment label | 9z3.1.25 | ⬜ Planned |

### Design Notes
- ~~`profiles:` blocks removed from sam-futu-opend, sam-ib-gateway, sam-services~~ ✅
- `docker compose up -d` starts all 6 containers (no flags)
- ~~Entrypoint always waits for Futu OpenD (TCP 11111) and IB Gateway (TCP 4004)~~ ✅
- ~~120s timeout per broker; clear error on timeout~~ ✅
- `IB_MARKET=US` env var added to ib-gateway for operator clarity (metadata only)

### Implementation Details (9z3.1.26)
- Removed `WAIT_FOR_IB_GATEWAY` and `WAIT_FOR_FUTU_OPEND` env-var conditionals
- Brokers now always waited for unconditionally
- Introduced `BROKER_WAIT_TIMEOUT` defaulting to 120s (separate from generic `WAIT_TIMEOUT` 60s)
- Cleaned up `WAIT_FOR_*` references from docker-compose.yml and validate_ib_stack.sh
- 7 unit tests cover: all-ready, PG timeout, Futu timeout, IB timeout, and 3 env-validation tests

### Nautilus Types / Patterns Used
- Docker Compose health checks (L1/L2/L3 — already standardized)
- TCP socket checks in entrypoint (already built)

*Last updated: 2026-05-27 — Dynamic Multi-Market: 9z3.1.25 + 9z3.1.26 complete*
