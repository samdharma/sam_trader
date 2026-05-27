"""EndOfDayReporterActor — EOD aggregated report generator.

Triggered via ``LiveClock.set_time_alert()`` at the market's
``eod_report_time`` (e.g., 16:05 local — 5 minutes after market close).

Generates a 6-section aggregated report and persists it to:

- Redis: ``sam:eod_report:{market}:{date}`` (JSON)
- PostgreSQL: ``daily_reports`` table

Sections
--------
1. Daily P&L per strategy — Redis ``sam:pnl:{strategy}:{date}`` keys
   (written by RealizedPnLTrackerActor). Falls back to PG fills.
2. Total fills + commissions — PG ``fills`` table, grouped by strategy.
3. Max drawdown — from Redis P&L time series or PG fill data.
4. Position summary — zero open positions expected at EOD.
5. Rejection events — RejectionMonitorActor circuit breaker state (Redis).
6. Health events — HealthMonitorActor heartbeat log (Redis).
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

from sam_trader.services.market_calendar import MarketCalendarService

# ── Config ─────────────────────────────────────────────────────────


class EndOfDayReporterActorConfig(ActorConfig, frozen=True):
    """Configuration for the EndOfDayReporterActor.

    Parameters
    ----------
    market : str
        Market code (``"US"`` or ``"HK"``) for calendar lookup and Redis key.
    eod_report_time : str
        Local market time in HH:MM format (e.g. ``"16:05"``).
    session_timezone : str
        IANA timezone name (e.g. ``"America/New_York"``).
    redis_host : str
        Redis host for reading P&L data and writing the report.
    redis_port : int
        Redis port.
    redis_password : str
        Redis password.
    postgres_host : str
        PostgreSQL host for querying fills.
    postgres_port : int
        PostgreSQL port.
    postgres_db : str
        PostgreSQL database name.
    postgres_user : str
        PostgreSQL user.
    postgres_password : str
        PostgreSQL password.
    market_calendar_enabled : bool
        Master switch for calendar integration. When enabled, skips EOD
        reporting on non-trading days.

    """

    market: str = ""
    eod_report_time: str = "16:05"
    session_timezone: str = "America/New_York"
    redis_host: str = ""
    redis_port: int = 6379
    redis_password: str = ""
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_db: str = ""
    postgres_user: str = ""
    postgres_password: str = ""
    market_calendar_enabled: bool = True


# ── Report Generation ──────────────────────────────────────────────

# Redis key prefixes used by other actors.
_PNL_KEY_PREFIX = "sam:pnl"
_HEARTBEAT_KEY_PREFIX = "sam:heartbeat"
_REJECTION_KEY_PREFIX = "sam:rejection"

# Designated key for the EOD report in Redis.
_EOD_KEY_TEMPLATE = "sam:eod_report:{market}:{date}"


class EndOfDayReporterActor(Actor):
    """Actor that generates an end-of-day aggregated report.

    Scheduled at ``eod_report_time`` daily. Aggregates data from Redis
    (realized P&L, rejection state, heartbeats) and PostgreSQL (fills),
    then writes a structured JSON report.

    Parameters
    ----------
    config : EndOfDayReporterActorConfig
        Actor configuration.

    """

    def __init__(self, config: EndOfDayReporterActorConfig):
        super().__init__(config)
        self._alert_name = "eod_report"
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._calendar: MarketCalendarService | None = None

    # ── Lifecycle ──────────────────────────────────────────────────

    def on_start(self) -> None:
        """Schedule the EOD time alert and initialise calendar service."""
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None
            self.log.warning(
                "EndOfDayReporterActor: unable to capture event loop — "
                "report generation will be skipped"
            )

        if self.config.market and self.config.market_calendar_enabled:
            self._calendar = MarketCalendarService()

        self._schedule_next_report()

    def on_stop(self) -> None:
        """Cancel all scheduled alerts when the actor stops."""
        self.clock.cancel_timers()

    # ── Scheduling ─────────────────────────────────────────────────

    def _compute_next_alert_utc(self) -> datetime:
        """Compute the next occurrence of ``eod_report_time`` in UTC."""
        tz = ZoneInfo(self.config.session_timezone)
        now_utc = self.clock.utc_now()
        now_local = now_utc.astimezone(tz)

        hour, minute = map(int, self.config.eod_report_time.split(":"))
        target_local = now_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )

        if target_local <= now_local:
            target_local += timedelta(days=1)

        target_utc: datetime = target_local.astimezone(ZoneInfo("UTC"))
        return target_utc.replace(tzinfo=None)

    def _schedule_next_report(self) -> None:
        """Schedule the next EOD report via ``set_time_alert``."""
        next_utc = self._compute_next_alert_utc()
        self.clock.set_time_alert(
            self._alert_name,
            next_utc,
            self._on_eod_report,
            override=True,
        )
        self.log.info(
            f"EndOfDayReporterActor: next report at {next_utc.isoformat()} UTC"
        )

    # ── Alert Callback ─────────────────────────────────────────────

    def _on_eod_report(self, alert: UUID4 | None = None) -> None:  # noqa: ARG002
        """Callback executed at ``eod_report_time`` daily.

        Offloads the actual report generation to an async task and
        re-schedules for the next day.
        """
        self.log.info(
            f"EndOfDayReporterActor: EOD report triggered "
            f"for market={self.config.market or 'N/A'}"
        )

        if self._main_loop is not None:
            self._main_loop.create_task(self._generate_eod_report())
        else:
            self.log.error(
                "EndOfDayReporterActor: no event loop — " "report generation skipped"
            )

        self._schedule_next_report()

    # ── Report Orchestrator ────────────────────────────────────────

    async def _generate_eod_report(self) -> None:
        """Generate all 6 sections and persist the EOD report."""
        now_utc = self.clock.utc_now()
        tz = ZoneInfo(self.config.session_timezone)
        now_local = now_utc.astimezone(tz)
        today = now_local.date()

        # Skip on non-trading days.
        if not self._is_trading_day(now_local):
            self.log.info(
                f"EndOfDayReporterActor: {today.isoformat()} is not a "
                f"trading day — skipping EOD report"
            )
            return

        self.log.info(
            f"EndOfDayReporterActor: generating EOD report "
            f"for {self.config.market} {today.isoformat()}"
        )

        report: dict[str, Any] = {
            "market": self.config.market,
            "date": today.isoformat(),
            "generated_at_utc": now_utc.isoformat(),
        }

        # Section 1: Daily P&L per strategy
        report["daily_pnl"] = await self._section_daily_pnl(today)

        # Section 2: Total fills + commissions
        report["fills_summary"] = await self._section_fills(today)

        # Section 3: Max drawdown
        report["max_drawdown"] = await self._section_max_drawdown(today)

        # Section 4: Position summary
        report["position_summary"] = self._section_positions()

        # Section 5: Rejection events
        report["rejection_events"] = await self._section_rejections(today)

        # Section 6: Health events
        report["health_events"] = await self._section_health(today)

        # Persist.
        await self._write_report_to_redis(today, report)
        await self._write_report_to_pg(today, report)

        self._log_report_summary(report)

    # ── Trading Day Check ─────────────────────────────────────────

    def _is_trading_day(self, now_local: datetime) -> bool:
        """Check if today is a trading day for the configured market."""
        if not self.config.market or not self.config.market_calendar_enabled:
            return True  # Always report when calendar is disabled.
        if self._calendar is None:
            return True
        try:
            return self._calendar.is_trading_day(self.config.market, now_local.date())
        except Exception:
            return True  # Err on the side of generating a report.

    # ── Section 1: Daily P&L ───────────────────────────────────────

    async def _section_daily_pnl(self, today: date) -> list[dict[str, Any]]:
        """Collect daily realized P&L per strategy.

        Reads ``sam:pnl:{strategy_id}:{date}`` keys from Redis
        (published by RealizedPnLTrackerActor). Falls back to computing
        rough P&L from the PG fills table if Redis data is unavailable.
        """
        pnl_entries: list[dict[str, Any]] = []
        strategies_from_redis: set[str] = set()

        if self.config.redis_host:
            try:
                import redis.asyncio as aioredis

                r = aioredis.Redis(
                    host=self.config.redis_host,
                    port=self.config.redis_port,
                    password=self.config.redis_password or None,
                    socket_connect_timeout=5,
                )

                # Scan for sam:pnl:*:{date} keys.
                date_str = today.isoformat()
                pattern = f"{_PNL_KEY_PREFIX}:*:{date_str}"
                cursor = 0
                while True:
                    cursor, keys = await r.scan(cursor, match=pattern, count=100)
                    for key in keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        # Extract strategy_id from key: sam:pnl:{strategy_id}:{date}
                        parts = key_str.rsplit(":", 1)
                        strategy_part = (
                            parts[0].removeprefix(f"{_PNL_KEY_PREFIX}:")
                            if len(parts) == 2
                            else ""
                        )
                        strategies_from_redis.add(strategy_part)

                        raw = await r.get(key_str)
                        if raw is not None:
                            try:
                                val = float(
                                    raw.decode() if isinstance(raw, bytes) else raw
                                )
                            except (ValueError, TypeError):
                                val = None
                            pnl_entries.append(
                                {
                                    "strategy_id": strategy_part,
                                    "realized_pnl": val,
                                    "source": "redis",
                                }
                            )
                    if cursor == 0:
                        break

                await r.aclose()
            except Exception as exc:  # noqa: BLE001
                self.log.warning(f"EndOfDayReporterActor: Redis P&L read failed: {exc}")

        # Fallback: compute P&L from PG fills for strategies not found in Redis.
        if self.config.postgres_host:
            pg_entries = await self._pnl_from_fills(today, strategies_from_redis)
            pnl_entries.extend(pg_entries)

        return pnl_entries

    async def _pnl_from_fills(
        self, today: date, skip_strategies: set[str]
    ) -> list[dict[str, Any]]:
        """Estimate realized P&L from the PG fills table for today.

        This is a rough estimate: (sum(sell_qty * sell_price) -
        sum(buy_qty * buy_price)) per strategy.
        """
        entries: list[dict[str, Any]] = []
        try:
            conn = await asyncpg.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                database=self.config.postgres_db,
                user=self.config.postgres_user,
                password=self.config.postgres_password,
                timeout=10,
            )
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                        strategy_id,
                        SUM(
                            CASE WHEN side = 'SELL'
                                THEN qty * price
                                ELSE -qty * price
                            END
                        ) AS estimated_pnl,
                        SUM(qty) FILTER (WHERE side = 'BUY')  AS buy_qty,
                        SUM(qty) FILTER (WHERE side = 'SELL') AS sell_qty,
                        COUNT(*) AS fill_count
                    FROM fills
                    WHERE ts_event::date = $1
                    GROUP BY strategy_id
                    """,
                    today,
                )
                for row in rows:
                    sid = row["strategy_id"]
                    if sid in skip_strategies:
                        continue
                    entries.append(
                        {
                            "strategy_id": sid,
                            "realized_pnl": (
                                float(row["estimated_pnl"])
                                if row["estimated_pnl"] is not None
                                else None
                            ),
                            "buy_qty": float(row["buy_qty"] or 0),
                            "sell_qty": float(row["sell_qty"] or 0),
                            "fill_count": row["fill_count"],
                            "source": "postgres",
                        }
                    )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"EndOfDayReporterActor: PG P&L query failed: {exc}")

        return entries

    # ── Section 2: Fills + Commissions ─────────────────────────────

    async def _section_fills(self, today: date) -> dict[str, Any]:
        """Aggregate total fills and commissions from PG.

        Returns summary with per-strategy breakdown and grand totals.
        """
        result: dict[str, Any] = {
            "total_fills": 0,
            "total_commission": 0.0,
            "total_volume": 0.0,
            "by_strategy": [],
        }

        if not self.config.postgres_host:
            result["status"] = "skipped — postgres not configured"
            return result

        try:
            conn = await asyncpg.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                database=self.config.postgres_db,
                user=self.config.postgres_user,
                password=self.config.postgres_password,
                timeout=10,
            )
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                        strategy_id,
                        COUNT(*)               AS fill_count,
                        SUM(qty)               AS total_qty,
                        SUM(commission)        AS total_commission,
                        SUM(ABS(qty * price))  AS total_volume
                    FROM fills
                    WHERE ts_event::date = $1
                    GROUP BY strategy_id
                    ORDER BY strategy_id
                    """,
                    today,
                )
                for row in rows:
                    fc = row["fill_count"]
                    tq = float(row["total_qty"] or 0)
                    tc = float(row["total_commission"] or 0)
                    tv = float(row["total_volume"] or 0)

                    result["total_fills"] += fc
                    result["total_commission"] += tc
                    result["total_volume"] += tv
                    result["by_strategy"].append(
                        {
                            "strategy_id": row["strategy_id"],
                            "fill_count": fc,
                            "total_qty": tq,
                            "total_commission": tc,
                            "total_volume": tv,
                        }
                    )

                # Round totals for readability.
                result["total_commission"] = round(result["total_commission"], 4)
                result["total_volume"] = round(result["total_volume"], 4)
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"EndOfDayReporterActor: PG fills query failed: {exc}")
            result["status"] = f"error — {exc}"

        return result

    # ── Section 3: Max Drawdown ────────────────────────────────────

    async def _section_max_drawdown(self, today: date) -> dict[str, Any]:
        """Estimate max drawdown from Redis P&L time series or PG fills.

        Reads ``sam:pnl:{strategy}:{date}:peak`` and
        ``sam:pnl:{strategy}:{date}:trough`` keys if available,
        or estimates from fill timestamps.
        """
        result: dict[str, Any] = {
            "status": "unavailable",
            "drawdowns": [],
        }

        if self.config.redis_host:
            try:
                import redis.asyncio as aioredis

                r = aioredis.Redis(
                    host=self.config.redis_host,
                    port=self.config.redis_port,
                    password=self.config.redis_password or None,
                    socket_connect_timeout=5,
                )

                date_str = today.isoformat()
                pnl_pattern = f"{_PNL_KEY_PREFIX}:*:{date_str}"
                cursor = 0
                found_any = False

                while True:
                    cursor, keys = await r.scan(cursor, match=pnl_pattern, count=100)
                    for key in keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        parts = key_str.rsplit(":", 1)
                        strategy_part = (
                            parts[0].removeprefix(f"{_PNL_KEY_PREFIX}:")
                            if len(parts) == 2
                            else ""
                        )
                        raw = await r.get(key_str)
                        if raw is not None:
                            try:
                                val = float(
                                    raw.decode() if isinstance(raw, bytes) else raw
                                )
                            except (ValueError, TypeError):
                                val = 0.0

                            result["drawdowns"].append(
                                {
                                    "strategy_id": strategy_part,
                                    "realized_pnl": val,
                                    "drawdown_available": False,
                                    "note": (
                                        "drawdown requires per-trade P&L "
                                        "time series — not yet implemented"
                                    ),
                                }
                            )
                            found_any = True

                    if cursor == 0:
                        break

                await r.aclose()

                if found_any:
                    result["status"] = "partial — time series not available"
            except Exception as exc:  # noqa: BLE001
                self.log.warning(
                    f"EndOfDayReporterActor: Redis drawdown read failed: {exc}"
                )

        # Fallback: compute drawdown from PG fills ordered by time.
        if not result["drawdowns"] and self.config.postgres_host:
            pg_drawdowns = await self._drawdown_from_fills(today)
            if pg_drawdowns:
                result["drawdowns"] = pg_drawdowns
                result["status"] = "estimated from fills"

        return result

    async def _drawdown_from_fills(self, today: date) -> list[dict[str, Any]]:
        """Estimate per-strategy drawdown from fill events."""
        drawdowns: list[dict[str, Any]] = []
        try:
            conn = await asyncpg.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                database=self.config.postgres_db,
                user=self.config.postgres_user,
                password=self.config.postgres_password,
                timeout=10,
            )
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                        strategy_id,
                        ts_event,
                        CASE WHEN side = 'SELL'
                            THEN qty * price
                            ELSE -qty * price
                        END AS signed_volume
                    FROM fills
                    WHERE ts_event::date = $1
                    ORDER BY strategy_id, ts_event
                    """,
                    today,
                )
                # Group by strategy, compute running P&L, track peak→trough.
                strategy_pnls: dict[str, list[tuple[datetime, float]]] = {}
                for row in rows:
                    sid = row["strategy_id"]
                    ts = row["ts_event"]
                    sv = float(row["signed_volume"])
                    strategy_pnls.setdefault(sid, []).append((ts, sv))

                for sid, events in strategy_pnls.items():
                    cumulative = 0.0
                    peak = 0.0
                    max_dd = 0.0
                    peak_ts = ""
                    trough_ts = ""

                    for ts, sv in events:
                        cumulative += sv
                        if cumulative > peak:
                            peak = cumulative
                            peak_ts = ts.isoformat()
                        dd = peak - cumulative
                        if dd > max_dd:
                            max_dd = dd
                            trough_ts = ts.isoformat()

                    drawdowns.append(
                        {
                            "strategy_id": sid,
                            "realized_pnl": round(cumulative, 4),
                            "max_drawdown": round(max_dd, 4),
                            "peak_time": peak_ts or None,
                            "trough_time": trough_ts or None,
                            "source": "postgres",
                        }
                    )
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"EndOfDayReporterActor: PG drawdown query failed: {exc}")

        return drawdowns

    # ── Section 4: Position Summary ────────────────────────────────

    def _section_positions(self) -> dict[str, Any]:
        """Check open positions — should all be flat at EOD."""
        positions = self.cache.positions()
        open_positions: list[dict[str, object]] = []

        for pos in positions or []:
            qty = getattr(pos, "net_qty", None) or getattr(pos, "quantity", 0)
            try:
                qty_val = float(qty) if qty else 0.0
            except (TypeError, ValueError):
                qty_val = 0.0

            if abs(qty_val) > 0:
                ins_id = str(getattr(pos, "instrument_id", "unknown"))
                sid = str(getattr(pos, "strategy_id", "unknown"))
                open_positions.append(
                    {
                        "instrument_id": ins_id,
                        "strategy_id": sid,
                        "net_qty": qty_val,
                    }
                )

        return {
            "total_open_positions": len(open_positions),
            "all_flat": len(open_positions) == 0,
            "positions": open_positions,
        }

    # ── Section 5: Rejection Events ─────────────────────────────────

    async def _section_rejections(self, today: date) -> dict[str, Any]:
        """Read rejection / circuit breaker state from Redis.

        Looks for ``sam:rejection:*`` keys and counts active streaks
        (published by RejectionMonitorActor).
        """
        result: dict[str, Any] = {
            "circuit_breakers_active": 0,
            "total_rejections": 0,
            "streaks": [],
        }

        if not self.config.redis_host:
            result["status"] = "skipped — redis not configured"
            return result

        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                socket_connect_timeout=5,
            )

            cursor = 0
            pattern = f"{_REJECTION_KEY_PREFIX}:*"
            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    raw = await r.get(key_str)
                    if raw is not None:
                        try:
                            data = json.loads(
                                raw.decode() if isinstance(raw, bytes) else raw
                            )
                            if isinstance(data, dict):
                                active = data.get("active", False)
                                count = data.get("count", 0)
                                ins = data.get("instrument_id", key_str)
                                if active:
                                    result["circuit_breakers_active"] += 1
                                result["total_rejections"] += count
                                result["streaks"].append(
                                    {
                                        "key": key_str,
                                        "instrument_id": ins,
                                        "count": count,
                                        "active": active,
                                    }
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass

                if cursor == 0:
                    break

            await r.aclose()
            result["status"] = "ok"
        except Exception as exc:  # noqa: BLE001
            self.log.warning(
                f"EndOfDayReporterActor: Redis rejection read failed: {exc}"
            )
            result["status"] = f"error — {exc}"

        return result

    # ── Section 6: Health Events ────────────────────────────────────

    async def _section_health(self, today: date) -> dict[str, Any]:
        """Read health heartbeat log from Redis.

        Looks for ``sam:heartbeat:*`` keys (written by HealthMonitorActor).
        """
        result: dict[str, Any] = {
            "heartbeat_count": 0,
            "last_heartbeat": None,
            "alerts": [],
        }

        if not self.config.redis_host:
            result["status"] = "skipped — redis not configured"
            return result

        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                socket_connect_timeout=5,
            )

            cursor = 0
            pattern = f"{_HEARTBEAT_KEY_PREFIX}:*"
            latest_ts: float = 0.0
            latest_raw: str = ""

            while True:
                cursor, keys = await r.scan(cursor, match=pattern, count=100)
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    raw = await r.get(key_str)
                    if raw is not None:
                        raw_str = raw.decode() if isinstance(raw, bytes) else str(raw)
                        result["heartbeat_count"] += 1
                        try:
                            data = json.loads(raw_str)
                            ts = data.get("timestamp", "")
                            if ts and ts > latest_raw:
                                latest_raw = ts
                                # Try to parse for ordering.
                                try:
                                    from datetime import datetime as dt

                                    parsed = dt.fromisoformat(ts.replace("Z", "+00:00"))
                                    ts_epoch = parsed.timestamp()
                                    if ts_epoch > latest_ts:
                                        latest_ts = ts_epoch
                                except (ValueError, TypeError):
                                    pass
                        except (json.JSONDecodeError, TypeError):
                            pass
                        result["alerts"].append(
                            {
                                "key": key_str,
                                "value": raw_str[:200],  # Truncate for sanity.
                            }
                        )

                if cursor == 0:
                    break

            if latest_raw:
                result["last_heartbeat"] = latest_raw

            await r.aclose()
            result["status"] = "ok"
        except Exception as exc:  # noqa: BLE001
            self.log.warning(f"EndOfDayReporterActor: Redis health read failed: {exc}")
            result["status"] = f"error — {exc}"

        return result

    # ── Persistence ────────────────────────────────────────────────

    async def _write_report_to_redis(self, today: date, report: dict[str, Any]) -> None:
        """Persist the EOD report to Redis with a 7-day TTL."""
        if not self.config.redis_host:
            self.log.debug(
                "EndOfDayReporterActor: Redis not configured — " "skipping report write"
            )
            return

        key = _EOD_KEY_TEMPLATE.format(
            market=self.config.market, date=today.isoformat()
        )

        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                socket_connect_timeout=5,
            )
            payload = json.dumps(report, indent=2, default=str)
            # 7-day TTL (604800 seconds).
            await r.setex(key, 604800, payload)
            await r.aclose()
            self.log.info(f"EndOfDayReporterActor: report written to Redis key={key}")
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"EndOfDayReporterActor: Redis report write failed: {exc}")

    async def _write_report_to_pg(self, today: date, report: dict[str, Any]) -> None:
        """Persist the EOD report to the ``daily_reports`` PostgreSQL table."""
        if not self.config.postgres_host:
            self.log.debug(
                "EndOfDayReporterActor: PostgreSQL not configured — "
                "skipping report write"
            )
            return

        try:
            conn = await asyncpg.connect(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                database=self.config.postgres_db,
                user=self.config.postgres_user,
                password=self.config.postgres_password,
                timeout=10,
            )
            try:
                await conn.execute(
                    """
                    INSERT INTO daily_reports (market, date, report_json)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (market, date)
                    DO UPDATE SET report_json = $3, created_at = NOW()
                    """,
                    self.config.market,
                    today,
                    json.dumps(report, default=str),
                )
            finally:
                await conn.close()
            self.log.info(
                f"EndOfDayReporterActor: report written to "
                f"PG daily_reports ({self.config.market}, {today.isoformat()})"
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error(f"EndOfDayReporterActor: PG report write failed: {exc}")

    # ── Logging ────────────────────────────────────────────────────

    def _log_report_summary(self, report: dict[str, Any]) -> None:
        """Log a human-readable summary of the report."""
        lines = [
            f"EndOfDayReport [{report['market']}] {report['date']}:",
        ]

        # P&L summary
        pnl = report.get("daily_pnl", [])
        total_pnl = 0.0
        for entry in pnl:
            val = entry.get("realized_pnl")
            if val is not None:
                total_pnl += val
        lines.append(f"  P&L: {total_pnl:.2f} across {len(pnl)} strategies")

        # Fills summary
        fills = report.get("fills_summary", {})
        lines.append(
            f"  Fills: {fills.get('total_fills', 0)} fills, "
            f"commission {fills.get('total_commission', 0):.2f}"
        )

        # Positions
        pos = report.get("position_summary", {})
        flat = (
            "✓ flat"
            if pos.get("all_flat")
            else f"✗ {pos.get('total_open_positions', 0)} open"
        )
        lines.append(f"  Positions: {flat}")

        # Rejections
        rej = report.get("rejection_events", {})
        lines.append(
            f"  Rejections: {rej.get('total_rejections', 0)} total, "
            f"{rej.get('circuit_breakers_active', 0)} active CBs"
        )

        # Health
        health = report.get("health_events", {})
        lines.append(f"  Health: {health.get('heartbeat_count', 0)} heartbeats")

        self.log.info("\n".join(lines))
