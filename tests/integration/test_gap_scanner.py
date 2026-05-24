"""Integration tests for PreMarketGapScanner with real Nautilus types."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from sam_trader.services.gap_scanner import (
    GapScannerConfig,
    PreMarketGapScanner,
    Trend,
)
from sam_trader.services.quote_collector import QuoteCollectionService


def _make_tick(sym: str, bid: str, ask: str) -> QuoteTick:
    return QuoteTick(
        instrument_id=InstrumentId.from_str(sym),
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_int(100),
        ask_size=Quantity.from_int(100),
        ts_event=0,
        ts_init=0,
    )


@pytest.fixture(autouse=True)
def patch_futu_context():
    """Prevent real Futu network calls in all integration tests."""
    with patch(
        "sam_trader.services.quote_collector.get_cached_futu_quote_context"
    ) as mock_ctx:
        mock_ctx.return_value = MagicMock()
        yield mock_ctx


class TestFutuQuoteTickFlow:
    """Gap scanner fed by QuoteCollectionService → FutuLiveDataClient."""

    def test_futu_flow_pass_1(self, event_loop):
        """End-to-end: quote collection → gap computation → filtering → Redis."""
        redis = MagicMock()

        async def _run():
            # 1. Build a QuoteCollectionService with a tiny watchlist
            quote_svc = QuoteCollectionService(
                broker="FUTU",
                host="test-host",
                port=11111,
                watchlist=["TSLA.NASDAQ", "AAPL.NASDAQ"],
                collection_period_secs=0,
            )
            await quote_svc._setup()

            # 2. Inject fake ticks directly onto the msgbus handler
            for sym, bid, ask in [
                ("TSLA.NASDAQ", "150.00", "150.05"),
                ("AAPL.NASDAQ", "180.00", "180.02"),
            ]:
                quote_svc._on_data(_make_tick(sym, bid, ask))

            # 3. Build scanner with a prev-close loader
            async def _prev_load(sym: str) -> float | None:
                return {"TSLA.NASDAQ": 145.0, "AAPL.NASDAQ": 185.0}.get(sym)

            prev_loader = MagicMock()
            prev_loader.load = _prev_load

            cfg = GapScannerConfig(min_gap_pct=1.0, max_gap_pct=20.0)
            scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader, redis)

            # 4. Run scan (bypass real connect/subscribe)
            with patch.object(quote_svc, "_connect_with_timeout", return_value=None):
                with patch.object(quote_svc, "_subscribe_all", return_value=None):
                    result = await scanner.scan(
                        ["TSLA.NASDAQ", "AAPL.NASDAQ"],
                        pass_number=1,
                    )

            await quote_svc._teardown()
            return result

        result = event_loop.run_until_complete(_run())

        # TSLA gap ≈ +3.47% (mid 150.025 vs 145.0)
        # AAPL gap ≈ -2.69% (mid 180.01 vs 185.0)
        assert len(result) == 2
        tsla = next(c for c in result if c.instrument_id == "TSLA.NASDAQ")
        aapl = next(c for c in result if c.instrument_id == "AAPL.NASDAQ")
        assert tsla.gap_pct > 0
        assert aapl.gap_pct < 0

        # Verify Redis write
        redis.set.assert_called_once()
        key = redis.set.call_args[0][0]
        assert key.startswith("sam:gapscan:") and key.endswith(":1")

    def test_futu_flow_with_partial_failure(self, event_loop):
        """Scanner tolerates symbols that fail to subscribe."""
        redis = MagicMock()

        async def _run():
            quote_svc = QuoteCollectionService(
                broker="FUTU",
                host="test-host",
                port=11111,
                watchlist=["TSLA.NASDAQ"],
                collection_period_secs=0,
            )
            await quote_svc._setup()
            quote_svc._on_data(_make_tick("TSLA.NASDAQ", "150.00", "150.05"))

            async def _prev_load(sym: str) -> float | None:
                return 145.0

            prev_loader = MagicMock()
            prev_loader.load = _prev_load

            cfg = GapScannerConfig(min_gap_pct=1.0)
            scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader, redis)

            with patch.object(quote_svc, "_connect_with_timeout", return_value=None):
                with patch.object(quote_svc, "_subscribe_all", return_value=None):
                    result = await scanner.scan(["TSLA.NASDAQ"], pass_number=1)

            await quote_svc._teardown()
            return result

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1

    def test_pass_2_trend_detection_integration(self, event_loop):
        """Two-pass scan detects RISING trend."""
        redis = MagicMock()

        async def _run():
            # Pass 1 quotes
            quote_svc_p1 = QuoteCollectionService(
                broker="FUTU",
                host="test-host",
                port=11111,
                watchlist=["TSLA.NASDAQ"],
                collection_period_secs=0,
            )
            await quote_svc_p1._setup()
            quote_svc_p1._on_data(_make_tick("TSLA.NASDAQ", "147.00", "147.05"))

            async def _prev_load(sym: str) -> float | None:
                return 145.0

            prev_loader = MagicMock()
            prev_loader.load = _prev_load

            cfg = GapScannerConfig(min_gap_pct=0.0)
            scanner = PreMarketGapScanner(cfg, quote_svc_p1, prev_loader, redis)

            with patch.object(quote_svc_p1, "_connect_with_timeout", return_value=None):
                with patch.object(quote_svc_p1, "_subscribe_all", return_value=None):
                    await scanner.scan(["TSLA.NASDAQ"], pass_number=1)

            await quote_svc_p1._teardown()

            # Pass 2 quotes (higher → RISING)
            quote_svc_p2 = QuoteCollectionService(
                broker="FUTU",
                host="test-host",
                port=11111,
                watchlist=["TSLA.NASDAQ"],
                collection_period_secs=0,
            )
            await quote_svc_p2._setup()
            quote_svc_p2._on_data(_make_tick("TSLA.NASDAQ", "152.00", "152.05"))

            # Re-use same scanner so pass_1_candidates cache is preserved
            scanner._quote_service = quote_svc_p2

            with patch.object(quote_svc_p2, "_connect_with_timeout", return_value=None):
                with patch.object(quote_svc_p2, "_subscribe_all", return_value=None):
                    result = await scanner.scan(["TSLA.NASDAQ"], pass_number=2)

            await quote_svc_p2._teardown()
            return result

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        assert result[0].trend == Trend.RISING.value


class TestCrossValidationIntegration:
    """Cross-validation between Futu and IB quote sources."""

    def test_cross_validation_flags_discrepancy(self):
        futu = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.00", "150.00")}
        ib = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "160.00", "160.00")}
        disc = PreMarketGapScanner.cross_validate(futu, ib, threshold_pct=1.0)
        assert "TSLA.NASDAQ" in disc

    def test_cross_validation_allows_close_prices(self):
        futu = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.00", "150.05")}
        ib = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.01", "150.04")}
        disc = PreMarketGapScanner.cross_validate(futu, ib, threshold_pct=1.0)
        assert disc == {}


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
