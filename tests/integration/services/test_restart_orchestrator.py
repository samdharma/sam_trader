"""Integration test for RestartOrchestrator market-switch flow.

Simulates a full market-switch request end-to-end:
1. Publish sam:market_switch_request to Redis
2. Orchestrator waits for state_saved confirmation
3. Orchestrator updates .env MARKET value
4. Orchestrator executes docker compose restart
5. Orchestrator polls sam:state_loaded
6. Verify .env updated and complete notification published
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam_trader.services.restart_orchestrator import (
    MARKET_SWITCH_COMPLETE_CHANNEL,
    MARKET_SWITCH_FAILED_CHANNEL,
    STATE_SAVED_CHANNEL,
    RestartOrchestrator,
    _read_market_from_env,
)


class TestMarketSwitchIntegration:
    """End-to-end market switch simulation."""

    @pytest.fixture
    def env_file(self, tmp_path: Path) -> Path:
        env = tmp_path / ".env"
        env.write_text("MARKET=HK\nFOO=bar\n")
        return env

    @pytest.fixture
    def orchestrator(self, env_file: Path) -> Iterator[RestartOrchestrator]:
        with patch(
            "sam_trader.services.restart_orchestrator.ENV_FILE_PATHS",
            [env_file],
        ):
            with patch(
                "sam_trader.services.restart_orchestrator.is_in_window",
                return_value=True,
            ):
                yield RestartOrchestrator()

    def test_full_switch_us_to_hk(
        self, env_file: Path, orchestrator: RestartOrchestrator
    ) -> None:
        """Simulate full US→HK switch: .env updated, restart issued, state loaded."""
        mock_redis = MagicMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock(return_value=None)
        mock_redis.exists = AsyncMock(return_value=1)

        # state_saved subscription handshake + confirmation
        mock_pubsub.subscribe = AsyncMock(return_value=None)
        mock_pubsub.unsubscribe = AsyncMock(return_value=None)
        mock_pubsub.get_message = AsyncMock(
            side_effect=[
                {"type": "subscribe", "channel": STATE_SAVED_CHANNEL},
                {
                    "type": "message",
                    "channel": STATE_SAVED_CHANNEL,
                    "data": '{"status": "saved"}',
                },
            ]
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            asyncio.run(orchestrator._handle_request(mock_redis, '{"market": "US"}'))

        # .env should now be US
        assert _read_market_from_env(env_file) == "US"

        # docker compose restart should have been called
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "restart" in cmd
        assert "sam-trader" in cmd

        # complete notification should have been published
        complete_calls = [
            c
            for c in mock_redis.publish.await_args_list
            if c[0][0] == MARKET_SWITCH_COMPLETE_CHANNEL
        ]
        assert len(complete_calls) == 1
        payload = json.loads(complete_calls[0][0][1])
        assert payload["market"] == "US"
        assert payload["status"] == "completed"

    def test_full_switch_rollback_on_state_loaded_timeout(
        self, env_file: Path, orchestrator: RestartOrchestrator
    ) -> None:
        """On state_loaded timeout, MARKET should roll back to original value."""
        from sam_trader.services.restart_orchestrator import OrchestratorConfig

        orch = RestartOrchestrator(config=OrchestratorConfig(state_loaded_timeout=0))

        mock_redis = MagicMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock(return_value=None)
        mock_redis.exists = AsyncMock(return_value=0)  # state_loaded never appears

        mock_pubsub.subscribe = AsyncMock(return_value=None)
        mock_pubsub.unsubscribe = AsyncMock(return_value=None)
        mock_pubsub.get_message = AsyncMock(
            side_effect=[
                {"type": "subscribe", "channel": STATE_SAVED_CHANNEL},
                {
                    "type": "message",
                    "channel": STATE_SAVED_CHANNEL,
                    "data": '{"status": "saved"}',
                },
            ]
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            asyncio.run(orch._handle_request(mock_redis, '{"market": "US"}'))

        # MARKET should have rolled back to HK
        assert _read_market_from_env(env_file) == "HK"

        # docker restart should have been called twice (once for US, once rollback)
        assert mock_run.call_count == 2

        # failure notification should have been published
        fail_calls = [
            c
            for c in mock_redis.publish.await_args_list
            if c[0][0] == MARKET_SWITCH_FAILED_CHANNEL
        ]
        assert len(fail_calls) == 1
        payload = json.loads(fail_calls[0][0][1])
        assert "state_loaded timeout" in payload["reason"]
