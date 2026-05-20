"""Futu adapter constants and enum mappings.

Adapted from nautilus-futu constants.py (MIT license).
Uses the official futu-api SDK enums/values.
"""

from __future__ import annotations

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

# ------------------------------------------------------------------------------
# Venue identifiers
# ------------------------------------------------------------------------------

FUTU_VENUE = Venue("FUTU")
HKEX_VENUE = Venue("HKEX")
NASDAQ_VENUE = Venue("NASDAQ")
NYSE_VENUE = Venue("NYSE")
SSE_VENUE = Venue("SSE")
SZSE_VENUE = Venue("SZSE")
SGX_VENUE = Venue("SGX")

# Futu market strings -> Nautilus Venue
FUTU_TO_NAUTILUS_VENUE: dict[str, Venue] = {
    "HK": HKEX_VENUE,
    "US": NASDAQ_VENUE,
    "SH": SSE_VENUE,
    "SZ": SZSE_VENUE,
}

# ------------------------------------------------------------------------------
# KLType -> BarType mapping
# ------------------------------------------------------------------------------

# Futu KLType values (from futu-api KLType.to_number())
FUTU_KL_TYPE_1MIN = 1
FUTU_KL_TYPE_DAY = 2
FUTU_KL_TYPE_WEEK = 3
FUTU_KL_TYPE_MONTH = 4
FUTU_KL_TYPE_YEAR = 5
FUTU_KL_TYPE_5MIN = 6
FUTU_KL_TYPE_15MIN = 7
FUTU_KL_TYPE_30MIN = 8
FUTU_KL_TYPE_60MIN = 9
FUTU_KL_TYPE_3MIN = 10
FUTU_KL_TYPE_10MIN = 12
FUTU_KL_TYPE_120MIN = 13
FUTU_KL_TYPE_240MIN = 15

_KL_TYPE_TO_BAR_SPEC: dict[int, BarSpecification] = {
    FUTU_KL_TYPE_1MIN: BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
    FUTU_KL_TYPE_3MIN: BarSpecification(3, BarAggregation.MINUTE, PriceType.LAST),
    FUTU_KL_TYPE_5MIN: BarSpecification(5, BarAggregation.MINUTE, PriceType.LAST),
    FUTU_KL_TYPE_10MIN: BarSpecification(10, BarAggregation.MINUTE, PriceType.LAST),
    FUTU_KL_TYPE_15MIN: BarSpecification(15, BarAggregation.MINUTE, PriceType.LAST),
    FUTU_KL_TYPE_30MIN: BarSpecification(30, BarAggregation.MINUTE, PriceType.LAST),
    FUTU_KL_TYPE_60MIN: BarSpecification(1, BarAggregation.HOUR, PriceType.LAST),
    FUTU_KL_TYPE_120MIN: BarSpecification(2, BarAggregation.HOUR, PriceType.LAST),
    FUTU_KL_TYPE_240MIN: BarSpecification(4, BarAggregation.HOUR, PriceType.LAST),
    FUTU_KL_TYPE_DAY: BarSpecification(1, BarAggregation.DAY, PriceType.LAST),
    FUTU_KL_TYPE_WEEK: BarSpecification(1, BarAggregation.WEEK, PriceType.LAST),
    FUTU_KL_TYPE_MONTH: BarSpecification(1, BarAggregation.MONTH, PriceType.LAST),
    FUTU_KL_TYPE_YEAR: BarSpecification(1, BarAggregation.YEAR, PriceType.LAST),
}


def futu_kl_type_to_bar_spec(kl_type: int) -> BarSpecification | None:
    """Convert Futu KLType integer to NautilusTrader BarSpecification."""
    return _KL_TYPE_TO_BAR_SPEC.get(kl_type)


def futu_kl_type_to_bar_type(
    kl_type: int,
    instrument_id: InstrumentId,
) -> BarType | None:
    """Convert Futu KLType integer to NautilusTrader BarType."""
    spec = futu_kl_type_to_bar_spec(kl_type)
    if spec is None:
        return None
    return BarType(instrument_id=instrument_id, bar_spec=spec)


# ------------------------------------------------------------------------------
# SecurityType -> InstrumentClass mapping
# ------------------------------------------------------------------------------

# Futu SecurityType values (from futu-api)
FUTU_SECURITY_TYPE_STOCK = 3
FUTU_SECURITY_TYPE_ETF = 4
FUTU_SECURITY_TYPE_WARRANT = 5
FUTU_SECURITY_TYPE_IDX = 6
FUTU_SECURITY_TYPE_BOND = 1
FUTU_SECURITY_TYPE_FUTURE = 10
FUTU_SECURITY_TYPE_DRVT = 8
FUTU_SECURITY_TYPE_CRYPTO = 12
FUTU_SECURITY_TYPE_BWRT = 2
FUTU_SECURITY_TYPE_PLATE = 7
FUTU_SECURITY_TYPE_PLATESET = 9

FUTU_SECURITY_TYPE_TO_INSTRUMENT_CLASS: dict[int, InstrumentClass] = {
    FUTU_SECURITY_TYPE_STOCK: InstrumentClass.SPOT,
    FUTU_SECURITY_TYPE_ETF: InstrumentClass.SPOT,
    FUTU_SECURITY_TYPE_WARRANT: InstrumentClass.WARRANT,
    FUTU_SECURITY_TYPE_BOND: InstrumentClass.BOND,
    FUTU_SECURITY_TYPE_FUTURE: InstrumentClass.FUTURE,
    FUTU_SECURITY_TYPE_DRVT: InstrumentClass.OPTION,
    FUTU_SECURITY_TYPE_CRYPTO: InstrumentClass.SPOT,
}


# ------------------------------------------------------------------------------
# OrderType enum mappings
# ------------------------------------------------------------------------------

# Futu OrderType values (from futu-api)
FUTU_ORDER_TYPE_NORMAL = 1
FUTU_ORDER_TYPE_MARKET = 2
FUTU_ORDER_TYPE_ABSOLUTE_LIMIT = 5
FUTU_ORDER_TYPE_AUCTION = 6
FUTU_ORDER_TYPE_AUCTION_LIMIT = 7
FUTU_ORDER_TYPE_SPECIAL_LIMIT = 8
FUTU_ORDER_TYPE_SPECIAL_LIMIT_ALL = 9
FUTU_ORDER_TYPE_STOP = 10
FUTU_ORDER_TYPE_STOP_LIMIT = 11
FUTU_ORDER_TYPE_MARKET_IF_TOUCHED = 12
FUTU_ORDER_TYPE_LIMIT_IF_TOUCHED = 13
FUTU_ORDER_TYPE_TRAILING_STOP = 14
FUTU_ORDER_TYPE_TRAILING_STOP_LIMIT = 15
FUTU_ORDER_TYPE_TWAP = 16
FUTU_ORDER_TYPE_TWAP_LIMIT = 17
FUTU_ORDER_TYPE_VWAP = 18
FUTU_ORDER_TYPE_VWAP_LIMIT = 19

FUTU_ORDER_TYPE_TO_NAUTILUS: dict[int, OrderType] = {
    FUTU_ORDER_TYPE_NORMAL: OrderType.LIMIT,
    FUTU_ORDER_TYPE_MARKET: OrderType.MARKET,
    FUTU_ORDER_TYPE_ABSOLUTE_LIMIT: OrderType.LIMIT,
    FUTU_ORDER_TYPE_STOP_LIMIT: OrderType.STOP_LIMIT,
    FUTU_ORDER_TYPE_STOP: OrderType.STOP_MARKET,
    FUTU_ORDER_TYPE_MARKET_IF_TOUCHED: OrderType.MARKET_IF_TOUCHED,
    FUTU_ORDER_TYPE_LIMIT_IF_TOUCHED: OrderType.LIMIT_IF_TOUCHED,
    FUTU_ORDER_TYPE_TRAILING_STOP: OrderType.TRAILING_STOP_MARKET,
    FUTU_ORDER_TYPE_TRAILING_STOP_LIMIT: OrderType.TRAILING_STOP_LIMIT,
}

NAUTILUS_ORDER_TYPE_TO_FUTU: dict[OrderType, int] = {
    OrderType.LIMIT: FUTU_ORDER_TYPE_NORMAL,
    OrderType.MARKET: FUTU_ORDER_TYPE_MARKET,
    OrderType.STOP_LIMIT: FUTU_ORDER_TYPE_STOP_LIMIT,
    OrderType.STOP_MARKET: FUTU_ORDER_TYPE_STOP,
    OrderType.MARKET_IF_TOUCHED: FUTU_ORDER_TYPE_MARKET_IF_TOUCHED,
    OrderType.LIMIT_IF_TOUCHED: FUTU_ORDER_TYPE_LIMIT_IF_TOUCHED,
    OrderType.TRAILING_STOP_MARKET: FUTU_ORDER_TYPE_TRAILING_STOP,
    OrderType.TRAILING_STOP_LIMIT: FUTU_ORDER_TYPE_TRAILING_STOP_LIMIT,
}


def futu_order_type_to_nautilus(order_type: int) -> OrderType:
    """Convert Futu OrderType to NautilusTrader OrderType."""
    result = FUTU_ORDER_TYPE_TO_NAUTILUS.get(order_type)
    if result is None:
        raise ValueError(f"Unsupported Futu order type: {order_type}")
    return result


def nautilus_order_type_to_futu(order_type: OrderType) -> int:
    """Convert NautilusTrader OrderType to Futu OrderType."""
    result = NAUTILUS_ORDER_TYPE_TO_FUTU.get(order_type)
    if result is None:
        raise ValueError(f"Unsupported Nautilus order type: {order_type}")
    return result


# ------------------------------------------------------------------------------
# Direction (TrdSide) enum mappings
# ------------------------------------------------------------------------------

# Futu TrdSide values (from futu-api)
FUTU_TRD_SIDE_BUY = 1
FUTU_TRD_SIDE_SELL = 2
FUTU_TRD_SIDE_SELL_SHORT = 3
FUTU_TRD_SIDE_BUY_BACK = 4


def futu_trd_side_to_nautilus(trd_side: int) -> OrderSide:
    """Convert Futu TrdSide to NautilusTrader OrderSide."""
    if trd_side in (FUTU_TRD_SIDE_BUY, FUTU_TRD_SIDE_BUY_BACK):
        return OrderSide.BUY
    elif trd_side in (FUTU_TRD_SIDE_SELL, FUTU_TRD_SIDE_SELL_SHORT):
        return OrderSide.SELL
    raise ValueError(f"Unsupported Futu trade side: {trd_side}")


def nautilus_order_side_to_futu(order_side: OrderSide) -> int:
    """Convert NautilusTrader OrderSide to Futu TrdSide."""
    if order_side == OrderSide.BUY:
        return FUTU_TRD_SIDE_BUY
    elif order_side == OrderSide.SELL:
        return FUTU_TRD_SIDE_SELL
    raise ValueError(f"Unsupported Nautilus order side: {order_side}")


# ------------------------------------------------------------------------------
# OrderStatus enum mappings
# ------------------------------------------------------------------------------

# Futu OrderStatus values (from futu-api)
FUTU_ORDER_STATUS_UNSUBMITTED = 0
FUTU_ORDER_STATUS_WAITING_SUBMIT = 1
FUTU_ORDER_STATUS_SUBMITTING = 2
FUTU_ORDER_STATUS_SUBMIT_FAILED = 3
FUTU_ORDER_STATUS_TIMEOUT = 4
FUTU_ORDER_STATUS_SUBMITTED = 5
FUTU_ORDER_STATUS_FILLED_PART = 10
FUTU_ORDER_STATUS_FILLED_ALL = 11
FUTU_ORDER_STATUS_CANCELLING_PART = 12
FUTU_ORDER_STATUS_CANCELLING_ALL = 13
FUTU_ORDER_STATUS_CANCELLED_PART = 14
FUTU_ORDER_STATUS_CANCELLED_ALL = 15
FUTU_ORDER_STATUS_FAILED = 21
FUTU_ORDER_STATUS_DISABLED = 22
FUTU_ORDER_STATUS_DELETED = 23
FUTU_ORDER_STATUS_FILL_CANCELLED = 24

FUTU_ORDER_STATUS_TO_NAUTILUS: dict[int, OrderStatus] = {
    FUTU_ORDER_STATUS_UNSUBMITTED: OrderStatus.INITIALIZED,
    FUTU_ORDER_STATUS_WAITING_SUBMIT: OrderStatus.SUBMITTED,
    FUTU_ORDER_STATUS_SUBMITTING: OrderStatus.SUBMITTED,
    FUTU_ORDER_STATUS_SUBMIT_FAILED: OrderStatus.REJECTED,
    FUTU_ORDER_STATUS_TIMEOUT: OrderStatus.REJECTED,
    FUTU_ORDER_STATUS_SUBMITTED: OrderStatus.ACCEPTED,
    FUTU_ORDER_STATUS_FILLED_PART: OrderStatus.PARTIALLY_FILLED,
    FUTU_ORDER_STATUS_FILLED_ALL: OrderStatus.FILLED,
    FUTU_ORDER_STATUS_CANCELLING_PART: OrderStatus.PENDING_CANCEL,
    FUTU_ORDER_STATUS_CANCELLING_ALL: OrderStatus.PENDING_CANCEL,
    FUTU_ORDER_STATUS_CANCELLED_PART: OrderStatus.CANCELED,
    FUTU_ORDER_STATUS_CANCELLED_ALL: OrderStatus.CANCELED,
    FUTU_ORDER_STATUS_DISABLED: OrderStatus.CANCELED,
    FUTU_ORDER_STATUS_DELETED: OrderStatus.CANCELED,
    FUTU_ORDER_STATUS_FILL_CANCELLED: OrderStatus.CANCELED,
    FUTU_ORDER_STATUS_FAILED: OrderStatus.REJECTED,
}


def futu_order_status_to_nautilus(status: int) -> OrderStatus:
    """Convert Futu OrderStatus to NautilusTrader OrderStatus."""
    result = FUTU_ORDER_STATUS_TO_NAUTILUS.get(status)
    if result is None:
        raise ValueError(f"Unsupported Futu order status: {status}")
    return result


# ------------------------------------------------------------------------------
# TrdMarket constants
# ------------------------------------------------------------------------------

# Futu TrdMarket values (from futu-api)
FUTU_TRD_MARKET_HK = 1
FUTU_TRD_MARKET_US = 2
FUTU_TRD_MARKET_CN = 3
FUTU_TRD_MARKET_HKCC = 4
FUTU_TRD_MARKET_FUTURES = 5
FUTU_TRD_MARKET_SG = 6
FUTU_TRD_MARKET_CRYPTO = 7
FUTU_TRD_MARKET_AU = 8
FUTU_TRD_MARKET_JP = 15
FUTU_TRD_MARKET_CA = 112
FUTU_TRD_MARKET_MY = 111
FUTU_TRD_MARKET_HKFUND = 113
FUTU_TRD_MARKET_USFUND = 123
FUTU_TRD_MARKET_SGFUND = 124
FUTU_TRD_MARKET_MYFUND = 125
FUTU_TRD_MARKET_JPFUND = 126

FUTU_TRD_MARKET_TO_VENUE: dict[int, Venue] = {
    FUTU_TRD_MARKET_HK: HKEX_VENUE,
    FUTU_TRD_MARKET_US: NASDAQ_VENUE,
    FUTU_TRD_MARKET_CN: SSE_VENUE,
    FUTU_TRD_MARKET_HKCC: HKEX_VENUE,
    FUTU_TRD_MARKET_FUTURES: HKEX_VENUE,
    FUTU_TRD_MARKET_SG: SGX_VENUE,
    FUTU_TRD_MARKET_AU: Venue("ASX"),
    FUTU_TRD_MARKET_JP: Venue("JPX"),
    FUTU_TRD_MARKET_CA: Venue("TSX"),
    FUTU_TRD_MARKET_MY: Venue("MYX"),
}

# Venue -> TrdMarket reverse mapping
NAUTILUS_VENUE_TO_FUTU_TRD_MARKET: dict[Venue, int] = {
    HKEX_VENUE: FUTU_TRD_MARKET_HK,
    NASDAQ_VENUE: FUTU_TRD_MARKET_US,
    NYSE_VENUE: FUTU_TRD_MARKET_US,
    SSE_VENUE: FUTU_TRD_MARKET_CN,
    SZSE_VENUE: FUTU_TRD_MARKET_CN,
    SGX_VENUE: FUTU_TRD_MARKET_SG,
}


# ------------------------------------------------------------------------------
# TrdEnv constants
# ------------------------------------------------------------------------------

# Futu TrdEnv values (from futu-api)
FUTU_TRD_ENV_SIMULATE = 0
FUTU_TRD_ENV_REAL = 1
