"""Unit tests for services/deploy_window.py."""

from __future__ import annotations

import datetime
from typing import Any

from sam_trader.services.deploy_window import check_window, is_in_window


class TestIsInWindow:
    def test_inside_day_window(self) -> None:
        now = datetime.datetime(2024, 1, 1, 6, 30)
        assert is_in_window("05:00-08:00", now) is True

    def test_before_day_window(self) -> None:
        now = datetime.datetime(2024, 1, 1, 4, 0)
        assert is_in_window("05:00-08:00", now) is False

    def test_after_day_window(self) -> None:
        now = datetime.datetime(2024, 1, 1, 9, 0)
        assert is_in_window("05:00-08:00", now) is False

    def test_overnight_window(self) -> None:
        now = datetime.datetime(2024, 1, 1, 23, 30)
        assert is_in_window("23:00-02:00", now) is True

    def test_overnight_window_early_hours(self) -> None:
        now = datetime.datetime(2024, 1, 1, 1, 0)
        assert is_in_window("23:00-02:00", now) is True

    def test_overnight_window_outside(self) -> None:
        now = datetime.datetime(2024, 1, 1, 14, 0)
        assert is_in_window("23:00-02:00", now) is False

    def test_invalid_format_returns_false(self) -> None:
        now = datetime.datetime(2024, 1, 1, 6, 0)
        assert is_in_window("invalid", now) is False

    def test_exact_boundary_start(self) -> None:
        now = datetime.datetime(2024, 1, 1, 5, 0)
        assert is_in_window("05:00-08:00", now) is True

    def test_exact_boundary_end(self) -> None:
        now = datetime.datetime(2024, 1, 1, 8, 0)
        assert is_in_window("05:00-08:00", now) is True


class TestCheckWindow:
    def test_check_window_active(self, capsys: Any) -> None:
        active = check_window("00:00-23:59")
        assert active is True
        captured = capsys.readouterr()
        assert "deploy_window_active=true" in captured.out

    def test_check_window_inactive(self, capsys: Any) -> None:
        active = check_window("00:00-00:01")
        assert active is False
        captured = capsys.readouterr()
        assert "deploy_window_active=false" in captured.out
