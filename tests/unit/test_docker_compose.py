"""Validate docker-compose.yml service definitions."""

import subprocess
from pathlib import Path

import pytest

DOCKER_COMPOSE_PATH = (
    Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
)


def _run_compose_config(profile: str | None = None) -> str:
    """Run docker compose config and return parsed output."""
    cmd = [
        "docker",
        "compose",
        "-f",
        str(DOCKER_COMPOSE_PATH),
        "config",
    ]
    if profile:
        cmd.insert(2, f"--profile={profile}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"docker compose config failed:\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return result.stdout


@pytest.mark.unit
def test_ib_profile_config_validates() -> None:
    """IB Gateway profile produces valid docker-compose config."""
    output = _run_compose_config(profile="ib")
    assert "sam-ib-gateway:" in output
    assert "ghcr.io/gnzsnz/ib-gateway:stable" in output
    assert "4004" in output
    assert "5900" in output
    assert "TWS_USERID" in output
    assert "TWS_PASSWORD" in output
    assert "TRADING_MODE" in output
    assert "TWOFA_TIMEOUT_ACTION" in output
    assert "TWOFA_EXIT_INTERVAL" in output
    assert "RELOGIN_AFTER_TWOFA_TIMEOUT" in output
    assert "EXISTING_SESSION_DETECTED_ACTION" in output
