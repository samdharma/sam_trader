"""Unit tests for sam_trader.services.db_schema."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

from sam_trader.services.db_schema import (
    EXPECTED_TABLES,
    SchemaValidationConfig,
    validate_schema,
)


class TestSchemaValidationConfig:
    """Tests for SchemaValidationConfig."""

    def test_defaults(self) -> None:
        """Default values match docker-compose service names."""
        cfg = SchemaValidationConfig()
        assert cfg.host == "sam-postgres"
        assert cfg.port == 5432
        assert cfg.database == "sam_trader"
        assert cfg.user == "sam"
        assert cfg.password == "sam_secret"

    @patch.dict(
        "os.environ",
        {
            "POSTGRES_HOST": "pg.example.com",
            "POSTGRES_PORT": "5433",
            "POSTGRES_DB": "test_db",
            "POSTGRES_USER": "test_user",
            "POSTGRES_PASSWORD": "test_pass",
        },
    )
    def test_from_env(self) -> None:
        """from_env reads all POSTGRES_* variables."""
        cfg = SchemaValidationConfig.from_env()
        assert cfg.host == "pg.example.com"
        assert cfg.port == 5433
        assert cfg.database == "test_db"
        assert cfg.user == "test_user"
        assert cfg.password == "test_pass"


class TestValidateSchema:
    """Tests for validate_schema."""

    def test_all_tables_present(self, caplog: Any) -> None:
        """Returns True and logs INFO when every expected table exists."""
        caplog.set_level(logging.INFO)

        async def _fake_list(*args: object, **kwargs: object) -> set[str]:
            return set(EXPECTED_TABLES)

        with patch("sam_trader.services.db_schema._list_tables", _fake_list):
            result = validate_schema(SchemaValidationConfig())

        assert result is True
        assert "DB schema validation passed" in caplog.text
        assert "4 expected tables present" in caplog.text

    def test_missing_tables(self, caplog: Any) -> None:
        """Returns False and logs a single CRITICAL message with missing tables."""
        caplog.set_level(logging.CRITICAL)

        async def _fake_list(*args: object, **kwargs: object) -> set[str]:
            return {"fills", "performance_stats"}  # orders and positions missing

        with patch("sam_trader.services.db_schema._list_tables", _fake_list):
            result = validate_schema(SchemaValidationConfig())

        assert result is False
        assert "DB schema validation failed" in caplog.text
        assert "orders" in caplog.text
        assert "positions" in caplog.text
        # Must be a single log line, not repeated warnings
        critical_lines = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_lines) == 1

    def test_connection_failure(self, caplog: Any) -> None:
        """Returns False and logs CRITICAL when PG is unreachable."""
        caplog.set_level(logging.CRITICAL)

        async def _fake_list(*args: object, **kwargs: object) -> set[str]:
            raise ConnectionRefusedError("Connection refused")

        with patch("sam_trader.services.db_schema._list_tables", _fake_list):
            result = validate_schema(SchemaValidationConfig())

        assert result is False
        assert "DB schema validation failed" in caplog.text
        assert "cannot connect to PostgreSQL" in caplog.text
        critical_lines = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_lines) == 1

    def test_uses_env_defaults_when_no_config(self, caplog: Any) -> None:
        """When called without arguments it builds config from env."""
        caplog.set_level(logging.INFO)

        async def _fake_list(*args: object, **kwargs: object) -> set[str]:
            return set(EXPECTED_TABLES)

        with patch("sam_trader.services.db_schema._list_tables", _fake_list):
            result = validate_schema()

        assert result is True
