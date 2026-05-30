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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.bundle_loader import load_bundles
from sam_trader.services.backtest.engine import (
    BacktestEngineError,
    BacktestEngineWrapper,
)
from sam_trader.services.backtest.results import BacktestResultStore, _build_pg_dsn
from sam_trader.services.backtest.sweep import ParameterSweep, parse_sweep_flags
from sam_trader.services.backtest.walk_forward import WalkForward, parse_days_flag

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


def _discover_instruments_from_filesystem(catalog_path: str) -> list[dict[str, Any]]:
    """Scan bar directory to discover instruments when catalog.instruments() fails.

    Nautilus ``ParquetDataCatalog.instruments()`` cannot discover instruments
    when only bar parquet files exist (no instrument metadata).  This helper
    scans ``<catalog_path>/data/bar/`` and extracts unique instrument IDs
    from filenames (e.g. ``AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet``)
    or directory names.

    Parameters
    ----------
    catalog_path : str
        Path to the ParquetDataCatalog root.

    Returns
    -------
    list[dict[str, Any]]
        Each entry: ``instrument_id``, ``bar_types``, ``first_bar``,
        ``last_bar`` (dates are ``None`` — caller should populate via
        :func:`_catalog_date_range`).

    """
    bar_dir = Path(catalog_path) / "data" / "bar"
    if not bar_dir.exists():
        return []

    instruments: dict[str, set[str]] = {}  # instrument_id -> {bar_types}
    for entry in bar_dir.iterdir():
        if entry.is_file() and entry.suffix != ".parquet":
            continue
        name = entry.stem if entry.is_file() else entry.name
        if not name:
            continue
        parts = name.split("-")
        # Try progressively longer prefixes to find the shortest valid
        # instrument ID (e.g. AAPL.NASDAQ before AAPL.NASDAQ-5-MINUTE...).
        for i in range(1, len(parts)):
            candidate = "-".join(parts[:i])
            try:
                InstrumentId.from_str(candidate)
                bar_type = "-".join(parts[i:])
                if bar_type:
                    instruments.setdefault(candidate, set()).add(bar_type)
                break
            except ValueError:
                continue

    return [
        {
            "instrument_id": iid,
            "bar_types": sorted(bar_types),
            "first_bar": None,
            "last_bar": None,
        }
        for iid, bar_types in sorted(instruments.items())
    ]


def _discover_bar_types(catalog: ParquetDataCatalog, instrument_id: str) -> list[str]:
    """Discover available bar types for an instrument by scanning catalog files.

    Parquet filenames in the Nautilus catalog are full bar type strings,
    e.g. ``TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL.parquet``.  The returned
    values are the complete bar type strings (including the instrument
    ID prefix) because that is what Nautilus ``BacktestDataConfig``
    expects.

    Parameters
    ----------
    catalog : ParquetDataCatalog
        The catalog whose ``data/bar`` directory is scanned.
    instrument_id : str
        Instrument ID to filter on (e.g. ``"TSLA.NASDAQ"``).

    Returns
    -------
    list[str]
        Sorted, de-duplicated full bar type strings for the instrument.

    """
    bar_types: list[str] = []
    try:
        bar_data_dir = Path(catalog.path) / "data" / "bar"
        if bar_data_dir.exists():
            prefix = f"{instrument_id}-"
            for f in bar_data_dir.rglob("*.parquet"):
                parent = f.parent.name
                if parent.startswith(prefix):
                    bar_types.append(parent)
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


def _filesystem_date_range(
    catalog_path: str, instrument_id: str, bar_type: str
) -> tuple[str | None, str | None]:
    """Extract first/last bar dates from parquet filenames in the filesystem.

    Parquet files are named like:
    ``2023-05-31T09-35-00-..._2026-05-29T16-00-00-....parquet``
    The first 10 chars of each timestamp segment are the date (YYYY-MM-DD).
    """
    bar_dir = Path(catalog_path) / "data" / "bar"
    if not bar_dir.exists():
        return None, None

    first_date: str | None = None
    last_date: str | None = None

    for entry in bar_dir.iterdir():
        if not entry.is_dir():
            continue
        if instrument_id not in entry.name:
            continue
        for f in entry.rglob("*.parquet"):
            stem = f.stem
            parts = stem.split("_")
            if len(parts) >= 2:
                d1 = parts[0][:10]
                d2 = parts[1][:10]
                if first_date is None or d1 < first_date:
                    first_date = d1
                if last_date is None or d2 > last_date:
                    last_date = d2
    return first_date, last_date


# ---------------------------------------------------------------------------
# Strategy lookup helpers
# ---------------------------------------------------------------------------


def _lookup_strategies_from_bundles(
    strategy_id: str,
    bundles_path: str = "config/bundles.yaml",
) -> list[ImportableStrategyConfig] | None:
    """Look up strategy configs from bundles YAML by bundle id.

    Parameters
    ----------
    strategy_id : str
        The bundle ``id`` field to match (e.g. ``"orb-aggressive-tsla"``).
        Also matches against the auto-generated ``strategy_id`` config key
        (``{market}-{bundle_id}``) for backward compatibility.
    bundles_path : str
        Path to the bundles YAML file.

    Returns
    -------
    list[ImportableStrategyConfig] | None
        Matching strategy configs, or ``None`` if the file or bundle is
        not found.

    """
    p = Path(bundles_path)
    if not p.exists():
        logger.warning("Bundles file not found: %s", bundles_path)
        return None

    try:
        all_bundles = load_bundles(bundles_path)
    except Exception as exc:
        logger.warning("Failed to load bundles from %s: %s", bundles_path, exc)
        return None

    # Match by bundle_id (stored in config dict) or strategy_id (market-bundle_id)
    matches: list[ImportableStrategyConfig] = []
    for b in all_bundles:
        cfg = b.config
        if cfg.get("bundle_id") == strategy_id or cfg.get("strategy_id") == strategy_id:
            matches.append(b)

    if not matches:
        logger.warning(
            "Bundle not found for strategy_id=%r in %s",
            strategy_id,
            bundles_path,
        )
        return None

    return matches


def _build_strategy_from_body(body: dict[str, Any]) -> ImportableStrategyConfig | None:
    """Build a single ImportableStrategyConfig from POST body fields.

    Supports direct strategy specification without needing a bundles file:

    - ``strategy_path`` (str, required) — dotted class path
    - ``config_path`` (str, required) — dotted config class path
    - ``config`` (dict, optional) — strategy parameter overrides

    Returns ``None`` if ``strategy_path`` is not present in the body.
    """
    strategy_path: str | None = body.get("strategy_path")
    if not strategy_path:
        return None

    config_path: str = body.get(
        "config_path"
    ) or _derive_config_path_from_strategy_path(strategy_path)
    config: dict[str, Any] = dict(body.get("config", {}))

    return ImportableStrategyConfig(
        strategy_path=strategy_path,
        config_path=config_path,
        config=config,
    )


def _derive_config_path_from_strategy_path(strategy_path: str) -> str:
    """Derive config class path from strategy class path.

    ``sam_trader.strategies.orb:OrbStrategy`` →
    ``sam_trader.strategies.orb:OrbStrategyConfig``
    """
    module, class_name = strategy_path.split(":", 1)
    return f"{module}:{class_name}Config"


def _parse_sweep_body(body: dict[str, Any]) -> dict[str, list]:
    """Parse sweep parameters from POST body into a parameter grid.

    Supports two formats:

    1. ``sweep_flags`` (list[str]) — CLI-style flags::

           ["stop_loss_ticks=5,10,15", "take_profit_ticks=20,30"]

    2. ``sweep_params`` (dict[str, list]) — pre-parsed grid::

           {"stop_loss_ticks": [5, 10, 15], "take_profit_ticks": [20, 30]}

    Returns an empty dict when neither field is present.

    """
    flags: list[str] = body.get("sweep_flags", [])
    if flags:
        try:
            return parse_sweep_flags(flags)
        except ValueError as exc:
            logger.warning("Invalid sweep_flags in body: %s", exc)
            return {}

    params: dict[str, list] = body.get("sweep_params", {})
    if isinstance(params, dict):
        return params

    return {}


def _resolve_strategies(
    body: dict[str, Any],
    bundles_path: str = "config/bundles.yaml",
) -> tuple[list[ImportableStrategyConfig] | None, str | None]:
    """Resolve strategy configs from a POST body.

    Resolution order:
    1. If ``strategy_path`` is in the body, build an ImportableStrategyConfig
       directly from body fields.
    2. If ``strategy_id`` is in the body, look it up from ``bundles.yaml``.

    Returns
    -------
    tuple[list[ImportableStrategyConfig] | None, str | None]
        (strategies, error_string).  If error_string is not None,
        strategies will be None.

    """
    # 1. Direct strategy path
    direct = _build_strategy_from_body(body)
    if direct is not None:
        return [direct], None

    # 2. Bundle lookup
    strategy_id: str = body.get("strategy_id", "")
    if strategy_id:
        bundles = _lookup_strategies_from_bundles(
            strategy_id, bundles_path=bundles_path
        )
        if bundles is None:
            return None, f"Strategy not found in bundles: {strategy_id!r}"
        return bundles, None

    # Neither provided
    return None, "Missing required field: strategy_id (or strategy_path + config_path)"


# ---------------------------------------------------------------------------
# POST /api/backtest/run
# ---------------------------------------------------------------------------


def _run_backtest_in_thread(
    run_id: str,
    catalog_path: str,
    strategies: list[dict[str, Any]],
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

        # Rebuild ImportableStrategyConfig objects from serialisable dicts.
        # (Threads share memory, but passing dicts is more explicit.)
        strategy_configs: list[ImportableStrategyConfig] = []
        for s in strategies:
            strategy_configs.append(
                ImportableStrategyConfig(
                    strategy_path=s["strategy_path"],
                    config_path=s["config_path"],
                    config=s["config"],
                )
            )

        # Phase 1 — build and run
        wrapper = BacktestEngineWrapper(catalog_path=catalog_path)
        try:
            result: BacktestResult = wrapper.run(
                strategies=strategy_configs,
                instrument_ids=instrument_ids,
                bar_types=bar_types,
                start=start,
                end=end,
            )
        except BacktestEngineError as exc:
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


def _run_walk_forward_in_thread(
    run_id: str,
    catalog_path: str,
    strategies: list[dict[str, Any]],
    strategy_id: str,
    instrument_ids: list[str],
    bar_types: list[str],
    start: str,
    end: str,
    train_days: int,
    test_days: int,
    param_grid: dict[str, list],
    pg_dsn: str,
) -> None:
    """Run walk-forward optimisation in a background thread and persist results."""
    import time

    try:
        _update_run_status(run_id, "running", progress_pct=0)

        # Rebuild ImportableStrategyConfig objects
        strategy_configs: list[ImportableStrategyConfig] = []
        for s in strategies:
            strategy_configs.append(
                ImportableStrategyConfig(
                    strategy_path=s["strategy_path"],
                    config_path=s["config_path"],
                    config=s["config"],
                )
            )

        wrapper = BacktestEngineWrapper(catalog_path=catalog_path)
        wf = WalkForward(
            wrapper=wrapper,
            base_strategies=strategy_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            train_days=train_days,
            test_days=test_days,
            data_start=start,
            data_end=end,
        )

        _update_run_status(run_id, "running", progress_pct=50)

        t0 = time.monotonic()
        try:
            result = wf.run(param_grid=param_grid)
        except Exception as exc:
            logger.exception("Walk-forward %s failed during run", run_id)
            _update_run_status(run_id, "failed", error=str(exc))
            return
        elapsed = time.monotonic() - t0

        _update_run_status(run_id, "running", progress_pct=80)

        # Persist
        store = BacktestResultStore(pg_dsn=pg_dsn)
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                window_dicts = [
                    {
                        "train_start": w.train_start,
                        "train_end": w.train_end,
                        "test_start": w.test_start,
                        "test_end": w.test_end,
                        "best_params": w.best_params,
                        "train_sharpe": w.train_sharpe,
                        "test_sharpe": w.test_sharpe,
                        "test_pnl": w.test_pnl,
                        "test_win_rate": w.test_win_rate,
                        "test_max_dd": w.test_max_dd,
                        "test_trades": w.test_trades,
                        "error": w.error,
                    }
                    for w in result.windows
                ]
                loop.run_until_complete(
                    store.save_walk_forward(
                        run_id=run_id,
                        strategy_id=strategy_id,
                        instrument_id=instrument_ids[0],
                        bar_type=bar_types[0] if bar_types else "",
                        start_date=date.fromisoformat(start),
                        end_date=date.fromisoformat(end),
                        overall_sharpe=result.overall_sharpe,
                        overall_pnl=result.overall_pnl,
                        profitable_windows=result.profitable_windows,
                        total_windows=result.total_windows,
                        param_stability=result.param_stability,
                        window_results=window_dicts,
                        elapsed_secs=round(elapsed, 2),
                        strategy_family=(
                            strategy_configs[0].config.get("family")
                            if strategy_configs
                            else None
                        ),
                    )
                )
            finally:
                loop.close()
        except Exception as exc:
            logger.exception("Failed to persist walk-forward result run_id=%s", run_id)
            _update_run_status(run_id, "failed", error=f"Persistence error: {exc}")
            return

        # Extract stats for registry
        stats = {
            "overall_sharpe": result.overall_sharpe,
            "overall_pnl": result.overall_pnl,
            "profitable_windows": result.profitable_windows,
            "total_windows": result.total_windows,
            "param_stability": result.param_stability,
        }
        _update_run_status(run_id, "completed", progress_pct=100, result=stats)

    except Exception as exc:
        logger.exception("Walk-forward %s failed with unexpected error", run_id)
        _update_run_status(run_id, "failed", error=str(exc))


def _run_sweep_in_thread(
    run_id: str,
    catalog_path: str,
    strategies: list[dict[str, Any]],
    strategy_id: str,
    instrument_ids: list[str],
    bar_types: list[str],
    start: str,
    end: str,
    param_grid: dict[str, list],
    pg_dsn: str,
) -> None:
    """Run a parameter sweep in a background thread and persist results."""
    import time

    try:
        _update_run_status(run_id, "running", progress_pct=0)

        # Rebuild ImportableStrategyConfig objects
        strategy_configs: list[ImportableStrategyConfig] = []
        for s in strategies:
            strategy_configs.append(
                ImportableStrategyConfig(
                    strategy_path=s["strategy_path"],
                    config_path=s["config_path"],
                    config=s["config"],
                )
            )

        wrapper = BacktestEngineWrapper(catalog_path=catalog_path)
        sweeper = ParameterSweep(
            wrapper=wrapper,
            base_strategies=strategy_configs,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start=start,
            end=end,
        )

        _update_run_status(run_id, "running", progress_pct=50)

        t0 = time.monotonic()
        try:
            sweep_results = sweeper.run(param_grid=param_grid)
        except Exception as exc:
            logger.exception("Sweep %s failed during run", run_id)
            _update_run_status(run_id, "failed", error=str(exc))
            return
        elapsed = time.monotonic() - t0

        _update_run_status(run_id, "running", progress_pct=80)

        # Persist
        store = BacktestResultStore(pg_dsn=pg_dsn)
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    store.save_sweep(
                        run_id=run_id,
                        strategy_id=strategy_id,
                        instrument_id=instrument_ids[0],
                        bar_type=bar_types[0] if bar_types else "",
                        start_date=date.fromisoformat(start),
                        end_date=date.fromisoformat(end),
                        sweep_results=sweep_results,
                        elapsed_secs=round(elapsed, 2),
                        strategy_family=(
                            strategy_configs[0].config.get("family")
                            if strategy_configs
                            else None
                        ),
                    )
                )
            finally:
                loop.close()
        except Exception as exc:
            logger.exception("Failed to persist sweep result run_id=%s", run_id)
            _update_run_status(run_id, "failed", error=f"Persistence error: {exc}")
            return

        # Extract stats for registry
        stats = {
            "sweep_result_count": len(sweep_results),
            "top_sharpe": sweep_results[0].get("sharpe") if sweep_results else None,
            "top_pnl": sweep_results[0].get("net_pnl") if sweep_results else None,
            "top_params": sweep_results[0].get("combo") if sweep_results else None,
        }
        _update_run_status(run_id, "completed", progress_pct=100, result=stats)

    except Exception as exc:
        logger.exception("Sweep %s failed with unexpected error", run_id)
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


# Mapping of Nautilus canonical stat names (v1.227.0) to dashboard output keys.
# See nautilus_trader.core.nautilus_pyo3 stat .name attributes.
_RETURNS_STAT_KEY_MAP: dict[str, str] = {
    "Sharpe Ratio (252 days)": "sharpe_ratio",
    "Sortino Ratio (252 days)": "sortino_ratio",
    "Max Drawdown": "max_drawdown",
    "Win Rate": "win_rate",
    "Profit Factor": "profit_factor",
    "Expectancy": "expectancy",
    "CAGR (252 days)": "cagr",
    "Calmar Ratio (252 days)": "calmar_ratio",
    "Returns Volatility (252 days)": "volatility",
}


def _extract_result_stats(result: BacktestResult) -> dict[str, Any]:
    """Extract human-readable stats from a :class:`BacktestResult`.

    Returns stats use mapped keys from the Nautilus canonical stat names
    (e.g. ``"Sharpe Ratio (252 days)"`` → ``"sharpe_ratio"``).
    ``total_pnl`` is read from :attr:`BacktestResult.stats_pnls` (key
    ``"PnL (total)"``, summed across all currencies), **not** from
    ``stats_returns``.
    """
    stats: dict[str, Any] = {}
    try:
        if result.stats_returns:
            sr = result.stats_returns
            if isinstance(sr, dict):
                for nautilus_key, output_key in _RETURNS_STAT_KEY_MAP.items():
                    stats[output_key] = sr.get(nautilus_key)
    except Exception:
        pass
    try:
        # total_pnl lives in stats_pnls (per-currency/per-strategy dict of dicts).
        # Key name from Nautilus PortfolioAnalyzer.get_performance_stats_pnls().
        if result.stats_pnls:
            sp = result.stats_pnls
            if isinstance(sp, dict):
                total_pnl: float | None = None
                for inner in sp.values():
                    if isinstance(inner, dict):
                        pnl = inner.get("PnL (total)")
                        if pnl is not None:
                            total_pnl = pnl if total_pnl is None else total_pnl + pnl
                stats["total_pnl"] = total_pnl
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
    bundles_path: str = "config/bundles.yaml",
    pg_dsn: str | None = None,
) -> dict[str, Any]:
    """Handle POST /api/backtest/run — launch an asynchronous backtest.

    Expects JSON body with one of these strategy specification methods:

    **Via bundles.yaml lookup** (recommended):

    - ``strategy_id`` (str, required) — bundle ``id`` field to look up

    **Via direct config** (for ad-hoc strategies):

    - ``strategy_path`` (str, required) — dotted class path
    - ``config_path`` (str, optional) — dotted config class path;
      auto-derived from ``strategy_path`` if omitted
    - ``config`` (dict, optional) — strategy parameter overrides

    **Common fields**:

    - ``instrument_ids`` (list[str], required) — instruments to use
    - ``bar_types`` (list[str], optional) — defaults to auto-discover
    - ``start`` (str, required) — ISO date
    - ``end`` (str, required) — ISO date

    **Walk-forward mode** (optional):

    - ``walk_forward`` (bool) — enable walk-forward optimisation
    - ``train_days`` (int) — training window in days
    - ``test_days`` (int) — test window in days
    - ``sweep_flags`` (list[str]) — parameter sweep flags

    **Parameter sweep mode** (optional):

    - ``sweep_flags`` (list[str]) — parameter sweep flags
    - ``sweep_params`` (dict[str, list]) — pre-parsed parameter grid

    Returns
    -------
    dict
        ``{"run_id": str, "status": "started", "mode":
        "backtest"|"walk_forward"|"sweep"}``

    """
    strategy_id: str = body.get("strategy_id", "")
    instrument_ids: list[str] = body.get("instrument_ids", [])
    bar_types: list[str] = body.get("bar_types", [])
    start: str = body.get("start", "")
    end: str = body.get("end", "")
    walk_forward: bool = body.get("walk_forward", False)

    # Resolve strategy config(s)
    strategies, strategy_error = _resolve_strategies(body, bundles_path=bundles_path)
    if strategies is None:
        return {"error": strategy_error or "Unable to resolve strategy configuration"}

    if not instrument_ids:
        return {"error": "Missing required field: instrument_ids"}
    if not start or not end:
        return {"error": "Missing required fields: start, end"}

    # Use strategy_id from body or derive from first strategy config
    if not strategy_id:
        first_config = strategies[0].config
        strategy_id = first_config.get(
            "strategy_id", first_config.get("bundle_id", "unknown")
        )

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

    # Serialise ImportableStrategyConfig objects to dicts for thread transfer
    strategies_serialised: list[dict[str, Any]] = []
    for s in strategies:
        strategies_serialised.append(
            {
                "strategy_path": s.strategy_path,
                "config_path": s.config_path,
                "config": dict(s.config),
            }
        )

    param_grid = _parse_sweep_body(body)

    if walk_forward:
        train_days_raw: str | int = body.get("train_days", 90)
        test_days_raw: str | int = body.get("test_days", 30)
        try:
            train_days = (
                int(train_days_raw)
                if isinstance(train_days_raw, int)
                else parse_days_flag(str(train_days_raw))
            )
            test_days = (
                int(test_days_raw)
                if isinstance(test_days_raw, int)
                else parse_days_flag(str(test_days_raw))
            )
        except ValueError as exc:
            return {"error": f"Invalid walk-forward days: {exc}"}

        if not param_grid:
            return {
                "error": "Walk-forward requires sweep parameters. "
                "Provide sweep_flags or sweep_params in the body."
            }

        thread = threading.Thread(
            target=_run_walk_forward_in_thread,
            args=(
                run_id,
                catalog_path,
                strategies_serialised,
                strategy_id,
                instrument_ids,
                bar_types,
                start,
                end,
                train_days,
                test_days,
                param_grid,
                dsn,
            ),
            daemon=True,
            name=f"bt-{run_id}",
        )
        thread.start()
        return {"run_id": run_id, "status": "started", "mode": "walk_forward"}

    if param_grid:
        thread = threading.Thread(
            target=_run_sweep_in_thread,
            args=(
                run_id,
                catalog_path,
                strategies_serialised,
                strategy_id,
                instrument_ids,
                bar_types,
                start,
                end,
                param_grid,
                dsn,
            ),
            daemon=True,
            name=f"bt-{run_id}",
        )
        thread.start()
        return {"run_id": run_id, "status": "started", "mode": "sweep"}

    thread = threading.Thread(
        target=_run_backtest_in_thread,
        args=(
            run_id,
            catalog_path,
            strategies_serialised,
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

    return {"run_id": run_id, "status": "started", "mode": "backtest"}


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

    # Build comparison table: one row per metric, columns per run.
    # Metrics are grouped by source: stats_returns (canonical Nautilus names),
    # stats_pnls (per-currency/per-strategy P&L), and scalar row columns.
    _COMPARE_RETURNS_METRICS: list[tuple[str, str]] = [
        ("Sharpe Ratio (252 days)", "sharpe_ratio"),
        ("Sortino Ratio (252 days)", "sortino_ratio"),
        ("Max Drawdown", "max_drawdown"),
        ("Win Rate", "win_rate"),
        ("Profit Factor", "profit_factor"),
        ("Expectancy", "expectancy"),
        ("CAGR (252 days)", "cagr"),
        ("Calmar Ratio (252 days)", "calmar_ratio"),
        ("Returns Volatility (252 days)", "volatility"),
    ]
    _COMPARE_PNLS_METRICS: list[tuple[str, str]] = [
        ("PnL (total)", "total_pnl"),
    ]
    _COMPARE_SCALAR_METRICS: list[str] = [
        "total_events",
        "total_orders",
        "total_positions",
        "elapsed_secs",
    ]

    comparison: list[dict[str, Any]] = []

    # Returns-based metrics (from stats_returns JSONB column)
    for nautilus_key, output_key in _COMPARE_RETURNS_METRICS:
        returns_row: dict[str, Any] = {"metric": output_key}
        for rid in run_ids:
            r_data = runs.get(rid, {})
            if isinstance(r_data, dict) and "error" not in r_data:
                sr = r_data.get("stats_returns") or {}
                returns_row[rid] = (
                    sr.get(nautilus_key) if isinstance(sr, dict) else None
                )
            else:
                returns_row[rid] = None
        comparison.append(returns_row)

    # PnL-based metrics (from stats_pnls JSONB column — summed across currencies)
    for pnl_key, output_key in _COMPARE_PNLS_METRICS:
        pnls_row: dict[str, Any] = {"metric": output_key}
        for rid in run_ids:
            r_data = runs.get(rid, {})
            if isinstance(r_data, dict) and "error" not in r_data:
                sp = r_data.get("stats_pnls") or {}
                if isinstance(sp, dict):
                    val: float | None = None
                    for inner in sp.values():
                        if isinstance(inner, dict):
                            v = inner.get(pnl_key)
                            if v is not None:
                                val = v if val is None else val + v
                    pnls_row[rid] = val
                else:
                    pnls_row[rid] = None
            else:
                pnls_row[rid] = None
        comparison.append(pnls_row)

    # Scalar metrics (direct row columns)
    for metric in _COMPARE_SCALAR_METRICS:
        scalar_row: dict[str, Any] = {"metric": metric}
        for rid in run_ids:
            r_data = runs.get(rid, {})
            if isinstance(r_data, dict) and "error" not in r_data:
                scalar_row[rid] = r_data.get(metric)
            else:
                scalar_row[rid] = None
        comparison.append(scalar_row)

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
    catalog_instruments: list[Any] = []
    try:
        catalog_instruments = list(catalog.instruments())
    except Exception as exc:
        logger.warning("Failed to enumerate catalog instruments: %s", exc)

    if catalog_instruments:
        try:
            for instr in catalog_instruments:
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
            logger.warning("Failed to process catalog instruments: %s", exc)
    else:
        # Fallback: discover from filesystem when catalog API returns empty
        instruments = _discover_instruments_from_filesystem(catalog_path)
        for inst in instruments:
            iid = inst["instrument_id"]
            bar_types = _discover_bar_types(catalog, iid)
            date_range = _catalog_date_range(catalog, iid)
            inst["bar_types"] = bar_types
            # Filesystem date range fallback when catalog queries return None
            if date_range.get("first_bar") is None and date_range.get("last_bar") is None:
                first_bar = None
                last_bar = None
                for bt in bar_types:
                    fs_first, fs_last = _filesystem_date_range(catalog_path, iid, bt)
                    if first_bar is None or (fs_first is not None and fs_first < str(first_bar)):
                        first_bar = fs_first
                    if last_bar is None or (fs_last is not None and fs_last > str(last_bar)):
                        last_bar = fs_last
                inst["first_bar"] = first_bar
                inst["last_bar"] = last_bar
            else:
                inst["first_bar"] = date_range.get("first_bar")
                inst["last_bar"] = date_range.get("last_bar")

    return instruments


# ---------------------------------------------------------------------------
# GET /api/backtest/runs/<id>/panels
# ---------------------------------------------------------------------------


def handle_backtest_run_panels(
    run_id: str,
    *,
    pg_dsn: str | None = None,
) -> dict[str, Any]:
    """Handle GET /api/backtest/runs/<id>/panels — WF/sweep display data.

    Returns structured data for the walk-forward or sweep display panels.
    Detects the mode from the ``tags`` JSONB column and returns
    appropriately formatted data for the frontend.

    Parameters
    ----------
    run_id : str
        The run_id to look up in PG.
    pg_dsn : str | None
        PostgreSQL DSN.

    Returns
    -------
    dict
        For walk-forward runs: ``mode``, ``walk_forward`` (summary, windows,
        param_stability).
        For sweep runs: ``mode``, ``sweep`` (ranked results list).
        For plain backtest runs: ``mode`` = ``"backtest"`` (no panels).
        On error: ``error`` key with message.

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
        logger.warning("Failed to query panels for run_id=%s: %s", run_id, exc)
        return {"error": f"Database error: {exc}"}

    if row is None:
        return {"error": f"Run not found: {run_id}"}

    tags: dict[str, Any] = row.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}

    mode: str = tags.get("mode", "backtest")
    stats_returns: dict[str, Any] = row.get("stats_returns") or {}
    stats_pnls: dict[str, Any] = row.get("stats_pnls") or {}

    # --- Walk-Forward panel ---
    if mode == "walk_forward":
        window_results: list[dict[str, Any]] = tags.get("window_results") or []

        # Summary
        overall_sharpe = stats_returns.get("overall_sharpe")
        overall_pnl = stats_pnls.get("overall_pnl")
        profitable_windows = stats_returns.get("profitable_windows", 0)
        total_windows = stats_returns.get("total_windows", 0)

        # Per-window table rows
        windows: list[dict[str, Any]] = []
        for w in window_results:
            windows.append(
                {
                    "train_start": w.get("train_start"),
                    "train_end": w.get("train_end"),
                    "test_start": w.get("test_start"),
                    "test_end": w.get("test_end"),
                    "best_params": w.get("best_params"),
                    "train_sharpe": w.get("train_sharpe"),
                    "test_sharpe": w.get("test_sharpe"),
                    "test_pnl": w.get("test_pnl"),
                    "test_win_rate": w.get("test_win_rate"),
                    "test_max_dd": w.get("test_max_dd"),
                    "test_trades": w.get("test_trades"),
                    "error": w.get("error"),
                }
            )

        # Parameter stability
        param_stability: dict[str, Any] = tags.get("param_stability") or {}

        return {
            "mode": "walk_forward",
            "walk_forward": {
                "summary": {
                    "overall_sharpe": overall_sharpe,
                    "overall_pnl": overall_pnl,
                    "profitable_windows": profitable_windows,
                    "total_windows": total_windows,
                },
                "windows": windows,
                "param_stability": param_stability,
            },
        }

    # --- Sweep panel ---
    if mode == "sweep":
        sweep_results: list[dict[str, Any]] = tags.get("sweep_results") or []

        # Build ranked table rows
        ranked: list[dict[str, Any]] = []
        for i, sr in enumerate(sweep_results):
            ranked.append(
                {
                    "rank": i + 1,
                    "combo": sr.get("combo"),
                    "sharpe": sr.get("sharpe"),
                    "net_pnl": sr.get("net_pnl"),
                    "win_rate": sr.get("win_rate"),
                    "max_dd": sr.get("max_dd"),
                    "total_trades": sr.get("total_trades"),
                    "profit_factor": sr.get("profit_factor"),
                    "expectancy": sr.get("expectancy"),
                }
            )

        return {
            "mode": "sweep",
            "sweep": {
                "results": ranked,
                "total_combinations": len(ranked),
            },
        }

    # Plain backtest — no panels
    return {"mode": "backtest", "walk_forward": None, "sweep": None}


# ---------------------------------------------------------------------------
# GET /api/backtest/catalog/strategies
# ---------------------------------------------------------------------------


def handle_backtest_catalog_strategies(
    bundles_path: str = "config/bundles.yaml",
) -> list[dict[str, Any]]:
    """Handle GET /api/backtest/catalog/strategies — list enabled bundles.

    Parameters
    ----------
    bundles_path : str
        Path to the bundles YAML file.

    Returns
    -------
    list[dict]
        Each entry: ``bundle_id``, ``strategy_path``, ``instrument_id``,
        ``venue``, ``market``, ``family``, ``enabled``.
        Only bundles with ``enabled: true`` are returned.
        Returns an empty list when the file is missing, empty, or
        cannot be parsed.

    """
    try:
        bundles = load_bundles(bundles_path)
    except Exception as exc:
        logger.warning("Failed to load bundles for catalog: %s", exc)
        return []

    result: list[dict[str, Any]] = []
    for b in bundles:
        cfg = b.config
        result.append(
            {
                "bundle_id": cfg.get("bundle_id", "unknown"),
                "strategy_path": b.strategy_path,
                "instrument_id": cfg.get("instrument_id"),
                "venue": cfg.get("venue"),
                "market": cfg.get("market", "US"),
                "family": cfg.get("family"),
                "enabled": True,
            }
        )
    return result


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
        ``{"total_instruments": int, "oldest_bar": str|null,
        "newest_bar": str|null, "catalog_exists": bool, "message": str|null}``

    """
    catalog = _get_catalog(catalog_path)
    if catalog is None:
        return {
            "total_instruments": 0,
            "oldest_bar": None,
            "newest_bar": None,
            "catalog_exists": False,
            "message": "Catalog directory not found. Create it with sam download-bars.",
        }

    instruments = handle_backtest_catalog_instruments(catalog_path=catalog_path)

    if not instruments:
        return {
            "total_instruments": 0,
            "oldest_bar": None,
            "newest_bar": None,
            "catalog_exists": True,
            "message": (
                "No historical data found. Download bars first — use sam download-bars."
            ),
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
        "catalog_exists": True,
        "message": None,
    }
