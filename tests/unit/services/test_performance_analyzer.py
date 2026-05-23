"""Unit tests for sam_trader.services.performance_analyzer."""

from __future__ import annotations

import asyncio
import datetime
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam_trader.services.performance_analyzer import (
    _RETURNS_LABEL_MAP,
    PerformanceAnalyzer,
    main,
)


class TestPortfolioAnalyzerIntegration:
    """Tests that Nautilus PortfolioAnalyzer is wired correctly."""

    def test_compute_stats_from_fills(self) -> None:
        """FIFO-matched fills produce non-None Nautilus statistics."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            {
                "trade_id": "t1",
                "strategy_id": "orb-tsla",
                "instrument_id": "TSLA.NASDAQ",
                "side": "BUY",
                "qty": 10.0,
                "price": 150.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            {
                "trade_id": "t2",
                "strategy_id": "orb-tsla",
                "instrument_id": "TSLA.NASDAQ",
                "side": "SELL",
                "qty": 10.0,
                "price": 160.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 2, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
        ]
        stats = analyzer._compute_stats(fills)

        # Returns-based stats should be populated
        for name in _RETURNS_LABEL_MAP.values():
            assert name in stats

        # PnL-based stats should be populated (we had one winning trade)
        assert stats["WinRate"] == 1.0
        assert stats["MaxWinner"] == pytest.approx(98.0, rel=1e-3)
        assert stats["MinWinner"] == pytest.approx(98.0, rel=1e-3)
        assert stats["Expectancy"] == pytest.approx(98.0, rel=1e-3)

    def test_compute_stats_multiple_instruments(self) -> None:
        """FIFO matching isolates instruments correctly."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            # TSLA long
            {
                "trade_id": "t1",
                "instrument_id": "TSLA.NASDAQ",
                "side": "BUY",
                "qty": 10.0,
                "price": 150.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            {
                "trade_id": "t2",
                "instrument_id": "TSLA.NASDAQ",
                "side": "SELL",
                "qty": 10.0,
                "price": 160.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 2, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            # AAPL short
            {
                "trade_id": "t3",
                "instrument_id": "AAPL.NASDAQ",
                "side": "SELL",
                "qty": 5.0,
                "price": 200.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 1, 11, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            {
                "trade_id": "t4",
                "instrument_id": "AAPL.NASDAQ",
                "side": "BUY",
                "qty": 5.0,
                "price": 190.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 2, 11, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
        ]
        stats = analyzer._compute_stats(fills)

        # Two winning trades => WinRate 1.0
        assert stats["WinRate"] == 1.0
        # Total realized PnL = (160-150)*10 - 2 + (200-190)*5 - 2
        #                     = 100 - 2 + 50 - 2 = 146
        assert stats["Expectancy"] == pytest.approx(73.0, rel=1e-3)

    def test_compute_stats_with_partial_fills(self) -> None:
        """Partial fills are matched pro-rata."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            {
                "trade_id": "t1",
                "instrument_id": "TSLA.NASDAQ",
                "side": "BUY",
                "qty": 10.0,
                "price": 150.0,
                "commission": 2.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            {
                "trade_id": "t2",
                "instrument_id": "TSLA.NASDAQ",
                "side": "SELL",
                "qty": 4.0,
                "price": 160.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 2, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
            {
                "trade_id": "t3",
                "instrument_id": "TSLA.NASDAQ",
                "side": "SELL",
                "qty": 6.0,
                "price": 165.0,
                "commission": 1.5,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 3, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
        ]
        trades = analyzer._match_trades(fills)
        assert len(trades) == 2
        # Trade 1: 4 shares, (160-150)*4 - (2*0.4 + 1) = 40 - 1.8 = 38.2
        assert trades[0]["realized_pnl"] == pytest.approx(38.2, rel=1e-3)
        # Trade 2: 6 shares, (165-150)*6 - (2*0.6 + 1.5) = 90 - 2.7 = 87.3
        assert trades[1]["realized_pnl"] == pytest.approx(87.3, rel=1e-3)


class TestStatsStoredToPg:
    """Tests that computed stats are persisted correctly."""

    def test_store_stats_upsert(self) -> None:
        """Stats are inserted (or updated on conflict) into performance_stats."""

        async def _test() -> None:
            analyzer = PerformanceAnalyzer("fake-dsn")
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            stats = {"SharpeRatio": 1.5, "WinRate": 0.6}
            await analyzer._store_stats(mock_pool, "orb-tsla", stats)

            assert mock_conn.execute.call_count == 2
            calls = [call.args for call in mock_conn.execute.call_args_list]
            # First call: SharpeRatio
            assert calls[0][1] == datetime.datetime.now(datetime.timezone.utc).date()
            assert calls[0][2] == "orb-tsla"
            assert calls[0][3] == "SharpeRatio"
            assert calls[0][4] == 1.5
            # Second call: WinRate
            assert calls[0][2] == "orb-tsla"
            assert calls[1][3] == "WinRate"
            assert calls[1][4] == 0.6

        asyncio.run(_test())

    def test_store_stats_nan_converted_to_none(self) -> None:
        """float('nan') is converted to None before inserting."""

        async def _test() -> None:
            analyzer = PerformanceAnalyzer("fake-dsn")
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            stats = {"SharpeRatio": float("nan")}
            await analyzer._store_stats(mock_pool, "orb-tsla", stats)

            call = mock_conn.execute.call_args
            assert call.args[4] is None

        asyncio.run(_test())


class TestEmptyFillsGraceful:
    """Tests graceful degradation when no fills exist."""

    def test_empty_fills_returns_all_none(self) -> None:
        """When fills list is empty, every stat is None."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        stats = analyzer._compute_stats([])
        assert all(v is None for v in stats.values())

    def test_no_matching_trades_returns_all_none(self) -> None:
        """When fills exist but are all same-side (no closes), stats are None."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            {
                "trade_id": "t1",
                "instrument_id": "TSLA.NASDAQ",
                "side": "BUY",
                "qty": 10.0,
                "price": 150.0,
                "commission": 1.0,
                "slippage": 0.0,
                "ts_event": datetime.datetime(
                    2024, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc
                ),
            },
        ]
        stats = analyzer._compute_stats(fills)
        assert all(v is None for v in stats.values())

    def test_compute_and_store_no_strategies(self) -> None:
        """When PG has no strategies, compute_and_store returns {}."""

        async def _test() -> None:
            analyzer = PerformanceAnalyzer("fake-dsn")
            mock_pool = AsyncMock()
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "asyncpg.create_pool",
                return_value=mock_pool,
            ):
                result = await analyzer.compute_and_store(lookback_days=30)

            assert result == {}

        asyncio.run(_test())

    def test_compute_and_store_pg_unavailable(self) -> None:
        """When PG connection fails, compute_and_store returns {} without raising."""

        async def _test() -> None:
            analyzer = PerformanceAnalyzer("fake-dsn")
            with patch(
                "asyncpg.create_pool",
                side_effect=ConnectionError("PG down"),
            ):
                result = await analyzer.compute_and_store(lookback_days=30)

            assert result == {}

        asyncio.run(_test())


class TestMatchTrades:
    """Tests for the FIFO trade matcher."""

    def test_buy_then_sell(self) -> None:
        """Simple long round-trip."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            _make_fill("BUY", 10.0, 100.0, 1.0, "2024-01-01"),
            _make_fill("SELL", 10.0, 110.0, 1.0, "2024-01-02"),
        ]
        trades = analyzer._match_trades(fills)
        assert len(trades) == 1
        # (110 - 100) * 10 - 2 = 98
        assert trades[0]["realized_pnl"] == pytest.approx(98.0, rel=1e-3)

    def test_sell_then_buy(self) -> None:
        """Simple short round-trip."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            _make_fill("SELL", 10.0, 110.0, 1.0, "2024-01-01"),
            _make_fill("BUY", 10.0, 100.0, 1.0, "2024-01-02"),
        ]
        trades = analyzer._match_trades(fills)
        assert len(trades) == 1
        # (110 - 100) * 10 - 2 = 98
        assert trades[0]["realized_pnl"] == pytest.approx(98.0, rel=1e-3)

    def test_unmatched_side_appended(self) -> None:
        """Remaining unmatched quantity becomes a new open lot."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            _make_fill("BUY", 10.0, 100.0, 1.0, "2024-01-01"),
            _make_fill("SELL", 15.0, 110.0, 1.0, "2024-01-02"),
        ]
        trades = analyzer._match_trades(fills)
        assert len(trades) == 1
        assert trades[0]["realized_pnl"] == pytest.approx(98.3333, rel=1e-3)
        # 5 shares remain open (short)

    def test_invalid_side_skipped(self) -> None:
        """Fills with unknown sides are ignored."""
        analyzer = PerformanceAnalyzer("fake-dsn")
        fills = [
            _make_fill("BUY", 10.0, 100.0, 1.0, "2024-01-01"),
            _make_fill("UNKNOWN", 10.0, 110.0, 1.0, "2024-01-02"),
        ]
        trades = analyzer._match_trades(fills)
        assert len(trades) == 0


class TestMainEntryPoint:
    """Tests for the module-level main() function."""

    @patch("sam_trader.services.performance_analyzer.asyncio.run")
    def test_main_parses_args(self, mock_run: Any) -> None:
        """main() parses --lookback-days and delegates to asyncio.run."""
        with patch.object(
            sys, "argv", ["performance_analyzer", "--lookback-days", "30"]
        ):
            main()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fill(
    side: str,
    qty: float,
    price: float,
    commission: float,
    date_str: str,
) -> dict[str, Any]:
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    )
    return {
        "trade_id": f"t-{side}-{date_str}",
        "instrument_id": "TSLA.NASDAQ",
        "side": side,
        "qty": qty,
        "price": price,
        "commission": commission,
        "slippage": 0.0,
        "ts_event": dt,
    }
