"""Restart orchestrator for market-switch operations.

Runs inside ``sam-services`` as a background thread.  Listens on Redis
``sam:market_switch_request`` channel and performs the full graceful
switch flow:

1. Wait for ``sam:state_saved`` confirmation from sam-trader.
2. Update ``MARKET`` in ``.env``.
3. Recreate sam-trader container so the new env var is picked up.
4. Poll ``sam:state_loaded`` Redis key.
5. On failure → rollback ``MARKET`` and log CRITICAL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

MARKET_SWITCH_REQUEST_CHANNEL = "sam:market_switch_request"
MARKET_SWITCH_COMPLETE_CHANNEL = "sam:market_switch_complete"
MARKET_SWITCH_FAILED_CHANNEL = "sam:market_switch_failed"
RESTART_REQUEST_CHANNEL = "sam:restart_request"
STATE_SAVED_CHANNEL = "sam:state_saved"
STATE_LOADED_KEY = "sam:state_loaded"

DEFAULT_STATE_SAVE_TIMEOUT = 30
DEFAULT_STATE_LOADED_TIMEOUT = 60
DEFAULT_HEALTH_TIMEOUT = 60

ENV_FILE_PATHS = [
    Path("/opt/sam_trader/.env"),  # inside sam-services container
    Path(".env"),  # local dev fallback
]

COMPOSE_FILE = Path("docker/docker-compose.yml")


@dataclass(frozen=True)
class OrchestratorConfig:
    """Configuration for RestartOrchestrator."""

    redis_host: str = "sam-redis"
    redis_port: int = 6379
    redis_password: str = ""
    state_save_timeout: int = DEFAULT_STATE_SAVE_TIMEOUT
    state_loaded_timeout: int = DEFAULT_STATE_LOADED_TIMEOUT
    health_timeout: int = DEFAULT_HEALTH_TIMEOUT
    docker_binary: str = "docker"
    sam_trader_container: str = "sam-trader"


def _find_env_file() -> Path | None:
    """Return the first existing env file path, or None."""
    for p in ENV_FILE_PATHS:
        if p.exists():
            return p
    return None


def _read_market_from_env(path: Path) -> str:
    """Read current MARKET value from .env file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    for line in text.splitlines():
        if line.startswith("MARKET="):
            return line.split("=", 1)[1].strip()
    return ""


def _update_market_in_env(path: Path, market: str) -> None:
    """Update MARKET value in .env file, adding the line if missing."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^MARKET=.*$", re.MULTILINE)
    new_line = f"MARKET={market}"
    if pattern.search(text):
        text = pattern.sub(new_line, text)
    else:
        text = text.rstrip("\n") + "\n" + new_line + "\n"
    path.write_text(text, encoding="utf-8")


class RestartOrchestrator:
    """Background orchestrator for market-switch restarts.

    Parameters
    ----------
    config : OrchestratorConfig
        Redis connection + timeout configuration.

    """

    def __init__(self, config: OrchestratorConfig | None = None) -> None:
        self._config = config or OrchestratorConfig()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background orchestrator thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("RestartOrchestrator started on %s", MARKET_SWITCH_REQUEST_CHANNEL)

    def stop(self) -> None:
        """Signal the orchestrator to stop and wait for thread exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("RestartOrchestrator stopped")

    def _run(self) -> None:
        """Thread entry point — runs the async listener."""
        try:
            import asyncio

            asyncio.run(self._listen())
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartOrchestrator loop exited with error: %s", exc)

    async def _listen(self) -> None:
        """Async Redis pub/sub listener."""
        cfg = self._config
        try:
            redis = aioredis.Redis(
                host=cfg.redis_host,
                port=cfg.redis_port,
                password=cfg.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            pubsub = redis.pubsub()
            await pubsub.subscribe(MARKET_SWITCH_REQUEST_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartOrchestrator: Redis connection failed: %s", exc)
            return

        try:
            async for message in pubsub.listen():
                if self._stop_event.is_set():
                    break
                if message.get("type") != "message":
                    continue
                data = message.get("data", "")
                await self._handle_request(redis, data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartOrchestrator: Redis listen error: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe(MARKET_SWITCH_REQUEST_CHANNEL)
                await redis.close()
            except Exception:  # noqa: S110
                pass

    async def _handle_request(self, redis: aioredis.Redis, data: str) -> None:
        """Process a single market-switch request."""
        try:
            payload = json.loads(data)
            target_market = str(payload.get("market", "")).upper()
        except json.JSONDecodeError:
            target_market = str(data).upper().strip()

        if target_market not in ("US", "HK"):
            logger.error("RestartOrchestrator: invalid market '%s'", target_market)
            await self._publish_failed(redis, f"invalid market: {target_market}")
            return

        env_path = _find_env_file()
        if env_path is None:
            logger.error("RestartOrchestrator: .env file not found")
            await self._publish_failed(redis, ".env file not found")
            return

        previous_market = _read_market_from_env(env_path)
        logger.info(
            "RestartOrchestrator: switching market %s → %s",
            previous_market or "(unset)",
            target_market,
        )

        # ------------------------------------------------------------------
        # 1. Graceful state save
        # ------------------------------------------------------------------
        state_saved = await self._wait_for_state_saved(redis)
        if not state_saved:
            logger.critical(
                "RestartOrchestrator: state-save handshake timed out — aborting switch"
            )
            await self._publish_failed(redis, "state-save handshake timed out")
            return

        # ------------------------------------------------------------------
        # 2. Update MARKET in .env
        # ------------------------------------------------------------------
        try:
            _update_market_in_env(env_path, target_market)
            logger.info(
                "RestartOrchestrator: updated MARKET=%s in %s", target_market, env_path
            )
        except Exception as exc:  # noqa: BLE001
            logger.critical("RestartOrchestrator: failed to update .env: %s", exc)
            await self._publish_failed(redis, f"env update failed: {exc}")
            return

        # ------------------------------------------------------------------
        # 3. Recreate sam-trader container (restart does not pick up new env)
        # ------------------------------------------------------------------
        docker_ok = self._recreate_trader()
        if not docker_ok:
            logger.critical(
                "RestartOrchestrator: docker recreate failed — rolling back MARKET"
            )
            try:
                _update_market_in_env(env_path, previous_market)
            except Exception as rb_exc:  # noqa: BLE001
                logger.critical("RestartOrchestrator: rollback also failed: %s", rb_exc)
            await self._publish_failed(
                redis, "docker recreate failed, MARKET rolled back"
            )
            return

        # ------------------------------------------------------------------
        # 4. Poll sam:state_loaded
        # ------------------------------------------------------------------
        loaded = await self._poll_state_loaded(redis)
        if not loaded:
            logger.critical(
                "RestartOrchestrator: state_loaded not confirmed — rolling back MARKET"
            )
            try:
                _update_market_in_env(env_path, previous_market)
            except Exception as rb_exc:  # noqa: BLE001
                logger.critical("RestartOrchestrator: rollback also failed: %s", rb_exc)
            # Attempt to restart back to previous market
            self._recreate_trader()
            await self._publish_failed(
                redis, "state_loaded timeout, MARKET rolled back and trader restarted"
            )
            return

        logger.info(
            "RestartOrchestrator: market switch %s → %s completed successfully",
            previous_market or "(unset)",
            target_market,
        )
        await self._publish_complete(redis, target_market)

    async def _wait_for_state_saved(self, redis: aioredis.Redis) -> bool:
        """Publish restart request and wait for state_saved confirmation."""
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(STATE_SAVED_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to subscribe to %s: %s", STATE_SAVED_CHANNEL, exc)
            return False

        # Consume the subscription-confirmation message so it doesn't
        # interfere with the real state_saved message we are waiting for.
        try:
            confirm_msg = await pubsub.get_message(
                timeout=2.0  # type: ignore[call-arg]
            )
            if confirm_msg is None:
                logger.warning(
                    "No subscription confirmation for %s",
                    STATE_SAVED_CHANNEL,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error reading subscription confirmation: %s", exc)

        try:
            await redis.publish(RESTART_REQUEST_CHANNEL, "graceful")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to publish restart request: %s", exc)
            await pubsub.unsubscribe()
            return False

        start = time.time()
        confirmed = False
        try:
            while time.time() - start < self._config.state_save_timeout:
                message = await pubsub.get_message(
                    timeout=1.0  # type: ignore[call-arg]
                )
                if message and message.get("type") == "message":
                    data = message.get("data", "")
                    try:
                        payload = json.loads(data)
                        if payload.get("status") == "saved":
                            confirmed = True
                            break
                    except json.JSONDecodeError:
                        if data == "saved":
                            confirmed = True
                            break
                await asyncio.sleep(0.1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error waiting for state_saved: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe()
            except Exception:  # noqa: S110
                pass

        return confirmed

    async def _poll_state_loaded(self, redis: aioredis.Redis) -> bool:
        """Poll Redis for sam:state_loaded key."""
        start = time.time()
        while time.time() - start < self._config.state_loaded_timeout:
            try:
                if await redis.exists(STATE_LOADED_KEY):
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error polling state_loaded: %s", exc)
            await asyncio.sleep(0.5)  # type: ignore[name-defined]
        return False

    def _recreate_trader(self) -> bool:
        """Recreate sam-trader container to pick up new env vars.

        Uses ``docker compose up -d --force-recreate --no-deps`` because a
        plain ``restart`` does not re-evaluate environment variables.
        """
        cfg = self._config
        cmd = [
            cfg.docker_binary,
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            cfg.sam_trader_container,
        ]
        logger.info("RestartOrchestrator: running %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.critical(
                "RestartOrchestrator: docker recreate failed: %s",
                result.stderr.strip(),
            )
            return False
        return True

    async def _publish_complete(self, redis: aioredis.Redis, market: str) -> None:
        """Publish market_switch_complete notification."""
        payload = {
            "market": market,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
        }
        try:
            await redis.publish(MARKET_SWITCH_COMPLETE_CHANNEL, json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to publish complete: %s", exc)

    async def _publish_failed(self, redis: aioredis.Redis, reason: str) -> None:
        """Publish market_switch_failed notification."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "reason": reason,
        }
        try:
            await redis.publish(MARKET_SWITCH_FAILED_CHANNEL, json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to publish failure: %s", exc)
