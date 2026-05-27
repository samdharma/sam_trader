"""Integration tests for per-market Futu connection context coexistence.

Verifies that US and HK trade contexts are isolated by cache key
while sharing a single quote context. Tests the full factory ->
connection -> cache path for multi-market support.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from futu import ContextStatus
from nautilus_trader.common.component import LiveClock
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.adapters.futu.config import FutuDataClientConfig, FutuExecClientConfig
from sam_trader.adapters.futu.connection import (
    _QUOTE_CACHE,
    _TRADE_CACHE,
    close_futu_contexts,
    get_cached_futu_quote_context,
    get_cached_futu_trade_context,
)
from sam_trader.adapters.futu.factories import (
    FutuLiveDataClientFactory,
    FutuLiveExecClientFactory,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Ensure module-level caches are empty for every test."""
    _QUOTE_CACHE.clear()
    _TRADE_CACHE.clear()
    yield
    _QUOTE_CACHE.clear()
    _TRADE_CACHE.clear()


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def factory_deps(event_loop):
    """Return common factory dependencies."""
    return {
        "loop": event_loop,
        "name": "FUTU-1",
        "msgbus": TestComponentStubs.msgbus(),
        "cache": TestComponentStubs.cache(),
        "clock": LiveClock(),
    }


# ---------------------------------------------------------------------------
# Helper: create mock contexts with READY status
# ---------------------------------------------------------------------------


def _make_ready_mock():
    m = MagicMock()
    m.status = ContextStatus.READY
    m.close = MagicMock()
    m.set_handler.return_value = 0  # RET_OK = 0
    return m


# ---------------------------------------------------------------------------
# Tests: Connection Layer -- Cache Key Isolation
# ---------------------------------------------------------------------------


class TestConnectionCacheKeyIsolation:
    """Verify connection cache keys correctly isolate per-market trade contexts."""

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_trade_cache_has_separate_keys_for_us_and_hk(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        """Trade cache has two separate entries for US and HK markets."""
        mock_q = _make_ready_mock()
        mock_t_us = _make_ready_mock()
        mock_t_hk = _make_ready_mock()

        mock_quote_cls.return_value = mock_q
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        assert len(_TRADE_CACHE) == 2
        assert ("h1", 11111, "SIMULATE", "US") in _TRADE_CACHE
        assert ("h1", 11111, "SIMULATE", "HK") in _TRADE_CACHE
        assert _TRADE_CACHE[("h1", 11111, "SIMULATE", "US")] is mock_t_us
        assert _TRADE_CACHE[("h1", 11111, "SIMULATE", "HK")] is mock_t_hk

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    def test_quote_cache_single_entry_across_markets(self, mock_cls: MagicMock) -> None:
        """Quote context is NOT market-keyed; (host, port, env) = single entry."""
        mock_q = _make_ready_mock()
        mock_cls.return_value = mock_q

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        get_cached_futu_quote_context("h1", 11111, "SIMULATE")

        assert len(_QUOTE_CACHE) == 1
        assert ("h1", 11111, "SIMULATE") in _QUOTE_CACHE

    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_recreate_only_affects_target_market(
        self, mock_trade_cls: MagicMock
    ) -> None:
        """When US context disconnects, only US key is invalidated; HK is untouched."""
        mock_t_us = _make_ready_mock()
        mock_t_hk = _make_ready_mock()
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")
        assert len(_TRADE_CACHE) == 2

        # Simulate US disconnect
        mock_t_us.status = ContextStatus.CLOSED

        # HK should still be cached
        ctx_hk = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")
        assert ctx_hk is mock_t_hk

        # US is recreated (old was closed, new is created)
        mock_t_us2 = _make_ready_mock()
        mock_trade_cls.side_effect = [mock_t_us2]
        ctx_us = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        assert ctx_us is not mock_t_us
        mock_t_us.close.assert_called_once()

        # HK context still intact
        assert ("h1", 11111, "SIMULATE", "HK") in _TRADE_CACHE
        assert _TRADE_CACHE[("h1", 11111, "SIMULATE", "HK")] is mock_t_hk

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_full_coexistence_all_contexts_cached(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        """Quote + US trade + HK trade all coexist in caches simultaneously."""
        mock_q = _make_ready_mock()
        mock_t_us = _make_ready_mock()
        mock_t_hk = _make_ready_mock()

        mock_quote_cls.return_value = mock_q
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        q = get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        t_us = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        t_hk = get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        assert len(_QUOTE_CACHE) == 1
        assert len(_TRADE_CACHE) == 2
        assert q is not t_us
        assert t_us is not t_hk
        assert _TRADE_CACHE[("h1", 11111, "SIMULATE", "US")] is t_us
        assert _TRADE_CACHE[("h1", 11111, "SIMULATE", "HK")] is t_hk

    @patch("sam_trader.adapters.futu.connection.OpenQuoteContext")
    @patch("sam_trader.adapters.futu.connection.OpenSecTradeContext")
    def test_close_contexts_clears_all_market_entries(
        self, mock_trade_cls: MagicMock, mock_quote_cls: MagicMock
    ) -> None:
        """close_futu_contexts() clears both US and HK trade cache entries."""
        mock_q = _make_ready_mock()
        mock_t_us = _make_ready_mock()
        mock_t_hk = _make_ready_mock()

        mock_quote_cls.return_value = mock_q
        mock_trade_cls.side_effect = [mock_t_us, mock_t_hk]

        get_cached_futu_quote_context("h1", 11111, "SIMULATE")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="US")
        get_cached_futu_trade_context("h1", 11111, "SIMULATE", trd_market="HK")

        close_futu_contexts()

        assert len(_QUOTE_CACHE) == 0
        assert len(_TRADE_CACHE) == 0
        mock_q.close.assert_called_once()
        mock_t_us.close.assert_called_once()
        mock_t_hk.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Factory Layer -- Market-Aware Context Construction
# ---------------------------------------------------------------------------


class TestFactoryMarketIsolation:
    """Verify factories construct per-market contexts correctly."""

    @staticmethod
    def _us_exec_config():
        return FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="US",
            client_id=1,
        )

    @staticmethod
    def _hk_exec_config():
        return FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            client_id=1,
        )

    @staticmethod
    def _data_config(market: str):
        return FutuDataClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market=market,
            client_id=1,
        )

    def test_exec_factory_creates_us_context_with_correct_key(
        self, factory_deps
    ) -> None:
        """US exec factory uses trd_market='US' for trade context caching."""
        mock_quote_ctx = _make_ready_mock()
        mock_trade_ctx = _make_ready_mock()

        with (
            patch(
                "sam_trader.adapters.futu.factories" ".get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories" ".get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ) as mock_get_trade,
        ):
            FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=self._us_exec_config(),
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        mock_get_trade.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
            trd_market="US",
        )

    def test_exec_factory_creates_hk_context_with_correct_key(
        self, factory_deps
    ) -> None:
        """HK exec factory uses trd_market='HK' for trade context caching."""
        mock_quote_ctx = _make_ready_mock()
        mock_trade_ctx = _make_ready_mock()

        with (
            patch(
                "sam_trader.adapters.futu.factories" ".get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories" ".get_cached_futu_trade_context",
                return_value=mock_trade_ctx,
            ) as mock_get_trade,
        ):
            FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=self._hk_exec_config(),
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        mock_get_trade.assert_called_once_with(
            host="test-host",
            port=11111,
            trade_env="SIMULATE",
            trd_market="HK",
        )

    def test_data_factory_same_quote_context_key_for_both_markets(
        self, factory_deps
    ) -> None:
        """Data factory for US and HK uses same key (no market param)."""
        mock_quote_ctx = _make_ready_mock()

        with patch(
            "sam_trader.adapters.futu.factories" ".get_cached_futu_quote_context",
            return_value=mock_quote_ctx,
        ) as mock_get_quote:
            FutuLiveDataClientFactory.create(
                name=factory_deps["name"],
                config=self._data_config("US"),
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )
            FutuLiveDataClientFactory.create(
                name=factory_deps["name"],
                config=self._data_config("HK"),
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        # Both calls use the same key (host, port, trade_env) -- no market
        assert mock_get_quote.call_count == 2
        for call_args in mock_get_quote.call_args_list:
            assert call_args.kwargs == {
                "host": "test-host",
                "port": 11111,
                "trade_env": "SIMULATE",
            }

    def test_two_exec_factories_distinct_trade_contexts(self, factory_deps) -> None:
        """US and HK exec factories use different market params."""
        mock_quote_ctx = _make_ready_mock()
        mock_trade_us = _make_ready_mock()
        mock_trade_hk = _make_ready_mock()

        with (
            patch(
                "sam_trader.adapters.futu.factories" ".get_cached_futu_quote_context",
                return_value=mock_quote_ctx,
            ),
            patch(
                "sam_trader.adapters.futu.factories" ".get_cached_futu_trade_context",
                side_effect=[mock_trade_us, mock_trade_hk],
            ) as mock_get_trade,
        ):
            us_client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=self._us_exec_config(),
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )
            hk_client = FutuLiveExecClientFactory.create(
                name=factory_deps["name"],
                config=self._hk_exec_config(),
                msgbus=factory_deps["msgbus"],
                cache=factory_deps["cache"],
                clock=factory_deps["clock"],
                loop=factory_deps["loop"],
            )

        assert mock_get_trade.call_count == 2
        assert mock_get_trade.call_args_list[0].kwargs["trd_market"] == "US"
        assert mock_get_trade.call_args_list[1].kwargs["trd_market"] == "HK"
        assert us_client._trade_ctx is mock_trade_us
        assert hk_client._trade_ctx is mock_trade_hk
