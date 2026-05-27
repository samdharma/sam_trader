"""Unit tests for services/pipeline.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from sam_trader.services.pipeline import (
    _convert_pipeline_time_to_hkt,
    _get_active_market,
    _get_pipeline_schedule,
    run_pipeline,
)


class TestRunPipeline:
    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.publish_bundles_to_redis")
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
        mock_publish: Any,
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
        mock_publish.return_value = {"published": 1}

        mock_report = MagicMock()
        mock_report.candidate_count = 1
        mock_report.approved_count = 1
        mock_report.rejected_count = 0
        mock_report.bundles_generated = 1
        mock_report.bundle_path = None
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
        assert result["bundles_published"] == 1
        assert result["bundle_path"] is None
        assert result["trace_id"] == "test-trace"

        mock_executor.run.assert_called_once()
        mock_gen_bundles.assert_called_once_with(mock_pipeline_result.approved)
        mock_publish.assert_called_once()
        mock_report_gen.generate.assert_called_once()
        mock_report_gen.save_audit.assert_called_once_with(mock_report)

    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.publish_bundles_to_redis")
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
        mock_publish: Any,
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

    @patch("sam_trader.services.pipeline.ReadinessReportGenerator")
    @patch("sam_trader.services.pipeline.PipelineExecutor")
    @patch("sam_trader.services.pipeline.PreMarketGapScanner")
    @patch("sam_trader.services.pipeline.QuoteCollectionService")
    @patch("sam_trader.services.pipeline.build_watchlist")
    @patch("sam_trader.services.pipeline.load_watchlist_config")
    def test_run_pipeline_logs_market_closed_when_no_candidates(
        self,
        mock_load_wl: Any,
        mock_build_wl: Any,
        mock_quote_svc: Any,
        mock_scanner_cls: Any,
        mock_executor_cls: Any,
        mock_report_gen_cls: Any,
        caplog: Any,
    ) -> None:
        import logging

        mock_load_wl.return_value = {"US": MagicMock(min_gap_pct=2.0)}
        mock_build_wl.return_value = {"US": ["TSLA.NASDAQ"]}

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[])
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

        with caplog.at_level(logging.INFO, logger="sam_trader.pipeline"):
            result = run_pipeline(market="US", schedule="08:30")

        assert result["status"] == "success"
        assert result["candidate_count"] == 0
        assert "0 candidates (market closed)" in caplog.text


class TestMarketAwareScheduling:
    def test_get_active_market_reads_market_env(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("MARKET", "HK")
        assert _get_active_market() == "HK"

    def test_get_active_market_defaults_to_us(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("MARKET", raising=False)
        assert _get_active_market() == "US"

    def test_get_active_market_strips_and_uppercases(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("MARKET", " hk ")
        assert _get_active_market() == "HK"

    def test_get_pipeline_schedule_us(self) -> None:
        schedule = _get_pipeline_schedule("US")
        assert schedule == "08:30"

    def test_get_pipeline_schedule_hk(self) -> None:
        schedule = _get_pipeline_schedule("HK")
        assert schedule == "07:30"

    def test_get_pipeline_schedule_fallback(self) -> None:
        assert _get_pipeline_schedule("UNKNOWN") == "07:30"

    def test_convert_pipeline_time_to_hkt_hk_unchanged(self) -> None:
        assert _convert_pipeline_time_to_hkt("HK", "07:30") == "07:30"

    def test_convert_pipeline_time_to_hkt_us_returns_hkt(self) -> None:
        result = _convert_pipeline_time_to_hkt("US", "08:30")
        # Should be a valid HH:MM string
        assert len(result) == 5
        assert result[2] == ":"
        hour, minute = map(int, result.split(":"))
        assert 0 <= hour <= 23
        assert 0 <= minute <= 59
        # 08:30 ET should convert to either 20:30 or 21:30 HKT depending on DST
        assert result in ("20:30", "21:30")

    def test_run_pipeline_uses_market_env_var(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("MARKET", "HK")
        monkeypatch.delenv("PIPELINE_MARKET", raising=False)

        with (
            patch(
                "sam_trader.services.pipeline.load_watchlist_config",
                return_value={"HK": MagicMock(min_gap_pct=2.0)},
            ),
            patch(
                "sam_trader.services.pipeline.build_watchlist", return_value={"HK": []}
            ),
        ):
            result = run_pipeline()

        assert result["market"] == "HK"

    def test_run_pipeline_market_param_overrides_env(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("MARKET", "US")

        with (
            patch(
                "sam_trader.services.pipeline.load_watchlist_config",
                return_value={"HK": MagicMock(min_gap_pct=2.0)},
            ),
            patch(
                "sam_trader.services.pipeline.build_watchlist", return_value={"HK": []}
            ),
        ):
            result = run_pipeline(market="HK")

        assert result["market"] == "HK"

    def test_run_pipeline_computes_schedule_from_market_config(self) -> None:
        with (
            patch(
                "sam_trader.services.pipeline.load_watchlist_config",
                return_value={"US": MagicMock(min_gap_pct=2.0)},
            ),
            patch(
                "sam_trader.services.pipeline.build_watchlist", return_value={"US": []}
            ),
        ):
            result = run_pipeline(market="US")

        assert result["schedule"] == "08:30"

    def test_run_pipeline_computes_hk_schedule(self) -> None:
        with (
            patch(
                "sam_trader.services.pipeline.load_watchlist_config",
                return_value={"HK": MagicMock(min_gap_pct=2.0)},
            ),
            patch(
                "sam_trader.services.pipeline.build_watchlist", return_value={"HK": []}
            ),
        ):
            result = run_pipeline(market="HK")

        assert result["schedule"] == "07:30"
