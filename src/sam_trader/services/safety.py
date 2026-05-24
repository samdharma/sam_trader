"""Safety controls — kill switch, circuit breakers, emergency halt.

All commands work from the ``sam-services`` CLI.  State is persisted in Redis
so it survives ``sam-services`` restarts.

"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SafetyState(str, Enum):
    """Trading safety state persisted in Redis."""

    RUNNING = "RUNNING"
    CLOSE_ONLY = "CLOSE_ONLY"
    HALTED = "HALTED"


@dataclass(frozen=True)
class SafetyConfig:
    """Circuit-breaker thresholds and Redis connection."""

    max_daily_loss: float
    connectivity_timeout_secs: int
    max_rejection_streak: int
    redis_host: str
    redis_port: int
    redis_password: str


def get_safety_config() -> SafetyConfig:
    """Load safety configuration from environment variables."""
    return SafetyConfig(
        max_daily_loss=float(os.getenv("SAFETY_MAX_DAILY_LOSS", "0")),
        connectivity_timeout_secs=int(
            os.getenv("SAFETY_CONNECTIVITY_TIMEOUT_SECS", "60")
        ),
        max_rejection_streak=int(os.getenv("SAFETY_MAX_REJECTION_STREAK", "3")),
        redis_host=os.getenv("REDIS_HOST", "sam-redis"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_password=os.getenv("REDIS_PASSWORD", ""),
    )


def _redis_client(config: SafetyConfig) -> Any:
    """Return a synchronous Redis client."""
    import redis  # type: ignore[import-untyped]

    return redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password or None,
        decode_responses=True,
        socket_connect_timeout=5,
    )


def publish_safety_state(
    redis_client: Any,
    state: SafetyState,
    reason: str,
) -> None:
    """Publish safety state to Redis for sam-trader to consume.

    Writes the persistent key ``sam:kill_switch`` plus audit keys
    ``sam:kill_switch:reason`` and ``sam:kill_switch:timestamp``.
    Also publishes on the ``sam:kill_switch`` pub/sub channel.

    Parameters
    ----------
    redis_client : redis.Redis
        Synchronous Redis client.
    state : SafetyState
        The new safety state.
    reason : str
        Human-readable reason for the state change.

    """
    now = datetime.now(timezone.utc).isoformat()
    redis_client.set("sam:kill_switch", state.value)
    redis_client.set("sam:kill_switch:reason", reason)
    redis_client.set("sam:kill_switch:timestamp", now)
    redis_client.publish("sam:kill_switch", state.value)
    logger.critical(
        "SAFETY %s → %s (reason=%s, timestamp=%s)",
        state.value,
        state.value,
        reason,
        now,
    )


def cmd_kill(config: SafetyConfig | None = None) -> dict[str, Any]:
    """Activate the kill switch: cancel all orders, halt trading.

    Parameters
    ----------
    config : SafetyConfig, optional
        Safety configuration.  Loaded from env vars when omitted.

    Returns
    -------
    dict
        Result with ``status``, ``state``, and ``reason``.

    """
    cfg = config or get_safety_config()
    r = _redis_client(cfg)
    publish_safety_state(
        r, SafetyState.HALTED, "Manual kill switch activated (sam kill)"
    )
    return {"status": "success", "state": SafetyState.HALTED.value, "reason": "kill"}


def cmd_halt(config: SafetyConfig | None = None) -> dict[str, Any]:
    """Halt trading: cancel all orders, position-close-only mode.

    Parameters
    ----------
    config : SafetyConfig, optional
        Safety configuration.  Loaded from env vars when omitted.

    Returns
    -------
    dict
        Result with ``status``, ``state``, and ``reason``.

    """
    cfg = config or get_safety_config()
    r = _redis_client(cfg)
    publish_safety_state(r, SafetyState.CLOSE_ONLY, "Manual halt activated (sam halt)")
    return {
        "status": "success",
        "state": SafetyState.CLOSE_ONLY.value,
        "reason": "halt",
    }


def cmd_resume(config: SafetyConfig | None = None) -> dict[str, Any]:
    """Resume trading: clear halt, re-enable normal operation.

    Parameters
    ----------
    config : SafetyConfig, optional
        Safety configuration.  Loaded from env vars when omitted.

    Returns
    -------
    dict
        Result with ``status``, ``state``, and ``reason``.

    """
    cfg = config or get_safety_config()
    r = _redis_client(cfg)
    publish_safety_state(r, SafetyState.RUNNING, "Manual resume activated (sam resume)")
    return {
        "status": "success",
        "state": SafetyState.RUNNING.value,
        "reason": "resume",
    }


# ---------------------------------------------------------------------------
# Circuit breakers (automated)
# ---------------------------------------------------------------------------


def check_daily_pnl_breaker(
    redis_client: Any,
    max_daily_loss: float,
) -> list[dict[str, Any]]:
    """Check realized P&L against daily loss limit.

    Scans Redis keys ``sam:pnl:{strategy}:{date}`` and returns any
    strategy whose realized loss exceeds *max_daily_loss*.

    Parameters
    ----------
    redis_client : redis.Redis
        Synchronous Redis client.
    max_daily_loss : float
        Maximum allowed daily loss (positive number).  Zero disables.

    Returns
    -------
    list[dict]
        Triggered strategies with ``strategy_id``, ``pnl``, ``limit``.

    """
    if max_daily_loss <= 0:
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    triggered: list[dict[str, Any]] = []

    for key in redis_client.scan_iter(match=f"sam:pnl:*:{today}"):
        val = redis_client.get(key)
        if val is None:
            continue
        try:
            pnl = float(val)
        except ValueError:
            continue
        # pnl < 0 means a loss; trigger if loss magnitude exceeds limit.
        if pnl < -max_daily_loss:
            strategy_id = key.split(":")[2]
            triggered.append(
                {
                    "strategy_id": strategy_id,
                    "pnl": pnl,
                    "limit": -max_daily_loss,
                }
            )

    return triggered


def check_rejection_streak_breaker(redis_client: Any) -> list[dict[str, Any]]:
    """Check for strategies halted by RejectionMonitorActor.

    Scans Redis keys ``sam:rejection_halt:{strategy_id}`` written by
    :class:`RejectionMonitorActor`.

    Parameters
    ----------
    redis_client : redis.Redis
        Synchronous Redis client.

    Returns
    -------
    list[dict]
        Halted strategies with ``strategy_id`` and ``reason``.

    """
    triggered: list[dict[str, Any]] = []
    for key in redis_client.scan_iter(match="sam:rejection_halt:*"):
        val = redis_client.get(key)
        if val:
            strategy_id = key.split(":")[2]
            triggered.append({"strategy_id": strategy_id, "reason": val})
    return triggered


def check_connectivity_breaker(
    redis_client: Any,
    timeout_secs: int,
) -> dict[str, Any] | None:
    """Check HealthMonitorActor heartbeat freshness.

    Reads ``sam:heartbeat:last`` from Redis.

    Parameters
    ----------
    redis_client : redis.Redis
        Synchronous Redis client.
    timeout_secs : int
        Seconds before a heartbeat is considered stale.

    Returns
    -------
    dict | None
        Connectivity issue details, or *None* if heartbeat is fresh.

    """
    last_hb = redis_client.get("sam:heartbeat:last")
    if last_hb is None:
        return {"status": "no_heartbeat", "last_seen": None, "age_seconds": None}

    try:
        last_ts = datetime.fromisoformat(last_hb)
    except ValueError:
        return {
            "status": "invalid_timestamp",
            "last_seen": last_hb,
            "age_seconds": None,
        }

    age = (datetime.now(timezone.utc) - last_ts).total_seconds()
    if age > timeout_secs:
        return {
            "status": "timeout",
            "last_seen": last_hb,
            "age_seconds": int(age),
        }
    return None


def run_circuit_breaker_monitor(
    config: SafetyConfig | None = None,
) -> dict[str, Any]:
    """Run one iteration of all circuit-breaker checks.

    This function is intended to be called periodically (e.g. via a cron
    job or the ``sam safety-monitor`` CLI command).

    Parameters
    ----------
    config : SafetyConfig, optional
        Safety configuration.  Loaded from env vars when omitted.

    Returns
    -------
    dict
        Audit record with ``timestamp``, ``breakers``, and ``actions``.

    """
    cfg = config or get_safety_config()
    r = _redis_client(cfg)
    now = datetime.now(timezone.utc).isoformat()
    breakers: dict[str, Any] = {}
    actions: list[dict[str, Any]] = []

    # DAILY_PNL
    pnl_triggers = check_daily_pnl_breaker(r, cfg.max_daily_loss)
    breakers["daily_pnl"] = {
        "triggered": bool(pnl_triggers),
        "strategies": pnl_triggers,
    }
    for t in pnl_triggers:
        reason = (
            f"DAILY_PNL breaker: {t['strategy_id']} "
            f"realized PnL {t['pnl']:.2f} exceeds limit {t['limit']:.2f}"
        )
        publish_safety_state(r, SafetyState.HALTED, reason)
        actions.append({"action": "kill", "reason": reason})

    # REJECTION_STREAK
    rej_triggers = check_rejection_streak_breaker(r)
    breakers["rejection_streak"] = {
        "triggered": bool(rej_triggers),
        "strategies": rej_triggers,
    }
    for t in rej_triggers:
        reason = (
            f"REJECTION_STREAK breaker: {t['strategy_id']} "
            f"halted by RejectionMonitorActor ({t['reason']})"
        )
        # Per-strategy halt (system stays RUNNING unless overridden).
        r.set(f"sam:strategy_halt:{t['strategy_id']}", "HALTED")
        actions.append({"action": "strategy_halt", "reason": reason})

    # CONNECTIVITY_LOSS
    conn_issue = check_connectivity_breaker(r, cfg.connectivity_timeout_secs)
    breakers["connectivity"] = {
        "triggered": conn_issue is not None,
        "details": conn_issue,
    }
    if conn_issue:
        reason = f"CONNECTIVITY_LOSS breaker: {conn_issue['status']}"
        logger.critical("SAFETY %s — %s", reason, conn_issue)
        actions.append({"action": "log_critical", "reason": reason})

    return {
        "timestamp": now,
        "breakers": breakers,
        "actions": actions,
    }


def main() -> int:
    """Entry point for ``python -m sam_trader.services.safety``."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    result = run_circuit_breaker_monitor()
    if result["actions"]:
        for action in result["actions"]:
            logger.critical(
                "SAFETY ACTION: %s — %s", action["action"], action["reason"]
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
