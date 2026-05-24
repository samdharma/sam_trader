"""Unit tests for PreMarketGapScanner."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from sam_trader.services.gap_scanner import (
    CompositePrevCloseLoader,
    FutuKLinePrevCloseLoader,
    GapCandidate,
    GapScannerConfig,
    PGFillPrevCloseLoader,
    PreMarketGapScanner,
    Trend,
)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True)
class _FakeQuoteResult:
    quotes: dict[InstrumentId, QuoteTick]
    partial_failures: list[str] = field(default_factory=list)
    elapsed_secs: float = 0.0


class FakeQuoteService:
    def __init__(self, quotes: dict[InstrumentId, QuoteTick]) -> None:
        self._quotes = quotes

    async def collect(self) -> _FakeQuoteResult:
        return _FakeQuoteResult(quotes=self._quotes)


class FakePrevCloseLoader:
    def __init__(self, mapping: dict[str, float]) -> None:
        self._mapping = mapping

    async def load(self, instrument_id: str) -> float | None:
        return self._mapping.get(instrument_id)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


# ---------------------------------------------------------------------------
# Compute gap
# ---------------------------------------------------------------------------


class TestComputeGapPct:
    def test_positive_gap(self):
        assert PreMarketGapScanner.compute_gap_pct(110.0, 100.0) == 10.0

    def test_negative_gap(self):
        assert PreMarketGapScanner.compute_gap_pct(95.0, 100.0) == -5.0

    def test_zero_gap(self):
        assert PreMarketGapScanner.compute_gap_pct(100.0, 100.0) == 0.0

    def test_rounding(self):
        # Python uses banker's rounding
        assert PreMarketGapScanner.compute_gap_pct(100.12345, 100.0) == 0.1235


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_min_gap_filter(self):
        cfg = GapScannerConfig(min_gap_pct=3.0, max_gap_pct=20.0)
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("A.NASDAQ", 100.0, 102.0, 2.0, 102.0, 102.0, None),
            GapCandidate("B.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
        ]
        result = scanner._apply_filters(cands)
        assert len(result) == 1
        assert result[0].instrument_id == "B.NASDAQ"

    def test_max_gap_filter(self):
        cfg = GapScannerConfig(min_gap_pct=1.0, max_gap_pct=10.0)
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("A.NASDAQ", 100.0, 115.0, 15.0, 115.0, 115.0, None),
        ]
        assert scanner._apply_filters(cands) == []

    def test_price_range_filter(self):
        cfg = GapScannerConfig(min_price=5.0, max_price=500.0)
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("LOW.NASDAQ", 1.0, 2.0, 100.0, 2.0, 2.0, None),
            GapCandidate("OK.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
            GapCandidate("HIGH.NASDAQ", 1000.0, 1001.0, 0.1, 1001.0, 1001.0, None),
        ]
        result = scanner._apply_filters(cands)
        assert len(result) == 1
        assert result[0].instrument_id == "OK.NASDAQ"

    def test_blacklist_filter(self):
        cfg = GapScannerConfig(blacklist=("BAD.NASDAQ",))
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("BAD.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
            GapCandidate("GOOD.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
        ]
        result = scanner._apply_filters(cands)
        assert len(result) == 1
        assert result[0].instrument_id == "GOOD.NASDAQ"

    def test_volume_filter_skipped_when_none(self):
        cfg = GapScannerConfig(min_volume=1000.0)
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("A.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
        ]
        # volume is None → filter should NOT reject
        result = scanner._apply_filters(cands)
        assert len(result) == 1

    def test_volume_filter_applied(self):
        cfg = GapScannerConfig(min_volume=1000.0)
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("LOW.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, 500.0),
            GapCandidate("HIGH.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, 2000.0),
        ]
        result = scanner._apply_filters(cands)
        assert len(result) == 1
        assert result[0].instrument_id == "HIGH.NASDAQ"

    def test_otc_etf_exclusion_via_provider(self):
        provider = MagicMock()
        provider.find.return_value = MagicMock()
        provider.find.return_value.instrument_type.name = "ETF"

        cfg = GapScannerConfig(exclude_etf=True, exclude_otc=False)
        scanner = PreMarketGapScanner(cfg, MagicMock(), instrument_provider=provider)
        cands = [
            GapCandidate("ETF.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
        ]
        assert scanner._apply_filters(cands) == []

    def test_otc_exclusion_via_venue(self):
        provider = MagicMock()
        provider.find.return_value = None

        cfg = GapScannerConfig(exclude_otc=True)
        scanner = PreMarketGapScanner(cfg, MagicMock(), instrument_provider=provider)
        cands = [
            GapCandidate("PINK.OTC", 100.0, 105.0, 5.0, 105.0, 105.0, None),
        ]
        result = scanner._apply_filters(cands)
        assert result == []

    def test_no_provider_skips_metadata_filter(self):
        cfg = GapScannerConfig(exclude_otc=True, exclude_etf=True)
        scanner = PreMarketGapScanner(cfg, MagicMock())
        cands = [
            GapCandidate("A.NASDAQ", 100.0, 105.0, 5.0, 105.0, 105.0, None),
        ]
        result = scanner._apply_filters(cands)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Full scan (Pass 1)
# ---------------------------------------------------------------------------


class TestScanPass1:
    def test_scan_pass_1_returns_candidates(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
            InstrumentId.from_str("AAPL.NASDAQ"): _make_tick(
                "AAPL.NASDAQ", "180.00", "180.02"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0, "AAPL.NASDAQ": 185.0})
        redis = FakeRedis()
        cfg = GapScannerConfig(min_gap_pct=1.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader, redis)

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ", "AAPL.NASDAQ"], pass_number=1)
        )

        assert len(result) == 2
        tsla = next(c for c in result if c.instrument_id == "TSLA.NASDAQ")
        aapl = next(c for c in result if c.instrument_id == "AAPL.NASDAQ")
        # Mid = 150.025, prev = 145.0 → gap ≈ 3.4655%
        assert tsla.gap_pct > 0
        # Mid = 180.01, prev = 185.0 → gap ≈ -2.6973%
        assert aapl.gap_pct < 0

    def test_scan_ignores_symbols_without_prev_close(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({})  # no prev closes
        cfg = GapScannerConfig()
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ"], pass_number=1)
        )
        assert result == []

    def test_scan_sorts_by_abs_gap_descending(self, event_loop):
        quotes = {
            InstrumentId.from_str("A.NASDAQ"): _make_tick(
                "A.NASDAQ", "100.00", "100.00"
            ),
            InstrumentId.from_str("B.NASDAQ"): _make_tick(
                "B.NASDAQ", "110.00", "110.00"
            ),
            InstrumentId.from_str("C.NASDAQ"): _make_tick("C.NASDAQ", "95.00", "95.00"),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader(
            {"A.NASDAQ": 100.0, "B.NASDAQ": 100.0, "C.NASDAQ": 100.0}
        )
        cfg = GapScannerConfig(min_gap_pct=0.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        result = event_loop.run_until_complete(
            scanner.scan(["A.NASDAQ", "B.NASDAQ", "C.NASDAQ"], pass_number=1)
        )
        gaps = [c.gap_pct for c in result]
        assert gaps == sorted(gaps, key=abs, reverse=True)

    def test_scan_saves_to_redis(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        redis = FakeRedis()
        cfg = GapScannerConfig(min_gap_pct=1.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader, redis)

        event_loop.run_until_complete(scanner.scan(["TSLA.NASDAQ"], pass_number=1))

        assert any(
            k.startswith("sam:gapscan:") and k.endswith(":1") for k in redis.store
        )
        raw = next(v for v in redis.store.values())
        payload = json.loads(raw)
        assert isinstance(payload, list)
        assert payload[0]["instrument_id"] == "TSLA.NASDAQ"

    def test_scan_no_redis_graceful(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        cfg = GapScannerConfig(min_gap_pct=1.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader, redis_client=None)

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ"], pass_number=1)
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Trend detection (Pass 2)
# ---------------------------------------------------------------------------


class TestTrendDetection:
    def test_pass_2_rising(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "152.00", "152.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        cfg = GapScannerConfig(min_gap_pct=0.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        # Seed pass 1 with a smaller gap
        scanner._pass_1_candidates = {
            "TSLA.NASDAQ": GapCandidate(
                "TSLA.NASDAQ", 145.0, 147.0, 1.3793, 147.0, 147.0, None
            )
        }

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ"], pass_number=2)
        )
        assert result[0].trend == Trend.RISING.value

    def test_pass_2_fading(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "146.00", "146.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        cfg = GapScannerConfig(min_gap_pct=0.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        scanner._pass_1_candidates = {
            "TSLA.NASDAQ": GapCandidate(
                "TSLA.NASDAQ", 145.0, 150.0, 3.4483, 150.0, 150.0, None
            )
        }

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ"], pass_number=2)
        )
        assert result[0].trend == Trend.FADING.value

    def test_pass_2_stable(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        cfg = GapScannerConfig(min_gap_pct=0.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        scanner._pass_1_candidates = {
            "TSLA.NASDAQ": GapCandidate(
                "TSLA.NASDAQ", 145.0, 150.025, 3.4655, 150.025, 150.025, None
            )
        }

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ"], pass_number=2)
        )
        assert result[0].trend == Trend.STABLE.value

    def test_pass_2_late_breaker(self, event_loop):
        quotes = {
            InstrumentId.from_str("NEW.NASDAQ"): _make_tick(
                "NEW.NASDAQ", "110.00", "110.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"NEW.NASDAQ": 100.0})
        cfg = GapScannerConfig(min_gap_pct=0.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        # Pass 1 has no candidates for NEW.NASDAQ
        scanner._pass_1_candidates = {}

        result = event_loop.run_until_complete(
            scanner.scan(["NEW.NASDAQ"], pass_number=2)
        )
        assert result[0].trend == Trend.LATE_BREAKER.value


# ---------------------------------------------------------------------------
# Market separation
# ---------------------------------------------------------------------------


class TestMarketSeparation:
    def test_us_market_scan(self, event_loop):
        quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        cfg = GapScannerConfig(market="US", min_gap_pct=1.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        result = event_loop.run_until_complete(
            scanner.scan(["TSLA.NASDAQ"], pass_number=1)
        )
        assert len(result) == 1

    def test_hk_market_scan(self, event_loop):
        quotes = {
            InstrumentId.from_str("00700.HKEX"): _make_tick(
                "00700.HKEX", "400.00", "400.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"00700.HKEX": 390.0})
        cfg = GapScannerConfig(market="HK", min_gap_pct=1.0)
        scanner = PreMarketGapScanner(cfg, quote_svc, prev_loader)

        result = event_loop.run_until_complete(
            scanner.scan(["00700.HKEX"], pass_number=1)
        )
        assert len(result) == 1
        assert result[0].instrument_id == "00700.HKEX"


# ---------------------------------------------------------------------------
# Cross validation
# ---------------------------------------------------------------------------


class TestCrossValidate:
    def test_no_discrepancy(self):
        futu = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.00", "150.05")}
        ib = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.01", "150.04")}
        disc = PreMarketGapScanner.cross_validate(futu, ib, threshold_pct=1.0)
        assert disc == {}

    def test_detects_discrepancy(self):
        futu = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.00", "150.00")}
        ib = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "160.00", "160.00")}
        disc = PreMarketGapScanner.cross_validate(futu, ib, threshold_pct=1.0)
        assert "TSLA.NASDAQ" in disc
        assert "Futu mid" in disc["TSLA.NASDAQ"]

    def test_missing_ib_symbol_ignored(self):
        futu = {"TSLA.NASDAQ": _make_tick("TSLA.NASDAQ", "150.00", "150.05")}
        ib: dict[str, QuoteTick] = {}
        disc = PreMarketGapScanner.cross_validate(futu, ib)
        assert disc == {}


# ---------------------------------------------------------------------------
# Prev-close loaders
# ---------------------------------------------------------------------------


class TestPGFillPrevCloseLoader:
    def test_load_success(self, event_loop):
        loader = PGFillPrevCloseLoader()
        mock_row = {"fill_price": 123.45}
        with patch("asyncpg.connect") as mock_conn:
            mock_conn.return_value.fetchrow = AsyncMock(return_value=mock_row)
            mock_conn.return_value.close = AsyncMock()
            result = event_loop.run_until_complete(loader.load("TSLA.NASDAQ"))
            assert result == 123.45

    def test_load_no_row(self, event_loop):
        loader = PGFillPrevCloseLoader()
        with patch("asyncpg.connect") as mock_conn:
            mock_conn.return_value.fetchrow = AsyncMock(return_value=None)
            mock_conn.return_value.close = AsyncMock()
            result = event_loop.run_until_complete(loader.load("TSLA.NASDAQ"))
            assert result is None

    def test_load_pg_error(self, event_loop):
        loader = PGFillPrevCloseLoader()
        with patch("asyncpg.connect", side_effect=Exception("conn refused")):
            result = event_loop.run_until_complete(loader.load("TSLA.NASDAQ"))
            assert result is None


class TestFutuKLinePrevCloseLoader:
    def test_load_success(self):
        loader = FutuKLinePrevCloseLoader()
        mock_data = MagicMock()
        mock_data.empty = False
        mock_data.__len__ = lambda self: 2  # noqa: E741
        mock_data.iloc = MagicMock()
        mock_data.iloc.__getitem__ = lambda self, idx: {"close": 200.0}

        mock_ctx_class = MagicMock()
        mock_ctx_instance = MagicMock()
        mock_ctx_instance.get_cur_kline = MagicMock(return_value=(0, mock_data))
        mock_ctx_class.return_value = mock_ctx_instance

        with patch.object(
            __import__(
                "sam_trader.services.gap_scanner", fromlist=["_futu_open_quote_ctx"]
            ),
            "_futu_open_quote_ctx",
            mock_ctx_class,
        ):
            with patch.object(
                __import__(
                    "sam_trader.services.gap_scanner", fromlist=["_futu_ret_ok"]
                ),
                "_futu_ret_ok",
                0,
            ):
                with patch.object(
                    __import__(
                        "sam_trader.services.gap_scanner",
                        fromlist=["_instrument_id_to_futu_security_fn"],
                    ),
                    "_instrument_id_to_futu_security_fn",
                    return_value="US.TSLA",
                ):
                    result = loader.load("TSLA.NASDAQ")
                    assert result == 200.0

    def test_load_futu_error(self):
        loader = FutuKLinePrevCloseLoader()
        with patch.object(
            __import__(
                "sam_trader.services.gap_scanner",
                fromlist=["_instrument_id_to_futu_security_fn"],
            ),
            "_instrument_id_to_futu_security_fn",
            side_effect=ValueError("bad map"),
        ):
            result = loader.load("BAD.SYMBOL")
            assert result is None


class TestCompositePrevCloseLoader:
    def test_first_loader_wins(self, event_loop):
        loader1 = FakePrevCloseLoader({"A.NASDAQ": 100.0})
        loader2 = FakePrevCloseLoader({"A.NASDAQ": 200.0})
        composite = CompositePrevCloseLoader([loader1, loader2])
        result = event_loop.run_until_complete(composite.load("A.NASDAQ"))
        assert result == 100.0

    def test_fallback_to_second(self, event_loop):
        loader1 = FakePrevCloseLoader({})
        loader2 = FakePrevCloseLoader({"A.NASDAQ": 200.0})
        composite = CompositePrevCloseLoader([loader1, loader2])
        result = event_loop.run_until_complete(composite.load("A.NASDAQ"))
        assert result == 200.0

    def test_all_fail_returns_none(self, event_loop):
        composite = CompositePrevCloseLoader([FakePrevCloseLoader({})])
        result = event_loop.run_until_complete(composite.load("A.NASDAQ"))
        assert result is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_invalid_pass_number(self, event_loop):
        scanner = PreMarketGapScanner(GapScannerConfig(), MagicMock())
        with pytest.raises(ValueError, match="pass_number must be 1 or 2"):
            event_loop.run_until_complete(scanner.scan(["A.NASDAQ"], pass_number=3))

    def test_zero_prev_close_skipped(self, event_loop):
        quotes = {
            InstrumentId.from_str("A.NASDAQ"): _make_tick(
                "A.NASDAQ", "100.00", "100.05"
            ),
        }
        quote_svc = FakeQuoteService(quotes)
        prev_loader = FakePrevCloseLoader({"A.NASDAQ": 0.0})
        scanner = PreMarketGapScanner(GapScannerConfig(), quote_svc, prev_loader)
        result = event_loop.run_until_complete(scanner.scan(["A.NASDAQ"]))
        assert result == []

    def test_callable_quote_service(self, event_loop):
        async def _quote_fn():
            return {
                InstrumentId.from_str("A.NASDAQ"): _make_tick(
                    "A.NASDAQ", "110.00", "110.05"
                )
            }

        prev_loader = FakePrevCloseLoader({"A.NASDAQ": 100.0})
        cfg = GapScannerConfig(min_gap_pct=1.0)
        scanner = PreMarketGapScanner(cfg, _quote_fn, prev_loader)
        result = event_loop.run_until_complete(scanner.scan(["A.NASDAQ"]))
        assert len(result) == 1


# Small async mock helper for pg tests
class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)
