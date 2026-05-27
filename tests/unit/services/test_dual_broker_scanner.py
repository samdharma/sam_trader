"""Unit tests for DualBrokerGapScanner."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from sam_trader.services.dual_broker_scanner import (
    DualBrokerGapScanner,
    DualBrokerScannerConfig,
    GapScannerConfigError,
    _StaticQuoteService,
    get_gap_scanner_config,
    load_gap_scanner_config,
)
from sam_trader.services.gap_scanner import Trend
from sam_trader.services.quote_collector import QuoteCollectionResult


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
# Config loading
# ---------------------------------------------------------------------------


class TestLoadGapScannerConfig:
    def test_load_us_and_hk(self, tmp_path):
        yaml_path = tmp_path / "gap_scanner.yaml"
        yaml_path.write_text(
            """
gap_scanner:
  US:
    primary_broker: "FUTU"
    secondary_broker: "IB"
    cross_validation_threshold_pct: 1.5
    min_gap_pct: 3.0
  HK:
    primary_broker: "FUTU"
    secondary_broker: null
    min_gap_pct: 1.5
""",
            encoding="utf-8",
        )

        result = load_gap_scanner_config(str(yaml_path))
        assert "US" in result
        assert "HK" in result
        assert result["US"].primary_broker == "FUTU"
        assert result["US"].secondary_broker == "IB"
        assert result["US"].cross_validation_threshold_pct == 1.5
        assert result["US"].min_gap_pct == 3.0
        assert result["HK"].secondary_broker is None
        assert result["HK"].min_gap_pct == 1.5

    def test_missing_file_raises(self):
        with pytest.raises(GapScannerConfigError, match="not found"):
            load_gap_scanner_config("/nonexistent/gap_scanner.yaml")

    def test_empty_file_returns_empty(self, tmp_path):
        yaml_path = tmp_path / "gap_scanner.yaml"
        yaml_path.write_text("", encoding="utf-8")
        result = load_gap_scanner_config(str(yaml_path))
        assert result == {}

    def test_blacklist_list_converted_to_tuple(self, tmp_path):
        yaml_path = tmp_path / "gap_scanner.yaml"
        yaml_path.write_text(
            """
gap_scanner:
  US:
    blacklist:
      - "BAD.NASDAQ"
      - "WORSE.NASDAQ"
""",
            encoding="utf-8",
        )
        result = load_gap_scanner_config(str(yaml_path))
        assert result["US"].blacklist == ("BAD.NASDAQ", "WORSE.NASDAQ")

    def test_secondary_broker_none_variants(self, tmp_path):
        yaml_path = tmp_path / "gap_scanner.yaml"
        yaml_path.write_text(
            """
gap_scanner:
  US:
    secondary_broker: "none"
  HK:
    secondary_broker: "NULL"
""",
            encoding="utf-8",
        )
        result = load_gap_scanner_config(str(yaml_path))
        assert result["US"].secondary_broker is None
        assert result["HK"].secondary_broker is None


class TestGetGapScannerConfig:
    def test_fallback_defaults_for_us(self):
        cfg = get_gap_scanner_config("US", path="/nonexistent/path.yaml")
        assert cfg.market == "US"
        assert cfg.primary_broker == "FUTU"
        assert cfg.secondary_broker == "IB"
        assert cfg.min_gap_pct == 2.0

    def test_fallback_defaults_for_hk(self):
        cfg = get_gap_scanner_config("HK", path="/nonexistent/path.yaml")
        assert cfg.market == "HK"
        assert cfg.primary_broker == "FUTU"
        assert cfg.secondary_broker is None
        assert cfg.min_gap_pct == 1.5

    def test_loads_from_file(self, tmp_path):
        yaml_path = tmp_path / "gap_scanner.yaml"
        yaml_path.write_text(
            """
gap_scanner:
  US:
    min_gap_pct: 5.0
""",
            encoding="utf-8",
        )
        cfg = get_gap_scanner_config("US", path=str(yaml_path))
        assert cfg.min_gap_pct == 5.0


# ---------------------------------------------------------------------------
# Static quote service helper
# ---------------------------------------------------------------------------


class TestStaticQuoteService:
    def test_returns_wrapped_result(self, event_loop):
        tick = _make_tick("TSLA.NASDAQ", "150.00", "150.05")
        result = QuoteCollectionResult(
            quotes={InstrumentId.from_str("TSLA.NASDAQ"): tick}
        )
        svc = _StaticQuoteService(result)

        async def _run():
            out = await svc.collect()
            return out

        out = event_loop.run_until_complete(_run())
        assert out.quotes == result.quotes


# ---------------------------------------------------------------------------
# Dual-broker scan — US market
# ---------------------------------------------------------------------------


class TestDualBrokerScanUS:
    def test_us_dual_broker_returns_candidates(self, event_loop):
        """US scan with both brokers collects in parallel and returns candidates."""
        futu_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        ib_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.01", "150.04"
            ),
        }

        config = DualBrokerScannerConfig(
            market="US",
            primary_broker="FUTU",
            secondary_broker="IB",
            min_gap_pct=1.0,
        )
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
            prev_close_loader=prev_loader,
        )

        async def _run():
            # Patch _collect_quotes to return fake results directly
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_quotes),
                    QuoteCollectionResult(quotes=ib_quotes),
                )
            )
            return await scanner.scan(pass_number=1)

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        assert result[0].instrument_id == "TSLA.NASDAQ"

    def test_us_cross_validation_flags_discrepancy(self, event_loop):
        """When IB mid differs > threshold the candidate is not cross_validated."""
        futu_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.00"
            ),
        }
        ib_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "160.00", "160.00"
            ),
        }

        config = DualBrokerScannerConfig(
            market="US",
            primary_broker="FUTU",
            secondary_broker="IB",
            cross_validation_threshold_pct=1.0,
            min_gap_pct=0.0,
        )
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
            prev_close_loader=prev_loader,
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_quotes),
                    QuoteCollectionResult(quotes=ib_quotes),
                )
            )
            return await scanner.scan(pass_number=1)

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        assert result[0].cross_validated is False
        assert "Futu mid" in result[0].cross_validation_note

    def test_us_cross_validation_passes_when_close(self, event_loop):
        """When IB mid is within threshold the candidate is cross_validated."""
        futu_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        ib_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.01", "150.04"
            ),
        }

        config = DualBrokerScannerConfig(
            market="US",
            primary_broker="FUTU",
            secondary_broker="IB",
            cross_validation_threshold_pct=1.0,
            min_gap_pct=0.0,
        )
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
            prev_close_loader=prev_loader,
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_quotes),
                    QuoteCollectionResult(quotes=ib_quotes),
                )
            )
            return await scanner.scan(pass_number=1)

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        assert result[0].cross_validated is True
        assert result[0].cross_validation_note == ""

    def test_us_secondary_failure_continues_with_primary(self, event_loop, caplog):
        """If IB collection fails the scan continues on Futu quotes only."""
        futu_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }

        config = DualBrokerScannerConfig(
            market="US",
            primary_broker="FUTU",
            secondary_broker="IB",
            min_gap_pct=1.0,
        )
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
            prev_close_loader=prev_loader,
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_quotes),
                    None,
                )
            )
            return await scanner.scan(pass_number=1)

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        # No cross-validation performed → validated by default
        assert result[0].cross_validated is True

    def test_us_primary_failure_raises(self, event_loop):
        """If primary broker fails the scan aborts."""
        config = DualBrokerScannerConfig(
            market="US",
            primary_broker="FUTU",
            secondary_broker="IB",
        )
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                side_effect=RuntimeError("Primary broker failed")
            )
            return await scanner.scan(pass_number=1)

        with pytest.raises(RuntimeError, match="Primary broker"):
            event_loop.run_until_complete(_run())


# ---------------------------------------------------------------------------
# Dual-broker scan — HK market
# ---------------------------------------------------------------------------


class TestDualBrokerScanHK:
    def test_hk_single_broker_no_cross_validation(self, event_loop):
        """HK scan uses Futu only; secondary broker is not created."""
        futu_quotes = {
            InstrumentId.from_str("00700.HKEX"): _make_tick(
                "00700.HKEX", "400.00", "400.05"
            ),
        }

        config = DualBrokerScannerConfig(
            market="HK",
            primary_broker="FUTU",
            secondary_broker=None,
            min_gap_pct=1.0,
        )
        prev_loader = FakePrevCloseLoader({"00700.HKEX": 390.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["00700.HKEX"],
            prev_close_loader=prev_loader,
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_quotes),
                    None,
                )
            )
            return await scanner.scan(pass_number=1)

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        assert result[0].instrument_id == "00700.HKEX"
        assert result[0].cross_validated is True
        assert result[0].cross_validation_note == ""


# ---------------------------------------------------------------------------
# Trend detection (Pass 2)
# ---------------------------------------------------------------------------


class TestTrendDetection:
    def test_pass_2_rising(self, event_loop):
        futu_p1 = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "147.00", "147.05"
            ),
        }
        futu_p2 = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "152.00", "152.05"
            ),
        }

        config = DualBrokerScannerConfig(
            market="US",
            primary_broker="FUTU",
            secondary_broker=None,
            min_gap_pct=0.0,
        )
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
            prev_close_loader=prev_loader,
        )

        async def _run():
            # Pass 1
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_p1),
                    None,
                )
            )
            await scanner.scan(pass_number=1)

            # Pass 2
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_p2),
                    None,
                )
            )
            return await scanner.scan(pass_number=2)

        result = event_loop.run_until_complete(_run())
        assert len(result) == 1
        assert result[0].trend == Trend.RISING.value


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_invalid_pass_number_zero(self, event_loop):
        scanner = DualBrokerGapScanner(
            config=DualBrokerScannerConfig(),
            watchlist=["A.NASDAQ"],
        )
        with pytest.raises(ValueError, match="pass_number must be >= 1"):
            event_loop.run_until_complete(scanner.scan(pass_number=0))

    def test_empty_quotes_returns_empty(self, event_loop):
        config = DualBrokerScannerConfig(min_gap_pct=1.0)
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes={}),
                    None,
                )
            )
            return await scanner.scan(pass_number=1)

        result = event_loop.run_until_complete(_run())
        assert result == []

    def test_redis_persistence(self, event_loop):
        futu_quotes = {
            InstrumentId.from_str("TSLA.NASDAQ"): _make_tick(
                "TSLA.NASDAQ", "150.00", "150.05"
            ),
        }
        redis = FakeRedis()
        config = DualBrokerScannerConfig(min_gap_pct=1.0)
        prev_loader = FakePrevCloseLoader({"TSLA.NASDAQ": 145.0})
        scanner = DualBrokerGapScanner(
            config=config,
            watchlist=["TSLA.NASDAQ"],
            prev_close_loader=prev_loader,
            redis_client=redis,
        )

        async def _run():
            scanner._collect_quotes = AsyncMock(  # type: ignore[method-assign]
                return_value=(
                    QuoteCollectionResult(quotes=futu_quotes),
                    None,
                )
            )
            return await scanner.scan(pass_number=1)

        event_loop.run_until_complete(_run())
        assert any(
            k.startswith("sam:gapscan:") and k.endswith(":1") for k in redis.store
        )
        raw = next(v for v in redis.store.values())
        payload = json.loads(raw)
        assert isinstance(payload, list)
        assert payload[0]["instrument_id"] == "TSLA.NASDAQ"


# Small async mock helper
class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)
