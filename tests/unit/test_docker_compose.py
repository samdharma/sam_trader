"""Validate docker-compose.yml service definitions."""

import subprocess
from pathlib import Path

import pytest

DOCKER_COMPOSE_PATH = (
    Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"
)


def _run_compose_config() -> str:
    """Run docker compose config and return parsed output (no profiles)."""
    cmd = [
        "docker",
        "compose",
        "-f",
        str(DOCKER_COMPOSE_PATH),
        "config",
    ]
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
def test_all_services_in_config() -> None:
    """All 6 services are always-on — no profiles needed."""
    output = _run_compose_config()
    assert "sam-trader:" in output
    assert "sam-postgres:" in output
    assert "sam-redis:" in output
    assert "sam-futu-opend:" in output
    assert "sam-ib-gateway:" in output
    assert "sam-services:" in output
