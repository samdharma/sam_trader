"""BundleController — Nautilus Controller for dynamic strategy lifecycle.

Subscribes to Redis channels to load/unload strategies at runtime:

- ``sam:bundle:load`` → JSON bundle dict → `create_strategy_from_config()`
- ``sam:bundle:unload`` → JSON ``{strategy_id: ...}`` → `remove_strategy_from_id()`

Also provides ``reload_market(market)`` for bulk market switching.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.trading.config import ImportableStrategyConfig
from nautilus_trader.trading.controller import Controller

if TYPE_CHECKING:
    from nautilus_trader.trading.trader import Trader

logger = logging.getLogger(__name__)

# Redis channels
CHANNEL_BUNDLE_LOAD = "sam:bundle:load"
CHANNEL_BUNDLE_UNLOAD = "sam:bundle:unload"


class BundleControllerConfig(ActorConfig, frozen=True):
    """Configuration for BundleController.

    Parameters
    ----------
    redis_host : str
        Redis host for pub/sub.
    redis_port : int
        Redis port.
    redis_password : str
        Redis password (empty string if none).
    bundles_path : str
        Path to ``bundles.yaml`` for ``reload_market()``.
    market : str
        Current active market (``"US"``, ``"HK"``, or ``""``).

    """

    redis_host: str = ""
    redis_port: int = 6379
    redis_password: str = ""
    bundles_path: str = "config/bundles.yaml"
    market: str = ""


class BundleController(Controller):
    """Controller that manages strategy lifecycle via Redis pub/sub.

    Parameters
    ----------
    config : BundleControllerConfig
        Controller configuration.

    """

    def __init__(
        self,
        config: BundleControllerConfig,
        trader: Trader | None = None,
    ) -> None:
        # ControllerFactory passes config= and trader= as kwargs.
        # We forward both so the parent Controller stores _trader.
        if trader is None:
            # Unit tests may omit trader; Controller will error at
            # registration time but that is acceptable for testing.
            super().__init__(trader=None, config=config)  # type: ignore[arg-type]
        else:
            super().__init__(trader=trader, config=config)
        self._redis_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._loaded_ids: set[str] = set()
        self._active_market: str = config.market

    # ── Lifecycle ──────────────────────────────────────────────────

    def on_start(self) -> None:
        """Start Redis pub/sub listener for bundle commands."""
        self._redis_thread = threading.Thread(target=self._run, daemon=True)
        self._redis_thread.start()
        self.log.info(
            f"BundleController: subscribed to {CHANNEL_BUNDLE_LOAD}, "
            f"{CHANNEL_BUNDLE_UNLOAD}"
        )

    def on_stop(self) -> None:
        """Stop Redis listener and clean up."""
        self._stop_event.set()
        if self._redis_thread is not None:
            self._redis_thread.join(timeout=5)
        self.log.info("BundleController: stopped")

    # ── Public API ─────────────────────────────────────────────────

    def load_bundle(self, bundle_dict: dict[str, Any]) -> None:
        """Create and start a strategy from a bundle dictionary.

        Converts *bundle_dict* to an ``ImportableStrategyConfig`` and
        creates the strategy via ``self.create_strategy_from_config()``.

        Parameters
        ----------
        bundle_dict : dict[str, Any]
            Bundle definition matching the ``bundles.yaml`` schema.

        """
        config = self._dict_to_config(bundle_dict)
        bundle_id = config.config.get("bundle_id", "unknown")
        self.log.info(f"BundleController: loading bundle {bundle_id}")
        self.create_strategy_from_config(config, start=True)

    def unload_bundle(self, strategy_id: str) -> None:
        """Remove and stop a strategy by ID.

        Parameters
        ----------
        strategy_id : str
            The strategy ID to remove (e.g., ``OrbStrategy-0``).

        """
        self.log.info(f"BundleController: unloading strategy {strategy_id}")
        self.remove_strategy_from_id(strategy_id)

    def reload_market(self, market: str) -> None:
        """Unload all current strategies and load bundles for *market*.

        Reads ``bundles.yaml``, filters bundles to *market*, stops all
        currently-running strategies, then loads the target-market bundles.

        Parameters
        ----------
        market : str
            Target market code (``"US"`` or ``"HK"``).

        """
        self.log.info(
            f"BundleController: reloading market from "
            f"{self._active_market} to {market}"
        )

        # ── Step 1: Unload all current strategies ──
        # Nautilus Controller provides access to the trader's strategy list
        # via self._trader. We stop + remove each.
        from nautilus_trader.model.identifiers import StrategyId

        for strat in self._trader.strategies():
            sid = strat.id.value if hasattr(strat.id, "value") else str(strat.id)
            self.log.info(f"BundleController: removing strategy {sid}")
            self.remove_strategy_from_id(StrategyId(sid))

        # ── Step 2: Load target-market bundles ──
        from sam_trader.bundle_loader import load_bundles

        try:
            all_bundles = load_bundles(self.config.bundles_path)
        except Exception as exc:
            self.log.error(
                f"BundleController: failed to load bundles from "
                f"{self.config.bundles_path}: {exc}"
            )
            return

        target_bundles = [
            b for b in all_bundles if b.config.get("market", "US") == market
        ]

        if not target_bundles:
            self.log.warning(
                f"BundleController: no bundles found for market {market} "
                f"in {self.config.bundles_path}"
            )
            return

        for bundle in target_bundles:
            bid = bundle.config.get("bundle_id", "unknown")
            self.log.info(f"BundleController: loading bundle {bid} for {market}")
            self.create_strategy_from_config(bundle, start=True)

        # Update the stored market reference.
        self._active_market = market

        self.log.info(
            f"BundleController: market reload complete — "
            f"{len(target_bundles)} strategies loaded for {market}"
        )

    # ── Redis Listener (threaded) ──────────────────────────────────

    def _run(self) -> None:
        """Thread entry point — runs the async Redis listener."""
        try:
            asyncio.run(self._listen())
        except Exception as exc:
            self.log.warning(f"BundleController: Redis listener exited: {exc}")

    async def _listen(self) -> None:
        """Async Redis pub/sub loop."""
        try:
            redis = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            pubsub = redis.pubsub()
            await pubsub.subscribe(CHANNEL_BUNDLE_LOAD, CHANNEL_BUNDLE_UNLOAD)
        except Exception as exc:
            self.log.warning(f"BundleController: Redis connection failed: {exc}")
            return

        try:
            async for message in pubsub.listen():
                if self._stop_event.is_set():
                    break
                if message.get("type") != "message":
                    continue
                channel = message.get("channel", "")
                data = message.get("data", "")
                self._dispatch_redis(channel, data)
        except Exception as exc:
            self.log.warning(f"BundleController: Redis listen error: {exc}")
        finally:
            try:
                await pubsub.unsubscribe()
                await redis.close()
            except Exception:
                pass

    def _dispatch_redis(self, channel: str, data: str) -> None:
        """Dispatch an incoming Redis message to the appropriate handler."""
        loop = self._trader.get_event_loop()
        if loop is not None and loop.is_running():
            done = threading.Event()
            exc_box: list[Exception] = []

            def _call() -> None:
                try:
                    self._handle_redis_message(channel, data)
                except Exception as exc:
                    exc_box.append(exc)
                finally:
                    done.set()

            loop.call_soon_threadsafe(_call)
            if not done.wait(timeout=10):
                self.log.error(
                    f"BundleController: Redis dispatch timed out on {channel}"
                )
                return
            if exc_box:
                self.log.error(
                    f"BundleController: dispatch error on {channel}: " f"{exc_box[0]}"
                )
        else:
            self._handle_redis_message(channel, data)

    def _handle_redis_message(self, channel: str, data: str) -> None:
        """Process a Redis message on the trader's event loop."""
        try:
            payload: dict[str, Any] = json.loads(data)
        except json.JSONDecodeError:
            self.log.warning(f"BundleController: invalid JSON on {channel}: {data}")
            return

        if channel == CHANNEL_BUNDLE_LOAD:
            self._handle_load(payload)
        elif channel == CHANNEL_BUNDLE_UNLOAD:
            self._handle_unload(payload)
        else:
            self.log.warning(f"BundleController: unknown channel {channel}")

    def _handle_load(self, payload: dict[str, Any]) -> None:
        """Process a bundle load request from Redis."""
        try:
            self.load_bundle(payload)
        except Exception as exc:
            bundle_id = payload.get("id", "unknown")
            self.log.error(
                f"BundleController: failed to load bundle {bundle_id}: {exc}"
            )

    def _handle_unload(self, payload: dict[str, Any]) -> None:
        """Process a bundle unload request from Redis."""
        strategy_id = payload.get("strategy_id", "")
        if not strategy_id:
            self.log.warning("BundleController: unload message missing strategy_id")
            return
        try:
            self.unload_bundle(strategy_id)
        except Exception as exc:
            self.log.error(f"BundleController: failed to unload {strategy_id}: {exc}")

    # ── Helpers ────────────────────────────────────────────────────

    def _dict_to_config(self, bundle: dict[str, Any]) -> ImportableStrategyConfig:
        """Convert a bundle dictionary to an ``ImportableStrategyConfig``.

        Replicates the logic from ``bundle_loader._load_bundle()`` so the
        controller can create strategies from Redis-published bundles.

        Parameters
        ----------
        bundle : dict[str, Any]
            Raw bundle mapping matching ``bundles.yaml`` schema.

        Returns
        -------
        ImportableStrategyConfig

        """
        from sam_trader.bundle_loader import _load_bundle

        return _load_bundle(bundle)
