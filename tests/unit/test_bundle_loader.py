"""Unit tests for bundle_loader.py."""

from __future__ import annotations

import pathlib

import pytest
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.bundle_loader import (
    BundleLoaderError,
    BundleValidationError,
    load_bundles,
)


class TestBundleLoader:
    def test_futu_bundle_parsed(self, tmp_path: pathlib.Path) -> None:
        """A FUTU bundle is correctly parsed into ImportableStrategyConfig."""
        yaml_content = """
bundles:
  - id: "tsla-orb-15m-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 15
        trade_size: 5
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
    risk:
      max_position: 500
      max_daily_loss: 1000
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))

        assert len(configs) == 1
        cfg = configs[0]
        assert isinstance(cfg, ImportableStrategyConfig)
        assert cfg.strategy_path == "sam_trader.strategies.orb:OrbStrategy"
        assert cfg.config_path == "sam_trader.strategies.orb:OrbStrategyConfig"
        assert cfg.config["instrument_id"] == "TSLA.NASDAQ"
        assert cfg.config["futu_code"] == "US.TSLA"
        assert cfg.config["venue"] == "FUTU"
        assert cfg.config["bar_type"] == "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        assert cfg.config["first_candle_minutes"] == 15
        assert cfg.config["trade_size"] == 5
        assert cfg.config["stop_loss_ticks"] == 10
        assert cfg.config["take_profit_ticks"] == 30
        assert cfg.config["max_position"] == 500
        assert cfg.config["max_daily_loss"] == 1000

    def test_futu_symbology_mapping(self, tmp_path: pathlib.Path) -> None:
        """Nautilus instrument_ids map to correct Futu security codes."""
        yaml_content = """
bundles:
  - id: "tsla-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
  - id: "tencent-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "00700.HKEX"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))

        assert len(configs) == 2
        assert configs[0].config["futu_code"] == "US.TSLA"
        assert configs[1].config["futu_code"] == "HK.00700"

    def test_futu_and_ib_coexist(self, tmp_path: pathlib.Path) -> None:
        """Both FUTU and IB bundles load correctly in the same file."""
        yaml_content = """
bundles:
  - id: "tsla-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
  - id: "nvda-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))

        assert len(configs) == 2

        futu_cfg = configs[0]
        assert futu_cfg.config["venue"] == "FUTU"
        assert futu_cfg.config["futu_code"] == "US.TSLA"
        assert futu_cfg.strategy_path == "sam_trader.strategies.orb:OrbStrategy"

        ib_cfg = configs[1]
        assert ib_cfg.config["venue"] == "IB"
        assert "futu_code" not in ib_cfg.config
        assert ib_cfg.strategy_path == "sam_trader.strategies.momentum:MomentumStrategy"
        assert ib_cfg.config["bar_type"] == "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"

    def test_disabled_bundle_skipped(self, tmp_path: pathlib.Path) -> None:
        """Disabled bundles are skipped."""
        yaml_content = """
bundles:
  - id: "enabled-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: s:S
      config:
        instrument_id: "AAPL.NASDAQ"
  - id: "disabled-futu"
    enabled: false
    venue: FUTU
    strategy:
      path: s:S
      config:
        instrument_id: "MSFT.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))

        assert len(configs) == 1
        assert configs[0].config["instrument_id"] == "AAPL.NASDAQ"

    def test_unknown_venue_raises(self, tmp_path: pathlib.Path) -> None:
        """An unknown venue raises BundleValidationError."""
        yaml_content = """
bundles:
  - id: "bad-venue"
    enabled: true
    venue: UNKNOWN
    strategy:
      path: s:S
      config:
        instrument_id: "X"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        with pytest.raises(BundleValidationError, match="Unknown venue"):
            load_bundles(str(path))

    def test_missing_strategy_path_raises(self, tmp_path: pathlib.Path) -> None:
        """A bundle without strategy.path raises BundleValidationError."""
        yaml_content = """
bundles:
  - id: "no-path"
    enabled: true
    venue: FUTU
    strategy:
      config:
        instrument_id: "TSLA.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        with pytest.raises(BundleValidationError, match="strategy.path"):
            load_bundles(str(path))

    def test_missing_file_raises(self, tmp_path: pathlib.Path) -> None:
        """A missing bundles file raises BundleLoaderError."""
        with pytest.raises(BundleLoaderError, match="not found"):
            load_bundles(str(tmp_path / "missing.yaml"))

    def test_empty_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """An empty YAML file returns an empty list."""
        path = tmp_path / "empty.yaml"
        path.write_text("")

        configs = load_bundles(str(path))
        assert configs == []

    def test_path_object_accepted(self, tmp_path: pathlib.Path) -> None:
        """load_bundles accepts a pathlib.Path object."""
        yaml_content = """
bundles:
  - id: "path-test"
    enabled: true
    venue: FUTU
    strategy:
      path: s:S
      config:
        instrument_id: "TSLA.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(path)
        assert len(configs) == 1

    def test_malformed_yaml_raises(self, tmp_path: pathlib.Path) -> None:
        """A malformed YAML file raises BundleLoaderError."""
        path = tmp_path / "bad.yaml"
        path.write_text("not: [ valid yaml {{\n")

        with pytest.raises(BundleLoaderError, match="Failed to parse YAML"):
            load_bundles(str(path))

    def test_duplicate_bundle_id_raises(self, tmp_path: pathlib.Path) -> None:
        """Duplicate bundle IDs raise BundleValidationError."""
        yaml_content = """
bundles:
  - id: "dup"
    enabled: true
    venue: FUTU
    strategy:
      path: s:S
      config:
        instrument_id: "TSLA.NASDAQ"
  - id: "dup"
    enabled: true
    venue: IB
    strategy:
      path: s:S
      config:
        instrument_id: "NVDA.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        with pytest.raises(BundleValidationError, match="Duplicate bundle id"):
            load_bundles(str(path))

    def test_custom_config_path(self, tmp_path: pathlib.Path) -> None:
        """A bundle can specify an explicit config_path."""
        yaml_content = """
bundles:
  - id: "custom-cfg"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config_path: my.custom:CustomConfig
      config:
        instrument_id: "TSLA.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))
        assert configs[0].config_path == "my.custom:CustomConfig"

    def test_futu_risk_params_merged(self, tmp_path: pathlib.Path) -> None:
        """Futu-specific risk params (trd_market, trd_env) merge into config."""
        yaml_content = """
bundles:
  - id: "futu-risk"
    enabled: true
    venue: FUTU
    strategy:
      path: s:S
      config:
        instrument_id: "TSLA.NASDAQ"
    risk:
      trd_market: "US"
      trd_env: "SIMULATE"
      max_position: 100
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))
        assert configs[0].config["trd_market"] == "US"
        assert configs[0].config["trd_env"] == "SIMULATE"
        assert configs[0].config["max_position"] == 100

    def test_ib_smart_exchange_default(self, tmp_path: pathlib.Path) -> None:
        """IB bundles default exchange to SMART to avoid direct-routing fees."""
        yaml_content = """
bundles:
  - id: "nvda-momentum-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))
        assert len(configs) == 1
        assert configs[0].config["exchange"] == "SMART"

    def test_ib_explicit_exchange_preserved(self, tmp_path: pathlib.Path) -> None:
        """An explicitly provided exchange in an IB bundle is preserved."""
        yaml_content = """
bundles:
  - id: "nvda-direct-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        exchange: "NASDAQ"
"""
        path = tmp_path / "bundles.yaml"
        path.write_text(yaml_content)

        configs = load_bundles(str(path))
        assert len(configs) == 1
        assert configs[0].config["exchange"] == "NASDAQ"
