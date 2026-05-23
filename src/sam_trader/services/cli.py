"""SAM Trader CLI — unified operations and deployment commands.

Usage (inside sam-services container):
    sam status
    sam health
    sam backup
    sam restore 20240520
    sam logs [sam-trader]
    sam restart
    sam quote TSLA.NASDAQ
    sam performance [--strategy <id>] [--days 30]
    sam deploy [--tag v1.2.3]
    sam hotfix src/sam_trader/strategies/orb.py
    sam update
    sam rollback v1.1.0
    sam version
    sam validate-bundles

"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import asyncpg
import click

from sam_trader.bundle_validation import validate_bundles
from sam_trader.services.backup import BackupError
from sam_trader.services.backup import backup as run_backup
from sam_trader.services.backup import restore as run_restore
from sam_trader.services.deploy_window import check_window as check_deploy_window
from sam_trader.services.pipeline import run_pipeline
from sam_trader.services.quote import format_quote, get_quote
from sam_trader.services.rotate_logs import rotate_logs

logger = logging.getLogger(__name__)

# Environment-driven defaults
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "sam-postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "sam_trader")
POSTGRES_USER = os.getenv("POSTGRES_USER", "sam")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "sam_secret")
REDIS_HOST = os.getenv("REDIS_HOST", "sam-redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
FUTU_CONTAINER = os.getenv("FUTU_CONTAINER", "sam-futu-opend")
DOCKER_BINARY = os.getenv("DOCKER_BINARY", "docker")
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/opt/sam_trader/backups"))
DEFAULT_BUNDLES_PATH = Path("config/bundles.yaml")

SAM_TRADER_CONTAINER = "sam-trader"
SAM_SERVICES_CONTAINER = "sam-services"
SAM_POSTGRES_CONTAINER = "sam-postgres"
SAM_REDIS_CONTAINER = "sam-redis"
SAM_IB_GATEWAY_CONTAINER = "sam-ib-gateway"


def _out(ctx: click.Context, data: dict[str, Any]) -> None:
    """Print structured output (JSON or readable table)."""
    if ctx.obj.get("json"):
        click.echo(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            click.echo(f"{key}: {value}")


def _run(
    cmd: list[str],
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command, returning the result."""
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise click.ClickException(f"Command failed: {' '.join(cmd)}\n{stderr}")
    return result


@click.group()
@click.option("--json", "output_json", is_flag=True, help="Output structured JSON.")
@click.pass_context
def cli(ctx: click.Context, output_json: bool) -> None:
    """SAM Trader V3 — operations and deployment CLI."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = output_json


# ---------------------------------------------------------------------------
# Deployment commands
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--tag", default=None, help="Git tag or branch to deploy.")
@click.pass_context
def deploy(ctx: click.Context, tag: str | None) -> None:
    """Git pull + rebuild + graceful restart."""
    result: dict[str, Any] = {"command": "deploy", "steps": []}

    # Git fetch
    r = _run(["git", "fetch", "--tags"], check=False)
    result["steps"].append({"git_fetch": r.returncode == 0})

    # Checkout tag/branch if provided
    if tag:
        r = _run(["git", "checkout", tag])
        result["steps"].append({"git_checkout": tag})
    else:
        r = _run(["git", "pull"])
        result["steps"].append({"git_pull": r.returncode == 0})

    # Rebuild
    r = _run(
        [
            DOCKER_BINARY,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "build",
            SAM_TRADER_CONTAINER,
        ],
        check=False,
    )
    result["steps"].append({"docker_build": r.returncode == 0})

    # Graceful restart via Redis state save signal
    _signal_restart()
    result["steps"].append({"restart_signal": "sent"})

    _out(ctx, result)


@cli.command()
@click.argument("module_path")
@click.pass_context
def hotfix(ctx: click.Context, module_path: str) -> None:
    """Copy updated module into running container + trigger reload."""
    src = Path(module_path)
    if not src.exists():
        raise click.ClickException(f"File not found: {module_path}")

    # Copy into container at matching path under /opt/sam_trader/src
    dest = f"{SAM_TRADER_CONTAINER}:/opt/sam_trader/{module_path}"
    _run([DOCKER_BINARY, "cp", str(src), dest])

    # Touch hotfix trigger file inside container to signal reload watcher
    _run(
        [
            DOCKER_BINARY,
            "exec",
            SAM_TRADER_CONTAINER,
            "touch",
            "/opt/sam_trader/.hotfix_trigger",
        ]
    )

    result = {
        "command": "hotfix",
        "source": str(src),
        "destination": dest,
        "status": "copied",
        "trigger": "/opt/sam_trader/.hotfix_trigger",
    }
    _out(ctx, result)


@cli.command()
@click.pass_context
def update(ctx: click.Context) -> None:
    """Git pull latest + rebuild + restart."""
    result: dict[str, Any] = {"command": "update", "steps": []}

    r = _run(["git", "pull"], check=False)
    result["steps"].append({"git_pull": r.returncode == 0})

    r = _run(
        [
            DOCKER_BINARY,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "build",
            SAM_TRADER_CONTAINER,
        ],
        check=False,
    )
    result["steps"].append({"docker_build": r.returncode == 0})

    _signal_restart()
    result["steps"].append({"restart_signal": "sent"})

    _out(ctx, result)


@cli.command()
@click.argument("tag")
@click.pass_context
def rollback(ctx: click.Context, tag: str) -> None:
    """Git checkout tag + rebuild + restart."""
    result: dict[str, Any] = {"command": "rollback", "tag": tag, "steps": []}

    r = _run(["git", "fetch", "--tags"], check=False)
    result["steps"].append({"git_fetch": r.returncode == 0})

    r = _run(["git", "checkout", tag])
    result["steps"].append({"git_checkout": tag})

    r = _run(
        [
            DOCKER_BINARY,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "build",
            SAM_TRADER_CONTAINER,
        ],
        check=False,
    )
    result["steps"].append({"docker_build": r.returncode == 0})

    _signal_restart()
    result["steps"].append({"restart_signal": "sent"})

    _out(ctx, result)


@cli.command()
@click.pass_context
def version(ctx: click.Context) -> None:
    """Show deployed version (git tag/commit + build time)."""
    tag_result = _run(["git", "describe", "--tags", "--always"], check=False)
    commit_result = _run(["git", "rev-parse", "--short", "HEAD"], check=False)
    build_time_result = _run(
        [
            DOCKER_BINARY,
            "inspect",
            "--format",
            "{{.Metadata.LastTagTime}}",
            f"{SAM_TRADER_CONTAINER}:latest",
        ],
        check=False,
    )

    result = {
        "command": "version",
        "git_tag": (
            tag_result.stdout.strip() if tag_result.returncode == 0 else "unknown"
        ),
        "git_commit": (
            commit_result.stdout.strip() if commit_result.returncode == 0 else "unknown"
        ),
        "image_build_time": (
            build_time_result.stdout.strip()
            if build_time_result.returncode == 0
            else "unknown"
        ),
    }
    _out(ctx, result)


# ---------------------------------------------------------------------------
# Operations commands
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show docker ps filtered to sam-* containers."""
    r = _run(
        [
            DOCKER_BINARY,
            "ps",
            "--filter",
            "name=sam-",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
        ],
        check=False,
    )
    lines = r.stdout.strip().split("\n") if r.stdout else []
    containers = []
    for line in lines[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) >= 2:
            containers.append(
                {
                    "name": parts[0],
                    "status": parts[1],
                    "ports": parts[2] if len(parts) > 2 else "",
                }
            )

    result = {
        "command": "status",
        "containers": containers,
        "raw": r.stdout.strip(),
    }
    _out(ctx, result)


@cli.command()
@click.pass_context
def health(ctx: click.Context) -> None:
    """Deep health check (PG, Redis, Futu OpenD, Nautilus)."""
    checks: dict[str, Any] = {}

    # PostgreSQL
    try:
        env = {**os.environ, "PGPASSWORD": POSTGRES_PASSWORD}
        r = subprocess.run(
            [
                "psql",
                "-h",
                POSTGRES_HOST,
                "-p",
                POSTGRES_PORT,
                "-U",
                POSTGRES_USER,
                "-d",
                POSTGRES_DB,
                "-c",
                "SELECT 1",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        checks["postgres"] = {
            "status": "UP" if r.returncode == 0 else "DOWN",
            "detail": r.stdout.strip(),
        }
    except Exception as exc:
        checks["postgres"] = {"status": "DOWN", "detail": str(exc)}

    # Redis
    try:
        redis_cmd = ["redis-cli", "-h", REDIS_HOST, "-p", REDIS_PORT, "ping"]
        if REDIS_PASSWORD:
            redis_cmd = [
                "redis-cli",
                "-h",
                REDIS_HOST,
                "-p",
                REDIS_PORT,
                "-a",
                REDIS_PASSWORD,
                "ping",
            ]
        r = subprocess.run(redis_cmd, capture_output=True, text=True, check=False)
        checks["redis"] = {
            "status": "UP" if "PONG" in r.stdout else "DOWN",
            "detail": r.stdout.strip(),
        }
    except Exception as exc:
        checks["redis"] = {"status": "DOWN", "detail": str(exc)}

    # Futu OpenD
    try:
        r = subprocess.run(
            [
                DOCKER_BINARY,
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                FUTU_CONTAINER,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        health_status = r.stdout.strip()
        checks["futu_opend"] = {
            "status": "UP" if health_status == "healthy" else "DOWN",
            "health": health_status,
        }
    except Exception as exc:
        checks["futu_opend"] = {"status": "DOWN", "detail": str(exc)}

    # Nautilus (sam-trader)
    try:
        r = subprocess.run(
            [
                DOCKER_BINARY,
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                SAM_TRADER_CONTAINER,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        health_status = r.stdout.strip()
        checks["sam_trader"] = {
            "status": "UP" if health_status == "healthy" else "DOWN",
            "health": health_status,
        }
    except Exception as exc:
        checks["sam_trader"] = {"status": "DOWN", "detail": str(exc)}

    all_up = all(c["status"] == "UP" for c in checks.values())
    result = {
        "command": "health",
        "overall": "HEALTHY" if all_up else "UNHEALTHY",
        "checks": checks,
    }
    _out(ctx, result)


@cli.command()
@click.pass_context
def backup(ctx: click.Context) -> None:
    """Trigger full backup (delegates to backup.py)."""
    try:
        archive_path = run_backup()
        result = {
            "command": "backup",
            "status": "success",
            "archive": str(archive_path),
        }
    except SystemExit:
        # backup.py exits 0 on weekend/holiday skip
        result = {
            "command": "backup",
            "status": "skipped",
            "reason": "weekend or trading holiday",
        }
    except BackupError as exc:
        raise click.ClickException(str(exc))
    _out(ctx, result)


@cli.command()
@click.argument("date")
@click.pass_context
def restore(ctx: click.Context, date: str) -> None:
    """Restore from date-specific archive (YYYYMMDD)."""
    try:
        run_restore(date)
        result = {
            "command": "restore",
            "status": "success",
            "date": date,
        }
    except BackupError as exc:
        raise click.ClickException(str(exc))
    _out(ctx, result)


@cli.command()
@click.argument("service", required=False, default="")
@click.pass_context
def logs(ctx: click.Context, service: str) -> None:
    """Tail logs for service or all sam-* containers."""
    result: dict[str, Any]
    if service:
        container = service if service.startswith("sam-") else f"sam-{service}"
        r = _run([DOCKER_BINARY, "logs", "--tail", "100", "-f", container], check=False)
        result = {"command": "logs", "service": container, "logs": r.stdout}
    else:
        # Fetch last 50 lines from all sam-* containers
        r = _run(
            [DOCKER_BINARY, "ps", "--filter", "name=sam-", "--format", "{{.Names}}"],
            check=False,
        )
        containers = [c for c in r.stdout.strip().split("\n") if c]
        all_logs: dict[str, str] = {}
        for container in containers:
            lr = _run([DOCKER_BINARY, "logs", "--tail", "50", container], check=False)
            all_logs[container] = lr.stdout
        result = {"command": "logs", "service": "all", "logs": all_logs}
    _out(ctx, result)


@cli.command()
@click.pass_context
def restart(ctx: click.Context) -> None:
    """Graceful restart of sam-trader via Redis state."""
    # Signal via Redis that a graceful restart is requested
    _signal_restart()
    result = {
        "command": "restart",
        "status": "signal_sent",
        "detail": "sam-trader will save state and restart",
    }
    _out(ctx, result)


@cli.command("rotate-logs")
@click.pass_context
def rotate_logs_cmd(ctx: click.Context) -> None:
    """Rotate oversized log files and purge old archives."""
    rotated, deleted = rotate_logs()
    result = {
        "command": "rotate-logs",
        "rotated": rotated,
        "deleted": deleted,
    }
    _out(ctx, result)


@cli.command("deploy-window")
@click.pass_context
def deploy_window_cmd(ctx: click.Context) -> None:
    """Check whether the current time is inside the deployment window."""
    active = check_deploy_window()
    result = {
        "command": "deploy-window",
        "active": active,
        "window": os.getenv("DEPLOY_WINDOW", "05:00-08:00"),
    }
    _out(ctx, result)


@cli.command()
@click.pass_context
def pipeline(ctx: click.Context) -> None:
    """Trigger the pre-market pipeline slot (Phase 9 placeholder)."""
    run_pipeline()
    result = {
        "command": "pipeline",
        "status": "triggered",
        "note": "Phase 9 placeholder",
    }
    _out(ctx, result)


@cli.command()
@click.argument("symbol")
@click.pass_context
def quote(ctx: click.Context, symbol: str) -> None:
    """Real-time quote from cache or broker."""
    result = get_quote(symbol)
    if ctx.obj.get("json"):
        _out(ctx, result)
    else:
        click.echo(format_quote(result))


@cli.command()
@click.option("--strategy", default=None, help="Filter by strategy ID.")
@click.option("--days", default=30, type=int, help="Lookback days (default 30).")
@click.pass_context
def performance(ctx: click.Context, strategy: str | None, days: int) -> None:
    """Display performance stats from Nautilus PortfolioAnalyzer results."""
    try:
        result = asyncio.run(_performance_query(strategy, days, ctx.obj.get("json")))
        _out(ctx, result)
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"Performance query failed: {exc}")


async def _performance_query(
    strategy: str | None, days: int, output_json: bool
) -> dict[str, Any]:
    """Query performance_stats PG table and return formatted result."""
    dsn = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        sql = """
            SELECT strategy_id, stat_name, stat_value
            FROM performance_stats
            WHERE date >= CURRENT_DATE - $1
        """
        params: list[Any] = [days]
        if strategy:
            sql += " AND strategy_id = $2"
            params.append(strategy)
        sql += " ORDER BY strategy_id, stat_name"

        rows = await conn.fetch(sql, *params)

        if not rows:
            return {
                "command": "performance",
                "days": days,
                "strategy": strategy,
                "stats": {},
                "note": "No performance stats found. Run PerformanceAnalyzer first.",
            }

        grouped: dict[str, dict[str, float | None]] = {}
        for row in rows:
            sid = row["strategy_id"]
            name = row["stat_name"]
            value = float(row["stat_value"]) if row["stat_value"] is not None else None
            grouped.setdefault(sid, {})[name] = value

        return {
            "command": "performance",
            "days": days,
            "strategy": strategy,
            "stats": grouped,
        }
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Bundle validation (ported from argparse CLI)
# ---------------------------------------------------------------------------


@cli.command("validate-bundles")
@click.option(
    "--path", type=click.Path(path_type=Path), default=str(DEFAULT_BUNDLES_PATH)
)
@click.option("--no-backtest", is_flag=True, help="Skip the backtest smoke test")
@click.pass_context
def validate_bundles_cmd(ctx: click.Context, path: Path, no_backtest: bool) -> None:
    """Validate bundle YAML: schema + strategy class + backtest gate."""
    if not path.exists():
        raise click.ClickException(f"Bundles file not found: {path}")

    result_obj = validate_bundles(path, backtest_gate=not no_backtest)

    bundles = []
    for bundle in result_obj.bundles:
        bundles.append(
            {
                "id": bundle.bundle_id,
                "passed": bundle.passed,
                "errors": bundle.errors,
                "warnings": bundle.warnings,
            }
        )

    result = {
        "command": "validate-bundles",
        "summary": result_obj.summary,
        "all_passed": result_obj.all_passed,
        "bundles": bundles,
    }
    _out(ctx, result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal_restart() -> None:
    """Signal sam-trader to gracefully restart via Redis, then docker restart."""
    # 1. Publish restart request to Redis so Nautilus can save state
    redis_cmd = [
        "redis-cli",
        "-h",
        REDIS_HOST,
        "-p",
        REDIS_PORT,
        "PUBLISH",
        "sam:restart_request",
        "graceful",
    ]
    if REDIS_PASSWORD:
        redis_cmd = [
            "redis-cli",
            "-h",
            REDIS_HOST,
            "-p",
            REDIS_PORT,
            "-a",
            REDIS_PASSWORD,
            "PUBLISH",
            "sam:restart_request",
            "graceful",
        ]
    subprocess.run(redis_cmd, capture_output=True, check=False)

    # 2. Trigger docker compose restart
    subprocess.run(
        [
            DOCKER_BINARY,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "restart",
            SAM_TRADER_CONTAINER,
        ],
        capture_output=True,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (also used by ``sam-validate-bundles`` console script)."""
    try:
        cli.main(args=argv, standalone_mode=False)
        return 0
    except click.ClickException as exc:
        click.echo(f"ERROR: {exc.message}", err=True)
        return 1
    except Exception as exc:
        click.echo(f"ERROR: {exc}", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
