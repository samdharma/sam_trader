"""Phase 11 EXIT — Full E2E validation: deploy, update, rollback, cleanup.

Automated coverage (operator must still run 1-hour soak with live Futu):
1. Fresh deploy: deploy.sh structure, sequential start, health gating
2. Daily update: sam apply pipeline, bundle-diff version bump
3. Rollback: snapshot → change → diff → apply restores state
4. Cleanup: deploy.sh stop cleans up all containers
5. State continuity: P&L keys survive across snapshot/apply cycle
"""

from __future__ import annotations

import json
import pathlib
import stat
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.cli import main

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[2] / "deploy.sh"


@pytest.mark.integration
class TestFreshDeployStructure:
    """AC: Fresh macOS: git clone + ./deploy.sh --build start."""

    def test_deploy_sh_executable(self) -> None:
        """deploy.sh must exist and be executable."""
        assert DEPLOY_SH.exists()
        assert DEPLOY_SH.stat().st_mode & stat.S_IXUSR

    def test_deploy_sh_has_fresh_deploy_flags(self) -> None:
        """deploy.sh must support --build and --tag (always-on, no --with-* needed)."""
        content = DEPLOY_SH.read_text()
        assert "--build" in content
        assert "--tag" in content

    def test_deploy_sh_sequential_start_order(self) -> None:
        """deploy.sh starts postgres/redis first, then brokers, then trader."""
        content = DEPLOY_SH.read_text()
        assert "sam-postgres" in content
        assert "sam-redis" in content
        assert "sam-trader" in content
        pg = content.find("sam-postgres")
        redis = content.find("sam-redis")
        trader = content.find("sam-trader")
        assert pg < trader
        assert redis < trader

    def test_deploy_sh_health_gating(self) -> None:
        """deploy.sh waits for each service to become healthy before next."""
        content = DEPLOY_SH.read_text()
        assert "wait_for_healthy" in content
        assert "sam-postgres" in content
        assert "sam-redis" in content

    def test_deploy_sh_syntax_valid(self) -> None:
        """deploy.sh must be syntactically valid bash."""
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY_SH)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.integration
class TestDailyUpdateCycle:
    """AC: Code change → ./deploy.sh --build → sam apply.
    AC: Parameter-only change → sam apply (no rebuild) → bundle diff shows version bump.
    """

    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_after_code_change(
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
        """sam apply runs preflight → snapshot → restart → verify after code change."""
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = True
        result.summary = "1/1 bundles passed validation"
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
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["--json", "apply"])

        captured = capsys.readouterr()
        assert rc == 0, captured.err
        data = json.loads(captured.out)
        assert data["command"] == "apply"
        assert data["overall"] == "PASS"
        step_names = [s["step"] for s in data["steps"]]
        assert "preflight" in step_names
        assert "snapshot" in step_names
        assert "restart" in step_names
        assert "verify" in step_names

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_shows_version_bump_on_parameter_change(
        self,
        mock_redis_mod: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Editing bundles.yaml (parameter-only) produces version bump in diff."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-25T08:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "tsla-orb": {
                        "id": "tsla-orb",
                        "enabled": True,
                        "venue": "FUTU",
                        "version": "1.0.0",
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
            '    version: "1.1.0"\n'
            "    strategy:\n"
            "      config:\n"
            "        trade_size: 10\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "VERSION BUMPS" in captured.out
        assert "tsla-orb" in captured.out
        assert "1.0.0" in captured.out
        assert "1.1.0" in captured.out


@pytest.mark.integration
class TestRollbackCycle:
    """AC: sam snapshot (baseline) → deploy problematic change → rollback → verify."""

    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run")
    def test_snapshot_creates_baseline(
        self,
        mock_run: Any,
        mock_redis_mod: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """sam snapshot saves git hash, bundles hash, and active strategies."""
        mock_r = MagicMock()
        stored: dict[str, str] = {}

        def _store(key: str, value: str, **kwargs: Any) -> None:
            stored[key] = value

        mock_r.set.side_effect = _store
        mock_r.ping.return_value = True
        mock_redis_mod.Redis.return_value = mock_r

        mock_run.return_value = MagicMock(returncode=0, stdout="abc1234\n")

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: baseline-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["--json", "snapshot"])

        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["command"] == "snapshot"
        assert data["status"] == "created"
        assert data["git_hash"] == "abc1234"
        assert "baseline-bundle" in data["active_strategies"]

        # Verify Redis payload
        assert len(stored) == 1
        payload = json.loads(list(stored.values())[0])
        assert payload["git_hash"] == "abc1234"
        assert "baseline-bundle" in payload["active_strategies"]
        assert "bundles" in payload

    @patch("sam_trader.services.cli._redis_cli")
    def test_rollback_detects_added_removed_modified(
        self,
        mock_redis_mod: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """After problematic change, bundle-diff shows added/removed/modified."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-25T08:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "stable-bundle": {
                        "id": "stable-bundle",
                        "enabled": True,
                        "venue": "FUTU",
                    },
                    "removed-bundle": {
                        "id": "removed-bundle",
                        "enabled": True,
                        "venue": "FUTU",
                    },
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: stable-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
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
        assert "REMOVED" in captured.out
        assert "removed-bundle" in captured.out

    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_restores_state_after_rollback(
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
        """sam apply after rollback verifies all services UP and state loaded."""
        mock_window.return_value = True
        result = MagicMock()
        result.all_passed = True
        result.summary = "1/1 bundles passed validation"
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
        bundles_file.write_text("bundles:\n  - id: rollback-test\n")
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["--json", "apply"])

        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["overall"] == "PASS"
        verify_step = [s for s in data["steps"] if s["step"] == "verify"][0]
        assert verify_step["status"] == "PASS"


@pytest.mark.integration
class TestPnlContinuity:
    """AC: Verify previous state restored, P&L continuity."""

    @patch("sam_trader.services.cli._redis_cli")
    def test_pnl_keys_survive_snapshot(
        self,
        mock_redis_mod: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Redis P&L keys written by RealizedPnLTrackerActor survive snapshot."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        pnl_keys = ["sam:pnl:tsla-orb:2026-05-25", "sam:pnl:baba-orb:2026-05-25"]
        mock_r.keys.return_value = pnl_keys
        mock_r.get.side_effect = lambda k: json.dumps(
            {"realized_pnl": 150.0, "fills": 3}
        )
        mock_redis_mod.Redis.return_value = mock_r

        # Snapshot should include active strategies but not delete P&L keys
        mock_run = MagicMock(returncode=0, stdout="deadbeef\n")
        with patch("sam_trader.services.cli._run", return_value=mock_run):
            bundles_file = tmp_path / "bundles.yaml"
            bundles_file.write_text("bundles:\n  - id: tsla-orb\n")
            with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
                rc = main(["--json", "snapshot"])

        captured = capsys.readouterr()
        assert rc == 0
        data = json.loads(captured.out)
        assert data["status"] == "created"
        # P&L keys were not deleted during snapshot
        deleted_calls = [c for c in mock_r.delete.call_args_list]
        pnl_deleted = any("pnl" in str(c[0][0]) for c in deleted_calls if c[0])
        assert not pnl_deleted, "Snapshot must not delete P&L keys"


@pytest.mark.integration
class TestTagBasedDeploy:
    """AC: ./deploy.sh --tag v1.0.0 --build checks out tag before building."""

    def test_deploy_sh_has_tag_flag(self) -> None:
        """deploy.sh must support --tag <tag>."""
        content = DEPLOY_SH.read_text()
        assert "--tag" in content

    def test_deploy_sh_fetches_tags(self) -> None:
        """deploy.sh must run git fetch --tags before checkout."""
        content = DEPLOY_SH.read_text()
        assert "git fetch --tags" in content
        assert "git checkout" in content

    def test_tag_flag_requires_value(self) -> None:
        """deploy.sh --tag without value must show error/usage."""
        content = DEPLOY_SH.read_text()
        assert "--tag requires" in content or 'TAG="$2"' in content


@pytest.mark.integration
class TestCleanup:
    """AC: ./deploy.sh stop cleans up all containers."""

    def test_deploy_sh_has_stop_action(self) -> None:
        """deploy.sh must support stop action."""
        content = DEPLOY_SH.read_text()
        assert "stop)" in content or "stop_stack" in content

    def test_stop_calls_docker_compose_down(self) -> None:
        """deploy.sh stop must run docker compose down."""
        content = DEPLOY_SH.read_text()
        assert "docker compose" in content
        assert "down" in content

    def test_stop_has_no_profile_flags(self) -> None:
        """deploy.sh stop must NOT use --profile flags (all always-on)."""
        content = DEPLOY_SH.read_text()
        stop_section = content[content.find("stop_stack") :]
        assert "--profile" not in stop_section


@pytest.mark.integration
class TestSoakTestPrerequisites:
    """AC: 1-hour soak test prerequisites validated.

    NOTE: The actual 1-hour soak with live Futu data is an operator
    manual step. These tests validate that the automation exists.
    """

    def test_sam_health_command_exists(self) -> None:
        """sam health must exist for operator to poll during soak."""
        from sam_trader.services.cli import cli

        assert "health" in [cmd.name for cmd in cli.commands.values()]

    def test_health_checks_all_services(self) -> None:
        """sam health checks postgres, redis, futu_opend, sam_trader."""
        from sam_trader.services.cli import _run_health_checks

        result = _run_health_checks()
        assert "postgres" in result
        assert "redis" in result
        assert "futu_opend" in result
        assert "sam_trader" in result

    def test_dashboard_auto_refresh_meta_tag(self) -> None:
        """Dashboard HTML must contain 30-second auto-refresh meta tag."""
        from sam_trader.services.dashboard import _DASHBOARD_HTML

        assert 'http-equiv="refresh"' in _DASHBOARD_HTML
        assert "30" in _DASHBOARD_HTML

    def test_sam_status_exists(self) -> None:
        """sam status must exist for operator visibility during soak."""
        from sam_trader.services.cli import cli

        assert "status" in [cmd.name for cmd in cli.commands.values()]
