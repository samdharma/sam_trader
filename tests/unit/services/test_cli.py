"""Unit tests for services/cli.py."""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

from sam_trader.services.cli import main


class TestStatusCommand:
    @patch("sam_trader.services.cli._run")
    def test_status_returns_container_list(self, mock_run: Any, capsys: Any) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "NAMES\tSTATUS\tPORTS\n"
                "sam-trader\tUp 2 hours\t8080/tcp\n"
                "sam-postgres\tUp 2 hours\t5432/tcp\n"
            ),
        )
        rc = main(["status"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "sam-trader" in captured.out
        assert "Up 2 hours" in captured.out

    @patch("sam_trader.services.cli._run")
    def test_status_json(self, mock_run: Any, capsys: Any) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAMES\tSTATUS\tPORTS\nsam-trader\tUp 2 hours\t8080/tcp\n",
        )
        rc = main(["--json", "status"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "status"
        assert len(data["containers"]) == 1
        assert data["containers"][0]["name"] == "sam-trader"


class TestHealthCommand:
    @patch("sam_trader.services.cli.subprocess.run")
    def test_health_all_up(self, mock_subproc: Any, capsys: Any) -> None:
        def side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            m = MagicMock()
            if "psql" in cmd:
                m.returncode = 0
                m.stdout = " 1 \n"
            elif "redis-cli" in cmd and "ping" in cmd:
                m.returncode = 0
                m.stdout = "PONG\n"
            elif "docker" in cmd and "inspect" in cmd:
                m.returncode = 0
                m.stdout = "healthy\n"
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        mock_subproc.side_effect = side_effect
        rc = main(["health"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "HEALTHY" in captured.out or "UP" in captured.out

    @patch("sam_trader.services.cli.subprocess.run")
    def test_health_json(self, mock_subproc: Any, capsys: Any) -> None:
        def side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            m = MagicMock()
            if "psql" in cmd:
                m.returncode = 1
                m.stdout = ""
            elif "redis-cli" in cmd and "ping" in cmd:
                m.returncode = 0
                m.stdout = "PONG\n"
            elif "docker" in cmd and "inspect" in cmd:
                m.returncode = 0
                m.stdout = "healthy\n"
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        mock_subproc.side_effect = side_effect
        rc = main(["--json", "health"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["overall"] == "UNHEALTHY"
        assert data["checks"]["postgres"]["status"] == "DOWN"
        assert data["checks"]["redis"]["status"] == "UP"


class TestBackupCommand:
    @patch("sam_trader.services.cli.run_backup")
    def test_backup_success(self, mock_backup: Any, capsys: Any) -> None:
        mock_backup.return_value = pathlib.Path(
            "/opt/sam_trader/backups/sam_trader_backup_20240101_120000.tar.gz"
        )
        rc = main(["backup"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "success" in captured.out
        assert "sam_trader_backup" in captured.out

    @patch("sam_trader.services.cli.run_backup")
    def test_backup_skipped(self, mock_backup: Any, capsys: Any) -> None:
        mock_backup.side_effect = SystemExit(0)
        rc = main(["backup"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "skipped" in captured.out


class TestRestoreCommand:
    @patch("sam_trader.services.cli.run_restore")
    def test_restore_success(self, mock_restore: Any, capsys: Any) -> None:
        rc = main(["restore", "20240101"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "success" in captured.out
        assert "20240101" in captured.out
        mock_restore.assert_called_once_with("20240101")


class TestLogsCommand:
    @patch("sam_trader.services.cli._run")
    def test_logs_single_service(self, mock_run: Any, capsys: Any) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="log line 1\nlog line 2\n"
        )
        rc = main(["logs", "sam-trader"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "log line 1" in captured.out

    @patch("sam_trader.services.cli._run")
    def test_logs_all_services(self, mock_run: Any, capsys: Any) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="sam-trader\nsam-postgres\n"),
            MagicMock(returncode=0, stdout="trader logs\n"),
            MagicMock(returncode=0, stdout="postgres logs\n"),
        ]
        rc = main(["logs"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "trader logs" in captured.out
        assert "postgres logs" in captured.out


class TestRestartCommand:
    @patch("sam_trader.services.cli.subprocess.run")
    def test_restart_sends_signal(self, mock_subproc: Any, capsys: Any) -> None:
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        rc = main(["restart"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "signal_sent" in captured.out
        # Verify redis PUBLISH and docker compose restart were called
        calls = [c[0][0] for c in mock_subproc.call_args_list]
        assert any("PUBLISH" in str(c) for c in calls)
        assert any("restart" in str(c) for c in calls)


class TestQuoteCommand:
    @patch("sam_trader.services.quote._try_cache")
    def test_quote_from_redis_cache(self, mock_cache: Any, capsys: Any) -> None:
        mock_cache.return_value = {
            "bid": 150.0,
            "ask": 150.5,
            "last": 150.25,
            "source": "redis_cache",
        }
        rc = main(["quote", "AAPL.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "redis_cache" in captured.out
        assert "AAPL.NASDAQ" in captured.out
        assert "150.00" in captured.out or "150.50" in captured.out

    @patch("sam_trader.services.quote._try_cache")
    @patch("sam_trader.services.quote._try_futu_broker")
    def test_quote_fallback_to_broker(
        self, mock_broker: Any, mock_cache: Any, capsys: Any
    ) -> None:
        mock_cache.return_value = None
        mock_broker.return_value = {
            "symbol": "TSLA.NASDAQ",
            "bid": 250.0,
            "ask": 250.5,
            "last": 250.25,
            "source": "futu_broker",
            "timestamp": "2026-05-23T12:00:00+00:00",
        }
        rc = main(["quote", "TSLA.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "futu_broker" in captured.out
        assert "TSLA.NASDAQ" in captured.out
        assert "250.00" in captured.out or "250.50" in captured.out


class TestDeployCommand:
    @patch("sam_trader.services.cli._run")
    @patch("sam_trader.services.cli._signal_restart")
    def test_deploy_with_tag(
        self, mock_restart: Any, mock_run: Any, capsys: Any
    ) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        rc = main(["deploy", "--tag", "v1.2.3"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "v1.2.3" in captured.out
        mock_restart.assert_called_once()

    @patch("sam_trader.services.cli._run")
    @patch("sam_trader.services.cli._signal_restart")
    def test_deploy_without_tag(
        self, mock_restart: Any, mock_run: Any, capsys: Any
    ) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        rc = main(["deploy"])
        _ = capsys.readouterr()
        assert rc == 0
        mock_restart.assert_called_once()


class TestUpdateCommand:
    @patch("sam_trader.services.cli._run")
    @patch("sam_trader.services.cli._signal_restart")
    def test_update(self, mock_restart: Any, mock_run: Any, capsys: Any) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        rc = main(["update"])
        _ = capsys.readouterr()
        assert rc == 0
        mock_restart.assert_called_once()


class TestRollbackCommand:
    @patch("sam_trader.services.cli._run")
    @patch("sam_trader.services.cli._signal_restart")
    def test_rollback(self, mock_restart: Any, mock_run: Any, capsys: Any) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        rc = main(["rollback", "v1.0.0"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "v1.0.0" in captured.out
        mock_restart.assert_called_once()


class TestHotfixCommand:
    @patch("sam_trader.services.cli._run")
    def test_hotfix_copies_file(
        self, mock_run: Any, tmp_path: pathlib.Path, capsys: Any
    ) -> None:
        src = tmp_path / "orb.py"
        src.write_text("# test")
        rc = main(["hotfix", str(src)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "copied" in captured.out
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("cp" in c for c in calls)
        assert any("touch" in c for c in calls)

    def test_hotfix_missing_file(self, capsys: Any) -> None:
        rc = main(["hotfix", "/nonexistent/path.py"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "not found" in captured.err


class TestVersionCommand:
    @patch("sam_trader.services.cli._run")
    def test_version(self, mock_run: Any, capsys: Any) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="v1.0.0-5-gabc1234\n"),
            MagicMock(returncode=0, stdout="abc1234\n"),
            MagicMock(returncode=0, stdout="2024-01-01T00:00:00Z\n"),
        ]
        rc = main(["version"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "v1.0.0" in captured.out
        assert "abc1234" in captured.out


class TestValidateBundlesCommand:
    @patch("sam_trader.services.cli.validate_bundles")
    def test_valid_bundles(
        self, mock_validate: Any, tmp_path: pathlib.Path, capsys: Any
    ) -> None:
        result = MagicMock()
        result.summary = "1/1 bundles passed validation"
        result.all_passed = True
        bundle = MagicMock()
        bundle.bundle_id = "tsla-orb-futu"
        bundle.passed = True
        bundle.errors = []
        bundle.warnings = []
        result.bundles = [bundle]
        mock_validate.return_value = result

        path = tmp_path / "bundles.yaml"
        path.write_text("bundles:\n  - id: test\n")

        rc = main(["validate-bundles", "--path", str(path)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "1/1 bundles passed validation" in captured.out

    @patch("sam_trader.services.cli.validate_bundles")
    def test_invalid_bundles(
        self, mock_validate: Any, tmp_path: pathlib.Path, capsys: Any
    ) -> None:
        result = MagicMock()
        result.summary = "0/1 bundles passed validation"
        result.all_passed = False
        bundle = MagicMock()
        bundle.bundle_id = "bad"
        bundle.passed = False
        bundle.errors = ["missing bar_type"]
        bundle.warnings = []
        result.bundles = [bundle]
        mock_validate.return_value = result

        path = tmp_path / "bundles.yaml"
        path.write_text("bundles:\n  - id: bad\n")

        rc = main(["validate-bundles", "--path", str(path)])
        captured = capsys.readouterr()
        assert rc == 0  # CLI returns 0 even if bundles fail; caller checks all_passed
        assert "0/1 bundles passed validation" in captured.out

    def test_missing_file_returns_error(self, capsys: Any) -> None:
        rc = main(["validate-bundles", "--path", "/nonexistent/bundles.yaml"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "not found" in captured.err


class TestRotateLogsCommand:
    @patch("sam_trader.services.cli.rotate_logs")
    def test_rotate_logs(self, mock_rotate: Any, capsys: Any) -> None:
        mock_rotate.return_value = (2, 1)
        rc = main(["rotate-logs"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "2" in captured.out
        assert "1" in captured.out

    @patch("sam_trader.services.cli.rotate_logs")
    def test_rotate_logs_json(self, mock_rotate: Any, capsys: Any) -> None:
        mock_rotate.return_value = (0, 0)
        rc = main(["--json", "rotate-logs"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "rotate-logs"
        assert data["rotated"] == 0


class TestDeployWindowCommand:
    @patch("sam_trader.services.cli.check_deploy_window")
    def test_deploy_window_active(self, mock_check: Any, capsys: Any) -> None:
        mock_check.return_value = True
        rc = main(["deploy-window"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "true" in captured.out or "active" in captured.out.lower()

    @patch("sam_trader.services.cli.check_deploy_window")
    def test_deploy_window_inactive(self, mock_check: Any, capsys: Any) -> None:
        mock_check.return_value = False
        rc = main(["deploy-window"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "False" in captured.out or "inactive" in captured.out.lower()


class TestPipelineCommand:
    @patch("sam_trader.services.cli.run_pipeline")
    def test_pipeline_placeholder(self, mock_run: Any, capsys: Any) -> None:
        rc = main(["pipeline"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "triggered" in captured.out or "placeholder" in captured.out


class TestPerformanceCommand:
    @patch("sam_trader.services.cli.asyncpg.connect")
    def test_performance_command_table(self, mock_connect: Any, capsys: Any) -> None:
        from unittest.mock import AsyncMock

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "strategy_id": "tsla-orb-futu",
                "stat_name": "SharpeRatio",
                "stat_value": 1.23,
            },
            {
                "strategy_id": "tsla-orb-futu",
                "stat_name": "WinRate",
                "stat_value": 0.55,
            },
        ]
        mock_connect.return_value = mock_conn

        rc = main(["performance"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "SharpeRatio" in captured.out
        assert "tsla-orb-futu" in captured.out
        # Verify aligned table formatting
        assert "Metric" in captured.out
        assert "Value" in captured.out
        assert "Strategy:" in captured.out

    @patch("sam_trader.services.cli.asyncpg.connect")
    def test_performance_command_json(self, mock_connect: Any, capsys: Any) -> None:
        from unittest.mock import AsyncMock

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "strategy_id": "tsla-orb-futu",
                "stat_name": "SharpeRatio",
                "stat_value": 1.23,
            },
        ]
        mock_connect.return_value = mock_conn

        rc = main(
            ["--json", "performance", "--strategy", "tsla-orb-futu", "--days", "7"]
        )
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "performance"
        assert data["days"] == 7
        assert data["strategy"] == "tsla-orb-futu"
        assert "tsla-orb-futu" in data["stats"]

    @patch("sam_trader.services.cli.asyncpg.connect")
    def test_performance_no_data(self, mock_connect: Any, capsys: Any) -> None:
        from unittest.mock import AsyncMock

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        mock_connect.return_value = mock_conn

        rc = main(["performance"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "No performance data available" in captured.out
        assert "Run nightly analysis first" in captured.out


class TestJsonGlobalFlag:
    @patch("sam_trader.services.cli._run")
    def test_json_flag_on_status(self, mock_run: Any, capsys: Any) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAMES\tSTATUS\tPORTS\nsam-trader\tUp\t\n",
        )
        rc = main(["--json", "status"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert "command" in data
        assert "containers" in data
