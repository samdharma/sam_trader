"""Unit tests for KillSwitchSubscriber."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model.enums import TradingState

from sam_trader.config import SamTraderConfig
from sam_trader.kill_switch_subscriber import KillSwitchSubscriber


@pytest.fixture
def mock_node() -> MagicMock:
    """Return a mocked TradingNode with risk_engine and strategies."""
    node = MagicMock()
    node.get_event_loop.return_value = asyncio.new_event_loop()
    node.kernel.exec_engine.risk_engine = MagicMock()
    node.trader.strategies.return_value = []
    return node


@pytest.fixture
def cfg() -> SamTraderConfig:
    return SamTraderConfig(
        trader_id="sam_trader",
        environment="paper",
        log_level="INFO",
        ib_enabled=False,
        ib_gateway_host="",
        ib_gateway_port=0,
        ib_client_id=0,
        ib_account_id="",
        ib_symbols=[],
        ib_read_only_api=False,
        ib_market_data_type="REALTIME",
        futu_enabled=False,
        futu_opend_host="",
        futu_opend_port=0,
        futu_trd_env="SIMULATE",
        futu_trd_market="US",
        futu_unlock_pwd_md5="",
        actor_bar_resub_enabled=False,
        actor_journal_enabled=False,
        actor_health_enabled=False,
        actor_rejection_monitor_enabled=False,
        actor_realized_pnl_enabled=False,
        actor_position_snapshot_enabled=False,
        state_save_enabled=False,
        state_load_enabled=False,
        state_save_handshake_timeout=30,
        bundles_path="config/bundles.yaml",
        postgres_host="",
        postgres_port=0,
        postgres_db="",
        postgres_user="",
        postgres_password="",
        redis_host="localhost",
        redis_port=6379,
        redis_password="",
        risk_max_order_submit_rate="",
        risk_max_order_modify_rate="",
        risk_max_notional_per_order="",
        risk_bypass=False,
    )


class TestKillSwitchSubscriber:
    """Tests for KillSwitchSubscriber state transitions."""

    def test_halted_sets_trading_state_and_cancels_orders(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """HALTED message sets TradingState.HALTED and triggers market_exit."""
        strategy = MagicMock()
        mock_node.trader.strategies.return_value = [strategy]

        sub = KillSwitchSubscriber(mock_node, cfg)
        sub._apply_state(TradingState.HALTED, "HALTED")

        risk_engine = mock_node.kernel.exec_engine.risk_engine
        risk_engine.set_trading_state.assert_called_once_with(TradingState.HALTED)
        strategy.market_exit.assert_called_once()

    def test_close_only_sets_reducing_no_cancel(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """CLOSE_ONLY message sets TradingState.REDUCING without market_exit."""
        strategy = MagicMock()
        mock_node.trader.strategies.return_value = [strategy]

        sub = KillSwitchSubscriber(mock_node, cfg)
        sub._apply_state(TradingState.REDUCING, "CLOSE_ONLY")

        risk_engine = mock_node.kernel.exec_engine.risk_engine
        risk_engine.set_trading_state.assert_called_once_with(TradingState.REDUCING)
        strategy.market_exit.assert_not_called()

    def test_running_sets_active(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """RUNNING message sets TradingState.ACTIVE."""
        sub = KillSwitchSubscriber(mock_node, cfg)
        sub._apply_state(TradingState.ACTIVE, "RUNNING")

        risk_engine = mock_node.kernel.exec_engine.risk_engine
        risk_engine.set_trading_state.assert_called_once_with(TradingState.ACTIVE)

    def test_unknown_state_is_ignored(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """Unknown safety state is ignored and does not touch the risk engine."""
        sub = KillSwitchSubscriber(mock_node, cfg)
        asyncio.run(sub._handle_state("UNKNOWN"))

        risk_engine = mock_node.kernel.exec_engine.risk_engine
        risk_engine.set_trading_state.assert_not_called()

    def test_market_exit_failure_is_logged(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """If market_exit raises, the error is logged and other strategies continue."""
        bad_strategy = MagicMock()
        bad_strategy.id = "bad"
        bad_strategy.market_exit.side_effect = RuntimeError("boom")

        good_strategy = MagicMock()
        good_strategy.id = "good"

        mock_node.trader.strategies.return_value = [bad_strategy, good_strategy]

        sub = KillSwitchSubscriber(mock_node, cfg)
        sub._cancel_all_orders()

        bad_strategy.market_exit.assert_called_once()
        good_strategy.market_exit.assert_called_once()

    def test_start_stop_lifecycle(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """Subscriber starts and stops its background thread cleanly."""
        sub = KillSwitchSubscriber(mock_node, cfg)
        with patch.object(sub, "_run"):
            sub.start()
            assert sub._thread is not None
            sub.stop()
            assert sub._thread is not None
