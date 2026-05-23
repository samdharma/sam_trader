"""Unit tests for services/rotate_logs.py."""

from __future__ import annotations

import datetime
import gzip
from pathlib import Path

from sam_trader.services.rotate_logs import rotate_logs


class TestRotateLogs:
    def test_no_rotation_when_files_small(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("small content")
        rotated, deleted = rotate_logs(tmp_path, retention_days=30, max_size_mb=100)
        assert rotated == 0
        assert deleted == 0
        assert log_file.exists()

    def test_rotates_oversized_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "big.log"
        # Write slightly more than 1 MB
        log_file.write_bytes(b"x" * (1024 * 1024 + 1))
        rotated, deleted = rotate_logs(tmp_path, retention_days=30, max_size_mb=1)
        assert rotated == 1
        assert log_file.stat().st_size == 0  # truncated
        archives = list(tmp_path.glob("*.log.gz"))
        assert len(archives) == 1
        with gzip.open(archives[0], "rb") as f:
            assert f.read() == b"x" * (1024 * 1024 + 1)

    def test_deletes_old_archives(self, tmp_path: Path) -> None:
        old_archive = tmp_path / "old_20200101_000000.log.gz"
        old_archive.write_bytes(gzip.compress(b"old"))
        # Set mtime to 60 days ago
        past = datetime.datetime.now() - datetime.timedelta(days=60)
        import os

        os.utime(old_archive, (past.timestamp(), past.timestamp()))

        rotated, deleted = rotate_logs(tmp_path, retention_days=30, max_size_mb=100)
        assert deleted == 1
        assert not old_archive.exists()

    def test_keeps_recent_archives(self, tmp_path: Path) -> None:
        recent = tmp_path / "recent_20240101_000000.log.gz"
        recent.write_bytes(gzip.compress(b"recent"))
        rotated, deleted = rotate_logs(tmp_path, retention_days=30, max_size_mb=100)
        assert deleted == 0
        assert recent.exists()

    def test_creates_log_dir_if_missing(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "missing_logs"
        assert not log_dir.exists()
        rotate_logs(log_dir, retention_days=30, max_size_mb=100)
        assert log_dir.exists()
