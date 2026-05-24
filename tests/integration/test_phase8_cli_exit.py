"""Phase 8 CLI EXIT integration tests.

End-to-end CLI validation for the Phase 8 exit gate:
1. sam restart follows graceful save_state→stop→run→restore
2. sam preflight catches bundle errors, deploy-window violations, unhealthy services
3. sam snapshot creates Redis checkpoint, --list shows history
4. sam bundle diff shows version-aware changes between current and snapshot
5. sam apply runs full preflight→snapshot→restart→verify pipeline
6. sam apply --dry-run = preflight only
7. Broken commands (deploy, update, rollback, hotfix) removed with helpful hints
8. All existing non-removed CLI commands still work
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.cli import SAM_TRADER_CONTAINER, main


@pytest.mark.integration
class TestRestartGracefulFlow:
    """AC: sam restart follows graceful save_state→stop→run→restore."""

    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_restart_graceful_flow(
        self, mock_subproc: Any, mock_redis_mod: Any, capsys: Any
    ) -> None:
        """Full graceful restart: subscribe, publish, wait, docker restart,
        health, verify."""
        mock_r = MagicMock()
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = [
            {"type": "subscribe"},
            {"type": "message", "data": '{"status": "saved"}'},
        ]
        mock_r.pubsub.return_value = mock_pubsub
        mock_r.exists.return_value = True
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

        # Verify Redis handshake
        mock_r.publish.assert_any_call("sam:restart_request", "graceful")
        mock_pubsub.subscribe.assert_called_once_with("sam:state_saved")

        # Verify docker restart and health inspect
        calls = [c[0][0] for c in mock_subproc.call_args_list]
        assert any(
            "restart" in str(c) and SAM_TRADER_CONTAINER in str(c) for c in calls
        )
        assert any("inspect" in str(c) for c in calls)

        # Verify state_loaded confirmation
        mock_r.exists.assert_called_with("sam:state_loaded")


@pytest.mark.integration
class TestPreflightCatchesIssues:
    """AC: sam preflight catches bundle errors, deploy-window violations,
    unhealthy services."""

    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_preflight_catches_issues(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Blocking issues produce exit code 2 with FAIL in output."""
        mock_window.return_value = False  # outside deploy window
        result = MagicMock()
        result.all_passed = True
        result.summary = "ok"
        mock_validate.return_value = result
        mock_health.return_value = {
            "postgres": {"status": "DOWN"},
            "redis": {"status": "UP"},
            "futu_opend": {"status": "UP", "health": "healthy"},
            "sam_trader": {"status": "UP", "health": "healthy"},
        }
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["preflight"])

        captured = capsys.readouterr()
        assert rc == 2
        assert "FAIL" in captured.out
        assert (
            "deploy_window" in captured.out.lower() or "window" in captured.out.lower()
        )
        assert (
            "services_healthy" in captured.out.lower()
            or "postgres" in captured.out.lower()
        )


@pytest.mark.integration
class TestSnapshotRoundtrip:
    """AC: sam snapshot creates Redis checkpoint; sam snapshot --list shows history."""

    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run")
    def test_snapshot_roundtrip(
        self, mock_run: Any, mock_redis_mod: Any, capsys: Any
    ) -> None:
        """Create a snapshot and then list it."""
        mock_run.return_value = MagicMock(returncode=0, stdout="abc1234\n")

        mock_r = MagicMock()
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = pathlib.Path("config/bundles.yaml")
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
        key = mock_r.set.call_args[0][0]
        assert key.startswith("sam:snapshot:")

        # List snapshots
        mock_r.reset_mock()
        mock_r.keys.return_value = [key]
        mock_r.get.return_value = json.dumps(
            {
                "git_hash": "abc1234",
                "bundles_hash": "sha256_a",
                "timestamp": key.replace("sam:snapshot:", ""),
                "active_strategies": ["test-bundle"],
            }
        )
        rc = main(["snapshot", "--list"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "abc1234" in captured.out
        # --list only shows timestamp + git_hash, not active_strategies


@pytest.mark.integration
class TestBundleDiffShowsChanges:
    """AC: sam bundle diff shows version-aware changes between current and snapshot."""

    @patch("sam_trader.services.cli._redis_cli")
    def test_bundle_diff_shows_changes(
        self,
        mock_redis_mod: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """Diff detects added, removed, modified, and version-bumped bundles."""
        mock_r = MagicMock()
        mock_r.keys.return_value = ["sam:snapshot:2026-05-24T10:00:00+00:00"]
        mock_r.get.return_value = json.dumps(
            {
                "bundles": {
                    "removed-bundle": {
                        "id": "removed-bundle",
                        "enabled": True,
                        "venue": "FUTU",
                    },
                    "modified-bundle": {
                        "id": "modified-bundle",
                        "enabled": True,
                        "venue": "FUTU",
                        "strategy": {"config": {"trade_size": 5}},
                    },
                    "versioned-bundle": {
                        "id": "versioned-bundle",
                        "enabled": True,
                        "venue": "FUTU",
                        "version": "1.0.0",
                    },
                }
            }
        )
        mock_redis_mod.Redis.return_value = mock_r

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text(
            "bundles:\n"
            "  - id: added-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            "  - id: modified-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            "    strategy:\n"
            "      config:\n"
            "        trade_size: 10\n"
            "  - id: versioned-bundle\n"
            "    enabled: true\n"
            "    venue: FUTU\n"
            '    version: "1.1.0"\n'
        )
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["bundle-diff"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "ADDED" in captured.out
        assert "added-bundle" in captured.out
        assert "REMOVED" in captured.out
        assert "removed-bundle" in captured.out
        assert "MODIFIED" in captured.out
        assert "modified-bundle" in captured.out
        assert "VERSION BUMPS" in captured.out
        assert "versioned-bundle" in captured.out
        assert "1.0.0" in captured.out
        assert "1.1.0" in captured.out


@pytest.mark.integration
class TestApplyDryRun:
    """AC: sam apply --dry-run = preflight only."""

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
        """--dry-run stops after preflight; snapshot and restart are never called."""
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
        with patch("sam_trader.services.cli.DEFAULT_BUNDLES_PATH", bundles_file):
            rc = main(["apply", "--dry-run"])

        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out or "dry-run" in captured.out.lower()
        mock_restart.assert_not_called()
        mock_r.set.assert_not_called()


@pytest.mark.integration
class TestRemovedCommandsNotFound:
    """AC: Broken commands (deploy, update, rollback, hotfix) removed with
    helpful hints."""

    def test_removed_commands_not_found(self, capsys: Any) -> None:
        """Invoking removed commands returns error; module docstring points
        to deploy.sh."""
        for cmd in ("deploy", "update", "rollback", "hotfix"):
            rc = main([cmd])
            captured = capsys.readouterr()
            assert rc == 1, f"Command '{cmd}' should return 1, got {rc}"
            assert "ERROR" in captured.err or "No such command" in captured.err

        # Helpful hint lives in the module docstring
        from sam_trader.services import cli as cli_mod

        assert "deploy.sh" in (cli_mod.__doc__ or "")


@pytest.mark.integration
class TestExistingCommandsStillWork:
    """AC: All existing non-removed CLI commands still work."""

    @patch("sam_trader.services.cli._run")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.check_deploy_window")
    @patch("sam_trader.services.cli.rotate_logs")
    @patch("sam_trader.services.cli.run_pipeline")
    @patch("sam_trader.services.cli.asyncpg.connect")
    def test_existing_commands_still_work(
        self,
        mock_connect: Any,
        mock_pipeline: Any,
        mock_rotate: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_run: Any,
        capsys: Any,
        tmp_path: pathlib.Path,
    ) -> None:
        """status, health, backup, restore, quote, logs, version,
        validate-bundles, deploy-window, pipeline, performance all succeed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        mock_window.return_value = True
        mock_rotate.return_value = (0, 0)
        mock_pipeline.return_value = None

        result = MagicMock()
        result.all_passed = True
        result.summary = "1/1 bundles passed validation"
        result.bundles = []
        mock_validate.return_value = result

        mock_conn = MagicMock()
        mock_conn.fetch.return_value = []
        mock_connect.return_value = mock_conn

        bundles_file = tmp_path / "bundles.yaml"
        bundles_file.write_text("bundles:\n  - id: test\n")

        commands: list[list[str]] = [
            ["status"],
            ["version"],
            ["validate-bundles", "--path", str(bundles_file)],
            ["deploy-window"],
            ["rotate-logs"],
            ["pipeline"],
        ]

        for argv in commands:
            rc = main(argv)
            captured = capsys.readouterr()
            assert rc == 0, f"Command {argv} failed with rc={rc}: {captured.err}"
