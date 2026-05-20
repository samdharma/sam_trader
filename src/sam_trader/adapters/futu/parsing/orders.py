"""Futu order parsing module.

Adapted from nautilus-futu parsing/orders.py (MIT license).
Uses the official futu-api SDK handler base classes.

Maps Futu TradeOrderHandler / TradeDealHandler push data to
NautilusTrader OrderStatusReport, FillReport, and PositionStatusReport.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from futu import (
    TradeDealHandlerBase,
    TradeOrderHandlerBase,
)
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import (
    FillReport,
    OrderStatusReport,
    PositionStatusReport,
)
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from sam_trader.adapters.futu.constants import (
    FUTU_ORDER_STATUS_CANCELLED_ALL,
    FUTU_ORDER_STATUS_CANCELLED_PART,
    FUTU_ORDER_STATUS_CANCELLING_ALL,
    FUTU_ORDER_STATUS_CANCELLING_PART,
    FUTU_ORDER_STATUS_DELETED,
    FUTU_ORDER_STATUS_DISABLED,
    FUTU_ORDER_STATUS_FAILED,
    FUTU_ORDER_STATUS_FILL_CANCELLED,
    FUTU_ORDER_STATUS_FILLED_ALL,
    FUTU_ORDER_STATUS_FILLED_PART,
    FUTU_ORDER_STATUS_SUBMIT_FAILED,
    FUTU_ORDER_STATUS_SUBMITTED,
    FUTU_ORDER_STATUS_SUBMITTING,
    FUTU_ORDER_STATUS_TIMEOUT,
    FUTU_ORDER_STATUS_UNSUBMITTED,
    FUTU_ORDER_STATUS_WAITING_SUBMIT,
    FUTU_ORDER_TYPE_MARKET,
    FUTU_ORDER_TYPE_NORMAL,
    FUTU_POSITION_SIDE_LONG,
    FUTU_POSITION_SIDE_SHORT,
    FUTU_TRD_SIDE_BUY,
    FUTU_TRD_SIDE_BUY_BACK,
    FUTU_TRD_SIDE_SELL,
    FUTU_TRD_SIDE_SELL_SHORT,
    futu_time_in_force_to_nautilus,
)
from sam_trader.adapters.futu.parsing.market_data import security_to_instrument_id

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# String / int normalisation helpers
# ------------------------------------------------------------------------------

_TRD_SIDE_STR_TO_INT: dict[str, int] = {
    "BUY": FUTU_TRD_SIDE_BUY,
    "SELL": FUTU_TRD_SIDE_SELL,
    "SELL_SHORT": FUTU_TRD_SIDE_SELL_SHORT,
    "BUY_BACK": FUTU_TRD_SIDE_BUY_BACK,
}

_ORDER_STATUS_STR_TO_INT: dict[str, int] = {
    "UNSUBMITTED": FUTU_ORDER_STATUS_UNSUBMITTED,
    "WAITING_SUBMIT": FUTU_ORDER_STATUS_WAITING_SUBMIT,
    "SUBMITTING": FUTU_ORDER_STATUS_SUBMITTING,
    "SUBMIT_FAILED": FUTU_ORDER_STATUS_SUBMIT_FAILED,
    "TIMEOUT": FUTU_ORDER_STATUS_TIMEOUT,
    "SUBMITTED": FUTU_ORDER_STATUS_SUBMITTED,
    "FILLED_PART": FUTU_ORDER_STATUS_FILLED_PART,
    "FILLED_ALL": FUTU_ORDER_STATUS_FILLED_ALL,
    "CANCELLING_PART": FUTU_ORDER_STATUS_CANCELLING_PART,
    "CANCELLING_ALL": FUTU_ORDER_STATUS_CANCELLING_ALL,
    "CANCELLED_PART": FUTU_ORDER_STATUS_CANCELLED_PART,
    "CANCELLED_ALL": FUTU_ORDER_STATUS_CANCELLED_ALL,
    "FAILED": FUTU_ORDER_STATUS_FAILED,
    "DISABLED": FUTU_ORDER_STATUS_DISABLED,
    "DELETED": FUTU_ORDER_STATUS_DELETED,
    "FILL_CANCELLED": FUTU_ORDER_STATUS_FILL_CANCELLED,
}

_ORDER_TYPE_STR_TO_INT: dict[str, int] = {
    "NORMAL": FUTU_ORDER_TYPE_NORMAL,
    "MARKET": FUTU_ORDER_TYPE_MARKET,
}

_TIF_STR_TO_INT: dict[str, int] = {
    "DAY": 0,
    "GTC": 1,
    "IOC": 2,
}

_POSITION_SIDE_STR_TO_INT: dict[str, int] = {
    "LONG": FUTU_POSITION_SIDE_LONG,
    "SHORT": FUTU_POSITION_SIDE_SHORT,
}


def _normalize_trd_side(value: int | str) -> int:
    if isinstance(value, int):
        return value
    result = _TRD_SIDE_STR_TO_INT.get(value)
    if result is None:
        raise ValueError(f"Unsupported Futu trade side: {value}")
    return result


def _normalize_order_status(value: int | str) -> int:
    if isinstance(value, int):
        return value
    result = _ORDER_STATUS_STR_TO_INT.get(value)
    if result is None:
        raise ValueError(f"Unsupported Futu order status: {value}")
    return result


def _normalize_order_type(value: int | str) -> int:
    if isinstance(value, int):
        return value
    result = _ORDER_TYPE_STR_TO_INT.get(value)
    if result is None:
        # Unknown order types default to LIMIT (consistent with nautilus-futu)
        logger.warning("Unknown Futu order type %s, defaulting to LIMIT", value)
        return FUTU_ORDER_TYPE_NORMAL
    return result


def _normalize_tif(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    result = _TIF_STR_TO_INT.get(value)
    if result is None:
        return None
    return result


def _normalize_position_side(value: int | str) -> int:
    if isinstance(value, int):
        return value
    result = _POSITION_SIDE_STR_TO_INT.get(value)
    if result is None:
        # Default to LONG if unknown
        return FUTU_POSITION_SIDE_LONG
    return result


# ------------------------------------------------------------------------------
# Timestamp helpers
# ------------------------------------------------------------------------------

_FUTU_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_futu_timestamp(value: Any) -> int:
    """Convert Futu timestamp to nanoseconds.

    Handles float (seconds since epoch) or string (``YYYY-MM-DD HH:MM:SS``).

    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value * 1_000_000_000)
    if isinstance(value, str):
        value = value.strip()
        if not value or value in ("N/A", ""):
            return 0
        try:
            dt = datetime.strptime(value, _FUTU_TIME_FMT)
            return int(dt.timestamp() * 1_000_000_000)
        except ValueError:
            return 0
    return 0


# ------------------------------------------------------------------------------
# Core parsing functions
# ------------------------------------------------------------------------------


def parse_futu_order_to_report(
    order: dict[str, Any],
    account_id: AccountId,
) -> OrderStatusReport:
    """Parse a Futu order dict to NautilusTrader OrderStatusReport.

    Parameters
    ----------
    order : dict[str, Any]
        Order dictionary from Futu SDK (e.g. ``UpdateOrderPush.unpack_rsp``).
    account_id : AccountId
        The account ID.

    Returns
    -------
    OrderStatusReport

    """
    code = order["code"]
    instrument_id = security_to_instrument_id(code)

    order_side = _trd_side_to_nautilus(_normalize_trd_side(order["trd_side"]))
    order_type = _order_type_to_nautilus(
        _normalize_order_type(order.get("order_type", "NORMAL"))
    )
    order_status = _order_status_to_nautilus(
        _normalize_order_status(order["order_status"])
    )
    time_in_force = futu_time_in_force_to_nautilus(
        _normalize_tif(order.get("time_in_force")),
    )

    qty = Quantity.from_str(str(order.get("qty") or 0))
    dealt_qty = Quantity.from_str(str(order.get("dealt_qty") or 0))
    price_raw = order.get("price")
    price = Price.from_str(str(price_raw)) if price_raw is not None else None
    avg_px = (
        Decimal(str(order.get("dealt_avg_price") or 0))
        if order.get("dealt_avg_price")
        else None
    )

    ts_accepted = _parse_futu_timestamp(
        order.get("create_time") or order.get("create_timestamp")
    )
    ts_last = _parse_futu_timestamp(
        order.get("updated_time") or order.get("update_timestamp")
    )

    return OrderStatusReport(
        account_id=account_id,
        instrument_id=instrument_id,
        venue_order_id=VenueOrderId(str(order["order_id"])),
        order_side=order_side,
        order_type=order_type,
        time_in_force=time_in_force,
        order_status=order_status,
        quantity=qty,
        filled_qty=dealt_qty,
        price=price,
        avg_px=avg_px,
        report_id=UUID4(),
        ts_accepted=ts_accepted,
        ts_last=ts_last,
        ts_init=ts_last,
    )


def parse_futu_fill_to_report(
    fill: dict[str, Any],
    account_id: AccountId,
) -> FillReport:
    """Parse a Futu deal/fill dict to NautilusTrader FillReport.

    Parameters
    ----------
    fill : dict[str, Any]
        Fill dictionary from Futu SDK (e.g. ``UpdateDealPush.unpack_rsp``).
    account_id : AccountId
        The account ID.

    Returns
    -------
    FillReport

    """
    code = fill["code"]
    instrument_id = security_to_instrument_id(code)

    order_side = _trd_side_to_nautilus(_normalize_trd_side(fill["trd_side"]))

    ts_event = _parse_futu_timestamp(
        fill.get("create_time") or fill.get("create_timestamp")
    )

    # Derive currency from instrument venue for commission
    currency = _venue_to_currency(instrument_id.venue)
    commission = Money(0, currency)

    return FillReport(
        account_id=account_id,
        instrument_id=instrument_id,
        venue_order_id=VenueOrderId(str(fill.get("order_id") or 0)),
        trade_id=TradeId(str(fill["deal_id"])),
        order_side=order_side,
        last_qty=Quantity.from_str(str(fill.get("qty") or 0)),
        last_px=Price.from_str(str(fill.get("price") or 0)),
        commission=commission,
        liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
        report_id=UUID4(),
        ts_event=ts_event,
        ts_init=ts_event,
    )


def parse_futu_position_to_report(
    position: dict[str, Any],
    account_id: AccountId,
) -> PositionStatusReport:
    """Parse a Futu position dict to NautilusTrader PositionStatusReport.

    Parameters
    ----------
    position : dict[str, Any]
        Position dictionary from Futu SDK (e.g. ``get_position_list``).
    account_id : AccountId
        The account ID.

    Returns
    -------
    PositionStatusReport

    """
    code = position["code"]
    instrument_id = security_to_instrument_id(code)

    qty = position.get("qty", 0)
    position_side_int = _normalize_position_side(
        position.get("position_side", FUTU_POSITION_SIDE_LONG)
    )

    if qty == 0:
        position_side = PositionSide.FLAT
    elif position_side_int == FUTU_POSITION_SIDE_SHORT:
        position_side = PositionSide.SHORT
    else:
        position_side = PositionSide.LONG

    avg_px_open = (
        Decimal(str(position.get("cost_price") or 0))
        if position.get("cost_price")
        else None
    )

    return PositionStatusReport(
        account_id=account_id,
        instrument_id=instrument_id,
        position_side=position_side,
        quantity=Quantity.from_str(str(abs(qty))),
        report_id=UUID4(),
        ts_last=0,
        ts_init=0,
        avg_px_open=avg_px_open,
    )


# ------------------------------------------------------------------------------
# Enum mapping helpers (private)
# ------------------------------------------------------------------------------


def _trd_side_to_nautilus(trd_side: int) -> OrderSide:
    if trd_side in (FUTU_TRD_SIDE_BUY, FUTU_TRD_SIDE_BUY_BACK):
        return OrderSide.BUY
    elif trd_side in (FUTU_TRD_SIDE_SELL, FUTU_TRD_SIDE_SELL_SHORT):
        return OrderSide.SELL
    raise ValueError(f"Unsupported Futu trade side: {trd_side}")


def _order_type_to_nautilus(order_type: int) -> OrderType:
    if order_type == FUTU_ORDER_TYPE_NORMAL:
        return OrderType.LIMIT
    elif order_type == FUTU_ORDER_TYPE_MARKET:
        return OrderType.MARKET
    logger.warning("Unknown Futu order type %d, defaulting to LIMIT", order_type)
    return OrderType.LIMIT


def _order_status_to_nautilus(status: int) -> OrderStatus:
    if status in (FUTU_ORDER_STATUS_UNSUBMITTED,):
        return OrderStatus.INITIALIZED
    elif status in (FUTU_ORDER_STATUS_WAITING_SUBMIT, FUTU_ORDER_STATUS_SUBMITTING):
        return OrderStatus.SUBMITTED
    elif status in (FUTU_ORDER_STATUS_SUBMIT_FAILED, FUTU_ORDER_STATUS_TIMEOUT):
        return OrderStatus.REJECTED
    elif status == FUTU_ORDER_STATUS_SUBMITTED:
        return OrderStatus.ACCEPTED
    elif status == FUTU_ORDER_STATUS_FILLED_PART:
        return OrderStatus.PARTIALLY_FILLED
    elif status == FUTU_ORDER_STATUS_FILLED_ALL:
        return OrderStatus.FILLED
    elif status in (
        FUTU_ORDER_STATUS_CANCELLING_PART,
        FUTU_ORDER_STATUS_CANCELLING_ALL,
    ):
        return OrderStatus.PENDING_CANCEL
    elif status in (
        FUTU_ORDER_STATUS_CANCELLED_PART,
        FUTU_ORDER_STATUS_CANCELLED_ALL,
        FUTU_ORDER_STATUS_DISABLED,
        FUTU_ORDER_STATUS_DELETED,
        FUTU_ORDER_STATUS_FILL_CANCELLED,
    ):
        return OrderStatus.CANCELED
    elif status == FUTU_ORDER_STATUS_FAILED:
        return OrderStatus.REJECTED
    else:
        logger.warning(
            "Unknown Futu order status %d, defaulting to INITIALIZED", status
        )
        return OrderStatus.INITIALIZED


def _venue_to_currency(venue: Any) -> Currency:
    """Map Nautilus venue to default currency for commission."""
    venue_str = str(venue)
    mapping = {
        "HKEX": "HKD",
        "NASDAQ": "USD",
        "NYSE": "USD",
        "SSE": "CNY",
        "SZSE": "CNY",
        "SGX": "SGD",
    }
    code = mapping.get(venue_str, "USD")
    return Currency.from_str(code)


# ------------------------------------------------------------------------------
# Push handlers (callback → asyncio.Queue)
# ------------------------------------------------------------------------------


class TradeOrderHandler(TradeOrderHandlerBase):
    """Handler for Futu order push data.

    Converts each row to an :class:`OrderStatusReport` and places it on the
    provided ``asyncio.Queue`` via ``loop.call_soon_threadsafe`` so that the
    callback (which runs on a Futu SDK background thread) does not block the
    event loop.

    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        account_id: AccountId,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._account_id = account_id
        self._loop = loop or asyncio.get_running_loop()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:  # type: ignore[override]
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != 0 or content is None or getattr(content, "empty", True):
            return ret_code, content

        for _, row in content.iterrows():
            try:
                order_dict = row.to_dict()
                report = parse_futu_order_to_report(order_dict, self._account_id)
                self._loop.call_soon_threadsafe(self._queue.put_nowait, report)
            except Exception:
                # Silently skip malformed rows; push loop must not die
                continue

        return ret_code, content


class TradeDealHandler(TradeDealHandlerBase):
    """Handler for Futu deal/fill push data.

    Converts each row to a :class:`FillReport` and places it on the provided
    ``asyncio.Queue`` via ``loop.call_soon_threadsafe``.

    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        account_id: AccountId,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._account_id = account_id
        self._loop = loop or asyncio.get_running_loop()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:  # type: ignore[override]
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != 0 or content is None or getattr(content, "empty", True):
            return ret_code, content

        for _, row in content.iterrows():
            try:
                fill_dict = row.to_dict()
                report = parse_futu_fill_to_report(fill_dict, self._account_id)
                self._loop.call_soon_threadsafe(self._queue.put_nowait, report)
            except Exception:
                continue

        return ret_code, content
