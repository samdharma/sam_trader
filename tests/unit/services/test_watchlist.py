"""Unit tests for the pre-market watchlist service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.watchlist import (
    MarketWatchlist,
    WatchlistError,
    build_watchlist,
    filter_premarket,
    load_watchlist_config,
    validate_symbols,
)


class TestLoadWatchlistConfig:
    def test_load_us(self, tmp_path: Any) -> None:
        """Load watchlist with US market configuration."""
        path = tmp_path / "watchlist.yaml"
        path.write_text(
            """
watchlist:
  US:
    symbols: [TSLA.NASDAQ, AAPL.NASDAQ]
    min_gap_pct: 2.5
    max_candidates: 20
    premarket_only: true
""",
            encoding="utf-8",
        )

        cfg = load_watchlist_config(str(path))

        assert "US" in cfg
        assert cfg["US"].symbols == ["TSLA.NASDAQ", "AAPL.NASDAQ"]
        assert cfg["US"].min_gap_pct == 2.5
        assert cfg["US"].max_candidates == 20
        assert cfg["US"].premarket_only is True

    def test_load_hk(self, tmp_path: Any) -> None:
        """Load watchlist with HK market configuration."""
        path = tmp_path / "watchlist.yaml"
        path.write_text(
            """
watchlist:
  HK:
    symbols: [00700.HKEX, 00005.HKEX]
    min_gap_pct: 1.0
    max_candidates: 10
    premarket_only: false
""",
            encoding="utf-8",
        )

        cfg = load_watchlist_config(str(path))

        assert "HK" in cfg
        assert cfg["HK"].symbols == ["00700.HKEX", "00005.HKEX"]
        assert cfg["HK"].min_gap_pct == 1.0
        assert cfg["HK"].max_candidates == 10
        assert cfg["HK"].premarket_only is False

    def test_file_not_found(self, tmp_path: Any) -> None:
        """Raise WatchlistError when file does not exist."""
        with pytest.raises(WatchlistError, match="not found"):
            load_watchlist_config(str(tmp_path / "missing.yaml"))

    def test_invalid_yaml(self, tmp_path: Any) -> None:
        """Raise WatchlistError on malformed YAML."""
        path = tmp_path / "bad.yaml"
        path.write_text("{not valid", encoding="utf-8")
        with pytest.raises(WatchlistError, match="Failed to parse"):
            load_watchlist_config(str(path))


class TestBuildWatchlist:
    def test_static_override(self, tmp_path: Any) -> None:
        """Static symbols override dynamic bundle extraction."""
        config = {
            "US": MarketWatchlist(
                symbols=["META.NASDAQ", "NVDA.NASDAQ"],
                min_gap_pct=2.0,
                max_candidates=50,
            ),
        }

        result = build_watchlist(config, bundles_path=str(tmp_path / "no_bundles.yaml"))

        assert result["US"] == ["META.NASDAQ", "NVDA.NASDAQ"]

    @patch("sam_trader.services.watchlist._extract_symbols_from_bundles")
    def test_dynamic_from_bundles(
        self,
        mock_extract: Any,
        tmp_path: Any,
    ) -> None:
        """Dynamic mode extracts symbols from active bundles."""
        mock_extract.return_value = {
            "US": ["TSLA.NASDAQ", "AAPL.NASDAQ"],
            "HK": [],
        }

        config = {
            "US": MarketWatchlist(
                symbols=[],
                min_gap_pct=2.0,
                max_candidates=50,
            ),
        }

        result = build_watchlist(config)

        assert result["US"] == ["TSLA.NASDAQ", "AAPL.NASDAQ"]
        mock_extract.assert_called_once()

    def test_premarket_filter_applied(self, tmp_path: Any) -> None:
        """Pre-market filter excludes non-eligible symbols."""
        config = {
            "US": MarketWatchlist(
                symbols=["TSLA.NASDAQ", "00700.HKEX"],
                min_gap_pct=2.0,
                max_candidates=50,
                premarket_only=True,
            ),
        }

        result = build_watchlist(config)

        # HK symbol dropped because it does not trade pre-market
        assert result["US"] == ["TSLA.NASDAQ"]

    def test_max_candidates_cap(self, tmp_path: Any) -> None:
        """Result is capped to max_candidates."""
        config = {
            "US": MarketWatchlist(
                symbols=["A.NASDAQ", "B.NASDAQ", "C.NASDAQ"],
                min_gap_pct=2.0,
                max_candidates=2,
            ),
        }

        result = build_watchlist(config)

        assert len(result["US"]) == 2
        assert result["US"] == ["A.NASDAQ", "B.NASDAQ"]

    def test_empty_config_returns_empty(self, tmp_path: Any) -> None:
        """Empty configuration yields empty result."""
        result = build_watchlist({})
        assert result == {}


class TestExtractSymbolsFromBundles:
    @patch("sam_trader.services.watchlist.load_bundles")
    def test_extract_us_and_hk(self, mock_load: Any) -> None:
        """Extract symbols from bundles grouped by market."""
        from nautilus_trader.trading.config import ImportableStrategyConfig

        bundle1 = MagicMock(spec=ImportableStrategyConfig)
        bundle1.config = {"instrument_id": "TSLA.NASDAQ"}
        bundle2 = MagicMock(spec=ImportableStrategyConfig)
        bundle2.config = {"instrument_id": "00700.HKEX"}
        bundle3 = MagicMock(spec=ImportableStrategyConfig)
        bundle3.config = {"instrument_id": "AAPL.NASDAQ"}
        mock_load.return_value = [bundle1, bundle2, bundle3]

        from sam_trader.services.watchlist import _extract_symbols_from_bundles

        result = _extract_symbols_from_bundles("config/bundles.yaml")

        assert sorted(result["US"]) == ["AAPL.NASDAQ", "TSLA.NASDAQ"]
        assert result["HK"] == ["00700.HKEX"]

    @patch("sam_trader.services.watchlist.load_bundles")
    def test_deduplicates_symbols(self, mock_load: Any) -> None:
        """Duplicate instrument IDs across bundles are deduplicated."""
        from nautilus_trader.trading.config import ImportableStrategyConfig

        bundle1 = MagicMock(spec=ImportableStrategyConfig)
        bundle1.config = {"instrument_id": "TSLA.NASDAQ"}
        bundle2 = MagicMock(spec=ImportableStrategyConfig)
        bundle2.config = {"instrument_id": "TSLA.NASDAQ"}
        mock_load.return_value = [bundle1, bundle2]

        from sam_trader.services.watchlist import _extract_symbols_from_bundles

        result = _extract_symbols_from_bundles("config/bundles.yaml")

        assert result["US"] == ["TSLA.NASDAQ"]


class TestFilterPremarket:
    def test_keeps_us_exchange_symbols(self) -> None:
        """US exchange-listed symbols are pre-market eligible."""
        symbols = ["TSLA.NASDAQ", "AAPL.NYSE", "SPY.AMEX", "QQQ.ARCA"]
        assert filter_premarket(symbols) == symbols

    def test_drops_hk_symbols(self) -> None:
        """HK symbols are excluded (no pre-market session)."""
        symbols = ["00700.HKEX", "00005.HKEX"]
        assert filter_premarket(symbols) == []

    def test_mixed_market_filter(self) -> None:
        """Mixed list filters correctly."""
        symbols = ["TSLA.NASDAQ", "00700.HKEX", "AAPL.NYSE"]
        assert filter_premarket(symbols) == ["TSLA.NASDAQ", "AAPL.NYSE"]

    def test_empty_list(self) -> None:
        """Empty list returns empty."""
        assert filter_premarket([]) == []

    def test_unknown_suffix_dropped(self) -> None:
        """Symbols with unknown suffixes are dropped."""
        assert filter_premarket(["BTC.CRYPTO", "GOLD.COMM"]) == []


class TestValidateSymbols:
    def test_valid_symbol_found(self) -> None:
        """Symbol found in provider cache is valid."""
        provider = MagicMock()
        provider.find.return_value = MagicMock()  # non-None instrument

        valid, invalid = validate_symbols(["TSLA.NASDAQ"], provider)

        assert valid == ["TSLA.NASDAQ"]
        assert invalid == []

    def test_invalid_symbol_not_found(self) -> None:
        """Symbol not resolvable by provider is invalid."""
        provider = MagicMock()
        provider.find.return_value = None
        # simulate load_async still leaving it missing
        provider.load_async = MagicMock()

        valid, invalid = validate_symbols(["BAD.TICKER"], provider)

        assert valid == []
        assert invalid == ["BAD.TICKER"]

    def test_mixed_valid_and_invalid(self) -> None:
        """Mixed list splits into valid and invalid."""
        provider = MagicMock()

        def _find(iid: Any) -> Any:
            if str(iid) == "TSLA.NASDAQ":
                return MagicMock()
            return None

        provider.find.side_effect = _find

        valid, invalid = validate_symbols(
            ["TSLA.NASDAQ", "UNKNOWN.XYZ"],
            provider,
        )

        assert valid == ["TSLA.NASDAQ"]
        assert invalid == ["UNKNOWN.XYZ"]
