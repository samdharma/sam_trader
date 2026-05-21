"""Unit tests for Futu adapter config dataclasses."""

from __future__ import annotations

import pytest

from sam_trader.adapters.futu.config import FutuDataClientConfig, FutuExecClientConfig


class TestFutuDataClientConfig:
    """Tests for FutuDataClientConfig."""

    def test_default_values(self) -> None:
        config = FutuDataClientConfig()

        assert config.host == "futu-opend"
        assert config.port == 11111
        assert config.trd_env == "SIMULATE"
        assert config.trd_market == "US"
        assert config.client_id == 1
        assert config.client_key == ("futu-opend", 11111, "SIMULATE")
        assert config.handle_revised_bars is False

    def test_env_override(self) -> None:
        config = FutuDataClientConfig(
            host="custom-host",
            port=22222,
            trd_env="REAL",
            trd_market="HK",
            client_id=42,
        )

        assert config.host == "custom-host"
        assert config.port == 22222
        assert config.trd_env == "REAL"
        assert config.trd_market == "HK"
        assert config.client_id == 42
        assert config.client_key == ("custom-host", 22222, "REAL")

    def test_frozen_immutability(self) -> None:
        config = FutuDataClientConfig()

        with pytest.raises(AttributeError):
            config.host = "new-host"  # type: ignore[misc]

        with pytest.raises(AttributeError):
            config.port = 99999  # type: ignore[misc]

        with pytest.raises(AttributeError):
            config.trd_env = "REAL"  # type: ignore[misc]


class TestFutuExecClientConfig:
    """Tests for FutuExecClientConfig."""

    def test_default_values(self) -> None:
        config = FutuExecClientConfig()

        assert config.host == "futu-opend"
        assert config.port == 11111
        assert config.trd_env == "SIMULATE"
        assert config.trd_market == "US"
        assert config.client_id == 1
        assert config.unlock_pwd_md5 == ""
        assert config.client_key == ("futu-opend", 11111, "SIMULATE")

    def test_env_override(self) -> None:
        config = FutuExecClientConfig(
            host="exec-host",
            port=33333,
            trd_env="REAL",
            trd_market="CN",
            client_id=7,
            unlock_pwd_md5="abc123",
        )

        assert config.host == "exec-host"
        assert config.port == 33333
        assert config.trd_env == "REAL"
        assert config.trd_market == "CN"
        assert config.client_id == 7
        assert config.unlock_pwd_md5 == "abc123"
        assert config.client_key == ("exec-host", 33333, "REAL")

    def test_frozen_immutability(self) -> None:
        config = FutuExecClientConfig()

        with pytest.raises(AttributeError):
            config.host = "new-host"  # type: ignore[misc]

        with pytest.raises(AttributeError):
            config.port = 99999  # type: ignore[misc]

        with pytest.raises(AttributeError):
            config.trd_env = "REAL"  # type: ignore[misc]
