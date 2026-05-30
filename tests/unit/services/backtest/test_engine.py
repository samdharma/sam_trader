"""Unit tests for BacktestEngineWrapper."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.services.backtest.engine import (
    BacktestEngineError,
    BacktestEngineWrapper,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wrapper() -> BacktestEngineWrapper:
    """Return a BacktestEngineWrapper with a temp catalog path."""
    return BacktestEngineWrapper(catalog_path="/tmp/test-catalog")


@pytest.fixture
def orb_strategy_config() -> ImportableStrategyConfig:
    """Return a minimal ORB strategy config."""
    return ImportableStrategyConfig(
        strategy_path="sam_trader.strategies.orb:OrbStrategy",
        config_path="sam_trader.strategies.orb:OrbStrategyConfig",
        config={
            "instrument_id": "TSLA.NASDAQ",
            "bar_type": "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "first_candle_minutes": 15,
            "trade_size": 5,
            "venue": "FUTU",
            "bundle_id": "tsla-orb-15m",
            "market": "US",
        },
    )


@pytest.fixture
def momentum_strategy_config() -> ImportableStrategyConfig:
    """Return a minimal Momentum strategy config."""
    return ImportableStrategyConfig(
        strategy_path="sam_trader.strategies.momentum:MomentumStrategy",
        config_path="sam_trader.strategies.momentum:MomentumStrategyConfig",
        config={
            "instrument_id": "AAPL.NASDAQ",
            "bar_type": "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            "window": 20,
            "trade_size": 10,
            "venue": "FUTU",
            "bundle_id": "aapl-momentum-5m",
            "market": "US",
        },
    )


@pytest.fixture
def sample_backtest_result() -> BacktestResult:
    """Return a minimal BacktestResult for mock tests."""
    now = datetime.now(timezone.utc)
    now_ns = int(now.timestamp() * 1_000_000_000)
    return BacktestResult(
        trader_id="BACKTEST-001",
        machine_id="test-machine",
        run_config_id="test-run-config-id",
        instance_id="test-instance",
        run_id="test-run-id",
        run_started=now_ns,
        run_finished=now_ns,
        backtest_start=now_ns,
        backtest_end=now_ns,
        elapsed_time=2.5,
        iterations=100,
        total_events=500,
        total_orders=10,
        total_positions=5,
        stats_pnls={"OrbStrategy": {"PnL (total)": 1500.0, "avg_win": 200.0}},
        stats_returns={
            "Sharpe Ratio (252 days)": 1.8,
            "Sortino Ratio (252 days)": 2.1,
            "Max Drawdown": -0.12,
            "Win Rate": 0.55,
            "Profit Factor": 1.8,
        },
    )


# ---------------------------------------------------------------------------
# Config construction tests
# ---------------------------------------------------------------------------


class TestBuildRunConfig:
    """Tests for _build_run_config / build_run_config."""

    def test_constructs_all_config_objects(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """build_run_config returns a valid BacktestRunConfig."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        assert isinstance(result, BacktestRunConfig)
        # Venue is derived from instrument: TSLA.NASDAQ → venue NASDAQ
        assert len(result.venues) == 1
        assert str(result.venues[0].name) == "NASDAQ"
        assert result.venues[0].oms_type == "NETTING"
        assert result.venues[0].account_type == "MARGIN"
        assert result.venues[0].starting_balances == ["100000 USD"]

        assert len(result.data) == 1
        assert result.data[0].catalog_path == "/tmp/test-catalog"
        assert result.data[0].instrument_ids == ["TSLA.NASDAQ"]
        assert result.data[0].bar_types == ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"]
        assert result.data[0].start_time == "2024-01-01"
        assert result.data[0].end_time == "2024-06-30"

        assert result.engine.run_analysis is True  # type: ignore[union-attr]
        assert result.engine.strategies == [  # type: ignore[union-attr]
            orb_strategy_config
        ]

    def test_venue_derived_from_instruments(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Venue name is derived from instrument IDs, not hardcoded."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )
        assert str(result.venues[0].name) == "NASDAQ"

    def test_hedging_oms_type(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """HEDGING OMS type is propagated."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
            oms_type="HEDGING",
            account_type="CASH",
        )
        assert result.venues[0].oms_type == "HEDGING"
        assert result.venues[0].account_type == "CASH"

    def test_custom_starting_balances(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Custom starting_balances override default."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
            starting_balances=["50000 USD", "20000 HKD"],
        )
        assert result.venues[0].starting_balances == ["50000 USD", "20000 HKD"]

    def test_run_analysis_false(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """run_analysis=False disables PortfolioAnalyzer."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
            run_analysis=False,
        )
        assert result.engine.run_analysis is False  # type: ignore[union-attr]

    def test_custom_trader_and_instance_id(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Custom trader_id and instance_id are set."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
            trader_id="MY-TRADER",
            instance_id="inst-42",
        )
        assert str(result.engine.trader_id) == "MY-TRADER"  # type: ignore[union-attr]
        assert result.engine.instance_id == "inst-42"  # type: ignore[union-attr]

    def test_multi_strategy_config(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
        momentum_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Multiple strategies produce correct config."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config, momentum_strategy_config],
            instrument_ids=["TSLA.NASDAQ", "AAPL.NASDAQ"],
            bar_types=[
                "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            ],
            start="2024-01-01",
            end="2024-06-30",
        )
        assert len(result.engine.strategies) == 2  # type: ignore[union-attr]
        assert len(result.data[0].instrument_ids) == 2  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Venue derivation tests
# ---------------------------------------------------------------------------


class TestDeriveVenuesFromInstruments:
    """Tests for _derive_venues_from_instruments."""

    def test_single_nasdaq_instrument(self) -> None:
        """Single NASDAQ instrument returns [NASDAQ]."""
        result = BacktestEngineWrapper._derive_venues_from_instruments(["TSLA.NASDAQ"])
        assert result == ["NASDAQ"]

    def test_single_hkex_instrument(self) -> None:
        """Single HKEX instrument returns [HKEX]."""
        result = BacktestEngineWrapper._derive_venues_from_instruments(["00700.HKEX"])
        assert result == ["HKEX"]

    def test_mixed_venues_returns_all_unique(self) -> None:
        """Mixed venues return all unique venues, sorted."""
        result = BacktestEngineWrapper._derive_venues_from_instruments(
            ["TSLA.NASDAQ", "AAPL.NASDAQ", "00700.HKEX"]
        )
        assert sorted(result) == ["HKEX", "NASDAQ"]

    def test_empty_list_returns_sim(self) -> None:
        """Empty instrument list defaults to [SIM]."""
        result = BacktestEngineWrapper._derive_venues_from_instruments([])
        assert result == ["SIM"]

    def test_invalid_ids_returns_sim(self) -> None:
        """Invalid instrument IDs default to [SIM]."""
        result = BacktestEngineWrapper._derive_venues_from_instruments(
            ["not-a-valid-id", "also-invalid"]
        )
        assert result == ["SIM"]


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestBuildRunConfigErrors:
    """Tests that raise BacktestEngineError on invalid input."""

    def test_empty_strategies_raises(self, wrapper: BacktestEngineWrapper) -> None:
        """Empty strategies list raises."""
        with pytest.raises(BacktestEngineError, match="At least one"):
            wrapper.build_run_config(
                strategies=[],
                instrument_ids=["TSLA.NASDAQ"],
                bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
                start="2024-01-01",
                end="2024-06-30",
            )

    def test_empty_instrument_ids_raises(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Empty instrument_ids raises."""
        with pytest.raises(BacktestEngineError, match="At least one instrument"):
            wrapper.build_run_config(
                strategies=[orb_strategy_config],
                instrument_ids=[],
                bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
                start="2024-01-01",
                end="2024-06-30",
            )

    def test_empty_bar_types_raises(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Empty bar_types raises."""
        with pytest.raises(BacktestEngineError, match="At least one bar_type"):
            wrapper.build_run_config(
                strategies=[orb_strategy_config],
                instrument_ids=["TSLA.NASDAQ"],
                bar_types=[],
                start="2024-01-01",
                end="2024-06-30",
            )


# ---------------------------------------------------------------------------
# BacktestNode execution tests (mocked)
# ---------------------------------------------------------------------------


class TestRunWithMockedNode:
    """Tests that verify the BacktestNode lifecycle with mocks."""

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_single_strategy(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
        sample_backtest_result: BacktestResult,
    ) -> None:
        """run() builds, runs, and returns BacktestResult."""
        mock_engine = MagicMock()
        mock_engine.get_result.return_value = sample_backtest_result

        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        result = wrapper.run(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )

        # Verify lifecycle
        mock_node_cls.assert_called_once()
        mock_node.build.assert_called_once()
        mock_node.run.assert_called_once()
        mock_node.dispose.assert_called_once()

        # Verify result
        assert isinstance(result, BacktestResult)
        assert result.stats_pnls == sample_backtest_result.stats_pnls
        assert result.stats_returns == sample_backtest_result.stats_returns

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_with_run_analysis_true(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
        sample_backtest_result: BacktestResult,
    ) -> None:
        """run_analysis=True is passed to engine config."""
        mock_engine = MagicMock()
        mock_engine.get_result.return_value = sample_backtest_result
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        wrapper.run(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
            run_analysis=True,
        )

        # Check that the config passed to BacktestNode has run_analysis=True
        call_args = mock_node_cls.call_args
        configs = call_args[1]["configs"]
        assert configs[0].engine.run_analysis is True

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_multi_configs(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
        momentum_strategy_config: ImportableStrategyConfig,
        sample_backtest_result: BacktestResult,
    ) -> None:
        """run_multi handles multiple BacktestRunConfig objects."""
        mock_engine = MagicMock()
        mock_engine.get_result.return_value = sample_backtest_result
        mock_node = MagicMock()
        mock_node.get_engine.return_value = mock_engine
        mock_node_cls.return_value = mock_node

        config1 = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-03-31",
        )
        config2 = wrapper.build_run_config(
            strategies=[momentum_strategy_config],
            instrument_ids=["AAPL.NASDAQ"],
            bar_types=["AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-04-01",
            end="2024-06-30",
        )

        results = wrapper.run_multi([config1, config2])

        assert len(results) == 2
        assert mock_node_cls.call_count == 1
        assert mock_node.build.call_count == 1
        assert mock_node.run.call_count == 1

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_disposes_node_on_error(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """BacktestNode.dispose() is called even when run() fails."""
        mock_node = MagicMock()
        mock_node.run.side_effect = RuntimeError("Engine blew up")
        mock_node_cls.return_value = mock_node

        with pytest.raises(BacktestEngineError, match="Engine blew up"):
            wrapper.run(
                strategies=[orb_strategy_config],
                instrument_ids=["TSLA.NASDAQ"],
                bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
                start="2024-01-01",
                end="2024-06-30",
            )

        mock_node.dispose.assert_called_once()

    @patch("sam_trader.services.backtest.engine.BacktestNode")
    def test_run_empty_configs_raises(
        self,
        mock_node_cls: Any,
        wrapper: BacktestEngineWrapper,
    ) -> None:
        """run_multi with empty configs list raises."""
        with pytest.raises(BacktestEngineError, match="No run configs"):
            wrapper.run_multi([])


# ---------------------------------------------------------------------------
# Default parameter tests
# ---------------------------------------------------------------------------


class TestDefaults:
    """Tests for default parameter values."""

    def test_default_starting_balances(
        self,
        wrapper: BacktestEngineWrapper,
        orb_strategy_config: ImportableStrategyConfig,
    ) -> None:
        """Default starting_balances is ['100000 USD']."""
        result = wrapper.build_run_config(
            strategies=[orb_strategy_config],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )
        assert result.venues[0].starting_balances == ["100000 USD"]

    def test_default_catalog_path(self) -> None:
        """Default catalog_path is 'data/catalog'."""
        wrapper = BacktestEngineWrapper()
        result = wrapper.build_run_config(
            strategies=[
                ImportableStrategyConfig(
                    strategy_path="x:X",
                    config_path="x:XC",
                    config={"instrument_id": "TSLA.NASDAQ"},
                )
            ],
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )
        assert result.data[0].catalog_path == "data/catalog"
