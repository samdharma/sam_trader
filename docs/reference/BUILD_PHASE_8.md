# Build Phase 8 — sam-services Container

> **Status:** Not Started  
> **Goal:** Operations container with CLI, cron, health checks, backup, quote fetcher. Decoupled from sam-trader.  
> **Prev Phase:** [BUILD_PHASE_4.md](./BUILD_PHASE_4.md) — Futu Instrument Provider & TradingNode Integration (can start after Phase 4)  
> **Next Phase:** [BUILD_PHASE_9.md](./BUILD_PHASE_9.md) — Pre-Market Pipeline

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    sam-services Container                     │
│                    (independent lifecycle)                    │
├──────────────────────────────────────────────────────────────┤
│  CLI (click or argparse)                                      │
│    ├── sam status  →  docker ps + health                     │
│    ├── sam health  →  deep health check                      │
│    ├── sam backup  →  pg_dump + config backup                │
│    ├── sam restore →  restore from backup                    │
│    ├── sam logs    →  docker logs wrapper                    │
│    └── sam restart →  graceful restart via Redis state       │
├──────────────────────────────────────────────────────────────┤
│  Cron                                                         │
│    ├── Daily backup  @ 16:30 ET                              │
│    └── Log rotation  @ 03:00 HKT                             │
├──────────────────────────────────────────────────────────────┤
│  Quote Fetcher                                                │
│    └── sam quote TSLA.NASDAQ → Redis cache lookup            │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered Reference — Docker Service

```yaml
# docker-compose.yml snippet
  sam-services:
    build:
      context: .
      dockerfile: Dockerfile.services
    container_name: sam-services
    profiles: ["services"]
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/app/config:ro
      - ./logs:/app/logs
      - ./backups:/app/backups
    environment:
      - POSTGRES_DSN=postgresql://user:pass@sam-postgres:5432/sam
      - REDIS_URL=redis://sam-redis:6379/0
    networks:
      - sam-net
```

---

## 3. Pre-Discovered Reference — CLI Patterns

```python
import click
import subprocess

@click.group()
def cli():
    """SAM Trader operations CLI."""
    pass

@cli.command()
def status():
    """Show container status."""
    subprocess.run(["docker", "ps", "--filter", "name=sam-"])

@cli.command()
@click.argument("service")
def logs(service: str):
    """Tail logs for a service."""
    subprocess.run(["docker", "logs", "-f", f"sam-{service}"])
```

---

## 4. Pre-Discovered Reference — Cron in Container

```dockerfile
# Dockerfile.services
FROM python:3.12-slim
RUN apt-get update && apt-get install -y cron
COPY crontab /etc/cron.d/sam-cron
RUN chmod 0644 /etc/cron.d/sam-cron && crontab /etc/cron.d/sam-cron
CMD ["cron", "-f"]
```

```cron
# crontab
30 16 * * * /app/.venv/bin/python -m sam_trader.cli backup >> /app/logs/backup.log 2>&1
0 3 * * * /app/.venv/bin/python -m sam_trader.cli rotate_logs >> /app/logs/rotate.log 2>&1
```

---

## 5. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p8-dockerfile` | Dockerfile.services | Lightweight Python 3.12 image | ✅ Small |
| `sam-p8-cli` | sam CLI tool | status, health, backup, restore, logs, restart | ✅ Medium |
| `sam-p8-cron` | Cron scheduler | Daily backup, log rotation | ✅ Small |
| `sam-p8-quote` | Quote fetcher | Redis cache query for Futu + IB quotes | ✅ Small |
| `sam-p8-deploy-decouple` | Deploy decoupling | Move ops commands from deploy.sh to sam-services | ✅ Medium |
| `sam-p8-verify` | Verify sam-services | Integration test: CLI works, cron runs, restart safe | ✅ Medium |

**No decomposition needed for Phase 8.** All tickets are well-scoped.

---

*Last updated: 2026-05-21*
