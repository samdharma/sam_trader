"""Unit tests for services/pipeline.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sam_trader.services.pipeline import run_pipeline


class TestRunPipeline:
    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.write_bundles")
    @patch("sam_trader.services.pipeline.generate_bundles")
    @patch("sam_trader.services.pipeline.PipelineExecutor")
    @patch("sam_trader.services.pipeline.PreMarketGapScanner")
    @patch("sam_trader.services.pipeline.QuoteCollectionService")
    @patch("sam_trader.services.pipeline.build_watchlist")
    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_runs_real_executor(
        self,
        mock_load_wl: Any,
        mock_build_wl: Any,
        mock_quote_svc: Any,
        mock_scanner_cls: Any,
        mock_executor_cls: Any,
        mock_gen_bundles: Any,
        mock_write_bundles: Any,
        mock_report_gen_cls: Any,
    ) -> None:
        mock_load_wl.return_value = {"US": MagicMock(min_gap_pct=2.0)}
        mock_build_wl.return_value = {"US": ["TSLA.NASDAQ", "AAPL.NASDAQ"]}

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[MagicMock()])
        mock_scanner_cls.return_value = mock_scanner

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.approved = [MagicMock()]
        mock_pipeline_result.rejected = []
        mock_pipeline_result.heat_result = None
        mock_pipeline_result.regime_prediction = None
        mock_pipeline_result.audit_trail = []
        mock_pipeline_result.trace_id = "test-trace"

        mock_executor = MagicMock()
        mock_executor.run.return_value = mock_pipeline_result
        mock_executor_cls.return_value = mock_executor

        mock_gen_bundles.return_value = [{"strategy": "test"}]
        mock_write_bundles.return_value = "/path/to/bundles.yaml"

        mock_report = MagicMock()
        mock_report.candidate_count = 1
        mock_report.approved_count = 1
        mock_report.rejected_count = 0
        mock_report.bundles_generated = 1
        mock_report.bundle_path = "/path/to/bundles.yaml"
        mock_report.regime_state = {"regime": "NEUTRAL"}
        mock_report.trace_id = "test-trace"

        mock_report_gen = MagicMock()
        mock_report_gen.generate.return_value = mock_report
        mock_report_gen_cls.return_value = mock_report_gen

        result = run_pipeline(market="US", schedule="08:30")

        assert result["status"] == "success"
        assert result["market"] == "US"
        assert result["schedule"] == "08:30"
        assert result["candidate_count"] == 1
        assert result["approved_count"] == 1
        assert result["bundles_generated"] == 1
        assert result["bundle_path"] == "/path/to/bundles.yaml"
        assert result["trace_id"] == "test-trace"

        mock_executor.run.assert_called_once()
        mock_gen_bundles.assert_called_once_with(mock_pipeline_result.approved)
        mock_write_bundles.assert_called_once()
        mock_report_gen.generate.assert_called_once()
        mock_report_gen.save_audit.assert_called_once_with(mock_report)

    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.write_bundles")
    @patch("sam_trader.services.pipeline.generate_bundles")
    @patch("sam_trader.services.pipeline.PipelineExecutor")
    @patch("sam_trader.services.pipeline.PreMarketGapScanner")
    @patch("sam_trader.services.pipeline.QuoteCollectionService")
    @patch("sam_trader.services.pipeline.build_watchlist")
    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_passes_market_to_regime_venue(
        self,
        mock_load_wl: Any,
        mock_build_wl: Any,
        mock_quote_svc: Any,
        mock_scanner_cls: Any,
        mock_executor_cls: Any,
        mock_gen_bundles: Any,
        mock_write_bundles: Any,
        mock_report_gen_cls: Any,
    ) -> None:
        mock_load_wl.return_value = {"HK": MagicMock(min_gap_pct=2.0)}
        mock_build_wl.return_value = {"HK": ["00700.HKEX"]}

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[MagicMock()])
        mock_scanner_cls.return_value = mock_scanner

        mock_pipeline_result = MagicMock()
        mock_pipeline_result.approved = []
        mock_pipeline_result.rejected = []
        mock_pipeline_result.heat_result = None
        mock_pipeline_result.regime_prediction = None
        mock_pipeline_result.audit_trail = []
        mock_pipeline_result.trace_id = "test-trace"

        mock_executor = MagicMock()
        mock_executor.run.return_value = mock_pipeline_result
        mock_executor_cls.return_value = mock_executor

        mock_report = MagicMock()
        mock_report.candidate_count = 0
        mock_report.approved_count = 0
        mock_report.rejected_count = 0
        mock_report.bundles_generated = 0
        mock_report.bundle_path = None
        mock_report.regime_state = {"regime": None}
        mock_report.trace_id = "test-trace"

        mock_report_gen = MagicMock()
        mock_report_gen.generate.return_value = mock_report
        mock_report_gen_cls.return_value = mock_report_gen

        result = run_pipeline(market="HK", schedule="08:30")

        assert result["market"] == "HK"
        # Verify PipelineExecutorConfig was constructed with regime_venue="HK"
        call_kwargs = mock_executor_cls.call_args.kwargs
        assert "config" in call_kwargs
        assert call_kwargs["config"].regime_venue == "HK"

    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_handles_empty_watchlist(
        self,
        mock_load_wl: Any,
    ) -> None:
        mock_load_wl.return_value = {"US": MagicMock(min_gap_pct=2.0)}

        with patch(
            "sam_trader.services.pipeline.build_watchlist", return_value={"US": []}
        ):
            result = run_pipeline(market="US", schedule="08:30")

        assert result["status"] == "success"
        assert result["candidate_count"] == 0
        assert result["approved_count"] == 0
        assert result["bundles_generated"] == 0
        assert result["bundle_path"] is None
        assert "No symbols" in result["note"]

    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.build_watchlist")
    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_skips_on_holiday(
        self,
        mock_load_wl: Any,
        mock_build_wl: Any,
        mock_report_gen_cls: Any,
    ) -> None:
        mock_load_wl.return_value = {"US": MagicMock(min_gap_pct=2.0)}
        mock_build_wl.return_value = {"US": ["TSLA.NASDAQ"]}

        mock_report = MagicMock()
        mock_report.candidate_count = 0
        mock_report.approved_count = 0
        mock_report.rejected_count = 0
        mock_report.bundles_generated = 0
        mock_report.bundle_path = None
        mock_report.regime_state = {"regime": None}
        mock_report.trace_id = "test-trace"

        mock_report_gen = MagicMock()
        mock_report_gen.generate.return_value = mock_report
        mock_report_gen_cls.return_value = mock_report_gen

        with patch(
            "sam_trader.services.pipeline.MarketCalendarService"
        ) as mock_calendar_cls:
            mock_calendar = MagicMock()
            mock_calendar.is_trading_day.return_value = False
            mock_calendar.holiday_name.return_value = "Independence Day"
            mock_calendar_cls.from_env.return_value = mock_calendar

            result = run_pipeline(market="US", schedule="08:30")

        assert result["status"] == "success"
        assert result["holiday_skipped"] is True
        assert result["holiday_name"] == "Independence Day"
        assert result["candidate_count"] == 0
        assert result["approved_count"] == 0
        assert result["bundles_generated"] == 0
        mock_report_gen.save_audit.assert_called_once_with(mock_report)

    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.build_watchlist")
    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_skips_on_hk_holiday(
        self,
        mock_load_wl: Any,
        mock_build_wl: Any,
        mock_report_gen_cls: Any,
    ) -> None:
        mock_load_wl.return_value = {"HK": MagicMock(min_gap_pct=2.0)}
        mock_build_wl.return_value = {"HK": ["00700.HKEX"]}

        mock_report = MagicMock()
        mock_report.candidate_count = 0
        mock_report.approved_count = 0
        mock_report.rejected_count = 0
        mock_report.bundles_generated = 0
        mock_report.bundle_path = None
        mock_report.regime_state = {"regime": None}
        mock_report.trace_id = "test-trace"

        mock_report_gen = MagicMock()
        mock_report_gen.generate.return_value = mock_report
        mock_report_gen_cls.return_value = mock_report_gen

        with patch(
            "sam_trader.services.pipeline.MarketCalendarService"
        ) as mock_calendar_cls:
            mock_calendar = MagicMock()
            mock_calendar.is_trading_day.return_value = False
            mock_calendar.holiday_name.return_value = None
            mock_calendar_cls.from_env.return_value = mock_calendar

            result = run_pipeline(market="HK", schedule="08:30")

        assert result["status"] == "success"
        assert result["holiday_skipped"] is True
        assert result["holiday_name"] == "HK market holiday"
        assert result["candidate_count"] == 0

    @patch("sam_trader.services.pipeline.PreMarketGapScanner")
    @patch("sam_trader.services.pipeline.QuoteCollectionService")
    @patch("sam_trader.services.pipeline.build_watchlist")
    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_handles_scan_failure(
        self,
        mock_load_wl: Any,
        mock_build_wl: Any,
        mock_quote_svc: Any,
        mock_scanner_cls: Any,
    ) -> None:
        mock_load_wl.return_value = {"US": MagicMock(min_gap_pct=2.0)}
        mock_build_wl.return_value = {"US": ["TSLA.NASDAQ"]}

        mock_scanner = MagicMock()
        mock_scanner.scan = MagicMock(side_effect=RuntimeError("connection refused"))
        mock_scanner_cls.return_value = mock_scanner

        result = run_pipeline(market="US", schedule="08:30")

        assert result["status"] == "error"
        assert "Gap scan failed" in result["error"]
