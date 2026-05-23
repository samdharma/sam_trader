"""RealizedPnLTrackerActor — per-strategy realized P&L via FIFO matching."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import redis.asyncio as aioredis
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId


class RealizedPnLTrackerActorConfig(ActorConfig, frozen=True):
    """Configuration for the RealizedPnLTrackerActor.

    Parameters
    ----------
    redis_host : str
        Redis host.
    redis_port : int
        Redis port.
    redis_password : str
        Redis password (empty = no auth).
    redis_db : int
        Redis database number.
    key_prefix : str
        Redis key prefix (default ``sam:pnl``).
    instrument_ids : list[str]
        Instrument IDs to subscribe to for order fills.

    """

    redis_host: str = "sam-redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0
    key_prefix: str = "sam:pnl"
    instrument_ids: list[str] = []


class RealizedPnLTrackerActor(Actor):
    """Actor that tracks **realized** P&L per strategy using FIFO matching.

    Listens to ``OrderFilled`` events, maintains FIFO lot lists per
    ``(strategy_id, instrument_id)``, computes realized P&L when closing
    lots, and persists the running total to Redis.

    Does **not** track unrealized P&L.  Resets at 00:00 UTC via the date
    component of the Redis key.

    Parameters
    ----------
    config : RealizedPnLTrackerActorConfig
        Actor configuration.

    """

    def __init__(self, config: RealizedPnLTrackerActorConfig):
        super().__init__(config)
        self._redis: aioredis.Redis | None = None
        self._redis_task: asyncio.Task | None = None
        # (strategy_id, instrument_id) -> list of (price, remaining_qty, side)
        # side: +1 for long entry, -1 for short entry
        self._lots: dict[tuple[str, str], list[tuple[Decimal, Decimal, int]]] = {}
        # (strategy_id, instrument_id) -> realized P&L for current session
        self._realized: dict[tuple[str, str], Decimal] = {}
        self._current_date: str | None = None

    def on_start(self) -> None:
        """Connect to Redis and subscribe to order fills."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.info(
                "RealizedPnLTrackerActor: no event loop, skipping Redis connect"
            )
            return

        self._redis_task = loop.create_task(self._connect_redis())

        for ins_str in self.config.instrument_ids:
            try:
                instrument_id = InstrumentId.from_str(ins_str)
                self.subscribe_order_fills(instrument_id)
                self.log.info(
                    f"RealizedPnLTrackerActor: subscribed to fills for {instrument_id}"
                )
            except Exception as exc:  # noqa: BLE001
                self.log.error(
                    "RealizedPnLTrackerActor: failed to subscribe to fills for "
                    f"{ins_str}: {exc}"
                )

        if not self.config.instrument_ids:
            self.log.warning(
                "RealizedPnLTrackerActor: no instrument_ids configured, "
                "fill subscription skipped"
            )

    async def _connect_redis(self) -> None:
        """Asynchronously connect to Redis."""
        try:
            self._redis = aioredis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password or None,
                db=self.config.redis_db,
                decode_responses=True,
            )
            ping_result = self._redis.ping()
            await ping_result  # type: ignore[misc]
            self.log.info("RealizedPnLTrackerActor: Redis connected")
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                "RealizedPnLTrackerActor: Redis connection failed: "
                f"host={self.config.redis_host}, port={self.config.redis_port}: {exc}"
            )

    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle OrderFilled by scheduling an async P&L update.

        Parameters
        ----------
        event : OrderFilled
            The fill event to process.

        """
        if self._redis is None:
            self.log.warning("RealizedPnLTrackerActor: Redis not ready, dropping fill")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.warning("RealizedPnLTrackerActor: no event loop, dropping fill")
            return

        loop.create_task(self._process_fill(event))

    async def _process_fill(self, event: OrderFilled) -> None:
        """Update FIFO lots, compute realized P&L, and persist to Redis.

        Parameters
        ----------
        event : OrderFilled
            The fill event to process.

        """
        date_str = self._ns_to_date(event.ts_event)

        # Reset state if we rolled over to a new UTC day.
        if self._current_date is not None and date_str != self._current_date:
            self._reset_state()
        self._current_date = date_str

        strategy_id = str(event.strategy_id)
        instrument_id = str(event.instrument_id)
        key = (strategy_id, instrument_id)

        price = Decimal(str(event.last_px.as_double()))
        qty = Decimal(str(event.last_qty.as_double()))
        fill_side = 1 if event.order_side == OrderSide.BUY else -1

        lots = self._lots.get(key, [])
        realized = self._realized.get(key, Decimal("0"))

        remaining = qty

        # Match against opposite-side lots FIFO.
        while remaining > 0 and lots and lots[0][2] != fill_side:
            lot_price, lot_qty, lot_side_val = lots[0]
            match_qty = min(remaining, lot_qty)

            # Realized P&L = (exit_price - entry_price) * match_qty * entry_direction
            realized += (price - lot_price) * match_qty * Decimal(lot_side_val)

            remaining -= match_qty
            if match_qty >= lot_qty:
                lots.pop(0)
            else:
                lots[0] = (lot_price, lot_qty - match_qty, lot_side_val)

        # Remaining quantity opens a new position in the fill direction.
        if remaining > 0:
            lots.append((price, remaining, fill_side))

        self._lots[key] = lots
        self._realized[key] = realized

        # Persist the total realized P&L for this strategy to Redis.
        total_pnl = self._get_total_realized(strategy_id)
        redis_key = f"{self.config.key_prefix}:{strategy_id}:{date_str}"
        try:
            await self._redis.set(redis_key, str(total_pnl))  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                "RealizedPnLTrackerActor: Redis persist failed for "
                f"{redis_key}: {exc}"
            )

    def _get_total_realized(self, strategy_id: str) -> Decimal:
        """Sum realized P&L across all instruments for a strategy."""
        total = Decimal("0")
        for (sid, _inst), pnl in self._realized.items():
            if sid == strategy_id:
                total += pnl
        return total

    def get_realized_pnl(self, strategy_id: str) -> Decimal:
        """Return realized P&L for *strategy_id* today.

        Parameters
        ----------
        strategy_id : str
            The strategy identifier.

        Returns
        -------
        Decimal
            Realized P&L (positive = profit, negative = loss).

        """
        return self._get_total_realized(strategy_id)

    def _reset_state(self) -> None:
        """Clear all lot and realized state (called at day rollover)."""
        self._lots.clear()
        self._realized.clear()
        self.log.info("RealizedPnLTrackerActor: state reset for new day")

    @staticmethod
    def _ns_to_date(ns: int) -> str:
        """Convert a nanosecond timestamp to an ISO date string.

        Parameters
        ----------
        ns : int
            Timestamp in nanoseconds.

        Returns
        -------
        str
            Date in ``YYYY-MM-DD`` format.

        """
        if ns:
            dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def on_stop(self) -> None:
        """Unsubscribe from fills and close the Redis connection."""
        if self._redis_task is not None and not self._redis_task.done():
            self._redis_task.cancel()

        for ins_str in self.config.instrument_ids:
            try:
                instrument_id = InstrumentId.from_str(ins_str)
                self.unsubscribe_order_fills(instrument_id)
            except Exception:  # noqa: S110
                pass

        if self._redis is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._redis.close())
                self.log.info("RealizedPnLTrackerActor: scheduled Redis close")
            except RuntimeError:
                self.log.info(
                    "RealizedPnLTrackerActor: no event loop, "
                    "cannot close Redis gracefully"
                )
            finally:
                self._redis = None
