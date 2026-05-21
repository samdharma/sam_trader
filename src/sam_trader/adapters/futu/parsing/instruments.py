"""Futu instrument parsing module.

Adapted from nautilus-futu parsing/instruments.py (MIT license).
Maps Futu basic info data to NautilusTrader Equity, OptionContract,
and FuturesContract.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from nautilus_trader.model.enums import AssetClass, OptionKind
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import Equity, FuturesContract, OptionContract
from nautilus_trader.model.objects import Currency, Price, Quantity

from sam_trader.adapters.futu.parsing.market_data import security_to_instrument_id

logger = logging.getLogger(__name__)

# Market-based default price precision (used when price_spread is unavailable)
_PRECISION_MAP: dict[str, tuple[int, str]] = {
    "US": (2, "0.01"),
    "HK": (3, "0.001"),
    "SH": (2, "0.01"),
    "SZ": (2, "0.01"),
}
_DEFAULT_PRECISION = (2, "0.01")


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------


def _market_from_code(code: str) -> str:
    """Extract market prefix from Futu code like ``US.AAPL``."""
    if "." in code:
        return code.split(".", 1)[0]
    return ""


def _venue_to_currency(venue: Any) -> Currency:
    """Map Nautilus venue to default currency."""
    mapping = {
        "HKEX": "HKD",
        "NASDAQ": "USD",
        "NYSE": "USD",
        "SSE": "CNY",
        "SZSE": "CNY",
        "SGX": "SGD",
    }
    code = mapping.get(str(venue), "USD")
    return Currency.from_str(code)


def _precision_from_spread(spread: float | None, market: str = "") -> tuple[int, str]:
    """Derive price precision and increment from tick spread.

    Falls back to market-based defaults when spread is unavailable or zero.

    Parameters
    ----------
    spread : float | None
        Tick size / price spread from Futu API.
    market : str
        Futu market prefix (e.g., ``US``, ``HK``).

    Returns
    -------
    tuple[int, str]
        (price_precision, price_increment_str)

    """
    if spread is not None and spread > 0:
        s = f"{spread:.10f}".rstrip("0")
        decimals = len(s.split(".")[-1]) if "." in s else 0
        return decimals, str(spread)
    return _PRECISION_MAP.get(market, _DEFAULT_PRECISION)


def _parse_date_to_ns(date_str: str) -> int:
    """Parse ``YYYY-MM-DD`` string to nanoseconds since epoch."""
    if not date_str or date_str in ("N/A", ""):
        return 0
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp() * 1_000_000_000)
    except ValueError:
        return 0


# ------------------------------------------------------------------------------
# Public dispatcher
# ------------------------------------------------------------------------------


def parse_futu_instrument(
    basic_info: dict[str, Any],
) -> Equity | OptionContract | FuturesContract | None:
    """Parse Futu basic info dict to NautilusTrader instrument.

    Dispatches by ``stock_type``:

    - ``STOCK``, ``ETF``, ``WARRANT``, ``DRVT`` → :class:`Equity`
    - ``OPTION`` → :class:`OptionContract`
    - ``FUTURE`` → :class:`FuturesContract`

    Parameters
    ----------
    basic_info : dict[str, Any]
        Dictionary from Futu ``get_stock_basicinfo`` (or similar) API.

    Returns
    -------
    Equity | OptionContract | FuturesContract | None
        Parsed NautilusTrader instrument, or ``None`` on failure.

    """
    try:
        code = basic_info.get("code", "")
        stock_type = basic_info.get("stock_type", "STOCK")

        if stock_type == "OPTION":
            return _parse_futu_option(basic_info)
        elif stock_type == "FUTURE":
            return _parse_futu_future(basic_info)
        elif stock_type in ("STOCK", "ETF", "WARRANT", "DRVT"):
            return _parse_futu_equity(basic_info)
        else:
            logger.warning(
                "Unknown stock_type %s for %s, treating as Equity",
                stock_type,
                code,
            )
            return _parse_futu_equity(basic_info)
    except Exception as e:
        logger.warning("Failed to parse instrument: %s", e)
        return None


# ------------------------------------------------------------------------------
# Equity
# ------------------------------------------------------------------------------


def _parse_futu_equity(basic_info: dict[str, Any]) -> Equity:
    """Parse Futu stock basic info to NautilusTrader :class:`Equity`.

    Parameters
    ----------
    basic_info : dict[str, Any]
        Dictionary from Futu ``get_stock_basicinfo`` with ``stock_type=STOCK``.

    Returns
    -------
    Equity

    """
    code = basic_info.get("code", "")
    lot_size = basic_info.get("lot_size", 1)
    market = _market_from_code(code)
    spread = basic_info.get("price_spread")
    precision, increment = _precision_from_spread(spread, market)

    instrument_id = security_to_instrument_id(code)
    currency = _venue_to_currency(instrument_id.venue)

    return Equity(
        instrument_id=instrument_id,
        raw_symbol=Symbol(code),
        currency=currency,
        price_precision=precision,
        price_increment=Price.from_str(increment),
        lot_size=Quantity.from_int(int(lot_size)),
        ts_event=0,
        ts_init=0,
    )


# ------------------------------------------------------------------------------
# OptionContract
# ------------------------------------------------------------------------------


def _parse_futu_option(basic_info: dict[str, Any]) -> OptionContract:
    """Parse Futu option basic info to NautilusTrader :class:`OptionContract`.

    Parameters
    ----------
    basic_info : dict[str, Any]
        Dictionary from Futu ``get_stock_basicinfo`` with ``stock_type=OPTION``.

    Returns
    -------
    OptionContract

    """
    code = basic_info.get("code", "")
    lot_size = basic_info.get("lot_size", 1)

    option_type = basic_info.get("option_type", "CALL")
    option_kind = OptionKind.CALL if option_type == "CALL" else OptionKind.PUT

    strike_price_val = basic_info.get("strike_price", 0.0)
    strike_time = basic_info.get("strike_time", "")
    expiration_ns = _parse_date_to_ns(strike_time)

    market = _market_from_code(code)
    spread = basic_info.get("price_spread")
    precision, increment = _precision_from_spread(spread, market)

    instrument_id = security_to_instrument_id(code)
    currency = _venue_to_currency(instrument_id.venue)
    underlying = basic_info.get("stock_owner", "")

    return OptionContract(
        instrument_id=instrument_id,
        raw_symbol=Symbol(code),
        asset_class=AssetClass.EQUITY,
        currency=currency,
        price_precision=precision,
        price_increment=Price.from_str(increment),
        multiplier=Quantity.from_int(int(lot_size)),
        lot_size=Quantity.from_int(int(lot_size)),
        underlying=underlying,
        option_kind=option_kind,
        strike_price=Price.from_str(str(strike_price_val)),
        activation_ns=0,
        expiration_ns=expiration_ns,
        ts_event=0,
        ts_init=0,
    )


# ------------------------------------------------------------------------------
# FuturesContract
# ------------------------------------------------------------------------------


def _parse_futu_future(basic_info: dict[str, Any]) -> FuturesContract:
    """Parse Futu future basic info to NautilusTrader :class:`FuturesContract`.

    Parameters
    ----------
    basic_info : dict[str, Any]
        Dictionary from Futu ``get_stock_basicinfo`` with ``stock_type=FUTURE``.

    Returns
    -------
    FuturesContract

    """
    code = basic_info.get("code", "")
    lot_size = basic_info.get("lot_size", 1)

    last_trade_time = basic_info.get("last_trade_time", "")
    expiration_ns = _parse_date_to_ns(last_trade_time)

    market = _market_from_code(code)
    spread = basic_info.get("price_spread")
    precision, increment = _precision_from_spread(spread, market)

    instrument_id = security_to_instrument_id(code)
    currency = _venue_to_currency(instrument_id.venue)

    return FuturesContract(
        instrument_id=instrument_id,
        raw_symbol=Symbol(code),
        asset_class=AssetClass.INDEX,
        currency=currency,
        price_precision=precision,
        price_increment=Price.from_str(increment),
        multiplier=Quantity.from_int(int(lot_size)),
        lot_size=Quantity.from_int(int(lot_size)),
        underlying=code,
        activation_ns=0,
        expiration_ns=expiration_ns,
        ts_event=0,
        ts_init=0,
    )
