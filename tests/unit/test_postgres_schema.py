"""Unit tests for PostgreSQL schema and docker-compose service definition."""

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.mark.unit
def test_postgres_init_schema_exists() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    assert schema_path.exists(), "01_schema.sql must exist"
    sql = schema_path.read_text()
    assert "CREATE TABLE IF NOT EXISTS orders" in sql
    assert "CREATE TABLE IF NOT EXISTS fills" in sql
    assert "CREATE TABLE IF NOT EXISTS positions" in sql


@pytest.mark.unit
def test_fills_has_venue_column() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert "venue" in fills_section, "fills table must have venue column"


@pytest.mark.unit
def test_fills_has_trd_market_column() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert "trd_market" in fills_section, "fills table must have trd_market column"


@pytest.mark.unit
def test_fills_has_venue_order_id() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert (
        "venue_order_id" in fills_section
    ), "fills table must have venue_order_id column"


@pytest.mark.unit
def test_fills_has_currency() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert "currency" in fills_section, "fills table must have currency column"


@pytest.mark.unit
def test_fills_has_ts_init() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert "ts_init" in fills_section, "fills table must have ts_init column"


@pytest.mark.unit
def test_fills_venue_is_varchar_10_not_null() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert (
        "venue           VARCHAR(10)  NOT NULL" in fills_section
    ), "fills.venue must be VARCHAR(10) NOT NULL"


@pytest.mark.unit
def test_fills_trd_market_is_varchar_10() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    fills_section = sql.split("CREATE TABLE IF NOT EXISTS fills")[1].split(";")[0]
    assert (
        "trd_market      VARCHAR(10)" in fills_section
    ), "fills.trd_market must be VARCHAR(10)"


@pytest.mark.unit
def test_orders_has_venue_column() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    orders_section = sql.split("CREATE TABLE IF NOT EXISTS orders")[1].split(";")[0]
    assert (
        "venue           VARCHAR(10)  NOT NULL" in orders_section
    ), "orders.venue must be VARCHAR(10) NOT NULL"


@pytest.mark.unit
def test_positions_has_venue_column() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    positions_section = sql.split("CREATE TABLE IF NOT EXISTS positions")[1].split(";")[
        0
    ]
    assert (
        "venue           VARCHAR(10)  NOT NULL" in positions_section
    ), "positions.venue must be VARCHAR(10) NOT NULL"


@pytest.mark.unit
def test_orders_has_v2_order_types() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    orders_section = sql.split("CREATE TABLE IF NOT EXISTS orders")[1].split(";")[0]
    assert "MARKET_TO_LIMIT" in orders_section, "orders must support v2 order types"
    assert (
        "TRAILING_STOP_MARKET" in orders_section
    ), "orders must support v2 order types"


@pytest.mark.unit
def test_positions_has_venue_unique_constraint() -> None:
    schema_path = PROJECT_ROOT / "docker" / "postgres" / "init" / "01_schema.sql"
    sql = schema_path.read_text()
    positions_section = sql.split("CREATE TABLE IF NOT EXISTS positions")[1].split(";")[
        0
    ]
    assert (
        "UNIQUE (strategy_id, instrument_id, venue)" in positions_section
    ), "positions must have unique constraint on (strategy_id, instrument_id, venue)"


@pytest.mark.unit
def test_docker_compose_postgres_service() -> None:
    compose_path = PROJECT_ROOT / "docker" / "docker-compose.yml"
    with open(compose_path, "r") as f:
        compose = yaml.safe_load(f)
    services = compose.get("services", {})
    assert "sam-postgres" in services, "sam-postgres service must be defined"
    pg = services["sam-postgres"]
    assert pg.get("image") == "postgres:16-alpine"
    assert "pg_isready" in str(pg.get("healthcheck", {}).get("test", []))
    volumes = pg.get("volumes", [])
    assert any(
        "postgres_data" in v for v in volumes
    ), "postgres_data volume must be mounted"
    assert any("init" in v for v in volumes), "init scripts must be mounted"
