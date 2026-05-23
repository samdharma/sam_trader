"""Unit tests for the quote fetcher module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from sam_trader.services.quote import (
    _try_cache,
    format_quote,
    get_quote,
)


class TestQuoteFromCache:
    @patch("sam_trader.services.quote._redis_client")
    def test_quote_from_cache(self, mock_redis_client: Any) -> None:
        """Cache hit returns bid/ask/last without touching the broker."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_r.get.return_value = '{"bid": 150.0, "ask": 150.5, "last": 150.25}'
        mock_redis_client.return_value = mock_r

        result = get_quote("AAPL.NASDAQ")

        assert result["symbol"] == "AAPL.NASDAQ"
        assert result["bid"] == 150.0
        assert result["ask"] == 150.5
        assert result["last"] == 150.25
        assert result["source"] == "redis_cache"
        assert "error" not in result

    @patch("sam_trader.services.quote._redis_client")
    def test_quote_cache_miss(self, mock_redis_client: Any) -> None:
        """Cache miss returns None from _try_cache."""
        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_r.get.return_value = None
        mock_redis_client.return_value = mock_r

        assert _try_cache("AAPL.NASDAQ") is None


class TestQuoteFallbackToBroker:
    @patch("sam_trader.services.quote._try_cache")
    @patch("sam_trader.services.quote._futu_ctx")
    @patch("sam_trader.services.quote._futu_ret_ok")
    def test_quote_fallback_to_broker(
        self,
        mock_ret_ok: Any,
        mock_ctx_cls: Any,
        mock_cache: Any,
    ) -> None:
        """Redis miss falls back to Futu broker query."""
        mock_cache.return_value = None
        mock_ret_ok.__ne__ = lambda self, other: other != 0  # RET_OK == 0

        mock_row = MagicMock()
        mock_row.get.side_effect = lambda key, default=None: {
            "bid_price": 250.0,
            "ask_price": 250.5,
            "last_price": 250.25,
        }.get(key, default)

        mock_iloc = MagicMock()
        mock_iloc.__getitem__ = lambda _self, _idx: mock_row

        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.iloc = mock_iloc

        mock_ctx = MagicMock()
        mock_ctx.get_market_snapshot.return_value = (0, mock_df)
        mock_ctx_cls.return_value = mock_ctx

        result = get_quote("TSLA.NASDAQ")

        assert result["symbol"] == "TSLA.NASDAQ"
        assert result["bid"] == 250.0
        assert result["ask"] == 250.5
        assert result["last"] == 250.25
        assert result["source"] == "futu_broker"
        assert "error" not in result
        mock_ctx.close.assert_called_once()

    @patch("sam_trader.services.quote._try_cache")
    @patch("sam_trader.services.quote._try_futu_broker")
    def test_quote_unavailable_when_both_fail(
        self, mock_broker: Any, mock_cache: Any
    ) -> None:
        """Graceful error when cache misses and broker is unreachable."""
        mock_cache.return_value = None
        mock_broker.return_value = None

        result = get_quote("UNKNOWN.XYZ")

        assert result["symbol"] == "UNKNOWN.XYZ"
        assert result["bid"] is None
        assert result["ask"] is None
        assert result["last"] is None
        assert result["source"] is None
        assert "error" in result
        assert "cache miss" in result["error"].lower()


class TestFormatQuote:
    def test_format_quote_success(self) -> None:
        result = {
            "symbol": "TSLA.NASDAQ",
            "bid": 250.0,
            "ask": 250.5,
            "last": 250.25,
            "source": "redis_cache",
            "timestamp": "2026-05-23T12:00:00+00:00",
        }
        text = format_quote(result)
        assert "TSLA.NASDAQ" in text
        assert "250.00" in text
        assert "250.50" in text
        assert "redis_cache" in text

    def test_format_quote_error(self) -> None:
        result = {
            "symbol": "BAD.SYMBOL",
            "bid": None,
            "ask": None,
            "last": None,
            "source": None,
            "error": "Quote unavailable",
        }
        text = format_quote(result)
        assert "BAD.SYMBOL" in text
        assert "Error:" in text
        assert "Quote unavailable" in text


class TestSymbology:
    def test_futu_format_passthrough(self) -> None:
        from sam_trader.services.quote import _to_futu_code

        assert _to_futu_code("US.TSLA") == "US.TSLA"
        assert _to_futu_code("HK.00700") == "HK.00700"

    def test_nautilus_to_futu_conversion(self) -> None:
        from sam_trader.services.quote import _to_futu_code

        assert _to_futu_code("TSLA.NASDAQ") == "US.TSLA"
        assert _to_futu_code("AAPL.NYSE") == "US.AAPL"
        assert _to_futu_code("00700.HKEX") == "HK.00700"
