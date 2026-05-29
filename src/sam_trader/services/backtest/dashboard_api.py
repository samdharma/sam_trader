"""Backtest Dashboard REST API handlers.

Provides handler functions for the backtest REST API endpoints served by
the dashboard HTTP server (see :mod:`sam_trader.services.dashboard`).

Endpoints
---------
POST   /api/backtest/run                 Launch a backtest asynchronously
GET    /api/backtest/run/<id>/status     Poll running backtest status
GET    /api/backtest/runs                List past backtest runs
GET    /api/backtest/runs/<id>           Get a single backtest result
GET    /api/backtest/compare             Side-by-side metric comparison
GET    /api/backtest/catalog/instruments  List catalog instruments + bar types
GET    /api/backtest/catalog/status      Catalog summary stats

All handlers are synchronous and accept a :class:`DashboardConfig` as their
first positional argument.  They return JSON-compatible dicts/lists.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sam_trader.services.backtest.engine import (
    BacktestEngineError,
    BacktestEngineWrapper,
)
from sam_trader.services.backtest.results import BacktestResultStore, _build_pg_dsn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory registry for running backtests
# ---------------------------------------------------------------------------

_run_registry: dict[str, dict[str, Any]] = {}
_run_registry_lock = threading.Lock()


def _generate_run_id() -> str:
    """Generate a unique backtest run identifier."""
    return f"bt-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Catalog helpers
# ---------------------------------------------------------------------------


def _get_catalog(catalog_path: str = "data/catalog") -> ParquetDataCatalog | None:
    """Return a ParquetDataCatalog if the path exists, else None."""
    p = Path(catalog_path)
    if not p.exists():
        return None
    try:
        return ParquetDataCatalog(path=str(p))
    except Exception as exc:
        logger.warning("Failed to open catalog at %s: %s", catalog_path, exc)
        return None


def _discover_bar_types(catalog: ParquetDataCatalog, instrument_id: str) -> list[str]:
    """Discover available bar types for an instrument by scanning catalog files."""
    bar_types: list[str] = []
    try:
        bar_data_dir = Path(catalog.path) / "data" / "bar"
        if bar_data_dir.exists():
            for f in bar_data_dir.glob("*.parquet"):
                # Parquet filenames are like: TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet
                stem = f.stem
                if stem.startswith(instrument_id):
                    bar_type_str = stem[len(instrument_id) + 1 :]  # skip the '-'
                    if bar_type_str:
                        bar_types.append(bar_type_str)
    except Exception as exc:
        logger.debug("Failed to scan bar files for %s: %s", instrument_id, exc)
    return sorted(set(bar_types))


def _catalog_date_range(
    catalog: ParquetDataCatalog, instrument_id: str
) -> dict[str, Any]:
    """Get first/last bar timestamps for an instrument from the catalog."""
    try:
        first_ts = catalog.query_first_timestamp(Bar, instrument_id)
        last_ts = catalog.query_last_timestamp(Bar, instrument_id)
        return {
            "first_bar": str(first_ts) if first_ts is not None else None,
            "last_bar": str(last_ts) if last_ts is not None else None,
        }
    except Exception as exc:
        logger.debug("Failed to query timestamps for %s: %s", instrument_id, exc)
        return {"first_bar": None, "last_bar": None}


# ---------------------------------------------------------------------------
# POST /api/backtest/run
# ---------------------------------------------------------------------------


def _run_backtest_in_thread(
    run_id: str,
    catalog_path: str,
    strategy_id: str,
    instrument_ids: list[str],
    bar_types: list[str],
    start: str,
    end: str,
    pg_dsn: str,
) -> None:
    """Run a backtest in a background thread and persist results."""
    try:
        _update_run_status(run_id, "running", progress_pct=0)

        # Phase 1 — build and run
        wrapper = BacktestEngineWrapper(catalog_path=catalog_path)
        try:
            result: BacktestResult = wrapper.run(
                strategies=[],  # strategies loaded via bundle configs
                instrument_ids=instrument_ids,
                bar_types=bar_types,
                start=start,
                end=end,
            )
        except BacktestEngineError as exc:
            # If no strategy configs were provided directly, store as failed
            # and note that live strategies are needed for full backtest.
            _update_run_status(run_id, "failed", error=str(exc))
            return

        _update_run_status(run_id, "running", progress_pct=80)

        # Phase 2 — persist
        store = BacktestResultStore(pg_dsn=pg_dsn)
        try:
            import asyncio

            for i, iid in enumerate(instrument_ids):
                bt = bar_types[i] if i < len(bar_types) else bar_types[0]
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        store.save(
                            result=result,
                            strategy_id=strategy_id,
                            instrument_id=iid,
                            bar_type=bt,
                            start_date=date.fromisoformat(start),
                            end_date=date.fromisoformat(end),
                            status="completed",
                        )
                    )
                finally:
                    loop.close()
        except Exception as exc:
            logger.exception("Failed to persist backtest result run_id=%s", run_id)
            _update_run_status(run_id, "failed", error=f"Persistence error: {exc}")
            return

        # Extract stats for the run registry
        stats = _extract_result_stats(result)
        _update_run_status(run_id, "completed", progress_pct=100, result=stats)

    except Exception as exc:
        logger.exception("Backtest %s failed with unexpected error", run_id)
        _update_run_status(run_id, "failed", error=str(exc))


def _update_run_status(
    run_id: str,
    status: str,
    *,
    progress_pct: int = 0,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Thread-safe update of the in-memory run registry."""
    with _run_registry_lock:
        entry = _run_registry.get(run_id, {})
        entry["status"] = status
        if progress_pct > entry.get("progress_pct", 0):
            entry["progress_pct"] = progress_pct
        if result is not None:
            entry["result"] = result
        if error is not None:
            entry["error"] = error
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        _run_registry[run_id] = entry


def _extract_result_stats(result: BacktestResult) -> dict[str, Any]:
    """Extract human-readable stats from a BacktestResult."""
    stats: dict[str, Any] = {}
    try:
        if result.stats_returns:
            sr = result.stats_returns
            if isinstance(sr, dict):
                stats["sharpe_ratio"] = sr.get("sharpe_ratio")
                stats["sortino_ratio"] = sr.get("sortino_ratio")
                stats["max_drawdown"] = sr.get("max_drawdown")
                stats["win_rate"] = sr.get("win_rate")
                stats["profit_factor"] = sr.get("profit_factor")
                stats["expectancy"] = sr.get("expectancy")
                stats["total_pnl"] = sr.get("total_pnl")
                stats["cagr"] = sr.get("cagr")
                stats["volatility"] = sr.get("volatility")
                stats["calmar_ratio"] = sr.get("calmar_ratio")
    except Exception:
        pass
    try:
        stats["total_events"] = (
            int(result.total_events) if result.total_events is not None else None
        )
    except Exception:
        stats["total_events"] = None
    try:
        stats["total_orders"] = (
            int(result.total_orders) if result.total_orders is not None else None
        )
    except Exception:
        stats["total_orders"] = None
    try:
        stats["total_positions"] = (
            int(result.total_positions) if result.total_positions is not None else None
        )
    except Exception:
        stats["total_positions"] = None
    try:
        stats["elapsed_secs"] = (
            float(result.elapsed_time) if result.elapsed_time is not None else None
        )
    except Exception:
        stats["elapsed_secs"] = None
    return stats


def handle_backtest_run(
    body: dict[str, Any],
    *,
    catalog_path: str = "data/catalog",
    pg_dsn: str | None = None,
) -> dict[str, Any]:
    """Handle POST /api/backtest/run — launch an asynchronous backtest.

    Expects JSON body with:

    - ``strategy_id`` (str, required) — strategy to backtest
    - ``instrument_ids`` (list[str], required) — instruments to use
    - ``bar_types`` (list[str], optional) — defaults to auto-discover
    - ``start`` (str, required) — ISO date
    - ``end`` (str, required) — ISO date

    Returns
    -------
    dict
        ``{"run_id": str, "status": "started"}``

    """
    strategy_id: str = body.get("strategy_id", "")
    instrument_ids: list[str] = body.get("instrument_ids", [])
    bar_types: list[str] = body.get("bar_types", [])
    start: str = body.get("start", "")
    end: str = body.get("end", "")

    if not strategy_id:
        return {"error": "Missing required field: strategy_id"}
    if not instrument_ids:
        return {"error": "Missing required field: instrument_ids"}
    if not start or not end:
        return {"error": "Missing required fields: start, end"}

    # Auto-discover bar types if not specified
    if not bar_types:
        catalog = _get_catalog(catalog_path)
        if catalog is not None:
            for iid in instrument_ids:
                discovered = _discover_bar_types(catalog, iid)
                bar_types.extend(discovered)
        if not bar_types:
            bar_types = [f"{iid}-5-MINUTE-LAST-EXTERNAL" for iid in instrument_ids]

    run_id = _generate_run_id()
    dsn = pg_dsn or _build_pg_dsn()

    with _run_registry_lock:
        _run_registry[run_id] = {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "instrument_ids": instrument_ids,
            "bar_types": bar_types,
            "start": start,
            "end": end,
            "status": "started",
            "progress_pct": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    thread = threading.Thread(
        target=_run_backtest_in_thread,
        args=(
            run_id,
            catalog_path,
            strategy_id,
            instrument_ids,
            bar_types,
            start,
            end,
            dsn,
        ),
        daemon=True,
        name=f"bt-{run_id}",
    )
    thread.start()

    return {"run_id": run_id, "status": "started"}


# ---------------------------------------------------------------------------
# GET /api/backtest/run/<id>/status
# ---------------------------------------------------------------------------


def handle_backtest_run_status(run_id: str) -> dict[str, Any]:
    """Handle GET /api/backtest/run/<id>/status — poll running backtest.

    Parameters
    ----------
    run_id : str
        The run identifier returned by :func:`handle_backtest_run`.

    Returns
    -------
    dict
        ``{"run_id": str, "status": str, "progress_pct": int, ...}``

    """
    with _run_registry_lock:
        entry = _run_registry.get(run_id)

    if entry is None:
        return {"error": f"Run not found: {run_id}"}

    return {
        "run_id": entry.get("run_id"),
        "status": entry.get("status", "unknown"),
        "progress_pct": entry.get("progress_pct", 0),
        "error": entry.get("error"),
        "result": entry.get("result"),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# GET /api/backtest/runs
# ---------------------------------------------------------------------------


def handle_backtest_runs(
    *,
    pg_dsn: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Handle GET /api/backtest/runs — list completed backtest runs from PG.

    Parameters
    ----------
    pg_dsn : str | None
        PostgreSQL DSN; auto-built from env vars when ``None``.
    limit : int
        Maximum rows to return (default 50).

    Returns
    -------
    list[dict]
        Each entry: run_id, strategy_id, instrument_id, bar_type,
        start_date, end_date, status, elapsed_secs, created_at,
        strategy_family.

    """
    dsn = pg_dsn or _build_pg_dsn()
    store = BacktestResultStore(pg_dsn=dsn)

    try:
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            rows = loop.run_until_complete(store.get_all(limit=limit))
        finally:
            loop.close()
    except Exception as exc:
        logger.warning("Failed to query backtest runs: %s", exc)
        return []

    if not rows:
        return []

    # Return a clean summary (exclude heavy JSONB columns)
    return [
        {
            "run_id": r.get("run_id"),
            "strategy_id": r.get("strategy_id"),
            "instrument_id": r.get("instrument_id"),
            "bar_type": r.get("bar_type"),
            "start_date": str(r.get("start_date")) if r.get("start_date") else None,
            "end_date": str(r.get("end_date")) if r.get("end_date") else None,
            "status": r.get("status"),
            "elapsed_secs": r.get("elapsed_secs"),
            "created_at": str(r.get("created_at")) if r.get("created_at") else None,
            "strategy_family": r.get("strategy_family"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /api/backtest/runs/<id>
# ---------------------------------------------------------------------------


def handle_backtest_runs_detail(
    run_id: str,
    *,
    pg_dsn: str | None = None,
) -> dict[str, Any]:
    """Handle GET /api/backtest/runs/<id> — full result with stats and equity.

    Parameters
    ----------
    run_id : str
        The run_id to look up in PG.
    pg_dsn : str | None
        PostgreSQL DSN.

    Returns
    -------
    dict
        Full result row including stats_pnls, stats_returns, equity_curve.

    """
    dsn = pg_dsn or _build_pg_dsn()
    store = BacktestResultStore(pg_dsn=dsn)

    try:
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            row = loop.run_until_complete(store.get_by_run_id(run_id))
        finally:
            loop.close()
    except Exception as exc:
        logger.warning("Failed to query backtest run detail: %s", exc)
        return {"error": f"Database error: {exc}"}

    if row is None:
        return {"error": f"Run not found: {run_id}"}

    return {
        "run_id": row.get("run_id"),
        "strategy_id": row.get("strategy_id"),
        "instrument_id": row.get("instrument_id"),
        "bar_type": row.get("bar_type"),
        "start_date": str(row.get("start_date")) if row.get("start_date") else None,
        "end_date": str(row.get("end_date")) if row.get("end_date") else None,
        "status": row.get("status"),
        "total_events": row.get("total_events"),
        "total_orders": row.get("total_orders"),
        "total_positions": row.get("total_positions"),
        "elapsed_secs": row.get("elapsed_secs"),
        "stats_pnls": row.get("stats_pnls"),
        "stats_returns": row.get("stats_returns"),
        "equity_curve": row.get("equity_curve"),
        "error_message": row.get("error_message"),
        "created_at": str(row.get("created_at")) if row.get("created_at") else None,
        "strategy_family": row.get("strategy_family"),
        "strategy_version": row.get("strategy_version"),
        "tags": row.get("tags"),
    }


# ---------------------------------------------------------------------------
# GET /api/backtest/compare?runs=id1,id2
# ---------------------------------------------------------------------------


def handle_backtest_compare(
    run_ids: list[str],
    *,
    pg_dsn: str | None = None,
) -> dict[str, Any]:
    """Handle GET /api/backtest/compare?runs=id1,id2 — side-by-side metrics.

    Parameters
    ----------
    run_ids : list[str]
        Run identifiers to compare.
    pg_dsn : str | None
        PostgreSQL DSN.

    Returns
    -------
    dict
        ``{"runs": {"<run_id>": {...}}, "comparison": [...]}``

    """
    dsn = pg_dsn or _build_pg_dsn()
    store = BacktestResultStore(pg_dsn=dsn)

    try:
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            rows = loop.run_until_complete(store.get_by_run_ids(run_ids))
        finally:
            loop.close()
    except Exception as exc:
        logger.warning("Failed to query backtest comparison: %s", exc)
        return {"error": f"Database error: {exc}"}

    row_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("run_id", "")
        if rid:
            row_map[rid] = row

    # Build per-run summaries
    runs: dict[str, Any] = {}
    for rid in run_ids:
        row_data: dict[str, Any] | None = row_map.get(rid)
        if row_data is None:
            runs[rid] = {"error": "Not found"}
            continue
        runs[rid] = {
            "run_id": row_data.get("run_id"),
            "strategy_id": row_data.get("strategy_id"),
            "instrument_id": row_data.get("instrument_id"),
            "bar_type": row_data.get("bar_type"),
            "start_date": (
                str(row_data.get("start_date")) if row_data.get("start_date") else None
            ),
            "end_date": (
                str(row_data.get("end_date")) if row_data.get("end_date") else None
            ),
            "status": row_data.get("status"),
            "elapsed_secs": row_data.get("elapsed_secs"),
            "stats_returns": row_data.get("stats_returns"),
            "stats_pnls": row_data.get("stats_pnls"),
            "equity_curve": row_data.get("equity_curve"),
        }

    # Build comparison table: one row per metric, columns per run
    all_metrics = [
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "expectancy",
        "total_pnl",
        "cagr",
        "calmar_ratio",
        "volatility",
        "total_events",
        "total_orders",
        "total_positions",
        "elapsed_secs",
    ]
    comparison: list[dict[str, Any]] = []
    for metric in all_metrics:
        metric_row: dict[str, Any] = {"metric": metric}
        for rid in run_ids:
            r_data = runs.get(rid, {})
            if isinstance(r_data, dict) and "error" not in r_data:
                sr = r_data.get("stats_returns") or {}
                if metric in ("total_events", "total_orders", "total_positions"):
                    metric_row[rid] = r_data.get(metric)
                elif metric == "elapsed_secs":
                    metric_row[rid] = r_data.get("elapsed_secs")
                elif isinstance(sr, dict):
                    metric_row[rid] = sr.get(metric)
                else:
                    metric_row[rid] = None
            else:
                metric_row[rid] = None
        comparison.append(metric_row)

    return {"runs": runs, "comparison": comparison}


# ---------------------------------------------------------------------------
# GET /api/backtest/catalog/instruments
# ---------------------------------------------------------------------------


def handle_backtest_catalog_instruments(
    *,
    catalog_path: str = "data/catalog",
) -> list[dict[str, Any]]:
    """Handle GET /api/backtest/catalog/instruments — list catalog contents.

    Parameters
    ----------
    catalog_path : str
        Path to the ParquetDataCatalog.

    Returns
    -------
    list[dict]
        Each entry: ``instrument_id``, ``bar_types``, ``first_bar``, ``last_bar``.

    """
    catalog = _get_catalog(catalog_path)
    if catalog is None:
        return []

    instruments: list[dict[str, Any]] = []
    try:
        for instr in catalog.instruments():
            iid = str(instr.id) if hasattr(instr, "id") else str(instr)
            bar_types = _discover_bar_types(catalog, iid)
            date_range = _catalog_date_range(catalog, iid)
            instruments.append(
                {
                    "instrument_id": iid,
                    "bar_types": bar_types,
                    "first_bar": date_range.get("first_bar"),
                    "last_bar": date_range.get("last_bar"),
                }
            )
    except Exception as exc:
        logger.warning("Failed to enumerate catalog instruments: %s", exc)

    return instruments


# ---------------------------------------------------------------------------
# GET /api/backtest/catalog/status
# ---------------------------------------------------------------------------


def handle_backtest_catalog_status(
    *,
    catalog_path: str = "data/catalog",
) -> dict[str, Any]:
    """Handle GET /api/backtest/catalog/status — aggregate catalog summary.

    Parameters
    ----------
    catalog_path : str
        Path to the ParquetDataCatalog.

    Returns
    -------
    dict
        ``{"total_instruments": int, "oldest_bar": str|null, "newest_bar": str|null}``

    """
    instruments = handle_backtest_catalog_instruments(catalog_path=catalog_path)

    if not instruments:
        return {
            "total_instruments": 0,
            "oldest_bar": None,
            "newest_bar": None,
        }

    total = len(instruments)

    oldest: str | None = None
    newest: str | None = None
    for inst in instruments:
        fb = inst.get("first_bar")
        lb = inst.get("last_bar")
        if fb and (oldest is None or fb < oldest):
            oldest = fb
        if lb and (newest is None or lb > newest):
            newest = lb

    return {
        "total_instruments": total,
        "oldest_bar": oldest,
        "newest_bar": newest,
    }
