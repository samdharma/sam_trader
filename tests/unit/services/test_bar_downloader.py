"""Unit tests for BarDownloader service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from sam_trader.services.bar_downloader import (
    _BAR_TYPE_TO_KL_TYPE,
    BarDownloader,
    BarDownloaderError,
    get_instruments_from_bundles,
)


@pytest.fixture
def downloader(tmp_path: Any) -> BarDownloader:
    """Return a BarDownloader pointing at a temp catalog path."""
    return BarDownloader(catalog_path=str(tmp_path))


class TestBarDownloaderDownload:
    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_download_single_instrument(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """Download bars for one instrument writes to catalog."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_df = pd.DataFrame(
            {
                "time_key": ["2024-01-01 09:30:00", "2024-01-01 09:35:00"],
                "open": [100.0, 101.0],
                "close": [101.0, 102.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "volume": [1000, 2000],
            }
        )
        mock_ctx.request_history_kline.return_value = (0, mock_df, None)
        mock_get_ctx.return_value = mock_ctx

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=30,
            )
        )

        assert result.total_bars_written == 2
        assert result.instruments_failed == []
        assert len(result.results) == 1
        assert result.results[0].instrument_id == "TSLA.NASDAQ"
        assert result.results[0].bars_downloaded == 2
        mock_catalog.write_data.assert_called_once()
        written_bars = mock_catalog.write_data.call_args[0][0]
        assert len(written_bars) == 2

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_incremental_update_uses_catalog_latest(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """When catalog has bars newer than lookback start, start date is adjusted."""
        from datetime import datetime, timedelta, timezone

        mock_catalog = MagicMock()
        # Catalog has bars from 10 days ago — well within the 365-day lookback
        latest = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=10))
        mock_catalog.query_last_timestamp.return_value = latest
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_df = pd.DataFrame(
            {
                "time_key": ["2024-06-16 09:30:00"],
                "open": [100.0],
                "close": [101.0],
                "high": [102.0],
                "low": [99.0],
                "volume": [1000],
            }
        )
        mock_ctx.request_history_kline.return_value = (0, mock_df, None)
        mock_get_ctx.return_value = mock_ctx

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=365,
            )
        )

        assert result.total_bars_written == 1
        call_args = mock_ctx.request_history_kline.call_args[1]
        # start should be day after catalog latest (9 days ago)
        expected_start = datetime.now(timezone.utc).date() - timedelta(days=9)
        assert call_args["start"] == str(expected_start)

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_rate_limit_sleep_between_requests(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """Downloader sleeps between Futu API requests."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        # First page has data, second page is empty
        mock_df = pd.DataFrame(
            {
                "time_key": ["2024-01-01 09:30:00"],
                "open": [100.0],
                "close": [101.0],
                "high": [102.0],
                "low": [99.0],
                "volume": [1000],
            }
        )
        mock_ctx.request_history_kline.side_effect = [
            (0, mock_df, "next_page_key"),
            (0, pd.DataFrame(), None),
        ]
        mock_get_ctx.return_value = mock_ctx

        with patch("asyncio.sleep") as mock_sleep:
            asyncio.run(
                downloader.download(
                    instrument_ids=["TSLA.NASDAQ"],
                    bar_type_spec="5-MINUTE",
                    lookback_days=30,
                )
            )

        assert mock_sleep.call_count >= 2
        # 30 req/min = 2.0s delay
        assert mock_sleep.call_args_list[0][0][0] == pytest.approx(2.0, rel=0.1)

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_empty_response_graceful(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """Empty DataFrame returns 0 bars without error."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_ctx.request_history_kline.return_value = (0, pd.DataFrame(), None)
        mock_get_ctx.return_value = mock_ctx

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=30,
            )
        )

        assert result.total_bars_written == 0
        assert result.instruments_failed == []
        mock_catalog.write_data.assert_not_called()

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_futu_api_error_captured(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """Futu API error is captured in result, not raised."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_ctx.request_history_kline.return_value = (-1, "network error", None)
        mock_get_ctx.return_value = mock_ctx

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=30,
            )
        )

        assert result.instruments_failed == ["TSLA.NASDAQ"]
        assert result.results[0].error is not None
        assert "network error" in result.results[0].error
        mock_catalog.write_data.assert_not_called()

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_explicit_start_end_dates(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """Download with explicit start_date/end_date uses them directly."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_df = pd.DataFrame(
            {
                "time_key": ["2023-06-01 09:30:00", "2023-06-01 09:35:00"],
                "open": [100.0, 101.0],
                "close": [101.0, 102.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "volume": [1000, 2000],
            }
        )
        mock_ctx.request_history_kline.return_value = (0, mock_df, None)
        mock_get_ctx.return_value = mock_ctx

        from datetime import date

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                start_date=date(2023, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )

        assert result.total_bars_written == 2
        call_args = mock_ctx.request_history_kline.call_args[1]
        assert call_args["start"] == "2023-01-01"
        assert call_args["end"] == "2024-12-31"

    def test_only_start_date_raises(self, downloader: BarDownloader) -> None:
        """Providing only start_date without end_date raises BarDownloaderError."""
        from datetime import date

        with pytest.raises(BarDownloaderError, match="start_date and end_date"):
            asyncio.run(
                downloader.download(
                    instrument_ids=["TSLA.NASDAQ"],
                    bar_type_spec="5-MINUTE",
                    start_date=date(2023, 1, 1),
                )
            )

    def test_only_end_date_raises(self, downloader: BarDownloader) -> None:
        """Providing only end_date without start_date raises BarDownloaderError."""
        from datetime import date

        with pytest.raises(BarDownloaderError, match="start_date and end_date"):
            asyncio.run(
                downloader.download(
                    instrument_ids=["TSLA.NASDAQ"],
                    bar_type_spec="5-MINUTE",
                    end_date=date(2024, 12, 31),
                )
            )

    def test_unsupported_bar_type_raises(self, downloader: BarDownloader) -> None:
        """Unsupported bar type raises BarDownloaderError immediately."""
        with pytest.raises(BarDownloaderError, match="Unsupported bar_type_spec"):
            asyncio.run(
                downloader.download(
                    instrument_ids=["TSLA.NASDAQ"],
                    bar_type_spec="TICK",
                    lookback_days=30,
                )
            )

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_multiple_instruments_aggregated(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """Multiple instruments produce aggregated results."""
        mock_catalog = MagicMock()
        mock_catalog.query_last_timestamp.return_value = None
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_df = pd.DataFrame(
            {
                "time_key": ["2024-01-01 09:30:00"],
                "open": [100.0],
                "close": [101.0],
                "high": [102.0],
                "low": [99.0],
                "volume": [1000],
            }
        )
        mock_ctx.request_history_kline.return_value = (0, mock_df, None)
        mock_get_ctx.return_value = mock_ctx

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ", "AAPL.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=30,
            )
        )

        assert result.total_bars_written == 2
        assert len(result.results) == 2
        assert result.instruments_failed == []

    @patch("sam_trader.services.bar_downloader.get_cached_futu_quote_context")
    @patch("sam_trader.services.bar_downloader.ParquetDataCatalog")
    def test_catalog_up_to_date_skips_download(
        self,
        mock_catalog_cls: Any,
        mock_get_ctx: Any,
        downloader: BarDownloader,
    ) -> None:
        """When catalog latest is today, no API calls are made."""
        mock_catalog = MagicMock()
        # Latest bar is today — effective_start becomes tomorrow, which is > end_date
        latest = pd.Timestamp(datetime.now(timezone.utc))
        mock_catalog.query_last_timestamp.return_value = latest
        mock_catalog_cls.return_value = mock_catalog

        mock_ctx = MagicMock()
        mock_get_ctx.return_value = mock_ctx

        result = asyncio.run(
            downloader.download(
                instrument_ids=["TSLA.NASDAQ"],
                bar_type_spec="5-MINUTE",
                lookback_days=30,
            )
        )

        assert result.total_bars_written == 0
        mock_ctx.request_history_kline.assert_not_called()
        mock_catalog.write_data.assert_not_called()


class TestBarTypeMappings:
    def test_all_required_bar_types_present(self) -> None:
        """All acceptance-criteria bar types are mapped."""
        required = {"1-MINUTE", "5-MINUTE", "15-MINUTE", "1-HOUR", "DAY"}
        assert required.issubset(set(_BAR_TYPE_TO_KL_TYPE.keys()))


class TestGetInstrumentsFromBundles:
    def test_extracts_futu_instruments(self, tmp_path: Any) -> None:
        """Enabled FUTU bundles yield instrument IDs."""
        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text(
            """
bundles:
  - id: tsla-orb
    enabled: true
    venue: FUTU
    strategy:
      config:
        instrument_id: TSLA.NASDAQ
  - id: aapl-orb
    enabled: true
    venue: FUTU
    strategy:
      config:
        instrument_id: AAPL.NASDAQ
  - id: nvda-ib
    enabled: true
    venue: IB
    strategy:
      config:
        instrument_id: NVDA.NASDAQ
  - id: disabled-bundle
    enabled: false
    venue: FUTU
    strategy:
      config:
        instrument_id: MSFT.NASDAQ
""",
            encoding="utf-8",
        )
        instruments = get_instruments_from_bundles(bundles_yaml)
        assert sorted(instruments) == ["AAPL.NASDAQ", "TSLA.NASDAQ"]

    def test_empty_file_returns_empty(self, tmp_path: Any) -> None:
        """Missing or invalid YAML returns empty list."""
        assert get_instruments_from_bundles(tmp_path / "nonexistent.yaml") == []

    def test_no_bundles_key_returns_empty(self, tmp_path: Any) -> None:
        """YAML without 'bundles' key returns empty list."""
        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("other_key: []", encoding="utf-8")
        assert get_instruments_from_bundles(bundles_yaml) == []
