"""Tests for docker/entrypoint.sh service wait logic."""

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
ENTRYPOINT = PROJECT_ROOT / "docker" / "entrypoint.sh"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast(int, s.getsockname()[1])


def _start_mock_server(port: int, accept_once: bool = True) -> threading.Thread:
    """Start a minimal TCP listener on *port* in a background thread."""

    def _serve() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", port))
            srv.listen(1)
            if accept_once:
                try:
                    conn, _ = srv.accept()
                    conn.close()
                except OSError:
                    pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(0.1)
    return t


class TestEntrypoint:
    def test_entrypoint_exits_zero_when_all_services_ready(self):
        """Entrypoint should succeed when PostgreSQL + Redis are reachable."""
        pg_port = _find_free_port()
        redis_port = _find_free_port()

        _start_mock_server(pg_port)
        _start_mock_server(redis_port)

        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(pg_port),
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "WAIT_TIMEOUT": "5",
            "WAIT_FOR_FUTU_OPEND": "0",
            "WAIT_FOR_IB_GATEWAY": "0",
        }

        result = subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        msg = (
            f"entrypoint exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert result.returncode == 0, msg
        assert "PostgreSQL is ready" in result.stdout
        assert "Redis is ready" in result.stdout

    def test_entrypoint_exits_zero_with_optional_brokers(self):
        """Entrypoint should succeed when all optional brokers are reachable."""
        pg_port = _find_free_port()
        redis_port = _find_free_port()
        futu_port = _find_free_port()
        ib_port = _find_free_port()

        _start_mock_server(pg_port)
        _start_mock_server(redis_port)
        _start_mock_server(futu_port)
        _start_mock_server(ib_port)

        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(pg_port),
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "WAIT_TIMEOUT": "5",
            "WAIT_FOR_FUTU_OPEND": "1",
            "FUTU_OPEND_HOST": "127.0.0.1",
            "FUTU_OPEND_PORT": str(futu_port),
            "WAIT_FOR_IB_GATEWAY": "1",
            "IB_GATEWAY_HOST": "127.0.0.1",
            "IB_GATEWAY_PORT": str(ib_port),
        }

        result = subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        msg = (
            f"entrypoint exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert result.returncode == 0, msg
        assert "PostgreSQL is ready" in result.stdout
        assert "Redis is ready" in result.stdout
        assert "Futu OpenD is ready" in result.stdout
        assert "IB Gateway is ready" in result.stdout

    def test_entrypoint_times_out_when_postgres_unavailable(self):
        """Entrypoint should fail when PostgreSQL is unreachable within timeout."""
        redis_port = _find_free_port()
        _start_mock_server(redis_port)

        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(_find_free_port()),
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "WAIT_TIMEOUT": "2",
            "WAIT_FOR_FUTU_OPEND": "0",
            "WAIT_FOR_IB_GATEWAY": "0",
        }

        result = subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        assert result.returncode != 0
        assert "PostgreSQL not ready" in result.stdout

    def test_entrypoint_fatals_when_futu_enabled_but_no_password(self):
        """AC: If .env is missing, FUTU_ENABLED=true with no password must fatal."""
        pg_port = _find_free_port()
        redis_port = _find_free_port()

        _start_mock_server(pg_port)
        _start_mock_server(redis_port)

        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(pg_port),
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "WAIT_TIMEOUT": "5",
            "WAIT_FOR_FUTU_OPEND": "0",
            "WAIT_FOR_IB_GATEWAY": "0",
            "FUTU_ENABLED": "true",
            # FUTU_ACCOUNT_PWD_MD5 deliberately unset
        }

        result = subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when FUTU_ENABLED=true but no password.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FUTU_ENABLED=true" in result.stderr
        assert "FUTU_ACCOUNT_PWD_MD5 is empty" in result.stderr

    def test_entrypoint_fatals_when_ib_enabled_but_no_credentials(self):
        """AC: If .env is missing, IB_ENABLED=true with no credentials must fatal."""
        pg_port = _find_free_port()
        redis_port = _find_free_port()

        _start_mock_server(pg_port)
        _start_mock_server(redis_port)

        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(pg_port),
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "WAIT_TIMEOUT": "5",
            "WAIT_FOR_FUTU_OPEND": "0",
            "WAIT_FOR_IB_GATEWAY": "0",
            "IB_ENABLED": "true",
            # TWS_USERID and TWS_PASSWORD deliberately unset
        }

        result = subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        assert result.returncode != 0, (
            f"Expected non-zero exit when IB_ENABLED=true but no credentials.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "IB_ENABLED=true" in result.stderr
        assert "TWS_USERID or TWS_PASSWORD is empty" in result.stderr

    def test_entrypoint_succeeds_when_futu_enabled_with_password(self):
        """AC: FUTU_ENABLED=true with FUTU_ACCOUNT_PWD_MD5 set should succeed."""
        pg_port = _find_free_port()
        redis_port = _find_free_port()

        _start_mock_server(pg_port)
        _start_mock_server(redis_port)

        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(pg_port),
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "WAIT_TIMEOUT": "5",
            "WAIT_FOR_FUTU_OPEND": "0",
            "WAIT_FOR_IB_GATEWAY": "0",
            "FUTU_ENABLED": "true",
            "FUTU_ACCOUNT_PWD_MD5": "deadbeef",
        }

        result = subprocess.run(
            ["/bin/bash", str(ENTRYPOINT), "true"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        assert result.returncode == 0, (
            f"Expected success when FUTU_ENABLED=true with password set.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
