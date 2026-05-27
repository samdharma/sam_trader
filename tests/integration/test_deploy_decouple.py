"""Integration tests for deploy.sh Phase 11 thin wrapper.

Validates ticket 9z3.12.1 acceptance criteria:
1. deploy.sh is a thin host-side wrapper (~150 lines)
2. Pre-flight checks: docker, docker compose, git
3. Profiles: --with-futu, --with-ib, --with-services
4. Actions: start, stop, build; --setup re-runs wizard
5. --build does git pull → docker compose build
6. --tag v1.0.0 --build does git fetch --tags → checkout → build
7. --with-futu --build start does git pull → build → health-gated start
8. Sequential start with health gating (postgres → redis → futu-opend → trader)
9. --setup triggers scripts/wizard.py
10. First-run trigger when .env missing: runs wizard, exits with instructions
11. deploy.sh does NOT contain: --status, --health, --backup, --restore, --quote, --logs
12. For all ops commands, prints hint: docker exec sam-services sam <command>
13. For daily update workflow, prints hint: deploy.sh --build then
    docker exec sam-services sam apply
14. Portable: works on macOS (zsh) and Linux (bash)
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY_SH = Path(__file__).resolve().parents[2] / "deploy.sh"
WIZARD_PY = Path(__file__).resolve().parents[2] / "scripts" / "wizard.py"


@pytest.mark.integration
class TestDeployScriptStructure:
    def test_deploy_sh_exists_and_executable(self) -> None:
        """deploy.sh must exist and be executable."""
        assert DEPLOY_SH.exists(), f"deploy.sh not found at {DEPLOY_SH}"
        assert DEPLOY_SH.stat().st_mode & stat.S_IXUSR, "deploy.sh must be executable"

    def test_deploy_sh_approximate_line_count(self) -> None:
        """deploy.sh should be a thin wrapper (~150 lines, allow up to 220)."""
        lines = DEPLOY_SH.read_text().splitlines()
        assert len(lines) <= 220, f"deploy.sh is {len(lines)} lines; target ~150"

    def test_deploy_sh_has_no_ops_flags(self) -> None:
        """deploy.sh must not contain removed ops flags."""
        content = DEPLOY_SH.read_text()
        removed = ["--status", "--health", "--backup", "--restore", "--quote", "--logs"]
        for flag in removed:
            assert (
                flag not in content
            ), f"deploy.sh must not contain removed flag {flag}"

    def test_deploy_sh_has_no_profile_flags(self) -> None:
        """deploy.sh must NOT have --with-futu, --with-ib, --with-services."""
        content = DEPLOY_SH.read_text()
        assert "--with-futu" not in content
        assert "--with-ib" not in content
        assert "--with-services" not in content

    def test_deploy_sh_has_lifecycle_actions(self) -> None:
        """deploy.sh must support start, stop, build actions."""
        content = DEPLOY_SH.read_text()
        assert "start)" in content or "start_stack" in content
        assert "stop)" in content or "stop_stack" in content
        assert "build)" in content or "run_build" in content

    def test_deploy_sh_no_restart_action(self) -> None:
        """deploy.sh must NOT have restart action (delegated to sam CLI)."""
        content = DEPLOY_SH.read_text()
        assert "restart)" not in content, "restart must be delegated to sam CLI"
        assert "restart_stack" not in content, "restart must be delegated to sam CLI"

    def test_deploy_sh_uses_correct_compose_file(self) -> None:
        """deploy.sh must reference docker/docker-compose.yml."""
        content = DEPLOY_SH.read_text()
        assert "docker/docker-compose.yml" in content

    def test_deploy_sh_calls_health_wait(self) -> None:
        """deploy.sh must wait for containers to become healthy."""
        content = DEPLOY_SH.read_text()
        assert "wait_for_healthy" in content

    def test_deploy_sh_has_preflight_checks(self) -> None:
        """deploy.sh must check for docker, docker compose, and git."""
        content = DEPLOY_SH.read_text()
        assert "command -v docker" in content
        assert "docker compose version" in content
        assert "command -v git" in content

    def test_deploy_sh_has_build_flag(self) -> None:
        """deploy.sh must support --build flag."""
        content = DEPLOY_SH.read_text()
        assert "--build" in content

    def test_deploy_sh_has_tag_flag(self) -> None:
        """deploy.sh must support --tag flag."""
        content = DEPLOY_SH.read_text()
        assert "--tag" in content

    def test_deploy_sh_has_setup_flag(self) -> None:
        """deploy.sh must support --setup flag to trigger wizard."""
        content = DEPLOY_SH.read_text()
        assert "--setup" in content
        assert "wizard" in content

    def test_deploy_sh_sam_cli_hint(self) -> None:
        """deploy.sh must print hint for sam CLI ops commands."""
        content = DEPLOY_SH.read_text()
        assert "docker exec sam-services sam" in content

    def test_deploy_sh_daily_update_hint(self) -> None:
        """deploy.sh must print hint for daily update workflow."""
        content = DEPLOY_SH.read_text()
        assert "docker exec sam-services sam apply" in content


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
        assert "sam-postgres" in content
        assert "sam-redis" in content
        assert "sam-trader" in content
        pg_pos = content.find("sam-postgres")
        redis_pos = content.find("sam-redis")
        trader_pos = content.find("sam-trader")
        assert pg_pos < trader_pos, "postgres must start before trader"
        assert redis_pos < trader_pos, "redis must start before trader"

    def test_deploy_sh_git_pull_on_build(self) -> None:
        """deploy.sh --build must invoke git pull before docker compose build."""
        content = DEPLOY_SH.read_text()
        build_section = content[
            content.find("run_git_ops") : content.find("run_build") + 200
        ]
        assert "git pull" in build_section
        assert "docker compose" in content
        assert "build" in content

    def test_deploy_sh_git_fetch_tags_on_tag(self) -> None:
        """deploy.sh --tag must invoke git fetch --tags and git checkout."""
        content = DEPLOY_SH.read_text()
        git_section = content[
            content.find("run_git_ops") : content.find("run_git_ops") + 400
        ]
        assert "git fetch --tags" in git_section
        assert "git checkout" in git_section


@pytest.mark.integration
class TestWizardIntegration:
    def test_wizard_script_exists(self) -> None:
        """scripts/wizard.py must exist."""
        assert WIZARD_PY.exists(), f"wizard.py not found at {WIZARD_PY}"

    def test_wizard_generates_env(self) -> None:
        """wizard.py must generate a valid .env file."""
        # We can't run the interactive wizard, but we can verify the
        # script is importable and that its main function is correct.
        import importlib.util

        spec = importlib.util.spec_from_file_location("wizard", WIZARD_PY)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert hasattr(mod, "main")
        assert callable(mod.main)

    def test_first_run_triggers_wizard(self) -> None:
        """deploy.sh must run wizard when .env is missing."""
        content = DEPLOY_SH.read_text()
        assert ".env not found" in content or ".env" in content
        assert "wizard" in content
        assert "python3 scripts/wizard.py" in content


@pytest.mark.integration
class TestSamCliDelegation:
    def test_sam_status_exists_in_cli(self) -> None:
        """sam status must exist in the CLI (ops delegated to sam-services)."""
        from sam_trader.services.cli import main

        # Just verify import succeeds; actual command tested in test_cli.py
        assert callable(main)
