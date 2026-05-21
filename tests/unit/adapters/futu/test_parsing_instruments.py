"""Tests for Futu instrument parsing."""

from nautilus_trader.model.enums import AssetClass, OptionKind
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Equity, FuturesContract, OptionContract
from nautilus_trader.model.objects import Currency

from sam_trader.adapters.futu.parsing.instruments import (
    _parse_date_to_ns,
    _parse_futu_equity,
    _parse_futu_future,
    _parse_futu_option,
    _precision_from_spread,
    parse_futu_instrument,
)


class TestParseEquity:
    """Tests for _parse_futu_equity."""

    def test_parse_equity(self):
        """Parse a US stock basic info dict to Equity."""
        basic_info = {
            "code": "US.AAPL",
            "name": "Apple Inc",
            "lot_size": 100,
            "stock_type": "STOCK",
            "exchange_type": "US_NASDAQ",
        }

        equity = _parse_futu_equity(basic_info)

        assert isinstance(equity, Equity)
        assert equity.id == InstrumentId.from_str("AAPL.NASDAQ")
        assert equity.raw_symbol.value == "US.AAPL"
        assert equity.quote_currency == Currency.from_str("USD")
        assert equity.price_precision == 2
        assert str(equity.price_increment) == "0.01"
        assert str(equity.lot_size) == "100"
        assert equity.ts_event == 0
        assert equity.ts_init == 0

    def test_parse_hk_equity(self):
        """Parse a HK stock basic info dict to Equity."""
        basic_info = {
            "code": "HK.00700",
            "name": "Tencent",
            "lot_size": 100,
            "stock_type": "STOCK",
        }

        equity = _parse_futu_equity(basic_info)

        assert equity.id == InstrumentId.from_str("00700.HKEX")
        assert equity.quote_currency == Currency.from_str("HKD")
        assert equity.price_precision == 3  # HK fallback
        assert str(equity.price_increment) == "0.001"

    def test_parse_equity_with_spread(self):
        """Parse equity when price_spread is provided."""
        basic_info = {
            "code": "US.TSLA",
            "lot_size": 1,
            "stock_type": "STOCK",
            "price_spread": 0.05,
        }

        equity = _parse_futu_equity(basic_info)

        assert equity.price_precision == 2
        assert str(equity.price_increment) == "0.05"


class TestParseOption:
    """Tests for _parse_futu_option."""

    def test_parse_option(self):
        """Parse an option basic info dict to OptionContract."""
        basic_info = {
            "code": "US.AAPL240119C00150000",
            "name": "AAPL JAN 19 2024 150 CALL",
            "lot_size": 100,
            "stock_type": "OPTION",
            "option_type": "CALL",
            "strike_price": 150.0,
            "strike_time": "2024-01-19",
            "stock_owner": "US.AAPL",
        }

        option = _parse_futu_option(basic_info)

        assert isinstance(option, OptionContract)
        assert option.id == InstrumentId.from_str("AAPL240119C00150000.NASDAQ")
        assert option.raw_symbol.value == "US.AAPL240119C00150000"
        assert option.asset_class == AssetClass.EQUITY
        assert option.quote_currency == Currency.from_str("USD")
        assert option.option_kind == OptionKind.CALL
        assert str(option.strike_price) == "150.0"
        assert option.underlying == "US.AAPL"
        assert str(option.multiplier) == "100"
        assert str(option.lot_size) == "100"
        assert option.expiration_ns > 0

    def test_parse_put_option(self):
        """Parse a PUT option."""
        basic_info = {
            "code": "US.AAPL240119P00150000",
            "lot_size": 100,
            "stock_type": "OPTION",
            "option_type": "PUT",
            "strike_price": 150.0,
            "strike_time": "2024-01-19",
            "stock_owner": "US.AAPL",
        }

        option = _parse_futu_option(basic_info)

        assert option.option_kind == OptionKind.PUT


class TestParseFuture:
    """Tests for _parse_futu_future."""

    def test_parse_future(self):
        """Parse a future basic info dict to FuturesContract."""
        basic_info = {
            "code": "HK.HSI2401",
            "name": "Hang Seng Index Jan 2024",
            "lot_size": 50,
            "stock_type": "FUTURE",
            "last_trade_time": "2024-01-30",
        }

        future = _parse_futu_future(basic_info)

        assert isinstance(future, FuturesContract)
        assert future.id == InstrumentId.from_str("HSI2401.HKEX")
        assert future.raw_symbol.value == "HK.HSI2401"
        assert future.asset_class == AssetClass.INDEX
        assert future.quote_currency == Currency.from_str("HKD")
        assert str(future.multiplier) == "50"
        assert str(future.lot_size) == "50"
        assert future.underlying == "HK.HSI2401"
        assert future.expiration_ns > 0


class TestPrecisionFromSpread:
    """Tests for _precision_from_spread."""

    def test_tick_size_precision(self):
        """Derive precision from explicit tick sizes."""
        assert _precision_from_spread(0.01) == (2, "0.01")
        assert _precision_from_spread(0.001) == (3, "0.001")
        assert _precision_from_spread(0.5) == (1, "0.5")
        assert _precision_from_spread(1.0) == (0, "1.0")
        assert _precision_from_spread(0.0001) == (4, "0.0001")

    def test_fallback_by_market(self):
        """Fall back to market defaults when spread is missing or zero."""
        assert _precision_from_spread(None, "US") == (2, "0.01")
        assert _precision_from_spread(None, "HK") == (3, "0.001")
        assert _precision_from_spread(None, "SH") == (2, "0.01")
        assert _precision_from_spread(None, "SZ") == (2, "0.01")
        assert _precision_from_spread(0.0, "US") == (2, "0.01")

    def test_unknown_market_fallback(self):
        """Fall back to default precision for unknown markets."""
        assert _precision_from_spread(None, "XX") == (2, "0.01")
        assert _precision_from_spread(None) == (2, "0.01")


class TestParseFutuInstrumentDispatcher:
    """Tests for parse_futu_instrument dispatcher."""

    def test_dispatcher_equity(self):
        """Dispatcher routes STOCK to Equity."""
        basic_info = {
            "code": "US.AAPL",
            "lot_size": 100,
            "stock_type": "STOCK",
        }
        result = parse_futu_instrument(basic_info)
        assert isinstance(result, Equity)

    def test_dispatcher_option(self):
        """Dispatcher routes OPTION to OptionContract."""
        basic_info = {
            "code": "US.AAPL240119C00150000",
            "lot_size": 100,
            "stock_type": "OPTION",
            "option_type": "CALL",
            "strike_price": 150.0,
            "strike_time": "2024-01-19",
            "stock_owner": "US.AAPL",
        }
        result = parse_futu_instrument(basic_info)
        assert isinstance(result, OptionContract)

    def test_dispatcher_future(self):
        """Dispatcher routes FUTURE to FuturesContract."""
        basic_info = {
            "code": "HK.HSI2401",
            "lot_size": 50,
            "stock_type": "FUTURE",
            "last_trade_time": "2024-01-30",
        }
        result = parse_futu_instrument(basic_info)
        assert isinstance(result, FuturesContract)

    def test_dispatcher_unknown_type_defaults_to_equity(self):
        """Unknown stock_type falls back to Equity with a warning."""
        basic_info = {
            "code": "US.XXX",
            "lot_size": 1,
            "stock_type": "UNKNOWN",
        }
        result = parse_futu_instrument(basic_info)
        assert isinstance(result, Equity)

    def test_dispatcher_malformed_returns_none(self):
        """Malformed input returns None instead of raising."""
        basic_info = {
            "code": "INVALID_CODE_NO_DOT",
            "stock_type": "STOCK",
        }
        result = parse_futu_instrument(basic_info)
        assert result is None


class TestParseDateToNs:
    """Tests for _parse_date_to_ns helper."""

    def test_valid_date(self):
        """Parse a valid YYYY-MM-DD string."""
        ns = _parse_date_to_ns("2024-01-19")
        assert ns > 0

    def test_empty_date(self):
        """Empty or N/A dates return 0."""
        assert _parse_date_to_ns("") == 0
        assert _parse_date_to_ns("N/A") == 0

    def test_invalid_date(self):
        """Invalid date strings return 0."""
        assert _parse_date_to_ns("not-a-date") == 0
