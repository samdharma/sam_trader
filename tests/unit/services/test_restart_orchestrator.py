"""Unit tests for RestartOrchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam_trader.services.restart_orchestrator import (
    MARKET_SWITCH_COMPLETE_CHANNEL,
    MARKET_SWITCH_FAILED_CHANNEL,
    RESTART_REQUEST_CHANNEL,
    STATE_SAVED_CHANNEL,
    OrchestratorConfig,
    RestartOrchestrator,
    _find_env_file,
    _read_market_from_env,
    _update_market_in_env,
)


class TestEnvHelpers:
    """Tests for .env helper functions."""

    def test_find_env_file_returns_existing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MARKET=US\n")
        with patch(
            "sam_trader.services.restart_orchestrator.ENV_FILE_PATHS",
            [env_file],
        ):
            assert _find_env_file() == env_file

    def test_find_env_file_returns_none_when_missing(self) -> None:
        with patch(
            "sam_trader.services.restart_orchestrator.ENV_FILE_PATHS",
            [Path("/nonexistent/.env")],
        ):
            assert _find_env_file() is None

    def test_read_market_from_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nMARKET=HK\nBAZ=qux\n")
        assert _read_market_from_env(env_file) == "HK"

    def test_read_market_from_env_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        assert _read_market_from_env(env_file) == ""

    def test_update_market_in_env_existing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nMARKET=US\nBAZ=qux\n")
        _update_market_in_env(env_file, "HK")
        assert "MARKET=HK" in env_file.read_text()
        assert "MARKET=US" not in env_file.read_text()

    def test_update_market_in_env_adds_when_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")
        _update_market_in_env(env_file, "US")
        text = env_file.read_text()
        assert "MARKET=US" in text


class TestRestartOrchestrator:
    """Tests for RestartOrchestrator."""

    @pytest.fixture
    def mock_redis(self) -> MagicMock:
        return MagicMock()

    def test_start_stop(self) -> None:
        orch = RestartOrchestrator()
        with patch.object(orch, "_listen") as mock_listen:
            orch.start()
            assert orch._thread is not None
            assert orch._thread.is_alive()
            orch.stop()
            assert not orch._thread.is_alive()
        _ = mock_listen  # mock is used by side-effect in thread

    def test_handle_request_invalid_market(self) -> None:
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=True,
        ):
            with patch.object(orch, "_publish_failed") as mock_fail:
                asyncio.run(orch._handle_request(mock_r, '{"market": "XX"}'))
                mock_fail.assert_awaited_once()
                assert "invalid market" in mock_fail.call_args[0][1]

    def test_handle_request_outside_maintenance_window(self) -> None:
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=False,
        ):
            with patch.object(orch, "_publish_failed") as mock_fail:
                asyncio.run(orch._handle_request(mock_r, '{"market": "US"}'))
                mock_fail.assert_awaited_once()
                assert "outside maintenance window" in mock_fail.call_args[0][1]

    def test_handle_request_missing_env(self) -> None:
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=True,
        ):
            with patch(
                "sam_trader.services.restart_orchestrator._find_env_file",
                return_value=None,
            ):
                with patch.object(orch, "_publish_failed") as mock_fail:
                    asyncio.run(orch._handle_request(mock_r, '{"market": "US"}'))
                    mock_fail.assert_awaited_once()
                    assert ".env file not found" in mock_fail.call_args[0][1]

    def test_handle_request_state_save_timeout(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MARKET=HK\n")
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=True,
        ):
            with patch(
                "sam_trader.services.restart_orchestrator._find_env_file",
                return_value=env_file,
            ):
                with patch.object(orch, "_wait_for_state_saved", return_value=False):
                    with patch.object(orch, "_publish_failed") as mock_fail:
                        asyncio.run(orch._handle_request(mock_r, '{"market": "US"}'))
                        mock_fail.assert_awaited_once()
                        assert (
                            "state-save handshake timed out"
                            in mock_fail.call_args[0][1]
                        )
        assert _read_market_from_env(env_file) == "HK"

    def test_handle_request_docker_fail_rolls_back(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MARKET=HK\n")
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=True,
        ):
            with patch(
                "sam_trader.services.restart_orchestrator._find_env_file",
                return_value=env_file,
            ):
                with patch.object(orch, "_wait_for_state_saved", return_value=True):
                    with patch.object(orch, "_restart_trader", return_value=False):
                        with patch.object(orch, "_publish_failed") as mock_fail:
                            asyncio.run(
                                orch._handle_request(mock_r, '{"market": "US"}')
                            )
                            mock_fail.assert_awaited_once()
                            assert "docker restart failed" in mock_fail.call_args[0][1]
        assert _read_market_from_env(env_file) == "HK"

    def test_handle_request_state_loaded_timeout_rolls_back(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MARKET=HK\n")
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=True,
        ):
            with patch(
                "sam_trader.services.restart_orchestrator._find_env_file",
                return_value=env_file,
            ):
                with patch.object(orch, "_wait_for_state_saved", return_value=True):
                    with patch.object(orch, "_restart_trader", return_value=True):
                        with patch.object(
                            orch, "_poll_state_loaded", return_value=False
                        ):
                            with patch.object(orch, "_publish_failed") as mock_fail:
                                asyncio.run(
                                    orch._handle_request(mock_r, '{"market": "US"}')
                                )
                                mock_fail.assert_awaited_once()
                                assert (
                                    "state_loaded timeout" in mock_fail.call_args[0][1]
                                )
        assert _read_market_from_env(env_file) == "HK"

    def test_handle_request_success(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("MARKET=HK\n")
        orch = RestartOrchestrator()
        mock_r = MagicMock()
        with patch(
            "sam_trader.services.restart_orchestrator.is_in_window",
            return_value=True,
        ):
            with patch(
                "sam_trader.services.restart_orchestrator._find_env_file",
                return_value=env_file,
            ):
                with patch.object(orch, "_wait_for_state_saved", return_value=True):
                    with patch.object(orch, "_restart_trader", return_value=True):
                        with patch.object(
                            orch, "_poll_state_loaded", return_value=True
                        ):
                            with patch.object(
                                orch, "_publish_complete"
                            ) as mock_complete:
                                asyncio.run(
                                    orch._handle_request(mock_r, '{"market": "US"}')
                                )
                                mock_complete.assert_awaited_once_with(mock_r, "US")
        assert _read_market_from_env(env_file) == "US"

    def test_wait_for_state_saved_confirmed(self) -> None:
        orch = RestartOrchestrator()
        mock_redis = MagicMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock(return_value=None)
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

        result = asyncio.run(orch._wait_for_state_saved(mock_redis))
        assert result is True
        mock_redis.publish.assert_awaited_once_with(RESTART_REQUEST_CHANNEL, "graceful")

    def test_wait_for_state_saved_timeout(self) -> None:
        orch = RestartOrchestrator(config=OrchestratorConfig(state_save_timeout=0))
        mock_redis = MagicMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        mock_redis.publish = AsyncMock(return_value=None)
        mock_pubsub.subscribe = AsyncMock(return_value=None)
        mock_pubsub.unsubscribe = AsyncMock(return_value=None)
        mock_pubsub.get_message = AsyncMock(
            side_effect=[
                {"type": "subscribe", "channel": STATE_SAVED_CHANNEL},
                None,
            ]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(orch._wait_for_state_saved(mock_redis))
        assert result is False

    def test_poll_state_loaded_found(self) -> None:
        orch = RestartOrchestrator()
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=1)

        result = asyncio.run(orch._poll_state_loaded(mock_redis))
        assert result is True

    def test_poll_state_loaded_timeout(self) -> None:
        orch = RestartOrchestrator(config=OrchestratorConfig(state_loaded_timeout=0))
        mock_redis = MagicMock()
        mock_redis.exists = AsyncMock(return_value=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(orch._poll_state_loaded(mock_redis))
        assert result is False

    def test_restart_trader_success(self) -> None:
        orch = RestartOrchestrator()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = orch._restart_trader()
            assert result is True
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "restart" in cmd
            assert "sam-trader" in cmd

    def test_restart_trader_failure(self) -> None:
        orch = RestartOrchestrator()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="docker error")
            result = orch._restart_trader()
            assert result is False

    def test_publish_complete(self) -> None:
        orch = RestartOrchestrator()
        mock_redis = MagicMock()
        mock_redis.publish = AsyncMock(return_value=None)
        asyncio.run(orch._publish_complete(mock_redis, "HK"))
        mock_redis.publish.assert_awaited_once()
        channel = mock_redis.publish.call_args[0][0]
        assert channel == MARKET_SWITCH_COMPLETE_CHANNEL

    def test_publish_failed(self) -> None:
        orch = RestartOrchestrator()
        mock_redis = MagicMock()
        mock_redis.publish = AsyncMock(return_value=None)
        asyncio.run(orch._publish_failed(mock_redis, "some reason"))
        mock_redis.publish.assert_awaited_once()
        channel = mock_redis.publish.call_args[0][0]
        assert channel == MARKET_SWITCH_FAILED_CHANNEL
