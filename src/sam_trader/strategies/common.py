"""Venue-aware order helpers for SAM Trader strategies.

Centralises broker-specific order defaults so that strategies do not need to
scatter venue conditionals throughout their entry/exit logic.

Example
-------
>>> from sam_trader.strategies.common import make_bracket, make_limit
>>> bracket = make_bracket(
...     self.order_factory,
...     instrument_id=self.instrument_id,
...     order_side=OrderSide.BUY,
...     quantity=Quantity.from_int(100),
...     entry=Price.from_str("150.00"),
...     stop_loss=Price.from_str("145.00"),
...     take_profit=Price.from_str("160.00"),
... )
>>> self.submit_order_list(bracket)
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.orders import LimitOrder, OrderList

IB_VENUE = Venue("IB")
FUTU_VENUE = Venue("FUTU")


def _is_ib_venue(instrument_id: Any) -> bool:
    """Return ``True`` when *instrument_id* targets the IB venue."""
    venue = getattr(instrument_id, "venue", None)
    if venue is None:
        return False
    return bool(venue == IB_VENUE)


def make_bracket(
    order_factory: Any,
    *,
    instrument_id: Any,
    **kwargs: Any,
) -> OrderList:
    """Build a bracket order with venue-safe defaults.

    For the IB venue, ``tp_post_only`` is automatically set to ``False`` to
    avoid order rejection — Interactive Brokers does not support the
    ``post_only`` attribute.  The explicit value is only injected when the
    caller has not already supplied one, so strategies can still override it
    if required.

    Parameters
    ----------
    order_factory : Any
        The strategy's ``self.order_factory`` (usually a
        ``Nautilus OrderFactory``).
    instrument_id : InstrumentId
        Identifier used to infer the target venue.
    **kwargs : Any
        Forwarded to ``order_factory.bracket()``.

    Returns
    -------
    OrderList
        The bracket order list created by the factory.

    """
    if _is_ib_venue(instrument_id):
        kwargs.setdefault("tp_post_only", False)
    return order_factory.bracket(instrument_id=instrument_id, **kwargs)


def make_limit(
    order_factory: Any,
    *,
    instrument_id: Any,
    **kwargs: Any,
) -> LimitOrder:
    """Build a limit order with venue-safe defaults.

    For the IB venue, ``post_only`` is automatically set to ``False`` to
    avoid order rejection — Interactive Brokers does not support the
    ``post_only`` attribute.  The explicit value is only injected when the
    caller has not already supplied one, so strategies can still override it
    if required.

    Parameters
    ----------
    order_factory : Any
        The strategy's ``self.order_factory``.
    instrument_id : InstrumentId
        Identifier used to infer the target venue.
    **kwargs : Any
        Forwarded to ``order_factory.limit()``.

    Returns
    -------
    LimitOrder
        The limit order created by the factory.

    """
    if _is_ib_venue(instrument_id):
        kwargs.setdefault("post_only", False)
    return order_factory.limit(instrument_id=instrument_id, **kwargs)
