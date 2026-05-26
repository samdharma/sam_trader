"""Validate Dockerfile.services structure and requirements."""

from pathlib import Path

import pytest

DOCKERFILE_PATH = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile.services"


@pytest.fixture
def dockerfile_text() -> str:
    return DOCKERFILE_PATH.read_text()


@pytest.mark.unit
def test_base_image_is_python_312_slim(dockerfile_text: str) -> None:
    assert "FROM python:3.12-slim" in dockerfile_text


@pytest.mark.unit
def test_system_dependencies_installed(dockerfile_text: str) -> None:
    assert "git" in dockerfile_text
    assert "cron" in dockerfile_text
    assert "postgresql-client" in dockerfile_text
    assert "redis-tools" in dockerfile_text
    assert "procps" in dockerfile_text


@pytest.mark.unit
def test_docker_cli_installed(dockerfile_text: str) -> None:
    assert "download.docker.com/linux/static/stable" in dockerfile_text


@pytest.mark.unit
def test_docker_buildx_installed(dockerfile_text: str) -> None:
    assert "docker-buildx" in dockerfile_text
    assert "buildx" in dockerfile_text.lower()


@pytest.mark.unit
def test_non_root_user_sam(dockerfile_text: str) -> None:
    assert "useradd" in dockerfile_text
    assert "sam" in dockerfile_text


@pytest.mark.unit
def test_workdir_set(dockerfile_text: str) -> None:
    assert "WORKDIR /opt/sam_trader" in dockerfile_text


@pytest.mark.unit
def test_required_directories_created(dockerfile_text: str) -> None:
    assert "/opt/sam_trader/config" in dockerfile_text
    assert "/opt/sam_trader/logs" in dockerfile_text
    assert "/opt/sam_trader/backups" in dockerfile_text


@pytest.mark.unit
def test_healthcheck_present(dockerfile_text: str) -> None:
    assert "HEALTHCHECK" in dockerfile_text
    assert "--interval=30s" in dockerfile_text
    assert "--timeout=10s" in dockerfile_text
    assert "--start-period=60s" in dockerfile_text
    assert "--retries=3" in dockerfile_text


@pytest.mark.unit
def test_healthcheck_three_layers(dockerfile_text: str) -> None:
    assert "pgrep" in dockerfile_text
    assert "/dev/tcp/localhost/8080" in dockerfile_text
    assert "curl" in dockerfile_text


@pytest.mark.unit
def test_port_8080_exposed(dockerfile_text: str) -> None:
    assert "EXPOSE 8080" in dockerfile_text


@pytest.mark.unit
def test_cmd_starts_cron_and_http_server(dockerfile_text: str) -> None:
    assert "cron" in dockerfile_text
    assert "sam_trader.services.dashboard" in dockerfile_text
