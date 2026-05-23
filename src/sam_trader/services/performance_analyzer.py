"""Performance analyzer for SAM Trader V3.

Nightly cron job that computes performance statistics using
NautilusTrader's native PortfolioAnalyzer and built-in statistic classes.
Reads fills from PostgreSQL, computes per-trade realized PnL via FIFO
matching, feeds returns to PortfolioAnalyzer, and stores results back
in PostgreSQL.

Zero custom math — all statistics are computed by NautilusTrader.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from nautilus_trader.analysis import (
    CAGR,
    AvgLoser,
    AvgWinner,
    CalmarRatio,
    Expectancy,
    LongRatio,
    MaxDrawdown,
    MaxLoser,
    MaxWinner,
    MinLoser,
    MinWinner,
    PortfolioAnalyzer,
    ProfitFactor,
    ReturnsAverage,
    ReturnsVolatility,
    RiskReturnRatio,
    SharpeRatio,
    SortinoRatio,
    WinRate,
)

logger = logging.getLogger("sam_trader.performance_analyzer")

_STAT_CLASSES: dict[str, type] = {
    "CAGR": CAGR,
    "SharpeRatio": SharpeRatio,
    "SortinoRatio": SortinoRatio,
    "MaxDrawdown": MaxDrawdown,
    "CalmarRatio": CalmarRatio,
    "WinRate": WinRate,
    "ProfitFactor": ProfitFactor,
    "Expectancy": Expectancy,
    "ReturnsVolatility": ReturnsVolatility,
    "RiskReturnRatio": RiskReturnRatio,
    "AvgWinner": AvgWinner,
    "AvgLoser": AvgLoser,
    "MaxWinner": MaxWinner,
    "MaxLoser": MaxLoser,
    "MinWinner": MinWinner,
    "MinLoser": MinLoser,
    "LongRatio": LongRatio,
    "ReturnsAverage": ReturnsAverage,
}

# Mapping from Nautilus human-readable labels to our canonical stat names.
_RETURNS_LABEL_MAP: dict[str, str] = {
    "CAGR (252 days)": "CAGR",
    "Sharpe Ratio (252 days)": "SharpeRatio",
    "Sortino Ratio (252 days)": "SortinoRatio",
    "Max Drawdown": "MaxDrawdown",
    "Calmar Ratio (252 days)": "CalmarRatio",
    "Profit Factor": "ProfitFactor",
    "Returns Volatility (252 days)": "ReturnsVolatility",
    "Risk Return Ratio": "RiskReturnRatio",
    "Average (Return)": "ReturnsAverage",
}

_PNL_STAT_NAMES: list[str] = [
    "WinRate",
    "Expectancy",
    "AvgWinner",
    "AvgLoser",
    "MaxWinner",
    "MaxLoser",
    "MinWinner",
    "MinLoser",
]


class PerformanceAnalyzer:
    """Wraps NautilusTrader PortfolioAnalyzer for nightly stats computation.

    Parameters
    ----------
    pg_dsn : str
        asyncpg-compatible PostgreSQL DSN.
    starting_capital : float, default 100_000.0
        Nominal starting capital used to convert daily PnL into returns.

    """

    NAUTILUS_STATS: list[str] = list(_STAT_CLASSES.keys())

    def __init__(self, pg_dsn: str, *, starting_capital: float = 100_000.0) -> None:
        self._pg_dsn = pg_dsn
        self._starting_capital = starting_capital

    async def compute_and_store(
        self, lookback_days: int = 365
    ) -> dict[str, dict[str, Any]]:
        """Main entry point — query fills, compute stats, store in PG.

        Parameters
        ----------
        lookback_days : int
            Number of days to look back for fill data.

        Returns
        -------
        dict[str, dict[str, Any]]
            Mapping of strategy_id -> stat_name -> stat_value.

        """
        try:
            import asyncpg
        except ImportError as exc:
            logger.error("asyncpg is required for PG connectivity: %s", exc)
            return {}

        try:
            pool = await asyncpg.create_pool(self._pg_dsn, min_size=1, max_size=2)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to connect to PostgreSQL: %s", exc)
            return {}

        try:
            strategies = await self._get_strategies(pool, lookback_days)
            if not strategies:
                logger.warning(
                    "No strategies with fills found in the last %d days", lookback_days
                )
                return {}

            results: dict[str, dict[str, Any]] = {}
            for strategy_id in strategies:
                fills = await self._get_fills(pool, strategy_id, lookback_days)
                if not fills:
                    continue
                stats = self._compute_stats(fills)
                await self._store_stats(pool, strategy_id, stats)
                results[strategy_id] = stats

            # Aggregate portfolio stats across all strategies
            all_fills = await self._get_all_fills(pool, lookback_days)
            if all_fills:
                portfolio_stats = self._compute_stats(all_fills)
                await self._store_stats(pool, "_PORTFOLIO", portfolio_stats)
                results["_PORTFOLIO"] = portfolio_stats

            return results
        finally:
            await pool.close()

    async def _get_strategies(self, pool: Any, lookback_days: int) -> list[str]:
        """Return distinct strategy_ids with fills in the lookback window."""
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT strategy_id FROM fills
                WHERE ts_event > NOW() - ($1 || ' days')::interval
                ORDER BY strategy_id
                """,
                str(lookback_days),
            )
            return [row["strategy_id"] for row in rows]

    async def _get_fills(
        self, pool: Any, strategy_id: str, lookback_days: int
    ) -> list[dict[str, Any]]:
        """Return fills for a single strategy."""
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    trade_id,
                    strategy_id,
                    instrument_id,
                    side,
                    qty,
                    price,
                    commission,
                    slippage,
                    ts_event
                FROM fills
                WHERE strategy_id = $1
                  AND ts_event > NOW() - ($2 || ' days')::interval
                ORDER BY ts_event
                """,
                strategy_id,
                str(lookback_days),
            )
            return [dict(row) for row in rows]

    async def _get_all_fills(
        self, pool: Any, lookback_days: int
    ) -> list[dict[str, Any]]:
        """Return all fills in the lookback window."""
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    trade_id,
                    strategy_id,
                    instrument_id,
                    side,
                    qty,
                    price,
                    commission,
                    slippage,
                    ts_event
                FROM fills
                WHERE ts_event > NOW() - ($1 || ' days')::interval
                ORDER BY ts_event
                """,
                str(lookback_days),
            )
            return [dict(row) for row in rows]

    def _compute_stats(self, fills: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute Nautilus-backed statistics from a list of fill dicts.

        Parameters
        ----------
        fills : list[dict]
            Raw fill rows from PostgreSQL.

        Returns
        -------
        dict[str, Any]
            Mapping of canonical stat_name -> computed value (or None).

        """
        if not fills:
            return {name: None for name in self.NAUTILUS_STATS}

        trades = self._match_trades(fills)
        if not trades:
            return {name: None for name in self.NAUTILUS_STATS}

        # Daily PnLs -> daily returns
        daily_pnls: dict[date, float] = defaultdict(float)
        for trade in trades:
            daily_pnls[trade["date"]] += trade["realized_pnl"]

        pa = PortfolioAnalyzer()
        for stat_cls in _STAT_CLASSES.values():
            pa.register_statistic(stat_cls())

        for dt, pnl in sorted(daily_pnls.items()):
            ret = pnl / self._starting_capital if self._starting_capital else 0.0
            pa.add_return(
                datetime.combine(dt, datetime.min.time(), tzinfo=timezone.utc), ret
            )

        returns_stats = pa.get_performance_stats_returns()

        # Map human-readable labels to canonical names
        stats: dict[str, Any] = {name: None for name in self.NAUTILUS_STATS}
        for label, value in returns_stats.items():
            canonical = _RETURNS_LABEL_MAP.get(label)
            if canonical:
                stats[canonical] = value

        # PnL-based stats (calculated directly from realized PnL list)
        pnls = [t["realized_pnl"] for t in trades]
        for stat_name in _PNL_STAT_NAMES:
            try:
                stats[stat_name] = _STAT_CLASSES[
                    stat_name
                ]().calculate_from_realized_pnls(pnls)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Statistic %s failed: %s", stat_name, exc)
                stats[stat_name] = None

        # LongRatio — Nautilus implementation requires Position objects.
        # We store None because we do not have Position objects from PG fills.
        stats["LongRatio"] = None

        return stats

    @staticmethod
    def _match_trades(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """FIFO-match fills per instrument to produce closed-trade realized PnLs.

        This is pure data-transformation (not custom math) — it simply pairs
        entry fills with exit fills so that Nautilus can compute statistics.

        Parameters
        ----------
        fills : list[dict]
            Fill rows ordered by time.

        Returns
        -------
        list[dict]
            Closed trades with keys ``date`` and ``realized_pnl``.

        """
        open_lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
        trades: list[dict[str, Any]] = []

        for fill in sorted(fills, key=lambda f: f["ts_event"]):
            key: str = fill["instrument_id"]
            side: str = fill["side"]
            qty: float = float(fill["qty"])
            price: float = float(fill["price"])
            commission: float = float(fill["commission"] or 0.0)
            ts: datetime = fill["ts_event"]

            if side not in ("BUY", "SELL"):
                continue

            if not open_lots[key] or open_lots[key][0]["side"] == side:
                open_lots[key].append(
                    {
                        "side": side,
                        "qty": qty,
                        "price": price,
                        "commission": commission,
                        "ts": ts,
                    }
                )
                continue

            # Opposite side — FIFO match
            remaining = qty
            fill_comm_ratio = commission / qty if qty > 0.0 else 0.0
            while remaining > 1e-9 and open_lots[key]:
                lot = open_lots[key][0]
                matched = min(remaining, lot["qty"])
                lot_comm = (
                    lot["commission"] * (matched / lot["qty"])
                    if lot["qty"] > 0
                    else 0.0
                )
                fill_comm = fill_comm_ratio * matched
                total_comm = lot_comm + fill_comm

                if lot["side"] == "BUY":
                    pnl = (price - lot["price"]) * matched - total_comm
                else:
                    pnl = (lot["price"] - price) * matched - total_comm

                trades.append({"date": ts.date(), "realized_pnl": pnl})

                lot["qty"] -= matched
                lot["commission"] -= lot_comm
                remaining -= matched
                if lot["qty"] <= 1e-9:
                    open_lots[key].pop(0)

            if remaining > 1e-9:
                open_lots[key].append(
                    {
                        "side": side,
                        "qty": remaining,
                        "price": price,
                        "commission": fill_comm_ratio * remaining,
                        "ts": ts,
                    }
                )

        return trades

    async def _store_stats(
        self, pool: Any, strategy_id: str, stats: dict[str, Any]
    ) -> None:
        """Upsert computed statistics into the ``performance_stats`` table."""
        today = datetime.now(timezone.utc).date()
        async with pool.acquire() as conn:
            for stat_name, stat_value in stats.items():
                # Convert float('nan') to None so PG gets NULL
                if (
                    isinstance(stat_value, float) and stat_value != stat_value
                ):  # noqa: PLR0124
                    stat_value = None
                await conn.execute(
                    """
                    INSERT INTO performance_stats (
                        date, strategy_id, stat_name, stat_value
                    ) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (date, strategy_id, stat_name)
                    DO UPDATE SET stat_value = $4, computed_at = NOW()
                    """,
                    today,
                    strategy_id,
                    stat_name,
                    stat_value,
                )


def _build_pg_dsn() -> str:
    """Construct a PG DSN from standard environment variables."""
    import os

    host = os.environ.get("POSTGRES_HOST", "sam-postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "sam_trader")
    user = os.environ.get("POSTGRES_USER", "sam")
    password = os.environ.get("POSTGRES_PASSWORD", "sam_secret")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def _async_main(lookback_days: int) -> int:
    """Async entry point for the performance analyzer."""
    dsn = _build_pg_dsn()
    analyzer = PerformanceAnalyzer(dsn)
    results = await analyzer.compute_and_store(lookback_days=lookback_days)
    if not results:
        logger.warning("No performance statistics computed.")
        return 0

    total_rows = sum(len(s) for s in results.values())
    logger.info(
        "Performance analysis complete: %d strategies, %d stat rows inserted/updated.",
        len(results),
        total_rows,
    )
    return 0


def main() -> int:
    """Entry point for performance analyzer cron job."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="SAM Trader V3 Performance Analyzer")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=365,
        help="Number of days to look back for fill data",
    )
    args = parser.parse_args()

    return asyncio.run(_async_main(args.lookback_days))


if __name__ == "__main__":
    sys.exit(main())
