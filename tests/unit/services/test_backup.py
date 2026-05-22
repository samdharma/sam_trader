"""Unit tests for sam_trader.services.backup."""

import datetime
import json
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services import backup


class TestIsTradingHoliday:
    """Tests for _is_trading_holiday."""

    def test_known_us_holiday(self) -> None:
        """2024-07-04 is a US holiday."""
        assert backup._is_trading_holiday(datetime.date(2024, 7, 4)) is True

    def test_known_hk_holiday(self) -> None:
        """2024-10-01 is a HK holiday."""
        assert backup._is_trading_holiday(datetime.date(2024, 10, 1)) is True

    def test_regular_weekday_not_holiday(self) -> None:
        """2024-07-08 is a regular Monday."""
        assert backup._is_trading_holiday(datetime.date(2024, 7, 8)) is False

    def test_future_year_not_in_hardcoded(self) -> None:
        """Years beyond 2026 fall back to holidays package if available."""
        # 2030-01-01 is a holiday; result depends on whether holidays pkg is installed
        result = backup._is_trading_holiday(datetime.date(2030, 1, 1))
        if backup._HAS_HOLIDAYS:
            assert result is True
        else:
            # Without holidays package, 2030 is not in hardcoded set
            assert result is False


class TestBackupSkipConditions:
    """Tests that backup skips weekends and holidays."""

    @patch("sam_trader.services.backup.datetime")
    @patch("sam_trader.services.backup.logger")
    def test_skip_saturday(self, mock_logger: Any, mock_dt: Any) -> None:
        """Backup skips Saturday."""
        mock_dt.date.today.return_value = datetime.date(2024, 7, 6)  # Saturday
        mock_dt.datetime.now.return_value = datetime.datetime(2024, 7, 6, 6, 0, 0)
        with pytest.raises(SystemExit) as exc_info:
            backup.backup()
        assert exc_info.value.code == 0
        mock_logger.info.assert_any_call("Skipping backup: weekend (%s)", "Saturday")

    @patch("sam_trader.services.backup.datetime")
    @patch("sam_trader.services.backup.logger")
    def test_skip_sunday(self, mock_logger: Any, mock_dt: Any) -> None:
        """Backup skips Sunday."""
        mock_dt.date.today.return_value = datetime.date(2024, 7, 7)  # Sunday
        mock_dt.datetime.now.return_value = datetime.datetime(2024, 7, 7, 6, 0, 0)
        with pytest.raises(SystemExit) as exc_info:
            backup.backup()
        assert exc_info.value.code == 0
        mock_logger.info.assert_any_call("Skipping backup: weekend (%s)", "Sunday")

    @patch("sam_trader.services.backup.datetime")
    @patch("sam_trader.services.backup.logger")
    def test_skip_trading_holiday(self, mock_logger: Any, mock_dt: Any) -> None:
        """Backup skips US/HK trading holidays."""
        mock_dt.date.today.return_value = datetime.date(2024, 7, 4)  # Thu holiday
        mock_dt.datetime.now.return_value = datetime.datetime(2024, 7, 4, 6, 0, 0)
        with pytest.raises(SystemExit) as exc_info:
            backup.backup()
        assert exc_info.value.code == 0
        mock_logger.info.assert_any_call(
            "Skipping backup: trading holiday (%s)", "2024-07-04"
        )


class TestBackupCreation:
    """Tests for successful backup creation."""

    @patch("sam_trader.services.backup._backup_config")
    @patch("sam_trader.services.backup._backup_futu_volume")
    @patch("sam_trader.services.backup._backup_redis")
    @patch("sam_trader.services.backup._backup_postgres")
    @patch("sam_trader.services.backup._cleanup_old_backups")
    @patch("sam_trader.services.backup.datetime")
    def test_backup_creates_archive(
        self,
        mock_dt: Any,
        mock_cleanup: Any,
        mock_pg: Any,
        mock_redis: Any,
        mock_futu: Any,
        mock_config: Any,
        tmp_path: Path,
    ) -> None:
        """Backup creates a tar.gz archive with all components."""
        backup_date = datetime.date(2024, 7, 8)  # Monday
        backup_time = datetime.datetime(2024, 7, 8, 6, 0, 0)
        mock_dt.date.today.return_value = backup_date
        mock_dt.datetime.now.return_value = backup_time

        # Ensure mocks create files in the temp dir passed to them
        def _make_pg(temp_dir: Path) -> Path:
            p = temp_dir / "postgres_dump.sql"
            p.write_text("pg dump")
            return p

        def _make_redis(temp_dir: Path) -> Path:
            p = temp_dir / "redis_dump.rdb"
            p.write_bytes(b"redis")
            return p

        def _make_futu(temp_dir: Path) -> Path:
            p = temp_dir / "futu_volume.tar.gz"
            p.write_bytes(b"futu")
            return p

        def _make_config(temp_dir: Path) -> Path:
            p = temp_dir / "config.tar.gz"
            p.write_bytes(b"config")
            return p

        mock_pg.side_effect = _make_pg
        mock_redis.side_effect = _make_redis
        mock_futu.side_effect = _make_futu
        mock_config.side_effect = _make_config

        with patch.object(backup, "BACKUP_DIR", tmp_path):
            archive_path = backup.backup()

        assert archive_path.exists()
        assert archive_path.name == "sam_trader_backup_20240708_060000.tar.gz"

        # Verify archive contents
        with tarfile.open(archive_path, "r:gz") as tar:
            members = set(tar.getnames())
        assert "manifest.json" in members
        assert "postgres_dump.sql" in members
        assert "redis_dump.rdb" in members
        assert "futu_volume.tar.gz" in members
        assert "config.tar.gz" in members
        mock_cleanup.assert_called_once()


class TestValidateArchive:
    """Tests for _validate_archive."""

    def test_valid_archive(self, tmp_path: Path) -> None:
        """Validation succeeds for a well-formed archive."""
        archive = tmp_path / "valid.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            # manifest.json with proper content
            manifest = tmp_path / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": "1.0",
                        "created": "2024-07-08T06:00:00+00:00",
                        "components": {
                            "postgres": "postgres_dump.sql",
                            "redis": "redis_dump.rdb",
                            "futu_volume": "futu_volume.tar.gz",
                            "config": "config.tar.gz",
                        },
                    }
                )
            )
            tar.add(manifest, arcname="manifest.json")
            for name in [
                "postgres_dump.sql",
                "redis_dump.rdb",
                "futu_volume.tar.gz",
                "config.tar.gz",
            ]:
                item = tmp_path / name
                item.write_text(name)
                tar.add(item, arcname=name)
        parsed = backup._validate_archive(archive)
        assert parsed["version"] == "1.0"

    def test_missing_manifest(self, tmp_path: Path) -> None:
        """Validation fails when manifest.json is missing."""
        archive = tmp_path / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            item = tmp_path / "postgres_dump.sql"
            item.write_text("dump")
            tar.add(item, arcname="postgres_dump.sql")
        with pytest.raises(backup.BackupError, match="missing expected"):
            backup._validate_archive(archive)

    def test_corrupt_archive(self, tmp_path: Path) -> None:
        """Validation fails for a corrupt gzip file."""
        bad = tmp_path / "corrupt.tar.gz"
        bad.write_bytes(b"not a valid gzip file")
        with pytest.raises(backup.BackupError, match="integrity check failed"):
            backup._validate_archive(bad)

    def test_archive_not_found(self, tmp_path: Path) -> None:
        """Validation fails when archive path does not exist."""
        missing = tmp_path / "missing.tar.gz"
        with pytest.raises(backup.BackupError, match="Archive not found"):
            backup._validate_archive(missing)


class TestCleanupOldBackups:
    """Tests for _cleanup_old_backups."""

    @patch.object(backup, "BACKUP_RETENTION_DAYS", 7)
    def test_removes_old_backups(self, tmp_path: Path) -> None:
        """Backups older than retention days are removed."""
        with patch.object(backup, "BACKUP_DIR", tmp_path):
            old = tmp_path / "sam_trader_backup_20240101_060000.tar.gz"
            old.write_bytes(b"old")
            recent = tmp_path / "sam_trader_backup_20991231_060000.tar.gz"
            recent.write_bytes(b"recent")
            backup._cleanup_old_backups()
            assert not old.exists()
            assert recent.exists()

    def test_keeps_recent_backups(self, tmp_path: Path) -> None:
        """Backups within retention period are kept."""
        with patch.object(backup, "BACKUP_DIR", tmp_path):
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            recent = tmp_path / f"sam_trader_backup_{now}.tar.gz"
            recent.write_bytes(b"recent")
            backup._cleanup_old_backups()
            assert recent.exists()


class TestRestore:
    """Tests for restore."""

    @patch("sam_trader.services.backup._run_cmd")
    def test_restore_validates_then_restores(
        self,
        mock_run_cmd: Any,
        tmp_path: Path,
    ) -> None:
        """Restore validates archive integrity before restoring components."""
        with patch.object(backup, "BACKUP_DIR", tmp_path):
            # Create a valid backup archive
            archive = tmp_path / "sam_trader_backup_20240708_060000.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                # manifest.json with proper content
                manifest = tmp_path / "manifest.json"
                manifest.write_text(
                    json.dumps(
                        {
                            "version": "1.0",
                            "created": "2024-07-08T06:00:00+00:00",
                            "components": {
                                "postgres": "postgres_dump.sql",
                                "redis": "redis_dump.rdb",
                                "futu_volume": "futu_volume.tar.gz",
                                "config": "config.tar.gz",
                            },
                        }
                    )
                )
                tar.add(manifest, arcname="manifest.json")

                for name in [
                    "postgres_dump.sql",
                    "redis_dump.rdb",
                ]:
                    item = tmp_path / name
                    item.write_text(name)
                    tar.add(item, arcname=name)

                # futu_volume.tar.gz — a real nested tar.gz
                futu = tmp_path / "futu_volume.tar.gz"
                with tarfile.open(futu, "w:gz") as ftar:
                    futu_item = tmp_path / "futu_data.txt"
                    futu_item.write_text("futu data")
                    ftar.add(futu_item, arcname="futu_data.txt")
                tar.add(futu, arcname="futu_volume.tar.gz")

                # config.tar.gz — a real nested tar.gz
                config = tmp_path / "config.tar.gz"
                with tarfile.open(config, "w:gz") as ctar:
                    cfg_item = tmp_path / "config.yaml"
                    cfg_item.write_text("key: value")
                    ctar.add(cfg_item, arcname="config.yaml")
                tar.add(config, arcname="config.tar.gz")

            with patch.object(backup, "CONFIG_DIR", tmp_path / "config_out"):
                backup.restore("20240708")

            # Validate that psql restore was called
            calls = [str(c) for c in mock_run_cmd.call_args_list]
            assert any("psql" in c for c in calls)
            assert any("docker" in c for c in calls)

    @patch("sam_trader.services.backup.logger")
    def test_restore_missing_archive(self, mock_logger: Any, tmp_path: Path) -> None:
        """Restore fails gracefully when no archive exists for the date."""
        with patch.object(backup, "BACKUP_DIR", tmp_path):
            with pytest.raises(backup.BackupError, match="No backup found"):
                backup.restore("19990101")


class TestRunCmd:
    """Tests for _run_cmd."""

    @patch("sam_trader.services.backup.subprocess.run")
    def test_success(self, mock_run: Any) -> None:
        """Successful command returns CompletedProcess."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = backup._run_cmd(["echo", "hello"])
        assert result.returncode == 0

    @patch("sam_trader.services.backup.subprocess.run")
    def test_failure_raises(self, mock_run: Any) -> None:
        """Failed command raises BackupError."""
        mock_run.return_value = MagicMock(returncode=1, stderr="something went wrong")
        with pytest.raises(backup.BackupError, match="something went wrong"):
            backup._run_cmd(["false"])
