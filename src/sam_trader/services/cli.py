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
    sam deploy, update, rollback, hotfix → Run deploy.sh on host:
    ./deploy.sh --build start

    sam version
    sam validate-bundles

"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import click
import yaml

from sam_trader.bundle_validation import validate_bundles
from sam_trader.market_config import MarketConfig
from sam_trader.services.backup import BackupError
from sam_trader.services.backup import backup as run_backup
from sam_trader.services.backup import restore as run_restore
from sam_trader.services.bar_downloader import (
    BarDownloader,
    BarDownloaderError,
    get_instruments_from_bundles,
)
from sam_trader.services.bundle_generator import (
    generate_bundles,
    publish_bundles_to_redis,
)
from sam_trader.services.deploy_window import check_window as check_deploy_window
from sam_trader.services.deploy_window import is_in_window
from sam_trader.services.gap_scanner import (
    CompositePrevCloseLoader,
    FutuKLinePrevCloseLoader,
    GapScannerConfig,
    PGFillPrevCloseLoader,
    PreMarketGapScanner,
)
from sam_trader.services.pipeline import run_pipeline
from sam_trader.services.pipeline_executor import (
    PipelineCandidate,
    PipelineExecutor,
    PipelineExecutorConfig,
    PipelineResult,
    PipelineStageRecord,
)
from sam_trader.services.quote import _redis_client, format_quote, get_quote
from sam_trader.services.quote_collector import QuoteCollectionService
from sam_trader.services.readiness_report import ReadinessReportGenerator
from sam_trader.services.regime_detection import Regime, RegimePrediction
from sam_trader.services.rotate_logs import rotate_logs
from sam_trader.services.safety import (
    cmd_halt,
    cmd_kill,
    cmd_resume,
    run_circuit_breaker_monitor,
)
from sam_trader.services.watchlist import (
    build_watchlist,
    load_watchlist_config,
)

# Optional redis import — graceful degradation if package is missing
_redis_cli: Any = None
try:
    import redis as _redis_mod_cli  # type: ignore[import-untyped]

    _redis_cli = _redis_mod_cli
except ImportError:  # pragma: no cover
    pass

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

STATE_SAVE_HANDSHAKE_TIMEOUT = int(os.getenv("STATE_SAVE_HANDSHAKE_TIMEOUT", "30"))
RESTART_HEALTH_TIMEOUT = int(os.getenv("RESTART_HEALTH_TIMEOUT", "60"))
SNAPSHOT_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


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
# Deployment commands (removed — use deploy.sh on host instead)
# ---------------------------------------------------------------------------


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
# Snapshot commands
# ---------------------------------------------------------------------------


def _get_active_bundle_ids(path: Path) -> list[str]:
    """Return bundle IDs with enabled=True from bundles YAML."""
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        bundles = raw.get("bundles", [])
        if not isinstance(bundles, list):
            return []
        return [
            str(b.get("id", "unknown"))
            for b in bundles
            if isinstance(b, dict) and b.get("enabled", True)
        ]
    except Exception:
        return []


def _get_bundle_snapshot_data(path: Path) -> dict[str, dict[str, Any]]:
    """Return mapping of bundle ID → raw bundle dict from YAML.

    Only includes *enabled* bundles so the snapshot reflects what is
    actually deployed.  The raw dicts are used by ``bundle diff`` to
    detect per-key configuration changes.
    """
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        bundles = raw.get("bundles", [])
        if not isinstance(bundles, list):
            return {}
        return {
            str(b["id"]): dict(b)
            for b in bundles
            if isinstance(b, dict) and b.get("enabled", True) and "id" in b
        }
    except Exception:
        return {}


def _create_snapshot(r: Any) -> dict[str, Any]:
    """Create a new system-state snapshot in Redis.

    Parameters
    ----------
    r : redis.Redis
        Connected Redis client.

    Returns
    -------
    dict[str, Any]
        Result dict with snapshot metadata.
    """
    commit_result = _run(["git", "rev-parse", "--short", "HEAD"], check=False)
    git_hash = (
        commit_result.stdout.strip() if commit_result.returncode == 0 else "unknown"
    )

    bundles_path = DEFAULT_BUNDLES_PATH
    bundles_hash = ""
    if bundles_path.exists():
        bundles_hash = hashlib.sha256(bundles_path.read_bytes()).hexdigest()

    timestamp = datetime.now(timezone.utc).isoformat()
    active_strategies = _get_active_bundle_ids(bundles_path)

    payload = {
        "git_hash": git_hash,
        "bundles_hash": bundles_hash,
        "timestamp": timestamp,
        "active_strategies": active_strategies,
        "bundles": _get_bundle_snapshot_data(bundles_path),
    }

    key = f"sam:snapshot:{timestamp}"
    r.set(key, json.dumps(payload), ex=SNAPSHOT_TTL_SECONDS)

    return {
        "command": "snapshot",
        "status": "created",
        "key": key,
        "git_hash": git_hash,
        "bundles_hash": bundles_hash,
        "timestamp": timestamp,
        "active_strategies": active_strategies,
    }


@cli.command()
@click.option("--list", "list_flag", is_flag=True, help="Show last 10 snapshots.")
@click.option(
    "--show",
    type=int,
    help="Show full details of snapshot N (1-based, newest first).",
)
@click.pass_context
def snapshot(ctx: click.Context, list_flag: bool, show: int | None) -> None:
    """Capture or inspect system state checkpoints in Redis."""
    if _redis_cli is None:
        raise click.ClickException("redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        raise click.ClickException(f"Redis connection failed: {exc}")

    if list_flag:
        _snapshot_list(ctx, r)
        return

    if show is not None:
        _snapshot_show(ctx, r, show)
        return

    result = _create_snapshot(r)
    _out(ctx, result)


def _snapshot_list(ctx: click.Context, r: Any) -> None:
    """List last 10 snapshots."""
    keys = sorted(r.keys("sam:snapshot:*"), reverse=True)
    entries = []
    for key in keys[:10]:
        raw = r.get(key)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        entries.append(
            {
                "timestamp": data.get("timestamp", ""),
                "git_hash": data.get("git_hash", ""),
            }
        )

    result = {
        "command": "snapshot",
        "action": "list",
        "count": len(entries),
        "entries": entries,
    }
    _out(ctx, result)


def _snapshot_show(ctx: click.Context, r: Any, n: int) -> None:
    """Show full details of snapshot N (1-based, newest first)."""
    keys = sorted(r.keys("sam:snapshot:*"), reverse=True)
    if n < 1 or n > len(keys):
        raise click.ClickException(f"Snapshot {n} not found (total: {len(keys)})")

    key = keys[n - 1]
    raw = r.get(key)
    if not raw:
        raise click.ClickException(f"Snapshot {n} data missing")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Corrupt snapshot data: {exc}")

    result = {
        "command": "snapshot",
        "action": "show",
        "index": n,
        "key": key,
        "git_hash": data.get("git_hash", ""),
        "bundles_hash": data.get("bundles_hash", ""),
        "timestamp": data.get("timestamp", ""),
        "active_strategies": data.get("active_strategies", []),
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


def _run_health_checks() -> dict[str, Any]:
    """Run deep health checks and return a dict of check results.

    Returns
    -------
    dict[str, Any]
        Mapping of service name → result dict with ``status`` and
        ``detail``/``health`` keys.
    """
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

    return checks


@cli.command()
@click.pass_context
def health(ctx: click.Context) -> None:
    """Deep health check (PG, Redis, Futu OpenD, Nautilus)."""
    checks = _run_health_checks()
    all_up = all(c["status"] == "UP" for c in checks.values())
    result = {
        "command": "health",
        "overall": "HEALTHY" if all_up else "UNHEALTHY",
        "checks": checks,
    }
    _out(ctx, result)


@cli.command("data-health")
@click.option(
    "--venue",
    default=None,
    help="Filter by venue (FUTU or IB).",
)
@click.option(
    "--instrument",
    default=None,
    help="Specific instrument ID (e.g., TSLA.NASDAQ).",
)
@click.option(
    "--threshold",
    default=300,
    type=int,
    help="Staleness threshold in seconds (default 300).",
)
@click.pass_context
def data_health(
    ctx: click.Context,
    venue: str | None,
    instrument: str | None,
    threshold: int,
) -> None:
    """Verify market data bar flow end-to-end after restart or fix.

    Queries Redis for the latest bar timestamp per instrument and reports
    staleness.  Returns exit code 0 only if every instrument has received
    a bar within the threshold.
    """
    if _redis_cli is None:
        raise click.ClickException("redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        raise click.ClickException(f"Redis connection failed: {exc}")

    # Determine instruments to check
    if instrument:
        instruments = [(instrument, venue or "")]
    else:
        instruments = _get_active_instruments(DEFAULT_BUNDLES_PATH, venue_filter=venue)
        if not instruments:
            msg = "No active bundles found"
            if venue:
                msg += f" for venue={venue}"
            raise click.ClickException(msg)

    now = datetime.now(timezone.utc)
    reports: list[dict[str, Any]] = []
    all_healthy = True

    for inst_id, inst_venue in instruments:
        report: dict[str, Any] = {
            "instrument_id": inst_id,
            "venue": inst_venue,
        }

        # Check venue connection status
        if inst_venue:
            conn_raw = r.get(f"sam:venue:conn:{inst_venue}")
            if conn_raw:
                venue_conn = conn_raw.split(":")[0]
                report["venue_connection"] = venue_conn
            else:
                report["venue_connection"] = "unknown"
        else:
            report["venue_connection"] = "unknown"

        # Check bar timestamp
        bar_raw = r.get(f"sam:bars:last:{inst_id}")
        if bar_raw:
            try:
                last_ts = datetime.fromisoformat(bar_raw)
                age_seconds = int((now - last_ts).total_seconds())
                report["last_bar_seconds_ago"] = age_seconds
                if age_seconds > threshold:
                    report["status"] = "STALE"
                    report["detail"] = (
                        f"last bar {age_seconds}s ago " f"(threshold {threshold}s)"
                    )
                    all_healthy = False
                else:
                    report["status"] = "OK"
                    report["detail"] = f"last bar {age_seconds}s ago"
            except Exception as exc:
                report["status"] = "ERROR"
                report["detail"] = f"Invalid timestamp in Redis: {exc}"
                all_healthy = False
        else:
            report["status"] = "MISSING"
            report["detail"] = (
                "No bar data in Redis — " "try 'sam probe-bars' (future command)"
            )
            all_healthy = False

        reports.append(report)

    result = {
        "command": "data-health",
        "overall": "HEALTHY" if all_healthy else "UNHEALTHY",
        "threshold_seconds": threshold,
        "instruments_checked": len(reports),
        "reports": reports,
    }

    if ctx.obj.get("json"):
        _out(ctx, result)
    else:
        lines = [
            f"Data Health (threshold: {threshold}s)",
            "=" * 50,
        ]
        for rep in reports:
            status = rep["status"]
            if status == "OK":
                marker = "OK"
            elif status in ("STALE", "MISSING"):
                marker = "FAIL"
            else:
                marker = "WARN"
            lines.append(f"{rep['instrument_id']:<20} [{marker}] {rep['detail']}")
        if not all_healthy:
            lines.append("")
            lines.append("Tip: If bars are missing, verify sam-trader is running and")
            lines.append(
                "      subscribed to the instruments. Future: 'sam probe-bars'"
            )
        click.echo("\n".join(lines))

    return 0 if all_healthy else 1  # type: ignore[return-value]


def _get_active_instruments(
    path: Path, venue_filter: str | None = None
) -> list[tuple[str, str]]:
    """Return (instrument_id, venue) tuples from enabled bundles.

    Parameters
    ----------
    path : Path
        Path to the bundles YAML file.
    venue_filter : str, optional
        If provided, only return bundles for this venue.

    Returns
    -------
    list[tuple[str, str]]
        List of (instrument_id, venue) tuples.

    """
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        bundles = raw.get("bundles", [])
        if not isinstance(bundles, list):
            return []
        result: list[tuple[str, str]] = []
        for b in bundles:
            if not isinstance(b, dict) or not b.get("enabled", True):
                continue
            venue = str(b.get("venue", "IB"))
            if venue_filter and venue != venue_filter:
                continue
            instrument_id = b.get("strategy", {}).get("config", {}).get("instrument_id")
            if instrument_id and isinstance(instrument_id, str):
                result.append((instrument_id, venue))
        return result
    except Exception:
        return []


def _run_preflight(skip_window: bool) -> tuple[dict[str, Any], int, list[str]]:
    """Run preflight checks and return (result_dict, exit_code, blocking_issues).

    Returns
    -------
    tuple[dict, int, list[str]]
        Result dict, exit code (0=pass, 1=warn, 2=fail), list of blocking issue IDs.
    """
    checks: dict[str, Any] = {}
    blocking_issues: list[str] = []
    warnings_list: list[str] = []

    # 1. Deploy window
    if skip_window:
        checks["deploy_window"] = {
            "status": "SKIPPED",
            "detail": "Bypassed via --skip-window",
        }
    else:
        window = os.getenv("DEPLOY_WINDOW", "05:00-08:00")
        active = is_in_window(window)
        if active:
            checks["deploy_window"] = {
                "status": "PASS",
                "detail": f"Window {window} is active",
            }
        else:
            checks["deploy_window"] = {
                "status": "FAIL",
                "detail": f"Window {window} is NOT active",
            }
            blocking_issues.append("deploy_window")

    # 2. Bundles valid
    bundles_path = DEFAULT_BUNDLES_PATH
    if bundles_path.exists():
        try:
            result_obj = validate_bundles(bundles_path, backtest_gate=False)
            if result_obj.all_passed:
                checks["bundles_valid"] = {
                    "status": "PASS",
                    "detail": result_obj.summary,
                }
            else:
                checks["bundles_valid"] = {
                    "status": "FAIL",
                    "detail": result_obj.summary,
                }
                blocking_issues.append("bundles_valid")
        except Exception as exc:
            checks["bundles_valid"] = {
                "status": "FAIL",
                "detail": f"Validation error: {exc}",
            }
            blocking_issues.append("bundles_valid")
    else:
        checks["bundles_valid"] = {
            "status": "FAIL",
            "detail": f"Bundles file not found: {bundles_path}",
        }
        blocking_issues.append("bundles_valid")

    # 3. Services healthy
    health_checks = _run_health_checks()
    services_up = all(c["status"] == "UP" for c in health_checks.values())
    if services_up:
        checks["services_healthy"] = {
            "status": "PASS",
            "detail": "All services UP",
            "checks": health_checks,
        }
    else:
        down = [k for k, v in health_checks.items() if v["status"] != "UP"]
        checks["services_healthy"] = {
            "status": "FAIL",
            "detail": f"Services DOWN: {', '.join(down)}",
            "checks": health_checks,
        }
        blocking_issues.append("services_healthy")

    # 4. Pending git changes (informational only)
    try:
        r = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
        )
        git_out = r.stdout.strip()
        if git_out:
            checks["git_status"] = {
                "status": "INFO",
                "detail": "Pending changes detected",
                "changes": git_out.split("\n"),
            }
        else:
            checks["git_status"] = {
                "status": "INFO",
                "detail": "Working tree clean",
                "changes": [],
            }
    except Exception as exc:
        checks["git_status"] = {
            "status": "INFO",
            "detail": f"Could not check git status: {exc}",
            "changes": [],
        }

    # 5. Pending bundle changes (compare hash in Redis)
    try:
        if bundles_path.exists():
            current_hash = hashlib.sha256(bundles_path.read_bytes()).hexdigest()
        else:
            current_hash = ""

        stored_hash: str | None = None
        if _redis_cli is not None:
            try:
                r = _redis_cli.Redis(
                    host=REDIS_HOST,
                    port=int(REDIS_PORT),
                    password=REDIS_PASSWORD or None,
                    decode_responses=True,
                    socket_connect_timeout=5,
                )
                stored_hash = r.get("sam:bundles:snapshot_hash")
            except Exception:
                pass

        if stored_hash is None:
            checks["bundle_changes"] = {
                "status": "WARN",
                "detail": (
                    "No snapshot hash in Redis — "
                    "run 'sam apply' once to establish baseline"
                ),
            }
            warnings_list.append("bundle_changes")
        elif current_hash != stored_hash:
            checks["bundle_changes"] = {
                "status": "WARN",
                "detail": "bundles.yaml differs from last deployed snapshot",
                "current_hash": current_hash[:16],
                "stored_hash": stored_hash[:16],
            }
            warnings_list.append("bundle_changes")
        else:
            checks["bundle_changes"] = {
                "status": "PASS",
                "detail": "bundles.yaml matches deployed snapshot",
            }
    except Exception as exc:
        checks["bundle_changes"] = {
            "status": "WARN",
            "detail": f"Could not compare bundle snapshot: {exc}",
        }
        warnings_list.append("bundle_changes")

    # Determine exit code
    if blocking_issues:
        overall = "FAIL"
        exit_code = 2
    elif warnings_list:
        overall = "WARN"
        exit_code = 1
    else:
        overall = "PASS"
        exit_code = 0

    result = {
        "command": "preflight",
        "overall": overall,
        "exit_code": exit_code,
        "checks": checks,
    }
    return result, exit_code, blocking_issues


@cli.command()
@click.option(
    "--skip-window",
    is_flag=True,
    help="Bypass deploy-window check (for testing).",
)
@click.pass_context
def preflight(ctx: click.Context, skip_window: bool) -> None:
    """Pre-update validation — dry-run, read-only.

    Checks deploy window, bundle validity, service health, git status,
    and pending bundle changes.  Exit code: 0=all-clear, 1=warnings,
    2=blocking issues.
    """
    result, exit_code, _blocking = _run_preflight(skip_window)
    _out(ctx, result)
    return exit_code  # type: ignore[return-value]


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
@click.option(
    "--force",
    is_flag=True,
    help="Skip state-save wait (emergency use only).",
)
@click.pass_context
def restart(ctx: click.Context, force: bool) -> None:
    """Graceful restart of sam-trader via Redis state."""
    result = _signal_restart(force=force)
    if result.get("status") in ("error", "aborted"):
        raise click.ClickException(result.get("detail", "Restart failed"))
    _out(ctx, result)


@cli.command("flush-cache")
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def flush_cache(ctx: click.Context, force: bool) -> None:
    """Emergency flush of the Redis cache database.

    Removes ALL keys from the selected Redis DB, clearing stale orders,
    strategy state, and any other cached data.  Use only when the node
    cannot start because of orphaned orders persisted across restarts.
    """
    if _redis_cli is None:
        raise click.ClickException("redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        raise click.ClickException(f"Redis connection failed: {exc}")

    if not force:
        click.confirm(
            "This will delete ALL keys in the Redis cache database. Continue?",
            abort=True,
        )

    try:
        before_count = r.dbsize()
    except Exception:
        before_count = "unknown"

    r.flushdb()

    result = {
        "command": "flush-cache",
        "status": "flushed",
        "keys_before": before_count,
    }
    _out(ctx, result)


def _run_verify() -> dict[str, Any]:
    """Post-restart verification: health checks + state-loaded confirmation.

    Returns
    -------
    dict[str, Any]
        Result with ``status``, ``health``, and ``state_loaded`` keys.
    """
    health_checks = _run_health_checks()
    all_up = all(c["status"] == "UP" for c in health_checks.values())

    state_loaded = False
    if _redis_cli is not None:
        try:
            r = _redis_cli.Redis(
                host=REDIS_HOST,
                port=int(REDIS_PORT),
                password=REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            state_loaded = bool(r.exists("sam:state_loaded"))
        except Exception as exc:
            logger.warning("Could not verify state_loaded in Redis: %s", exc)

    if all_up and state_loaded:
        status = "PASS"
        detail = "All services healthy and state loaded"
    elif all_up:
        status = "WARN"
        detail = "All services healthy but state_loaded not confirmed"
    else:
        status = "FAIL"
        down = [k for k, v in health_checks.items() if v["status"] != "UP"]
        detail = f"Services DOWN: {', '.join(down)}"

    return {
        "status": status,
        "detail": detail,
        "health": health_checks,
        "state_loaded": state_loaded,
    }


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run preflight only — no snapshot or restart.",
)
@click.option(
    "--skip-window",
    is_flag=True,
    help="Bypass deploy-window check.",
)
@click.pass_context
def apply(ctx: click.Context, dry_run: bool, skip_window: bool) -> None:
    """Orchestrated preflight → snapshot → restart → verify pipeline.

    The operator's one-button pre-market deploy.  Each step is logged
    with a timestamp.  Blocking preflight issues abort the pipeline
    before any mutating action.
    """
    steps: list[dict[str, Any]] = []
    start_time = datetime.now(timezone.utc)

    def _log_step(name: str, status: str, detail: str = "") -> None:
        ts = datetime.now(timezone.utc).isoformat()
        entry = {"step": name, "status": status, "timestamp": ts, "detail": detail}
        steps.append(entry)
        if status == "FAIL":
            logger.critical("apply step %s FAILED at %s: %s", name, ts, detail)
        else:
            logger.info("apply step %s %s at %s", name, status, ts)

    def _emit_progress(label: str, emoji: str = "▶") -> None:
        if not ctx.obj.get("json"):
            click.echo(f"{emoji}  {label}")

    # ------------------------------------------------------------------
    # 1. Preflight
    # ------------------------------------------------------------------
    _emit_progress("Preflight checks…", emoji="[1/4]")
    preflight_result, preflight_code, blocking = _run_preflight(skip_window)
    if preflight_code == 0:
        _log_step("preflight", "PASS", "All checks passed")
    elif preflight_code == 1:
        _log_step(
            "preflight", "WARN", f"Warnings: {list(preflight_result['checks'].keys())}"
        )
    else:
        _log_step("preflight", "FAIL", f"Blocking: {blocking}")
        result = {
            "command": "apply",
            "overall": "ABORTED",
            "reason": "preflight blocked",
            "blocking_issues": blocking,
            "steps": steps,
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _out(ctx, result)
        raise click.ClickException(
            f"Preflight blocked — aborting apply. Blocking issues: {blocking}"
        )

    if dry_run:
        _log_step("dry-run", "PASS", "Preflight passed — no mutating actions taken")
        result = {
            "command": "apply",
            "overall": "PASS",
            "mode": "dry-run",
            "steps": steps,
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _out(ctx, result)
        return

    # ------------------------------------------------------------------
    # 2. Snapshot
    # ------------------------------------------------------------------
    _emit_progress("Capturing snapshot…", emoji="[2/4]")
    if _redis_cli is None:
        _log_step("snapshot", "FAIL", "redis package not available")
        result = {
            "command": "apply",
            "overall": "FAIL",
            "failed_step": "snapshot",
            "steps": steps,
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _out(ctx, result)
        raise click.ClickException("Snapshot failed: redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        _log_step("snapshot", "FAIL", f"Redis connection failed: {exc}")
        result = {
            "command": "apply",
            "overall": "FAIL",
            "failed_step": "snapshot",
            "steps": steps,
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _out(ctx, result)
        raise click.ClickException(f"Snapshot failed: Redis connection failed: {exc}")

    snap = _create_snapshot(r)
    _log_step("snapshot", "PASS", f"Created {snap['key']}")

    # ------------------------------------------------------------------
    # 3. Restart
    # ------------------------------------------------------------------
    _emit_progress("Graceful restart…", emoji="[3/4]")
    restart_result = _signal_restart(force=False)
    if restart_result.get("status") in ("error", "aborted"):
        _log_step("restart", "FAIL", restart_result.get("detail", "Restart failed"))
        result = {
            "command": "apply",
            "overall": "FAIL",
            "failed_step": "restart",
            "steps": steps,
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _out(ctx, result)
        raise click.ClickException(
            f"Restart failed: {restart_result.get('detail', 'Unknown error')}"
        )
    _log_step("restart", "PASS", restart_result.get("detail", "Restarted successfully"))

    # ------------------------------------------------------------------
    # 4. Verify
    # ------------------------------------------------------------------
    _emit_progress("Post-restart verification…", emoji="[4/4]")
    verify_result = _run_verify()
    if verify_result["status"] == "PASS":
        _log_step("verify", "PASS", verify_result["detail"])
    elif verify_result["status"] == "WARN":
        _log_step("verify", "WARN", verify_result["detail"])
    else:
        _log_step("verify", "FAIL", verify_result["detail"])
        result = {
            "command": "apply",
            "overall": "FAIL",
            "failed_step": "verify",
            "steps": steps,
            "started_at": start_time.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _out(ctx, result)
        raise click.ClickException(
            f"Post-restart verification failed: {verify_result['detail']}"
        )

    result = {
        "command": "apply",
        "overall": "PASS",
        "steps": steps,
        "started_at": start_time.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
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
@click.option("--market", default=None, help="Market to scan (US or HK).")
@click.pass_context
def pipeline(ctx: click.Context, market: str | None) -> None:
    """Trigger the pre-market pipeline (gap scan → AI scoring → bundles → report)."""
    result = run_pipeline(market=market)
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
@click.option("--broker", default="FUTU", help="Broker to probe (FUTU or IB).")
@click.option("--instrument", required=True, help="Instrument ID (e.g. TSLA.NASDAQ).")
@click.option(
    "--type", "data_type", default="quotes", help="Data type: quotes or bars."
)
@click.option(
    "--duration", default=60, type=int, help="Collection duration in seconds."
)
@click.option(
    "--bar-type",
    default=None,
    help="Bar type string (e.g. TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL).",
)
@click.pass_context
def probe(
    ctx: click.Context,
    broker: str,
    instrument: str,
    data_type: str,
    duration: int,
    bar_type: str | None,
) -> int:
    """Probe broker data feed independently of the running TradingNode.

    Spins up an isolated Nautilus data client, subscribes to the
    requested instrument, collects for the specified duration, and
    reports PASS or FAIL.
    """
    broker = broker.upper()
    if broker not in ("FUTU", "IB"):
        raise click.ClickException(f"Unsupported broker: {broker}")

    data_type = data_type.lower()
    if data_type not in ("quotes", "bars"):
        raise click.ClickException(f"Unsupported data type: {data_type}")

    svc = QuoteCollectionService(
        broker=broker,
        watchlist=[instrument],
        data_type=data_type,
        bar_type_str=bar_type,
        collection_period_secs=duration,
    )

    try:
        result = asyncio.run(svc.collect())
    except ConnectionError as exc:
        result_data = {
            "command": "probe",
            "broker": broker,
            "instrument": instrument,
            "data_type": data_type,
            "duration": duration,
            "status": "FAIL",
            "detail": f"Connection error: {exc}",
            "received": 0,
            "elapsed_secs": 0,
        }
        if ctx.obj.get("json"):
            _out(ctx, result_data)
        else:
            click.echo(
                f"Probe FAIL — could not connect to {broker} "
                f"for {instrument}: {exc}"
            )
        return 1  # type: ignore[return-value]
    except Exception as exc:
        result_data = {
            "command": "probe",
            "broker": broker,
            "instrument": instrument,
            "data_type": data_type,
            "duration": duration,
            "status": "FAIL",
            "detail": str(exc),
            "received": 0,
            "elapsed_secs": 0,
        }
        if ctx.obj.get("json"):
            _out(ctx, result_data)
        else:
            click.echo(
                f"Probe FAIL — unexpected error probing {broker} "
                f"for {instrument}: {exc}"
            )
        return 1  # type: ignore[return-value]

    received = len(result.quotes) if data_type == "quotes" else len(result.bars)
    status = "PASS" if received > 0 else "FAIL"

    result_data = {
        "command": "probe",
        "broker": broker,
        "instrument": instrument,
        "data_type": data_type,
        "duration": duration,
        "status": status,
        "received": received,
        "elapsed_secs": result.elapsed_secs,
        "partial_failures": result.partial_failures,
    }

    if ctx.obj.get("json"):
        _out(ctx, result_data)
    else:
        detail = f"received {received} {data_type} in {result.elapsed_secs:.1f}s"
        if result.partial_failures:
            detail += f" (partial failures: {result.partial_failures})"
        click.echo(f"Probe {status} — {detail}")

    return 0 if status == "PASS" else 1  # type: ignore[return-value]


@cli.command()
@click.option("--market", default=None, help="Filter by market (US or HK).")
@click.pass_context
def watchlist(ctx: click.Context, market: str | None) -> None:
    """Show the current pre-market watchlist universe."""
    try:
        cfg = load_watchlist_config("config/premarket_watchlist.yaml")
        universe = build_watchlist(cfg)
    except Exception as exc:
        raise click.ClickException(f"Failed to load watchlist: {exc}")

    if market:
        market = market.upper()
        if market not in universe:
            raise click.ClickException(f"Unknown market: {market}")
        universe = {market: universe[market]}

    if ctx.obj.get("json"):
        _out(ctx, {"command": "watchlist", "universe": universe})
    else:
        lines: list[str] = ["Pre-Market Watchlist Universe"]
        lines.append("=" * 40)
        for mkt, symbols in universe.items():
            lines.append(f"\n{mkt}: {len(symbols)} symbols")
            lines.append("-" * 40)
            for sym in symbols:
                lines.append(f"  {sym}")
            if not symbols:
                lines.append("  (empty)")
        click.echo("\n".join(lines))


@cli.command()
@click.option("--market", default="US", help="Market to scan (US or HK).")
@click.option(
    "--pass",
    "pass_number",
    default=1,
    type=int,
    help="Scan pass (1=early, 2=trended, 3+=final).",
)
@click.pass_context
def gapscan(ctx: click.Context, market: str, pass_number: int) -> None:
    """Run the pre-market gap scanner."""
    market = market.upper()
    if market not in ("US", "HK"):
        raise click.ClickException(f"Unknown market: {market}")
    if pass_number not in (1, 2):
        raise click.ClickException("pass must be 1 or 2")

    # Load watchlist
    try:
        wl_cfg = load_watchlist_config("config/premarket_watchlist.yaml")
        universe = build_watchlist(wl_cfg)
    except Exception as exc:
        raise click.ClickException(f"Failed to load watchlist: {exc}")

    symbols = universe.get(market, [])
    if not symbols:
        msg = f"No symbols in watchlist for market={market}"
        if ctx.obj.get("json"):
            _out(
                ctx,
                {
                    "command": "gapscan",
                    "market": market,
                    "pass": pass_number,
                    "error": msg,
                },
            )
        else:
            click.echo(msg)
        return

    # Build scanner infrastructure
    market_config = wl_cfg.get(market)
    min_gap = market_config.min_gap_pct if market_config else 2.0

    scanner_cfg = GapScannerConfig(
        market=market,
        min_gap_pct=min_gap,
        collection_period_secs=30,
    )

    quote_svc = QuoteCollectionService(
        broker="FUTU",
        host=os.getenv("FUTU_OPEND_HOST", "sam-futu-opend"),
        port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
        watchlist=symbols,
        collection_period_secs=scanner_cfg.collection_period_secs,
        connection_timeout_secs=scanner_cfg.connection_timeout_secs,
    )

    prev_loader = CompositePrevCloseLoader(
        [
            PGFillPrevCloseLoader(),
            FutuKLinePrevCloseLoader(),
        ]
    )

    redis = _redis_client()
    scanner = PreMarketGapScanner(
        config=scanner_cfg,
        quote_service=quote_svc,
        prev_close_loader=prev_loader,
        redis_client=redis,
    )

    try:
        result = asyncio.run(scanner.scan(symbols, pass_number=pass_number))
    except Exception as exc:
        raise click.ClickException(f"Gap scan failed: {exc}")

    # Output
    payload = {
        "command": "gapscan",
        "market": market,
        "pass": pass_number,
        "symbols_scanned": len(symbols),
        "candidates_found": len(result),
        "candidates": [
            {
                "instrument_id": c.instrument_id,
                "prev_close": c.prev_close,
                "quote_last": c.quote_last,
                "gap_pct": c.gap_pct,
                "bid": c.bid,
                "ask": c.ask,
                "trend": c.trend,
            }
            for c in result
        ],
    }

    if ctx.obj.get("json"):
        _out(ctx, payload)
    else:
        lines = [
            f"Pre-Market Gap Scan — {market} Pass {pass_number}",
            "=" * 60,
            f"Symbols scanned: {len(symbols)}",
            f"Candidates:     {len(result)}",
            "",
        ]
        if not result:
            lines.append("No gap candidates matched the filters.")
        else:
            lines.append(f"{'Symbol':<20} {'Gap%':>10} {'Last':>12} {'Trend':<14}")
            lines.append("-" * 60)
            for c in result:
                lines.append(
                    f"{c.instrument_id:<20} {c.gap_pct:>10.2f} "
                    f"{c.quote_last:>12.2f} {c.trend:<14}"
                )
        click.echo("\n".join(lines))


@cli.command("readiness-report")
@click.option("--market", default="US", help="Market to scan (US or HK).")
@click.option("--simulate", is_flag=True, help="Use synthetic demo data.")
@click.option("--webhook-url", default=None, help="Override webhook URL.")
@click.option("--no-save", is_flag=True, help="Skip audit JSON save.")
@click.pass_context
def readiness_report(
    ctx: click.Context,
    market: str,
    simulate: bool,
    webhook_url: str | None,
    no_save: bool,
) -> None:
    """Generate the daily pre-market readiness report (pipeline mode).

    In normal mode this runs the full pipeline (gap scan → AI scoring →
    sizing → risk checks → heat monitor → bundle generation) and prints a
    summary table.  Use ``--simulate`` for a deterministic demo report.
    """
    market = market.upper()
    if market not in ("US", "HK"):
        raise click.ClickException(f"Unknown market: {market}")

    if simulate:
        pipeline_result = _simulate_pipeline_result()
    else:
        # Load watchlist
        try:
            wl_cfg = load_watchlist_config("config/premarket_watchlist.yaml")
            universe = build_watchlist(wl_cfg)
        except Exception as exc:
            raise click.ClickException(f"Failed to load watchlist: {exc}")

        symbols = universe.get(market, [])
        if not symbols:
            msg = f"No symbols in watchlist for market={market}"
            if ctx.obj.get("json"):
                _out(
                    ctx, {"command": "readiness-report", "market": market, "error": msg}
                )
            else:
                click.echo(msg)
            return

        # Gap scan (pass 1)
        market_config = wl_cfg.get(market)
        min_gap = market_config.min_gap_pct if market_config else 2.0

        scanner_cfg = GapScannerConfig(
            market=market,
            min_gap_pct=min_gap,
            collection_period_secs=30,
        )
        quote_svc = QuoteCollectionService(
            broker="FUTU",
            host=os.getenv("FUTU_OPEND_HOST", "sam-futu-opend"),
            port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
            watchlist=symbols,
            collection_period_secs=scanner_cfg.collection_period_secs,
            connection_timeout_secs=scanner_cfg.connection_timeout_secs,
        )
        prev_loader = CompositePrevCloseLoader(
            [PGFillPrevCloseLoader(), FutuKLinePrevCloseLoader()]
        )
        redis = _redis_client()
        scanner = PreMarketGapScanner(
            config=scanner_cfg,
            quote_service=quote_svc,
            prev_close_loader=prev_loader,
            redis_client=redis,
        )

        try:
            candidates = asyncio.run(scanner.scan(symbols, pass_number=1))
        except Exception as exc:
            raise click.ClickException(f"Gap scan failed: {exc}")

        # Pipeline executor
        executor = PipelineExecutor(config=PipelineExecutorConfig(regime_venue=market))
        pipeline_result = executor.run(
            candidates=candidates,
            trace_id=f"readiness-{market}-{datetime.now(timezone.utc).isoformat()}",
        )

        # Bundle generation → Redis pub/sub
        if pipeline_result.approved:
            bundles = generate_bundles(pipeline_result.approved)
            publish_bundles_to_redis(bundles, market=market)

    # Generate readiness report
    redis_client = None
    if not simulate and _redis_cli is not None:
        try:
            redis_client = _redis_cli.Redis(
                host=REDIS_HOST,
                port=int(REDIS_PORT),
                password=REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
        except Exception:
            redis_client = None

    gen = ReadinessReportGenerator(
        webhook_url=webhook_url,
        redis_client=redis_client,
    )
    report = gen.generate(
        pipeline_result,
        bundle_path=None,
        market=market,
    )

    if not no_save:
        try:
            gen.save_audit(report)
        except Exception as exc:
            logger.warning("Failed to save readiness audit: %s", exc)

    if webhook_url or gen.webhook_url:
        try:
            gen.send_webhook(report)
        except Exception as exc:
            logger.warning("Webhook delivery failed: %s", exc)

    if ctx.obj.get("json"):
        _out(
            ctx,
            {
                "command": "readiness-report",
                "market": report.market,
                "candidate_count": report.candidate_count,
                "approved_count": report.approved_count,
                "rejected_count": report.rejected_count,
                "bundles_generated": report.bundles_generated,
                "bundle_path": report.bundle_path,
                "regime": report.regime_state.get("regime"),
                "scan_timestamp": report.scan_timestamp,
                "trace_id": report.trace_id,
            },
        )
    else:
        click.echo(gen.format_table(report))


@cli.command()
@click.option("--market", required=True, help="Market to check (US or HK).")
@click.pass_context
def readiness(ctx: click.Context, market: str) -> None:
    """Read SOD readiness report from Redis (published by ReadinessCheckerActor).

    Displays a pass/fail table with per-check status.  Exit code 0 if all
    checks pass, 1 if any check fails.
    """
    market = market.upper()
    if market not in ("US", "HK"):
        raise click.ClickException(f"Unknown market: {market}")

    if _redis_cli is None:
        raise click.ClickException("redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        raise click.ClickException(f"Redis connection failed: {exc}")

    # Determine today's date in the market's timezone
    try:
        mcfg = MarketConfig.get_market(market)
        tz = ZoneInfo(mcfg.session_timezone)
    except Exception:
        tz = (
            ZoneInfo("America/New_York")
            if market == "US"
            else ZoneInfo("Asia/Hong_Kong")
        )

    today = datetime.now(tz).date().isoformat()
    key = f"sam:readiness:{market}:{today}"

    raw = r.get(key)
    if not raw:
        msg = f"Readiness check not yet run for {market} on {today}"
        if ctx.obj.get("json"):
            _out(
                ctx,
                {
                    "command": "readiness",
                    "market": market,
                    "date": today,
                    "status": "NOT_FOUND",
                    "message": msg,
                },
            )
        else:
            click.echo(msg)
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Corrupt readiness data in Redis: {exc}")

    checks = data.get("checks", [])
    overall = data.get("overall", "UNKNOWN")

    has_fail = any(c.get("result") == "FAIL" for c in checks)
    exit_code = 1 if has_fail else 0

    if ctx.obj.get("json"):
        _out(
            ctx,
            {
                "command": "readiness",
                "market": market,
                "date": today,
                "overall": overall,
                "checks": checks,
                "exit_code": exit_code,
            },
        )
    else:
        lines = [
            f"SOD Readiness [{market}] {today}",
            "=" * 50,
        ]
        for check in checks:
            name = check.get("name", "unknown")
            result = check.get("result", "UNKNOWN")
            detail = check.get("detail", "")
            if result == "PASS":
                marker = "✓"
            elif result == "FAIL":
                marker = "✗"
            else:
                marker = "→"
            lines.append(f"  {marker} {name:<25} [{result}] {detail}")
        lines.append("")
        lines.append(f"Overall: {overall}")
        click.echo("\n".join(lines))

    return exit_code  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# EOD Report command
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--market", required=True, help="Market to report on (US or HK).")
@click.option(
    "--date",
    default=None,
    help="Report date (YYYY-MM-DD). Defaults to today in market timezone.",
)
@click.pass_context
def report(ctx: click.Context, market: str, date: str | None) -> None:
    """Display EOD report from Redis or PG daily_reports.

    Reads the aggregated end-of-day report generated by EndOfDayReporterActor.
    For today's report, tries Redis first (fast). For historical dates or if
    Redis TTL has expired, falls back to the PostgreSQL daily_reports table.
    """
    market = market.upper()
    if market not in ("US", "HK"):
        raise click.ClickException(f"Unknown market: {market}")

    # Determine report date.
    if date:
        try:
            report_date = datetime.strptime(date, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise click.ClickException(
                f"Invalid date format: {date}. Use YYYY-MM-DD."
            ) from exc
    else:
        try:
            mcfg = MarketConfig.get_market(market)
            tz = ZoneInfo(mcfg.session_timezone)
        except Exception:
            tz = (
                ZoneInfo("America/New_York")
                if market == "US"
                else ZoneInfo("Asia/Hong_Kong")
            )
        report_date = datetime.now(tz).date().isoformat()

    # Fetch report.
    try:
        result = asyncio.run(_report_query(market, report_date, ctx.obj.get("json")))
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"Report query failed: {exc}")

    if ctx.obj.get("json"):
        _out(ctx, result)
    else:
        click.echo(_format_report_table(result))

    return 0 if result.get("status") != "NOT_FOUND" else 1  # type: ignore[return-value]


async def _report_query(
    market: str, report_date: str, output_json: bool
) -> dict[str, Any]:
    """Fetch EOD report from Redis or PG daily_reports."""
    report_data: dict[str, Any] | None = None
    source: str | None = None

    # Try Redis first.
    if _redis_cli is not None:
        try:
            r = _redis_cli.Redis(
                host=REDIS_HOST,
                port=int(REDIS_PORT),
                password=REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            key = f"sam:eod_report:{market}:{report_date}"
            raw = r.get(key)
            if raw:
                report_data = json.loads(raw)
                source = "redis"
        except Exception:
            pass  # Fall through to PG.

    # Fallback to PostgreSQL daily_reports.
    if report_data is None:
        try:
            dsn = (
                f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
                f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
            )
            conn = await asyncpg.connect(dsn)
            try:
                row = await conn.fetchrow(
                    """
                    SELECT report_json
                    FROM daily_reports
                    WHERE market = $1 AND date = $2
                    """,
                    market,
                    report_date,
                )
                if row and row["report_json"]:
                    report_data = (
                        json.loads(row["report_json"])
                        if isinstance(row["report_json"], str)
                        else dict(row["report_json"])
                    )
                    source = "postgres"
            finally:
                await conn.close()
        except Exception:
            pass

    if report_data is None:
        return {
            "command": "report",
            "market": market,
            "date": report_date,
            "status": "NOT_FOUND",
            "message": f"No EOD report for {report_date}",
        }

    return {
        "command": "report",
        "market": market,
        "date": report_date,
        "source": source,
        "status": "OK",
        "report": report_data,
    }


def _format_report_table(data: dict[str, Any]) -> str:
    """Format EOD report as human-readable aligned text."""
    if data.get("status") == "NOT_FOUND":
        return str(data.get("message", "Report not found"))

    report = data.get("report", {})
    lines: list[str] = []

    lines.append(f"EOD Report [{data['market']}] {data['date']}")
    lines.append("=" * 56)

    # P&L section
    lines.append("\nP&L Summary")
    lines.append("-" * 56)
    pnl_entries = report.get("daily_pnl", [])
    total_pnl = 0.0
    total_comm = 0.0

    if pnl_entries:
        lines.append(f"{'Strategy':<30} {'P&L':>12} {'Source':>10}")
        lines.append("-" * 56)
        for entry in pnl_entries:
            sid = entry.get("strategy_id", "unknown")
            val = entry.get("realized_pnl")
            src = entry.get("source", "")
            if val is not None:
                total_pnl += val
                val_str = f"{val:>12.2f}"
            else:
                val_str = "         N/A"
            lines.append(f"{sid:<30} {val_str} {src:>10}")

        fills_summary = report.get("fills_summary", {})
        total_comm = fills_summary.get("total_commission", 0.0)

        lines.append("-" * 56)
        lines.append(f"{'Total P&L':<30} {total_pnl:>12.2f}")
        lines.append(f"{'Total Commission':<30} {total_comm:>12.2f}")
        lines.append(f"{'Net P&L':<30} {total_pnl - total_comm:>12.2f}")
    else:
        lines.append("No P&L data available.")

    # Fills section
    lines.append("\nFills Summary")
    lines.append("-" * 56)
    fills_summary = report.get("fills_summary", {})
    total_fills = fills_summary.get("total_fills", 0)
    total_volume = fills_summary.get("total_volume", 0.0)
    total_commission = fills_summary.get("total_commission", 0.0)
    by_strategy = fills_summary.get("by_strategy", [])

    lines.append(f"Total Fills:        {total_fills}")
    lines.append(f"Total Volume:       {total_volume:,.2f}")
    lines.append(f"Total Commission:   {total_commission:,.2f}")

    if by_strategy:
        lines.append(f"\n{'Strategy':<30} {'Fills':>8} {'Qty':>10} {'Avg Price':>12}")
        lines.append("-" * 56)
        for s in by_strategy:
            sid = s.get("strategy_id", "unknown")
            fc = s.get("fill_count", 0)
            tq = s.get("total_qty", 0.0)
            tv = s.get("total_volume", 0.0)
            avg_px = tv / tq if tq and tq > 0 else 0.0
            lines.append(f"{sid:<30} {fc:>8} {tq:>10.2f} {avg_px:>12.2f}")

    # Health section
    lines.append("\nHealth Events")
    lines.append("-" * 56)
    health = report.get("health_events", {})
    hb_count = health.get("heartbeat_count", 0)
    last_hb = health.get("last_heartbeat", "N/A")
    hstatus = health.get("status", "unknown")
    lines.append(f"Heartbeat Count:    {hb_count}")
    lines.append(f"Last Heartbeat:     {last_hb}")
    lines.append(f"Status:             {hstatus}")

    alerts = health.get("alerts", [])
    critical_alerts = [
        a for a in alerts if "CRITICAL" in str(a.get("value", "")).upper()
    ]
    if critical_alerts:
        lines.append(f"\nCRITICAL Alerts ({len(critical_alerts)}):")
        for alert in critical_alerts:
            lines.append(f"  - {alert.get('key', '')}: {alert.get('value', '')[:100]}")

    # Position section
    lines.append("\nPosition Check")
    lines.append("-" * 56)
    pos = report.get("position_summary", {})
    all_flat = pos.get("all_flat", False)
    open_count = pos.get("total_open_positions", 0)
    if all_flat:
        lines.append("✓ All positions flat")
    else:
        lines.append(f"✗ {open_count} open position(s)")
        for p in pos.get("positions", []):
            lines.append(
                f"  - {p.get('instrument_id', '?')}: {p.get('net_quantity', 0)}"
            )

    # Rejection events
    lines.append("\nRejection Events")
    lines.append("-" * 56)
    rej = report.get("rejection_events", {})
    lines.append(f"Total Rejections:        {rej.get('total_rejections', 0)}")
    lines.append(f"Active Circuit Breakers: {rej.get('circuit_breakers_active', 0)}")

    # Max drawdown
    mdd = report.get("max_drawdown", {})
    if mdd and mdd.get("status") != "unavailable":
        lines.append("\nMax Drawdown")
        lines.append("-" * 56)
        lines.append(f"Status: {mdd.get('status', 'unknown')}")
        for d in mdd.get("drawdowns", []):
            dd_val = d.get("drawdown", "N/A")
            lines.append(f"  {d.get('strategy_id', '?')}: {dd_val}")

    lines.append(f"\nReport Source: {data.get('source', 'unknown')}")
    lines.append(f"Generated At:  {report.get('generated_at_utc', 'unknown')}")

    return "\n".join(lines)


def _simulate_pipeline_result() -> PipelineResult:
    """Return a synthetic PipelineResult for demo / testing."""
    from sam_trader.services.ai_scoring import (
        AIRecommendation,
        Conviction,
        DimensionScores,
        Grade,
        TradeParameters,
    )
    from sam_trader.services.gap_scanner import GapCandidate
    from sam_trader.services.heat_monitor import HeatMapEntry, HeatMonitorResult
    from sam_trader.services.risk_checks import RiskCheckResult
    from sam_trader.services.risk_sizing import PositionSizeResult

    gaps = [
        GapCandidate(
            instrument_id="TSLA.NASDAQ",
            prev_close=150.0,
            quote_last=155.0,
            gap_pct=3.33,
            bid=154.9,
            ask=155.1,
            volume=1_000_000.0,
            trend="STABLE",
            pass_number=1,
            cross_validated=True,
            cross_validation_note="",
        ),
        GapCandidate(
            instrument_id="AAPL.NASDAQ",
            prev_close=180.0,
            quote_last=185.0,
            gap_pct=2.78,
            bid=184.9,
            ask=185.1,
            volume=2_000_000.0,
            trend="RISING",
            pass_number=1,
            cross_validated=True,
            cross_validation_note="",
        ),
    ]

    recs = [
        AIRecommendation(
            instrument_id="TSLA.NASDAQ",
            grade=Grade.STRONG_BUY,
            conviction=Conviction.STRONG,
            confidence=0.75,
            scores=DimensionScores(
                gap_quality=20,
                technical_setup=15,
                sentiment=12,
                liquidity=10,
                risk=8,
                market_context=10,
            ),
            trade_params=TradeParameters(
                entry=155.0,
                stop=150.0,
                target=165.0,
                position_size_pct=0.02,
            ),
            reasoning="Strong gap with technical support",
            key_factors=["gap", "support"],
            risk_factors=[],
            llm_used="RuleBased",
            trace_id="sim",
            timestamp="2026-05-24T08:00:00+00:00",
        ),
        AIRecommendation(
            instrument_id="AAPL.NASDAQ",
            grade=Grade.BUY,
            conviction=Conviction.MODERATE,
            confidence=0.55,
            scores=DimensionScores(
                gap_quality=15,
                technical_setup=10,
                sentiment=8,
                liquidity=10,
                risk=7,
                market_context=7,
            ),
            trade_params=TradeParameters(
                entry=185.0,
                stop=180.0,
                target=195.0,
                position_size_pct=0.015,
            ),
            reasoning="Moderate gap momentum",
            key_factors=["gap"],
            risk_factors=[],
            llm_used="RuleBased",
            trace_id="sim",
            timestamp="2026-05-24T08:00:00+00:00",
        ),
    ]

    sizes = [
        PositionSizeResult(position_size=100, max_risk_dollars=500.0, var_95=300.0),
        PositionSizeResult(position_size=75, max_risk_dollars=375.0, var_95=225.0),
    ]

    risks = [
        RiskCheckResult(
            passed=True,
            rejected_reasons=[],
            post_trade_exposure=15_500.0,
            estimated_risk_dollars=500.0,
            required_margin=0.0,
        ),
        RiskCheckResult(
            passed=True,
            rejected_reasons=[],
            post_trade_exposure=13_875.0,
            estimated_risk_dollars=375.0,
            required_margin=0.0,
        ),
    ]

    approved = [
        PipelineCandidate(
            gap=gaps[0],
            recommendation=recs[0],
            position_size=sizes[0],
            risk_check=risks[0],
            approved=True,
        ),
        PipelineCandidate(
            gap=gaps[1],
            recommendation=recs[1],
            position_size=sizes[1],
            risk_check=risks[1],
            approved=True,
        ),
    ]

    heat = HeatMonitorResult(
        total_heat_pct=0.03,
        total_notional=29_375.0,
        heat_map={
            "TSLA.NASDAQ": HeatMapEntry(
                instrument_id="TSLA.NASDAQ",
                risk_contribution=0.01,
                notional=15_500.0,
                concentration_pct=0.0155,
                warning="",
            ),
            "AAPL.NASDAQ": HeatMapEntry(
                instrument_id="AAPL.NASDAQ",
                risk_contribution=0.0075,
                notional=13_875.0,
                concentration_pct=0.0139,
                warning="",
            ),
        },
        sector_map={"tech": 29_375.0},
        warnings=[],
        passed=True,
    )

    regime = RegimePrediction(
        regime=Regime.TRENDING,
        confidence=0.72,
        is_stable=True,
        model_version="sim-1.0",
    )

    audit = [
        PipelineStageRecord(
            stage="ai_scoring",
            timestamp="2026-05-24T08:00:00+00:00",
            input_count=2,
            output_count=2,
            errors=[],
            notes="",
        ),
        PipelineStageRecord(
            stage="merge",
            timestamp="2026-05-24T08:01:00+00:00",
            input_count=2,
            output_count=2,
            errors=[],
            notes="regime=trending",
        ),
    ]

    return PipelineResult(
        approved=approved,
        rejected=[],
        heat_result=heat,
        regime_prediction=regime,
        audit_trail=audit,
        trace_id="sim-readiness",
    )


@cli.command()
@click.option("--strategy", default=None, help="Filter by strategy ID.")
@click.option("--days", default=30, type=int, help="Lookback days (default 30).")
@click.pass_context
def performance(ctx: click.Context, strategy: str | None, days: int) -> None:
    """Display performance stats from Nautilus PortfolioAnalyzer results."""
    try:
        result = asyncio.run(_performance_query(strategy, days, ctx.obj.get("json")))
        if ctx.obj.get("json"):
            _out(ctx, result)
        else:
            click.echo(_format_performance_table(result))
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"Performance query failed: {exc}")


def _format_performance_table(data: dict[str, Any]) -> str:
    """Format performance stats as human-readable aligned columns."""
    lines: list[str] = []
    days = data.get("days", 30)
    lines.append(f"Performance Summary (last {days} days)")
    lines.append("=" * 56)

    stats = data.get("stats", {})
    if not stats:
        note: str = data.get(
            "note",
            "No performance data available. Run nightly analysis first.",
        )
        return note

    for strategy_id, strategy_stats in stats.items():
        lines.append(f"\nStrategy: {strategy_id}")
        lines.append("-" * 56)
        lines.append(f"{'Metric':<30} {'Value':>12}")
        lines.append("-" * 56)
        for name, value in sorted(strategy_stats.items()):
            if value is None:
                val_str = "N/A"
            elif isinstance(value, float):
                val_str = f"{value:>12.4f}"
            else:
                val_str = str(value)
            lines.append(f"{name:<30} {val_str}")

    return "\n".join(lines)


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
                "note": "No performance data available. Run nightly analysis first.",
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


def _diff_bundles(
    current: dict[str, dict[str, Any]],
    snapshot: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare current bundles against snapshot bundles.

    Returns a dict with keys:
    - ``added``: list of bundle IDs present in current but not snapshot
    - ``removed``: list of bundle IDs present in snapshot but not current
    - ``modified``: list of dicts with ``id``, ``changed_keys``, ``old``, ``new``
    - ``version_bumps``: list of dicts with ``id``, ``old_version``, ``new_version``
    """
    current_ids = set(current.keys())
    snapshot_ids = set(snapshot.keys())

    added = sorted(current_ids - snapshot_ids)
    removed = sorted(snapshot_ids - current_ids)

    modified: list[dict[str, Any]] = []
    version_bumps: list[dict[str, Any]] = []

    for bid in sorted(current_ids & snapshot_ids):
        cur = current[bid]
        snap = snapshot[bid]

        # Simple top-level key diff (ignoring ordering differences in lists)
        changed_keys: list[str] = []
        all_keys = set(cur.keys()) | set(snap.keys())
        for key in sorted(all_keys):
            if cur.get(key) != snap.get(key):
                changed_keys.append(key)

        if changed_keys:
            modified.append(
                {
                    "id": bid,
                    "changed_keys": changed_keys,
                    "old": {k: snap.get(k) for k in changed_keys},
                    "new": {k: cur.get(k) for k in changed_keys},
                }
            )

        # Version bump detection (Phase 7 metadata)
        cur_ver = str(cur.get("version", "")) if cur.get("version") is not None else ""
        snap_ver = (
            str(snap.get("version", "")) if snap.get("version") is not None else ""
        )
        if cur_ver and snap_ver and cur_ver != snap_ver:
            version_bumps.append(
                {
                    "id": bid,
                    "old_version": snap_ver,
                    "new_version": cur_ver,
                }
            )

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "version_bumps": version_bumps,
    }


def _format_bundle_diff(diff: dict[str, Any]) -> str:
    """Render bundle diff as human-readable text."""
    lines: list[str] = ["Bundle Diff", "=" * 40]

    if diff.get("added"):
        lines.append("\n  ADDED")
        lines.append("  " + "-" * 36)
        for bid in diff["added"]:
            lines.append(f"    + {bid}")

    if diff.get("removed"):
        lines.append("\n  REMOVED")
        lines.append("  " + "-" * 36)
        for bid in diff["removed"]:
            lines.append(f"    - {bid}")

    if diff.get("modified"):
        lines.append("\n  MODIFIED")
        lines.append("  " + "-" * 36)
        for mod in diff["modified"]:
            bid = mod["id"]
            keys = ", ".join(mod["changed_keys"])
            lines.append(f"    ~ {bid}  ({keys})")

    if diff.get("version_bumps"):
        lines.append("\n  VERSION BUMPS")
        lines.append("  " + "-" * 36)
        for vb in diff["version_bumps"]:
            lines.append(f"    ~ {vb['id']}  {vb['old_version']} → {vb['new_version']}")

    if not any(diff[k] for k in ("added", "removed", "modified", "version_bumps")):
        lines.append("\n  No pending bundle changes.")

    return "\n".join(lines)


@cli.command("bundle-diff")
@click.pass_context
def bundle_diff(ctx: click.Context) -> None:
    """Show pending bundle changes compared to last deployed snapshot."""
    if _redis_cli is None:
        raise click.ClickException("redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        raise click.ClickException(f"Redis connection failed: {exc}")

    bundles_path = DEFAULT_BUNDLES_PATH
    current = _get_bundle_snapshot_data(bundles_path)

    # Find latest snapshot
    keys = sorted(r.keys("sam:snapshot:*"), reverse=True)
    if not keys:
        # First-run case: no snapshot exists
        result: dict[str, Any] = {
            "command": "bundle-diff",
            "status": "new",
            "note": "No snapshot found — all bundles are new (first-run)",
            "added": sorted(current.keys()),
            "removed": [],
            "modified": [],
            "version_bumps": [],
        }
        if ctx.obj.get("json"):
            _out(ctx, result)
        else:
            lines = ["Bundle Diff", "=" * 40]
            lines.append("\n  NEW (no snapshot baseline)")
            lines.append("  " + "-" * 36)
            for bid in result["added"]:
                lines.append(f"    + {bid}")
            click.echo("\n".join(lines))
        return

    latest_key = keys[0]
    raw = r.get(latest_key)
    if not raw:
        raise click.ClickException(f"Snapshot {latest_key} data missing")

    try:
        snap_data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Corrupt snapshot data: {exc}")

    snapshot_bundles = snap_data.get("bundles", {})
    diff = _diff_bundles(current, snapshot_bundles)

    result = {
        "command": "bundle-diff",
        "status": "diff",
        "snapshot_key": latest_key,
        "added": diff["added"],
        "removed": diff["removed"],
        "modified": diff["modified"],
        "version_bumps": diff["version_bumps"],
    }

    if ctx.obj.get("json"):
        _out(ctx, result)
    else:
        click.echo(_format_bundle_diff(diff))


def _signal_restart(force: bool = False) -> dict[str, Any]:
    """Signal sam-trader to gracefully restart via Redis, then docker restart.

    Parameters
    ----------
    force : bool
        If *True*, skip the state-save handshake and restart immediately.

    Returns
    -------
    dict
        Result dict with ``status`` and ``detail`` keys.

    """
    result: dict[str, Any] = {
        "command": "restart",
        "status": "unknown",
        "detail": "",
    }

    if _redis_cli is None:
        logger.critical(
            "redis package not available — cannot perform graceful restart handshake"
        )
        result["status"] = "error"
        result["detail"] = "redis package not available"
        return result

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        logger.critical("Cannot connect to Redis for restart handshake: %s", exc)
        result["status"] = "error"
        result["detail"] = f"Redis connection failed: {exc}"
        return result

    if not force:
        # Subscribe BEFORE publishing so we don't miss the confirmation
        pubsub = r.pubsub()
        try:
            pubsub.subscribe("sam:state_saved")
        except Exception as exc:
            logger.critical("Failed to subscribe to sam:state_saved: %s", exc)
            result["status"] = "error"
            result["detail"] = f"Redis subscribe failed: {exc}"
            return result

        # Publish restart request
        try:
            r.publish("sam:restart_request", "graceful")
        except Exception as exc:
            logger.critical("Failed to publish restart request: %s", exc)
            pubsub.unsubscribe()
            pubsub.close()
            result["status"] = "error"
            result["detail"] = f"Redis publish failed: {exc}"
            return result

        # Wait for confirmation with timeout
        start = time.time()
        confirmed = False
        while time.time() - start < STATE_SAVE_HANDSHAKE_TIMEOUT:
            message = pubsub.get_message(timeout=1.0)
            if message and message.get("type") == "message":
                data = message.get("data", "")
                try:
                    payload = json.loads(data)
                    if payload.get("status") == "saved":
                        confirmed = True
                        break
                except json.JSONDecodeError:
                    if data == "saved":
                        confirmed = True
                        break
            time.sleep(0.1)

        try:
            pubsub.unsubscribe()
            pubsub.close()
        except Exception:
            pass

        if not confirmed:
            logger.critical(
                "State-save handshake timed out after %ds. "
                "Aborting restart to prevent data loss.",
                STATE_SAVE_HANDSHAKE_TIMEOUT,
            )
            result["status"] = "aborted"
            result["detail"] = (
                f"State-save timeout ({STATE_SAVE_HANDSHAKE_TIMEOUT}s) — "
                "restart aborted to prevent unsaved state loss"
            )
            return result
    else:
        # Force mode: skip wait, just publish
        try:
            r.publish("sam:restart_request", "graceful")
        except Exception as exc:
            logger.critical("Failed to publish restart request: %s", exc)
            result["status"] = "error"
            result["detail"] = f"Redis publish failed: {exc}"
            return result
        result["detail"] = "Force restart — skipped state-save wait"

    # Docker compose restart
    docker_result = subprocess.run(
        [
            DOCKER_BINARY,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "restart",
            SAM_TRADER_CONTAINER,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if docker_result.returncode != 0:
        logger.critical("Docker restart failed: %s", docker_result.stderr)
        result["status"] = "error"
        result["detail"] = f"Docker restart failed: {docker_result.stderr}"
        return result

    # Wait for health check
    health_ok = False
    health_start = time.time()
    while time.time() - health_start < RESTART_HEALTH_TIMEOUT:
        health_r = subprocess.run(
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
        if health_r.stdout.strip() == "healthy":
            health_ok = True
            break
        time.sleep(2)

    if not health_ok:
        logger.critical(
            "sam-trader health check did not pass within %ds after restart.",
            RESTART_HEALTH_TIMEOUT,
        )
        result["status"] = "error"
        result["detail"] = (
            f"Health check timeout ({RESTART_HEALTH_TIMEOUT}s) after restart"
        )
        return result

    # Verify sam:state_loaded published by restarted node
    state_loaded = False
    try:
        if r.exists("sam:state_loaded"):
            state_loaded = True
        else:
            # Brief pub/sub listen in case node just published
            pubsub2 = r.pubsub()
            pubsub2.subscribe("sam:state_loaded")
            # Consume subscription confirmation message
            pubsub2.get_message(timeout=1)
            # Wait for actual message
            msg = pubsub2.get_message(timeout=3)
            if msg and msg.get("type") == "message":
                state_loaded = True
            pubsub2.unsubscribe()
            pubsub2.close()
    except Exception as exc:
        logger.warning("Could not verify sam:state_loaded: %s", exc)

    if state_loaded:
        result["status"] = "success"
        if force:
            result["detail"] = (
                "Force restart completed: container restarted, health OK, state loaded"
            )
        else:
            result["detail"] = (
                "Graceful restart completed: state saved, container restarted, "
                "health OK, state loaded"
            )
    else:
        result["status"] = "warning"
        if force:
            result["detail"] = (
                "Force restart completed but sam:state_loaded not confirmed"
            )
        else:
            result["detail"] = "Restart completed but sam:state_loaded not confirmed"

    return result


@cli.command()
@click.pass_context
def kill(ctx: click.Context) -> None:
    """Emergency kill switch — cancel all orders and halt trading."""
    result = cmd_kill()
    _out(ctx, result)


@cli.command()
@click.pass_context
def halt(ctx: click.Context) -> None:
    """Halt trading — cancel all orders, position-close-only mode."""
    result = cmd_halt()
    _out(ctx, result)


@cli.command()
@click.pass_context
def resume(ctx: click.Context) -> None:
    """Resume trading — clear halt state."""
    result = cmd_resume()
    _out(ctx, result)


@cli.command("safety-monitor")
@click.pass_context
def safety_monitor(ctx: click.Context) -> None:
    """Run circuit-breaker checks once (daily PnL, rejection streak, connectivity)."""
    result = run_circuit_breaker_monitor()
    _out(ctx, result)


@cli.command("switch-market")
@click.argument("market")
@click.option(
    "--timeout",
    default=120,
    type=int,
    help="Seconds to wait for orchestrator completion (default 120).",
)
@click.pass_context
def switch_market(ctx: click.Context, market: str, timeout: int) -> None:
    """Request a market switch and wait for orchestrator completion.

    Publishes ``sam:market_switch_request`` to Redis.  The
    RestartOrchestrator inside sam-services performs the graceful
    save → env update → container recreate → state_loaded poll flow.

    Usage:
        sam switch-market US
        sam switch-market HK
    """
    market = market.upper()
    if market not in ("US", "HK"):
        raise click.ClickException("Market must be US or HK")

    if _redis_cli is None:
        raise click.ClickException("redis package not available")

    try:
        r = _redis_cli.Redis(
            host=REDIS_HOST,
            port=int(REDIS_PORT),
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        r.ping()
    except Exception as exc:
        raise click.ClickException(f"Redis connection failed: {exc}")

    # Publish request
    payload = {"market": market, "requested_at": datetime.now(timezone.utc).isoformat()}
    try:
        r.publish("sam:market_switch_request", json.dumps(payload))
    except Exception as exc:
        raise click.ClickException(f"Failed to publish request: {exc}")

    # Wait for completion or failure notification
    pubsub = r.pubsub()
    pubsub.subscribe("sam:market_switch_complete", "sam:market_switch_failed")
    # Consume subscription confirmations
    pubsub.get_message(timeout=1)
    pubsub.get_message(timeout=1)

    start = time.time()
    result: dict[str, Any] = {
        "command": "switch-market",
        "market": market,
        "status": "unknown",
        "detail": "",
    }
    while time.time() - start < timeout:
        msg = pubsub.get_message(timeout=1.0)
        if msg and msg.get("type") == "message":
            channel = msg.get("channel", "")
            data = msg.get("data", "")
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {}
            if channel == "sam:market_switch_complete":
                result["status"] = "completed"
                result["detail"] = f"Switched to {payload.get('market', market)}"
                break
            elif channel == "sam:market_switch_failed":
                result["status"] = "failed"
                result["detail"] = payload.get("reason", "unknown failure")
                break
        time.sleep(0.1)

    pubsub.unsubscribe()
    pubsub.close()

    if result["status"] == "unknown":
        result["status"] = "timeout"
        result["detail"] = f"No response from orchestrator within {timeout}s"

    _out(ctx, result)
    if result["status"] != "completed":
        raise click.ClickException(result["detail"])


# ---------------------------------------------------------------------------
# Backtest commands
# ---------------------------------------------------------------------------


def _run_walk_forward(
    ctx: click.Context,
    strategies: list,
    instrument_ids: list[str],
    bar_types: list[str],
    start_date: str,
    end_date: str,
    catalog_path: str,
    sweep_flags: list[str],
    train_days: str,
    test_days: str,
) -> None:
    """Execute a walk-forward optimization and output results."""
    from sam_trader.services.backtest.engine import BacktestEngineWrapper
    from sam_trader.services.backtest.sweep import parse_sweep_flags
    from sam_trader.services.backtest.walk_forward import (
        WalkForward,
        parse_days_flag,
    )

    # Parse sweep grid (required for walk-forward)
    try:
        param_grid = parse_sweep_flags(sweep_flags)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if not param_grid:
        raise click.ClickException(
            "Walk-forward requires --sweep flags"
            " (e.g., --sweep stop_loss_ticks=5,10,15)"
        )

    # Parse train/test days
    try:
        train = parse_days_flag(train_days)
        test = parse_days_flag(test_days)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    # Run walk-forward
    wrapper = BacktestEngineWrapper(catalog_path=catalog_path)
    wf = WalkForward(
        wrapper=wrapper,
        base_strategies=list(strategies),
        instrument_ids=instrument_ids,
        bar_types=bar_types,
        train_days=train,
        test_days=test,
        data_start=start_date,
        data_end=end_date,
    )

    try:
        result = wf.run(param_grid=param_grid)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Walk-forward failed: {exc}")

    if ctx.obj.get("json"):
        json_result: dict[str, Any] = {
            "command": "backtest-walk-forward",
            "config": result.config,
            "overall_sharpe": result.overall_sharpe,
            "overall_pnl": result.overall_pnl,
            "profitable_windows": result.profitable_windows,
            "total_windows": result.total_windows,
            "param_stability": {k: dict(v) for k, v in result.param_stability.items()},
            "windows": [
                {
                    "train_start": w.train_start,
                    "train_end": w.train_end,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "best_params": w.best_params,
                    "train_sharpe": w.train_sharpe,
                    "test_sharpe": w.test_sharpe,
                    "test_pnl": w.test_pnl,
                    "test_win_rate": w.test_win_rate,
                    "test_max_dd": w.test_max_dd,
                    "test_trades": w.test_trades,
                    "error": w.error,
                }
                for w in result.windows
            ],
        }
        _out(ctx, json_result)
    else:
        click.echo(WalkForward.format_report(result))


def _run_sweep(
    ctx: click.Context,
    strategies: list,
    instrument_ids: list[str],
    bar_types: list[str],
    start_date: str,
    end_date: str,
    catalog_path: str,
    sweep_flags: list[str],
) -> None:
    """Execute a parameter sweep and output results.

    Parameters
    ----------
    ctx : click.Context
        CLI context (for --json flag).
    strategies : list[ImportableStrategyConfig]
        Base strategy configurations.
    instrument_ids : list[str]
        Instrument IDs for data loading.
    bar_types : list[str]
        Bar type strings for data loading.
    start_date : str
        Backtest start date.
    end_date : str
        Backtest end date.
    catalog_path : str
        Path to Parquet data catalog.
    sweep_flags : list[str]
        Raw --sweep key=val1,val2 entries.

    """
    from sam_trader.services.backtest.engine import BacktestEngineWrapper
    from sam_trader.services.backtest.sweep import ParameterSweep, parse_sweep_flags

    # Parse sweep grid
    try:
        param_grid = parse_sweep_flags(sweep_flags)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if not param_grid:
        raise click.ClickException(
            "No valid sweep parameters. Use: --sweep key=val1,val2,val3"
        )

    # Run sweep
    wrapper = BacktestEngineWrapper(catalog_path=catalog_path)
    sweeper = ParameterSweep(
        wrapper=wrapper,
        base_strategies=list(strategies),
        instrument_ids=instrument_ids,
        bar_types=bar_types,
        start=start_date,
        end=end_date,
    )

    try:
        results = sweeper.run(param_grid=param_grid)
    except Exception as exc:
        raise click.ClickException(f"Sweep failed: {exc}")

    # Group by strategy class if multi-strategy sweep
    if ctx.obj.get("json"):
        result_payload: dict[str, Any] = {
            "command": "backtest-sweep",
            "start": start_date,
            "end": end_date,
            "param_grid": {k: [str(v) for v in vals] for k, vals in param_grid.items()},
            "results": results,
        }
        _out(ctx, result_payload)
    else:
        click.echo(sweeper.format_table(results))


def _safe_round(value: Any, ndigits: int = 2) -> float | None:
    """Round a numeric value, returning None for non-numeric input."""
    if isinstance(value, (int, float)):
        return round(value, ndigits)
    return None


def _dict_get(d: Any, key: str) -> Any:
    """Safely get a value from a dict, returning None for non-dict input."""
    if isinstance(d, dict):
        return d.get(key)
    return None


def _format_backtest_table(result: dict[str, Any]) -> str:
    """Format backtest results as a human-readable table."""

    def _fmt_float(value: Any, width: int, suffix: str = "") -> str:
        """Format a float value, showing N/A for None."""
        if value is None:
            return f"{'N/A':>{width}}"
        return f"{float(value):>{width}.{width - 3 - len(suffix)}f}{suffix}"

    def _fmt_int(value: Any, width: int) -> str:
        """Format an int value, showing N/A for None."""
        if value is None:
            return f"{'N/A':>{width}}"
        return f"{int(value):>{width}}"

    def _fmt_pct(value: Any, width: int) -> str:
        """Format a percentage value, showing N/A for None."""
        if value is None:
            return f"{'N/A':>{width}}"
        return f"{float(value):>{width - 1}.1%}"

    lines: list[str] = []
    lines.append("Backtest Results")
    lines.append("=" * 72)

    bundles = result.get("bundles", [])
    if not bundles:
        note = result.get("note", "No results — backtest produced no output.")
        lines.append(note)
        return "\n".join(lines)

    lines.append(
        f"{'Bundle':<24} {'Net P&L':>10} {'Sharpe':>8} "
        f"{'Max DD':>8} {'Win Rate':>9} {'Trades':>7} {'Elapsed':>8}"
    )
    lines.append("-" * 72)

    for b in bundles:
        biz = b.get("bundle_id", b.get("strategy_id", "?"))
        lines.append(
            f"{str(biz)[:24]:<24} "
            f"{_fmt_float(b.get('net_pnl'), 10)} "
            f"{_fmt_float(b.get('sharpe'), 8)} "
            f"{_fmt_pct(b.get('max_drawdown'), 8)} "
            f"{_fmt_pct(b.get('win_rate'), 9)} "
            f"{_fmt_int(b.get('total_trades'), 7)} "
            f"{_fmt_float(b.get('elapsed'), 7, 's')}"
        )

    return "\n".join(lines)


def _build_backtest_summary(
    result_obj: Any,
    bundle_id: str,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """Extract summary metrics from a single BacktestResult.

    Parameters
    ----------
    result_obj : BacktestResult
        A Nautilus BacktestResult from a single strategy run.
    bundle_id : str
        The bundle identifier for display.
    strategy_id : str | None
        The Nautilus strategy_id from config (falls back to bundle_id).

    Returns
    -------
    dict
        Summary dict with keys: bundle_id, net_pnl, sharpe, max_drawdown,
        win_rate, total_trades, elapsed.

    """
    display_id = strategy_id or bundle_id

    # Extract P&L — use the first strategy's total_pnl from stats_pnls
    net_pnl: float | None = None
    stats_pnls = getattr(result_obj, "stats_pnls", {}) or {}
    for _key, pnl_data in stats_pnls.items():
        if isinstance(pnl_data, dict):
            net_pnl = pnl_data.get("total_pnl")
            if net_pnl is not None:
                break

    stats_returns = getattr(result_obj, "stats_returns", {}) or {}

    sharpe = stats_returns.get("sharpe_ratio")
    max_dd = stats_returns.get("max_drawdown")
    win_rate = stats_returns.get("win_rate")
    total_trades = getattr(result_obj, "total_orders", None)
    elapsed = getattr(result_obj, "elapsed_time", None)

    return {
        "bundle_id": display_id,
        "net_pnl": round(net_pnl, 2) if isinstance(net_pnl, (int, float)) else net_pnl,
        "sharpe": round(sharpe, 4) if isinstance(sharpe, (int, float)) else sharpe,
        "max_drawdown": (
            round(max_dd, 4) if isinstance(max_dd, (int, float)) else max_dd
        ),
        "win_rate": (
            round(win_rate, 4) if isinstance(win_rate, (int, float)) else win_rate
        ),
        "total_trades": int(total_trades) if total_trades is not None else None,
        "elapsed": round(elapsed, 2) if isinstance(elapsed, (int, float)) else elapsed,
    }


def _infer_bar_type_from_catalog(catalog_path: str, instrument_id: str) -> str | None:
    """Infer a bar type for an instrument from the Parquet catalog.

    Scans the catalog for available bar data matching the instrument.
    Prefers ``5-MINUTE`` bar types if present; otherwise returns the
    first available bar type.  Returns ``None`` if no data is found.

    Parameters
    ----------
    catalog_path : str
        Path to the Nautilus ParquetDataCatalog directory.
    instrument_id : str
        Instrument ID (e.g. ``"TSLA.NASDAQ"``).

    Returns
    -------
    str | None
        A Nautilus bar-type string (e.g.
        ``"TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"``) or ``None``.

    """
    from nautilus_trader.model.data import Bar
    from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

    try:
        catalog = ParquetDataCatalog(path=catalog_path)
        files = catalog.get_file_list_from_data_cls(Bar)
    except Exception:
        return None

    prefix = f"{instrument_id}-"
    candidates: list[str] = []
    for filepath in files:
        # Files live under .../data/bar/<BAR_TYPE>/...
        parts = filepath.split("/")
        for idx, part in enumerate(parts):
            if part == "bar" and idx + 1 < len(parts):
                bar_type = parts[idx + 1]
                if bar_type.startswith(prefix):
                    candidates.append(bar_type)
                break

    if not candidates:
        return None

    # Prefer 5-MINUTE, then 1-MINUTE, then first available
    for preferred in ("5-MINUTE", "1-MINUTE", "15-MINUTE", "1-HOUR", "1-DAY"):
        for candidate in candidates:
            if preferred in candidate:
                return candidate

    return candidates[0]


def _build_adhoc_strategy(
    instrument_id: str,
    strategy_path: str,
    bar_type: str,
) -> Any:
    """Build an ad-hoc :class:`ImportableStrategyConfig` from CLI args.

    Generates sensible bracket and risk defaults so the user does not
    need a ``bundles.yaml`` entry for quick experiments.

    Parameters
    ----------
    instrument_id : str
        Instrument ID (e.g. ``"TSLA.NASDAQ"``).
    strategy_path : str
        Fully-qualified strategy class path (e.g.
        ``"sam_trader.strategies.orb:OrbStrategy"``).
    bar_type : str
        Nautilus bar-type string.

    Returns
    -------
    ImportableStrategyConfig
        A strategy config ready for the backtest engine.

    """
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.trading.config import ImportableStrategyConfig

    # Derive config class path:  module:ClassName → module:ClassNameConfig
    module, class_name = strategy_path.split(":", 1)
    config_path = f"{module}:{class_name}Config"

    # Derive venue from instrument; default to IB for unknown venues
    try:
        venue = str(InstrumentId.from_str(instrument_id).venue)
    except (ValueError, AttributeError):
        venue = "NASDAQ"

    # Map exchange venue to broker venue
    broker_venue = "FUTU" if venue == "FUTU" else "IB"
    market = "HK" if venue in {"HKEX", "SEHK"} else "US"

    bundle_id = f"ad-hoc-{instrument_id.lower().replace('.', '-')}"
    strategy_id = f"{market}-{bundle_id}"

    config: dict[str, Any] = {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "stop_loss_ticks": 10,
        "take_profit_ticks": 30,
        "venue": broker_venue,
        "market": market,
        "bundle_id": bundle_id,
        "strategy_id": strategy_id,
    }

    if broker_venue == "IB":
        config["exchange"] = "SMART"

    return ImportableStrategyConfig(
        strategy_path=strategy_path,
        config_path=config_path,
        config=config,
    )


@cli.command("backtest")
@click.argument("bundle_id", required=False)
@click.option(
    "--bundles",
    "bundles_path",
    type=click.Path(path_type=Path),
    default=str(DEFAULT_BUNDLES_PATH),
    help="Path to bundles YAML file.",
)
@click.option(
    "--start",
    "start_date",
    required=True,
    help="Backtest start date (ISO format, e.g., 2024-01-01).",
)
@click.option(
    "--end",
    "end_date",
    required=True,
    help="Backtest end date (ISO format, e.g., 2024-06-30).",
)
@click.option(
    "--catalog",
    "catalog_path",
    default="data/catalog",
    help="Path to Parquet data catalog.",
)
@click.option(
    "--instrument",
    "instrument_id",
    help="Instrument ID for ad-hoc backtest (e.g., TSLA.NASDAQ).",
)
@click.option(
    "--strategy-path",
    "strategy_path",
    help=(
        "Strategy class path for ad-hoc backtest "
        '(e.g., "sam_trader.strategies.orb:OrbStrategy").'
    ),
)
@click.option(
    "--bar-type",
    "bar_type_override",
    help=(
        "Bar type for ad-hoc backtest "
        '(e.g., "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"). '
        "Inferred from catalog if omitted."
    ),
)
@click.option(
    "--sweep",
    "sweep_flags",
    multiple=True,
    help=(
        "Parameter sweep flag (repeatable). "
        "Format: key=val1,val2,val3 (e.g., --sweep stop_loss_ticks=5,10,15)."
    ),
)
@click.option(
    "--walk-forward",
    is_flag=True,
    default=False,
    help="Enable walk-forward optimization with rolling train/test windows.",
)
@click.option(
    "--train",
    "train_days",
    default="90d",
    help="Training (in-sample) window in days (e.g., 90 or 90d).",
)
@click.option(
    "--test",
    "test_days",
    default="30d",
    help="Test (out-of-sample) window in days (e.g., 30 or 30d).",
)
@click.pass_context
def backtest(
    ctx: click.Context,
    bundle_id: str | None,
    bundles_path: Path,
    start_date: str,
    end_date: str,
    catalog_path: str,
    instrument_id: str | None,
    strategy_path: str | None,
    bar_type_override: str | None,
    sweep_flags: tuple[str, ...],
    walk_forward: bool,
    train_days: str,
    test_days: str,
) -> None:
    """Run a backtest using NautilusTrader's BacktestNode.

    BACKTEST EXAMPLES:

        sam backtest tsla-orb-15m-futu --start 2024-01-01 --end 2024-06-30

        sam backtest --bundles config/bundles.yaml --start 2024-01-01 --end 2024-06-30

    AD-HOC EXAMPLES:

        sam backtest --instrument AAPL.NASDAQ \\
                     --strategy-path sam_trader.strategies.orb:OrbStrategy \\
                     --start 2023-01-01 --end 2024-12-31

    SWEEP EXAMPLES:

        sam backtest --sweep stop_loss_ticks=5,10,15 \\
            --sweep take_profit_ticks=20,30,40 \\
            --bundles config/bundles.yaml \\
            --start 2024-01-01 --end 2024-06-30

    WALK-FORWARD EXAMPLES:

        sam backtest --walk-forward --train 90d --test 30d \\
            --sweep stop_loss_ticks=5,10,15 \\
            --bundles config/bundles.yaml \\
            --start 2024-01-01 --end 2024-12-31

    """
    from sam_trader.bundle_loader import BundleLoaderError, load_bundles
    from sam_trader.services.backtest.engine import (
        BacktestEngineError,
        BacktestEngineWrapper,
    )

    # --- Ad-hoc mode validation ---
    adhoc_mode = instrument_id is not None or strategy_path is not None

    if adhoc_mode and bundle_id:
        raise click.ClickException(
            "Cannot use both BUNDLE_ID argument and --instrument/--strategy-path. "
            "They are mutually exclusive."
        )

    if instrument_id and not strategy_path:
        raise click.ClickException(
            "--instrument requires --strategy-path for ad-hoc backtests."
        )

    if strategy_path and not instrument_id:
        raise click.ClickException(
            "--strategy-path requires --instrument for ad-hoc backtests."
        )

    if adhoc_mode:
        # At this point both instrument_id and strategy_path are non-None
        # (validated above), but mypy needs a nudge.
        assert instrument_id is not None
        assert strategy_path is not None

        # Resolve bar type
        bar_type = bar_type_override
        if bar_type is None:
            bar_type = _infer_bar_type_from_catalog(catalog_path, instrument_id)
        if bar_type is None:
            raise click.ClickException(
                f"Could not infer bar type for {instrument_id} from catalog. "
                f"Use --bar-type to specify explicitly."
            )

        strategies = [_build_adhoc_strategy(instrument_id, strategy_path, bar_type)]
    else:
        # Load bundles
        if not bundles_path.exists():
            raise click.ClickException(f"Bundles file not found: {bundles_path}")

        try:
            all_bundles = load_bundles(bundles_path)
        except BundleLoaderError as exc:
            raise click.ClickException(f"Failed to load bundles: {exc}")

        if not all_bundles:
            raise click.ClickException(
                "No enabled bundles found. Check config/bundles.yaml."
            )

        # Filter to single bundle_id if provided
        if bundle_id:
            matching = [
                b for b in all_bundles if b.config.get("bundle_id") == bundle_id
            ]
            if not matching:
                available = [b.config.get("bundle_id", "?") for b in all_bundles]
                raise click.ClickException(
                    f"Bundle '{bundle_id}' not found. "
                    f"Available: {', '.join(available)}"
                )
            strategies = matching
        else:
            strategies = all_bundles

    # Extract instrument_ids and bar_types from strategy configs
    instrument_ids: list[str] = []
    bar_types: list[str] = []
    for s in strategies:
        cfg = s.config
        iid = cfg.get("instrument_id")
        if isinstance(iid, str) and iid not in instrument_ids:
            instrument_ids.append(iid)
        bt = cfg.get("bar_type")
        if isinstance(bt, str) and bt not in bar_types:
            bar_types.append(bt)

    if not instrument_ids:
        raise click.ClickException("No instrument_ids found in bundle configs")
    if not bar_types:
        raise click.ClickException("No bar_types found in bundle configs")

    # --- Walk-Forward path ---
    if walk_forward:
        _run_walk_forward(
            ctx=ctx,
            strategies=strategies,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start_date=start_date,
            end_date=end_date,
            catalog_path=catalog_path,
            sweep_flags=list(sweep_flags),
            train_days=train_days,
            test_days=test_days,
        )
        return

    # --- Parameter Sweep path ---
    if sweep_flags:
        _run_sweep(
            ctx=ctx,
            strategies=strategies,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start_date=start_date,
            end_date=end_date,
            catalog_path=catalog_path,
            sweep_flags=list(sweep_flags),
        )
        return

    # --- Single Run path ---
    # Run backtest
    wrapper = BacktestEngineWrapper(catalog_path=catalog_path)

    try:
        # Run all strategies in a single BacktestNode.
        # If there's a single strategy, run() returns one result.
        # For multi-strategy, run() handles them together.
        raw_result = wrapper.run(
            strategies=list(strategies),
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start=start_date,
            end=end_date,
        )
    except BacktestEngineError as exc:
        raise click.ClickException(f"Backtest failed: {exc}")
    except Exception as exc:
        raise click.ClickException(f"Backtest error: {exc}")

    # Build result summary — one entry per strategy
    bundles_data: list[dict[str, Any]] = []
    try:
        stats_pnls = getattr(raw_result, "stats_pnls", {}) or {}
        stats_returns = getattr(raw_result, "stats_returns", {}) or {}

        if stats_pnls:
            # BacktestRunConfig.run_analysis=True → per-strategy stats
            for strategy_key in stats_pnls:
                strategy_pnl = stats_pnls.get(strategy_key, {})
                strategy_ret = stats_returns
                if isinstance(stats_returns, dict) and strategy_key in stats_returns:
                    strategy_ret = stats_returns[strategy_key]

                entry: dict[str, Any] = {
                    "bundle_id": strategy_key,
                    "net_pnl": _safe_round(strategy_pnl.get("total_pnl"), 2),
                    "sharpe": _safe_round(_dict_get(strategy_ret, "sharpe_ratio"), 4),
                    "max_drawdown": _safe_round(
                        _dict_get(strategy_ret, "max_drawdown"), 4
                    ),
                    "win_rate": _safe_round(_dict_get(strategy_ret, "win_rate"), 4),
                }
                bundles_data.append(entry)
        else:
            # Fallback: single top-level result (run_analysis=False or edge case)
            entry = _build_backtest_summary(raw_result, bundle_id or "result")
            bundles_data.append(entry)

        # Add elapsed and total_trades at top level
        elapsed = getattr(raw_result, "elapsed_time", None)
        total_orders = getattr(raw_result, "total_orders", None)
        for e in bundles_data:
            if elapsed is not None and "elapsed" not in e:
                e["elapsed"] = round(elapsed, 2)
            if total_orders is not None and "total_trades" not in e:
                e["total_trades"] = int(total_orders)

    except Exception as exc:
        # If stats extraction fails, provide a minimal result
        bundles_data = [
            {
                "bundle_id": strategy_key if "strategy_key" in dir() else "result",
                "note": f"Stats extraction error: {exc}",
            }
        ]

    result_payload: dict[str, Any] = {
        "command": "backtest",
        "start": start_date,
        "end": end_date,
        "bundles": bundles_data,
    }

    if ctx.obj.get("json"):
        _out(ctx, result_payload)
    else:
        click.echo(_format_backtest_table(result_payload))


@cli.command("download-bars")
@click.option(
    "--instrument",
    default=None,
    help=(
        "Specific instrument ID (e.g., TSLA.NASDAQ). "
        "If omitted, uses all FUTU instruments from bundles.yaml."
    ),
)
@click.option(
    "--bar-type",
    "bar_type_spec",
    default="5-MINUTE",
    help="Bar type: 1-MINUTE, 5-MINUTE, 15-MINUTE, 1-HOUR, DAY.",
)
@click.option(
    "--lookback",
    default=None,
    type=int,
    help="Number of calendar days to look back (default 365).",
)
@click.option(
    "--start",
    "start_date",
    default=None,
    help="Start date (ISO format, e.g., 2023-01-01). Must be used with --end.",
)
@click.option(
    "--end",
    "end_date",
    default=None,
    help="End date (ISO format, e.g., 2024-12-31). Must be used with --start.",
)
@click.option(
    "--catalog",
    "catalog_path",
    default="data/catalog",
    help="Path to Parquet catalog directory.",
)
@click.pass_context
def download_bars(
    ctx: click.Context,
    instrument: str | None,
    bar_type_spec: str,
    lookback: int | None,
    start_date: str | None,
    end_date: str | None,
    catalog_path: str,
) -> None:
    """Download historical bars from Futu OpenD to Parquet catalog.

    Defaults to all enabled FUTU instruments from config/bundles.yaml when
    --instrument is not specified.

    Use either --lookback OR both --start and --end.
    """
    from datetime import date as _date

    # Validate date args
    has_range = start_date is not None or end_date is not None
    has_lookback = lookback is not None

    if has_range and has_lookback:
        raise click.ClickException(
            "--start/--end and --lookback are mutually exclusive. "
            "Use one or the other."
        )

    if not has_range and not has_lookback:
        raise click.ClickException(
            "Must provide either --lookback or both --start and --end."
        )

    parsed_start: _date | None = None
    parsed_end: _date | None = None
    if has_range:
        if start_date is None or end_date is None:
            raise click.ClickException("--start and --end must be provided together.")
        try:
            parsed_start = _date.fromisoformat(start_date)
            parsed_end = _date.fromisoformat(end_date)
        except ValueError as exc:
            raise click.ClickException(
                f"Invalid date format: {exc}. Use ISO format (YYYY-MM-DD)."
            )

    if instrument:
        instrument_ids = [instrument]
    else:
        instrument_ids = get_instruments_from_bundles(DEFAULT_BUNDLES_PATH)
        if not instrument_ids:
            raise click.ClickException(
                "No enabled FUTU instruments found in bundles.yaml. "
                "Use --instrument to specify one explicitly."
            )

    downloader = BarDownloader(
        catalog_path=catalog_path,
    )

    try:
        result = asyncio.run(
            downloader.download(
                instrument_ids=instrument_ids,
                bar_type_spec=bar_type_spec,
                lookback_days=lookback if lookback is not None else 365,
                start_date=parsed_start,
                end_date=parsed_end,
            )
        )
    except BarDownloaderError as exc:
        raise click.ClickException(str(exc))

    summary: dict[str, Any] = {
        "command": "download-bars",
        "bar_type": bar_type_spec,
        "instruments": instrument_ids,
        "total_bars_downloaded": result.total_bars_downloaded,
        "total_bars_written": result.total_bars_written,
        "failed": result.instruments_failed,
    }
    if lookback is not None:
        summary["lookback_days"] = lookback
    if parsed_start is not None:
        summary["start_date"] = str(parsed_start)
    if parsed_end is not None:
        summary["end_date"] = str(parsed_end)

    if ctx.obj.get("json"):
        _out(ctx, summary)
    else:
        if lookback is not None:
            header = f"Download Bars: {bar_type_spec} (lookback={lookback}d)"
        else:
            header = (
                f"Download Bars: {bar_type_spec} " f"({parsed_start} → {parsed_end})"
            )
        lines = [header, "=" * 50]
        for r in result.results:
            if r.error:
                lines.append(f"{r.instrument_id:<20} [FAIL] {r.error}")
            else:
                lines.append(
                    f"{r.instrument_id:<20} [OK] downloaded={r.bars_downloaded} "
                    f"({r.start_date} → {r.end_date})"
                )
        if result.instruments_failed:
            lines.append("")
            lines.append(f"Failed: {', '.join(result.instruments_failed)}")
        click.echo("\n".join(lines))

    if result.instruments_failed:
        raise click.ClickException(
            f"Download failed for {len(result.instruments_failed)} instrument(s)"
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (also used by ``sam-validate-bundles`` console script)."""
    try:
        rv = cli.main(args=argv, standalone_mode=False)
        if isinstance(rv, int):
            return rv
        return 0
    except click.ClickException as exc:
        click.echo(f"ERROR: {exc.message}", err=True)
        return 1
    except Exception as exc:
        click.echo(f"ERROR: {exc}", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
