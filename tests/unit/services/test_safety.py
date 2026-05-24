"""Unit tests for safety controls (kill switch + circuit breakers)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.safety import (
    SafetyConfig,
    check_connectivity_breaker,
    check_daily_pnl_breaker,
    check_rejection_streak_breaker,
    cmd_halt,
    cmd_kill,
    cmd_resume,
    get_safety_config,
    run_circuit_breaker_monitor,
)


class TestKillSwitchCli:
    """Tests for sam kill / halt / resume CLI commands."""

    def test_kill_switch_cli(self) -> None:
        """``sam kill`` publishes HALTED to Redis and returns success."""
        mock_redis = MagicMock()
        config = SafetyConfig(
            max_daily_loss=0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = cmd_kill(config)

        assert result["status"] == "success"
        assert result["state"] == "HALTED"
        assert result["reason"] == "kill"
        assert mock_redis.set.call_count >= 3
        mock_redis.publish.assert_called_once_with("sam:kill_switch", "HALTED")

    def test_halt_resume_cycle(self) -> None:
        """``sam halt`` → CLOSE_ONLY, ``sam resume`` → RUNNING."""
        mock_redis = MagicMock()
        config = SafetyConfig(
            max_daily_loss=0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            halt_result = cmd_halt(config)
            assert halt_result["state"] == "CLOSE_ONLY"
            assert mock_redis.publish.call_args_list[-1] == (
                ("sam:kill_switch", "CLOSE_ONLY"),
                {},
            )

            resume_result = cmd_resume(config)
            assert resume_result["state"] == "RUNNING"
            assert mock_redis.publish.call_args_list[-1] == (
                ("sam:kill_switch", "RUNNING"),
                {},
            )


class TestDailyPnlBreaker:
    """Tests for the DAILY_PNL circuit breaker."""

    def test_daily_pnl_breaker_trips(self) -> None:
        """Breaker triggers when realized loss exceeds max_daily_loss."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:pnl:orb-tsla:2026-05-24"]
        mock_redis.get.return_value = "-1500.50"

        triggered = check_daily_pnl_breaker(mock_redis, max_daily_loss=1000.0)

        assert len(triggered) == 1
        assert triggered[0]["strategy_id"] == "orb-tsla"
        assert triggered[0]["pnl"] == -1500.50
        assert triggered[0]["limit"] == -1000.0

    def test_daily_pnl_breaker_disabled_when_zero(self) -> None:
        """Breaker is a no-op when max_daily_loss is 0."""
        mock_redis = MagicMock()
        triggered = check_daily_pnl_breaker(mock_redis, max_daily_loss=0)
        assert triggered == []
        mock_redis.scan_iter.assert_not_called()

    def test_daily_pnl_breaker_no_trigger_on_profit(self) -> None:
        """Breaker does not trigger when PnL is positive."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:pnl:orb-tsla:2026-05-24"]
        mock_redis.get.return_value = "500.00"

        triggered = check_daily_pnl_breaker(mock_redis, max_daily_loss=1000.0)
        assert triggered == []


class TestRejectionStreakBreaker:
    """Tests for the REJECTION_STREAK circuit breaker."""

    def test_rejection_streak_breaker(self) -> None:
        """Breaker triggers when RejectionMonitorActor has written halt keys."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:rejection_halt:orb-tsla"]
        mock_redis.get.return_value = "Order rejected: 189"

        triggered = check_rejection_streak_breaker(mock_redis)

        assert len(triggered) == 1
        assert triggered[0]["strategy_id"] == "orb-tsla"
        assert triggered[0]["reason"] == "Order rejected: 189"

    def test_rejection_streak_breaker_empty(self) -> None:
        """Breaker returns empty list when no halt keys exist."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = []

        triggered = check_rejection_streak_breaker(mock_redis)
        assert triggered == []


class TestConnectivityLossDetection:
    """Tests for the CONNECTIVITY_LOSS circuit breaker."""

    def test_connectivity_loss_detection(self) -> None:
        """Breaker logs CRITICAL when heartbeat is older than timeout."""
        mock_redis = MagicMock()
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
        mock_redis.get.return_value = old_ts

        issue = check_connectivity_breaker(mock_redis, timeout_secs=60)

        assert issue is not None
        assert issue["status"] == "timeout"
        assert issue["age_seconds"] > 60

    def test_connectivity_no_heartbeat(self) -> None:
        """Breaker reports no_heartbeat when key is missing."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        issue = check_connectivity_breaker(mock_redis, timeout_secs=60)
        assert issue is not None
        assert issue["status"] == "no_heartbeat"

    def test_connectivity_fresh_heartbeat(self) -> None:
        """Breaker returns None when heartbeat is within timeout."""
        mock_redis = MagicMock()
        fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        mock_redis.get.return_value = fresh_ts

        issue = check_connectivity_breaker(mock_redis, timeout_secs=60)
        assert issue is None


class TestSafetyConfig:
    """Tests for configuration loading."""

    def test_get_safety_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config loads correctly from environment variables."""
        monkeypatch.setenv("SAFETY_MAX_DAILY_LOSS", "2500")
        monkeypatch.setenv("SAFETY_CONNECTIVITY_TIMEOUT_SECS", "120")
        monkeypatch.setenv("SAFETY_MAX_REJECTION_STREAK", "5")

        cfg = get_safety_config()
        assert cfg.max_daily_loss == 2500.0
        assert cfg.connectivity_timeout_secs == 120
        assert cfg.max_rejection_streak == 5

    def test_get_safety_config_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config uses safe defaults when env vars are absent."""
        monkeypatch.delenv("SAFETY_MAX_DAILY_LOSS", raising=False)
        monkeypatch.delenv("SAFETY_CONNECTIVITY_TIMEOUT_SECS", raising=False)
        monkeypatch.delenv("SAFETY_MAX_REJECTION_STREAK", raising=False)

        cfg = get_safety_config()
        assert cfg.max_daily_loss == 0.0
        assert cfg.connectivity_timeout_secs == 60
        assert cfg.max_rejection_streak == 3


class TestCircuitBreakerMonitor:
    """Tests for the integrated monitor run."""

    def test_monitor_runs_all_breakers(self) -> None:
        """``run_circuit_breaker_monitor`` returns audit record with all breakers."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [
            ["sam:pnl:orb-tsla:2026-05-24"],
            ["sam:rejection_halt:orb-tsla"],
        ]
        mock_redis.get.side_effect = [
            "-1500.50",  # pnl
            "Order rejected: 189",  # rejection
            (
                datetime.now(timezone.utc) - timedelta(seconds=300)
            ).isoformat(),  # heartbeat
        ]

        config = SafetyConfig(
            max_daily_loss=1000.0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = run_circuit_breaker_monitor(config)

        assert "timestamp" in result
        assert "breakers" in result
        assert "actions" in result
        assert result["breakers"]["daily_pnl"]["triggered"] is True
        assert result["breakers"]["rejection_streak"]["triggered"] is True
        assert result["breakers"]["connectivity"]["triggered"] is True
        assert len(result["actions"]) == 3

    def test_monitor_no_triggers_when_healthy(self) -> None:
        """Monitor returns empty actions when all systems are healthy."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [[], []]
        mock_redis.get.return_value = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()

        config = SafetyConfig(
            max_daily_loss=1000.0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = run_circuit_breaker_monitor(config)

        assert result["actions"] == []
        assert result["breakers"]["daily_pnl"]["triggered"] is False
        assert result["breakers"]["rejection_streak"]["triggered"] is False
        assert result["breakers"]["connectivity"]["triggered"] is False
