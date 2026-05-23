"""Quote fetcher — Redis cache (fast path) or broker query (fallback).

Usage (inside sam-services container):
    from sam_trader.services.quote import get_quote, format_quote
    result = get_quote("TSLA.NASDAQ")
    print(format_quote(result))

Supports both Nautilus symbology (``TSLA.NASDAQ``) and Futu symbology
(``US.TSLA``).  The fast path reads from Redis; if the cache misses we
fall back to a Futu OpenD market snapshot.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

# Optional imports — fail gracefully if packages are missing
_redis: Any = None
try:
    import redis as _redis_mod  # type: ignore[import-untyped]

    _redis = _redis_mod
except ImportError:  # pragma: no cover
    pass

_futu_ctx: Any = None
_futu_ret_ok: Any = None
try:
    from futu import RET_OK as _RET_OK  # type: ignore[import-untyped]
    from futu import OpenQuoteContext as _OpenQuoteContext

    _futu_ctx = _OpenQuoteContext
    _futu_ret_ok = _RET_OK
except ImportError:  # pragma: no cover
    pass


# ── Redis helpers ──────────────────────────────────────────────────────────


def _redis_client() -> Any:
    """Build a Redis client from environment variables."""
    if _redis is None:
        return None
    host = os.getenv("REDIS_HOST", "sam-redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    return _redis.Redis(
        host=host,
        port=port,
        password=password,
        socket_connect_timeout=5,
        decode_responses=True,
    )


def _try_cache(symbol: str) -> dict[str, Any] | None:
    """Attempt to read a cached quote from Redis.

    Tries the explicit ``sam:quote:{symbol}`` key first, then a set of
    alternative keys for both Nautilus and Futu symbology.
    """
    r = _redis_client()
    if r is None:
        return None

    try:
        r.ping()
    except Exception:
        return None

    keys_to_try = [f"sam:quote:{symbol}"]
    # Also try the swapped symbology
    if symbol.count(".") == 1:
        sym, venue = symbol.split(".", 1)
        if venue.upper() in ("NASDAQ", "NYSE", "HKEX", "SSE", "SZSE"):
            # Nautilus format → Futu format guess
            market = "US" if venue.upper() in ("NASDAQ", "NYSE") else venue.upper()[:2]
            keys_to_try.append(f"sam:quote:{market}.{sym}")
        elif sym.upper() in ("US", "HK", "SH", "SZ"):
            # Futu format → Nautilus format guess (default venue)
            venue_guess = "NASDAQ" if sym.upper() == "US" else sym.upper() + "EX"
            keys_to_try.append(f"sam:quote:{venue}.{venue_guess}")

    for key in keys_to_try:
        raw = r.get(key)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and ("bid" in data or "last" in data):
            data["source"] = "redis_cache"
            return data

    return None


# ── Broker helpers ─────────────────────────────────────────────────────────


def _to_futu_code(symbol: str) -> str | None:
    """Convert a symbol to Futu ``MARKET.CODE`` format.

    Accepts Nautilus ``SYMBOL.VENUE`` (e.g. ``TSLA.NASDAQ``) and Futu
    ``MARKET.SYMBOL`` (e.g. ``US.TSLA``).
    """
    if symbol.count(".") != 1:
        return None

    left, right = symbol.split(".", 1)
    left_up = left.upper()
    right_up = right.upper()

    # Already Futu format
    if left_up in ("US", "HK", "SH", "SZ"):
        return symbol

    # Nautilus format → Futu format
    if right_up in ("NASDAQ", "NYSE"):
        return f"US.{left_up}"
    if right_up == "HKEX":
        return f"HK.{left_up}"
    if right_up == "SSE":
        return f"SH.{left_up}"
    if right_up == "SZSE":
        return f"SZ.{left_up}"

    # Try the helper for unknown venues
    try:
        from nautilus_trader.model.identifiers import InstrumentId

        from sam_trader.adapters.futu.common import instrument_id_to_futu_security

        return instrument_id_to_futu_security(InstrumentId.from_str(symbol))
    except Exception:
        return None


def _try_futu_broker(symbol: str) -> dict[str, Any] | None:
    """Query Futu OpenD for a market snapshot.

    Returns *None* when the Futu SDK is unavailable, the symbol cannot be
    mapped, or the OpenD host is unreachable.
    """
    if _futu_ctx is None or _futu_ret_ok is None:
        return None

    futu_code = _to_futu_code(symbol)
    if futu_code is None:
        return None

    host = os.getenv("FUTU_OPEND_HOST", "sam-futu-opend")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))

    try:
        ctx = _futu_ctx(host=host, port=port)
        ret, data = ctx.get_market_snapshot([futu_code])
        ctx.close()
    except Exception:
        return None

    if ret != _futu_ret_ok or data is None or getattr(data, "empty", True):
        return None

    row = data.iloc[0]

    def _float_or_none(val: Any) -> float | None:
        if val is None:
            return None
        try:
            # pandas NaN check
            import math

            if isinstance(val, float) and math.isnan(val):
                return None
            return float(val)
        except (TypeError, ValueError):
            return None

    return {
        "symbol": symbol,
        "bid": _float_or_none(row.get("bid_price")),
        "ask": _float_or_none(row.get("ask_price")),
        "last": _float_or_none(row.get("last_price")),
        "source": "futu_broker",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ── Public API ─────────────────────────────────────────────────────────────


def get_quote(symbol: str) -> dict[str, Any]:
    """Fetch the latest quote for *symbol*.

    Resolution order:

    1. Redis cache (``sam:quote:{symbol}`` and alternative symbology keys).
    2. Futu OpenD market snapshot (fallback).
    3. Graceful error dict when everything fails.

    Parameters
    ----------
    symbol : str
        Instrument identifier.  Nautilus format ``TSLA.NASDAQ`` or Futu
        format ``US.TSLA`` are both accepted.

    Returns
    -------
    dict
        Always contains at least ``symbol``.  On success also contains
        ``bid``, ``ask``, ``last``, ``source``, and ``timestamp``.  On
        failure contains ``error`` and ``bid``/``ask``/``last`` set to
        ``None``.
    """
    cached = _try_cache(symbol)
    if cached is not None:
        cached["symbol"] = symbol
        cached.setdefault("bid", None)
        cached.setdefault("ask", None)
        cached.setdefault("last", None)
        return cached

    broker = _try_futu_broker(symbol)
    if broker is not None:
        return broker

    return {
        "symbol": symbol,
        "bid": None,
        "ask": None,
        "last": None,
        "source": None,
        "error": "Quote unavailable — cache miss and broker unreachable",
    }


def format_quote(result: dict[str, Any]) -> str:
    """Render a quote result as a human-readable block.

    Parameters
    ----------
    result : dict
        The dict returned by :func:`get_quote`.

    Returns
    -------
    str
        Formatted text suitable for terminal output.
    """
    symbol = result.get("symbol", "UNKNOWN")
    error = result.get("error")

    if error:
        return (
            f"┌─────────────────────────────────────┐\n"
            f"│  {symbol:<33} │\n"
            f"│  Error: {error:<25} │\n"
            f"└─────────────────────────────────────┘"
        )

    bid = result.get("bid")
    ask = result.get("ask")
    last = result.get("last")
    source = result.get("source", "unknown")
    ts = result.get("timestamp", "")

    lines = [
        "┌─────────────────────────────────────┐",
        f"│  {symbol:<33} │",
        f"│  Bid:   {(_fmt(bid)):>25} │",
        f"│  Ask:   {(_fmt(ask)):>25} │",
        f"│  Last:  {(_fmt(last)):>25} │",
    ]
    if ts:
        lines.append(f"│  Time:  {ts[:25]:>25} │")
    lines.append(f"│  Source: {source:<26} │")
    lines.append("└─────────────────────────────────────┘")
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "N/A"
