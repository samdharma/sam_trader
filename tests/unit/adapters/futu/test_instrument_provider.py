"""Unit tests for FutuInstrumentProvider."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pandas as pd
import pytest
from futu import RET_OK
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Equity

from sam_trader.adapters.futu.instrument_provider import FutuInstrumentProvider


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_quote_ctx() -> MagicMock:
    """Return a mock OpenQuoteContext."""
    ctx = MagicMock()
    ctx.get_stock_basicinfo.return_value = (RET_OK, pd.DataFrame())
    return ctx


@pytest.fixture
def make_provider(event_loop, mock_quote_ctx):
    """Factory to create a FutuInstrumentProvider with mocked context."""

    def _factory(
        config: InstrumentProviderConfig | None = None,
    ) -> FutuInstrumentProvider:
        return FutuInstrumentProvider(
            quote_context=mock_quote_ctx,
            config=config,
        )

    return _factory


# ---------------------------------------------------------------------------
# load_ids_async
# ---------------------------------------------------------------------------


class TestLoadIds:
    """Tests for load_ids_async."""

    def test_load_single_id(self, event_loop, make_provider, mock_quote_ctx):
        """Load a single US instrument by ID."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        assert provider.count == 1
        inst = provider.find(iid)
        assert isinstance(inst, Equity)
        assert inst.id == iid
        assert inst.raw_symbol.value == "US.AAPL"

    def test_load_multiple_ids(self, event_loop, make_provider, mock_quote_ctx):
        """Load multiple instruments across markets."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    },
                    {
                        "code": "HK.00700",
                        "name": "Tencent",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    },
                ]
            ),
        )

        provider = make_provider()
        iids = [
            InstrumentId.from_str("AAPL.NASDAQ"),
            InstrumentId.from_str("00700.HKEX"),
        ]
        event_loop.run_until_complete(provider.load_ids_async(iids))

        assert provider.count == 2
        assert provider.find(iids[0]) is not None
        assert provider.find(iids[1]) is not None

    def test_load_ids_empty_list(self, event_loop, make_provider, mock_quote_ctx):
        """Empty instrument ID list is a no-op."""
        provider = make_provider()
        event_loop.run_until_complete(provider.load_ids_async([]))

        assert provider.count == 0
        mock_quote_ctx.get_stock_basicinfo.assert_not_called()

    def test_load_ids_caches_existing(self, event_loop, make_provider, mock_quote_ctx):
        """Loading the same ID twice does not duplicate."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_ids_async([iid]))
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        assert provider.count == 1
        assert mock_quote_ctx.get_stock_basicinfo.call_count == 2

    def test_load_ids_skips_unknown_venue(
        self, event_loop, make_provider, mock_quote_ctx
    ):
        """Instrument IDs with unmapped venues are skipped with a warning."""
        provider = make_provider()
        iid = InstrumentId.from_str("XYZ.UNKNOWN")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        assert provider.count == 0
        mock_quote_ctx.get_stock_basicinfo.assert_not_called()

    def test_load_ids_api_error(self, event_loop, make_provider, mock_quote_ctx):
        """API errors are handled gracefully."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (-1, "connection error")

        provider = make_provider()
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        assert provider.count == 0


# ---------------------------------------------------------------------------
# load_all_async
# ---------------------------------------------------------------------------


class TestLoadAll:
    """Tests for load_all_async."""

    def test_load_all_queries_multiple_markets(
        self, event_loop, make_provider, mock_quote_ctx
    ):
        """load_all_async queries US, HK, SH, SZ markets."""
        mock_quote_ctx.get_stock_basicinfo.side_effect = [
            (
                RET_OK,
                pd.DataFrame(
                    [{"code": "US.AAPL", "lot_size": 100, "stock_type": "STOCK"}]
                ),
            ),
            (
                RET_OK,
                pd.DataFrame(
                    [{"code": "HK.00700", "lot_size": 100, "stock_type": "STOCK"}]
                ),
            ),
            (RET_OK, pd.DataFrame()),
            (RET_OK, pd.DataFrame()),
        ]

        provider = make_provider()
        event_loop.run_until_complete(provider.load_all_async())

        assert provider.count == 2
        assert provider.find(InstrumentId.from_str("AAPL.NASDAQ")) is not None
        assert provider.find(InstrumentId.from_str("00700.HKEX")) is not None
        assert mock_quote_ctx.get_stock_basicinfo.call_count == 4

    def test_load_all_empty_markets(self, event_loop, make_provider, mock_quote_ctx):
        """All markets returning empty DataFrames results in zero instruments."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (RET_OK, pd.DataFrame())

        provider = make_provider()
        event_loop.run_until_complete(provider.load_all_async())

        assert provider.count == 0
        assert mock_quote_ctx.get_stock_basicinfo.call_count == 4

    def test_load_all_api_error_graceful(
        self, event_loop, make_provider, mock_quote_ctx
    ):
        """API errors for individual markets are logged but not raised."""
        mock_quote_ctx.get_stock_basicinfo.side_effect = [
            (-1, "error"),
            (
                RET_OK,
                pd.DataFrame(
                    [{"code": "HK.00700", "lot_size": 100, "stock_type": "STOCK"}]
                ),
            ),
            (RET_OK, pd.DataFrame()),
            (RET_OK, pd.DataFrame()),
        ]

        provider = make_provider()
        event_loop.run_until_complete(provider.load_all_async())

        assert provider.count == 1
        assert provider.find(InstrumentId.from_str("00700.HKEX")) is not None


# ---------------------------------------------------------------------------
# Symbology mapping
# ---------------------------------------------------------------------------


class TestSymbologyMapping:
    """Tests for HK.00700 → 00700.HKEX and US.AAPL → AAPL.NASDAQ mapping."""

    def test_hk_symbology(self, event_loop, make_provider, mock_quote_ctx):
        """HK.00700 maps to 00700.HKEX."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "HK.00700",
                        "name": "Tencent",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("00700.HKEX")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        inst = provider.find(iid)
        assert isinstance(inst, Equity)
        assert inst.id == iid
        assert inst.raw_symbol.value == "HK.00700"
        assert inst.quote_currency.code == "HKD"

    def test_us_symbology(self, event_loop, make_provider, mock_quote_ctx):
        """US.AAPL maps to AAPL.NASDAQ."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        inst = provider.find(iid)
        assert isinstance(inst, Equity)
        assert inst.id == iid
        assert inst.raw_symbol.value == "US.AAPL"
        assert inst.quote_currency.code == "USD"

    def test_us_nyse_symbology(self, event_loop, make_provider, mock_quote_ctx):
        """NYSE-listed symbols map to US market code; Futu returns NASDAQ venue.

        Futu uses a single ``US`` market prefix for all US equities.  The
        reverse mapping therefore resolves to ``NASDAQ`` regardless of
        whether the original bundle specified ``NYSE``.
        """
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.BABA",
                        "name": "Alibaba",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("BABA.NYSE")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        # Futu "US" market always resolves to NASDAQ on the way back
        nasdaq_iid = InstrumentId.from_str("BABA.NASDAQ")
        inst = provider.find(nasdaq_iid)
        assert isinstance(inst, Equity)
        assert inst.id == nasdaq_iid
        assert inst.raw_symbol.value == "US.BABA"


# ---------------------------------------------------------------------------
# Position auto-loading
# ---------------------------------------------------------------------------


class TestLoadFromPositionData:
    """Tests for load_from_position_data."""

    def test_auto_load_unknown_instrument(self, make_provider, mock_quote_ctx):
        """Position data for unknown instrument triggers fetch."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.TSLA",
                        "name": "Tesla",
                        "lot_size": 1,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        inst = provider.load_from_position_data("US.TSLA")

        assert isinstance(inst, Equity)
        assert inst.id == InstrumentId.from_str("TSLA.NASDAQ")
        assert provider.find(InstrumentId.from_str("TSLA.NASDAQ")) is not None

    def test_auto_load_cached_instrument(
        self, event_loop, make_provider, mock_quote_ctx
    ):
        """Position data for already-cached instrument returns immediately."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        # Pre-load
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_ids_async([iid]))

        mock_quote_ctx.get_stock_basicinfo.reset_mock()
        inst = provider.load_from_position_data("US.AAPL")

        assert inst is not None
        assert inst.id == iid
        # Should not have made another API call
        mock_quote_ctx.get_stock_basicinfo.assert_not_called()

    def test_auto_load_api_failure(self, make_provider, mock_quote_ctx):
        """Failed API call returns None gracefully."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (-1, "error")

        provider = make_provider()
        inst = provider.load_from_position_data("US.UNKNOWN")

        assert inst is None

    def test_auto_load_invalid_code(self, make_provider, mock_quote_ctx):
        """Invalid Futu code returns None."""
        provider = make_provider()
        inst = provider.load_from_position_data("INVALID_CODE")

        assert inst is None
        mock_quote_ctx.get_stock_basicinfo.assert_not_called()


# ---------------------------------------------------------------------------
# load_async (single instrument)
# ---------------------------------------------------------------------------


class TestLoadAsync:
    """Tests for load_async."""

    def test_load_single_async(self, event_loop, make_provider, mock_quote_ctx):
        """load_async fetches a single instrument."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_async(iid))

        assert provider.find(iid) is not None

    def test_load_single_already_cached(
        self, event_loop, make_provider, mock_quote_ctx
    ):
        """load_async is a no-op if instrument already cached."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    }
                ]
            ),
        )

        provider = make_provider()
        iid = InstrumentId.from_str("AAPL.NASDAQ")
        event_loop.run_until_complete(provider.load_async(iid))
        mock_quote_ctx.get_stock_basicinfo.reset_mock()
        event_loop.run_until_complete(provider.load_async(iid))

        mock_quote_ctx.get_stock_basicinfo.assert_not_called()
