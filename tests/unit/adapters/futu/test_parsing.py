"""Tests for Futu market data parsing."""

import pytest
from nautilus_trader.model.data import (
    Bar,
    BarType,
    OrderBookDelta,
    OrderBookDeltas,
    QuoteTick,
    TradeTick,
)
from nautilus_trader.model.enums import AggressorSide, BookAction
from nautilus_trader.model.identifiers import InstrumentId

from sam_trader.adapters.futu.parsing.market_data import (
    parse_futu_bars,
    parse_futu_order_book,
    parse_futu_quote_tick,
    parse_futu_trade_tick,
    security_to_instrument_id,
)


class TestSecurityToInstrumentId:
    """Tests for security_to_instrument_id mapping."""

    def test_us_stock(self):
        result = security_to_instrument_id("US.AAPL")
        assert result == InstrumentId.from_str("AAPL.NASDAQ")

    def test_hk_stock(self):
        result = security_to_instrument_id("HK.00700")
        assert result == InstrumentId.from_str("00700.HKEX")

    def test_sh_stock(self):
        result = security_to_instrument_id("SH.600519")
        assert result == InstrumentId.from_str("600519.SSE")

    def test_sz_stock(self):
        result = security_to_instrument_id("SZ.000001")
        assert result == InstrumentId.from_str("000001.SZSE")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid Futu security code format"):
            security_to_instrument_id("INVALID")

    def test_unknown_market_raises(self):
        with pytest.raises(ValueError, match="Unknown Futu market"):
            security_to_instrument_id("XX.UNKNOWN")


class TestQuoteTickParsing:
    """Tests for parse_futu_quote_tick."""

    def test_basic_quote_tick(self):
        data = {
            "last_price": 150.25,
            "price_spread": 0.01,
            "volume": 1000,
        }
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        ts_init = 1_234_567_890_000_000_000

        tick = parse_futu_quote_tick(data, instrument_id, ts_init)

        assert isinstance(tick, QuoteTick)
        assert tick.instrument_id == instrument_id
        assert str(tick.bid_price) == "150.25"
        assert str(tick.ask_price) == "150.26"
        assert str(tick.bid_size) == "1000"
        assert str(tick.ask_size) == "1000"
        assert tick.ts_event == ts_init
        assert tick.ts_init == ts_init

    def test_zero_spread(self):
        data = {
            "last_price": 200.0,
            "price_spread": 0.0,
            "volume": 500,
        }
        tick = parse_futu_quote_tick(
            data,
            InstrumentId.from_str("TSLA.NASDAQ"),
            1_000_000_000_000_000_000,
        )
        assert str(tick.bid_price) == "200.0"
        assert str(tick.ask_price) == "200.0"

    def test_missing_fields_defaults(self):
        data = {}
        tick = parse_futu_quote_tick(
            data,
            InstrumentId.from_str("AAPL.NASDAQ"),
            0,
        )
        assert str(tick.bid_price) == "0"
        assert str(tick.ask_price) == "0"
        assert str(tick.bid_size) == "1"
        assert str(tick.ask_size) == "1"


class TestTradeTickParsing:
    """Tests for parse_futu_trade_tick."""

    def test_buyer_aggressor(self):
        data = {
            "price": 150.25,
            "volume": 100,
            "ticker_direction": "BUY",
            "sequence": 12345,
        }
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        ts_init = 1_234_567_890_000_000_000

        tick = parse_futu_trade_tick(data, instrument_id, ts_init)

        assert isinstance(tick, TradeTick)
        assert tick.instrument_id == instrument_id
        assert str(tick.price) == "150.25"
        assert str(tick.size) == "100"
        assert tick.aggressor_side == AggressorSide.BUYER
        assert str(tick.trade_id) == "12345"

    def test_seller_aggressor(self):
        data = {
            "price": 150.25,
            "volume": 50,
            "ticker_direction": "SELL",
            "sequence": 999,
        }
        tick = parse_futu_trade_tick(
            data,
            InstrumentId.from_str("AAPL.NASDAQ"),
            0,
        )
        assert tick.aggressor_side == AggressorSide.SELLER

    def test_no_aggressor(self):
        data = {
            "price": 150.25,
            "volume": 50,
            "ticker_direction": "NEUTRAL",
            "sequence": 1,
        }
        tick = parse_futu_trade_tick(
            data,
            InstrumentId.from_str("AAPL.NASDAQ"),
            0,
        )
        assert tick.aggressor_side == AggressorSide.NO_AGGRESSOR

    def test_missing_fields_defaults(self):
        data = {}
        tick = parse_futu_trade_tick(
            data,
            InstrumentId.from_str("AAPL.NASDAQ"),
            0,
        )
        assert str(tick.price) == "0"
        assert str(tick.size) == "1"
        assert tick.aggressor_side == AggressorSide.NO_AGGRESSOR


class TestBarParsing:
    """Tests for parse_futu_bars."""

    def test_single_bar_with_timestamp(self):
        kl_data = [
            {
                "open": 150.0,
                "high": 151.0,
                "low": 149.0,
                "close": 150.5,
                "volume": 5000,
                "timestamp": 1_234_567_890.0,
            }
        ]
        bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")

        bars = parse_futu_bars(kl_data, bar_type)

        assert len(bars) == 1
        bar = bars[0]
        assert isinstance(bar, Bar)
        assert bar.bar_type == bar_type
        assert str(bar.open) == "150.0"
        assert str(bar.high) == "151.0"
        assert str(bar.low) == "149.0"
        assert str(bar.close) == "150.5"
        assert str(bar.volume) == "5000"
        assert bar.ts_event == 1_234_567_890_000_000_000
        assert bar.ts_init == 1_234_567_890_000_000_000

    def test_bar_with_time_key(self):
        kl_data = [
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000,
                "time_key": "2024-01-15 09:30:00",
            }
        ]
        bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")

        bars = parse_futu_bars(kl_data, bar_type)

        assert len(bars) == 1
        bar = bars[0]
        assert bar.ts_event > 0
        assert bar.ts_init > 0

    def test_blank_bar_skipped(self):
        kl_data = [
            {"is_blank": True, "open": 150.0, "close": 150.0},
            {"open": 151.0, "high": 152.0, "low": 150.0, "close": 151.5, "volume": 100},
        ]
        bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")

        bars = parse_futu_bars(kl_data, bar_type)

        assert len(bars) == 1
        assert str(bars[0].close) == "151.5"

    def test_multiple_bars(self):
        kl_data = [
            {
                "open": 150.0,
                "high": 151.0,
                "low": 149.0,
                "close": 150.5,
                "volume": 1000,
                "timestamp": 1.0,
            },
            {
                "open": 150.5,
                "high": 152.0,
                "low": 150.0,
                "close": 151.0,
                "volume": 2000,
                "timestamp": 2.0,
            },
        ]
        bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")

        bars = parse_futu_bars(kl_data, bar_type)

        assert len(bars) == 2
        assert str(bars[0].open) == "150.0"
        assert str(bars[1].open) == "150.5"


class TestOrderBookParsing:
    """Tests for parse_futu_order_book."""

    def test_basic_order_book(self):
        data = {
            "code": "US.AAPL",
            "Bid": [
                (150.0, 100, 5, {}),
                (149.5, 200, 3, {}),
            ],
            "Ask": [
                (150.5, 150, 4, {}),
                (151.0, 300, 6, {}),
            ],
        }
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")
        ts_init = 1_234_567_890_000_000_000

        deltas = parse_futu_order_book(data, instrument_id, ts_init)

        assert isinstance(deltas, OrderBookDeltas)
        assert deltas.instrument_id == instrument_id
        # 1 CLEAR + 2 bids + 2 asks = 5 deltas
        assert len(deltas.deltas) == 5

        clear_delta = deltas.deltas[0]
        assert isinstance(clear_delta, OrderBookDelta)
        assert clear_delta.action == BookAction.CLEAR

        bid_delta = deltas.deltas[1]
        assert bid_delta.action == BookAction.ADD
        assert str(bid_delta.order.price) == "150.0"
        assert str(bid_delta.order.size) == "100"

        ask_delta = deltas.deltas[3]
        assert ask_delta.action == BookAction.ADD
        assert str(ask_delta.order.price) == "150.5"
        assert str(ask_delta.order.size) == "150"

    def test_dict_style_order_book(self):
        data = {
            "code": "HK.00700",
            "Bid": [
                {"price": 400.0, "volume": 50},
                {"price": 399.5, "volume": 100},
            ],
            "Ask": [
                {"price": 400.5, "volume": 75},
            ],
        }
        instrument_id = InstrumentId.from_str("00700.HKEX")
        ts_init = 0

        deltas = parse_futu_order_book(data, instrument_id, ts_init)

        assert len(deltas.deltas) == 4  # 1 CLEAR + 2 bids + 1 ask
        assert str(deltas.deltas[1].order.price) == "400.0"
        assert str(deltas.deltas[3].order.price) == "400.5"

    def test_empty_order_book(self):
        data = {
            "code": "US.AAPL",
            "Bid": [],
            "Ask": [],
        }
        instrument_id = InstrumentId.from_str("AAPL.NASDAQ")

        deltas = parse_futu_order_book(data, instrument_id, 0)

        assert len(deltas.deltas) == 1  # Only CLEAR
