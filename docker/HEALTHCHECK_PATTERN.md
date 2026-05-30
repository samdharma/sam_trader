# Docker Health Check Pattern — SAM Trader V3

## Overview

Every container in the `sam-trader` stack uses a **3-layer health check** to maximize reliability and minimize false positives:

| Layer | Name | Purpose | Typical Failure Mode |
|-------|------|---------|----------------------|
| L1 | Process | Verify the main process is alive | Crash, OOM kill, zombie |
| L2 | Socket / Service | Verify the service is listening / responding | Port bind failure, network partition |
| L3 | Protocol / Application | Verify the application logic is healthy | Login failure, deadlock, startup stall |

## Timing Parameters

All containers use the **same timing parameters** for consistency:

| Parameter | Value |
|-----------|-------|
| `interval` | `30s` |
| `timeout` | `10s` |
| `start_period` | `60s` |
| `retries` | `3` |

> **Rationale:** 30s interval avoids health-check spam. 60s start-period gives slow-starting services (IB Gateway, Futu OpenD) enough time to initialize. 3 retries × 10s timeout = 30s of tolerance before marking unhealthy.

## Per-Container Checks

### sam-postgres (PostgreSQL 16)

| Layer | Command |
|-------|---------|
| L1 | `pgrep postgres` |
| L2 | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` |
| L3 | `psql -U $POSTGRES_USER -d $POSTGRES_DB -c 'SELECT 1'` |

### sam-redis (Redis 7)

| Layer | Command |
|-------|---------|
| L1 | `pgrep redis-server` |
| L2 | `redis-cli ping` → expect `PONG` |
| L3 | `redis-cli INFO server` → expect `redis_version` |

> Authenticates with `REDIS_PASSWORD` when set.

### sam-trader (Nautilus TradingNode)

| Layer | Command |
|-------|---------|
| L1 | `pgrep python` |
| L2 | `cat /proc/1/cmdline \| grep -q python` |
| L3 | Optional TCP port check (if a fixed port is configured) |

> Nautilus TradingNode does not expose a fixed management port by default, so L3 is a no-op fallback.

### sam-futu-opend (Futu OpenD)

| Layer | Command |
|-------|---------|
| L1 | `pgrep -x FutuOpenD` |
| L2 | TCP connect to `localhost:11111` |
| L3 | Log scan of most recent GTWLog for "Login successful" + failure patterns |

> Implemented in `docker/futu-opend/healthcheck.sh` (copied into the image).  
> Filters to `GTWLog_*` files only — `.ftlog` (internal binary logs) and `Monitor.log` are excluded because they don't contain login status text.  
> **⚠️ Known issue (sam_trader-2vj):** L3 scans only the single most recent GTWLog file. Futu rotates log files during operation; newer files may not contain "Login successful" from startup, causing false-unhealthy. Fix: scan **all** GTWLog files with `grep -lq`.

### sam-ib-gateway (IB Gateway)

| Layer | Command |
|-------|---------|
| L1 | `pgrep java` |
| L2 | TCP connect to `localhost:4004` |
| L3 | TCP connect to `localhost:4004` (API readiness) |

> IB Gateway's proprietary API does not expose a simple HTTP health endpoint, so L2 and L3 both use TCP socket readiness as the proxy.

### sam-services (Operations container)

| Layer | Command |
|-------|---------|
| L1 | `pgrep python` |
| L2 | TCP connect to `localhost:8080` |
| L3 | `curl -sf http://localhost:8080/health` (falls back to success if endpoint does not exist) |

> The HTTP `/health` endpoint is optional; when absent the container is still considered healthy as long as L1 and L2 pass.

## Implementation Notes

- **CMD-SHELL vs CMD:** All checks use `CMD-SHELL` so that shell operators (`&&`, `||`, `>`, `|`) and environment variables work naturally.
- **Environment variables:** Inside `CMD-SHELL`, use `$$VAR` in `docker-compose.yml` to pass the variable through to the container shell.
- **Exit codes:** A check must return exit code `0` to be considered healthy. Any non-zero exit code counts as a failure.
- **Logging:** Health-check output is captured by Docker and visible via `docker inspect --format='{{.State.Health}}' <container>`.

## References

- Docker Compose healthcheck docs: https://docs.docker.com/compose/compose-file/05-services/#healthcheck
- `docker-compose.yml` in this repo for the live configuration.
- `docker/futu-opend/healthcheck.sh` for the Futu OpenD script implementation.
