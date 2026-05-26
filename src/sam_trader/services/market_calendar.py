"""Market calendar service for US (NYSE/NASDAQ) and HK (HKEX).

Provides trading-day, holiday, market-hours, and early-close awareness
with optional Redis caching (TTL 24h). Falls back to hardcoded 2024-2028
holidays when the ``holidays`` library is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, time, timedelta
from typing import Any, Final

# Optional holidays package -------------------------------------------------
try:
    import holidays

    _HAS_HOLIDAYS = True
except ImportError:
    _HAS_HOLIDAYS = False

# Optional redis package ----------------------------------------------------
try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment, misc]

logger = logging.getLogger("sam_trader.market_calendar")

# Hardcoded fallback holidays (2024-2028) -----------------------------------
_HARDCODED_HOLIDAYS_US: Final[set[str]] = {
    # 2024
    "2024-01-01",
    "2024-01-15",
    "2024-02-19",
    "2024-03-29",
    "2024-05-27",
    "2024-06-19",
    "2024-07-04",
    "2024-09-02",
    "2024-11-28",
    "2024-12-25",
    # 2025
    "2025-01-01",
    "2025-01-20",
    "2025-02-17",
    "2025-04-18",
    "2025-05-26",
    "2025-06-19",
    "2025-07-04",
    "2025-09-01",
    "2025-11-27",
    "2025-12-25",
    # 2026
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25",
    # 2027
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-06-18",
    "2027-07-05",
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",
    # 2028
    "2028-01-17",
    "2028-02-21",
    "2028-04-07",
    "2028-05-29",
    "2028-06-19",
    "2028-07-04",
    "2028-09-04",
    "2028-11-23",
    "2028-12-25",
}

_HARDCODED_HOLIDAYS_HK: Final[set[str]] = {
    # 2024
    "2024-01-01",
    "2024-02-10",
    "2024-02-12",
    "2024-02-13",
    "2024-03-29",
    "2024-04-01",
    "2024-04-04",
    "2024-05-01",
    "2024-05-15",
    "2024-06-10",
    "2024-07-01",
    "2024-09-18",
    "2024-10-01",
    "2024-10-11",
    "2024-12-25",
    "2024-12-26",
    # 2025
    "2025-01-01",
    "2025-01-29",
    "2025-01-30",
    "2025-01-31",
    "2025-04-04",
    "2025-04-07",
    "2025-04-18",
    "2025-05-01",
    "2025-05-05",
    "2025-05-31",
    "2025-07-01",
    "2025-10-01",
    "2025-10-07",
    "2025-10-29",
    "2025-12-25",
    # 2026
    "2026-01-01",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-04-03",
    "2026-04-06",
    "2026-05-01",
    "2026-06-19",
    "2026-07-01",
    "2026-09-21",
    "2026-10-01",
    "2026-10-02",
    "2026-10-19",
    "2026-12-25",
    "2026-12-26",
    # 2027
    "2027-01-01",
    "2027-02-06",
    "2027-02-08",
    "2027-02-09",
    "2027-03-26",
    "2027-04-05",
    "2027-05-01",
    "2027-06-18",
    "2027-07-01",
    "2027-09-22",
    "2027-10-01",
    "2027-10-18",
    "2027-12-27",
    # 2028
    "2028-01-01",
    "2028-01-26",
    "2028-01-27",
    "2028-01-28",
    "2028-04-14",
    "2028-04-17",
    "2028-05-01",
    "2028-06-19",
    "2028-07-01",
    "2028-09-21",
    "2028-10-02",
    "2028-10-23",
    "2028-12-25",
    "2028-12-26",
}

_DEFAULT_MARKET_HOURS: Final[dict[str, tuple[time, time]]] = {
    "US": (time(9, 30), time(16, 0)),
    "HK": (time(9, 30), time(16, 0)),
}

_MARKET_TIMEZONE: Final[dict[str, str]] = {
    "US": "America/New_York",
    "HK": "Asia/Hong_Kong",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_date_set(value: str) -> set[str]:
    """Parse a comma-separated list of ISO dates."""
    return {d.strip() for d in value.split(",") if d.strip()}


def _parse_date_dict(value: str) -> dict[str, str]:
    """Parse a JSON dict of ``date -> time`` strings."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        logger.warning(
            "MarketCalendarService: invalid JSON for early closes: %s",
            value,
        )
    return {}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class MarketCalendarService:
    """Centralized market calendar for US (NYSE/NASDAQ) and HK (HKEX).

    Uses the ``holidays`` library when available, with a hardcoded fallback
    for 2024-2028. Results are optionally cached in Redis with a 24-hour TTL.

    Parameters
    ----------
    redis_client : redis.Redis | None
        Optional synchronous Redis client for result caching.
    custom_holidays_us : set[str] | None
        Additional US holidays as ISO date strings (YYYY-MM-DD).
    custom_holidays_hk : set[str] | None
        Additional HK holidays as ISO date strings (YYYY-MM-DD).
    early_closes_us : dict[str, str] | None
        US early-close overrides: ``{"YYYY-MM-DD": "HH:MM"}``.
    early_closes_hk : dict[str, str] | None
        HK early-close overrides: ``{"YYYY-MM-DD": "HH:MM"}``.
    """

    _REDIS_TTL: Final[int] = 86400
    _VALID_MARKETS: Final[set[str]] = {"US", "HK"}

    def __init__(
        self,
        redis_client: Any | None = None,
        custom_holidays_us: set[str] | None = None,
        custom_holidays_hk: set[str] | None = None,
        early_closes_us: dict[str, str] | None = None,
        early_closes_hk: dict[str, str] | None = None,
    ):
        self._redis = redis_client
        self._custom_holidays_us = custom_holidays_us or set()
        self._custom_holidays_hk = custom_holidays_hk or set()
        self._early_closes_us = early_closes_us or {}
        self._early_closes_hk = early_closes_hk or {}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> MarketCalendarService:
        """Create a service instance from environment variables.

        Reads:
        - ``REDIS_HOST``, ``REDIS_PORT``, ``REDIS_PASSWORD``
        - ``CUSTOM_HOLIDAYS_US``, ``CUSTOM_HOLIDAYS_HK`` (comma-separated dates)
        - ``EARLY_CLOSES_US``, ``EARLY_CLOSES_HK`` (JSON dicts)
        """
        redis_client = None
        redis_host = os.getenv("REDIS_HOST", "")
        if redis_host and redis is not None:
            try:
                redis_client = redis.Redis(
                    host=redis_host,
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD") or None,
                    decode_responses=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MarketCalendarService: Redis connect failed: %s",
                    exc,
                )

        custom_us = _parse_date_set(os.getenv("CUSTOM_HOLIDAYS_US", ""))
        custom_hk = _parse_date_set(os.getenv("CUSTOM_HOLIDAYS_HK", ""))
        early_us = _parse_date_dict(os.getenv("EARLY_CLOSES_US", ""))
        early_hk = _parse_date_dict(os.getenv("EARLY_CLOSES_HK", ""))

        return cls(
            redis_client=redis_client,
            custom_holidays_us=custom_us,
            custom_holidays_hk=custom_hk,
            early_closes_us=early_us,
            early_closes_hk=early_hk,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_trading_day(self, market: str, date_val: date) -> bool:
        """Return ``True`` if *date_val* is a trading day for *market*."""
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")
        if date_val.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        return not self.is_holiday(market, date_val)

    def is_holiday(self, market: str, date_val: date) -> bool:
        """Return ``True`` if *date_val* is a holiday for *market*."""
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")

        cache_key = f"sam:calendar:holiday:{market}:{date_val.isoformat()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached == "1"

        result = self._is_holiday_uncached(market, date_val)
        self._cache_set(cache_key, "1" if result else "0")
        return result

    def market_hours(self, market: str, date_val: date) -> tuple[time, time]:
        """Return ``(open_time, close_time)`` for *market* on *date_val*.

        Accounts for early-close days configured via env vars.
        """
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")

        cache_key = f"sam:calendar:hours:{market}:{date_val.isoformat()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            open_str, close_str = cached.split(",")
            return time.fromisoformat(open_str), time.fromisoformat(close_str)

        if self.is_early_close(market, date_val):
            early_map = (
                self._early_closes_us if market == "US" else self._early_closes_hk
            )
            close_time = time.fromisoformat(
                early_map.get(date_val.isoformat(), "13:00")
            )
        else:
            close_time = _DEFAULT_MARKET_HOURS[market][1]
        open_time = _DEFAULT_MARKET_HOURS[market][0]

        self._cache_set(
            cache_key,
            f"{open_time.isoformat()},{close_time.isoformat()}",
        )
        return open_time, close_time

    def next_trading_day(self, market: str, date_val: date) -> date:
        """Return the next trading day for *market* strictly after *date_val*."""
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")

        cache_key = f"sam:calendar:next:{market}:{date_val.isoformat()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return date.fromisoformat(cached)

        candidate = date_val + timedelta(days=1)
        while not self.is_trading_day(market, candidate):
            candidate += timedelta(days=1)

        self._cache_set(cache_key, candidate.isoformat())
        return candidate

    def is_early_close(self, market: str, date_val: date) -> bool:
        """Return ``True`` if *date_val* is an early-close day for *market*."""
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")

        cache_key = f"sam:calendar:early:{market}:{date_val.isoformat()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached == "1"

        early_map = self._early_closes_us if market == "US" else self._early_closes_hk
        result = date_val.isoformat() in early_map

        # The day before a major US holiday is typically an early-close day.
        if market == "US" and not result:
            next_day = date_val + timedelta(days=1)
            if next_day.weekday() < 5 and self._is_holiday_uncached("US", next_day):
                result = True

        self._cache_set(cache_key, "1" if result else "0")
        return result

    def market_timezone(self, market: str) -> str:
        """Return the IANA timezone name for *market*."""
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")
        return _MARKET_TIMEZONE[market]

    def holiday_name(self, market: str, date_val: date) -> str | None:
        """Return the holiday name for *date_val* in *market*, or ``None``."""
        market = market.upper()
        if market not in self._VALID_MARKETS:
            raise ValueError(f"Unsupported market: {market}")
        if date_val.weekday() >= 5:
            return None
        return self._holiday_name_uncached(market, date_val)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _holiday_name_uncached(self, market: str, date_val: date) -> str | None:
        date_str = date_val.isoformat()
        if market == "US":
            if date_str in self._custom_holidays_us:
                return "Custom Holiday"
            if _HAS_HOLIDAYS:
                us_hols = holidays.US(years=date_val.year)  # type: ignore[attr-defined]
                if date_val in us_hols:
                    return str(us_hols[date_val])
            if date_str in _HARDCODED_HOLIDAYS_US:
                return "US Holiday"
        elif market == "HK":
            if date_str in self._custom_holidays_hk:
                return "Custom Holiday"
            if _HAS_HOLIDAYS:
                hk_hols = holidays.HK(years=date_val.year)  # type: ignore[attr-defined]
                if date_val in hk_hols:
                    return str(hk_hols[date_val])
            if date_str in _HARDCODED_HOLIDAYS_HK:
                return "HK Holiday"
        return None

    def _is_holiday_uncached(self, market: str, date_val: date) -> bool:
        date_str = date_val.isoformat()
        if market == "US":
            if date_str in self._custom_holidays_us:
                return True
            if _HAS_HOLIDAYS:
                us_hols = holidays.US(years=date_val.year)  # type: ignore[attr-defined]
                if date_val in us_hols:
                    return True
            if date_str in _HARDCODED_HOLIDAYS_US:
                return True
        elif market == "HK":
            if date_str in self._custom_holidays_hk:
                return True
            if _HAS_HOLIDAYS:
                hk_hols = holidays.HK(years=date_val.year)  # type: ignore[attr-defined]
                if date_val in hk_hols:
                    return True
            if date_str in _HARDCODED_HOLIDAYS_HK:
                return True
        return False

    def _cache_get(self, key: str) -> str | None:
        if self._redis is None:
            return None
        try:
            val = self._redis.get(key)
            if val is None:
                return None
            return str(val)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MarketCalendarService: cache get failed: %s", exc)
            return None

    def _cache_set(self, key: str, value: str) -> None:
        if self._redis is None:
            return
        try:
            self._redis.setex(key, self._REDIS_TTL, value)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MarketCalendarService: cache set failed: %s", exc)
