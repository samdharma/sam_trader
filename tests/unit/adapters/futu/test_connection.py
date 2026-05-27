"""Unit tests for the Futu connection manager."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from futu import RET_ERROR, RET_OK, ContextStatus

from sam_trader.adapters.futu.connection import (
    _QUOTE_CACHE,
    _TRADE_CACHE,
    close_futu_contexts,
    get_cached_futu_quote_context,
    get_cached_futu_trade_context,
    unlock_futu_trade,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Ensure module-level caches are empty for every test."""
    _QUOTE_CACHE.clear()
    _TRADE_CACHE.clear()
    yield
    _QUOTE_CACHE.clear()
    _TRADE_CACHE.clear()


class TestGetCachedQuoteContext:
    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_creates_and_caches(self, mock_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        ctx1 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        ctx2 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")

        assert ctx1 is ctx2 is mock_ctx
        mock_cls.assert_called_once_with(host="h1", port=11111, is_async_connect=True)
        mock_ctx.set_handler.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_recreate_when_not_ready(self, mock_cls: MagicMock) -> None:
        mock_old = MagicMock()
        mock_old.status = ContextStatus.READY
        mock_new = MagicMock()
        mock_new.status = ContextStatus.READY
        mock_cls.side_effect = [mock_old, mock_new]

        ctx1 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        assert ctx1 is mock_old

        # Simulate a later disconnect
        mock_old.status = ContextStatus.CLOSED

        ctx2 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        assert ctx2 is mock_new
        mock_old.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_connection_timeout(self, mock_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.WAIT_RECONNECT
        mock_cls.return_value = mock_ctx

        with patch(
            "sam_trader.adapters.futu.connection._DEFAULT_CONNECT_TIMEOUT", 0.05
        ):
            with pytest.raises(TimeoutError, match="did not reach READY"):
                get_cached_futu_quote_context("h1", 11111, "SIMULATE")

        mock_ctx.close.assert_not_called()  # we leave it for caller / GC

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_connection_closed_during_wait(self, mock_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.CLOSED
        mock_cls.return_value = mock_ctx

        with pytest.raises(ConnectionError, match="closed during connection"):
            get_cached_futu_quote_context("h1", 11111, "SIMULATE")

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_set_handler_failure_logged(
        self, mock_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_ctx.set_handler.return_value = RET_ERROR
        mock_cls.return_value = mock_ctx

        with caplog.at_level(logging.WARNING):
            get_cached_futu_quote_context("h1", 11111, "SIMULATE")

        assert "Failed to set disconnect handler" in caplog.text


class TestGetCachedTradeContext:
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_creates_and_caches(self, mock_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        ctx1 = get_cached_futu_trade_context("h1", 11111, "SIMULATE")
        ctx2 = get_cached_futu_trade_context("h1", 11111, "SIMULATE")

        assert ctx1 is ctx2 is mock_ctx
        _, kwargs = mock_cls.call_args
        assert kwargs["host"] == "h1"
        assert kwargs["port"] == 11111
        mock_ctx.set_handler.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_recreate_when_not_ready(self, mock_cls: MagicMock) -> None:
        mock_old = MagicMock()
        mock_old.status = ContextStatus.READY
        mock_new = MagicMock()
        mock_new.status = ContextStatus.READY
        mock_cls.side_effect = [mock_old, mock_new]

        ctx1 = get_cached_futu_trade_context("h1", 11111, "SIMULATE")
        assert ctx1 is mock_old

        mock_old.status = ContextStatus.CLOSED

        ctx2 = get_cached_futu_trade_context("h1", 11111, "SIMULATE")
        assert ctx2 is mock_new
        mock_old.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_connection_timeout(self, mock_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.WAIT_RECONNECT
        mock_cls.return_value = mock_ctx

        with patch(
            "sam_trader.adapters.futu.connection._DEFAULT_CONNECT_TIMEOUT", 0.05
        ):
            with pytest.raises(TimeoutError, match="did not reach READY"):
                get_cached_futu_trade_context("h1", 11111, "SIMULATE")

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_connection_closed_during_wait(self, mock_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.CLOSING
        mock_cls.return_value = mock_ctx

        with pytest.raises(ConnectionError, match="closed during connection"):
            get_cached_futu_trade_context("h1", 11111, "SIMULATE")


class TestUnlockFutuTrade:
    def test_unlock_success(self) -> None:
        mock_ctx = MagicMock()
        mock_ctx.unlock_trade.return_value = (RET_OK, None)

        assert unlock_futu_trade(mock_ctx, "secret") is True
        mock_ctx.unlock_trade.assert_called_once_with(password="secret")

    def test_unlock_simulate_no_need(self) -> None:
        mock_ctx = MagicMock()
        mock_ctx.unlock_trade.return_value = (RET_OK, "NoNeedUnlock")

        assert unlock_futu_trade(mock_ctx, "secret") is True

    def test_unlock_failure(self) -> None:
        mock_ctx = MagicMock()
        mock_ctx.unlock_trade.return_value = (RET_ERROR, "Wrong password")

        assert unlock_futu_trade(mock_ctx, "bad") is False


class TestDisconnectHandler:
    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("CONN_STATUS", None, {"qot_logined": False})),
    )
    def test_invalidation_on_qot_disconnect(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        assert ("h1", 11111, "SIMULATE") in _QUOTE_CACHE

        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        assert ("h1", 11111, "SIMULATE") not in _QUOTE_CACHE
        mock_ctx.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("CONN_STATUS", None, {"trd_logined": False})),
    )
    def test_invalidation_on_trd_disconnect(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        get_cached_futu_trade_context("h1", 11111, "SIMULATE")
        assert ("h1", 11111, "SIMULATE", "US") in _TRADE_CACHE

        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        assert ("h1", 11111, "SIMULATE", "US") not in _TRADE_CACHE
        mock_ctx.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("GTW_EVENT", "LoginFailed", "desc")),
    )
    def test_invalidation_on_gtw_event(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        assert ("h1", 11111, "SIMULATE") not in _QUOTE_CACHE
        mock_ctx.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("CONN_STATUS", None, {"qot_logined": True})),
    )
    def test_no_invalidation_when_still_connected(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        assert ("h1", 11111, "SIMULATE") in _QUOTE_CACHE
        mock_ctx.close.assert_not_called()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("GTW_EVENT", "RemoteClose", None)),
    )
    def test_invalidation_on_remote_close(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        assert ("h1", 11111, "SIMULATE") not in _QUOTE_CACHE
        mock_ctx.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("GTW_EVENT", "Close", {"reason": "RemoteClose"})),
    )
    def test_invalidation_on_remote_close_in_data(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        assert ("h1", 11111, "SIMULATE") not in _QUOTE_CACHE
        mock_ctx.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("GTW_EVENT", "RemoteClose", None)),
    )
    def test_remote_close_invokes_callback(
        self, _mock_unpack: MagicMock, mock_cls: MagicMock
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        callback = MagicMock()
        get_cached_futu_quote_context("h1", 11111, "SIMULATE", on_disconnect=callback)
        handler = mock_ctx.set_handler.call_args[0][0]
        handler.on_recv_rsp(MagicMock())

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == "RemoteClose"
        assert isinstance(args[1], float)
        assert args[1] >= 0.0

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch(
        "futu.quote.quote_response_handler.SysNotifyPush.unpack_rsp",
        return_value=(RET_OK, ("GTW_EVENT", "RemoteClose", None)),
    )
    def test_remote_close_logs_structured(
        self,
        _mock_unpack: MagicMock,
        mock_cls: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_ctx = MagicMock()
        mock_ctx.status = ContextStatus.READY
        mock_cls.return_value = mock_ctx

        with caplog.at_level(logging.INFO):
            get_cached_futu_quote_context("h1", 11111, "SIMULATE")
            handler = mock_ctx.set_handler.call_args[0][0]
            handler.on_recv_rsp(MagicMock())

        assert "futu_disconnect" in caplog.text
        assert "reason=RemoteClose" in caplog.text


class TestCloseFutuContexts:
    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_closes_all_and_clears_caches(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        mock_q1 = MagicMock()
        mock_q1.status = ContextStatus.READY
        mock_t1 = MagicMock()
        mock_t1.status = ContextStatus.READY
        mock_quote_cls.return_value = mock_q1
        mock_trade_cls.return_value = mock_t1

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE")

        assert len(_QUOTE_CACHE) == 1
        assert len(_TRADE_CACHE) == 1

        close_futu_contexts()

        assert len(_QUOTE_CACHE) == 0
        assert len(_TRADE_CACHE) == 0
        mock_q1.close.assert_called_once()
        mock_t1.close.assert_called_once()


class TestTradeContextEncryption:
    """Verify monkey-patched OpenTradeContextBase passes is_encrypt correctly."""

    def test_is_encrypt_true_when_rsa_key_exists(self) -> None:
        """When RSA key file exists and is_encrypt not explicitly set,
        is_encrypt=True should be passed to OpenContextBase.__init__."""
        from sam_trader.adapters.futu.connection import (
            _RSA_KEY_PATH,
            OpenContextBase,
            OpenTradeContextBase,
        )

        with patch.object(OpenContextBase, "__init__", return_value=None) as mock_init:
            with patch("os.path.isfile", return_value=True) as mock_isfile:
                instance = MagicMock()
                OpenTradeContextBase.__init__(
                    instance,
                    trd_mkt="US",
                    host="127.0.0.1",
                    port=11111,
                )

        mock_isfile.assert_called_once_with(_RSA_KEY_PATH)
        # is_encrypt=True should be passed to OpenContextBase
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["is_encrypt"] is True
        assert call_kwargs["is_async_connect"] is True

    def test_is_encrypt_not_passed_when_no_rsa_key(self) -> None:
        """When RSA key file does NOT exist and is_encrypt not explicitly set,
        is_encrypt should NOT be passed to OpenContextBase.__init__."""
        from sam_trader.adapters.futu.connection import (
            OpenContextBase,
            OpenTradeContextBase,
        )

        with patch.object(OpenContextBase, "__init__", return_value=None) as mock_init:
            with patch("os.path.isfile", return_value=False):
                instance = MagicMock()
                OpenTradeContextBase.__init__(
                    instance,
                    trd_mkt="US",
                    host="127.0.0.1",
                    port=11111,
                )

        # is_encrypt should NOT be in the kwargs
        call_kwargs = mock_init.call_args.kwargs
        assert "is_encrypt" not in call_kwargs
        assert call_kwargs["is_async_connect"] is True

    def test_explicit_is_encrypt_false_respected(self) -> None:
        """When caller explicitly passes is_encrypt=False,
        it should be respected even if RSA key exists."""
        from sam_trader.adapters.futu.connection import (
            OpenContextBase,
            OpenTradeContextBase,
        )

        with patch.object(OpenContextBase, "__init__", return_value=None) as mock_init:
            with patch("os.path.isfile", return_value=True):
                instance = MagicMock()
                OpenTradeContextBase.__init__(
                    instance,
                    trd_mkt="US",
                    host="127.0.0.1",
                    port=11111,
                    is_encrypt=False,
                )

        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["is_encrypt"] is False

    def test_explicit_is_encrypt_true_passed_through(self) -> None:
        """When caller passes is_encrypt=True, it should be passed through."""
        from sam_trader.adapters.futu.connection import (
            OpenContextBase,
            OpenTradeContextBase,
        )

        with patch.object(OpenContextBase, "__init__", return_value=None) as mock_init:
            with patch("os.path.isfile", return_value=False):
                instance = MagicMock()
                OpenTradeContextBase.__init__(
                    instance,
                    trd_mkt="US",
                    host="0.0.0.0",
                    port=11111,
                    is_encrypt=True,
                )

        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["is_encrypt"] is True


class TestMultipleEnvironments:
    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_simulate_and_real_are_independent(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        mock_q_sim = MagicMock()
        mock_q_sim.status = ContextStatus.READY
        mock_q_real = MagicMock()
        mock_q_real.status = ContextStatus.READY
        mock_t_sim = MagicMock()
        mock_t_sim.status = ContextStatus.READY
        mock_t_real = MagicMock()
        mock_t_real.status = ContextStatus.READY

        mock_quote_cls.side_effect = [mock_q_sim, mock_q_real]
        mock_trade_cls.side_effect = [mock_t_sim, mock_t_real]

        q_sim = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        q_real = get_cached_futu_quote_context("h1", 11111, "REAL")
        t_sim = get_cached_futu_trade_context("h1", 11111, "SIMULATE")
        t_real = get_cached_futu_trade_context("h1", 11111, "REAL")

        assert q_sim is not q_real
        assert t_sim is not t_real
        assert q_sim is mock_q_sim
        assert q_real is mock_q_real
        assert t_sim is mock_t_sim
        assert t_real is mock_t_real


class TestPerMarketCoexistence:
    """Verify US and HK trade contexts are isolated by cache key.

    Quote contexts are shared (keyed by host/port/env only).
    Trade contexts are market-isolated (keyed by host/port/env/market).
    """

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_us_and_hk_trade_contexts_distinct(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        """US and HK trade contexts are different objects with different cache keys."""
        mock_t_us = MagicMock()
        mock_t_us.status = ContextStatus.READY
        mock_t_hk = MagicMock()
        mock_t_hk.status = ContextStatus.READY
        mock_q = MagicMock()
        mock_q.status = ContextStatus.READY

        mock_quote_cls.return_value = mock_q
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        t_us = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        t_hk = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        assert t_us is not t_hk
        assert t_us is mock_t_us
        assert t_hk is mock_t_hk
        # Verify distinct cache keys
        assert ("h1", 11111, "SIMULATE", "US") in _TRADE_CACHE
        assert ("h1", 11111, "SIMULATE", "HK") in _TRADE_CACHE
        assert t_us is _TRADE_CACHE[("h1", 11111, "SIMULATE", "US")]
        assert t_hk is _TRADE_CACHE[("h1", 11111, "SIMULATE", "HK")]

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_same_market_trade_context_cached(self, mock_trade_cls: MagicMock) -> None:
        """Same market returns the cached context (not a new one)."""
        mock_t = MagicMock()
        mock_t.status = ContextStatus.READY
        mock_trade_cls.return_value = mock_t

        t1 = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")
        t2 = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        assert t1 is t2
        # Only one constructor call (second was cache hit)
        assert mock_trade_cls.call_count == 1

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_quote_context_shared_across_markets(self, mock_cls: MagicMock) -> None:
        """Quote context is NOT market-keyed — shared across US and HK.

        The cache key is (host, port, trade_env). Different markets share
        the same quote context because OpenD serves all markets from one
        connection.
        """
        mock_q = MagicMock()
        mock_q.status = ContextStatus.READY
        mock_cls.return_value = mock_q

        q1 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        q2 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")

        assert q1 is q2
        assert mock_cls.call_count == 1
        # Quote cache key does NOT include market
        assert ("h1", 11111, "SIMULATE") in _QUOTE_CACHE
        assert len(_QUOTE_CACHE) == 1

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_us_invalidation_does_not_affect_hk(
        self, mock_trade_cls: MagicMock
    ) -> None:
        """Invalidating US trade context leaves HK context intact."""
        mock_t_us = MagicMock()
        mock_t_us.status = ContextStatus.READY
        mock_t_hk = MagicMock()
        mock_t_hk.status = ContextStatus.READY
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        # Simulate US context disconnecting
        mock_t_us.status = ContextStatus.CLOSED

        # HK context should still be cached and ready
        ctx_hk = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")
        assert ctx_hk is mock_t_hk
        assert ("h1", 11111, "SIMULATE", "HK") in _TRADE_CACHE

        # US context should be recreated (CLOSED → new)
        mock_t_us2 = MagicMock()
        mock_t_us2.status = ContextStatus.READY
        mock_trade_cls.side_effect = [mock_t_us2]  # next call for US recreate

        ctx_us = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        assert ctx_us is not mock_t_us  # old was invalidated
        assert ctx_us is mock_t_us2
        mock_t_us.close.assert_called_once()

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_market_string_normalization(self, mock_trade_cls: MagicMock) -> None:
        """Market string is normalized to uppercase for cache key.

        'us' and 'US' should resolve to the same cache entry.
        """
        mock_t = MagicMock()
        mock_t.status = ContextStatus.READY
        mock_trade_cls.return_value = mock_t

        t1 = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="us")
        t2 = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")

        assert t1 is t2
        assert mock_trade_cls.call_count == 1
        assert ("h1", 11111, "SIMULATE", "US") in _TRADE_CACHE

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_quote_unchanged_when_switching_market_trade_contexts(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        """Switching between US and HK trade contexts does not affect
        the shared quote context."""
        mock_q = MagicMock()
        mock_q.status = ContextStatus.READY
        mock_quote_cls.return_value = mock_q

        mock_t_us = MagicMock()
        mock_t_us.status = ContextStatus.READY
        mock_t_hk = MagicMock()
        mock_t_hk.status = ContextStatus.READY
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        q = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        # Quote context should still be the same object
        q2 = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        assert q is q2
        # Quote cache has one entry, trade cache has two
        assert len(_QUOTE_CACHE) == 1
        assert len(_TRADE_CACHE) == 2
