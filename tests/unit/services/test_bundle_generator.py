"""Unit tests for the bundle YAML generator."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

from sam_trader.services.bundle_generator import (
    CHANNEL_BUNDLE_LOAD,
    CHANNEL_BUNDLE_LOAD_COMPLETE,
    BundleGenerator,
    BundleGeneratorConfig,
    _infer_venue,
    _make_bar_type,
    _price_to_ticks,
    generate_bundles,
    publish_bundles_to_redis,
    write_bundles,
)
from sam_trader.services.gap_scanner import GapCandidate
from sam_trader.services.pipeline_executor import PipelineCandidate
from sam_trader.services.risk_sizing import PositionSizeResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gap_candidate(
    instrument_id: str = "TSLA.NASDAQ",
    **kwargs: Any,
) -> GapCandidate:
    defaults: dict[str, Any] = {
        "instrument_id": instrument_id,
        "prev_close": 150.0,
        "quote_last": 155.0,
        "gap_pct": 3.33,
        "bid": 154.9,
        "ask": 155.1,
        "volume": 1_000_000.0,
        "trend": "STABLE",
        "pass_number": 1,
        "cross_validated": False,
        "cross_validation_note": "",
    }
    defaults.update(kwargs)
    return GapCandidate(
        instrument_id=defaults["instrument_id"],
        prev_close=defaults["prev_close"],
        quote_last=defaults["quote_last"],
        gap_pct=defaults["gap_pct"],
        bid=defaults["bid"],
        ask=defaults["ask"],
        volume=defaults["volume"],
        trend=defaults["trend"],
        pass_number=defaults["pass_number"],
        cross_validated=defaults["cross_validated"],
        cross_validation_note=defaults["cross_validation_note"],
    )


def _make_approved_candidate(
    instrument_id: str = "TSLA.NASDAQ",
    position_size: int = 50,
    approved: bool = True,
) -> PipelineCandidate:
    gap = _make_gap_candidate(instrument_id=instrument_id)
    size = PositionSizeResult(
        position_size=position_size,
        max_risk_dollars=500.0,
        var_95=300.0,
    )
    return PipelineCandidate(
        gap=gap,
        position_size=size,
        approved=approved,
    )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


class TestInferVenue:
    def test_us_equity_returns_futu(self) -> None:
        assert _infer_venue("AAPL.NASDAQ") == "FUTU"

    def test_hk_equity_returns_futu(self) -> None:
        assert _infer_venue("00700.HKEX") == "FUTU"


class TestMakeBarType:
    def test_futu_bar_type(self) -> None:
        config = BundleGeneratorConfig()
        assert (
            _make_bar_type("TSLA.NASDAQ", "FUTU", config)
            == "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        )

    def test_ib_bar_type(self) -> None:
        config = BundleGeneratorConfig()
        assert (
            _make_bar_type("AAPL.NASDAQ", "IB", config)
            == "AAPL.NASDAQ-5-MINUTE-LAST-INTERNAL"
        )

    def test_custom_aggregation(self) -> None:
        config = BundleGeneratorConfig(
            bar_aggregation="15-MINUTE-LAST-EXTERNAL",
            ib_bar_aggregation="15-MINUTE-LAST-INTERNAL",
        )
        assert (
            _make_bar_type("TSLA.NASDAQ", "FUTU", config)
            == "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        )


class TestPriceToTicks:
    def test_basic_conversion(self) -> None:
        assert _price_to_ticks(1.0, 0.01) == 100

    def test_rounding(self) -> None:
        assert _price_to_ticks(0.015, 0.01) == 2

    def test_minimum_one_tick(self) -> None:
        assert _price_to_ticks(0.005, 0.01) == 1

    def test_zero_tick_size_fallback(self) -> None:
        assert _price_to_ticks(5.0, 0.0) == 10


# ---------------------------------------------------------------------------
# generate_bundles
# ---------------------------------------------------------------------------


class TestGenerateBundles:
    def test_empty_candidates_returns_empty(self) -> None:
        bundles = generate_bundles([])
        assert bundles == []

    def test_single_approved_candidate(self) -> None:
        candidate = _make_approved_candidate()
        bundles = generate_bundles([candidate])

        assert len(bundles) == 1
        bundle = bundles[0]
        assert bundle["venue"] == "FUTU"
        assert bundle["enabled"] is True
        assert bundle["strategy"]["path"] == "sam_trader.strategies.orb:OrbStrategy"
        assert bundle["strategy"]["config"]["instrument_id"] == "TSLA.NASDAQ"
        assert bundle["strategy"]["config"]["trade_size"] == 50
        assert "bracket" in bundle
        assert "risk" in bundle

    def test_rejected_candidate_is_skipped(self) -> None:
        approved = _make_approved_candidate(approved=True)
        rejected = _make_approved_candidate(approved=False)
        bundles = generate_bundles([approved, rejected])
        assert len(bundles) == 1

    def test_unapproved_candidate_is_skipped(self) -> None:
        candidate = _make_approved_candidate(approved=False)
        bundles = generate_bundles([candidate])
        assert bundles == []

    def test_bracket_values_derived_from_trade_params(self) -> None:
        candidate = _make_approved_candidate()
        bundles = generate_bundles([candidate])
        bracket = bundles[0]["bracket"]
        assert isinstance(bracket["stop_loss_ticks"], int)
        assert bracket["stop_loss_ticks"] >= 1
        assert isinstance(bracket["take_profit_ticks"], int)
        assert bracket["take_profit_ticks"] >= 1

    def test_risk_values_from_position_size(self) -> None:
        candidate = _make_approved_candidate(position_size=75)
        bundles = generate_bundles([candidate])
        risk = bundles[0]["risk"]
        assert risk["max_position"] == 75
        assert risk["max_daily_loss"] == 500

    def test_risk_defaults_when_no_position_size(self) -> None:
        gap = _make_gap_candidate()
        candidate = PipelineCandidate(
            gap=gap,
            position_size=None,
            approved=True,
        )
        bundles = generate_bundles([candidate])
        risk = bundles[0]["risk"]
        assert risk["max_position"] == 500  # default
        assert risk["max_daily_loss"] == 1000  # default

    def test_multiple_candidates(self) -> None:
        candidates = [
            _make_approved_candidate("TSLA.NASDAQ"),
            _make_approved_candidate("AAPL.NASDAQ", position_size=30),
        ]
        bundles = generate_bundles(candidates)
        assert len(bundles) == 2
        ids = {b["id"].split("-")[0] for b in bundles}
        assert ids == {"tsla", "aapl"}

    def test_bundle_id_format(self) -> None:
        candidate = _make_approved_candidate("NVDA.NASDAQ")
        bundles = generate_bundles([candidate])
        bundle_id = bundles[0]["id"]
        parts = bundle_id.split("-")
        assert parts[0] == "nvda"
        assert parts[1] == "orb"
        assert len(parts[2]) == 8  # YYYYMMDD
        assert parts[3] == "futu"

    def test_schema_validation_rejects_invalid(self) -> None:
        gap = _make_gap_candidate(instrument_id="")
        candidate = PipelineCandidate(gap=gap, approved=True)
        bundles = generate_bundles([candidate])
        assert (
            len(bundles) == 0 or bundles[0]["strategy"]["config"]["instrument_id"] == ""
        )

    def test_hk_instrument_bundle(self) -> None:
        candidate = _make_approved_candidate("00700.HKEX")
        bundles = generate_bundles([candidate])
        assert len(bundles) == 1
        assert bundles[0]["venue"] == "FUTU"
        assert bundles[0]["strategy"]["config"]["instrument_id"] == "00700.HKEX"


# ---------------------------------------------------------------------------
# write_bundles
# ---------------------------------------------------------------------------


class TestWriteBundles:
    def test_writes_empty_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bundles.daily.yaml")
            result = write_bundles([], path)
            assert result == os.path.abspath(path)
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert data == {"bundles": []}

    def test_writes_valid_bundles(self) -> None:
        bundles = [
            {
                "id": "test-1",
                "enabled": True,
                "venue": "FUTU",
                "strategy": {
                    "path": "sam_trader.strategies.orb:OrbStrategy",
                    "config": {
                        "instrument_id": "TSLA.NASDAQ",
                        "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                        "trade_size": 10,
                    },
                },
                "bracket": {
                    "stop_loss_ticks": 10,
                    "take_profit_ticks": 30,
                },
                "risk": {
                    "max_position": 100,
                    "max_daily_loss": 500,
                },
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bundles.daily.yaml")
            write_bundles(bundles, path)
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert data["bundles"][0]["id"] == "test-1"

    def test_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nested", "dir", "bundles.daily.yaml")
            write_bundles([], path)
            assert os.path.exists(path)

    def test_default_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config", "bundles.daily.yaml")
            write_bundles([], path)
            assert os.path.exists(path)


# ---------------------------------------------------------------------------
# BundleGenerator class
# ---------------------------------------------------------------------------


class TestBundleGenerator:
    def test_run_with_empty_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bundles.daily.yaml")
            generator = BundleGenerator(BundleGeneratorConfig(output_path=path))
            result = generator.run([])
            assert result == os.path.abspath(path)
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert data["bundles"] == []

    def test_run_with_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bundles.daily.yaml")
            generator = BundleGenerator(BundleGeneratorConfig(output_path=path))
            candidates = [
                _make_approved_candidate("TSLA.NASDAQ"),
                _make_approved_candidate("AAPL.NASDAQ"),
            ]
            result = generator.run(candidates)
            assert result == os.path.abspath(path)
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert len(data["bundles"]) == 2

    def test_custom_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.yaml")
            config = BundleGeneratorConfig(
                output_path=path,
                strategy_path=("sam_trader.strategies.momentum:MomentumStrategy"),
                default_first_candle_minutes=15,
            )
            generator = BundleGenerator(config)
            generator.run([_make_approved_candidate()])
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            bundle = data["bundles"][0]
            assert bundle["strategy"]["path"] == (
                "sam_trader.strategies.momentum:MomentumStrategy"
            )
            assert bundle["strategy"]["config"]["first_candle_minutes"] == 15

    def test_graceful_no_bundles_generated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bundles.daily.yaml")
            generator = BundleGenerator(BundleGeneratorConfig(output_path=path))
            generator.run([])
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "bundles" in content


# ---------------------------------------------------------------------------
# publish_bundles_to_redis
# ---------------------------------------------------------------------------


class TestPublishBundlesToRedis:
    def test_no_redis_returns_zero(self) -> None:
        with patch(
            "sam_trader.services.quote._redis_client",
            return_value=None,
        ):
            result = publish_bundles_to_redis([], market="US")
        assert result["published"] == 0
        assert result["market"] == "US"
        assert result["errors"] == 0

    def test_publishes_each_bundle(self) -> None:
        mock_redis = MagicMock()
        bundles = [
            {"id": "tsla-orb-20260101-futu", "venue": "FUTU"},
            {"id": "aapl-orb-20260101-futu", "venue": "FUTU"},
        ]
        result = publish_bundles_to_redis(bundles, market="US", redis_client=mock_redis)

        assert result["published"] == 2
        assert result["errors"] == 0
        assert mock_redis.publish.call_count == 3  # 2 bundles + 1 complete

        calls = mock_redis.publish.call_args_list
        assert calls[0][0][0] == CHANNEL_BUNDLE_LOAD
        assert json.loads(calls[0][0][1]) == bundles[0]
        assert calls[1][0][0] == CHANNEL_BUNDLE_LOAD
        assert json.loads(calls[1][0][1]) == bundles[1]
        assert calls[2][0][0] == CHANNEL_BUNDLE_LOAD_COMPLETE
        complete = json.loads(calls[2][0][1])
        assert complete["market"] == "US"
        assert complete["count"] == 2

    def test_publishes_complete_even_when_zero_bundles(self) -> None:
        mock_redis = MagicMock()
        result = publish_bundles_to_redis([], market="HK", redis_client=mock_redis)

        assert result["published"] == 0
        assert mock_redis.publish.call_count == 1
        assert mock_redis.publish.call_args[0][0] == CHANNEL_BUNDLE_LOAD_COMPLETE

    def test_continues_on_publish_error(self) -> None:
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = [None, RuntimeError("boom"), None]
        bundles = [
            {"id": "b1", "venue": "FUTU"},
            {"id": "b2", "venue": "FUTU"},
        ]
        result = publish_bundles_to_redis(bundles, market="US", redis_client=mock_redis)

        assert result["published"] == 1
        assert result["errors"] == 1

    def test_uses_redis_client_from_env_when_none_passed(self) -> None:
        mock_redis = MagicMock()
        with patch(
            "sam_trader.services.quote._redis_client",
            return_value=mock_redis,
        ):
            result = publish_bundles_to_redis([{"id": "b1"}], market="US")

        assert result["published"] == 1
        assert mock_redis.publish.call_count == 2
