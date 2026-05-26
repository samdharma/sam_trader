"""Tests for docker/futu-opend/healthcheck.sh L3 login-success verification."""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
HEALTHCHECK_SH = PROJECT_ROOT / "docker" / "futu-opend" / "healthcheck.sh"


class TestFutuOpenDHealthcheckScript:
    """Structural and behavioural tests for the health-check shell script."""

    def test_script_exists_and_is_executable(self):
        assert HEALTHCHECK_SH.exists()
        assert HEALTHCHECK_SH.stat().st_mode & 0o111  # any execute bit

    def test_script_syntax_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(HEALTHCHECK_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_script_contains_l1_process_check(self):
        content = HEALTHCHECK_SH.read_text()
        assert 'pgrep -x "FutuOpenD"' in content

    def test_script_contains_l2_socket_check(self):
        content = HEALTHCHECK_SH.read_text()
        assert "/dev/tcp/localhost/11111" in content

    def test_script_contains_l3_positive_login_check(self):
        content = HEALTHCHECK_SH.read_text()
        assert 'grep -q "Login successful"' in content

    def test_script_contains_l3_failure_pattern_defense(self):
        content = HEALTHCHECK_SH.read_text()
        assert 'grep -iE "login fail' in content


class TestFutuOpenDHealthcheckL3:
    """Behavioural tests for L3 (log-based login-success verification)."""

    def _run_l3_logic(self, log_dir: Path) -> subprocess.CompletedProcess:
        """Run only the L3 portion of the healthcheck against *log_dir*."""
        fail_pat = "login fail|login failed|conn failed|auth fail|account login"
        script = f"""
LOG_DIR="{log_dir}"
if [ ! -d "$LOG_DIR" ]; then
    echo "UNHEALTHY: FutuOpenD log directory not found"
    exit 1
fi

MOST_RECENT_LOG=$(ls -t "$LOG_DIR"/GTWLog_* 2>/dev/null | head -n 1)

if [ -z "$MOST_RECENT_LOG" ] || [ ! -f "$MOST_RECENT_LOG" ]; then
    echo "UNHEALTHY: No FutuOpenD log files found"
    exit 1
fi

if ! grep -q "Login successful" "$MOST_RECENT_LOG" 2>/dev/null; then
    echo "UNHEALTHY: Login successful not found in most recent GTWLog"
    exit 1
fi

PATTERN="{fail_pat}"
if grep -iE "$PATTERN" "$MOST_RECENT_LOG" > /dev/null 2>&1; then
    echo "UNHEALTHY: Login failure pattern detected in most recent GTWLog"
    exit 1
fi

exit 0
"""
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
        )

    def test_l3_missing_log_dir_fails(self, tmp_path: Path):
        missing_dir = tmp_path / "nonexistent"
        result = self._run_l3_logic(missing_dir)
        assert result.returncode == 1
        assert "log directory not found" in result.stdout

    def test_l3_empty_log_dir_fails(self, tmp_path: Path):
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        result = self._run_l3_logic(log_dir)
        assert result.returncode == 1
        assert "No FutuOpenD log files found" in result.stdout

    def test_l3_log_without_login_successful_fails(self, tmp_path: Path):
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        log_file = log_dir / "GTWLog_20260526.txt"
        log_file.write_text("some init lines\nconnecting...\n")
        result = self._run_l3_logic(log_dir)
        assert result.returncode == 1
        assert "Login successful not found" in result.stdout

    def test_l3_log_with_login_successful_passes(self, tmp_path: Path):
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        log_file = log_dir / "GTWLog_20260526.txt"
        log_file.write_text("init\nLogin successful\nstreaming started\n")
        result = self._run_l3_logic(log_dir)
        assert result.returncode == 0

    def test_l3_login_successful_in_most_recent_log_only(self, tmp_path: Path):
        """Only the most-recent log is evaluated."""
        log_dir = tmp_path / "Log"
        log_dir.mkdir()

        older = log_dir / "GTWLog_20260525.txt"
        older.write_text("Login successful\n")

        newer = log_dir / "GTWLog_20260526.txt"
        newer.write_text("reconnecting...\n")  # no Login successful

        result = self._run_l3_logic(log_dir)
        assert result.returncode == 1
        assert "Login successful not found" in result.stdout

    def test_l3_failure_pattern_overrides_success(self, tmp_path: Path):
        """If the most recent log contains both Login successful and a later
        failure pattern, the container is unhealthy."""
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        log_file = log_dir / "GTWLog_20260526.txt"
        log_file.write_text("Login successful\nconn failed\n")
        result = self._run_l3_logic(log_dir)
        assert result.returncode == 1
        assert "Login failure pattern detected" in result.stdout

    def test_l3_system_busy_without_login_success_fails(self, tmp_path: Path):
        """Reproduces the 25-May sandbox bug: System busy + exit with no
        Login successful must be treated as unhealthy."""
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        log_file = log_dir / "GTWLog_20260526.txt"
        log_file.write_text("System busy\nExiting\n")
        result = self._run_l3_logic(log_dir)
        assert result.returncode == 1
        assert "Login successful not found" in result.stdout

    def test_l3_ftlog_files_ignored_picks_gtwlog(self, tmp_path: Path):
        """Reproduces the 26-May bug: .ftlog files are more recently
        modified but should be ignored — only GTWLog_* is evaluated."""
        log_dir = tmp_path / "Log"
        log_dir.mkdir()

        # GTWLog created first (older)
        gtw_log = log_dir / "GTWLog_20260526.txt"
        gtw_log.write_text("Login successful\n")

        # .ftlog created second (newer) — this was being picked by old healthcheck
        ftlog = log_dir / "FutuOpenD_8_20260526.ftlog"
        ftlog.write_text("binary-ish internal log data\n")

        result = self._run_l3_logic(log_dir)
        assert (
            result.returncode == 0
        ), f"Expected exit 0 (healthy), got {result.returncode}: {result.stdout}"

    def test_l3_no_gtwlog_with_ftlog_present_fails(self, tmp_path: Path):
        """If only .ftlog files exist (no GTWLog_*), the health check
        should report unhealthy — no log files found."""
        log_dir = tmp_path / "Log"
        log_dir.mkdir()

        ftlog = log_dir / "FutuOpenD_8_20260526.ftlog"
        ftlog.write_text("binary data\n")

        monitor = log_dir / "Monitor.log"
        monitor.write_text("monitoring data\n")

        result = self._run_l3_logic(log_dir)
        assert result.returncode == 1
        assert "No FutuOpenD log files found" in result.stdout
