"""Log rotation for SAM Trader V3.

Compresses individual .log files when they exceed MAX_SIZE_MB,
and deletes .log.gz archives older than RETENTION_DAYS.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import logging
import os
import shutil
from pathlib import Path

LOG_DIR: Path = Path(os.getenv("LOG_DIR", "/opt/sam_trader/logs"))
RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))
MAX_SIZE_MB: int = int(os.getenv("LOG_MAX_SIZE_MB", "100"))

logger = logging.getLogger("sam_trader.rotate_logs")


def rotate_logs(
    log_dir: Path = LOG_DIR,
    retention_days: int = RETENTION_DAYS,
    max_size_mb: int = MAX_SIZE_MB,
) -> tuple[int, int]:
    """Rotate oversized logs and purge stale archives.

    Returns (rotated_count, deleted_count).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    rotated = 0
    deleted = 0
    max_bytes = max_size_mb * 1024 * 1024
    cutoff = datetime.datetime.now() - datetime.timedelta(days=retention_days)

    for log_file in log_dir.glob("*.log"):
        size = log_file.stat().st_size
        if size > max_bytes:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"{log_file.stem}_{timestamp}.log.gz"
            archive_path = log_dir / archive_name

            with open(log_file, "rb") as f_in:
                with gzip.open(archive_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            log_file.write_text("")
            rotated += 1
            logger.info(
                "Rotated %s -> %s (%d bytes)", log_file.name, archive_name, size
            )

    for archive in log_dir.glob("*.log.gz"):
        mtime = datetime.datetime.fromtimestamp(archive.stat().st_mtime)
        if mtime < cutoff:
            archive.unlink()
            deleted += 1
            logger.info("Deleted old archive: %s", archive.name)

    logger.info("Rotation complete: %d rotated, %d deleted", rotated, deleted)
    return rotated, deleted


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="SAM Trader V3 Log Rotation")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=LOG_DIR,
        help="Directory containing .log files",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=RETENTION_DAYS,
        help="Delete archives older than N days",
    )
    parser.add_argument(
        "--max-size-mb",
        type=int,
        default=MAX_SIZE_MB,
        help="Rotate files larger than N MB",
    )
    args = parser.parse_args()
    rotate_logs(args.log_dir, args.retention_days, args.max_size_mb)


if __name__ == "__main__":
    main()
