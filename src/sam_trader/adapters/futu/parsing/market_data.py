"""Futu market data parsing module.

Adapted from nautilus-futu parsing/market_data.py (MIT license).
Uses the official futu-api SDK handler base classes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pandas as pd
from futu import (
    CurKlineHandlerBase,
    OrderBookHandlerBase,
    StockQuoteHandlerBase,
    TickerHandlerBase,
)
from nautilus_trader.model.data import (
    Bar,
    BarType,
    BookOrder,
    OrderBookDelta,
    OrderBookDeltas,
    QuoteTick,
    TradeTick,
)
from nautilus_trader.model.enums import AggressorSide, BookAction, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

from sam_trader.adapters.futu.constants import FUTU_TO_NAUTILUS_VENUE


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------


def security_to_instrument_id(code: str) -> InstrumentId:
    """Convert Futu security code to NautilusTrader InstrumentId.

    Futu format: ``MARKET.SYMBOL`` (e.g., ``US.AAPL``, ``HK.00700``).
    Nautilus format: ``SYMBOL.VENUE`` (e.g., ``AAPL.NASDAQ``, ``00700.HKEX``).

    Parameters
    ----------
    code : str
        Futu security code string.

    Returns
    -------
    InstrumentId
        Mapped NautilusTrader instrument identifier.

    Raises
    ------
    ValueError
        If the code format is invalid or the market is unknown.

    """
    if "." not in code:
        raise ValueError(f"Invalid Futu security code format: {code}")

    market, symbol = code.split(".", 1)
    venue = FUTU_TO_NAUTILUS_VENUE.get(market)
    if venue is None:
        raise ValueError(f"Unknown Futu market: {market}")

    return InstrumentId.from_str(f"{symbol}.{venue.value}")


def _futu_timestamp_to_ns(ts: float | None) -> int:
    """Convert Futu timestamp (seconds) to nanoseconds."""
    if ts is None or ts == 0:
        return 0
    return int(ts * 1_000_000_000)


def _now_ns() -> int:
    """Current timestamp in nanoseconds."""
    return pd.Timestamp.now().value  # type: ignore[no-any-return]


# ------------------------------------------------------------------------------
# Parsing functions
# ------------------------------------------------------------------------------


def parse_futu_quote_tick(
    data: dict[str, Any],
    instrument_id: InstrumentId,
    ts_init: int,
) -> QuoteTick:
    """Parse Futu basic quote to NautilusTrader QuoteTick.

    Uses ``price_spread`` to derive bid/ask prices instead of fabricating
    a zero-spread tick from ``last_price`` alone.

    Parameters
    ----------
    data : dict[str, Any]
        Raw quote dict from Futu handler.
    instrument_id : InstrumentId
        Target Nautilus instrument identifier.
    ts_init : int
        Timestamp in nanoseconds.

    Returns
    -------
    QuoteTick

    """
    cur_price = data.get("last_price") or 0
    spread = data.get("price_spread") or 0
    bid_price = cur_price
    ask_price = cur_price + spread
    volume = max(data.get("volume") or 0, 1)  # avoid zero-quantity

    return QuoteTick(
        instrument_id=instrument_id,
        bid_price=Price.from_str(str(bid_price)),
        ask_price=Price.from_str(str(ask_price)),
        bid_size=Quantity.from_int(int(volume)),
        ask_size=Quantity.from_int(int(volume)),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def parse_futu_trade_tick(
    data: dict[str, Any],
    instrument_id: InstrumentId,
    ts_init: int,
) -> TradeTick:
    """Parse Futu ticker to NautilusTrader TradeTick.

    Parameters
    ----------
    data : dict[str, Any]
        Raw ticker dict from Futu handler.
    instrument_id : InstrumentId
        Target Nautilus instrument identifier.
    ts_init : int
        Timestamp in nanoseconds.

    Returns
    -------
    TradeTick

    """
    direction = data.get("ticker_direction", "N/A")
    if direction == "BUY":
        aggressor_side = AggressorSide.BUYER
    elif direction == "SELL":
        aggressor_side = AggressorSide.SELLER
    else:
        aggressor_side = AggressorSide.NO_AGGRESSOR

    return TradeTick(
        instrument_id=instrument_id,
        price=Price.from_str(str(data.get("price") or 0)),
        size=Quantity.from_int(max(data.get("volume") or 0, 1)),
        aggressor_side=aggressor_side,
        trade_id=TradeId(str(data.get("sequence", 0))),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def parse_futu_bars(
    kl_data: list[dict[str, Any]],
    bar_type: BarType,
) -> list[Bar]:
    """Parse Futu K-line data to NautilusTrader Bars.

    Parameters
    ----------
    kl_data : list[dict[str, Any]]
        List of raw K-line dicts from Futu handler.
    bar_type : BarType
        Target Nautilus bar type.

    Returns
    -------
    list[Bar]

    """
    bars: list[Bar] = []
    for kl in kl_data:
        if kl.get("is_blank", False):
            continue

        open_val = kl.get("open") or 0
        high_val = kl.get("high") or 0
        low_val = kl.get("low") or 0
        close_val = kl.get("close") or 0
        vol_val = max(kl.get("volume") or 0, 1)

        ts_val = kl.get("timestamp")
        if ts_val:
            ts_ns = _futu_timestamp_to_ns(ts_val)
        else:
            time_key = kl.get("time_key", "")
            if time_key:
                dt = datetime.strptime(time_key, "%Y-%m-%d %H:%M:%S")
                ts_ns = int(dt.timestamp() * 1_000_000_000)
            else:
                ts_ns = 0

        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(str(open_val)),
            high=Price.from_str(str(high_val)),
            low=Price.from_str(str(low_val)),
            close=Price.from_str(str(close_val)),
            volume=Quantity.from_int(int(vol_val)),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        bars.append(bar)

    return bars


def parse_futu_order_book(
    data: dict[str, Any],
    instrument_id: InstrumentId,
    ts_init: int,
) -> OrderBookDeltas:
    """Parse Futu push order book data to NautilusTrader OrderBookDeltas.

    Uses full snapshot mode: CLEAR then ADD for each level.

    Parameters
    ----------
    data : dict[str, Any]
        Raw order book dict from Futu handler.
    instrument_id : InstrumentId
        Target Nautilus instrument identifier.
    ts_init : int
        Timestamp in nanoseconds.

    Returns
    -------
    OrderBookDeltas

    """
    deltas: list[OrderBookDelta] = []

    # First delta: CLEAR the book
    deltas.append(
        OrderBookDelta.clear(
            instrument_id=instrument_id,
            ts_event=ts_init,
            ts_init=ts_init,
            sequence=0,
        )
    )

    # Add bid levels
    for bid in data.get("Bid", []):
        price = bid[0] if isinstance(bid, (list, tuple)) else bid.get("price")
        volume = bid[1] if isinstance(bid, (list, tuple)) else bid.get("volume")
        order = BookOrder(
            side=OrderSide.BUY,
            price=Price.from_str(str(price)),
            size=Quantity.from_int(int(volume)),
            order_id=0,
        )
        deltas.append(
            OrderBookDelta(
                instrument_id=instrument_id,
                action=BookAction.ADD,
                order=order,
                ts_event=ts_init,
                ts_init=ts_init,
                flags=0,
                sequence=0,
            )
        )

    # Add ask levels
    for ask in data.get("Ask", []):
        price = ask[0] if isinstance(ask, (list, tuple)) else ask.get("price")
        volume = ask[1] if isinstance(ask, (list, tuple)) else ask.get("volume")
        order = BookOrder(
            side=OrderSide.SELL,
            price=Price.from_str(str(price)),
            size=Quantity.from_int(int(volume)),
            order_id=0,
        )
        deltas.append(
            OrderBookDelta(
                instrument_id=instrument_id,
                action=BookAction.ADD,
                order=order,
                ts_event=ts_init,
                ts_init=ts_init,
                flags=0,
                sequence=0,
            )
        )

    return OrderBookDeltas(instrument_id=instrument_id, deltas=deltas)


# ------------------------------------------------------------------------------
# Handlers (callback → asyncio.Queue)
# ------------------------------------------------------------------------------


class StockQuoteHandler(StockQuoteHandlerBase):
    """Handler for Futu stock quote push data.

    Converts each row to a :class:`QuoteTick` and places it on the
    provided ``asyncio.Queue`` via ``loop.call_soon_threadsafe`` so
    that the callback (which runs on a Futu SDK background thread)
    does not block the event loop.

    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._loop = loop or asyncio.get_running_loop()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:  # type: ignore[override]
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != 0 or content is None or getattr(content, "empty", True):
            return ret_code, content

        ts_init = _now_ns()
        for _, row in content.iterrows():
            try:
                code = row.get("code")
                if not code:
                    continue
                instrument_id = security_to_instrument_id(code)
                tick = parse_futu_quote_tick(row.to_dict(), instrument_id, ts_init)
                self._loop.call_soon_threadsafe(self._queue.put_nowait, tick)
            except Exception:
                # Silently skip malformed rows; push loop must not die
                continue

        return ret_code, content


class CurKlineHandler(CurKlineHandlerBase):
    """Handler for Futu K-line push data.

    Converts each row to a :class:`Bar` and places it on the provided
    ``asyncio.Queue``.

    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        bar_type: BarType,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._bar_type = bar_type
        self._loop = loop or asyncio.get_running_loop()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:  # type: ignore[override]
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != 0 or content is None or getattr(content, "empty", True):
            return ret_code, content

        bars = parse_futu_bars(
            content.to_dict("records"),
            self._bar_type,
        )
        for bar in bars:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, bar)

        return ret_code, content


class TickerHandler(TickerHandlerBase):
    """Handler for Futu ticker push data.

    Converts each row to a :class:`TradeTick` and places it on the
    provided ``asyncio.Queue``.

    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._loop = loop or asyncio.get_running_loop()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:  # type: ignore[override]
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != 0 or content is None or getattr(content, "empty", True):
            return ret_code, content

        ts_init = _now_ns()
        for _, row in content.iterrows():
            try:
                code = row.get("code")
                if not code:
                    continue
                instrument_id = security_to_instrument_id(code)
                tick = parse_futu_trade_tick(row.to_dict(), instrument_id, ts_init)
                self._loop.call_soon_threadsafe(self._queue.put_nowait, tick)
            except Exception:
                continue

        return ret_code, content


class OrderBookHandler(OrderBookHandlerBase):
    """Handler for Futu order book push data.

    Converts the snapshot to :class:`OrderBookDeltas` and places it
    on the provided ``asyncio.Queue``.

    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._loop = loop or asyncio.get_running_loop()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:  # type: ignore[override]
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != 0 or content is None:
            return ret_code, content

        ts_init = _now_ns()
        try:
            code = content.get("code")
            if not code:
                return ret_code, content
            instrument_id = security_to_instrument_id(code)
            deltas = parse_futu_order_book(content, instrument_id, ts_init)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, deltas)
        except Exception:
            pass

        return ret_code, content
