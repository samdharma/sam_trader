"""Validate the sam-services crontab structure and requirements."""

from pathlib import Path

import pytest

CRONTAB_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "sam_trader" / "services" / "crontab"
)


@pytest.fixture
def crontab_text() -> str:
    return CRONTAB_PATH.read_text()


@pytest.mark.unit
def test_crontab_has_all_entries(crontab_text: str) -> None:
    """Verify all required cron entries are present."""
    entries = [
        ("backup", "sam_trader.services.backup backup", "0 6 * * 1-5"),
        ("log rotation", "sam_trader.services.rotate_logs", "0 3 * * *"),
        ("deploy window", "sam_trader.services.deploy_window", "*/30 4-9 * * *"),
        ("pipeline", "sam_trader.services.pipeline", "30 7 * * 1-5"),
        (
            "performance analysis",
            "sam_trader.services.performance_analyzer",
            "0 2 * * *",
        ),
    ]
    for name, command, schedule in entries:
        assert command in crontab_text, f"Missing {name} command: {command}"
        assert schedule in crontab_text, f"Missing {name} schedule: {schedule}"


@pytest.mark.unit
def test_runs_as_user_sam(crontab_text: str) -> None:
    lines = [
        ln
        for ln in crontab_text.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "=" not in ln.split()[0]
    ]
    for line in lines:
        parts = line.split()
        assert (
            len(parts) >= 7
        ), f"Expected system crontab format with user field: {line}"
        assert parts[5] == "sam", f"Cron job must run as user 'sam': {line}"


@pytest.mark.unit
def test_env_cron_sourced(crontab_text: str) -> None:
    assert ". /opt/sam_trader/.env_cron" in crontab_text


@pytest.mark.unit
def test_timezone_set_to_hkt(crontab_text: str) -> None:
    assert "TZ=Asia/Hong_Kong" in crontab_text


@pytest.mark.unit
def test_logs_redirected(crontab_text: str) -> None:
    lines = [
        ln
        for ln in crontab_text.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "=" not in ln.split()[0]
    ]
    for line in lines:
        assert ">> /opt/sam_trader/logs/" in line, f"Missing log redirection: {line}"
        assert "2>&1" in line, f"Missing stderr redirection: {line}"


@pytest.mark.unit
def test_performance_analyzer_schedule(crontab_text: str) -> None:
    assert "sam_trader.services.performance_analyzer" in crontab_text
    assert "0 2 * * *" in crontab_text
    assert "/opt/sam_trader/logs/performance.log" in crontab_text
