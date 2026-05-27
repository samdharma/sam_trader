"""ReadinessCheckerActor — SOD operational readiness check.

Triggered via ``LiveClock.set_time_alert()`` at the market's
``sod_readiness_time`` (e.g., 07:00 HKT for HK, 08:00 ET for US).

Runs 7 checks and writes a JSON result to Redis under the key
``sam:readiness:{market}:{date}`` with per-check pass/fail status.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId, Venue

from sam_trader.services.market_calendar import MarketCalendarService

_CHECK_NAMES: list[str] = [
    "broker_connectivity",
    "quote_flow",
    "instruments_resolved",
    "account_status",
    "bundles_loaded",
    "redis_pg_health",
    "calendar_trading_day",
]


class ReadinessCheckerActorConfig(ActorConfig, frozen=True):
    """Configuration for the ReadinessCheckerActor.

    Parameters
    ----------
    market : str
        Market code ("US" or "HK") for calendar lookup and Redis key.
    sod_readiness_time : str
        Local market time in HH:MM format (e.g. \"07:00\").
    session_timezone : str
        IANA timezone name (e.g. \"Asia/Hong_Kong\").
    redis_host : str
        Redis host for writing readiness results.
    redis_port : int
        Redis port.
    redis_password : str
        Redis password.
    futu_enabled : bool
        Whether Futu venue is expected to be connected.
    ib_enabled : bool
        Whether IBKR venue is expected to be connected.
    postgres_host : str
        PostgreSQL host for health check.
    postgres_port : int
        PostgreSQL port.
    postgres_db : str
        PostgreSQL database name.
    postgres_user : str
        PostgreSQL user.
    postgres_password : str
        PostgreSQL password.
    instrument_ids : list[str] | None
        Instrument IDs to subscribe to for quote-flow tracking.
    bundle_count : int
        Number of bundles loaded (injected from main.py).
    market_calendar_enabled : bool
        Master switch for calendar integration.

    """

    market: str = ""
    sod_readiness_time: str = "08:00"
    session_timezone: str = "America/New_York"
    redis_host: str = ""
    redis_port: int = 6379
    redis_password: str = ""
    futu_enabled: bool = False
    ib_enabled: bool = False
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_db: str = ""
    postgres_user: str = ""
    postgres_password: str = ""
    instrument_ids: list[str] | None = None
    bundle_count: int = 0
    market_calendar_enabled: bool = True


class ReadinessCheckerActor(Actor):
    """Actor that runs a start-of-day (SOD) operational readiness check.

    Checks broker connectivity, quote data flow, instrument resolution,
    account status, bundle loading, infra health (Redis/PG), and
    calendar trading-day confirmation.

    Results are written as JSON to Redis under
    ``sam:readiness:{market}:{date}``.

    Parameters
    ----------
    config : ReadinessCheckerActorConfig
        Actor configuration.

    """

    _PASS = "PASS"
    _FAIL = "FAIL"
    _SKIP = "SKIP"

    def __init__(self, config: ReadinessCheckerActorConfig):
        super().__init__(config)
        self._timer_name = "sod_readiness_check"
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._calendar: MarketCalendarService | None = None
        self._last_quote_times: dict[str, datetime] = {}
        self._quote_stale_threshold: int = 300  # seconds

    # ── Lifecycle ──────────────────────────────────────────────────

    def on_start(self) -> None:
        """Schedule the first readiness check and subscribe to quote ticks."""
        # Capture event loop for async Redis/PG checks via the callback.
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
            self.log.warning(
                "ReadinessCheckerActor: unable to capture event loop — "
                "Redis/PG checks will be skipped"
            )

        if self.config.market and self.config.market_calendar_enabled:
            self._calendar = MarketCalendarService()

        # Subscribe to quote ticks so we can determine data-flow freshness.
        for ins_str in self.config.instrument_ids or []:
            try:
                ins_id = InstrumentId.from_str(ins_str)
                self.subscribe_quote_ticks(instrument_id=ins_id)
                self.log.info(
                    f"ReadinessCheckerActor: subscribed to quotes for {ins_str}"
                )
            except Exception as exc:  # noqa: BLE001
                self.log.error(
                    f"ReadinessCheckerActor: failed to subscribe to quotes "
                    f"for '{ins_str}': {exc}"
                )

        self._schedule_next_check()

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Track the last quote tick time per instrument."""
        instrument_id = str(tick.instrument_id)
        self._last_quote_times[instrument_id] = self.clock.utc_now()

    def on_stop(self) -> None:
        """Cancel scheduled alerts when the actor stops."""
        self.clock.cancel_timers()

    # ── Scheduling ─────────────────────────────────────────────────

    def _compute_next_alert_utc(self) -> datetime:
        """Compute the next occurrence of ``sod_readiness_time`` in UTC.

        If the local readiness time today is already in the past,
        returns tomorrow's occurrence.
        """
        tz = ZoneInfo(self.config.session_timezone)
        now_utc = self.clock.utc_now()
        now_local = now_utc.astimezone(tz)

        # Parse HH:MM
        hour, minute = map(int, self.config.sod_readiness_time.split(":"))

        # Construct the local time for today's readiness check
        target_local = now_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

        # If today's target is already past, move to tomorrow
        if target_local <= now_local:
            target_local += timedelta(days=1)

        # Convert to UTC for LiveClock.set_time_alert
        target_utc: datetime = target_local.astimezone(ZoneInfo("UTC"))
        return target_utc.replace(tzinfo=None)  # Naive UTC for set_time_alert

    def _schedule_next_check(self) -> None:
        """Schedule the next readiness check via ``set_time_alert``."""
        next_utc = self._compute_next_alert_utc()
        self.clock.set_time_alert(
            self._timer_name,
            next_utc,
            self._on_sod_readiness_check,
            override=True,
        )
        self.log.info(
            f"ReadinessCheckerActor: next check at " f"{next_utc.isoformat()} UTC"
        )

    # ── Readiness Check Entry Point ─────────────────────────────────

    def _on_sod_readiness_check(
        self, alert: UUID4 | None = None
    ) -> None:  # noqa: ARG002
        """Callback executed at ``sod_readiness_time`` daily.

        Offloads the full check to an async task and re-schedules
        for the next day.
        """
        self.log.info(
            f"ReadinessCheckerActor: SOD readiness check "
            f"triggered for market={self.config.market or 'N/A'}"
        )
        if self._main_loop is not None:
            self._main_loop.create_task(self._run_readiness_check())
        else:
            self.log.error(
                "ReadinessCheckerActor: no event loop — readiness check "
                "cannot run async checks"
            )
        # Re-schedule for next day
        self._schedule_next_check()

    # ── Async Readiness Orchestrator ───────────────────────────────

    async def _run_readiness_check(self) -> None:
        """Orchestrate all 7 checks and write the combined result to Redis."""
        now_utc = self.clock.utc_now()
        tz = ZoneInfo(self.config.session_timezone)
        now_local = now_utc.astimezone(tz)
        today = now_local.date()

        results: dict[str, str] = {}
        details: dict[str, str] = {}

        # 1. Broker connectivity
        results["broker_connectivity"], details["broker_connectivity"] = (
            self._check_broker_connectivity()
        )

        # 2. QuoteTick flowing
        results["quote_flow"], details["quote_flow"] = self._check_quote_flow(now_utc)

        # 3. Instruments resolved
        results["instruments_resolved"], details["instruments_resolved"] = (
            self._check_instruments_resolved()
        )

        # 4. Account status
        results["account_status"], details["account_status"] = (
            self._check_account_status()
        )

        # 5. Bundles loaded
        results["bundles_loaded"], details["bundles_loaded"] = (
            self._check_bundles_loaded()
        )

        # 6. Redis/PG health (async)
        results["redis_pg_health"], details["redis_pg_health"] = (
            await self._check_redis_pg_health()
        )

        # 7. Calendar trading day
        results["calendar_trading_day"], details["calendar_trading_day"] = (
            self._check_calendar_trading_day(now_local)
        )

        # Build and write the report
        report = {
            "market": self.config.market,
            "date": today.isoformat(),
            "timestamp_utc": now_utc.isoformat(),
            "overall": (
                self._PASS
                if all(v != self._FAIL for v in results.values())
                else self._FAIL
            ),
            "checks": [
                {"name": name, "result": results[name], "detail": details.get(name, "")}
                for name in _CHECK_NAMES
            ],
        }

        self._log_readiness_report(report)
        await self._write_readiness_to_redis(today, report)

    # ── Check 1: Broker Connectivity ───────────────────────────────

    def _check_broker_connectivity(self) -> tuple[str, str]:
        """Check that expected brokers have registered accounts in cache."""
        issues: list[str] = []

        if self.config.futu_enabled:
            acc = self.cache.account_for_venue(Venue("FUTU"))
            if acc is None:
                issues.append("FUTU: no account in cache")
            else:
                self.log.debug("ReadinessCheck: FUTU account present")

        if self.config.ib_enabled:
            acc = self.cache.account_for_venue(Venue("IB"))
            if acc is None:
                issues.append("IB: no account in cache")
            else:
                self.log.debug("ReadinessCheck: IB account present")

        if not issues:
            return self._PASS, "all expected brokers connected"
        return self._FAIL, "; ".join(issues)

    # ── Check 2: QuoteTick Flow ────────────────────────────────────

    def _check_quote_flow(self, now_utc: datetime) -> tuple[str, str]:
        """Check that at least one quote tick was received recently."""
        if not self._last_quote_times:
            if not self.config.instrument_ids:
                return self._SKIP, "no instruments subscribed to quote ticks"
            return self._FAIL, "no quote ticks received yet"

        max_age_s = max(
            (now_utc - ts).total_seconds() for ts in self._last_quote_times.values()
        )
        fresh = [
            ins_id
            for ins_id, ts in self._last_quote_times.items()
            if (now_utc - ts).total_seconds() <= self._quote_stale_threshold
        ]

        if not fresh:
            return self._FAIL, (
                f"all quote ticks stale (max age {max_age_s:.0f}s, "
                f"threshold {self._quote_stale_threshold}s)"
            )
        if len(fresh) < len(self._last_quote_times):
            return self._PASS, (
                f"{len(fresh)}/{len(self._last_quote_times)} instruments fresh "
                f"(max age {max_age_s:.0f}s)"
            )
        return self._PASS, f"all {len(fresh)} instruments fresh"

    # ── Check 3: Instruments Resolved ──────────────────────────────

    def _check_instruments_resolved(self) -> tuple[str, str]:
        """Check that configured instrument IDs are resolved in cache."""
        expected = self.config.instrument_ids or []
        if not expected:
            return self._SKIP, "no instrument_ids configured"

        resolved_ids = {str(iid) for iid in self.cache.instrument_ids()}

        missing = [ins for ins in expected if ins not in resolved_ids]
        if missing:
            return self._FAIL, f"unresolved instruments: {', '.join(missing)}"
        return self._PASS, f"all {len(expected)} instruments resolved"

    # ── Check 4: Account Status ────────────────────────────────────

    def _check_account_status(self) -> tuple[str, str]:
        """Check account margin / buying power for enabled brokers."""
        issues: list[str] = []

        for venue_name, enabled in (
            ("FUTU", self.config.futu_enabled),
            ("IB", self.config.ib_enabled),
        ):
            if not enabled:
                continue
            acc = self.cache.account_for_venue(Venue(venue_name))
            if acc is None:
                issues.append(f"{venue_name}: no account")
                continue
            # Verify margin info is populated
            try:
                balances = acc.balances() if hasattr(acc, "balances") else None
                if balances is None:
                    issues.append(f"{venue_name}: no balance data")
            except Exception:  # noqa: BLE001
                issues.append(f"{venue_name}: balance read failed")

        if not issues:
            return self._PASS, "account balances available"
        return self._FAIL, "; ".join(issues)

    # ── Check 5: Bundles Loaded ────────────────────────────────────

    def _check_bundles_loaded(self) -> tuple[str, str]:
        """Verify that strategy bundles were loaded at boot."""
        count = self.config.bundle_count
        if count == 0:
            return self._FAIL, "zero bundles loaded — check bundles.yaml"
        return self._PASS, f"{count} bundle(s) loaded"

    # ── Check 6: Redis / PG Health ─────────────────────────────────

    async def _check_redis_pg_health(self) -> tuple[str, str]:
        """Ping Redis and PostgreSQL to confirm infra is healthy."""
        issues: list[str] = []

        # Redis
        if self.config.redis_host:
            try:
                import redis.asyncio as aioredis

                r = aioredis.Redis(
                    host=self.config.redis_host,
                    port=self.config.redis_port,
                    password=self.config.redis_password or None,
                    socket_connect_timeout=5,
                )
                await r.ping()  # type: ignore[misc]
                await r.aclose()
            except Exception as exc:  # noqa: BLE001
                issues.append(f"Redis: {exc}")
        else:
            issues.append("Redis: host not configured")

        # PostgreSQL
        if self.config.postgres_host:
            try:
                conn = await asyncpg.connect(
                    host=self.config.postgres_host,
                    port=self.config.postgres_port,
                    database=self.config.postgres_db,
                    user=self.config.postgres_user,
                    password=self.config.postgres_password,
                    timeout=5,
                )
                await conn.execute("SELECT 1")
                await conn.close()
            except Exception as exc:  # noqa: BLE001
                issues.append(f"PostgreSQL: {exc}")
        else:
            issues.append("PostgreSQL: host not configured")

        if not issues:
            return self._PASS, "Redis + PG both healthy"
        return self._FAIL, "; ".join(issues)

    # ── Check 7: Calendar Trading Day ──────────────────────────────

    def _check_calendar_trading_day(self, now_local: datetime) -> tuple[str, str]:
        """Confirm today is a trading day via the market calendar."""
        today = now_local.date()

        if not self.config.market or not self.config.market_calendar_enabled:
            return self._SKIP, "calendar check disabled"

        if self._calendar is None:
            return self._SKIP, "calendar service not initialized"

        try:
            is_trading = self._calendar.is_trading_day(self.config.market, today)
            if not is_trading:
                holiday = self._calendar.holiday_name(self.config.market, today)
                reason = f"{today.isoformat()} is a holiday" + (
                    f" ({holiday})" if holiday else ""
                )
                return self._FAIL, reason
            return self._PASS, f"{today.isoformat()} is a trading day"
        except Exception as exc:  # noqa: BLE001
            return self._FAIL, f"calendar lookup failed: {exc}"

    # ── Reporting ──────────────────────────────────────────────────

    def _log_readiness_report(self, report: dict[str, Any]) -> None:
        """Log the readiness report."""
        overall = report["overall"]
        lines = [f"SOD Readiness Check [{report['market']}] {report['date']}:"]
        for check in report["checks"]:
            flag = (
                "✓"
                if check["result"] == self._PASS
                else "✗" if check["result"] == self._FAIL else "→"
            )
            lines.append(f"  {flag} {check['name']}: {check['detail']}")
        msg = "\n".join(lines)
        if overall != self._PASS:
            self.log.warning(msg)
        else:
            self.log.info(msg)

    async def _write_readiness_to_redis(
        self, today: date, report: dict[str, Any]
    ) -> None:
        """Persist the readiness report to Redis with a 48-hour TTL."""
        if not self.config.redis_host:
            self.log.debug(
                "ReadinessCheckerActor: Redis host not configured — skipping write"
            )
            return
        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                socket_connect_timeout=5,
            )
            key = f"sam:readiness:{self.config.market}:{today.isoformat()}"
            await r.setex(key, 172800, json.dumps(report, indent=2))
            await r.aclose()
            self.log.info(f"ReadinessCheckerActor: report written to Redis key={key}")
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"ReadinessCheckerActor: Redis write failed: {exc}")
