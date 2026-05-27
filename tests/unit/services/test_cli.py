"""Unit tests for services/cli.py."""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sam_trader.services.cli import SAM_TRADER_CONTAINER, main


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
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_restart_waits_for_state_saved(
        self, mock_subproc: Any, mock_redis_mod: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = [
            {"type": "subscribe"},
            {"type": "message", "data": '{"status": "saved"}'},
        ]
        mock_r.pubsub.return_value = mock_pubsub
        mock_r.exists.return_value = True  # sam:state_loaded key present
        mock_redis_mod.Redis.return_value = mock_r

        def _docker_side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            m = MagicMock()
            if "restart" in cmd and SAM_TRADER_CONTAINER in cmd:
                m.returncode = 0
                m.stdout = ""
            elif "inspect" in cmd and "Health.Status" in str(cmd):
                m.returncode = 0
                m.stdout = "healthy\n"
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        mock_subproc.side_effect = _docker_side_effect

        rc = main(["restart"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "success" in captured.out.lower()
        mock_r.publish.assert_any_call("sam:restart_request", "graceful")
        calls = [c[0][0] for c in mock_subproc.call_args_list]
        assert any(
            "restart" in str(c) and SAM_TRADER_CONTAINER in str(c) for c in calls
        )
        assert any("inspect" in str(c) for c in calls)

    @patch("sam_trader.services.cli.STATE_SAVE_HANDSHAKE_TIMEOUT", 0.01)
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_restart_timeout_aborts(
        self, mock_subproc: Any, mock_redis_mod: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.return_value = None
        mock_r.pubsub.return_value = mock_pubsub
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["restart"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "aborted" in captured.err.lower() or "timeout" in captured.err.lower()

        # Docker restart must NOT have been called
        calls = [c[0][0] for c in mock_subproc.call_args_list]
        assert not any(
            "restart" in str(c) and SAM_TRADER_CONTAINER in str(c) for c in calls
        )

    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_restart_force_skips_wait(
        self, mock_subproc: Any, mock_redis_mod: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        def _docker_side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            m = MagicMock()
            if "restart" in cmd and SAM_TRADER_CONTAINER in cmd:
                m.returncode = 0
                m.stdout = ""
            elif "inspect" in cmd and "Health.Status" in str(cmd):
                m.returncode = 0
                m.stdout = "healthy\n"
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        mock_subproc.side_effect = _docker_side_effect

        rc = main(["restart", "--force"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "force" in captured.out.lower() or "skipped" in captured.out.lower()

        # Publish must have been called, but pubsub subscribe must NOT
        mock_r.publish.assert_any_call("sam:restart_request", "graceful")
        mock_r.pubsub.assert_not_called()

        # Docker restart MUST have been called
        calls = [c[0][0] for c in mock_subproc.call_args_list]
        assert any(
            "restart" in str(c) and SAM_TRADER_CONTAINER in str(c) for c in calls
        )


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
    def test_pipeline_cli_runs_full_pipeline(self, mock_run: Any, capsys: Any) -> None:
        mock_run.return_value = {
            "command": "pipeline",
            "status": "success",
            "market": "US",
            "schedule": "08:30",
            "candidate_count": 2,
            "approved_count": 1,
            "rejected_count": 0,
            "bundles_generated": 1,
            "bundle_path": "/path/to/bundles.yaml",
            "regime": "NEUTRAL",
            "trace_id": "test-trace",
        }
        rc = main(["pipeline"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "success" in captured.out
        assert "US" in captured.out
        mock_run.assert_called_once()


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


class TestGapscanCommand:
    @patch("sam_trader.services.cli.build_watchlist")
    @patch("sam_trader.services.cli.load_watchlist_config")
    @patch("sam_trader.services.cli.QuoteCollectionService")
    @patch("sam_trader.services.cli.PreMarketGapScanner")
    def test_gapscan_human_output(
        self,
        mock_scanner_cls: Any,
        mock_quote_svc: Any,
        mock_load_cfg: Any,
        mock_build: Any,
        capsys: Any,
    ) -> None:
        from unittest.mock import AsyncMock

        mock_build.return_value = {"US": ["TSLA.NASDAQ"]}
        mock_load_cfg.return_value = {
            "US": MagicMock(min_gap_pct=2.0),
        }

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(
            return_value=[
                MagicMock(
                    instrument_id="TSLA.NASDAQ",
                    gap_pct=3.5,
                    quote_last=150.0,
                    trend="RISING",
                    prev_close=145.0,
                    bid=149.99,
                    ask=150.01,
                )
            ]
        )
        mock_scanner_cls.return_value = mock_scanner

        rc = main(["gapscan", "--market", "US", "--pass", "1"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "TSLA.NASDAQ" in captured.out
        assert "3.5" in captured.out or "3.50" in captured.out
        assert "RISING" in captured.out

    @patch("sam_trader.services.cli.build_watchlist")
    @patch("sam_trader.services.cli.load_watchlist_config")
    @patch("sam_trader.services.cli.QuoteCollectionService")
    @patch("sam_trader.services.cli.PreMarketGapScanner")
    def test_gapscan_json_output(
        self,
        mock_scanner_cls: Any,
        mock_quote_svc: Any,
        mock_load_cfg: Any,
        mock_build: Any,
        capsys: Any,
    ) -> None:
        from unittest.mock import AsyncMock

        mock_build.return_value = {"HK": ["00700.HKEX"]}
        mock_load_cfg.return_value = {
            "HK": MagicMock(min_gap_pct=1.5),
        }

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[])
        mock_scanner_cls.return_value = mock_scanner

        rc = main(["--json", "gapscan", "--market", "HK", "--pass", "2"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "gapscan"
        assert data["market"] == "HK"
        assert data["pass"] == 2
        assert data["candidates_found"] == 0

    @patch("sam_trader.services.cli.build_watchlist")
    @patch("sam_trader.services.cli.load_watchlist_config")
    def test_gapscan_empty_watchlist(
        self,
        mock_load_cfg: Any,
        mock_build: Any,
        capsys: Any,
    ) -> None:
        mock_build.return_value = {"US": []}
        mock_load_cfg.return_value = {}

        rc = main(["gapscan", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "No symbols" in captured.out or "No gap candidates" in captured.out

    def test_gapscan_invalid_market(self, capsys: Any) -> None:
        rc = main(["gapscan", "--market", "EU"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Unknown market" in captured.err or "ERROR" in captured.err

    def test_gapscan_invalid_pass(self, capsys: Any) -> None:
        rc = main(["gapscan", "--pass", "3"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "pass must be 1 or 2" in captured.err or "ERROR" in captured.err


class TestReadinessReportCommand:
    def test_readiness_report_simulate_human(self, capsys: Any) -> None:
        rc = main(["readiness-report", "--simulate", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "SAM Trader V3" in captured.out
        assert "TSLA.NASDAQ" in captured.out
        assert "AAPL.NASDAQ" in captured.out
        assert "STRONG_BUY" in captured.out
        assert "Bundle Generation" in captured.out
        assert "Market Regime" in captured.out

    def test_readiness_report_simulate_json(self, capsys: Any) -> None:
        rc = main(["--json", "readiness-report", "--simulate", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "readiness-report"
        assert data["market"] == "US"
        assert data["approved_count"] == 2
        assert data["bundles_generated"] == 2

    def test_readiness_report_simulate_no_save(
        self, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        rc = main(["readiness-report", "--simulate", "--no-save", "--market", "US"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "SAM Trader V3" in out

    def test_readiness_report_invalid_market(self, capsys: Any) -> None:
        rc = main(["readiness-report", "--simulate", "--market", "EU"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Unknown market" in captured.err or "ERROR" in captured.err

    @patch("sam_trader.services.cli.build_watchlist")
    @patch("sam_trader.services.cli.load_watchlist_config")
    def test_readiness_report_empty_watchlist(
        self,
        mock_load_cfg: Any,
        mock_build: Any,
        capsys: Any,
    ) -> None:
        mock_build.return_value = {"US": []}
        mock_load_cfg.return_value = {}

        rc = main(["readiness-report", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "No symbols" in captured.out

    @patch("sam_trader.services.cli.ReadinessReportGenerator.send_webhook")
    @patch("sam_trader.services.cli.ReadinessReportGenerator.save_audit")
    def test_readiness_report_webhook_and_save(
        self,
        mock_save: Any,
        mock_webhook: Any,
        capsys: Any,
    ) -> None:
        mock_webhook.return_value = True
        mock_save.return_value = "/tmp/readiness/2026-05-24.json"

        rc = main(
            [
                "readiness-report",
                "--simulate",
                "--webhook-url",
                "https://hooks.slack.com/test",
                "--market",
                "US",
            ]
        )
        assert rc == 0
        mock_webhook.assert_called_once()
        mock_save.assert_called_once()

    @patch("sam_trader.services.cli.ReadinessReportGenerator.send_webhook")
    @patch("sam_trader.services.cli.ReadinessReportGenerator.save_audit")
    def test_readiness_report_no_save_flag(
        self,
        mock_save: Any,
        mock_webhook: Any,
        capsys: Any,
    ) -> None:
        rc = main(["readiness-report", "--simulate", "--no-save", "--market", "US"])
        assert rc == 0
        mock_save.assert_not_called()


class TestReadinessCommand:
    @patch("sam_trader.services.cli._redis_cli")
    def test_readiness_all_pass(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(
            {
                "market": "US",
                "date": "2026-05-27",
                "overall": "PASS",
                "checks": [
                    {
                        "name": "broker_connectivity",
                        "result": "PASS",
                        "detail": "all expected brokers connected",
                    },
                    {
                        "name": "quote_flow",
                        "result": "PASS",
                        "detail": "all 2 instruments fresh",
                    },
                    {
                        "name": "instruments_resolved",
                        "result": "PASS",
                        "detail": "all 2 instruments resolved",
                    },
                ],
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["readiness", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "SOD Readiness [US]" in captured.out
        assert "PASS" in captured.out
        assert "broker_connectivity" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_readiness_some_fail(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(
            {
                "market": "US",
                "date": "2026-05-27",
                "overall": "FAIL",
                "checks": [
                    {"name": "broker_connectivity", "result": "PASS", "detail": "ok"},
                    {"name": "quote_flow", "result": "FAIL", "detail": "stale"},
                ],
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["readiness", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "FAIL" in captured.out
        assert "stale" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_readiness_json(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(
            {
                "market": "HK",
                "date": "2026-05-27",
                "overall": "PASS",
                "checks": [
                    {
                        "name": "calendar_trading_day",
                        "result": "PASS",
                        "detail": "trading day",
                    },
                ],
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["--json", "readiness", "--market", "HK"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "readiness"
        assert data["market"] == "HK"
        assert data["overall"] == "PASS"
        assert data["exit_code"] == 0
        assert len(data["checks"]) == 1

    @patch("sam_trader.services.cli._redis_cli")
    def test_readiness_not_found(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["readiness", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "not yet run" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_readiness_not_found_json(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["--json", "readiness", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["status"] == "NOT_FOUND"
        assert data["market"] == "US"

    def test_readiness_invalid_market(self, capsys: Any) -> None:
        rc = main(["readiness", "--market", "EU"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Unknown market" in captured.err or "ERROR" in captured.err

    @patch("sam_trader.services.cli._redis_cli")
    def test_readiness_corrupt_data(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = "not-json{{"
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["readiness", "--market", "US"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Corrupt" in captured.err or "ERROR" in captured.err


class TestPreflightCommand:
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli.hashlib.sha256")
    def test_preflight_all_clear(
        self,
        mock_sha256: Any,
        mock_redis_mod: Any,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = True
        result.summary = "2/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")

        mock_sha256.return_value.hexdigest.return_value = "a" * 64
        mock_redis_client = MagicMock()
        mock_redis_client.get.return_value = "a" * 64
        mock_redis_mod.Redis.return_value = mock_redis_client

        # Point DEFAULT_BUNDLES_PATH to a real file
        from sam_trader.services.cli import DEFAULT_BUNDLES_PATH

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch.object(type(DEFAULT_BUNDLES_PATH), "exists", return_value=True):
            with patch(
                "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
                bundles_file,
            ):
                rc = main(["preflight"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out
        assert "exit_code" in captured.out or "exit" in captured.out.lower()

    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_preflight_outside_window(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_window.return_value = False
        result = MagicMock()
        result.all_passed = True
        result.summary = "2/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch(
            "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
            bundles_file,
        ):
            rc = main(["preflight"])

        captured = capsys.readouterr()
        assert rc == 2
        assert "FAIL" in captured.out
        assert "deploy_window" in captured.out.lower()

    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_preflight_invalid_bundles(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = False
        result.summary = "0/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: bad\n")
        with patch(
            "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
            bundles_file,
        ):
            rc = main(["preflight"])

        captured = capsys.readouterr()
        assert rc == 2
        assert "FAIL" in captured.out
        assert "bundles_valid" in captured.out.lower()


class TestSnapshotCommand:
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run")
    def test_snapshot_creates_redis_key(
        self, mock_run: Any, mock_redis_mod: Any, capsys: Any
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="abc1234\n")

        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = pathlib.Path("config/bundles.yaml")
        # Ensure bundles.yaml exists for the test
        bundles_file.parent.mkdir(parents=True, exist_ok=True)
        original_text = bundles_file.read_text() if bundles_file.exists() else ""
        bundles_file.write_text("bundles:\n  - id: test-bundle\n    enabled: true\n")

        try:
            rc = main(["snapshot"])
        finally:
            if original_text:
                bundles_file.write_text(original_text)
            else:
                bundles_file.write_text("bundles: []\n")

        captured = capsys.readouterr()
        assert rc == 0
        assert "created" in captured.out
        assert "abc1234" in captured.out
        assert "test-bundle" in captured.out
        mock_r.set.assert_called_once()
        call_args = mock_r.set.call_args
        assert call_args[0][0].startswith("sam:snapshot:")
        assert "ex" in call_args[1]
        assert call_args[1]["ex"] == 30 * 24 * 60 * 60

    @patch("sam_trader.services.cli._redis_cli")
    def test_snapshot_list_shows_entries(
        self, mock_redis_mod: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_r.keys.return_value = [
            "sam:snapshot:2026-05-24T10:00:00+00:00",
            "sam:snapshot:2026-05-24T09:00:00+00:00",
        ]
        mock_r.get.side_effect = [
            json.dumps(
                {
                    "git_hash": "abc1234",
                    "bundles_hash": "sha256_a",
                    "timestamp": "2026-05-24T10:00:00+00:00",
                    "active_strategies": ["bundle-a"],
                }
            ),
            json.dumps(
                {
                    "git_hash": "def5678",
                    "bundles_hash": "sha256_b",
                    "timestamp": "2026-05-24T09:00:00+00:00",
                    "active_strategies": ["bundle-b"],
                }
            ),
        ]
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["snapshot", "--list"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "abc1234" in captured.out
        assert "def5678" in captured.out
        assert "2026-05-24T10:00:00+00:00" in captured.out
        mock_r.keys.assert_called_once_with("sam:snapshot:*")

    @patch("sam_trader.services.cli._redis_cli")
    def test_snapshot_show_details(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.keys.return_value = [
            "sam:snapshot:2026-05-24T10:00:00+00:00",
            "sam:snapshot:2026-05-24T09:00:00+00:00",
        ]
        mock_r.get.return_value = json.dumps(
            {
                "git_hash": "def5678",
                "bundles_hash": "sha256_b",
                "timestamp": "2026-05-24T09:00:00+00:00",
                "active_strategies": ["bundle-b", "bundle-c"],
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["snapshot", "--show", "2"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "def5678" in captured.out
        assert "sha256_b" in captured.out
        assert "bundle-b" in captured.out
        assert "bundle-c" in captured.out
        assert "2026-05-24T09:00:00+00:00" in captured.out


class TestBundleDiffCommand:
    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_added(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        """Bundle in current but not in snapshot → ADDED."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-24T10:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "old-bundle": {"id": "old-bundle", "enabled": True, "venue": "FUTU"}
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: new-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "ADDED" in captured.out
        assert "new-bundle" in captured.out
        assert "old-bundle" not in captured.out or "REMOVED" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_removed(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        """Bundle in snapshot but not in current → REMOVED."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-24T10:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "gone-bundle": {
                        "id": "gone-bundle",
                        "enabled": True,
                        "venue": "FUTU",
                    }
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles: []\n")
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "REMOVED" in captured.out
        assert "gone-bundle" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_modified(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        """Same ID, different config → MODIFIED with changed keys."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-24T10:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "tsla-orb": {
                        "id": "tsla-orb",
                        "enabled": True,
                        "venue": "FUTU",
                        "strategy": {"config": {"trade_size": 5}},
                    }
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: tsla-orb\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            "    strategy:\n"
            "      config:\n"
            "        trade_size: 10\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "MODIFIED" in captured.out
        assert "tsla-orb" in captured.out
        assert "strategy" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_version_bump(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        """Version field changed → VERSION BUMPS."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-24T10:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "orb-aggressive": {
                        "id": "orb-aggressive",
                        "enabled": True,
                        "venue": "FUTU",
                        "family": "ORB_aggressive",
                        "version": "1.0.0",
                        "variant": "aggressive",
                    }
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: orb-aggressive\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            "    family: ORB_aggressive\n"
            '    version: "1.1.0"\n'
            "    variant: aggressive\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "VERSION BUMPS" in captured.out
        assert "orb-aggressive" in captured.out
        assert "1.0.0" in captured.out
        assert "1.1.0" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_no_snapshot(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        """No snapshot in Redis → all bundles shown as NEW."""
        mock_r = MagicMock()
        mock_r.keys.return_value = []
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: first-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "NEW" in captured.out or "new" in captured.out
        assert "first-bundle" in captured.out
        assert (
            "no snapshot" in captured.out.lower() or "first-run" in captured.out.lower()
        )

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_json(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        """--json flag emits structured diff output."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-24T10:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "existing": {
                        "id": "existing",
                        "enabled": True,
                        "venue": "FUTU",
                        "version": "1.0.0",
                    }
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: existing\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            '    version: "1.1.0"\n'
            "  - id: new-one\n"
            "    enabled: true\n"
            "    venue: IB\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["--json", "bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "bundle-diff"
        assert "new-one" in data["added"]
        assert data["version_bumps"]
        assert data["version_bumps"][0]["id"] == "existing"


class TestApplyCommand:
    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_dry_run(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        mock_redis_mod: Any,
        mock_restart: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """--dry-run stops after preflight; snapshot/restart never called."""
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = True
        result.summary = "2/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")

        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch(
            "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
            bundles_file,
        ):
            rc = main(["apply", "--dry-run"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "dry-run" in captured.out.lower() or "PASS" in captured.out
        mock_restart.assert_not_called()
        mock_r.set.assert_not_called()

    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_full_flow(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        mock_redis_mod: Any,
        mock_restart: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Full pipeline: preflight → snapshot → restart → verify."""
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = True
        result.summary = "2/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        mock_restart.return_value = {
            "status": "success",
            "detail": "Graceful restart completed",
        }

        mock_r = MagicMock()
        mock_r.exists.return_value = True
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch(
            "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
            bundles_file,
        ):
            rc = main(["apply"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out
        mock_restart.assert_called_once()
        mock_r.set.assert_called_once()

    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_preflight_blocks(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        mock_redis_mod: Any,
        mock_restart: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Blocking preflight issue aborts before snapshot/restart."""
        mock_window.return_value = False  # outside deploy window
        result = MagicMock()
        result.all_passed = True
        result.summary = "2/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")

        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch(
            "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
            bundles_file,
        ):
            rc = main(["apply"])

        captured = capsys.readouterr()
        assert rc == 1
        assert "ABORTED" in captured.out or "blocked" in captured.err.lower()
        mock_restart.assert_not_called()
        mock_r.set.assert_not_called()

    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_restart_failure(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        mock_redis_mod: Any,
        mock_restart: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Restart failure aborts pipeline after snapshot."""
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = True
        result.summary = "2/2 bundles passed validation"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "UP"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        mock_restart.return_value = {
            "status": "error",
            "detail": "Docker restart failed",
        }

        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch(
            "sam_trader.services.cli.DEFAULT_BUNDLES_PATH",
            bundles_file,
        ):
            rc = main(["apply"])

        captured = capsys.readouterr()
        assert rc == 1
        assert "Restart failed" in captured.err or "error" in captured.err.lower()
        mock_restart.assert_called_once()


class TestDataHealthCommand:
    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_all_ok(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        now = datetime.now(timezone.utc).isoformat()
        mock_r.get.side_effect = lambda key: {
            "sam:bars:last:TSLA.NASDAQ": now,
            "sam:venue:conn:FUTU": f"UP:{now}",
        }.get(key)
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["data-health", "--instrument", "TSLA.NASDAQ", "--venue", "FUTU"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "HEALTHY" in captured.out or "OK" in captured.out
        assert "TSLA.NASDAQ" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_stale(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        mock_r.get.side_effect = lambda key: {
            "sam:bars:last:TSLA.NASDAQ": stale,
            "sam:venue:conn:FUTU": f"UP:{stale}",
        }.get(key)
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["data-health", "--instrument", "TSLA.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "STALE" in captured.out or "FAIL" in captured.out
        assert "TSLA.NASDAQ" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_missing(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["data-health", "--instrument", "AAPL.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "MISSING" in captured.out or "FAIL" in captured.out
        assert "probe-bars" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_json(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        now = datetime.now(timezone.utc).isoformat()
        mock_r.get.side_effect = lambda key: {
            "sam:bars:last:TSLA.NASDAQ": now,
            "sam:venue:conn:FUTU": f"UP:{now}",
        }.get(key)
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["--json", "data-health", "--instrument", "TSLA.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "data-health"
        assert data["overall"] == "HEALTHY"
        assert data["instruments_checked"] == 1
        assert data["reports"][0]["status"] == "OK"

    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_venue_filter_from_bundles(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        mock_r = MagicMock()
        now = datetime.now(timezone.utc).isoformat()
        mock_r.get.side_effect = lambda key: {
            "sam:bars:last:TSLA.NASDAQ": now,
            "sam:venue:conn:FUTU": f"UP:{now}",
        }.get(key)
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: tsla-orb\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            "    strategy:\n"
            "      path: sam_trader.strategies.orb:OrbStrategy\n"
            "      config:\n"
            "        instrument_id: TSLA.NASDAQ\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["data-health", "--venue", "FUTU"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "TSLA.NASDAQ" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_no_bundles_for_venue(
        self, mock_redis_mod: Any, capsys: Any, tmp_path: pathlib.Path
    ) -> None:
        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: tsla-orb\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            "    strategy:\n"
            "      path: sam_trader.strategies.orb:OrbStrategy\n"
            "      config:\n"
            "        instrument_id: TSLA.NASDAQ\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["data-health", "--venue", "IB"])

        captured = capsys.readouterr()
        assert rc == 1
        assert "No active bundles found" in captured.err

    @patch("sam_trader.services.cli._redis_cli")
    def test_data_health_redis_unavailable(
        self, mock_redis_mod: Any, capsys: Any
    ) -> None:
        mock_redis_mod.Redis.side_effect = Exception("Connection refused")

        rc = main(["data-health", "--instrument", "TSLA.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Redis connection failed" in captured.err


class TestProbeCommand:
    @patch("sam_trader.services.cli.QuoteCollectionService")
    def test_probe_quotes_pass(self, mock_svc_cls: Any, capsys: Any) -> None:
        """Probe reports PASS when quotes are received."""
        mock_result = MagicMock()
        mock_result.quotes = {"TSLA.NASDAQ": MagicMock()}
        mock_result.bars = {}
        mock_result.elapsed_secs = 2.5
        mock_result.partial_failures = []
        mock_svc = MagicMock()
        mock_svc.collect = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        rc = main(
            [
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--type",
                "quotes",
                "--duration",
                "5",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out
        assert "received 1 quotes" in captured.out

    @patch("sam_trader.services.cli.QuoteCollectionService")
    def test_probe_bars_pass(self, mock_svc_cls: Any, capsys: Any) -> None:
        """Probe reports PASS when bars are received."""
        mock_result = MagicMock()
        mock_result.quotes = {}
        mock_result.bars = {"TSLA.NASDAQ": MagicMock()}
        mock_result.elapsed_secs = 3.0
        mock_result.partial_failures = []
        mock_svc = MagicMock()
        mock_svc.collect = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        rc = main(
            [
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--type",
                "bars",
                "--duration",
                "5",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out
        assert "received 1 bars" in captured.out

    @patch("sam_trader.services.cli.QuoteCollectionService")
    def test_probe_fail_no_data(self, mock_svc_cls: Any, capsys: Any) -> None:
        """Probe reports FAIL when no data is received."""
        mock_result = MagicMock()
        mock_result.quotes = {}
        mock_result.bars = {}
        mock_result.elapsed_secs = 5.0
        mock_result.partial_failures = []
        mock_svc = MagicMock()
        mock_svc.collect = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        rc = main(
            [
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--type",
                "quotes",
                "--duration",
                "5",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "FAIL" in captured.out
        assert "received 0 quotes" in captured.out

    @patch("sam_trader.services.cli.QuoteCollectionService")
    def test_probe_json_output(self, mock_svc_cls: Any, capsys: Any) -> None:
        """Probe with --json emits structured output."""
        mock_result = MagicMock()
        mock_result.quotes = {"TSLA.NASDAQ": MagicMock()}
        mock_result.bars = {}
        mock_result.elapsed_secs = 1.2
        mock_result.partial_failures = []
        mock_svc = MagicMock()
        mock_svc.collect = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        rc = main(
            [
                "--json",
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--type",
                "quotes",
                "--duration",
                "5",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "probe"
        assert data["status"] == "PASS"
        assert data["received"] == 1

    @patch("sam_trader.services.cli.QuoteCollectionService")
    def test_probe_connection_error(self, mock_svc_cls: Any, capsys: Any) -> None:
        """Probe reports FAIL on ConnectionError."""
        mock_svc = MagicMock()
        mock_svc.collect = AsyncMock(side_effect=ConnectionError("timed out"))
        mock_svc_cls.return_value = mock_svc

        rc = main(
            [
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--duration",
                "5",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "FAIL" in captured.out
        assert "could not connect" in captured.out

    def test_probe_unsupported_broker(self, capsys: Any) -> None:
        """Probe rejects unsupported broker."""
        rc = main(
            [
                "probe",
                "--broker",
                "UNKNOWN",
                "--instrument",
                "TSLA.NASDAQ",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "Unsupported broker" in captured.err

    def test_probe_unsupported_data_type(self, capsys: Any) -> None:
        """Probe rejects unsupported data type."""
        rc = main(
            [
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--type",
                "trades",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "Unsupported data type" in captured.err

    @patch("sam_trader.services.cli.QuoteCollectionService")
    def test_probe_bar_type_passed(self, mock_svc_cls: Any, capsys: Any) -> None:
        """Probe passes bar_type_str to the service when --bar-type is given."""
        mock_result = MagicMock()
        mock_result.quotes = {}
        mock_result.bars = {"TSLA.NASDAQ": MagicMock()}
        mock_result.elapsed_secs = 2.0
        mock_result.partial_failures = []
        mock_svc = MagicMock()
        mock_svc.collect = AsyncMock(return_value=mock_result)
        mock_svc_cls.return_value = mock_svc

        rc = main(
            [
                "probe",
                "--broker",
                "FUTU",
                "--instrument",
                "TSLA.NASDAQ",
                "--type",
                "bars",
                "--bar-type",
                "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "--duration",
                "5",
            ]
        )
        assert rc == 0
        call_kwargs = mock_svc_cls.call_args.kwargs
        assert call_kwargs["bar_type_str"] == "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"


class TestFlushCacheCommand:
    @patch("sam_trader.services.cli._redis_cli")
    def test_flush_cache_force(self, mock_redis_mod: Any, capsys: Any) -> None:
        """Flush cache with --force deletes all keys."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_r.dbsize.return_value = 42
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["flush-cache", "--force"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "flushed" in captured.out
        assert "42" in captured.out
        mock_r.flushdb.assert_called_once()

    @patch("sam_trader.services.cli._redis_cli")
    def test_flush_cache_no_force_aborts(
        self, mock_redis_mod: Any, capsys: Any
    ) -> None:
        """Flush cache without --force aborts in non-interactive mode."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["flush-cache"])
        # click.confirm with abort=True exits non-zero in non-interactive mode
        assert rc != 0
        mock_r.flushdb.assert_not_called()

    @patch("sam_trader.services.cli._redis_cli")
    def test_flush_cache_redis_unavailable(
        self, mock_redis_mod: Any, capsys: Any
    ) -> None:
        """Flush cache reports error when Redis is unreachable."""
        mock_redis_mod.Redis.side_effect = Exception("Connection refused")

        rc = main(["flush-cache", "--force"])
        captured = capsys.readouterr()
        assert rc != 0
        assert "Redis connection failed" in captured.err


class TestSwitchMarketCommand:
    @patch("sam_trader.services.cli._redis_cli")
    def test_switch_market_us(self, mock_redis_mod: Any, capsys: Any) -> None:
        """switch-market US publishes request and waits for completion."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = [
            None,  # sub confirm 1
            None,  # sub confirm 2
            {
                "type": "message",
                "channel": "sam:market_switch_complete",
                "data": '{"market": "US", "status": "completed"}',
            },
        ]
        mock_r.pubsub.return_value = mock_pubsub
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["switch-market", "US"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "completed" in captured.out
        assert mock_r.publish.call_count == 1
        call_args = mock_r.publish.call_args[0]
        assert call_args[0] == "sam:market_switch_request"
        payload = json.loads(call_args[1])
        assert payload["market"] == "US"

    @patch("sam_trader.services.cli._redis_cli")
    def test_switch_market_hk(self, mock_redis_mod: Any, capsys: Any) -> None:
        """switch-market HK publishes request and waits for completion."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = [
            None,
            None,
            {
                "type": "message",
                "channel": "sam:market_switch_complete",
                "data": '{"market": "HK", "status": "completed"}',
            },
        ]
        mock_r.pubsub.return_value = mock_pubsub
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["switch-market", "HK"])
        assert rc == 0

    @patch("sam_trader.services.cli._redis_cli")
    def test_switch_market_failure(self, mock_redis_mod: Any, capsys: Any) -> None:
        """switch-market exits non-zero when orchestrator reports failure."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = [
            None,
            None,
            {
                "type": "message",
                "channel": "sam:market_switch_failed",
                "data": '{"reason": "state-save timeout"}',
            },
        ]
        mock_r.pubsub.return_value = mock_pubsub
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["switch-market", "US"])
        assert rc != 0

    def test_switch_market_invalid(self, capsys: Any) -> None:
        """switch-market with invalid market exits non-zero."""
        rc = main(["switch-market", "EU"])
        assert rc != 0
        assert "Market must be US or HK" in capsys.readouterr().err

    def test_switch_market_no_redis(self, capsys: Any) -> None:
        """switch-market exits non-zero when redis is unavailable."""
        with patch("sam_trader.services.cli._redis_cli", None):
            rc = main(["switch-market", "US"])
            assert rc != 0
            assert "redis package not available" in capsys.readouterr().err

    @patch("sam_trader.services.cli._redis_cli")
    def test_switch_market_timeout(self, mock_redis_mod: Any, capsys: Any) -> None:
        """switch-market exits non-zero when orchestrator does not respond."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.return_value = None
        mock_r.pubsub.return_value = mock_pubsub
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["switch-market", "US", "--timeout", "1"])
        assert rc != 0
        err = capsys.readouterr().err.lower()
        assert "no response" in err or "timeout" in err


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
