"""Unit tests for services/cli.py."""

from __future__ import annotations

import pathlib
from typing import Any

from sam_trader.services.cli import main


class TestValidateBundlesCommand:
    def test_valid_bundles_returns_zero(
        self, tmp_path: pathlib.Path, capsys: Any
    ) -> None:
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

        rc = main(["validate-bundles", "--path", str(path)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "1/1 bundles passed validation" in captured.out
        assert "[PASS] tsla-orb-futu" in captured.out

    def test_invalid_bundles_returns_one(
        self, tmp_path: pathlib.Path, capsys: Any
    ) -> None:
        yaml_content = """
bundles:
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

        rc = main(["validate-bundles", "--path", str(path)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "0/1 bundles passed validation" in captured.out
        assert "[FAIL] bad" in captured.out
        assert "ERROR:" in captured.out

    def test_missing_file_returns_one(self, capsys: Any) -> None:
        rc = main(["validate-bundles", "--path", "/nonexistent/bundles.yaml"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "ERROR: Bundles file not found" in captured.err

    def test_no_backtest_flag(self, tmp_path: pathlib.Path, capsys: Any) -> None:
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

        rc = main(["validate-bundles", "--path", str(path), "--no-backtest"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "1/1 bundles passed validation" in captured.out

    def test_default_path_uses_config_bundles_yaml(
        self, tmp_path: pathlib.Path, monkeypatch: Any, capsys: Any
    ) -> None:
        # Change cwd so the default path resolves to our temp file
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        path = config_dir / "bundles.yaml"
        path.write_text("""
bundles:
  - id: "test"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
""")

        rc = main(["validate-bundles"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "1/1 bundles passed validation" in captured.out
