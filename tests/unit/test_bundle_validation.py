"""Unit tests for bundle_validation.py."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

import pytest

from sam_trader.bundle_validation import (
    _run_backtest_gate,
    _validate_bundle_schema,
    _validate_strategy_class,
    validate_bundles,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": "test-bundle",
        "enabled": True,
        "venue": "FUTU",
        "strategy": {
            "path": "sam_trader.strategies.orb:OrbStrategy",
            "config": {
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            },
        },
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestValidateBundleSchema:
    def test_valid_bundle_passes(self) -> None:
        bundle = _make_bundle()
        errors, warnings = _validate_bundle_schema(bundle)
        assert errors == []
        assert warnings == []

    def test_missing_id(self) -> None:
        bundle = _make_bundle()
        del bundle["id"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Missing required field: id" in e for e in errors)

    def test_missing_venue(self) -> None:
        bundle = _make_bundle()
        del bundle["venue"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Missing required field: venue" in e for e in errors)

    def test_unknown_venue(self) -> None:
        bundle = _make_bundle(venue="UNKNOWN")
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Unknown venue: 'UNKNOWN'" in e for e in errors)

    def test_missing_strategy(self) -> None:
        bundle = _make_bundle()
        del bundle["strategy"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Missing required field: strategy" in e for e in errors)

    def test_strategy_not_dict(self) -> None:
        bundle = _make_bundle(strategy="not-a-dict")
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Field 'strategy' must be a mapping" in e for e in errors)

    def test_missing_strategy_path(self) -> None:
        bundle = _make_bundle()
        del bundle["strategy"]["path"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Missing required field: strategy.path" in e for e in errors)

    def test_missing_strategy_config(self) -> None:
        bundle = _make_bundle()
        del bundle["strategy"]["config"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Missing required field: strategy.config" in e for e in errors)

    def test_missing_instrument_id(self) -> None:
        bundle = _make_bundle()
        del bundle["strategy"]["config"]["instrument_id"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any(
            "Missing required field: strategy.config.instrument_id" in e for e in errors
        )

    def test_missing_bar_type(self) -> None:
        bundle = _make_bundle()
        del bundle["strategy"]["config"]["bar_type"]
        errors, _ = _validate_bundle_schema(bundle)
        assert any(
            "Missing required field: strategy.config.bar_type" in e for e in errors
        )

    def test_enabled_not_bool(self) -> None:
        bundle = _make_bundle(enabled="yes")
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Field 'enabled' must be a boolean" in e for e in errors)

    def test_bracket_not_dict(self) -> None:
        bundle = _make_bundle(bracket="bad")
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Field 'bracket' must be a mapping" in e for e in errors)

    def test_risk_not_dict(self) -> None:
        bundle = _make_bundle(risk="bad")
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Field 'risk' must be a mapping" in e for e in errors)

    def test_valid_version_accepted(self) -> None:
        for version in ("0.0.1", "1.0.0", "1.2.3", "10.20.30"):
            bundle = _make_bundle(version=version)
            errors, _ = _validate_bundle_schema(bundle)
            assert not any("version" in e for e in errors), version

    def test_invalid_version_rejected(self) -> None:
        for version in ("1.0", "v1.0.0", "1.0.0-beta", "1.0.0.0", "", "01.02.03"):
            bundle = _make_bundle(version=version)
            errors, _ = _validate_bundle_schema(bundle)
            assert any(
                "Field 'version' must be a valid semver string (x.y.z)" in e
                for e in errors
            ), version

    def test_family_alphanumeric_only(self) -> None:
        bundle = _make_bundle(family="ORB_aggressive_v1")
        errors, _ = _validate_bundle_schema(bundle)
        assert errors == []

    def test_family_invalid_chars_rejected(self) -> None:
        bundle = _make_bundle(family="ORB-aggressive")
        errors, _ = _validate_bundle_schema(bundle)
        assert any(
            "Field 'family' must be alphanumeric with underscores only" in e
            for e in errors
        )

    def test_variant_free_text(self) -> None:
        bundle = _make_bundle(variant="bearish-v1.3 🔥")
        errors, _ = _validate_bundle_schema(bundle)
        assert errors == []

    def test_market_hk_valid(self) -> None:
        bundle = _make_bundle(market="HK")
        errors, _ = _validate_bundle_schema(bundle)
        assert not any("market" in e for e in errors)

    def test_market_us_valid(self) -> None:
        bundle = _make_bundle(market="US")
        errors, _ = _validate_bundle_schema(bundle)
        assert not any("market" in e for e in errors)

    def test_market_missing_ok(self) -> None:
        """Missing market field is valid (defaults to US in loader)."""
        bundle = _make_bundle()
        errors, _ = _validate_bundle_schema(bundle)
        assert not any("market" in e for e in errors)

    def test_market_invalid_rejected(self) -> None:
        bundle = _make_bundle(market="JP")
        errors, _ = _validate_bundle_schema(bundle)
        assert any("Field 'market' must be 'US' or 'HK'" in e for e in errors)


# ---------------------------------------------------------------------------
# Strategy class validation
# ---------------------------------------------------------------------------


class TestValidateStrategyClass:
    def test_valid_strategy_class(self) -> None:
        ok, errors = _validate_strategy_class("sam_trader.strategies.orb:OrbStrategy")
        assert ok is True
        assert errors == []

    def test_valid_strategy_class_momentum(self) -> None:
        ok, errors = _validate_strategy_class(
            "sam_trader.strategies.momentum:MomentumStrategy"
        )
        assert ok is True
        assert errors == []

    def test_invalid_path_format(self) -> None:
        ok, errors = _validate_strategy_class("no_colon_here")
        assert ok is False
        assert any("Invalid strategy path format" in e for e in errors)

    def test_missing_module(self) -> None:
        ok, errors = _validate_strategy_class("nonexistent.module:Class")
        assert ok is False
        assert any("Cannot import module" in e for e in errors)

    def test_missing_class(self) -> None:
        ok, errors = _validate_strategy_class(
            "sam_trader.strategies.orb:NonexistentStrategy"
        )
        assert ok is False
        assert any("Cannot find class" in e for e in errors)

    def test_not_a_strategy_subclass(self) -> None:
        ok, errors = _validate_strategy_class(
            "sam_trader.bundle_loader:BundleLoaderError"
        )
        assert ok is False
        assert any("is not a Strategy subclass" in e for e in errors)

    def test_missing_config_class(self) -> None:
        ok, errors = _validate_strategy_class(
            "sam_trader.strategies.orb:OrbStrategy",
        )
        # OrbStrategyConfig exists, so this should pass
        assert ok is True

        ok, errors = _validate_strategy_class(
            "sam_trader.strategies.orb:OrbStrategyMissing",
        )
        # The strategy class doesn't exist, so it fails at class lookup
        assert ok is False


# ---------------------------------------------------------------------------
# Backtest gate
# ---------------------------------------------------------------------------


class TestRunBacktestGate:
    @pytest.fixture(autouse=True)
    def _suppress_backtest_logs(self, caplog: Any) -> None:
        caplog.set_level(logging.WARNING, logger="nautilus_trader")

    def test_orb_strategy_passes(self) -> None:
        from nautilus_trader.trading.config import ImportableStrategyConfig

        isc = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "trade_size": 10,
                "venue": "FUTU",
            },
        )
        ok, errors = _run_backtest_gate(isc)
        assert ok is True, errors
        assert errors == []

    def test_momentum_strategy_passes(self) -> None:
        from nautilus_trader.trading.config import ImportableStrategyConfig

        isc = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.momentum:MomentumStrategy",
            config_path="sam_trader.strategies.momentum:MomentumStrategyConfig",
            config={
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "trade_size": 10,
                "venue": "IB",
            },
        )
        ok, errors = _run_backtest_gate(isc)
        assert ok is True, errors
        assert errors == []

    def test_missing_instrument_id_fails(self) -> None:
        from nautilus_trader.trading.config import ImportableStrategyConfig

        isc = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "venue": "FUTU",
            },
        )
        ok, errors = _run_backtest_gate(isc)
        assert ok is False
        assert any("missing instrument_id" in e for e in errors)

    def test_missing_bar_type_fails(self) -> None:
        from nautilus_trader.trading.config import ImportableStrategyConfig

        isc = ImportableStrategyConfig(
            strategy_path="sam_trader.strategies.orb:OrbStrategy",
            config_path="sam_trader.strategies.orb:OrbStrategyConfig",
            config={
                "instrument_id": "AAPL.NASDAQ",
                "venue": "FUTU",
            },
        )
        ok, errors = _run_backtest_gate(isc)
        assert ok is False
        assert any("missing bar_type" in e for e in errors)


# ---------------------------------------------------------------------------
# Full validate_bundles
# ---------------------------------------------------------------------------


class TestValidateBundles:
    def test_valid_bundle_file(self, tmp_path: pathlib.Path) -> None:
        yaml_content = """
bundles:
  - id: "tsla-orb-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
    risk:
      max_position: 500
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path))
        assert result.all_passed is True
        assert result.summary == "1/1 bundles passed validation"
        assert len(result.bundles) == 1
        assert result.bundles[0].bundle_id == "tsla-orb-futu"
        assert result.bundles[0].passed is True
        assert result.bundles[0].errors == []

    def test_disabled_bundle_skips_backtest(self, tmp_path: pathlib.Path) -> None:
        yaml_content = """
bundles:
  - id: "disabled-bundle"
    enabled: false
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path))
        assert result.all_passed is True
        assert len(result.bundles) == 1
        assert result.bundles[0].passed is True

    def test_invalid_strategy_path(self, tmp_path: pathlib.Path) -> None:
        yaml_content = """
bundles:
  - id: "bad-strat"
    enabled: true
    venue: FUTU
    strategy:
      path: nonexistent.module:BadStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path))
        assert result.all_passed is False
        assert result.bundles[0].passed is False
        assert any("Cannot import module" in e for e in result.bundles[0].errors)

    def test_schema_error_no_backtest(self, tmp_path: pathlib.Path) -> None:
        yaml_content = """
bundles:
  - id: "bad-schema"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path))
        assert result.all_passed is False
        assert any(
            "Missing required field: strategy.config.bar_type" in e
            for e in result.bundles[0].errors
        )

    def test_multiple_bundles_mixed_results(self, tmp_path: pathlib.Path) -> None:
        yaml_content = """
bundles:
  - id: "good"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
  - id: "bad"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path))
        assert result.all_passed is False
        assert result.summary == "1/2 bundles passed validation"
        good = next(b for b in result.bundles if b.bundle_id == "good")
        bad = next(b for b in result.bundles if b.bundle_id == "bad")
        assert good.passed is True
        assert bad.passed is False

    def test_no_backtest_flag(self, tmp_path: pathlib.Path) -> None:
        yaml_content = """
bundles:
  - id: "tsla-orb-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        assert result.all_passed is True
        # Strategy class check still runs
        assert result.bundles[0].errors == []

    def test_file_not_found(self) -> None:
        result = validate_bundles("/nonexistent/bundles.yaml")
        assert result.all_passed is False
        assert "not found" in result.summary

    def test_malformed_yaml(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("not: [ valid yaml {{")

        result = validate_bundles(str(path))
        assert result.all_passed is False
        assert "Failed to parse YAML" in result.summary

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("")

        result = validate_bundles(str(path))
        assert result.all_passed is True
        assert "No bundles defined" in result.summary

    def test_bundles_not_a_list(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("bundles: 123")

        result = validate_bundles(str(path))
        assert result.all_passed is False
        assert "'bundles' must be a list" in result.summary

    def test_bundle_with_market_hk(self, tmp_path: pathlib.Path) -> None:
        """Bundle with market=HK passes validation."""
        yaml_content = """
bundles:
  - id: "hk-bundle"
    enabled: true
    venue: FUTU
    market: HK
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "00700.HKEX"
        bar_type: "00700.HKEX-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        assert result.all_passed is True
        assert result.summary == "1/1 bundles passed validation"

    def test_bundle_with_invalid_market_fails(self, tmp_path: pathlib.Path) -> None:
        """Bundle with invalid market fails schema validation."""
        yaml_content = """
bundles:
  - id: "bad-market"
    enabled: true
    venue: FUTU
    market: JP
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "00700.HKEX"
        bar_type: "00700.HKEX-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        assert result.all_passed is False
        assert any(
            "Field 'market' must be 'US' or 'HK'" in e for e in result.bundles[0].errors
        )

    def test_bundle_not_a_mapping(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("bundles:\n  - not-a-mapping")

        result = validate_bundles(str(path))
        assert result.all_passed is False
        assert result.bundles[0].passed is False
        assert any(
            "Each bundle must be a mapping" in e for e in result.bundles[0].errors
        )


# ---------------------------------------------------------------------------
# Risk config validation warnings (AC #1)
# ---------------------------------------------------------------------------


class TestRiskConfigWarnings:
    """Verify that production bundles warn on unset/zero risk limits."""

    def test_enabled_bundle_no_risk_warns_both(self, tmp_path: pathlib.Path) -> None:
        """Enabled bundle without risk section warns on both fields."""
        yaml_content = """
bundles:
  - id: "no-risk"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        warnings = result.bundles[0].warnings
        assert any("max_trades_per_day" in w for w in warnings)
        assert any("trade_cooldown_seconds" in w for w in warnings)

    def test_enabled_bundle_zero_limits_warns(self, tmp_path: pathlib.Path) -> None:
        """Enabled bundle with risk set to 0 warns."""
        yaml_content = """
bundles:
  - id: "zero-risk"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
    risk:
      max_trades_per_day: 0
      trade_cooldown_seconds: 0
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        warnings = result.bundles[0].warnings
        assert any("max_trades_per_day" in w for w in warnings)
        assert any("trade_cooldown_seconds" in w for w in warnings)

    def test_enabled_bundle_negative_limits_warns(self, tmp_path: pathlib.Path) -> None:
        """Enabled bundle with negative risk values warns."""
        yaml_content = """
bundles:
  - id: "neg-risk"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
    risk:
      max_trades_per_day: -1
      trade_cooldown_seconds: -1
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        warnings = result.bundles[0].warnings
        assert any("max_trades_per_day" in w for w in warnings)
        assert any("trade_cooldown_seconds" in w for w in warnings)

    def test_disabled_bundle_no_risk_no_warn(self, tmp_path: pathlib.Path) -> None:
        """Disabled bundles should not generate risk warnings."""
        yaml_content = """
bundles:
  - id: "disabled-no-risk"
    enabled: false
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        warnings = result.bundles[0].warnings
        assert not any("max_trades_per_day" in w for w in warnings)
        assert not any("trade_cooldown_seconds" in w for w in warnings)

    def test_enabled_bundle_with_valid_limits_no_warn(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Enabled bundle with positive risk limits passes without warning."""
        yaml_content = """
bundles:
  - id: "valid-risk"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
    risk:
      max_trades_per_day: 5
      trade_cooldown_seconds: 300
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        warnings = result.bundles[0].warnings
        assert not any("max_trades_per_day" in w for w in warnings)
        assert not any("trade_cooldown_seconds" in w for w in warnings)
        assert result.bundles[0].passed is True

    def test_warnings_do_not_block_validation(self, tmp_path: pathlib.Path) -> None:
        """Risk warnings should NOT cause validation failure — they are warnings."""
        yaml_content = """
bundles:
  - id: "warn-but-ok"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
    risk:
      max_trades_per_day: 0
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        result = validate_bundles(str(path), backtest_gate=False)
        assert result.bundles[0].passed is True
        assert any("max_trades_per_day" in w for w in result.bundles[0].warnings)
