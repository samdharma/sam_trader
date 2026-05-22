"""Tests for docker/futu-opend/start.py XML generation and env validation."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
START_PY = PROJECT_ROOT / "docker" / "futu-opend" / "start.py"


def _load_start_module():
    spec = importlib.util.spec_from_file_location("start", START_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFutuOpenDStartupEnvValidation:
    def test_missing_account_id_exits_1(self):
        env = {
            **os.environ,
            "FUTU_ACCOUNT_PWD_MD5": "abc123",
            "FUTU_OPEND_IP": "127.0.0.1",
        }
        result = subprocess.run(
            [sys.executable, str(START_PY)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1
        assert "FUTU_ACCOUNT_ID is required" in result.stderr

    def test_missing_password_exits_1(self):
        env = {
            **os.environ,
            "FUTU_ACCOUNT_ID": "12345",
            "FUTU_OPEND_IP": "127.0.0.1",
        }
        result = subprocess.run(
            [sys.executable, str(START_PY)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1
        assert "FUTU_ACCOUNT_PWD_MD5 is required" in result.stderr

    def test_deprecated_pwd_warns_and_computes_md5(self):
        env = {
            **os.environ,
            "FUTU_ACCOUNT_ID": "12345",
            "FUTU_ACCOUNT_PWD": "secret",
            "FUTU_OPEND_IP": "127.0.0.1",
        }
        result = subprocess.run(
            [sys.executable, str(START_PY)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert "FUTU_ACCOUNT_PWD is deprecated" in result.stderr


class TestFutuOpenDStartupXmlGeneration:
    def test_build_xml_tree_creates_all_elements(self):
        start = _load_start_module()
        root = start.build_xml_tree(
            ip="192.168.1.10",
            api_port="11111",
            login_account="test_id",
            login_pwd_md5="md5hash",
            telnet_port="22222",
            rsa_private_key="/.futu/test.pem",
        )

        assert root.tag == "futu_opend"
        assert root.findtext("ip") == "192.168.1.10"
        assert root.findtext("api_port") == "11111"
        assert root.findtext("login_account") == "test_id"
        assert root.findtext("login_pwd_md5") == "md5hash"
        assert root.findtext("lang") == "chs"
        assert root.findtext("log_level") == "info"
        assert root.findtext("push_proto_type") == "0"
        assert root.findtext("telnet_ip") == "192.168.1.10"
        assert root.findtext("telnet_port") == "22222"
        assert root.findtext("rsa_private_key") == "/.futu/test.pem"
        assert root.findtext("price_reminder_push") == "1"
        assert root.findtext("auto_hold_quote_right") == "1"
        assert root.findtext("pdt_protection") == "1"
        assert root.findtext("dtcall_confirmation") == "1"

    def test_write_xml_produces_valid_file(self):
        start = _load_start_module()
        root = start.build_xml_tree(
            ip="127.0.0.1",
            api_port="11111",
            login_account="acc",
            login_pwd_md5="pwd",
            telnet_port="22222",
            rsa_private_key="/.futu/futu.pem",
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            path = f.name

        try:
            start.write_xml(root, path)
            tree = ET.parse(path)
            parsed_root = tree.getroot()
            assert parsed_root.tag == "futu_opend"
            assert parsed_root.findtext("login_account") == "acc"
        finally:
            os.unlink(path)

    def test_get_env_or_hostname_returns_env_when_set(self):
        start = _load_start_module()
        os.environ["TEST_HOST_VAR"] = "explicit_host"
        assert start.get_env_or_hostname("TEST_HOST_VAR") == "explicit_host"
        del os.environ["TEST_HOST_VAR"]

    def test_get_env_or_hostname_reads_etc_hostname_when_empty(self):
        import pytest

        if not Path("/etc/hostname").exists():
            pytest.skip("/etc/hostname not available on this host")

        start = _load_start_module()
        key = "TEST_HOST_VAR_EMPTY"
        if key in os.environ:
            del os.environ[key]
        result = start.get_env_or_hostname(key)
        assert isinstance(result, str)
        assert len(result) > 0
