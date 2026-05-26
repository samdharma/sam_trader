"""Shared Futu connection manager for data and execution clients.

Manages OpenQuoteContext + OpenSecTradeContext lifecycle, caching,
reconnection, and trade unlock.  All contexts are keyed by
(host, port, trade_env) so multiple environments can coexist.
"""

from __future__ import annotations

import inspect
import logging
import os
import threading
import time
from typing import Any, Callable

import futu
from futu import (
    RET_OK,
    ContextStatus,
    OpenQuoteContext,
    OpenSecTradeContext,
    SysConfig,
    SysNotifyHandlerBase,
    TrdMarket,
)

# Configure RSA encryption for cross-network trading when the key is present.
# Futu OpenD requires RSA when listening on 0.0.0.0 (the default in Docker).
_RSA_KEY_PATH = "/.futu/futu.pem"
if os.path.isfile(_RSA_KEY_PATH):
    SysConfig.set_init_rsa_file(_RSA_KEY_PATH)

logger = logging.getLogger(__name__)

from futu.common.constant import SecurityFirm, TrdCategory  # noqa: E402

# ---------------------------------------------------------------------------
# Monkey-patch OpenTradeContextBase to support is_async_connect
# ---------------------------------------------------------------------------
# OpenContextBase already supports ``is_async_connect``, but
# OpenTradeContextBase (parent of OpenSecTradeContext) does not pass it
# through.  Without this patch the constructor blocks forever retrying
# when OpenD is unreachable.  The patch is applied only when the
# parameter is missing, making it future-proof.
# ---------------------------------------------------------------------------
from futu.common.open_context_base import OpenContextBase  # noqa: E402
from futu.trade.open_trade_context import OpenTradeContextBase  # noqa: E402

if (
    "is_async_connect"
    not in inspect.signature(OpenTradeContextBase.__init__).parameters
):
    _orig_otcb_init = OpenTradeContextBase.__init__

    def _patched_otcb_init(  # type: ignore[misc]
        self: Any,
        trd_mkt: Any,
        host: str = "127.0.0.1",
        port: int = 11111,
        is_encrypt: Any = None,
        security_firm: Any = SecurityFirm.FUTUSECURITIES,
        trd_category: Any = TrdCategory.NONE,
        need_general_sec_acc: bool = False,
        ai_type: int = 0,
    ) -> None:
        if not SecurityFirm.if_has_key(security_firm):
            raise ValueError(
                "Invalid SecurityFirm value. Use allowed enum value "
                "(e.g., FUTUSECURITIES, FUTUINC, FUTUSG)."
            )
        # Replicate the original private-attribute setup exactly
        self._OpenTradeContextBase__trd_mkt = trd_mkt  # noqa: SLF001
        self._ctx_unlock = None
        self._OpenTradeContextBase__last_acc_list = []  # noqa: SLF001
        self._OpenTradeContextBase__is_acc_sub_push = False  # noqa: SLF001
        self._OpenTradeContextBase__security_firm = security_firm  # noqa: SLF001
        self._OpenTradeContextBase__trd_category = trd_category  # noqa: SLF001
        self._OpenTradeContextBase__need_general_sec_acc = (
            need_general_sec_acc  # noqa: SLF001
        )
        OpenContextBase.__init__(
            self,
            host,
            port,
            is_encrypt=is_encrypt,
            is_async_connect=True,
            ai_type=ai_type,
        )

    OpenTradeContextBase.__init__ = _patched_otcb_init  # type: ignore[method-assign]
    logger.debug("Patched OpenTradeContextBase to support is_async_connect=True")

# ---------------------------------------------------------------------------
# Global caches
# ---------------------------------------------------------------------------
_QUOTE_CACHE: dict[tuple[str, int, str], OpenQuoteContext] = {}
_TRADE_CACHE: dict[tuple[str, int, str, str], OpenSecTradeContext] = {}
_CACHE_LOCK = threading.Lock()

_DEFAULT_CONNECT_TIMEOUT = 10.0


class _FutuDisconnectHandler(SysNotifyHandlerBase):
    """Invalidate cached contexts when Futu notifies us of a disconnect."""

    def __init__(
        self,
        key: tuple[Any, ...],
        *,
        is_trade: bool = False,
        on_disconnect: Callable[[str, float], None] | None = None,
    ) -> None:
        super().__init__()
        self._key = key
        self._is_trade = is_trade
        self._on_disconnect = on_disconnect
        self._connect_time = time.monotonic()

    def on_recv_rsp(self, rsp_pb: Any) -> tuple[int, Any]:
        ret, content = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, content

        notify_type, sub_type, data = content
        should_invalidate = False
        reason: str | None = None

        if notify_type == "CONN_STATUS":
            if isinstance(data, dict):
                if self._is_trade:
                    if not data.get("trd_logined"):
                        should_invalidate = True
                        reason = "CONN_STATUS:trd_logined=False"
                else:
                    if not data.get("qot_logined"):
                        should_invalidate = True
                        reason = "CONN_STATUS:qot_logined=False"
        elif notify_type == "GTW_EVENT":
            if sub_type in ("LoginFailed", "KickedOut", "APISvrRunFailed"):
                should_invalidate = True
                reason = f"GTW_EVENT:{sub_type}"
            elif sub_type == "RemoteClose":
                should_invalidate = True
                reason = "RemoteClose"
            elif isinstance(data, dict) and data.get("reason") == "RemoteClose":
                should_invalidate = True
                reason = "RemoteClose"
        elif notify_type == "PROGRAM_STATUS":
            if sub_type == "FORCE_LOGOUT":
                should_invalidate = True
                reason = "FORCE_LOGOUT"

        if should_invalidate:
            duration = time.monotonic() - self._connect_time
            logger.info(
                "futu_disconnect event=%s reason=%s duration_seconds=%.3f is_trade=%s",
                "disconnect",
                reason,
                duration,
                self._is_trade,
            )
            if self._on_disconnect is not None and reason is not None:
                try:
                    self._on_disconnect(reason, duration)
                except Exception:
                    logger.exception("on_disconnect callback failed")
            _invalidate_context(self._key, is_trade=self._is_trade)

        return ret, content


def _invalidate_context(key: tuple[str, ...], *, is_trade: bool) -> None:
    """Remove a context from its cache and close it to stop auto-reconnect."""
    with _CACHE_LOCK:
        cache = _TRADE_CACHE if is_trade else _QUOTE_CACHE
        ctx = cache.pop(key, None)  # type: ignore[arg-type]
        if ctx is None:
            return

    market_str = key[3] if len(key) > 3 else "N/A"
    try:
        ctx.close()
        logger.info(
            "Closed Futu %s context on disconnect: host=%s port=%s env=%s market=%s",
            "trade" if is_trade else "quote",
            key[0],
            key[1],
            key[2],
            market_str,
        )
    except Exception:
        logger.exception(
            "Error closing Futu %s context: host=%s port=%s env=%s market=%s",
            "trade" if is_trade else "quote",
            key[0],
            key[1],
            key[2],
            market_str,
        )


def _wait_for_ready(
    ctx: OpenQuoteContext | OpenSecTradeContext,
    timeout: float = _DEFAULT_CONNECT_TIMEOUT,
) -> None:
    """Poll context status until it reaches ``READY``.

    Raises:
        TimeoutError: If READY is not reached within *timeout* seconds.
        ConnectionError: If the context enters CLOSED or CLOSING.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = ctx.status
        if status == ContextStatus.READY:
            return
        if status in (ContextStatus.CLOSED, ContextStatus.CLOSING):
            raise ConnectionError(
                f"Futu context closed during connection (status={status})"
            )
        time.sleep(0.05)

    raise TimeoutError(
        f"Futu context did not reach READY within {timeout}s " f"(status={ctx.status})"
    )


def get_cached_futu_quote_context(
    host: str,
    port: int,
    trade_env: str,
    on_disconnect: Callable[[str, float], None] | None = None,
) -> OpenQuoteContext:
    """Get or create a cached ``OpenQuoteContext``.

    One context is maintained per ``(host, port, trade_env)`` tuple.
    If the cached context has disconnected it is closed and recreated.
    """
    key = (host, port, trade_env)
    with _CACHE_LOCK:
        ctx = _QUOTE_CACHE.get(key)
        if ctx is not None and ctx.status == ContextStatus.READY:
            return ctx
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
            _QUOTE_CACHE.pop(key, None)

        ctx = OpenQuoteContext(host=host, port=port, is_async_connect=True)
        _QUOTE_CACHE[key] = ctx

    _wait_for_ready(ctx)
    ret = ctx.set_handler(
        _FutuDisconnectHandler(key, is_trade=False, on_disconnect=on_disconnect)
    )
    if ret != RET_OK:
        logger.warning("Failed to set disconnect handler on quote context %s", key)
    opend_ver = os.environ.get("FUTU_OPEND_VER", "unknown")
    logger.info(
        "Futu quote context ready: host=%s port=%s env=%s sdk=%s opend=%s",
        host,
        port,
        trade_env,
        futu.__version__,
        opend_ver,
    )
    return ctx


def _parse_trd_market(trd_market: str | TrdMarket) -> TrdMarket:
    """Convert a string market code to a ``TrdMarket`` enum value."""
    if isinstance(trd_market, TrdMarket):
        return trd_market
    mapping = {
        "US": TrdMarket.US,
        "HK": TrdMarket.HK,
        "CN": TrdMarket.CN,
        "SG": TrdMarket.SG,
        "JP": TrdMarket.JP,
        "AU": TrdMarket.AU,
        "CA": TrdMarket.CA,
        "MY": TrdMarket.MY,
    }
    market = mapping.get(trd_market.upper())
    if market is None:
        raise ValueError(f"Unsupported Futu trading market: {trd_market}")
    return market


def get_cached_futu_trade_context(
    host: str,
    port: int,
    trade_env: str,
    trd_market: str | TrdMarket = TrdMarket.US,
    on_disconnect: Callable[[str, float], None] | None = None,
) -> OpenSecTradeContext:
    """Get or create a cached ``OpenSecTradeContext``.

    One context is maintained per ``(host, port, trade_env)`` tuple.
    If the cached context has disconnected it is closed and recreated.

    Parameters
    ----------
    host : str
        The Futu OpenD host address.
    port : int
        The Futu OpenD port.
    trade_env : str
        The trading environment (e.g. 'SIMULATE' or 'REAL').
    trd_market : str | TrdMarket, default TrdMarket.US
        The trading market filter (e.g. 'US', 'HK', 'CN').

    """
    market_str = str(trd_market).upper()
    key = (host, port, trade_env, market_str)
    market_enum = _parse_trd_market(trd_market)
    with _CACHE_LOCK:
        ctx = _TRADE_CACHE.get(key)
        if ctx is not None and ctx.status == ContextStatus.READY:
            return ctx
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
            _TRADE_CACHE.pop(key, None)

        ctx = OpenSecTradeContext(
            filter_trdmarket=market_enum,
            host=host,
            port=port,
        )
        _TRADE_CACHE[key] = ctx

    _wait_for_ready(ctx)
    ret = ctx.set_handler(
        _FutuDisconnectHandler(key, is_trade=True, on_disconnect=on_disconnect)
    )
    if ret != RET_OK:
        logger.warning("Failed to set disconnect handler on trade context %s", key)
    opend_ver = os.environ.get("FUTU_OPEND_VER", "unknown")
    logger.info(
        "Futu trade context ready: host=%s port=%s env=%s market=%s sdk=%s opend=%s",
        host,
        port,
        trade_env,
        trd_market,
        futu.__version__,
        opend_ver,
    )
    return ctx


def unlock_futu_trade(context: OpenSecTradeContext, password: str) -> bool:
    """Unlock trade on a trade context.

    Returns ``True`` on success (including when simulate trading reports
    that no unlock is required).
    """
    ret, data = context.unlock_trade(password=password)
    if ret == RET_OK:
        logger.info("Futu trade unlocked successfully")
        return True
    logger.warning("Futu trade unlock failed: %s", data)
    return False


def close_futu_contexts() -> None:
    """Close all cached contexts (for clean shutdown)."""
    with _CACHE_LOCK:
        quote_items = list(_QUOTE_CACHE.items())
        trade_items = list(_TRADE_CACHE.items())
        _QUOTE_CACHE.clear()
        _TRADE_CACHE.clear()

    for key, ctx in quote_items:
        try:
            ctx.close()
            logger.info("Closed Futu quote context: %s", key)
        except Exception:
            logger.exception("Error closing Futu quote context: %s", key)

    for key, ctx in trade_items:  # type: ignore[assignment]
        try:
            ctx.close()
            logger.info("Closed Futu trade context: %s", key)
        except Exception:
            logger.exception("Error closing Futu trade context: %s", key)
