"""TradeJournalActor — writes OrderFilled events to PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId


class TradeJournalActorConfig(ActorConfig, frozen=True):
    """Configuration for the TradeJournalActor.

    Parameters
    ----------
    postgres_host : str
        PostgreSQL host.
    postgres_port : int
        PostgreSQL port.
    postgres_db : str
        PostgreSQL database name.
    postgres_user : str
        PostgreSQL user.
    postgres_password : str
        PostgreSQL password.
    instrument_ids : list[str]
        Instrument IDs to subscribe to for order fills.

    """

    postgres_host: str = "sam-postgres"
    postgres_port: int = 5432
    postgres_db: str = "sam_trader"
    postgres_user: str = "sam"
    postgres_password: str = "sam_secret"
    instrument_ids: list[str] = []


class TradeJournalActor(Actor):
    """Actor that persists OrderFilled events to PostgreSQL via asyncpg.

    Parameters
    ----------
    config : TradeJournalActorConfig
        Actor configuration.

    """

    def __init__(self, config: TradeJournalActorConfig):
        super().__init__(config)
        self._pool: Any = None
        self._pool_task: asyncio.Task | None = None

    def on_start(self) -> None:
        """Create the asyncpg pool and subscribe to order fills."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.info("TradeJournalActor: no event loop, skipping pool creation")
            return

        self._pool_task = loop.create_task(self._create_pool())

        for ins_str in self.config.instrument_ids:
            try:
                instrument_id = InstrumentId.from_str(ins_str)
                self.subscribe_order_fills(instrument_id)
                self.log.info(
                    f"TradeJournalActor: subscribed to fills for {instrument_id}"
                )
            except Exception as exc:  # noqa: BLE001
                self.log.error(
                    "TradeJournalActor: failed to subscribe to fills for "
                    f"{ins_str}: {exc}"
                )

        if not self.config.instrument_ids:
            self.log.warning(
                "TradeJournalActor: no instrument_ids configured, "
                "fill subscription skipped"
            )

        self.log.info("TradeJournalActor: scheduled pool creation")

    async def _create_pool(self) -> None:
        """Asynchronously create the asyncpg connection pool."""
        import asyncpg

        try:
            self._pool = await asyncpg.create_pool(
                host=self.config.postgres_host,
                port=self.config.postgres_port,
                database=self.config.postgres_db,
                user=self.config.postgres_user,
                password=self.config.postgres_password,
                min_size=1,
                max_size=5,
            )
            self.log.info("TradeJournalActor: DB pool ready")
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                "TradeJournalActor: failed to create DB pool "
                f"(host={self.config.postgres_host}, "
                f"port={self.config.postgres_port}, "
                f"db={self.config.postgres_db}, "
                f"user={self.config.postgres_user}): {exc}"
            )

    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle OrderFilled by scheduling an async DB write.

        Parameters
        ----------
        event : OrderFilled
            The fill event to persist.

        """
        if self._pool is None:
            self.log.warning("TradeJournalActor: pool not ready, dropping fill")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.warning("TradeJournalActor: no event loop, dropping fill")
            return

        loop.create_task(self._persist_fill(event))

    async def _persist_fill(self, event: OrderFilled) -> None:
        """Persist a fill record (and its parent order) to PostgreSQL.

        Parameters
        ----------
        event : OrderFilled
            The fill event to persist.

        """
        if self._pool is None:
            self.log.warning("TradeJournalActor: pool closed, dropping fill")
            return

        try:
            await self._upsert_order(event)
            await self._write_fill(event)
            self.log.info(f"TradeJournalActor: persisted fill {event.trade_id}")
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"TradeJournalActor: failed to persist fill {event.trade_id}: {exc}"
            )

    async def _upsert_order(self, event: OrderFilled) -> None:
        """Upsert the parent order record to satisfy the fills FK constraint.

        Parameters
        ----------
        event : OrderFilled
            The fill event containing order metadata.

        """
        if self._pool is None:
            return

        side = (
            event.order_side.name
            if hasattr(event.order_side, "name")
            else str(event.order_side)
        )
        order_type = (
            event.order_type.name
            if hasattr(event.order_type, "name")
            else str(event.order_type)
        )
        ts_event = self._ns_to_datetime(event.ts_event)

        # Try to get the original order quantity from cache.
        order_qty = event.last_qty.as_double()
        if self.cache is not None:
            try:
                cached_order = self.cache.order(event.client_order_id)
                if cached_order is not None:
                    order_qty = cached_order.quantity.as_double()
            except Exception:  # noqa: S110, BLE001
                pass  # Fall back to fill quantity

        # Try to get the accurate order status from cache.
        status = "PARTIALLY_FILLED"
        if self.cache is not None:
            try:
                cached_order = self.cache.order(event.client_order_id)
                if cached_order is not None:
                    status = cached_order.status.name
            except Exception:  # noqa: S110, BLE001
                pass

        sql = """
            INSERT INTO orders (
                client_order_id, venue_order_id, strategy_id,
                instrument_id, venue, side, order_type, quantity,
                price, status, ts_submitted, ts_updated
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
            ON CONFLICT (client_order_id) DO NOTHING
        """

        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                str(event.client_order_id),
                str(event.venue_order_id) if event.venue_order_id else None,
                str(event.strategy_id),
                str(event.instrument_id),
                str(event.instrument_id.venue),
                side,
                order_type,
                order_qty,
                event.last_px.as_double(),
                status,
                ts_event,
            )

    async def _write_fill(self, event: OrderFilled) -> None:
        """Insert a fill record into PostgreSQL.

        Parameters
        ----------
        event : OrderFilled
            The fill event to persist.

        """
        if self._pool is None:
            self.log.warning("TradeJournalActor: pool closed, dropping fill")
            return

        side = (
            event.order_side.name
            if hasattr(event.order_side, "name")
            else str(event.order_side)
        )
        ts_event = self._ns_to_datetime(event.ts_event)
        ts_init = self._ns_to_datetime(event.ts_init)

        sql = """
            INSERT INTO fills (
                trade_id, client_order_id, venue_order_id, strategy_id, instrument_id,
                venue, side, qty, price, commission, currency, ts_event, ts_init
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (trade_id) DO NOTHING
        """

        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                str(event.trade_id),
                str(event.client_order_id),
                str(event.venue_order_id) if event.venue_order_id else None,
                str(event.strategy_id),
                str(event.instrument_id),
                str(event.instrument_id.venue),
                side,
                event.last_qty.as_double(),
                event.last_px.as_double(),
                event.commission.as_double(),
                event.currency.code,
                ts_event,
                ts_init,
            )

    @staticmethod
    def _ns_to_datetime(ns: int) -> datetime:
        """Convert a nanosecond timestamp to a timezone-aware datetime.

        Parameters
        ----------
        ns : int
            Timestamp in nanoseconds.

        Returns
        -------
        datetime
            UTC datetime.

        """
        if ns:
            return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        return datetime.now(timezone.utc)

    def on_stop(self) -> None:
        """Cancel pending tasks, unsubscribe, and close the DB pool."""
        if self._pool_task is not None and not self._pool_task.done():
            self._pool_task.cancel()

        for ins_str in self.config.instrument_ids:
            try:
                instrument_id = InstrumentId.from_str(ins_str)
                self.unsubscribe_order_fills(instrument_id)
            except Exception:  # noqa: S110
                pass

        if self._pool is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._pool.close())
                self.log.info("TradeJournalActor: scheduled pool close")
            except RuntimeError:
                self.log.info(
                    "TradeJournalActor: no event loop, cannot close pool gracefully"
                )
            finally:
                self._pool = None
