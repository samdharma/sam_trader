# SAM Trader V3 — Deploy Guide

> **Audience:** New operators installing SAM Trader for the first time.  
> **Scope:** Prerequisites, first-run wizard, daily update workflow, and troubleshooting.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [First-Time Installation](#2-first-time-installation)
3. [Daily Update Workflow](#3-daily-update-workflow)
4. [Troubleshooting](#4-troubleshooting)
5. [Reference: deploy.sh Flags](#5-reference-deploysh-flags)

---

## 1. Prerequisites

### 1.1 Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | Apple Silicon (M1+) or x86_64, 4 cores | 8 cores |
| RAM | 8 GB | 16 GB |
| Disk | 20 GB free SSD | 50 GB free SSD |
| Network | Stable broadband | Low-latency connection |

### 1.2 Software

| Tool | Version | Verify Command |
|------|---------|----------------|
| macOS | 13 (Ventura) or newer | `sw_vers` |
| Docker Desktop | 4.25+ | `docker --version` |
| Docker Compose | v2+ | `docker compose version` |
| Git | 2.40+ | `git --version` |
| Python | 3.12+ (host-side wizard only) | `python3 --version` |

### 1.3 Broker Accounts

- **Futu OpenD** — FUTUBULL account with API access enabled.  
  See [`FUTU_FIRST_LOGIN.md`](./FUTU_FIRST_LOGIN.md) for first-time OpenD login and MD5 password generation.
- **IBKR** (optional) — Interactive Brokers account with TWS/Gateway API permissions.  
  Enable *“Allow connections from localhost only”* if running inside Docker.

### 1.4 Network

- Outbound ports required:
  - `443` — Docker image pulls, GitHub
  - `11111` — Futu OpenD (internal to `sam-net`)
  - `4004` — IB Gateway (internal to `sam-net`)
  - `5432` — PostgreSQL (localhost, optional for ad-hoc queries)
  - `6379` — Redis (localhost, optional for debugging)
  - `8080` — Dashboard (localhost)

---

## 2. First-Time Installation

### 2.1 Clone the Repository

```bash
git clone https://github.com/samdharma/sam_trader.git
cd sam_trader
```

### 2.2 Run the First-Run Wizard

The wizard generates `.env` from `.env.example` with interactive prompts.

```bash
./deploy.sh --setup
```

**Prompts you will see:**

| Prompt | Example | Notes |
|--------|---------|-------|
| Trader ID | `sam_trader` | Letters, numbers, underscores only |
| Environment | `paper` or `live` | Use `paper` until validated |
| Enable Futu broker? | `y` | Required for US/HK market data |
| Futu account ID | `user@email.com` | Futu login email or phone |
| Futu password | (masked) | MD5-hashed automatically |
| Futu trade-unlock password | (masked) | Optional; skip if not trading |
| Enable IBKR broker? | `n` | Enable later if needed |
| PostgreSQL password | `sam_secret` | Change from default in production |
| Redis password | (empty) | Optional local-only password |

The wizard writes `.env` with `600` permissions (owner read/write only).

### 2.3 Start the Stack

```bash
# Futu-only deployment (most common first-run)
./deploy.sh --with-futu start

# With operations container
./deploy.sh --with-futu --with-services start

# Full stack (Futu + IB + services)
./deploy.sh --with-futu --with-ib --with-services start
```

**Startup sequence (automatic):**

1. `sam-postgres` — waits for health (L3: `SELECT 1`)
2. `sam-redis` — waits for health (L3: `PING`)
3. `sam-futu-opend` — waits for health (L3: log scan)
4. `sam-ib-gateway` — waits for health (if `--with-ib`)
5. `sam-trader` — waits for health (L1: process + L3: cmdline check)
6. `sam-services` — waits for health (if `--with-services`)

**Success indicator:**

```
INFO: Stack is up
INFO: Ops commands: docker exec sam-services sam <command>
```

### 2.4 Verify First Run

```bash
# Check all containers
docker exec sam-services sam status

# Deep health check
docker exec sam-services sam health

# View trader logs
docker exec sam-services sam logs sam-trader
```

Expected `sam health` output:

```json
{
  "overall": "HEALTHY",
  "checks": {
    "postgres": {"status": "UP"},
    "redis": {"status": "UP"},
    "futu_opend": {"status": "UP", "health": "healthy"},
    "sam_trader": {"status": "UP", "health": "healthy"}
  }
}
```

### 2.5 Configure Bundles

Copy the example bundle registry and edit for your strategies:

```bash
cp config/bundles.example.yaml config/bundles.yaml
# Edit config/bundles.yaml with your instruments and parameters
```

Validate before applying:

```bash
docker exec sam-services sam validate-bundles
```

Apply bundles (graceful restart during maintenance window):

```bash
docker exec sam-services sam apply
```

---

## 3. Daily Update Workflow

### 3.1 Quick Morning Start (Already Configured)

If `.env` and `config/bundles.yaml` are already in place, simply:

```bash
./deploy.sh --with-futu --with-services start
```

### 3.2 Code Update + Rebuild

Run this **inside the 5am–8am HKT maintenance window**:

```bash
# Pull latest code, rebuild images, restart stack
./deploy.sh --with-futu --with-services --build start
```

What `--build` does:
1. `git pull` (or `git fetch --tags` if `--tag` is used)
2. `docker compose build` for selected profiles
3. `docker compose up -d` with health gating

### 3.3 Apply Bundle Changes Without Rebuild

If only `config/bundles.yaml` changed (no code changes):

```bash
docker exec sam-services sam apply
```

This runs the 4-step pipeline:
1. **Preflight** — checks deployment window, bundle schema, health
2. **Snapshot** — saves current bundle state to Redis
3. **Restart** — graceful restart with state-save handshake
4. **Verify** — post-restart health check

### 3.4 Deploy a Specific Git Tag

```bash
./deploy.sh --with-futu --tag v1.2.0 --build start
```

### 3.5 Stop the Stack

```bash
./deploy.sh stop
```

This runs `docker compose down` across all profiles.

---

## 4. Troubleshooting

### 4.1 Container Fails to Become Healthy

```bash
# Check logs for a specific service
docker exec sam-services sam logs <service>

# Services: sam-trader, sam-postgres, sam-redis, sam-futu-opend, sam-ib-gateway, sam-services
# Or use Docker directly:
docker logs --tail 100 sam-trader
docker logs --tail 100 sam-futu-opend
```

**Common causes:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| `sam-futu-opend` unhealthy | Futu credentials missing/invalid | Re-run `./deploy.sh --setup`, verify `FUTU_ACCOUNT_PWD_MD5` |
| `sam-trader` unhealthy | Cannot connect to Futu OpenD | Check `FUTU_OPEND_HOST` in `.env` |
| `sam-postgres` unhealthy | Port conflict or bad password | Check `POSTGRES_PORT` and `POSTGRES_PASSWORD` |
| `sam-ib-gateway` unhealthy | IB login failed or 2FA pending | Open VNC at `localhost:5900` |

### 4.2 Futu OpenD Authentication Errors

```bash
# Telnet into OpenD to check status
telnet localhost 22222
# Or from inside the container:
docker exec sam-futu-opend sh -c "pgrep -a FutuOpenD"
```

See [`FUTU_FIRST_LOGIN.md`](./FUTU_FIRST_LOGIN.md) for terminal access and MD5 generation.

### 4.3 Graceful Restart Hangs

If `sam apply` times out during restart:

```bash
# Force restart (skips state-save handshake)
docker exec sam-services sam restart --force

# Or restart only sam-trader
docker compose -f docker/docker-compose.yml restart sam-trader
```

### 4.4 Bundle Validation Fails

```bash
# See specific errors
docker exec sam-services sam validate-bundles --no-backtest

# Check for missing strategy classes
python3 -c "from sam_trader.strategies.orb import OrbStrategy; print('OK')"
```

### 4.5 Redis Connection Issues

```bash
# Test from sam-services container
docker exec sam-services redis-cli -h sam-redis ping

# If password-protected
docker exec sam-services redis-cli -h sam-redis -a "$REDIS_PASSWORD" ping
```

### 4.6 PostgreSQL Connection Issues

```bash
# Test from host (if port is mapped)
psql -h localhost -p 5432 -U sam -d sam_trader -c "SELECT 1"

# From inside sam-services
docker exec sam-services psql -h sam-postgres -U sam -d sam_trader -c "SELECT 1"
```

### 4.7 Disk Space

```bash
# Check Docker disk usage
docker system df -v

# Rotate logs manually
docker exec sam-services sam rotate-logs

# Prune old images (caution)
docker image prune -a
```

### 4.8 Reset Everything (Nuclear Option)

```bash
# Stop and remove containers + volumes
./deploy.sh stop
docker volume rm sam_trader_postgres_data sam_trader_redis_data

# Re-run wizard and start fresh
./deploy.sh --setup
./deploy.sh --with-futu --with-services start
```

> **Warning:** This deletes all PostgreSQL data and Redis state. Back up first if needed:
> ```bash
> docker exec sam-services sam backup
> ```

---

## 5. Reference: deploy.sh Flags

```bash
./deploy.sh [options] [action]
```

| Flag | Description |
|------|-------------|
| `--with-futu` | Include Futu OpenD broker profile |
| `--with-ib` | Include IB Gateway broker profile |
| `--with-services` | Include sam-services operations container |
| `--build` | Build images before starting (or build only if no explicit start) |
| `--tag <tag>` | Checkout a specific Git tag before building |
| `--setup` | Re-run first-run wizard to regenerate `.env` |
| `-h, --help` | Show usage |

| Action | Description |
|--------|-------------|
| `start` (default) | Start the stack with health gating |
| `stop` | Stop all containers |
| `build` | Build images without starting |

### Common Commands Cheat Sheet

```bash
# First run
./deploy.sh --setup
./deploy.sh --with-futu --with-services start

# Daily update (maintenance window)
./deploy.sh --with-futu --with-services --build start

# Quick status
docker exec sam-services sam status

# Deep health
docker exec sam-services sam health

# Apply bundle changes
docker exec sam-services sam apply

# Emergency stop
./deploy.sh stop
```

---

*Last updated: 2026-05-25*  
*See also: [`BUNDLE_GUIDE.md`](./BUNDLE_GUIDE.md), [`OPERATOR_GUIDE.md`](./OPERATOR_GUIDE.md)*
