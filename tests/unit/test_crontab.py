"""Validate the sam-services crontab structure and requirements."""

from pathlib import Path

import pytest

CRONTAB_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "sam_trader" / "services" / "crontab"
)


@pytest.fixture
def crontab_text() -> str:
    return CRONTAB_PATH.read_text()


@pytest.mark.unit
def test_backup_schedule_present(crontab_text: str) -> None:
    assert "sam_trader.services.backup backup" in crontab_text
    # 04:30 HKT on weekdays (after US close, within maintenance window)
    assert "30 4 * * 1-5" in crontab_text


@pytest.mark.unit
def test_log_rotation_schedule_present(crontab_text: str) -> None:
    assert "sam_trader.services.rotate_logs" in crontab_text
    # 04:15 HKT daily (right after US close at 04:00 HKT)
    assert "15 4 * * *" in crontab_text


@pytest.mark.unit
def test_deploy_window_schedule_present(crontab_text: str) -> None:
    assert "sam_trader.services.deploy_window" in crontab_text
    # Runs every 30 min during 04:00-09:00
    assert "*/30 4-9 * * *" in crontab_text


@pytest.mark.unit
def test_us_pipeline_schedule_present(crontab_text: str) -> None:
    assert "sam_trader.services.pipeline --market US" in crontab_text
    # 20:30 HKT weekdays (08:30 ET)
    assert "30 20 * * 1-5" in crontab_text


@pytest.mark.unit
def test_hk_pipeline_schedule_present(crontab_text: str) -> None:
    assert "sam_trader.services.pipeline --market HK" in crontab_text
    # 07:30 HKT weekdays
    assert "30 7 * * 1-5" in crontab_text


@pytest.mark.unit
def test_runs_as_user_sam(crontab_text: str) -> None:
    lines = [
        ln
        for ln in crontab_text.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and "=" not in ln.split()[0]
    ]
    for line in lines:
        parts = line.split()
        # System crontab format: minute hour dom month dow user command
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
