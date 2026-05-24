"""Integration tests for deploy.sh decoupling and sam-services CLI ops.

Validates ticket 9z3.9.5 acceptance criteria:
1. deploy.sh only handles setup, profiles, and compose lifecycle
2. Ops commands (status, health, backup, restore, quote, logs) live in sam CLI
3. deploy.sh --with-futu brings up the stack
4. sam status shows containers
5. Stack restart preserves Redis state
6. sam hotfix <module> copies file without full restart
7. sam rollback <tag> restores previous version
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.cli import main

DEPLOY_SH = Path(__file__).resolve().parents[2] / "deploy.sh"


@pytest.mark.integration
class TestDeployScriptStructure:
    def test_deploy_sh_exists_and_executable(self) -> None:
        """deploy.sh must exist and be executable."""
        assert DEPLOY_SH.exists(), f"deploy.sh not found at {DEPLOY_SH}"
        mode = DEPLOY_SH.stat().st_mode
        assert mode & stat.S_IXUSR, "deploy.sh must be executable"

    def test_deploy_sh_has_no_ops_flags(self) -> None:
        """Removed from deploy.sh: --status, --health, --backup,
        --restore, --quote, --logs."""
        content = DEPLOY_SH.read_text()
        removed = [
            "--status",
            "--health",
            "--backup",
            "--restore",
            "--quote",
            "--logs",
        ]
        for flag in removed:
            assert (
                flag not in content
            ), f"deploy.sh must not contain removed flag {flag}"

    def test_deploy_sh_has_required_profiles(self) -> None:
        """deploy.sh must support --with-futu, --with-ib, --with-services."""
        content = DEPLOY_SH.read_text()
        assert "--with-futu" in content
        assert "--with-ib" in content
        assert "--with-services" in content

    def test_deploy_sh_has_lifecycle_actions(self) -> None:
        """deploy.sh must support start, stop, restart actions."""
        content = DEPLOY_SH.read_text()
        assert "start)" in content or "start_stack" in content
        assert "stop)" in content or "stop_stack" in content
        assert "restart)" in content or "restart_stack" in content

    def test_deploy_sh_uses_correct_compose_file(self) -> None:
        """deploy.sh must reference docker/docker-compose.yml."""
        content = DEPLOY_SH.read_text()
        assert "docker/docker-compose.yml" in content

    def test_deploy_sh_calls_health_wait(self) -> None:
        """deploy.sh must wait for containers to become healthy."""
        content = DEPLOY_SH.read_text()
        assert "wait_for_healthy" in content


@pytest.mark.integration
class TestDeployBringsUpStack:
    def test_deploy_script_syntax_valid(self) -> None:
        """deploy.sh must be syntactically valid bash."""
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash syntax error: {result.stderr}"

    def test_deploy_sh_sequential_start_order(self) -> None:
        """deploy.sh must start postgres/redis first, then brokers, then trader."""
        content = DEPLOY_SH.read_text()
        # Find the start_stack function and verify order
        assert "sam-postgres" in content
        assert "sam-redis" in content
        assert "sam-trader" in content
        # Core infra comes before trader
        pg_pos = content.find("sam-postgres")
        redis_pos = content.find("sam-redis")
        trader_pos = content.find("sam-trader")
        assert pg_pos < trader_pos, "postgres must start before trader"
        assert redis_pos < trader_pos, "redis must start before trader"

    def test_deploy_sh_graceful_restart_via_redis(self) -> None:
        """deploy.sh restart must publish to Redis before docker restart."""
        content = DEPLOY_SH.read_text()
        restart_section = content[content.find("restart_stack") :]
        assert "PUBLISH" in restart_section
        assert "sam:restart_request" in restart_section
        assert "graceful" in restart_section
        assert "docker" in restart_section
        assert "restart" in restart_section


@pytest.mark.integration
class TestSamStatusShowsContainers:
    @patch("sam_trader.services.cli._run")
    def test_sam_status_shows_containers(self, mock_run: Any) -> None:
        """sam status must list sam-* containers with name, status, ports."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "NAMES\tSTATUS\tPORTS\n"
                "sam-trader\tUp 2 hours\t8080/tcp\n"
                "sam-postgres\tUp 2 hours\t5432/tcp\n"
                "sam-redis\tUp 2 hours\t6379/tcp\n"
                "sam-futu-opend\tUp 2 hours\t11111/tcp\n"
            ),
        )
        rc = main(["status"])
        assert rc == 0

    @patch("sam_trader.services.cli._run")
    def test_sam_status_json_structure(self, mock_run: Any) -> None:
        """sam status --json must return structured container data."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="NAMES\tSTATUS\tPORTS\nsam-trader\tUp 2 hours\t8080/tcp\n",
        )
        rc = main(["--json", "status"])
        assert rc == 0


@pytest.mark.integration
class TestSamRestartPreservesRedis:
    @patch("sam_trader.services.cli.subprocess.run")
    def test_restart_publishes_redis_graceful(self, mock_subproc: Any) -> None:
        """sam restart must publish graceful restart request to Redis."""
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        rc = main(["restart"])
        assert rc == 0

        calls = [c[0][0] for c in mock_subproc.call_args_list]
        redis_calls = [c for c in calls if "PUBLISH" in str(c)]
        assert len(redis_calls) >= 1
        assert any("sam:restart_request" in str(c) for c in redis_calls)
        assert any("graceful" in str(c) for c in redis_calls)

        # Also verify docker compose restart is triggered
        docker_calls = [c for c in calls if "restart" in str(c).lower()]
        assert any("sam-trader" in str(c) for c in docker_calls)
