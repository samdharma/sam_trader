"""Tests for docker/host-monitor.sh cooldown and state logic."""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
MONITOR_SCRIPT = PROJECT_ROOT / "docker" / "host-monitor.sh"


class TestHostMonitorScript:
    """Validate the bash monitor script via subprocess invocations."""

    @pytest.fixture(autouse=True)
    def ensure_script_executable(self):
        assert MONITOR_SCRIPT.exists(), f"Script not found: {MONITOR_SCRIPT}"
        os.chmod(MONITOR_SCRIPT, 0o755)

    def test_syntax_valid(self):
        """bash -n must report no syntax errors."""
        result = subprocess.run(
            ["bash", "-n", str(MONITOR_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_help_flag(self):
        """--help must print usage and exit 0."""
        result = subprocess.run(
            [str(MONITOR_SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_oneshot_no_containers(self):
        """--oneshot with a nonsense prefix should log 'No containers found'."""
        result = subprocess.run(
            [str(MONITOR_SCRIPT), "--oneshot"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "SAM_MONITOR_PREFIX": "sam-nonexistent-xxx-",
                "SAM_MONITOR_LOG": "/dev/null",
            },
        )
        assert result.returncode == 0
        assert (
            "No containers found" in result.stdout
            or "No containers found" in result.stderr
        )


class TestHostMonitorCooldownLogic:
    """Test the Python inline helpers in host-monitor.sh by exercising the
    state-file read/write/cooldown functions directly.
    """

    @pytest.fixture
    def tmp_state_dir(self):
        with tempfile.TemporaryDirectory() as td:
            yield Path(td)

    @pytest.fixture
    def monitor_env(self, tmp_state_dir):
        """Return a copy of os.environ overridden for testing."""
        return {
            **os.environ,
            "SAM_MONITOR_STATE_DIR": str(tmp_state_dir),
            "SAM_MONITOR_LOG": "/dev/null",
            "SAM_MONITOR_COOLDOWN_WINDOW": "900",
            "SAM_MONITOR_COOLDOWN_BACKOFF": "1800",
            "SAM_MONITOR_MAX_RESTARTS": "3",
        }

    def _write_state(self, tmp_state_dir: Path, container: str, data: dict) -> None:
        (tmp_state_dir / f"{container}.json").write_text(json.dumps(data))

    def _read_state(self, tmp_state_dir: Path, container: str) -> dict:
        path = tmp_state_dir / f"{container}.json"
        if not path.exists():
            return {}
        data: dict = json.loads(path.read_text())
        return data

    def _run_state_helper(
        self, tmp_state_dir: Path, container: str, helper: str, monitor_env: dict
    ) -> str:
        """Invoke a specific helper from the script via bash.

        Helpers that take a container name (read_state, record_restart,
        clear_cooldown) are called with the container.  Helpers that take a
        JSON string (is_in_cooldown, count_recent_restarts) are fed the
        container's state via stdin.
        """
        json_helpers = {"is_in_cooldown", "count_recent_restarts"}
        if helper in json_helpers:
            state_path = tmp_state_dir / f"{container}.json"
            json_data = state_path.read_text() if state_path.exists() else "{}"
            cmd = f"""
            SAM_MONITOR_STATE_DIR='{tmp_state_dir}'
            SAM_MONITOR_LOG='/dev/null'
            SAM_MONITOR_COOLDOWN_WINDOW=900
            SAM_MONITOR_COOLDOWN_BACKOFF=1800
            SAM_MONITOR_MAX_RESTARTS=3
            source {MONITOR_SCRIPT} --help >/dev/null 2>&1 || true
            json=$(cat <<'JSONEOF'
{json_data}
JSONEOF
)
            {helper} "$json"
            """
        else:
            cmd = f"""
            SAM_MONITOR_STATE_DIR='{tmp_state_dir}'
            SAM_MONITOR_LOG='/dev/null'
            SAM_MONITOR_COOLDOWN_WINDOW=900
            SAM_MONITOR_COOLDOWN_BACKOFF=1800
            SAM_MONITOR_MAX_RESTARTS=3
            source {MONITOR_SCRIPT} --help >/dev/null 2>&1 || true
            {helper} '{container}'
            """
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            env=monitor_env,
        )
        return result.stdout.strip()

    def test_read_state_missing(self, tmp_state_dir, monitor_env):
        """Reading state for an unknown container returns empty JSON."""
        out = self._run_state_helper(
            tmp_state_dir, "sam-foo", "read_state", monitor_env
        )
        assert out == "{}"

    def test_read_state_existing(self, tmp_state_dir, monitor_env):
        """Reading state returns the stored JSON."""
        self._write_state(tmp_state_dir, "sam-foo", {"restarts": [1, 2, 3]})
        out = self._run_state_helper(
            tmp_state_dir, "sam-foo", "read_state", monitor_env
        )
        data = json.loads(out)
        assert data["restarts"] == [1, 2, 3]

    def test_is_in_cooldown_true(self, tmp_state_dir, monitor_env):
        """Container with future cooldown_until is in cooldown."""
        future = int(time.time()) + 9999
        self._write_state(tmp_state_dir, "sam-foo", {"cooldown_until": future})
        out = self._run_state_helper(
            tmp_state_dir, "sam-foo", "is_in_cooldown", monitor_env
        )
        assert out == "true"

    def test_is_in_cooldown_false(self, tmp_state_dir, monitor_env):
        """Container with past cooldown_until is NOT in cooldown."""
        past = int(time.time()) - 9999
        self._write_state(tmp_state_dir, "sam-foo", {"cooldown_until": past})
        out = self._run_state_helper(
            tmp_state_dir, "sam-foo", "is_in_cooldown", monitor_env
        )
        assert out == "false"

    def test_count_recent_restarts(self, tmp_state_dir, monitor_env):
        """Only restarts within the window are counted."""
        now = int(time.time())
        self._write_state(
            tmp_state_dir,
            "sam-foo",
            {
                "restarts": [
                    now - 100,  # inside 900s window
                    now - 200,
                    now - 1000,  # outside window
                ]
            },
        )
        out = self._run_state_helper(
            tmp_state_dir, "sam-foo", "count_recent_restarts", monitor_env
        )
        assert out == "2"

    def test_record_restart_adds_timestamp(self, tmp_state_dir, monitor_env):
        """record_restart appends a timestamp."""
        before = int(time.time())
        self._run_state_helper(tmp_state_dir, "sam-foo", "record_restart", monitor_env)
        after = int(time.time())
        data = self._read_state(tmp_state_dir, "sam-foo")
        assert len(data["restarts"]) == 1
        assert before <= data["restarts"][0] <= after

    def test_record_restart_triggers_cooldown(self, tmp_state_dir, monitor_env):
        """3 restarts within the window trigger a 30-minute cooldown."""
        now = int(time.time())
        # Pre-populate with 2 recent restarts
        self._write_state(
            tmp_state_dir,
            "sam-foo",
            {"restarts": [now - 60, now - 120]},
        )
        # Third restart should trigger cooldown
        self._run_state_helper(tmp_state_dir, "sam-foo", "record_restart", monitor_env)
        data = self._read_state(tmp_state_dir, "sam-foo")
        assert "cooldown_until" in data
        # cooldown_until should be roughly now + 1800
        assert data["cooldown_until"] >= now + 1700

    def test_clear_cooldown_removes_field(self, tmp_state_dir, monitor_env):
        """clear_cooldown removes cooldown_until."""
        self._write_state(
            tmp_state_dir,
            "sam-foo",
            {"cooldown_until": int(time.time()) + 9999},
        )
        self._run_state_helper(tmp_state_dir, "sam-foo", "clear_cooldown", monitor_env)
        data = self._read_state(tmp_state_dir, "sam-foo")
        assert "cooldown_until" not in data
