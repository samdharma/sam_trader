"""BacktestResult → PostgreSQL storage service.

Persists :class:`BacktestResult` objects from NautilusTrader backtesting
engine to a PostgreSQL ``backtest_results`` table with JSONB columns for
stats, equity curve, and metadata.  Provides query-by-strategy,
query-by-date-range, and query-by-family accessors.

Usage::

    store = BacktestResultStore(
        pg_dsn="postgresql://sam:sam_secret@localhost:5432/sam_trader"
    )
    run_id = await store.save(
        result=result,
        strategy_id="US-orb_tsla_5m",
        instrument_id="TSLA.NASDAQ",
        bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 30),
        status="completed",
    )
    runs = await store.get_by_strategy("US-orb_tsla_5m")
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

from nautilus_trader.backtest.results import BacktestResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns_to_datetime(ns: int) -> datetime:
    """Convert nanosecond-since-epoch integer to timezone-aware UTC datetime."""
    return datetime.fromtimestamp(ns / 1_000_000_000.0, tz=timezone.utc)


def _sanitize_nan(obj: Any) -> Any:
    """Recursively replace float('nan') with None in dicts/lists."""
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    if isinstance(obj, float) and obj != obj:  # noqa: PLR0124 — NaN
        return None
    return obj


def _serialize_stats(obj: Any) -> str:
    """JSON-serialize an object, converting NaN → null for PostgreSQL."""
    if obj is None:
        return "null"
    sanitized = _sanitize_nan(obj)
    return json.dumps(sanitized, default=_json_default, allow_nan=False)


def _json_default(o: Any) -> Any:
    """Handle non-JSON-serializable types (e.g. float('nan'))."""
    if isinstance(o, float):
        if o != o:  # noqa: PLR0124 — NaN check
            return None
    raise TypeError(
        f"Object of type {type(o).__name__} is not JSON serializable: {o!r}"
    )


def _build_pg_dsn() -> str:
    """Construct a PG DSN from standard environment variables."""
    host = os.environ.get("POSTGRES_HOST", "sam-postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "sam_trader")
    user = os.environ.get("POSTGRES_USER", "sam")
    password = os.environ.get("POSTGRES_PASSWORD", "sam_secret")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# BacktestResultStore
# ---------------------------------------------------------------------------


class BacktestResultStore:
    """Persists and queries Nautilus :class:`BacktestResult` objects in PostgreSQL.

    Parameters
    ----------
    pg_dsn : str
        asyncpg-compatible PostgreSQL connection string.

    """

    def __init__(self, pg_dsn: str) -> None:
        self._pg_dsn = pg_dsn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def save(
        self,
        result: BacktestResult,
        strategy_id: str,
        instrument_id: str,
        bar_type: str,
        start_date: date,
        end_date: date,
        *,
        status: str = "completed",
        strategy_family: str | None = None,
        strategy_version: str | None = None,
        tags: list[str] | None = None,
        equity_curve: list[dict[str, Any]] | None = None,
    ) -> str:
        """Persist a :class:`BacktestResult` row.

        Parameters
        ----------
        result : BacktestResult
            The backtest result object from NautilusTrader.
        strategy_id : str
            Strategy identifier (e.g. ``"US-orb_tsla_5m"``).
        instrument_id : str
            Nautilus instrument ID (e.g. ``"TSLA.NASDAQ"``).
        bar_type : str
            Bar type string used in the backtest.
        start_date : date
            Backtest start date.
        end_date : date
            Backtest end date.
        status : str
            One of ``"running"``, ``"completed"``, ``"failed"``.
        strategy_family : str | None
            Strategy family tag (e.g. ``"ORB"``, ``"Momentum"``).
        strategy_version : str | None
            Strategy version string.
        tags : list[str] | None
            Custom tags stored as a JSONB array.
        equity_curve : list[dict] | None
            Equity curve data points (timestamp + value pairs).

        Returns
        -------
        str
            The ``run_id`` of the persisted row.

        """
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("asyncpg is required for PG connectivity") from exc

        run_id: str = getattr(result, "run_id", "") or ""

        try:
            pool = await asyncpg.create_pool(self._pg_dsn, min_size=1, max_size=2)
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to PostgreSQL: {exc}") from exc

        try:
            async with pool.acquire() as conn:
                stats_pnls_json = (
                    _serialize_stats(result.stats_pnls) if result.stats_pnls else "null"
                )
                stats_returns_json = (
                    _serialize_stats(result.stats_returns)
                    if result.stats_returns
                    else "null"
                )
                equity_curve_json = (
                    json.dumps(
                        _sanitize_nan(equity_curve),
                        default=_json_default,
                        allow_nan=False,
                    )
                    if equity_curve
                    else "null"
                )
                tags_json = (
                    json.dumps(
                        _sanitize_nan(tags), default=_json_default, allow_nan=False
                    )
                    if tags
                    else "null"
                )

                elapsed = (
                    float(result.elapsed_time)
                    if result.elapsed_time is not None
                    else None
                )
                total_events = (
                    int(result.total_events)
                    if result.total_events is not None
                    else None
                )
                total_orders = (
                    int(result.total_orders)
                    if result.total_orders is not None
                    else None
                )
                total_pos = (
                    int(result.total_positions)
                    if result.total_positions is not None
                    else None
                )

                run_config_id: str = getattr(result, "run_config_id", "") or ""

                await conn.execute(
                    """
                    INSERT INTO backtest_results (
                        run_id, run_config_id, strategy_id, instrument_id, bar_type,
                        start_date, end_date, status,
                        total_events, total_orders, total_positions,
                        elapsed_secs,
                        stats_pnls, stats_returns, equity_curve,
                        strategy_family, strategy_version, tags
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10, $11,
                        $12,
                        $13::jsonb, $14::jsonb, $15::jsonb,
                        $16, $17, $18::jsonb
                    )
                    ON CONFLICT (run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        total_events = EXCLUDED.total_events,
                        total_orders = EXCLUDED.total_orders,
                        total_positions = EXCLUDED.total_positions,
                        elapsed_secs = EXCLUDED.elapsed_secs,
                        stats_pnls = EXCLUDED.stats_pnls,
                        stats_returns = EXCLUDED.stats_returns,
                        equity_curve = EXCLUDED.equity_curve,
                        error_message = EXCLUDED.error_message
                    """,
                    run_id,
                    run_config_id,
                    strategy_id,
                    instrument_id,
                    bar_type,
                    start_date,
                    end_date,
                    status,
                    total_events,
                    total_orders,
                    total_pos,
                    elapsed,
                    stats_pnls_json,
                    stats_returns_json,
                    equity_curve_json,
                    strategy_family,
                    strategy_version,
                    tags_json,
                )
                logger.debug("Saved backtest result run_id=%s", run_id)
        finally:
            await pool.close()

        return run_id

    async def get_by_strategy(
        self,
        strategy_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return backtest runs for a given strategy, newest first.

        Parameters
        ----------
        strategy_id : str
            Strategy identifier to filter by.
        limit : int
            Maximum number of rows to return (default 50).

        Returns
        -------
        list[dict]
            Each dict contains all columns from the ``backtest_results`` table.
            ``stats_pnls``, ``stats_returns``, ``equity_curve``, and ``tags``
            are deserialized from JSONB to Python objects.

        """
        return await self._query(
            "WHERE strategy_id = $1 ORDER BY created_at DESC LIMIT $2",
            strategy_id,
            limit,
        )

    async def get_by_date_range(
        self,
        start: date,
        end: date,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return backtest runs whose window overlaps [*start*, *end*].

        Parameters
        ----------
        start : date
            Inclusive start of date range.
        end : date
            Inclusive end of date range.
        limit : int
            Maximum number of rows to return (default 100).

        Returns
        -------
        list[dict]
            Deserialized rows, newest first.

        """
        return await self._query(
            (
                "WHERE start_date <= $1 AND end_date >= $2"
                " ORDER BY created_at DESC LIMIT $3"
            ),
            end,
            start,
            limit,
        )

    async def get_by_family(
        self,
        strategy_family: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return backtest runs for a given strategy family, newest first.

        Parameters
        ----------
        strategy_family : str
            Strategy family tag (e.g. ``"ORB"``, ``"Momentum"``).
        limit : int
            Maximum number of rows to return.

        Returns
        -------
        list[dict]
            Deserialized rows.

        """
        return await self._query(
            "WHERE strategy_family = $1 ORDER BY created_at DESC LIMIT $2",
            strategy_family,
            limit,
        )

    async def get_all(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return all backtest runs, newest first.

        Parameters
        ----------
        limit : int
            Maximum number of rows to return (default 50).

        Returns
        -------
        list[dict]
            Deserialized rows.

        """
        return await self._query(
            "ORDER BY created_at DESC LIMIT $1",
            limit,
        )

    async def get_by_run_id(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Return a single backtest run by run_id.

        Parameters
        ----------
        run_id : str
            The run_id to look up.

        Returns
        -------
        dict | None
            Deserialized row, or ``None`` if not found.

        """
        rows = await self._query(
            "WHERE run_id = $1 LIMIT 1",
            run_id,
        )
        return rows[0] if rows else None

    async def get_by_run_ids(
        self,
        run_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Return backtest runs matching a list of run_ids.

        Parameters
        ----------
        run_ids : list[str]
            Run identifiers to fetch.

        Returns
        -------
        list[dict]
            Deserialized rows (may be fewer than input if some not found).

        """
        if not run_ids:
            return []
        placeholders = ", ".join(f"${i + 1}" for i in range(len(run_ids)))
        return await self._query(
            f"WHERE run_id IN ({placeholders}) ORDER BY created_at DESC",
            *run_ids,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _query(self, where_clause: str, *args: Any) -> list[dict[str, Any]]:
        """Execute a parameterized query and return deserialized rows."""
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("asyncpg is required for PG connectivity") from exc

        try:
            pool = await asyncpg.create_pool(self._pg_dsn, min_size=1, max_size=2)
        except Exception as exc:
            logger.error("Failed to connect to PostgreSQL: %s", exc)
            return []

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT
                        id, run_id, run_config_id, strategy_id, instrument_id,
                        bar_type, start_date, end_date, status,
                        total_events, total_orders, total_positions,
                        elapsed_secs, stats_pnls, stats_returns, equity_curve,
                        error_message, created_at,
                        strategy_family, strategy_version, tags
                    FROM backtest_results
                    {where_clause}
                    """,
                    *args,
                )
        finally:
            await pool.close()

        return [_deserialize_row(row) for row in rows]


def _deserialize_row(row: Any) -> dict[str, Any]:
    """Convert an asyncpg :class:`Record` to a plain dict with JSONB fields decoded."""
    d: dict[str, Any] = dict(row)

    # Deserialize JSONB columns.  asyncpg returns them as JSON strings for
    # jsonb columns when cast via ::jsonb; but if not cast they come as str.
    for col in ("stats_pnls", "stats_returns", "equity_curve", "tags"):
        val = d.get(col)
        if isinstance(val, str) and val != "null":
            try:
                d[col] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass  # keep as-is
        elif val is not None and not isinstance(val, (dict, list)):
            try:
                d[col] = json.loads(str(val))
            except (json.JSONDecodeError, TypeError):
                pass
        elif val == "null" or val is None:
            d[col] = None

    # Convert date/time columns.
    for col in ("start_date", "end_date", "created_at"):
        val = d.get(col)
        if isinstance(val, datetime):
            d[col] = val.date() if col != "created_at" else val

    return d
