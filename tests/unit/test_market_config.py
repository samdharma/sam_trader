"""Unit tests for MarketConfig frozen dataclass."""

from __future__ import annotations

import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sam_trader.market_config import MarketConfig

# ---------------------------------------------------------------------------
# Sample YAML content matching the production config/market_config.yaml
# ---------------------------------------------------------------------------

SAMPLE_YAML = """
markets:
  US:
    futu_trd_market: "US"
    futu_routing_venues:
      - "NASDAQ"
      - "NYSE"
    ib_enabled: true
    session_timezone: "America/New_York"
    session_open: "09:30"
    session_close: "16:00"
    lunch_start: ""
    lunch_end: ""
    premarket_pipeline_time: "08:30"
    sod_readiness_time: "08:00"
    eod_report_time: "16:05"

  HK:
    futu_trd_market: "HK"
    futu_routing_venues:
      - "HKEX"
    ib_enabled: false
    session_timezone: "Asia/Hong_Kong"
    session_open: "09:30"
    session_close: "16:00"
    lunch_start: "12:00"
    lunch_end: "13:00"
    premarket_pipeline_time: "07:30"
    sod_readiness_time: "07:00"
    eod_report_time: "16:05"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_temp_yaml(content: str) -> Path:
    """Write YAML content to a temporary file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Tests: from_yaml
# ---------------------------------------------------------------------------


class TestMarketConfigFromYaml:
    """Test MarketConfig.from_yaml() classmethod."""

    def test_loads_us_market_correctly(self) -> None:
        """US market entry has all expected field values."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            configs = MarketConfig.from_yaml(tmp_path)
            us = configs["US"]

            assert us.futu_trd_market == "US"
            assert us.futu_routing_venues == ["NASDAQ", "NYSE"]
            assert us.ib_enabled is True
            assert us.session_timezone == "America/New_York"
            assert us.session_open == "09:30"
            assert us.session_close == "16:00"
            assert us.lunch_start == ""
            assert us.lunch_end == ""
            assert us.premarket_pipeline_time == "08:30"
            assert us.sod_readiness_time == "08:00"
            assert us.eod_report_time == "16:05"
        finally:
            tmp_path.unlink()

    def test_loads_hk_market_correctly(self) -> None:
        """HK market entry has all expected field values including lunch times."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            configs = MarketConfig.from_yaml(tmp_path)
            hk = configs["HK"]

            assert hk.futu_trd_market == "HK"
            assert hk.futu_routing_venues == ["HKEX"]
            assert hk.ib_enabled is False
            assert hk.session_timezone == "Asia/Hong_Kong"
            assert hk.session_open == "09:30"
            assert hk.session_close == "16:00"
            assert hk.lunch_start == "12:00"
            assert hk.lunch_end == "13:00"
            assert hk.premarket_pipeline_time == "07:30"
            assert hk.sod_readiness_time == "07:00"
            assert hk.eod_report_time == "16:05"
        finally:
            tmp_path.unlink()

    def test_both_markets_loaded(self) -> None:
        """Both US and HK entries are present."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            configs = MarketConfig.from_yaml(tmp_path)
            assert set(configs.keys()) == {"US", "HK"}
        finally:
            tmp_path.unlink()

    def test_file_not_found_raises(self) -> None:
        """Non-existent path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            MarketConfig.from_yaml("/nonexistent/path/market_config.yaml")

    def test_missing_markets_key_raises(self) -> None:
        """YAML without top-level 'markets' key raises ValueError."""
        bad_yaml = "other_key: 42\n"
        tmp_path = _write_temp_yaml(bad_yaml)
        try:
            with pytest.raises(ValueError, match="expected top-level 'markets' key"):
                MarketConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink()

    def test_markets_not_a_dict_raises(self) -> None:
        """YAML where 'markets' is not a dict raises ValueError."""
        bad_yaml = "markets: [1, 2, 3]\n"
        tmp_path = _write_temp_yaml(bad_yaml)
        try:
            with pytest.raises(ValueError, match="'markets' must be a dict"):
                MarketConfig.from_yaml(tmp_path)
        finally:
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# Tests: get_market
# ---------------------------------------------------------------------------


class TestMarketConfigGetMarket:
    """Test MarketConfig.get_market() classmethod."""

    def test_get_market_us(self) -> None:
        """get_market('US') returns the US entry."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            us = MarketConfig.get_market("US", path=tmp_path)
            assert us.futu_trd_market == "US"
            assert us.ib_enabled is True
        finally:
            tmp_path.unlink()

    def test_get_market_hk(self) -> None:
        """get_market('HK') returns the HK entry."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            hk = MarketConfig.get_market("HK", path=tmp_path)
            assert hk.futu_trd_market == "HK"
            assert hk.ib_enabled is False
        finally:
            tmp_path.unlink()

    def test_unknown_market_raises_clear_error(self) -> None:
        """Unknown market raises ValueError with available markets listed."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            with pytest.raises(ValueError, match="Unknown market 'JP'"):
                MarketConfig.get_market("JP", path=tmp_path)
        finally:
            tmp_path.unlink()

    def test_unknown_market_message_lists_available(self) -> None:
        """Error message includes available market names."""
        tmp_path = _write_temp_yaml(SAMPLE_YAML)
        try:
            with pytest.raises(ValueError, match="Available: HK, US"):
                MarketConfig.get_market("JP", path=tmp_path)
        finally:
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# Tests: time validation
# ---------------------------------------------------------------------------


class TestMarketConfigTimeValidation:
    """Test MarketConfig time field validation (HH:MM format)."""

    def test_valid_hhmm_times_accept(self) -> None:
        """Standard HH:MM values pass validation."""
        cfg = MarketConfig(
            futu_trd_market="US",
            session_open="09:30",
            session_close="16:00",
            lunch_start="12:00",
            lunch_end="13:00",
            premarket_pipeline_time="08:30",
            sod_readiness_time="08:00",
            eod_report_time="16:05",
        )
        assert cfg.session_open == "09:30"

    def test_invalid_session_open_raises(self) -> None:
        """Non-HH:MM session_open raises ValueError."""
        with pytest.raises(ValueError, match="session_open"):
            MarketConfig(
                futu_trd_market="US",
                session_open="9:30",  # missing leading zero
            )

    def test_invalid_session_close_raises(self) -> None:
        """Non-HH:MM session_close raises ValueError."""
        with pytest.raises(ValueError, match="session_close"):
            MarketConfig(
                futu_trd_market="US",
                session_close="25:00",  # invalid hour
            )

    def test_invalid_premarket_pipeline_time_raises(self) -> None:
        """Non-HH:MM premarket_pipeline_time raises ValueError."""
        with pytest.raises(ValueError, match="premarket_pipeline_time"):
            MarketConfig(
                futu_trd_market="US",
                premarket_pipeline_time="8:30",  # missing leading zero
            )

    def test_invalid_sod_readiness_time_raises(self) -> None:
        """Non-HH:MM sod_readiness_time raises ValueError."""
        with pytest.raises(ValueError, match="sod_readiness_time"):
            MarketConfig(
                futu_trd_market="US",
                sod_readiness_time="abc",
            )

    def test_invalid_eod_report_time_raises(self) -> None:
        """Non-HH:MM eod_report_time raises ValueError."""
        with pytest.raises(ValueError, match="eod_report_time"):
            MarketConfig(
                futu_trd_market="US",
                eod_report_time="16:60",  # invalid minute
            )

    def test_empty_lunch_start_allowed(self) -> None:
        """Empty lunch_start is allowed (US market has no lunch break)."""
        cfg = MarketConfig(
            futu_trd_market="US",
            lunch_start="",
            lunch_end="",
        )
        assert cfg.lunch_start == ""
        assert cfg.lunch_end == ""

    def test_invalid_lunch_start_not_empty_raises(self) -> None:
        """Non-empty invalid lunch_start raises ValueError."""
        with pytest.raises(ValueError, match="lunch_start"):
            MarketConfig(
                futu_trd_market="US",
                lunch_start="garbage",  # not empty, not HH:MM
            )

    def test_midnight_boundary_accepted(self) -> None:
        """Edge case: 00:00 is valid HH:MM."""
        cfg = MarketConfig(
            futu_trd_market="US",
            session_open="00:00",
            session_close="23:59",
        )
        assert cfg.session_open == "00:00"
        assert cfg.session_close == "23:59"


# ---------------------------------------------------------------------------
# Tests: frozen dataclass
# ---------------------------------------------------------------------------


class TestMarketConfigFrozen:
    """Test that MarketConfig is an immutable frozen dataclass."""

    def test_frozen_dataclass_immutable(self) -> None:
        """Assigning to a field raises FrozenInstanceError."""
        cfg = MarketConfig(
            futu_trd_market="US",
            session_timezone="America/New_York",
        )
        with pytest.raises(FrozenInstanceError):
            cfg.session_timezone = "Asia/Tokyo"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: defaults
# ---------------------------------------------------------------------------


class TestMarketConfigDefaults:
    """Test MarketConfig default values."""

    def test_default_session_times(self) -> None:
        """Default session times match US market convention."""
        cfg = MarketConfig(futu_trd_market="US")
        assert cfg.session_open == "09:30"
        assert cfg.session_close == "16:00"
        assert cfg.premarket_pipeline_time == "08:30"
        assert cfg.sod_readiness_time == "08:00"
        assert cfg.eod_report_time == "16:05"

    def test_default_ib_disabled(self) -> None:
        """Default ib_enabled is False (conservative default)."""
        cfg = MarketConfig(futu_trd_market="US")
        assert cfg.ib_enabled is False

    def test_default_empty_routing_venues(self) -> None:
        """Default futu_routing_venues is empty list."""
        cfg = MarketConfig(futu_trd_market="US")
        assert cfg.futu_routing_venues == []

    def test_default_timezone(self) -> None:
        """Default timezone is America/New_York (US)."""
        cfg = MarketConfig(futu_trd_market="US")
        assert cfg.session_timezone == "America/New_York"
