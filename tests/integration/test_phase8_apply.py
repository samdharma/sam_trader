"""Integration tests for sam apply — orchestrated deploy pipeline."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from sam_trader.services.cli import main


class TestApplyEndToEndMocked:
    """End-to-end mocked test for sam apply pipeline."""

    @patch("sam_trader.services.cli._signal_restart")
    @patch("sam_trader.services.cli._redis_cli")
    @patch("sam_trader.services.cli._run_health_checks")
    @patch("sam_trader.services.cli.validate_bundles")
    @patch("sam_trader.services.cli.is_in_window")
    @patch("sam_trader.services.cli.subprocess.run")
    def test_apply_end_to_end_mocked(
        self,
        mock_subproc: Any,
        mock_window: Any,
        mock_validate: Any,
        mock_health: Any,
        mock_redis_mod: Any,
        mock_restart: Any,
        capsys: Any,
        tmp_path: Any,
    ) -> None:
        """Full apply pipeline with all subcomponents mocked."""
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
            rc = main(["--json", "apply"])

        captured = capsys.readouterr()
        assert rc == 0, f"Expected rc=0, got {rc}. stderr: {captured.err}"
        data = json.loads(captured.out)
        assert data["command"] == "apply"
        assert data["overall"] == "PASS"
        assert len(data["steps"]) == 4  # preflight, snapshot, restart, verify

        step_names = [s["step"] for s in data["steps"]]
        assert "preflight" in step_names
        assert "snapshot" in step_names
        assert "restart" in step_names
        assert "verify" in step_names

        for step in data["steps"]:
            assert step["status"] in ("PASS", "WARN")
            assert "timestamp" in step
