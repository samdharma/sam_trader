"""Backup and restore system for SAM Trader V3.

Backs up PostgreSQL (pg_dump), Redis (BGSAVE + copy), Futu data volume
(docker run --volumes-from), and config/ dir.
Skips weekends and US/HK trading holidays.
"""

import argparse
import datetime
import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Final, cast

from sam_trader.services.market_calendar import MarketCalendarService

logger = logging.getLogger("sam_trader.backup")

# Environment-driven configuration
BACKUP_DIR: Final[Path] = Path(os.getenv("BACKUP_DIR", "/opt/sam_trader/backups"))
BACKUP_RETENTION_DAYS: Final[int] = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
POSTGRES_HOST: Final[str] = os.getenv("POSTGRES_HOST", "sam-postgres")
POSTGRES_PORT: Final[str] = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB: Final[str] = os.getenv("POSTGRES_DB", "sam_trader")
POSTGRES_USER: Final[str] = os.getenv("POSTGRES_USER", "sam")
POSTGRES_PASSWORD: Final[str] = os.getenv("POSTGRES_PASSWORD", "sam_secret")
REDIS_HOST: Final[str] = os.getenv("REDIS_HOST", "sam-redis")
REDIS_PORT: Final[str] = os.getenv("REDIS_PORT", "6379")
REDIS_PASSWORD: Final[str] = os.getenv("REDIS_PASSWORD", "")
FUTU_CONTAINER: Final[str] = os.getenv("FUTU_CONTAINER", "sam-futu-opend")
CONFIG_DIR: Final[Path] = Path(os.getenv("CONFIG_DIR", "/opt/sam_trader/config"))
DOCKER_BINARY: Final[str] = os.getenv("DOCKER_BINARY", "docker")


class BackupError(Exception):
    """Raised when a backup or restore operation fails."""


def _is_trading_holiday(date_obj: datetime.date) -> bool:
    """Return True if the given date is a US or HK trading holiday."""
    cal = MarketCalendarService()
    return cal.is_holiday("US", date_obj) or cal.is_holiday("HK", date_obj)


def _run_cmd(
    cmd: list[str],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    stdout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command, raising BackupError on failure."""
    merged_env = {**os.environ, **(env or {})}
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=(stdout is None),
        text=True,
        env=merged_env,
        cwd=cwd,
        stdout=stdout if stdout is not None else subprocess.PIPE,
    )
    if result.returncode != 0:
        stderr = result.stderr or ""
        raise BackupError(f"Command failed: {' '.join(cmd)}\nstderr: {stderr}")
    return result


def _backup_postgres(temp_dir: Path) -> Path:
    """Dump PostgreSQL database to a SQL file."""
    dump_path = temp_dir / "postgres_dump.sql"
    env = {"PGPASSWORD": POSTGRES_PASSWORD}
    cmd = [
        "pg_dump",
        "-h",
        POSTGRES_HOST,
        "-p",
        POSTGRES_PORT,
        "-U",
        POSTGRES_USER,
        "-d",
        POSTGRES_DB,
        "-f",
        str(dump_path),
    ]
    _run_cmd(cmd, env=env)
    if not dump_path.exists() or dump_path.stat().st_size == 0:
        raise BackupError("PostgreSQL dump is empty or missing")
    logger.info(
        "PostgreSQL dump created: %s (%s bytes)",
        dump_path,
        dump_path.stat().st_size,
    )
    return dump_path


def _backup_redis(temp_dir: Path) -> Path:
    """Trigger BGSAVE and copy Redis RDB file."""
    rdb_path = temp_dir / "redis_dump.rdb"

    # Trigger BGSAVE
    redis_cmd: list[str] = [
        "redis-cli",
        "-h",
        REDIS_HOST,
        "-p",
        REDIS_PORT,
    ]
    if REDIS_PASSWORD:
        redis_cmd.extend(["-a", REDIS_PASSWORD])
    redis_cmd.append("BGSAVE")
    _run_cmd(redis_cmd)

    # Wait briefly for BGSAVE to complete
    logger.info("Waiting for Redis BGSAVE to complete...")
    # Attempt to copy dump.rdb from the redis container
    copy_cmd = [
        DOCKER_BINARY,
        "cp",
        f"{REDIS_HOST}:/data/dump.rdb",
        str(rdb_path),
    ]
    try:
        _run_cmd(copy_cmd)
    except BackupError:
        # Fallback: use redis-cli --rdb (writes RDB directly)
        rdb_cmd = [
            "redis-cli",
            "-h",
            REDIS_HOST,
            "-p",
            REDIS_PORT,
        ]
        if REDIS_PASSWORD:
            rdb_cmd.extend(["-a", REDIS_PASSWORD])
        rdb_cmd.extend(["--rdb", str(rdb_path)])
        _run_cmd(rdb_cmd)

    if not rdb_path.exists() or rdb_path.stat().st_size == 0:
        raise BackupError("Redis RDB is empty or missing")
    logger.info(
        "Redis RDB copied: %s (%s bytes)",
        rdb_path,
        rdb_path.stat().st_size,
    )
    return rdb_path


def _backup_futu_volume(temp_dir: Path) -> Path:
    """Backup Futu OpenD data volume via docker."""
    archive_path = temp_dir / "futu_volume.tar.gz"
    cmd = [
        DOCKER_BINARY,
        "run",
        "--rm",
        "--volumes-from",
        FUTU_CONTAINER,
        "busybox",
        "tar",
        "czf",
        "-",
        "/home/futu/.com.futunn.FutuOpenD",
    ]
    logger.info("Backing up Futu volume from container %s", FUTU_CONTAINER)
    with open(archive_path, "wb") as f:
        merged_env = {**os.environ}
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=merged_env)
    if result.returncode != 0:
        stderr = (
            result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        )
        raise BackupError(f"Futu volume backup failed: {stderr}")
    if not archive_path.exists() or archive_path.stat().st_size == 0:
        raise BackupError("Futu volume archive is empty")
    logger.info(
        "Futu volume archive: %s (%s bytes)",
        archive_path,
        archive_path.stat().st_size,
    )
    return archive_path


def _backup_config(temp_dir: Path) -> Path:
    """Backup config directory."""
    archive_path = temp_dir / "config.tar.gz"
    if not CONFIG_DIR.exists():
        raise BackupError(f"Config directory not found: {CONFIG_DIR}")
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(CONFIG_DIR, arcname="config")
    logger.info(
        "Config archive: %s (%s bytes)",
        archive_path,
        archive_path.stat().st_size,
    )
    return archive_path


def _create_manifest(temp_dir: Path, components: dict[str, str]) -> Path:
    """Create a JSON manifest describing the backup contents."""
    manifest = {
        "version": "1.0",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "components": components,
    }
    manifest_path = temp_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def backup() -> Path:
    """Run a full backup and return the path to the archive."""
    today = datetime.date.today()
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.info("Skipping backup: weekend (%s)", today.strftime("%A"))
        raise SystemExit(0)
    if _is_trading_holiday(today):
        logger.info("Skipping backup: trading holiday (%s)", today.isoformat())
        raise SystemExit(0)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"sam_trader_backup_{timestamp}.tar.gz"
    archive_path = BACKUP_DIR / archive_name

    with tempfile.TemporaryDirectory(prefix="sam_backup_") as tmp:
        temp_dir = Path(tmp)
        components: dict[str, str] = {}

        pg_dump = _backup_postgres(temp_dir)
        components["postgres"] = pg_dump.name

        redis_rdb = _backup_redis(temp_dir)
        components["redis"] = redis_rdb.name

        futu_archive = _backup_futu_volume(temp_dir)
        components["futu_volume"] = futu_archive.name

        config_archive = _backup_config(temp_dir)
        components["config"] = config_archive.name

        _create_manifest(temp_dir, components)

        # Create final combined archive
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in temp_dir.iterdir():
                tar.add(item, arcname=item.name)

    logger.info("Backup complete: %s", archive_path)
    _cleanup_old_backups()
    return archive_path


def _cleanup_old_backups() -> None:
    """Remove backup archives older than BACKUP_RETENTION_DAYS."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=BACKUP_RETENTION_DAYS)
    pattern = re.compile(r"^sam_trader_backup_(\d{8})_(\d{6})\.tar\.gz$")
    for file_path in BACKUP_DIR.glob("sam_trader_backup_*.tar.gz"):
        match = pattern.match(file_path.name)
        if not match:
            continue
        file_dt = datetime.datetime.strptime(
            match.group(1) + match.group(2), "%Y%m%d%H%M%S"
        )
        if file_dt < cutoff:
            logger.info("Removing old backup: %s", file_path)
            file_path.unlink()


def _validate_archive(archive_path: Path) -> dict[str, Any]:
    """Validate that a backup archive is a valid tar.gz and contains
    expected components."""
    if not archive_path.exists():
        raise BackupError(f"Archive not found: {archive_path}")
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getnames()
    except (tarfile.TarError, gzip.BadGzipFile, OSError) as exc:
        raise BackupError(f"Archive integrity check failed: {exc}") from exc

    expected = {
        "manifest.json",
        "postgres_dump.sql",
        "redis_dump.rdb",
        "futu_volume.tar.gz",
        "config.tar.gz",
    }
    missing = expected - set(members)
    if missing:
        raise BackupError(f"Archive missing expected components: {missing}")

    # Parse manifest
    with tarfile.open(archive_path, "r:gz") as tar:
        manifest_file = tar.extractfile("manifest.json")
        if manifest_file is None:
            raise BackupError("manifest.json not found in archive")
        manifest = cast(
            dict[str, Any], json.loads(manifest_file.read().decode("utf-8"))
        )

    logger.info("Archive validated: %s", archive_path)
    return manifest


def restore(date_str: str) -> None:
    """Restore from a backup archive for the given date (YYYYMMDD)."""
    archive_path = BACKUP_DIR / f"sam_trader_backup_{date_str}_060000.tar.gz"
    if not archive_path.exists():
        # Wildcard match for any time on that date
        candidates = list(BACKUP_DIR.glob(f"sam_trader_backup_{date_str}_*.tar.gz"))
        if not candidates:
            raise BackupError(f"No backup found for date: {date_str}")
        archive_path = candidates[0]

    manifest = _validate_archive(archive_path)
    logger.info("Restoring from %s — manifest: %s", archive_path, manifest)

    # Extract to temp dir
    with tempfile.TemporaryDirectory(prefix="sam_restore_") as tmp:
        temp_dir = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=temp_dir)

        # Restore PostgreSQL
        pg_dump = temp_dir / manifest["components"]["postgres"]
        env = {"PGPASSWORD": POSTGRES_PASSWORD}
        cmd = [
            "psql",
            "-h",
            POSTGRES_HOST,
            "-p",
            POSTGRES_PORT,
            "-U",
            POSTGRES_USER,
            "-d",
            POSTGRES_DB,
            "-f",
            str(pg_dump),
        ]
        _run_cmd(cmd, env=env)
        logger.info("PostgreSQL restored from %s", pg_dump)

        # Restore Redis
        redis_rdb = temp_dir / manifest["components"]["redis"]
        copy_cmd = [
            DOCKER_BINARY,
            "cp",
            str(redis_rdb),
            f"{REDIS_HOST}:/data/dump.rdb",
        ]
        _run_cmd(copy_cmd)
        logger.info("Redis RDB restored to %s", REDIS_HOST)

        # Restore Futu volume
        futu_archive = temp_dir / manifest["components"]["futu_volume"]
        cmd = [
            DOCKER_BINARY,
            "run",
            "--rm",
            "--volumes-from",
            FUTU_CONTAINER,
            "-v",
            f"{futu_archive.parent}:/restore:ro",
            "busybox",
            "tar",
            "xzf",
            f"/restore/{futu_archive.name}",
            "-C",
            "/",
        ]
        _run_cmd(cmd)
        logger.info("Futu volume restored")

        # Restore config
        config_archive = temp_dir / manifest["components"]["config"]
        with tarfile.open(config_archive, "r:gz") as tar:
            tar.extractall(path=temp_dir / "config_extracted")
        extracted_config = temp_dir / "config_extracted" / "config"
        if extracted_config.exists():
            if CONFIG_DIR.exists():
                shutil.rmtree(CONFIG_DIR)
            shutil.copytree(extracted_config, CONFIG_DIR)
            logger.info("Config restored to %s", CONFIG_DIR)

    logger.info("Restore complete for date %s", date_str)


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="SAM Trader V3 Backup & Restore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Run backup")
    backup_parser.set_defaults(func=lambda _: backup())

    restore_parser = subparsers.add_parser("restore", help="Restore from backup")
    restore_parser.add_argument("--date", required=True, help="Date in YYYYMMDD format")
    restore_parser.set_defaults(func=lambda args: restore(args.date))

    args = parser.parse_args()
    try:
        args.func(args)
    except BackupError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
