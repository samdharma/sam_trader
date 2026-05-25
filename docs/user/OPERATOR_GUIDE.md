# SAM Trader V3 — Operator Guide

> **Audience:** Daily operators who run, monitor, and intervene in the trading system.  
> **Scope:** Pre-market routine, market-hours monitoring, post-market review, and incident response.

---

## Table of Contents

1. [Pre-Market Routine (05:00–08:00 HKT)](#1-pre-market-routine-05000800-hkt)
2. [Market Hours (09:30–16:00 US/Eastern)](#2-market-hours-09301600-useastern)
3. [Post-Market Routine](#3-post-market-routine)
4. [Incident Response](#4-incident-response)
5. [Command Reference](#5-command-reference)

---

## 1. Pre-Market Routine (05:00–08:00 HKT)

All configuration changes, code updates, and bundle adjustments **must** happen in this window.  
The system enforces a read-only stance outside this window via `sam deploy-window`.

### 1.1 Quick Status Check

```bash
# Check all containers
docker exec sam-services sam status

# Deep health check
docker exec sam-services sam health
```

Expected output:

```
sam-postgres       Up 12 hours   5432/tcp
sam-redis          Up 12 hours   6379/tcp
sam-futu-opend     Up 12 hours   11111/tcp
sam-trader         Up 12 hours   (health: healthy)
sam-services       Up 12 hours   8080/tcp
```

### 1.2 Git Pull + Code Review

```bash
# On the host
cd ~/sam_trader
git pull

# Review what changed
git log --oneline -5
```

> **Rule:** Never deploy untested code during market hours. If `git pull` brings new commits, evaluate risk before applying.

### 1.3 Preflight Checks

```bash
docker exec sam-services sam preflight
```

Preflight validates:
- Deployment window is active
- All containers are healthy
- Bundle schema is valid
- Strategy classes are importable
- PostgreSQL and Redis are reachable

**Exit codes:**
- `0` — all checks passed
- `1` — warnings only (non-blocking)
- `2` — blocking issues found; `sam apply` will abort

Skip window check (for emergency fixes):

```bash
docker exec sam-services sam preflight --skip-window
```

### 1.4 Build Images (If Code Changed)

```bash
./deploy.sh --with-futu --with-services --build start
```

This is equivalent to:
1. `git pull`
2. `docker compose build`
3. Sequential start with health gating

### 1.5 Apply Bundle Changes

If only `config/bundles.yaml` changed (no code changes):

```bash
# Review pending changes
docker exec sam-services sam bundle-diff

# Validate
docker exec sam-services sam validate-bundles

# Apply (snapshot → restart → verify)
docker exec sam-services sam apply
```

The `sam apply` pipeline runs 4 steps automatically:

| Step | Command | Purpose |
|------|---------|---------|
| 1 | `preflight` | Block if checks fail |
| 2 | `snapshot` | Save current bundle state to Redis |
| 3 | `restart` | Graceful restart with state-save handshake |
| 4 | `verify` | Post-restart health check |

**Dry-run** (preview without changes):

```bash
docker exec sam-services sam apply --dry-run
```

### 1.6 Verify Post-Apply

```bash
# Health check
docker exec sam-services sam health

# Confirm active bundles
docker exec sam-services sam snapshot --list

# Check trader logs for clean startup
docker exec sam-services sam logs sam-trader
```

### 1.7 Pre-Market Pipeline (Optional)

Run the full pre-market analysis pipeline:

```bash
# Gap scan → AI scoring → bundle generation → readiness report
docker exec sam-services sam readiness

# Or run individual stages
docker exec sam-services sam gapscan --market US --pass 1
docker exec sam-services sam watchlist --market US
```

---

## 2. Market Hours (09:30–16:00 US/Eastern)

### 2.1 Monitoring via Dashboard

Open the dashboard in a browser:

```
http://localhost:8080
```

The dashboard shows:
- **Health indicators** — all services UP/DOWN
- **Recent fills** — last 20 fills from PostgreSQL
- **Open positions** — non-zero positions
- **Realized P&L** — per-strategy P&L from Redis (`sam:pnl:*`)
- **Auto-refresh** — every 30 seconds

### 2.2 CLI Health Checks

```bash
# Quick pulse check
docker exec sam-services sam health

# Quote check
docker exec sam-services sam quote TSLA.NASDAQ

# Version / build info
docker exec sam-services sam version
```

### 2.3 Log Monitoring

```bash
# Live trader logs
docker exec sam-services sam logs sam-trader

# Or follow via Docker
docker logs -f sam-trader

# Rotate logs if they grow too large
docker exec sam-services sam rotate-logs
```

### 2.4 Safety Monitor

The circuit-breaker monitor runs automatically via cron, but you can trigger it manually:

```bash
docker exec sam-services sam safety-monitor
```

It checks:
- **Daily PnL breaker** — realized loss exceeds `max_daily_loss`
- **Rejection streak breaker** — 3+ consecutive rejections per instrument
- **Connectivity breaker** — venue disconnected for > 60s

If a breaker trips, the system may:
- Emit `StrategyHaltRequest` (per-strategy pause)
- Emit global `HALTED` state (all trading stopped)
- Publish alert to dashboard and logs

---

## 3. Post-Market Routine

### 3.1 Capture End-of-Day Snapshot

```bash
docker exec sam-services sam snapshot
```

This saves:
- Git commit hash
- Bundle configuration hash
- Active strategy list
- Full bundle metadata

List recent snapshots:

```bash
docker exec sam-services sam snapshot --list
```

Show full details of the latest snapshot:

```bash
docker exec sam-services sam snapshot --show 1
```

### 3.2 Review Fills and P&L

```bash
# Performance stats (last 30 days)
docker exec sam-services sam performance

# Performance for a specific strategy
docker exec sam-services sam performance --strategy tsla-orb-15m-futu --days 7

# Or query PostgreSQL directly
psql -h localhost -p 5432 -U sam -d sam_trader -c "
  SELECT venue, instrument_id, side, fill_price, fill_qty, commission, ts_event
  FROM fills
  WHERE ts_event >= CURRENT_DATE
  ORDER BY ts_event DESC;
"
```

### 3.3 Bundle Adjustments for Next Day

1. Edit `config/bundles.yaml` on the host:
   - Enable/disable bundles
   - Adjust risk limits
   - Bump versions for config changes

2. Validate:
   ```bash
   docker exec sam-services sam validate-bundles
   ```

3. Do **not** apply now — wait for the next pre-market window.  
   The system is read-only outside 05:00–08:00 HKT.

### 3.4 Backup

```bash
# Automated backup (runs at 06:00 HKT weekdays via cron)
docker exec sam-services sam backup

# Restore from a specific date (if needed)
docker exec sam-services sam restore 20240520
```

Backups include:
- PostgreSQL dump (`pg_dump`)
- Redis RDB (`BGSAVE`)
- Futu OpenD volume
- `config/` directory

Retention: 30 days.

---

## 4. Incident Response

### 4.1 Severity Levels

| Level | Condition | Operator Action |
|-------|-----------|-----------------|
| **P0** | System-wide halt, data loss, unauthorized trades | `sam kill` → investigate → rollback |
| **P1** | Single venue down, repeated rejections, large slippage | `sam halt` → diagnose → `sam resume` or restart |
| **P2** | Dashboard unreachable, log rotation failure | Fix service, no trading impact |
| **P3** | Cosmetic issues, stale metrics | Fix at next maintenance window |

### 4.2 Emergency Kill Switch

Cancel all orders and halt trading **immediately**:

```bash
docker exec sam-services sam kill
```

Effect:
- Publishes `HALTED` to Redis `sam:safety_state`
- `LiveRiskEngine` rejects all new orders
- All open orders are cancelled
- Position-close-only mode

### 4.3 Halt (Less Severe)

Halt trading but preserve state:

```bash
docker exec sam-services sam halt
```

Effect:
- Same as `kill` but no position-close enforcement
- Useful when you want to stop new entries but keep stops active

### 4.4 Resume

Clear halt state and resume trading:

```bash
# Only resume if root cause is fixed
docker exec sam-services sam resume
```

> **Caution:** Do not resume until you have diagnosed and fixed the issue.

### 4.5 Rollback Procedure

If a bad deploy caused the incident:

```bash
# 1. Kill trading
docker exec sam-services sam kill

# 2. Stop stack
./deploy.sh stop

# 3. Checkout previous tag
git checkout v1.1.0

# 4. Rebuild and start
./deploy.sh --with-futu --with-services --build start

# 5. Verify health
docker exec sam-services sam health

# 6. Resume trading
docker exec sam-services sam resume
```

If only bundles are bad (no code change):

```bash
# 1. Revert bundles.yaml from git
git checkout HEAD -- config/bundles.yaml

# 2. Apply (snapshot + restart + verify)
docker exec sam-services sam apply
```

### 4.6 Force Restart (When Graceful Hangs)

```bash
# Skip state-save handshake
docker exec sam-services sam restart --force

# Or restart only sam-trader
docker compose -f docker/docker-compose.yml restart sam-trader
```

### 4.7 Check Incident Logs

```bash
# Trader logs
docker exec sam-services sam logs sam-trader

# Services logs
docker exec sam-services sam logs sam-services

# Futu logs
docker exec sam-services sam logs sam-futu-opend

# PostgreSQL logs
docker exec sam-services sam logs sam-postgres
```

---

## 5. Command Reference

### 5.1 Host-Side Commands (`deploy.sh`)

```bash
./deploy.sh --with-futu --with-services start     # Start stack
./deploy.sh --with-futu --with-services --build start  # Update + rebuild
./deploy.sh --tag v1.2.0 --build                  # Deploy specific tag
./deploy.sh --setup                               # Re-run wizard
./deploy.sh stop                                  # Stop all containers
```

### 5.2 Container-Side Commands (`sam` CLI)

Run all `sam` commands inside `sam-services`:

```bash
docker exec sam-services sam <command>
```

| Command | Purpose |
|---------|---------|
| `status` | Show container statuses |
| `health` | Deep health check (PG, Redis, Futu, trader) |
| `preflight` | Pre-deploy validation |
| `apply` | Snapshot → restart → verify pipeline |
| `snapshot` | Capture or list system state checkpoints |
| `bundle-diff` | Show pending bundle changes |
| `validate-bundles` | Validate bundle YAML |
| `version` | Show git tag/commit and build time |
| `backup` | Run backup (PG + Redis + config) |
| `restore <date>` | Restore from backup |
| `logs <service>` | Show logs for a service |
| `restart` | Graceful restart of sam-trader |
| `restart --force` | Force restart (skip state-save) |
| `rotate-logs` | Rotate oversized logs |
| `deploy-window` | Check if inside maintenance window |
| `quote <symbol>` | Real-time quote |
| `watchlist` | Show pre-market watchlist |
| `gapscan` | Run gap scanner |
| `readiness` | Full pre-market pipeline + report |
| `pipeline` | Run pipeline stages |
| `performance` | Show Nautilus performance stats |
| `kill` | Emergency kill switch |
| `halt` | Halt trading |
| `resume` | Resume trading |
| `safety-monitor` | Run circuit-breaker checks |

### 5.3 Options

| Option | Applies To | Description |
|--------|------------|-------------|
| `--json` | Most `sam` commands | Output structured JSON |
| `--dry-run` | `sam apply` | Preview without changes |
| `--skip-window` | `sam preflight`, `sam apply` | Skip maintenance window check |
| `--no-backtest` | `sam validate-bundles` | Skip backtest smoke test |
| `--force` | `sam restart` | Skip state-save handshake |
| `--strategy <id>` | `sam performance` | Filter by strategy |
| `--days <n>` | `sam performance` | Lookback days (default 30) |

### 5.4 Cron Schedule (Inside `sam-services`)

| Time (HKT) | Command | Purpose |
|------------|---------|---------|
| 02:00 | `sam safety-monitor` | Nightly circuit-breaker check |
| 03:00 | `sam rotate-logs` | Log rotation and cleanup |
| 06:00 | `sam backup` | Daily backup (weekdays) |
| 06:30 | `sam performance` | Nightly performance analysis |
| 08:00 | `sam readiness` | Pre-market pipeline |

---

*Last updated: 2026-05-25*  
*See also: [`DEPLOY_GUIDE.md`](./DEPLOY_GUIDE.md), [`BUNDLE_GUIDE.md`](./BUNDLE_GUIDE.md)*
