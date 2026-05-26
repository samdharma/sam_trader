"""Validate that Futu OpenD binary version matches the futu-api SDK version."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


class TestFutuVersionConsistency:
    def test_dockerfile_opend_version_matches_requirements(self):
        """Dockerfile.futu-opend FUTU_OPEND_VER must match requirements.txt futu-api."""
        dockerfile = PROJECT_ROOT / "docker" / "Dockerfile.futu-opend"
        requirements = PROJECT_ROOT / "docker" / "requirements.txt"

        df_text = dockerfile.read_text()
        req_text = requirements.read_text()

        df_ver = re.search(r"ARG FUTU_OPEND_VER=(\S+)", df_text)
        req_ver = re.search(r"futu-api==([\d.]+)", req_text)

        assert df_ver is not None, "FUTU_OPEND_VER not found in Dockerfile.futu-opend"
        assert req_ver is not None, "futu-api pin not found in docker/requirements.txt"
        assert df_ver.group(1) == req_ver.group(1), (
            f"Version mismatch: Dockerfile={df_ver.group(1)} "
            f"requirements.txt={req_ver.group(1)}"
        )

    def test_pyproject_version_matches_requirements(self):
        """pyproject.toml futu-api version must match docker/requirements.txt."""
        pyproject = PROJECT_ROOT / "pyproject.toml"
        requirements = PROJECT_ROOT / "docker" / "requirements.txt"

        pp_text = pyproject.read_text()
        req_text = requirements.read_text()

        pp_ver = re.search(r'"futu-api==([\d.]+)"', pp_text)
        req_ver = re.search(r"futu-api==([\d.]+)", req_text)

        assert pp_ver is not None, "futu-api pin not found in pyproject.toml"
        assert req_ver is not None, "futu-api pin not found in docker/requirements.txt"
        assert pp_ver.group(1) == req_ver.group(1), (
            f"Version mismatch: pyproject.toml={pp_ver.group(1)} "
            f"requirements.txt={req_ver.group(1)}"
        )

    def test_startpy_default_matches_dockerfile(self):
        """start.py default FUTU_OPEND_VER must match Dockerfile."""
        start_py = PROJECT_ROOT / "docker" / "futu-opend" / "start.py"
        dockerfile = PROJECT_ROOT / "docker" / "Dockerfile.futu-opend"

        start_text = start_py.read_text()
        df_text = dockerfile.read_text()

        start_ver = re.search(
            r'os\.environ\.get\("FUTU_OPEND_VER", "([\d.]+)"\)', start_text
        )
        df_ver = re.search(r"ARG FUTU_OPEND_VER=(\S+)", df_text)

        assert start_ver is not None, "Default FUTU_OPEND_VER not found in start.py"
        assert df_ver is not None, "FUTU_OPEND_VER not found in Dockerfile.futu-opend"
        assert start_ver.group(1) == df_ver.group(1), (
            f"Version mismatch: start.py={start_ver.group(1)} "
            f"Dockerfile={df_ver.group(1)}"
        )
