"""Background Redis subscriber for graceful restart handshake.

When the ops CLI publishes ``sam:restart_request graceful``, this subscriber
calls ``node.trader.save()`` to persist actor + strategy state to Redis,
then publishes a ``sam:state_saved`` confirmation so the orchestrator knows
it is safe to restart the container.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

import redis.asyncio as aioredis
from nautilus_trader.live.node import TradingNode

from sam_trader.config import SamTraderConfig

logger = logging.getLogger(__name__)

RESTART_REQUEST_CHANNEL = "sam:restart_request"
STATE_SAVED_CHANNEL = "sam:state_saved"


class RestartSubscriber:
    """Subscribes to Redis restart requests and triggers graceful state save.

    Parameters
    ----------
    node : TradingNode
        The running Nautilus trading node.
    cfg : SamTraderConfig
        Configuration (Redis connection + handshake timeout).

    """

    def __init__(self, node: TradingNode, cfg: SamTraderConfig) -> None:
        self._node = node
        self._cfg = cfg
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background subscriber thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("RestartSubscriber started on %s", RESTART_REQUEST_CHANNEL)

    def stop(self) -> None:
        """Signal the subscriber to stop and wait for thread exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._cfg.state_save_handshake_timeout)
        logger.info("RestartSubscriber stopped")

    def _run(self) -> None:
        """Thread entry point — runs the async listener."""
        try:
            asyncio.run(self._listen())
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartSubscriber loop exited with error: %s", exc)

    async def _listen(self) -> None:
        """Async Redis pub/sub listener."""
        try:
            redis = aioredis.Redis(
                host=self._cfg.redis_host,
                port=self._cfg.redis_port,
                password=self._cfg.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            pubsub = redis.pubsub()
            await pubsub.subscribe(RESTART_REQUEST_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartSubscriber: Redis connection failed: %s", exc)
            return

        try:
            async for message in pubsub.listen():
                if self._stop_event.is_set():
                    break
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if data == "graceful":
                    await self._handle_graceful(redis)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartSubscriber: Redis listen error: %s", exc)
        finally:
            try:
                await pubsub.unsubscribe(RESTART_REQUEST_CHANNEL)
                await redis.close()
            except Exception:  # noqa: S110
                pass

    async def _handle_graceful(self, redis: aioredis.Redis) -> None:
        """Handle graceful restart request: save state and publish confirmation."""
        logger.info("RestartSubscriber: graceful restart requested, saving state")
        try:
            self._save_state()
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartSubscriber: state save failed: %s", exc)
            return

        try:
            payload = {
                "trader_id": str(self._node.trader_id),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "saved",
            }
            await redis.publish(STATE_SAVED_CHANNEL, json.dumps(payload))
            logger.info("RestartSubscriber: state saved confirmation published")
        except Exception as exc:  # noqa: BLE001
            logger.warning("RestartSubscriber: failed to publish confirmation: %s", exc)

    def _save_state(self) -> None:
        """Save actor and strategy states via the trading node.

        Schedules the save on the node's event loop when possible to avoid
        thread-safety issues; falls back to a direct call if the loop is not
        available.
        """
        loop = self._node.get_event_loop()
        if loop is not None and loop.is_running():
            done_event = threading.Event()
            save_exc: Exception | None = None

            def _save_and_signal() -> None:
                nonlocal save_exc
                try:
                    self._node.trader.save()
                except Exception as exc:
                    save_exc = exc
                finally:
                    done_event.set()

            loop.call_soon_threadsafe(_save_and_signal)
            if not done_event.wait(timeout=self._cfg.state_save_handshake_timeout):
                raise TimeoutError("state save timed out on node event loop")
            if save_exc is not None:
                raise save_exc
        else:
            self._node.trader.save()
