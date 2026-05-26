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
