"""Unit tests for Futu adapter constants."""

import pytest
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import (
    BarAggregation,
    InstrumentClass,
    OrderSide,
    OrderStatus,
    OrderType,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Venue

from sam_trader.adapters.futu import constants as c


class TestVenueMapping:
    """Tests for venue mapping constants."""

    def test_futu_to_nautilus_venue(self) -> None:
        assert c.FUTU_TO_NAUTILUS_VENUE["HK"] == Venue("HKEX")
        assert c.FUTU_TO_NAUTILUS_VENUE["US"] == Venue("NASDAQ")
        assert c.FUTU_TO_NAUTILUS_VENUE["SH"] == Venue("SSE")
        assert c.FUTU_TO_NAUTILUS_VENUE["SZ"] == Venue("SZSE")

    def test_venue_singletons(self) -> None:
        assert c.HKEX_VENUE == Venue("HKEX")
        assert c.NASDAQ_VENUE == Venue("NASDAQ")
        assert c.NYSE_VENUE == Venue("NYSE")
        assert c.SSE_VENUE == Venue("SSE")
        assert c.SZSE_VENUE == Venue("SZSE")
        assert c.SGX_VENUE == Venue("SGX")
        assert c.FUTU_VENUE == Venue("FUTU")

    def test_trd_market_to_venue(self) -> None:
        assert c.FUTU_TRD_MARKET_TO_VENUE[c.FUTU_TRD_MARKET_HK] == c.HKEX_VENUE
        assert c.FUTU_TRD_MARKET_TO_VENUE[c.FUTU_TRD_MARKET_US] == c.NASDAQ_VENUE
        assert c.FUTU_TRD_MARKET_TO_VENUE[c.FUTU_TRD_MARKET_CN] == c.SSE_VENUE

    def test_venue_to_trd_market(self) -> None:
        assert c.NAUTILUS_VENUE_TO_FUTU_TRD_MARKET[c.HKEX_VENUE] == c.FUTU_TRD_MARKET_HK
        assert (
            c.NAUTILUS_VENUE_TO_FUTU_TRD_MARKET[c.NASDAQ_VENUE] == c.FUTU_TRD_MARKET_US
        )
        assert c.NAUTILUS_VENUE_TO_FUTU_TRD_MARKET[c.NYSE_VENUE] == c.FUTU_TRD_MARKET_US
        assert c.NAUTILUS_VENUE_TO_FUTU_TRD_MARKET[c.SSE_VENUE] == c.FUTU_TRD_MARKET_CN
        assert c.NAUTILUS_VENUE_TO_FUTU_TRD_MARKET[c.SZSE_VENUE] == c.FUTU_TRD_MARKET_CN


class TestKLTypeToBarType:
    """Tests for KLType -> BarType mapping functions."""

    @pytest.fixture
    def instrument_id(self) -> InstrumentId:
        return InstrumentId.from_str("TSLA.NASDAQ")

    def test_1min(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_1MIN)
        assert spec == BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST)

        bar_type = c.futu_kl_type_to_bar_type(c.FUTU_KL_TYPE_1MIN, instrument_id)
        assert bar_type == BarType(
            instrument_id=instrument_id,
            bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        )

    def test_5min(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_5MIN)
        assert spec == BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST)

    def test_15min(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_15MIN)
        assert spec == BarSpecification(15, BarAggregation.MINUTE, PriceType.LAST)

    def test_60min_maps_to_hour(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_60MIN)
        assert spec == BarSpecification(1, BarAggregation.HOUR, PriceType.LAST)

    def test_120min_maps_to_2hour(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_120MIN)
        assert spec == BarSpecification(2, BarAggregation.HOUR, PriceType.LAST)

    def test_day(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_DAY)
        assert spec == BarSpecification(1, BarAggregation.DAY, PriceType.LAST)

    def test_week(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_WEEK)
        assert spec == BarSpecification(1, BarAggregation.WEEK, PriceType.LAST)

    def test_month(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_MONTH)
        assert spec == BarSpecification(1, BarAggregation.MONTH, PriceType.LAST)

    def test_year(self, instrument_id: InstrumentId) -> None:
        spec = c.futu_kl_type_to_bar_spec(c.FUTU_KL_TYPE_YEAR)
        assert spec == BarSpecification(1, BarAggregation.YEAR, PriceType.LAST)

    def test_unknown_returns_none(self, instrument_id: InstrumentId) -> None:
        assert c.futu_kl_type_to_bar_spec(9999) is None
        assert c.futu_kl_type_to_bar_type(9999, instrument_id) is None


class TestSecurityTypeMapping:
    """Tests for SecurityType -> InstrumentClass mapping."""

    def test_stock_to_spot(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_STOCK]
            == InstrumentClass.SPOT
        )

    def test_etf_to_spot(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_ETF]
            == InstrumentClass.SPOT
        )

    def test_warrant_to_warrant(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_WARRANT]
            == InstrumentClass.WARRANT
        )

    def test_bond_to_bond(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_BOND]
            == InstrumentClass.BOND
        )

    def test_future_to_future(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_FUTURE]
            == InstrumentClass.FUTURE
        )

    def test_drvt_to_option(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_DRVT]
            == InstrumentClass.OPTION
        )

    def test_crypto_to_spot(self) -> None:
        assert (
            c.FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS[c.FUTU_SECURITY_TYPE_CRYPTO]
            == InstrumentClass.SPOT
        )


class TestOrderTypeMapping:
    """Tests for OrderType enum mappings."""

    def test_normal_to_limit(self) -> None:
        assert (
            c.futu_order_type_to_nautilus(c.FUTU_ORDER_TYPE_NORMAL) == OrderType.LIMIT
        )

    def test_market_to_market(self) -> None:
        assert (
            c.futu_order_type_to_nautilus(c.FUTU_ORDER_TYPE_MARKET) == OrderType.MARKET
        )

    def test_stop_limit_to_stop_limit(self) -> None:
        assert (
            c.futu_order_type_to_nautilus(c.FUTU_ORDER_TYPE_STOP_LIMIT)
            == OrderType.STOP_LIMIT
        )

    def test_nautilus_limit_to_futu(self) -> None:
        assert c.nautilus_order_type_to_futu(OrderType.LIMIT) == "NORMAL"

    def test_nautilus_market_to_futu(self) -> None:
        assert c.nautilus_order_type_to_futu(OrderType.MARKET) == "MARKET"

    def test_unsupported_futu_raises(self) -> None:
        with pytest.raises(ValueError):
            c.futu_order_type_to_nautilus(9999)

    def test_unsupported_nautilus_raises(self) -> None:
        with pytest.raises(ValueError):
            c.nautilus_order_type_to_futu(OrderType.MARKET_TO_LIMIT)


class TestDirectionMapping:
    """Tests for TrdSide (Direction) enum mappings."""

    def test_buy_to_buy(self) -> None:
        assert c.futu_trd_side_to_nautilus(c.FUTU_TRD_SIDE_BUY) == OrderSide.BUY

    def test_buy_back_to_buy(self) -> None:
        assert c.futu_trd_side_to_nautilus(c.FUTU_TRD_SIDE_BUY_BACK) == OrderSide.BUY

    def test_sell_to_sell(self) -> None:
        assert c.futu_trd_side_to_nautilus(c.FUTU_TRD_SIDE_SELL) == OrderSide.SELL

    def test_sell_short_to_sell(self) -> None:
        assert c.futu_trd_side_to_nautilus(c.FUTU_TRD_SIDE_SELL_SHORT) == OrderSide.SELL

    def test_nautilus_buy_to_futu(self) -> None:
        assert c.nautilus_order_side_to_futu(OrderSide.BUY) == "BUY"

    def test_nautilus_sell_to_futu(self) -> None:
        assert c.nautilus_order_side_to_futu(OrderSide.SELL) == "SELL"

    def test_invalid_trd_side_raises(self) -> None:
        with pytest.raises(ValueError):
            c.futu_trd_side_to_nautilus(9999)


class TestOrderStatusMapping:
    """Tests for OrderStatus enum mappings."""

    def test_unsubmitted_to_initialized(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_UNSUBMITTED)
            == OrderStatus.INITIALIZED
        )

    def test_submitted_to_accepted(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_SUBMITTED)
            == OrderStatus.ACCEPTED
        )

    def test_filled_all_to_filled(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_FILLED_ALL)
            == OrderStatus.FILLED
        )

    def test_filled_part_to_partially_filled(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_FILLED_PART)
            == OrderStatus.PARTIALLY_FILLED
        )

    def test_cancelled_all_to_canceled(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_CANCELLED_ALL)
            == OrderStatus.CANCELED
        )

    def test_cancelling_to_pending_cancel(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_CANCELLING_ALL)
            == OrderStatus.PENDING_CANCEL
        )

    def test_failed_to_rejected(self) -> None:
        assert (
            c.futu_order_status_to_nautilus(c.FUTU_ORDER_STATUS_FAILED)
            == OrderStatus.REJECTED
        )

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError):
            c.futu_order_status_to_nautilus(9999)


class TestTrdEnvConstants:
    """Tests for TrdEnv constants."""

    def test_simulate(self) -> None:
        assert c.FUTU_TRD_ENV_SIMULATE == 0

    def test_real(self) -> None:
        assert c.FUTU_TRD_ENV_REAL == 1


class TestTrdMarketConstants:
    """Tests for TrdMarket constants."""

    def test_hk(self) -> None:
        assert c.FUTU_TRD_MARKET_HK == 1

    def test_us(self) -> None:
        assert c.FUTU_TRD_MARKET_US == 2

    def test_cn(self) -> None:
        assert c.FUTU_TRD_MARKET_CN == 3
