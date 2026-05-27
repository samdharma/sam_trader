"""Unit tests for RestartSubscriber — Redis graceful restart handshake."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam_trader.config import SamTraderConfig
from sam_trader.restart_subscriber import (
    STATE_SAVED_CHANNEL,
    RestartSubscriber,
)


class FakeLoop:
    """Minimal asyncio loop stand-in for thread-safe scheduling tests."""

    def __init__(self, running: bool = True) -> None:
        self._running = running
        self._callbacks: list = []

    def is_running(self) -> bool:
        return self._running

    def call_soon_threadsafe(self, callback: object) -> None:
        self._callbacks.append(callback)
        # Execute immediately for deterministic tests
        if callable(callback):
            callback()


@pytest.fixture
def cfg() -> SamTraderConfig:
    return SamTraderConfig(
        trader_id="SAM-001",
        environment="paper",
        log_level="INFO",
        ib_enabled=False,
        ib_gateway_host="sam-ib-gateway",
        ib_gateway_port=4004,
        ib_client_id=11,
        ib_account_id="",
        ib_symbols=[],
        ib_read_only_api=False,
        ib_market_data_type="REALTIME",
        futu_enabled=False,
        futu_opend_host="sam-futu-opend",
        futu_opend_port=11111,
        futu_trd_env="SIMULATE",
        futu_trd_market="US",
        futu_unlock_pwd_md5="",
        futu_account_id="",
        futu_keep_alive_interval_secs=1800,
        actor_bar_resub_enabled=False,
        actor_journal_enabled=False,
        actor_health_enabled=False,
        actor_rejection_monitor_enabled=False,
        actor_realized_pnl_enabled=False,
        actor_position_snapshot_enabled=False,
        health_monitor_market="",
        bar_resub_market="",
        market_calendar_enabled=True,
        state_save_enabled=True,
        state_load_enabled=True,
        state_save_handshake_timeout=5,
        bundles_path="config/bundles.yaml",
        postgres_host="sam-postgres",
        postgres_port=5432,
        postgres_db="sam_trader",
        postgres_user="sam",
        postgres_password="sam_secret",
        redis_host="test-redis",
        redis_port=6379,
        redis_password="",
        risk_max_order_submit_rate="100/00:00:01",
        risk_max_order_modify_rate="100/00:00:01",
        risk_max_notional_per_order="",
        risk_bypass=False,
    )


@pytest.fixture
def mock_node() -> MagicMock:
    node = MagicMock()
    node.trader_id = "SAM-001"
    loop = FakeLoop(running=True)
    node.get_event_loop.return_value = loop
    return node


class TestRestartSubscriber:
    """Tests for RestartSubscriber Redis pub/sub behavior."""

    @patch("sam_trader.restart_subscriber.aioredis.Redis")
    def test_graceful_signal_triggers_save_and_confirmation(
        self,
        mock_redis_cls: MagicMock,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """On 'graceful', trader.save() is called and sam:state_saved is published."""
        # Set up async pub/sub mocks
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()

        async def _mock_listen():
            yield {"type": "subscribe", "channel": "sam:restart_request", "data": 1}
            yield {
                "type": "message",
                "channel": "sam:restart_request",
                "data": "graceful",
            }

        mock_pubsub.listen = _mock_listen

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock()
        mock_redis.close = AsyncMock()
        mock_redis_cls.return_value = mock_redis

        subscriber = RestartSubscriber(mock_node, cfg)
        # Run the async listener directly (no thread) for deterministic testing
        import asyncio

        asyncio.run(subscriber._listen())

        # State save should have been triggered
        mock_node.trader.save.assert_called_once()

        # Confirmation should have been published
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == STATE_SAVED_CHANNEL
        payload = json.loads(call_args[0][1])
        assert payload["trader_id"] == "SAM-001"
        assert payload["status"] == "saved"
        # Timestamp should be a valid ISO datetime
        dt = datetime.fromisoformat(payload["timestamp"])
        assert dt.tzinfo is not None

    @patch("sam_trader.restart_subscriber.aioredis.Redis")
    def test_non_graceful_message_ignored(
        self,
        mock_redis_cls: MagicMock,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """Messages other than 'graceful' do not trigger state save."""
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()

        async def _mock_listen():
            yield {"type": "message", "channel": "sam:restart_request", "data": "force"}

        mock_pubsub.listen = _mock_listen

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock()
        mock_redis.close = AsyncMock()
        mock_redis_cls.return_value = mock_redis

        subscriber = RestartSubscriber(mock_node, cfg)
        import asyncio

        asyncio.run(subscriber._listen())

        mock_node.trader.save.assert_not_called()
        mock_redis.publish.assert_not_called()

    @patch("sam_trader.restart_subscriber.aioredis.Redis")
    def test_redis_connection_error_logged(
        self,
        mock_redis_cls: MagicMock,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """Redis connection failure is logged as warning and does not crash."""
        mock_redis_cls.side_effect = ConnectionError("Redis down")

        subscriber = RestartSubscriber(mock_node, cfg)
        import asyncio

        # Should not raise
        asyncio.run(subscriber._listen())

        mock_node.trader.save.assert_not_called()

    @patch("sam_trader.restart_subscriber.aioredis.Redis")
    def test_save_failure_does_not_publish_confirmation(
        self,
        mock_redis_cls: MagicMock,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """If trader.save() raises, no confirmation is published."""
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()

        async def _mock_listen():
            yield {
                "type": "message",
                "channel": "sam:restart_request",
                "data": "graceful",
            }

        mock_pubsub.listen = _mock_listen

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock()
        mock_redis.close = AsyncMock()
        mock_redis_cls.return_value = mock_redis

        mock_node.trader.save.side_effect = RuntimeError("save exploded")

        subscriber = RestartSubscriber(mock_node, cfg)
        import asyncio

        asyncio.run(subscriber._listen())

        mock_node.trader.save.assert_called_once()
        mock_redis.publish.assert_not_called()

    def test_start_stop_thread_lifecycle(
        self,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """start() spawns a daemon thread; stop() signals it to exit."""
        subscriber = RestartSubscriber(mock_node, cfg)

        # Patch _listen to return quickly
        with patch.object(subscriber, "_listen", new_callable=AsyncMock):
            subscriber.start()
            assert subscriber._thread is not None
            assert subscriber._thread.daemon is True
            subscriber.stop()
            assert not subscriber._thread.is_alive()

    def test_save_state_on_running_loop(
        self,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """When node loop is running, save is scheduled thread-safely and executed."""
        subscriber = RestartSubscriber(mock_node, cfg)
        subscriber._save_state()

        mock_node.trader.save.assert_called_once()

    def test_save_state_fallback_when_loop_not_running(
        self,
        cfg: SamTraderConfig,
        mock_node: MagicMock,
    ) -> None:
        """When node loop is not running, save falls back to direct call."""
        mock_node.get_event_loop.return_value = FakeLoop(running=False)

        subscriber = RestartSubscriber(mock_node, cfg)
        subscriber._save_state()

        mock_node.trader.save.assert_called_once()
