"""Phase 11 deploy-structure validation — docker-compose + .env cross-reference.

Pure file-parsing tests (no Docker, no Redis, no external processes).
Covers:
1. Docker Compose structure (services, networks, volumes, dependencies)
2. 3-layer health-check conformance per HEALTHCHECK_PATTERN.md
3. .env.example ↔ docker-compose.yml consistency
"""

from __future__ import annotations

import pathlib
import re
from typing import Any, cast

import pytest
import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_PATH = PROJECT_ROOT / "docker" / "docker-compose.yml"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
DEPLOY_SH_PATH = PROJECT_ROOT / "deploy.sh"
WIZARD_PATH = PROJECT_ROOT / "scripts" / "wizard.py"
HEALTHCHECK_PATTERN_PATH = PROJECT_ROOT / "docker" / "HEALTHCHECK_PATTERN.md"

ALL_SERVICES = [
    "sam-trader",
    "sam-postgres",
    "sam-redis",
    "sam-futu-opend",
    "sam-ib-gateway",
    "sam-services",
]

CORE_SERVICES = ["sam-trader", "sam-postgres", "sam-redis"]
PROFILED_SERVICES = {
    "sam-futu-opend": "futu",
    "sam-ib-gateway": "ib",
    "sam-services": "services",
}

HEALTHCHECK_TIMING = {
    "sam-trader": {"start_period": "60s"},
    "sam-postgres": {"start_period": "60s"},
    "sam-redis": {"start_period": "60s"},
    "sam-futu-opend": {"start_period": "120s"},
    "sam-ib-gateway": {"start_period": "60s"},
    "sam-services": {"start_period": "60s"},
}


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    """Parsed docker-compose.yml."""
    with COMPOSE_PATH.open("r") as f:
        return cast(dict[str, Any], yaml.safe_load(f))


@pytest.fixture(scope="module")
def env_example_text() -> str:
    """Raw .env.example contents."""
    return ENV_EXAMPLE_PATH.read_text()


@pytest.fixture(scope="module")
def env_example_keys(env_example_text: str) -> set[str]:
    """Set of KEY names defined in .env.example."""
    keys: set[str] = set()
    for line in env_example_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            keys.add(stripped.split("=", 1)[0].strip())
    return keys


@pytest.fixture(scope="module")
def deploy_sh_text() -> str:
    """Raw deploy.sh contents."""
    return DEPLOY_SH_PATH.read_text()


@pytest.fixture(scope="module")
def wizard_text() -> str:
    """Raw wizard.py contents."""
    return WIZARD_PATH.read_text()


# ── TestDockerComposeStructure ────────────────────────────────────────────────


@pytest.mark.integration
class TestDockerComposeStructure:
    """AC: docker-compose.yml defines the correct service topology."""

    def test_all_six_services_defined(self, compose: dict[str, Any]) -> None:
        """All six services are declared: trader, postgres, redis,
        futu, ib, services.
        """
        services = compose.get("services", {})
        for svc in ALL_SERVICES:
            assert svc in services, f"Missing service: {svc}"

    def test_all_services_on_sam_net(self, compose: dict[str, Any]) -> None:
        """Every service attaches to the sam-net network."""
        services = compose.get("services", {})
        for svc in ALL_SERVICES:
            networks = services[svc].get("networks", [])
            assert "sam-net" in networks, f"{svc} not on sam-net"

    def test_named_volumes_use_local_driver(self, compose: dict[str, Any]) -> None:
        """postgres_data, redis_data, futu_opend_data use driver: local."""
        volumes = compose.get("volumes", {})
        for vol_name in ("postgres_data", "redis_data", "futu_opend_data"):
            assert vol_name in volumes, f"Missing volume: {vol_name}"
            assert (
                volumes[vol_name].get("driver") == "local"
            ), f"{vol_name} driver must be local"

    def test_sam_trader_depends_on_postgres_and_redis(
        self, compose: dict[str, Any]
    ) -> None:
        """sam-trader depends_on postgres and redis with condition service_healthy."""
        deps = compose["services"]["sam-trader"].get("depends_on", {})
        assert "sam-postgres" in deps
        assert "sam-redis" in deps
        assert deps["sam-postgres"].get("condition") == "service_healthy"
        assert deps["sam-redis"].get("condition") == "service_healthy"

    def test_sam_services_has_docker_sock_mount(self, compose: dict[str, Any]) -> None:
        """sam-services mounts /var/run/docker.sock:ro for ops commands."""
        volumes = compose["services"]["sam-services"].get("volumes", [])
        assert any(
            "/var/run/docker.sock" in str(v) and ":ro" in str(v) for v in volumes
        ), "sam-services missing docker.sock read-only mount"


# ── TestHealthCheckPattern ────────────────────────────────────────────────────


@pytest.mark.integration
class TestHealthCheckPattern:
    """AC: Every container implements the 3-layer health-check pattern."""

    @pytest.mark.parametrize("svc", ALL_SERVICES)
    def test_service_has_healthcheck(self, compose: dict[str, Any], svc: str) -> None:
        """Each service defines a healthcheck block."""
        assert "healthcheck" in compose["services"][svc], f"{svc} missing healthcheck"

    @pytest.mark.parametrize("svc", ALL_SERVICES)
    def test_healthcheck_interval_30s(self, compose: dict[str, Any], svc: str) -> None:
        """Health-check interval is 30s for all containers."""
        hc = compose["services"][svc]["healthcheck"]
        assert hc.get("interval") == "30s", f"{svc} interval != 30s"

    @pytest.mark.parametrize("svc", ALL_SERVICES)
    def test_healthcheck_timeout_10s(self, compose: dict[str, Any], svc: str) -> None:
        """Health-check timeout is 10s for all containers."""
        hc = compose["services"][svc]["healthcheck"]
        assert hc.get("timeout") == "10s", f"{svc} timeout != 10s"

    @pytest.mark.parametrize("svc", ALL_SERVICES)
    def test_healthcheck_retries_3(self, compose: dict[str, Any], svc: str) -> None:
        """Health-check retries is 3 for all containers."""
        hc = compose["services"][svc]["healthcheck"]
        assert hc.get("retries") == 3, f"{svc} retries != 3"

    @pytest.mark.parametrize("svc", ALL_SERVICES)
    def test_healthcheck_start_period_correct(
        self, compose: dict[str, Any], svc: str
    ) -> None:
        """start_period matches HEALTHCHECK_PATTERN.md (futu=120s, others=60s)."""
        expected = HEALTHCHECK_TIMING[svc]["start_period"]
        actual = compose["services"][svc]["healthcheck"].get("start_period")
        assert actual == expected, f"{svc} start_period {actual} != {expected}"

    def test_sam_postgres_healthcheck_has_l1_l2_l3(
        self, compose: dict[str, Any]
    ) -> None:
        """sam-postgres: L1=pgrep, L2=pg_isready, L3=SELECT 1."""
        test_cmd = " ".join(compose["services"]["sam-postgres"]["healthcheck"]["test"])
        assert "pgrep postgres" in test_cmd
        assert "pg_isready" in test_cmd
        assert "SELECT 1" in test_cmd

    def test_sam_redis_healthcheck_has_l1_l2_l3(self, compose: dict[str, Any]) -> None:
        """sam-redis: L1=pgrep redis-server, L2=redis-cli ping PONG, L3=INFO server."""
        test_cmd = " ".join(compose["services"]["sam-redis"]["healthcheck"]["test"])
        assert "pgrep redis-server" in test_cmd
        assert "redis-cli" in test_cmd
        assert "PONG" in test_cmd
        assert "INFO server" in test_cmd

    def test_sam_ib_gateway_healthcheck_has_l1_l2(
        self, compose: dict[str, Any]
    ) -> None:
        """sam-ib-gateway: L1=pgrep java, L2=/dev/tcp/localhost/4004."""
        test_cmd = " ".join(
            compose["services"]["sam-ib-gateway"]["healthcheck"]["test"]
        )
        assert "pgrep java" in test_cmd
        assert "/dev/tcp/localhost/4004" in test_cmd

    def test_sam_services_healthcheck_has_l1_l2_l3(
        self, compose: dict[str, Any]
    ) -> None:
        """sam-services: L1=pgrep python, L2=tcp/8080, L3=curl /health."""
        test_cmd = " ".join(compose["services"]["sam-services"]["healthcheck"]["test"])
        assert "pgrep python" in test_cmd
        assert "/dev/tcp/localhost/8080" in test_cmd
        assert "curl" in test_cmd
        assert "/health" in test_cmd

    def test_sam_trader_healthcheck_has_l1_l2(self, compose: dict[str, Any]) -> None:
        """sam-trader: L1=python PID 1 check, L2=cmdline grep python."""
        test_cmd = " ".join(compose["services"]["sam-trader"]["healthcheck"]["test"])
        assert "python" in test_cmd
        assert "/proc/1/cmdline" in test_cmd


# ── TestProfileGating ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestProfileGating:
    """AC: Optional services are gated by Docker Compose profiles."""

    def test_core_infra_has_no_profiles(self, compose: dict[str, Any]) -> None:
        """sam-trader, sam-postgres, sam-redis must NOT have profiles."""
        for svc in CORE_SERVICES:
            assert (
                "profiles" not in compose["services"][svc]
            ), f"{svc} must not declare profiles"

    def test_futu_opend_has_futu_profile(self, compose: dict[str, Any]) -> None:
        """sam-futu-opend has profile 'futu'."""
        profiles = compose["services"]["sam-futu-opend"].get("profiles", [])
        assert "futu" in profiles

    def test_ib_gateway_has_ib_profile(self, compose: dict[str, Any]) -> None:
        """sam-ib-gateway has profile 'ib'."""
        profiles = compose["services"]["sam-ib-gateway"].get("profiles", [])
        assert "ib" in profiles

    def test_services_has_services_profile(self, compose: dict[str, Any]) -> None:
        """sam-services has profile 'services'."""
        profiles = compose["services"]["sam-services"].get("profiles", [])
        assert "services" in profiles


# ── TestDeployE2EFlow ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestDeployE2EFlow:
    """AC: deploy.sh references correct compose file and orchestrates start order."""

    def test_deploy_sh_points_to_docker_compose_yml(self, deploy_sh_text: str) -> None:
        """deploy.sh must reference docker/docker-compose.yml."""
        assert "docker/docker-compose.yml" in deploy_sh_text

    def test_deploy_sh_uses_profile_args_for_optional_services(
        self, deploy_sh_text: str
    ) -> None:
        """deploy.sh passes --profile futu, --profile ib, --profile services."""
        assert (
            '--profile" "futu"' in deploy_sh_text
            or "'--profile' 'futu'" in deploy_sh_text
            or "--profile futu" in deploy_sh_text
        )
        assert "ib" in deploy_sh_text
        assert "services" in deploy_sh_text

    def test_deploy_sh_core_start_order(self, deploy_sh_text: str) -> None:
        """Core infra (postgres, redis) is started before sam-trader in deploy.sh."""
        pg = deploy_sh_text.find("sam-postgres")
        redis = deploy_sh_text.find("sam-redis")
        trader = deploy_sh_text.find("sam-trader")
        assert pg > 0 and redis > 0 and trader > 0
        assert pg < trader, "postgres must start before trader"
        assert redis < trader, "redis must start before trader"

    def test_deploy_sh_uses_env_file_flag(self, deploy_sh_text: str) -> None:
        """AC: All docker compose commands in deploy.sh must use --env-file."""
        # Only match actual docker compose invocations (not version checks or docs)
        compose_lines = [
            line
            for line in deploy_sh_text.splitlines()
            if "docker compose " in line
            and (
                "-f" in line
                or "up" in line
                or "down" in line
                or "build" in line
                or "ps" in line
            )
            and "version" not in line
        ]
        assert len(compose_lines) > 0, "deploy.sh must have docker compose commands"
        for line in compose_lines:
            assert (
                "--env-file " in line
            ), f"docker compose command missing --env-file:\n  {line.strip()}"


# ── TestEnvConsistency ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestEnvConsistency:
    """AC: .env.example is complete and consistent with docker-compose.yml."""

    def test_every_compose_var_has_default_or_example_entry(
        self, compose: dict[str, Any], env_example_keys: set[str]
    ) -> None:
        """Any ${VAR} in docker-compose without :-default must exist in .env.example."""
        raw_text = COMPOSE_PATH.read_text()
        # Find all ${VAR} references that do NOT have a :-default
        bare_vars = re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}(?!:)", raw_text)
        for var in set(bare_vars):
            assert (
                var in env_example_keys
            ), f"{var} has no default in compose and is missing from .env.example"

    def test_mandatory_keys_in_env_example(self, env_example_keys: set[str]) -> None:
        """.env.example must contain TRADER_ID, SAM_ENV, POSTGRES_PASSWORD, REDIS_PASSWORD."""  # noqa: E501
        for key in ("TRADER_ID", "SAM_ENV", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
            assert key in env_example_keys, f"Mandatory key missing: {key}"

    def test_env_example_warns_never_commit(self, env_example_text: str) -> None:
        """.env.example header must warn 'NEVER commit'."""
        assert "NEVER commit" in env_example_text

    def test_wizard_writes_all_keys_without_compose_defaults(
        self, compose: dict[str, Any], wizard_text: str
    ) -> None:
        """Wizard script writes keys that docker-compose needs without defaults."""
        raw_text = COMPOSE_PATH.read_text()
        # Find vars without defaults
        bare_vars = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}(?!:)", raw_text))
        for var in bare_vars:
            assert (
                var in wizard_text
            ), f"Wizard must write {var} (used in compose without default)"


# ── TestPortability ───────────────────────────────────────────────────────────


@pytest.mark.integration
class TestPortability:
    """AC: Deployment artifacts are portable across hosts."""

    def test_network_driver_is_bridge(self, compose: dict[str, Any]) -> None:
        """sam-net uses bridge driver for cross-host compatibility."""
        assert compose.get("networks", {}).get("sam-net", {}).get("driver") == "bridge"

    def test_no_hardcoded_host_paths_in_volumes(self, compose: dict[str, Any]) -> None:
        """Data volumes use named Docker volumes, not host bind-mounts
        (docker.sock exempt).
        """
        services = compose.get("services", {})
        for svc in ALL_SERVICES:
            for vol in services[svc].get("volumes", []):
                vol_str = str(vol)
                # Named volumes are simple strings like "postgres_data:/path"
                # Bind mounts start with ./ or / or ~
                if ":" in vol_str:
                    host_part = vol_str.split(":", 1)[0]
                    if host_part in (
                        "postgres_data",
                        "redis_data",
                        "futu_opend_data",
                    ):
                        continue  # named volume
                    if host_part == "/var/run/docker.sock":
                        continue  # required system socket mount
                    assert not host_part.startswith(
                        "/"
                    ), f"{svc} uses absolute host path: {vol_str}"

    def test_compose_file_exists_and_is_valid_yaml(
        self, compose: dict[str, Any]
    ) -> None:
        """docker-compose.yml is present and parses as valid YAML with
        'services' top key.
        """
        assert COMPOSE_PATH.exists()
        assert "services" in compose
        assert "volumes" in compose
        assert "networks" in compose
