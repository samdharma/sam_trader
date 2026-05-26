"""Unit tests for MarketCalendarService."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.market_calendar import (
    _HARDCODED_HOLIDAYS_HK,
    _HARDCODED_HOLIDAYS_US,
    MarketCalendarService,
    _parse_date_dict,
    _parse_date_set,
)


class TestParseDateSet:
    def test_empty(self) -> None:
        assert _parse_date_set("") == set()

    def test_single(self) -> None:
        assert _parse_date_set("2024-07-04") == {"2024-07-04"}

    def test_multiple(self) -> None:
        assert _parse_date_set("2024-07-04, 2024-12-25") == {
            "2024-07-04",
            "2024-12-25",
        }

    def test_whitespace(self) -> None:
        assert _parse_date_set("  2024-07-04  ,  2024-12-25  ") == {
            "2024-07-04",
            "2024-12-25",
        }


class TestParseDateDict:
    def test_empty(self) -> None:
        assert _parse_date_dict("") == {}

    def test_valid_json(self) -> None:
        assert _parse_date_dict('{"2024-11-29": "13:00"}') == {"2024-11-29": "13:00"}

    def test_invalid_json(self) -> None:
        assert _parse_date_dict("not json") == {}


class TestMarketCalendarServiceInit:
    def test_defaults(self) -> None:
        svc = MarketCalendarService()
        assert svc._redis is None
        assert svc._custom_holidays_us == set()
        assert svc._custom_holidays_hk == set()

    def test_custom_holidays(self) -> None:
        svc = MarketCalendarService(
            custom_holidays_us={"2024-07-05"},
            custom_holidays_hk={"2024-07-05"},
        )
        assert "2024-07-05" in svc._custom_holidays_us
        assert "2024-07-05" in svc._custom_holidays_hk

    def test_early_closes(self) -> None:
        svc = MarketCalendarService(
            early_closes_us={"2024-11-29": "13:00"},
        )
        assert svc._early_closes_us == {"2024-11-29": "13:00"}


class TestIsHoliday:
    def test_known_us_holiday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_holiday("US", datetime.date(2024, 7, 4)) is True

    def test_known_hk_holiday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_holiday("HK", datetime.date(2024, 10, 1)) is True

    def test_regular_weekday_not_holiday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_holiday("US", datetime.date(2024, 7, 8)) is False
        assert svc.is_holiday("HK", datetime.date(2024, 7, 8)) is False

    def test_custom_holiday(self) -> None:
        svc = MarketCalendarService(custom_holidays_us={"2024-07-08"})
        assert svc.is_holiday("US", datetime.date(2024, 7, 8)) is True

    def test_invalid_market(self) -> None:
        svc = MarketCalendarService()
        with pytest.raises(ValueError, match="Unsupported market"):
            svc.is_holiday("EU", datetime.date(2024, 7, 4))

    def test_redis_cache_hit(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get.return_value = "1"
        svc = MarketCalendarService(redis_client=mock_redis)
        assert svc.is_holiday("US", datetime.date(2024, 7, 4)) is True
        mock_redis.get.assert_called_once_with("sam:calendar:holiday:US:2024-07-04")
        mock_redis.setex.assert_not_called()

    def test_redis_cache_miss(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        svc = MarketCalendarService(redis_client=mock_redis)
        assert svc.is_holiday("US", datetime.date(2024, 7, 8)) is False
        mock_redis.setex.assert_called_once()

    def test_redis_failure_graceful(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("redis down")
        svc = MarketCalendarService(redis_client=mock_redis)
        # Should still work without Redis
        assert svc.is_holiday("US", datetime.date(2024, 7, 4)) is True

    def test_hardcoded_coverage_us(self) -> None:
        svc = MarketCalendarService()
        for d in _HARDCODED_HOLIDAYS_US:
            assert svc.is_holiday("US", datetime.date.fromisoformat(d)) is True

    def test_hardcoded_coverage_hk(self) -> None:
        svc = MarketCalendarService()
        for d in _HARDCODED_HOLIDAYS_HK:
            assert svc.is_holiday("HK", datetime.date.fromisoformat(d)) is True


class TestIsTradingDay:
    def test_weekday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_trading_day("US", datetime.date(2024, 7, 8)) is True

    def test_saturday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_trading_day("US", datetime.date(2024, 7, 6)) is False

    def test_sunday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_trading_day("US", datetime.date(2024, 7, 7)) is False

    def test_holiday_weekday(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_trading_day("US", datetime.date(2024, 7, 4)) is False


class TestMarketHours:
    def test_normal_day(self) -> None:
        svc = MarketCalendarService()
        open_t, close_t = svc.market_hours("US", datetime.date(2024, 7, 8))
        assert open_t == datetime.time(9, 30)
        assert close_t == datetime.time(16, 0)

    def test_early_close(self) -> None:
        svc = MarketCalendarService(early_closes_us={"2024-11-29": "13:00"})
        open_t, close_t = svc.market_hours("US", datetime.date(2024, 11, 29))
        assert open_t == datetime.time(9, 30)
        assert close_t == datetime.time(13, 0)

    def test_early_close_default_fallback(self) -> None:
        # Day before a holiday triggers auto early-close
        svc = MarketCalendarService()
        # 2024-07-03 is the day before 2024-07-04 (US holiday)
        open_t, close_t = svc.market_hours("US", datetime.date(2024, 7, 3))
        assert open_t == datetime.time(9, 30)
        assert close_t == datetime.time(13, 0)

    def test_hk_hours(self) -> None:
        svc = MarketCalendarService()
        open_t, close_t = svc.market_hours("HK", datetime.date(2024, 7, 8))
        assert open_t == datetime.time(9, 30)
        assert close_t == datetime.time(16, 0)

    def test_redis_cache_hit(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get.return_value = "09:30:00,15:00:00"
        svc = MarketCalendarService(redis_client=mock_redis)
        open_t, close_t = svc.market_hours("US", datetime.date(2024, 7, 8))
        assert open_t == datetime.time(9, 30)
        assert close_t == datetime.time(15, 0)


class TestIsEarlyClose:
    def test_explicit_early_close(self) -> None:
        svc = MarketCalendarService(early_closes_us={"2024-11-29": "13:00"})
        assert svc.is_early_close("US", datetime.date(2024, 11, 29)) is True

    def test_day_before_holiday(self) -> None:
        svc = MarketCalendarService()
        # 2024-07-03 is the day before 2024-07-04 (US holiday)
        assert svc.is_early_close("US", datetime.date(2024, 7, 3)) is True

    def test_normal_day(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_early_close("US", datetime.date(2024, 7, 8)) is False

    def test_hk_no_auto_early_close(self) -> None:
        svc = MarketCalendarService()
        assert svc.is_early_close("HK", datetime.date(2024, 7, 3)) is False


class TestNextTradingDay:
    def test_next_day(self) -> None:
        svc = MarketCalendarService()
        # Monday -> Tuesday
        assert svc.next_trading_day("US", datetime.date(2024, 7, 8)) == datetime.date(
            2024, 7, 9
        )

    def test_friday_to_monday(self) -> None:
        svc = MarketCalendarService()
        assert svc.next_trading_day("US", datetime.date(2024, 7, 5)) == datetime.date(
            2024, 7, 8
        )

    def test_skips_holiday(self) -> None:
        svc = MarketCalendarService()
        # 2024-07-03 (Wed) -> next is 2024-07-05 (Fri) because 2024-07-04 is holiday
        assert svc.next_trading_day("US", datetime.date(2024, 7, 3)) == datetime.date(
            2024, 7, 5
        )

    def test_redis_cache_hit(self) -> None:
        mock_redis = MagicMock()
        mock_redis.get.return_value = "2024-07-10"
        svc = MarketCalendarService(redis_client=mock_redis)
        assert svc.next_trading_day("US", datetime.date(2024, 7, 8)) == datetime.date(
            2024, 7, 10
        )


class TestMarketTimezone:
    def test_us(self) -> None:
        svc = MarketCalendarService()
        assert svc.market_timezone("US") == "America/New_York"

    def test_hk(self) -> None:
        svc = MarketCalendarService()
        assert svc.market_timezone("HK") == "Asia/Hong_Kong"

    def test_invalid(self) -> None:
        svc = MarketCalendarService()
        with pytest.raises(ValueError, match="Unsupported market"):
            svc.market_timezone("EU")


class TestFromEnv:
    @patch.dict(
        "os.environ",
        {
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6379",
            "CUSTOM_HOLIDAYS_US": "2024-07-05",
            "EARLY_CLOSES_US": '{"2024-11-29": "13:00"}',
        },
        clear=False,
    )
    def test_from_env(self) -> None:
        svc = MarketCalendarService.from_env()
        assert "2024-07-05" in svc._custom_holidays_us
        assert svc._early_closes_us == {"2024-11-29": "13:00"}

    @patch.dict(
        "os.environ",
        {"REDIS_HOST": ""},
        clear=False,
    )
    def test_from_env_no_redis(self) -> None:
        svc = MarketCalendarService.from_env()
        assert svc._redis is None
