"""PositionSnapshotActor — periodically snapshots Nautilus positions to PostgreSQL."""

from __future__ import annotations

import asyncio
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.objects import Price


class PositionSnapshotActorConfig(ActorConfig, frozen=True):
    """Configuration for the PositionSnapshotActor.

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
    snapshot_interval_secs : int
        Seconds between position snapshots.
    instrument_ids : list[str]
        Instrument IDs to filter snapshots (empty = all).

    """

    postgres_host: str = "sam-postgres"
    postgres_port: int = 5432
    postgres_db: str = "sam_trader"
    postgres_user: str = "sam"
    postgres_password: str = "sam_secret"
    snapshot_interval_secs: int = 60
    instrument_ids: list[str] = []


class PositionSnapshotActor(Actor):
    """Actor that periodically snapshots positions to PostgreSQL via asyncpg.

    Polls ``self.cache.positions()`` every ``snapshot_interval_secs`` and
    UPSERTs into the existing ``positions`` table.

    Parameters
    ----------
    config : PositionSnapshotActorConfig
        Actor configuration.

    """

    def __init__(self, config: PositionSnapshotActorConfig):
        super().__init__(config)
        self._pool: Any = None
        self._pool_task: asyncio.Task | None = None
        self._snapshot_task: asyncio.Task | None = None

    def on_start(self) -> None:
        """Create the asyncpg pool and start the periodic snapshot loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.info(
                "PositionSnapshotActor: no event loop, skipping pool creation"
            )
            return

        self._pool_task = loop.create_task(self._create_pool())
        self._snapshot_task = loop.create_task(self._snapshot_loop())
        self.log.info(
            "PositionSnapshotActor: scheduled pool creation and snapshot loop"
        )

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
            self.log.info("PositionSnapshotActor: DB pool ready")
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                "PositionSnapshotActor: failed to create DB pool "
                f"(host={self.config.postgres_host}, "
                f"port={self.config.postgres_port}, "
                f"db={self.config.postgres_db}, "
                f"user={self.config.postgres_user}): {exc}"
            )

    async def _snapshot_loop(self) -> None:
        """Periodic loop that snapshots positions to PostgreSQL."""
        while True:
            await asyncio.sleep(self.config.snapshot_interval_secs)
            await self._snapshot_positions()

    async def _snapshot_positions(self) -> None:
        """Upsert current positions from ``self.cache.positions()`` into PG."""
        if self._pool is None:
            self.log.warning("PositionSnapshotActor: pool not ready, skipping snapshot")
            return

        if self.cache is None:
            self.log.warning(
                "PositionSnapshotActor: cache not available, skipping snapshot"
            )
            return

        try:
            positions = self.cache.positions()
        except Exception as exc:  # noqa: BLE001
            self.log.error(
                f"PositionSnapshotActor: failed to read positions from cache: {exc}"
            )
            return

        if not positions:
            self.log.debug("PositionSnapshotActor: no positions to snapshot")
            return

        filter_ids = set(self.config.instrument_ids)

        for pos in positions:
            instrument_id_str = str(pos.instrument_id)
            if filter_ids and instrument_id_str not in filter_ids:
                continue

            # Determine a reference price for unrealized PnL.
            unrealized = 0.0
            try:
                quote = self.cache.quote_tick(pos.instrument_id)
                if quote is not None:
                    mid_px = (
                        quote.ask_price.as_double() + quote.bid_price.as_double()
                    ) / 2
                    unrealized = pos.unrealized_pnl(
                        Price.from_str(str(mid_px))
                    ).as_double()
            except Exception:  # noqa: S110, BLE001
                pass  # Unrealized PnL remains 0.0 if no price available

            net_quantity = pos.signed_decimal_qty()
            avg_px = pos.avg_px_open
            realized = pos.realized_pnl.as_double()
            strategy_id = str(pos.strategy_id)
            venue = str(pos.venue)

            sql = """
                INSERT INTO positions (
                    strategy_id, instrument_id, venue,
                    net_quantity, avg_px, unrealized_pnl, realized_pnl, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (strategy_id, instrument_id, venue)
                DO UPDATE SET
                    net_quantity = $4,
                    avg_px = $5,
                    unrealized_pnl = $6,
                    realized_pnl = $7,
                    updated_at = NOW()
            """

            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        sql,
                        strategy_id,
                        instrument_id_str,
                        venue,
                        net_quantity,
                        avg_px,
                        unrealized,
                        realized,
                    )
                self.log.debug(
                    f"PositionSnapshotActor: upserted {instrument_id_str} "
                    f"qty={net_quantity} avg_px={avg_px}"
                )
            except Exception as exc:  # noqa: BLE001
                self.log.error(
                    "PositionSnapshotActor: failed to upsert position "
                    f"{instrument_id_str}: {exc}"
                )

    def on_stop(self) -> None:
        """Cancel pending tasks and close the DB pool."""
        if self._pool_task is not None and not self._pool_task.done():
            self._pool_task.cancel()

        if self._snapshot_task is not None and not self._snapshot_task.done():
            self._snapshot_task.cancel()

        if self._pool is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._pool.close())
                self.log.info("PositionSnapshotActor: scheduled pool close")
            except RuntimeError:
                self.log.info(
                    "PositionSnapshotActor: no event loop, cannot close pool gracefully"
                )
            finally:
                self._pool = None
