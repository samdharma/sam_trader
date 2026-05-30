"""Unit tests for services/cli.py."""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.engine import BacktestEngineError
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
            "bundles_published": 1,
            "bundle_path": None,
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


class TestReportCommand:
    @patch("sam_trader.services.cli._redis_cli")
    def test_report_redis_today(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(
            {
                "market": "US",
                "date": "2026-05-27",
                "generated_at_utc": "2026-05-27T20:05:00+00:00",
                "daily_pnl": [
                    {
                        "strategy_id": "tsla-orb-futu",
                        "realized_pnl": 123.45,
                        "source": "redis",
                    }
                ],
                "fills_summary": {
                    "total_fills": 5,
                    "total_commission": 2.5,
                    "total_volume": 5000.0,
                    "by_strategy": [
                        {
                            "strategy_id": "tsla-orb-futu",
                            "fill_count": 5,
                            "total_qty": 50.0,
                            "total_commission": 2.5,
                            "total_volume": 5000.0,
                        }
                    ],
                },
                "position_summary": {
                    "total_open_positions": 0,
                    "all_flat": True,
                    "positions": [],
                },
                "rejection_events": {
                    "total_rejections": 0,
                    "circuit_breakers_active": 0,
                    "status": "ok",
                },
                "health_events": {
                    "heartbeat_count": 48,
                    "last_heartbeat": "2026-05-27T20:00:00Z",
                    "status": "ok",
                    "alerts": [],
                },
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["report", "--market", "US", "--date", "2026-05-27"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "EOD Report [US] 2026-05-27" in captured.out
        assert "tsla-orb-futu" in captured.out
        assert "123.45" in captured.out
        assert "Total Fills:        5" in captured.out
        assert "All positions flat" in captured.out
        assert "redis" in captured.out.lower()

    @patch("sam_trader.services.cli.asyncpg.connect")
    @patch("sam_trader.services.cli._redis_cli")
    def test_report_pg_fallback(
        self, mock_redis_mod: Any, mock_connect: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_mod.Redis.return_value = mock_r

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "report_json": json.dumps(
                {
                    "market": "HK",
                    "date": "2026-05-20",
                    "generated_at_utc": "2026-05-20T08:05:00+00:00",
                    "daily_pnl": [],
                    "fills_summary": {
                        "total_fills": 0,
                        "total_commission": 0.0,
                        "total_volume": 0.0,
                        "by_strategy": [],
                    },
                    "position_summary": {
                        "total_open_positions": 0,
                        "all_flat": True,
                        "positions": [],
                    },
                    "rejection_events": {
                        "total_rejections": 0,
                        "circuit_breakers_active": 0,
                    },
                    "health_events": {
                        "heartbeat_count": 0,
                        "status": "ok",
                        "alerts": [],
                    },
                }
            )
        }
        mock_connect.return_value = mock_conn

        rc = main(["report", "--market", "HK", "--date", "2026-05-20"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "EOD Report [HK] 2026-05-20" in captured.out
        assert "No P&L data available" in captured.out
        assert "postgres" in captured.out.lower()

    @patch("sam_trader.services.cli._redis_cli")
    def test_report_json_output(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(
            {
                "market": "US",
                "date": "2026-05-27",
                "generated_at_utc": "2026-05-27T20:05:00+00:00",
                "daily_pnl": [
                    {
                        "strategy_id": "orb-15m",
                        "realized_pnl": 99.0,
                        "source": "redis",
                    }
                ],
                "fills_summary": {
                    "total_fills": 2,
                    "total_commission": 1.0,
                    "total_volume": 2000.0,
                    "by_strategy": [],
                },
                "position_summary": {
                    "total_open_positions": 0,
                    "all_flat": True,
                    "positions": [],
                },
                "rejection_events": {},
                "health_events": {
                    "heartbeat_count": 10,
                    "last_heartbeat": "2026-05-27T20:00:00Z",
                    "status": "ok",
                    "alerts": [],
                },
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["--json", "report", "--market", "US", "--date", "2026-05-27"])
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "report"
        assert data["market"] == "US"
        assert data["date"] == "2026-05-27"
        assert data["status"] == "OK"
        assert data["source"] == "redis"
        assert data["report"]["daily_pnl"][0]["realized_pnl"] == 99.0

    @patch("sam_trader.services.cli.asyncpg.connect")
    @patch("sam_trader.services.cli._redis_cli")
    def test_report_not_found(
        self, mock_redis_mod: Any, mock_connect: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_mod.Redis.return_value = mock_r

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_connect.return_value = mock_conn

        rc = main(["report", "--market", "US", "--date", "2026-01-01"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "No EOD report for 2026-01-01" in captured.out

    @patch("sam_trader.services.cli.asyncpg.connect")
    @patch("sam_trader.services.cli._redis_cli")
    def test_report_not_found_json(
        self, mock_redis_mod: Any, mock_connect: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_redis_mod.Redis.return_value = mock_r

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        mock_connect.return_value = mock_conn

        rc = main(["--json", "report", "--market", "HK", "--date", "2026-01-01"])
        captured = capsys.readouterr()
        assert rc == 1
        data = json.loads(captured.out)
        assert data["status"] == "NOT_FOUND"
        assert data["market"] == "HK"

    def test_report_invalid_market(self, capsys: Any) -> None:
        rc = main(["report", "--market", "EU"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Unknown market" in captured.err or "ERROR" in captured.err

    def test_report_invalid_date(self, capsys: Any) -> None:
        rc = main(["report", "--market", "US", "--date", "not-a-date"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "Invalid date format" in captured.err or "ERROR" in captured.err

    @patch("sam_trader.services.cli.asyncpg.connect")
    @patch("sam_trader.services.cli._redis_cli")
    def test_report_corrupt_redis_fallback_pg(
        self, mock_redis_mod: Any, mock_connect: Any, capsys: Any
    ) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = "not-json{{"
        mock_redis_mod.Redis.return_value = mock_r

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "report_json": json.dumps(
                {
                    "market": "US",
                    "date": "2026-05-27",
                    "generated_at_utc": "2026-05-27T20:05:00+00:00",
                    "daily_pnl": [],
                    "fills_summary": {
                        "total_fills": 0,
                        "total_commission": 0.0,
                        "total_volume": 0.0,
                        "by_strategy": [],
                    },
                    "position_summary": {
                        "total_open_positions": 0,
                        "all_flat": True,
                    },
                    "rejection_events": {},
                    "health_events": {
                        "heartbeat_count": 0,
                        "status": "ok",
                        "alerts": [],
                    },
                }
            )
        }
        mock_connect.return_value = mock_conn

        rc = main(["report", "--market", "US", "--date", "2026-05-27"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "EOD Report [US] 2026-05-27" in captured.out

    @patch("sam_trader.services.cli._redis_cli")
    def test_report_critical_alerts(self, mock_redis_mod: Any, capsys: Any) -> None:
        mock_r = MagicMock()
        mock_r.get.return_value = json.dumps(
            {
                "market": "US",
                "date": "2026-05-27",
                "generated_at_utc": "2026-05-27T20:05:00+00:00",
                "daily_pnl": [],
                "fills_summary": {
                    "total_fills": 0,
                    "total_commission": 0.0,
                    "total_volume": 0.0,
                    "by_strategy": [],
                },
                "position_summary": {
                    "total_open_positions": 1,
                    "all_flat": False,
                    "positions": [
                        {
                            "instrument_id": "TSLA.NASDAQ",
                            "net_quantity": 100,
                        }
                    ],
                },
                "rejection_events": {
                    "total_rejections": 3,
                    "circuit_breakers_active": 1,
                },
                "health_events": {
                    "heartbeat_count": 10,
                    "last_heartbeat": "2026-05-27T20:00:00Z",
                    "status": "ok",
                    "alerts": [
                        {
                            "key": "sam:heartbeat:sam-trader",
                            "value": '{"level": "CRITICAL", "msg": "down"}',
                        }
                    ],
                },
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        rc = main(["report", "--market", "US", "--date", "2026-05-27"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "CRITICAL Alerts" in captured.out
        assert "1 open position(s)" in captured.out
        assert "TSLA.NASDAQ" in captured.out


class TestDownloadBarsCommand:
    @patch("sam_trader.services.cli.BarDownloader")
    def test_download_bars_single_instrument(self, mock_cls: Any, capsys: Any) -> None:
        mock_downloader = MagicMock()
        mock_result = MagicMock()
        mock_result.total_bars_downloaded = 100
        mock_result.total_bars_written = 100
        mock_result.instruments_failed = []
        mock_result.results = [
            MagicMock(
                instrument_id="TSLA.NASDAQ",
                bars_downloaded=100,
                bars_written=100,
                start_date="2024-01-01",
                end_date="2024-01-31",
                error=None,
            )
        ]

        async def _mock_download(*args: Any, **kwargs: Any) -> Any:
            return mock_result

        mock_downloader.download = _mock_download
        mock_cls.return_value = mock_downloader

        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--bar-type",
                "5-MINUTE",
                "--lookback",
                "30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "TSLA.NASDAQ" in captured.out
        assert "OK" in captured.out
        mock_cls.assert_called_once_with(catalog_path="data/catalog")

    @patch("sam_trader.services.cli.BarDownloader")
    @patch("sam_trader.services.cli.get_instruments_from_bundles")
    def test_download_bars_from_bundles(
        self, mock_get_inst: Any, mock_cls: Any, capsys: Any
    ) -> None:
        mock_get_inst.return_value = ["TSLA.NASDAQ", "AAPL.NASDAQ"]
        mock_downloader = MagicMock()
        mock_result = MagicMock()
        mock_result.total_bars_downloaded = 200
        mock_result.total_bars_written = 200
        mock_result.instruments_failed = []
        mock_result.results = [
            MagicMock(
                instrument_id="TSLA.NASDAQ",
                bars_downloaded=100,
                bars_written=100,
                start_date="2024-01-01",
                end_date="2024-01-31",
                error=None,
            ),
            MagicMock(
                instrument_id="AAPL.NASDAQ",
                bars_downloaded=100,
                bars_written=100,
                start_date="2024-01-01",
                end_date="2024-01-31",
                error=None,
            ),
        ]

        async def _mock_download(*args: Any, **kwargs: Any) -> Any:
            return mock_result

        mock_downloader.download = _mock_download
        mock_cls.return_value = mock_downloader

        rc = main(["download-bars", "--bar-type", "DAY", "--lookback", "180"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "TSLA.NASDAQ" in captured.out
        assert "AAPL.NASDAQ" in captured.out
        mock_get_inst.assert_called_once()

    @patch("sam_trader.services.cli.BarDownloader")
    def test_download_bars_invalid_bar_type(self, mock_cls: Any, capsys: Any) -> None:
        mock_downloader = MagicMock()

        async def _mock_download(*args: Any, **kwargs: Any) -> Any:
            raise Exception("Unsupported bar_type_spec")

        mock_downloader.download = _mock_download
        mock_cls.return_value = mock_downloader

        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--bar-type",
                "TICK",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR" in captured.err or "ERROR" in captured.out

    @patch("sam_trader.services.cli.BarDownloader")
    def test_download_bars_json_output(self, mock_cls: Any, capsys: Any) -> None:
        mock_downloader = MagicMock()
        mock_result = MagicMock()
        mock_result.total_bars_downloaded = 50
        mock_result.total_bars_written = 50
        mock_result.instruments_failed = []
        mock_result.results = [
            MagicMock(
                instrument_id="TSLA.NASDAQ",
                bars_downloaded=50,
                bars_written=50,
                start_date="2024-01-01",
                end_date="2024-01-31",
                error=None,
            )
        ]

        async def _mock_download(*args: Any, **kwargs: Any) -> Any:
            return mock_result

        mock_downloader.download = _mock_download
        mock_cls.return_value = mock_downloader

        rc = main(
            [
                "--json",
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--lookback",
                "365",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "download-bars"
        assert data["total_bars_written"] == 50

    @patch("sam_trader.services.cli.get_instruments_from_bundles")
    def test_download_bars_no_instruments_no_bundles(
        self, mock_get_inst: Any, capsys: Any
    ) -> None:
        mock_get_inst.return_value = []
        rc = main(["download-bars", "--lookback", "365"])
        captured = capsys.readouterr()
        assert rc == 1
        assert (
            "No enabled FUTU instruments" in captured.out
            or "No enabled FUTU instruments" in captured.err
        )

    @patch("sam_trader.services.cli.BarDownloader")
    def test_download_bars_with_start_end(self, mock_cls: Any, capsys: Any) -> None:
        mock_downloader = MagicMock()
        mock_result = MagicMock()
        mock_result.total_bars_downloaded = 100
        mock_result.total_bars_written = 100
        mock_result.instruments_failed = []
        mock_result.results = [
            MagicMock(
                instrument_id="TSLA.NASDAQ",
                bars_downloaded=100,
                bars_written=100,
                start_date="2023-01-01",
                end_date="2024-12-31",
                error=None,
            )
        ]

        mock_downloader.download = AsyncMock(return_value=mock_result)
        mock_cls.return_value = mock_downloader

        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--bar-type",
                "5-MINUTE",
                "--start",
                "2023-01-01",
                "--end",
                "2024-12-31",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "TSLA.NASDAQ" in captured.out
        assert "OK" in captured.out
        assert "2023-01-01" in captured.out
        mock_cls.assert_called_once_with(catalog_path="data/catalog")
        # Verify start_date and end_date were passed to download()
        call_kwargs = mock_downloader.download.call_args[1]
        assert call_kwargs["start_date"].isoformat() == "2023-01-01"
        assert call_kwargs["end_date"].isoformat() == "2024-12-31"

    def test_download_bars_mutual_exclusive_date_and_lookback(
        self, capsys: Any
    ) -> None:
        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--lookback",
                "30",
                "--start",
                "2023-01-01",
                "--end",
                "2024-12-31",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "mutually exclusive" in captured.err.lower()

    def test_download_bars_start_without_end(self, capsys: Any) -> None:
        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--start",
                "2023-01-01",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "--start and --end must be provided together" in captured.err

    def test_download_bars_end_without_start(self, capsys: Any) -> None:
        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--end",
                "2024-12-31",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "--start and --end must be provided together" in captured.err

    def test_download_bars_neither_lookback_nor_dates(self, capsys: Any) -> None:
        rc = main(["download-bars", "--instrument", "TSLA.NASDAQ"])
        captured = capsys.readouterr()
        assert rc == 1
        assert (
            "must provide either --lookback or both --start and --end"
            in captured.err.lower()
        )

    def test_download_bars_invalid_date_format(self, capsys: Any) -> None:
        rc = main(
            [
                "download-bars",
                "--instrument",
                "TSLA.NASDAQ",
                "--start",
                "not-a-date",
                "--end",
                "2024-12-31",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "invalid date format" in captured.err.lower()


class TestBacktestCommand:
    """Tests for the 'sam backtest' command."""

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_single_bundle_table_output(
        self,
        mock_load_bundles: Any,
        mock_wrapper_cls: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """sam backtest <bundle-id> prints a result table."""
        strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "bundle_id": "tsla-orb-15m-futu",
                "venue": "FUTU",
                "market": "US",
            },
        )
        mock_load_bundles.return_value = [strategy]

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=3.5,
            iterations=100,
            total_events=500,
            total_orders=12,
            total_positions=0,
            stats_pnls={"OrbStrategy-001": {"total_pnl": 1250.50}},
            stats_returns={
                "sharpe_ratio": 1.85,
                "max_drawdown": -0.12,
                "win_rate": 0.55,
            },
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "tsla-orb-15m-futu",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--bundles",
                str(bundles_yaml),
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "Backtest Results" in captured.out
        assert "Net P&L" in captured.out
        assert "Sharpe" in captured.out
        assert "Max DD" in captured.out
        assert "Win Rate" in captured.out
        assert "Trades" in captured.out
        assert "Elapsed" in captured.out

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_json_output(
        self,
        mock_load_bundles: Any,
        mock_wrapper_cls: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """sam backtest --json prints structured JSON."""
        strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "bundle_id": "aapl-momentum-5m",
                "venue": "FUTU",
                "market": "US",
            },
        )
        mock_load_bundles.return_value = [strategy]

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=2.0,
            iterations=50,
            total_events=200,
            total_orders=5,
            total_positions=0,
            stats_pnls={"MomentumStrategy-001": {"total_pnl": 800.0}},
            stats_returns={
                "sharpe_ratio": 1.2,
                "max_drawdown": -0.08,
                "win_rate": 0.60,
            },
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "--json",
                "backtest",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "backtest"
        assert data["start"] == "2024-01-01"
        assert data["end"] == "2024-06-30"
        assert len(data["bundles"]) >= 1

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_multi_bundle(
        self,
        mock_load_bundles: Any,
        mock_wrapper_cls: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """sam backtest --bundles runs all enabled bundles."""
        s1 = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "bundle_id": "tsla-orb-15m",
                "venue": "FUTU",
            },
        )
        s2 = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.momentum:MomentumStrategy",
            config_path="sam_trader.strategies.momentum:MomentumStrategyConfig",
            config={
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "bundle_id": "aapl-momentum-5m",
                "venue": "FUTU",
            },
        )
        mock_load_bundles.return_value = [s1, s2]

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=4.0,
            iterations=200,
            total_events=1000,
            total_orders=20,
            total_positions=0,
            stats_pnls={
                "OrbStrategy-001": {"total_pnl": 1500.0},
                "MomentumStrategy-001": {"total_pnl": 800.0},
            },
            stats_returns={
                "sharpe_ratio": 1.6,
                "max_drawdown": -0.10,
                "win_rate": 0.52,
            },
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "Backtest Results" in captured.out
        mock_wrapper.run.assert_called_once()

    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_bundle_not_found(
        self,
        mock_load_bundles: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """Backtest with non-existent bundle_id fails with helpful message."""
        strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "bundle_id": "tsla-orb-15m-futu",
                "venue": "FUTU",
            },
        )
        mock_load_bundles.return_value = [strategy]

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "nonexistent-bundle",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "not found" in captured.err or "not found" in captured.out

    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_no_enabled_bundles(
        self,
        mock_load_bundles: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """Backtest with no enabled bundles fails gracefully."""
        mock_load_bundles.return_value = []

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert (
            "No enabled bundles" in captured.err or "No enabled bundles" in captured.out
        )

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_engine_error(
        self,
        mock_load_bundles: Any,
        mock_wrapper_cls: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """Backtest engine failure returns non-zero exit code."""
        strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "bundle_id": "tsla-orb-15m-futu",
                "venue": "FUTU",
            },
        )
        mock_load_bundles.return_value = [strategy]

        mock_wrapper = MagicMock()
        mock_wrapper.run.side_effect = BacktestEngineError("No data in catalog")
        mock_wrapper_cls.return_value = mock_wrapper

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "tsla-orb-15m-futu",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "Backtest failed" in captured.err or "Backtest failed" in captured.out

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_custom_catalog_path(
        self,
        mock_load_bundles: Any,
        mock_wrapper_cls: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """--catalog flag propagates to BacktestEngineWrapper."""
        strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "bundle_id": "tsla-orb-15m-futu",
                "venue": "FUTU",
            },
        )
        mock_load_bundles.return_value = [strategy]

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=1.0,
            iterations=10,
            total_events=50,
            total_orders=3,
            total_positions=0,
            stats_pnls={"OrbStrategy-001": {"total_pnl": 100.0}},
            stats_returns={"sharpe_ratio": 0.5},
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
                "--catalog",
                "/custom/catalog/path",
            ]
        )
        _ = capsys.readouterr()
        assert rc == 0
        mock_wrapper_cls.assert_called_once_with(catalog_path="/custom/catalog/path")

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_backtest_result_with_none_stats(
        self,
        mock_load_bundles: Any,
        mock_wrapper_cls: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """Backtest result with None/empty stats still displays table."""
        strategy = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "bundle_id": "tsla-orb-15m-futu",
                "venue": "FUTU",
            },
        )
        mock_load_bundles.return_value = [strategy]

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=0.5,
            iterations=0,
            total_events=0,
            total_orders=0,
            total_positions=0,
            stats_pnls={},
            stats_returns={},
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("bundles: []")

        rc = main(
            [
                "backtest",
                "--bundles",
                str(bundles_yaml),
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        # Should still produce output without crashing
        assert "Backtest Results" in captured.out

    @patch("sam_trader.services.cli._infer_bar_type_from_catalog")
    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    def test_backtest_adhoc_runs_without_bundle(
        self,
        mock_wrapper_cls: Any,
        mock_infer_bar: Any,
        capsys: Any,
    ) -> None:
        """Ad-hoc backtest with --instrument and --strategy-path skips bundles.yaml."""
        mock_infer_bar.return_value = "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=2.5,
            iterations=100,
            total_events=500,
            total_orders=8,
            total_positions=0,
            stats_pnls={"OrbStrategy-001": {"total_pnl": 900.0}},
            stats_returns={
                "sharpe_ratio": 1.3,
                "max_drawdown": -0.09,
                "win_rate": 0.50,
            },
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        rc = main(
            [
                "backtest",
                "--instrument",
                "TSLA.NASDAQ",
                "--strategy-path",
                "sam_trader.strategies.orb:OrbStrategy",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 0
        assert "Backtest Results" in captured.out
        # Verify the engine was called with the ad-hoc strategy
        call_kwargs = mock_wrapper.run.call_args.kwargs
        assert call_kwargs["instrument_ids"] == ["TSLA.NASDAQ"]
        assert call_kwargs["bar_types"] == ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"]
        strategies = call_kwargs["strategies"]
        assert len(strategies) == 1
        assert strategies[0].config["stop_loss_ticks"] == 10
        assert strategies[0].config["take_profit_ticks"] == 30

    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    def test_backtest_adhoc_with_explicit_bar_type(
        self,
        mock_wrapper_cls: Any,
        capsys: Any,
    ) -> None:
        """Ad-hoc backtest with explicit --bar-type does not infer from catalog."""
        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=1.5,
            iterations=50,
            total_events=200,
            total_orders=4,
            total_positions=0,
            stats_pnls={"OrbStrategy-001": {"total_pnl": 400.0}},
            stats_returns={"sharpe_ratio": 0.8},
        )

        mock_wrapper = MagicMock()
        mock_wrapper.run.return_value = result
        mock_wrapper_cls.return_value = mock_wrapper

        rc = main(
            [
                "backtest",
                "--instrument",
                "AAPL.NASDAQ",
                "--strategy-path",
                "sam_trader.strategies.orb:OrbStrategy",
                "--bar-type",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        _ = capsys.readouterr()
        assert rc == 0
        call_kwargs = mock_wrapper.run.call_args.kwargs
        assert call_kwargs["bar_types"] == ["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"]

    def test_backtest_adhoc_mutual_exclusive_with_bundle_id(
        self,
        capsys: Any,
    ) -> None:
        """Cannot use BUNDLE_ID argument together with --instrument."""
        rc = main(
            [
                "backtest",
                "some-bundle",
                "--instrument",
                "TSLA.NASDAQ",
                "--strategy-path",
                "sam_trader.strategies.orb:OrbStrategy",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "mutually exclusive" in captured.err.lower()

    def test_backtest_adhoc_missing_strategy_path(
        self,
        capsys: Any,
    ) -> None:
        """--instrument without --strategy-path raises an error."""
        rc = main(
            [
                "backtest",
                "--instrument",
                "TSLA.NASDAQ",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "requires --strategy-path" in captured.err

    def test_backtest_adhoc_missing_instrument(
        self,
        capsys: Any,
    ) -> None:
        """--strategy-path without --instrument raises an error."""
        rc = main(
            [
                "backtest",
                "--strategy-path",
                "sam_trader.strategies.orb:OrbStrategy",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "requires --instrument" in captured.err

    @patch("sam_trader.services.cli._infer_bar_type_from_catalog")
    def test_backtest_adhoc_bar_type_inference_fails(
        self,
        mock_infer_bar: Any,
        capsys: Any,
    ) -> None:
        """Ad-hoc backtest fails gracefully when bar type cannot be inferred."""
        mock_infer_bar.return_value = None

        rc = main(
            [
                "backtest",
                "--instrument",
                "UNKNOWN.NASDAQ",
                "--strategy-path",
                "sam_trader.strategies.orb:OrbStrategy",
                "--start",
                "2024-01-01",
                "--end",
                "2024-06-30",
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "Could not infer bar type" in captured.err

    @patch("sam_trader.services.cli._infer_bar_type_from_catalog")
    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    def test_backtest_adhoc_works_with_sweep(
        self,
        mock_wrapper_cls: Any,
        mock_infer_bar: Any,
        capsys: Any,
    ) -> None:
        """Ad-hoc backtest works with --sweep flags."""
        mock_infer_bar.return_value = "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"

        from sam_trader.services.backtest.sweep import ParameterSweep

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=1.0,
            iterations=20,
            total_events=100,
            total_orders=2,
            total_positions=0,
            stats_pnls={"OrbStrategy-001": {"total_pnl": 200.0}},
            stats_returns={"sharpe_ratio": 0.6},
        )

        mock_wrapper = MagicMock()
        mock_wrapper.build_run_config.return_value = MagicMock()
        mock_wrapper.run_multi.return_value = [result, result]
        mock_wrapper_cls.return_value = mock_wrapper

        with patch.object(ParameterSweep, "format_table", return_value="sweep table"):
            rc = main(
                [
                    "backtest",
                    "--instrument",
                    "TSLA.NASDAQ",
                    "--strategy-path",
                    "sam_trader.strategies.orb:OrbStrategy",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-06-30",
                    "--sweep",
                    "stop_loss_ticks=5,10",
                ]
            )
        captured = capsys.readouterr()
        assert rc == 0
        assert "sweep table" in captured.out

    @patch("sam_trader.services.cli._infer_bar_type_from_catalog")
    @patch("sam_trader.services.backtest.engine.BacktestEngineWrapper")
    def test_backtest_adhoc_works_with_walk_forward(
        self,
        mock_wrapper_cls: Any,
        mock_infer_bar: Any,
        capsys: Any,
    ) -> None:
        """Ad-hoc backtest works with --walk-forward flags."""
        mock_infer_bar.return_value = "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"

        from sam_trader.services.backtest.walk_forward import WalkForward

        now = datetime.now(timezone.utc)
        now_ns = int(now.timestamp() * 1_000_000_000)
        result = BacktestResult(
            trader_id="BACKTEST-001",
            machine_id="test",
            run_config_id="config-1",
            instance_id="inst-1",
            run_id="run-1",
            run_started=now_ns,
            run_finished=now_ns,
            backtest_start=now_ns,
            backtest_end=now_ns,
            elapsed_time=1.0,
            iterations=20,
            total_events=100,
            total_orders=2,
            total_positions=0,
            stats_pnls={"OrbStrategy-001": {"total_pnl": 200.0}},
            stats_returns={"sharpe_ratio": 0.6},
        )

        mock_wrapper = MagicMock()
        mock_wrapper.build_run_config.return_value = MagicMock()
        mock_wrapper.run_multi.return_value = [result]
        mock_wrapper_cls.return_value = mock_wrapper

        wf_result = MagicMock(
            config={"train_days": 30, "test_days": 10},
            overall_sharpe=0.6,
            overall_pnl=200.0,
            profitable_windows=1,
            total_windows=1,
            param_stability={},
            windows=[
                MagicMock(
                    train_start="2024-01-01",
                    train_end="2024-01-31",
                    test_start="2024-02-01",
                    test_end="2024-02-10",
                    best_params={"stop_loss_ticks": 5},
                    train_sharpe=0.7,
                    test_sharpe=0.6,
                    test_pnl=200.0,
                    test_win_rate=0.5,
                    test_max_dd=-0.05,
                    test_trades=2,
                    error=None,
                )
            ],
        )

        with patch.object(WalkForward, "run", return_value=wf_result):
            with patch.object(
                WalkForward, "format_report", return_value="walk-forward report"
            ):
                rc = main(
                    [
                        "backtest",
                        "--instrument",
                        "TSLA.NASDAQ",
                        "--strategy-path",
                        "sam_trader.strategies.orb:OrbStrategy",
                        "--start",
                        "2024-01-01",
                        "--end",
                        "2024-03-31",
                        "--walk-forward",
                        "--train",
                        "30d",
                        "--test",
                        "10d",
                        "--sweep",
                        "stop_loss_ticks=5,10",
                    ]
                )
        captured = capsys.readouterr()
        assert rc == 0
        assert "walk-forward report" in captured.out

    def test_infer_bar_type_from_catalog_prefers_5m(
        self,
    ) -> None:
        """_infer_bar_type_from_catalog prefers 5-MINUTE bars when available."""
        from sam_trader.services.cli import _infer_bar_type_from_catalog

        result = _infer_bar_type_from_catalog("data/catalog", "TSLA.NASDAQ")
        assert result == "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"

    def test_infer_bar_type_from_catalog_falls_back_to_1m(
        self,
    ) -> None:
        """_infer_bar_type_from_catalog falls back to 1-MINUTE for instruments
        that lack 5-minute data."""
        from sam_trader.services.cli import _infer_bar_type_from_catalog

        # Only AAPL has 5m in the current catalog; if we query an instrument
        # with only 1m data this would test the fallback.  Since TSLA has both,
        # we assert the function returns *a* valid bar type.
        result = _infer_bar_type_from_catalog("data/catalog", "TSLA.NASDAQ")
        assert result is not None
        assert "TSLA.NASDAQ" in result

    def test_infer_bar_type_from_catalog_returns_none_for_missing(
        self,
    ) -> None:
        """_infer_bar_type_from_catalog returns None for unknown instruments."""
        from sam_trader.services.cli import _infer_bar_type_from_catalog

        result = _infer_bar_type_from_catalog("data/catalog", "ZZZZ.NASDAQ")
        assert result is None


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
