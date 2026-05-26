"""PostgreSQL schema validation for sam-services startup.

Verifies that expected tables exist and emits a single clear CRITICAL log
if any are missing. This prevents the silent-failure scenario where the
init script fails and services spend hours emitting repeated WARNING logs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Tables that must exist for sam-services to function correctly.
EXPECTED_TABLES = ("fills", "orders", "positions", "performance_stats")


@dataclass(frozen=True)
class SchemaValidationConfig:
    """PG connection configuration for schema validation."""

    host: str = "sam-postgres"
    port: int = 5432
    database: str = "sam_trader"
    user: str = "sam"
    password: str = "sam_secret"

    @classmethod
    def from_env(cls) -> SchemaValidationConfig:
        """Build config from environment variables."""
        return cls(
            host=os.getenv("POSTGRES_HOST", "sam-postgres"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "sam_trader"),
            user=os.getenv("POSTGRES_USER", "sam"),
            password=os.getenv("POSTGRES_PASSWORD", "sam_secret"),
        )


async def _list_tables(
    config: SchemaValidationConfig,
) -> set[str]:
    """Return the set of table names in the public schema."""
    import asyncpg

    conn = await asyncpg.connect(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.user,
        password=config.password,
        timeout=10,
    )
    try:
        rows = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            """)
        return {str(r["table_name"]) for r in rows}
    finally:
        await conn.close()


def validate_schema(config: SchemaValidationConfig | None = None) -> bool:
    """Check that all expected tables exist.

    Emits a single CRITICAL log listing any missing tables.
    Returns ``True`` when the schema is valid, ``False`` otherwise.
    """
    cfg = config or SchemaValidationConfig.from_env()

    try:
        import asyncio

        tables = asyncio.run(_list_tables(cfg))
    except Exception as exc:
        logger.critical(
            "DB schema validation failed: cannot connect to PostgreSQL "
            "at %s:%d/%s — %s",
            cfg.host,
            cfg.port,
            cfg.database,
            exc,
        )
        return False

    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        logger.critical(
            "DB schema validation failed: missing table(s) — %s. "
            "Ensure docker/postgres/init/01_schema.sql was applied.",
            ", ".join(missing),
        )
        return False

    logger.info(
        "DB schema validation passed: all %d expected tables present.",
        len(EXPECTED_TABLES),
    )
    return True
