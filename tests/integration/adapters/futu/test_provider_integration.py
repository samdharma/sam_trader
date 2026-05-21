"""Integration tests for FutuInstrumentProvider.

These tests verify that the provider correctly interacts with mocked
Futu SDK contexts and produces valid Nautilus instruments.
"""

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


@pytest.mark.integration
class TestLoadHkInstruments:
    """Integration test: loading HK instruments via the provider."""

    def test_load_hk_instruments(self, event_loop, make_provider, mock_quote_ctx):
        """Provider can load a basket of HK stocks and resolve them correctly.

        Verifies:
        - HK.00700 → 00700.HKEX
        - HK.00005 → 00005.HKEX
        - Currency is HKD
        - Lot sizes are parsed correctly
        """
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "HK.00700",
                        "name": "Tencent Holdings Ltd",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                        "exchange_type": "HK_HKEX",
                    },
                    {
                        "code": "HK.00005",
                        "name": "HSBC Holdings plc",
                        "lot_size": 400,
                        "stock_type": "STOCK",
                        "exchange_type": "HK_HKEX",
                    },
                ]
            ),
        )

        provider = make_provider()
        iids = [
            InstrumentId.from_str("00700.HKEX"),
            InstrumentId.from_str("00005.HKEX"),
        ]
        event_loop.run_until_complete(provider.load_ids_async(iids))

        assert provider.count == 2

        tencent = provider.find(iids[0])
        assert isinstance(tencent, Equity)
        assert tencent.id == iids[0]
        assert tencent.raw_symbol.value == "HK.00700"
        assert tencent.quote_currency.code == "HKD"
        assert str(tencent.lot_size) == "100"

        hsbc = provider.find(iids[1])
        assert isinstance(hsbc, Equity)
        assert hsbc.id == iids[1]
        assert hsbc.raw_symbol.value == "HK.00005"
        assert hsbc.quote_currency.code == "HKD"
        assert str(hsbc.lot_size) == "400"

    def test_load_hk_precision_fallback(
        self, event_loop, make_provider, mock_quote_ctx
    ):
        """HK equities use 3-decimal precision fallback when spread is absent."""
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
        assert inst.price_precision == 3
        assert str(inst.price_increment) == "0.001"

    def test_load_hk_mixed_with_us(self, event_loop, make_provider, mock_quote_ctx):
        """Provider handles a mixed HK+US basket in a single call."""
        mock_quote_ctx.get_stock_basicinfo.return_value = (
            RET_OK,
            pd.DataFrame(
                [
                    {
                        "code": "HK.00700",
                        "name": "Tencent",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    },
                    {
                        "code": "US.AAPL",
                        "name": "Apple Inc",
                        "lot_size": 100,
                        "stock_type": "STOCK",
                    },
                ]
            ),
        )

        provider = make_provider()
        iids = [
            InstrumentId.from_str("00700.HKEX"),
            InstrumentId.from_str("AAPL.NASDAQ"),
        ]
        event_loop.run_until_complete(provider.load_ids_async(iids))

        assert provider.count == 2
        assert provider.find(iids[0]).quote_currency.code == "HKD"
        assert provider.find(iids[1]).quote_currency.code == "USD"
