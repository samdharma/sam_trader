"""Unit tests for PositionSnapshotActor."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import LiquiditySide, OmsType, OrderSide, OrderType
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs

from sam_trader.actors.position_snapshot import (
    PositionSnapshotActor,
    PositionSnapshotActorConfig,
)


@pytest.fixture
def actor_config() -> PositionSnapshotActorConfig:
    return PositionSnapshotActorConfig(
        postgres_host="test-host",
        postgres_port=5432,
        postgres_db="test_db",
        postgres_user="test_user",
        postgres_password="test_pass",
        snapshot_interval_secs=1,
    )


@pytest.fixture
def registered_actor(
    actor_config: PositionSnapshotActorConfig,
) -> PositionSnapshotActor:
    actor = PositionSnapshotActor(actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


def _make_position(
    instrument_id: InstrumentId, side: OrderSide = OrderSide.BUY
) -> Position:
    """Create a Position from an OrderFilled event."""
    instrument = TestInstrumentProvider.equity()
    # Override instrument id to match what we want
    fill = OrderFilled(
        TraderId("SAM-001"),
        StrategyId("ORB-001"),
        instrument_id,
        ClientOrderId("O-001"),
        VenueOrderId("V-001"),
        AccountId("FUTU-001"),
        TradeId("T-001"),
        PositionId("P-001"),
        side,
        OrderType.LIMIT,
        Quantity.from_str("100"),
        Price.from_str("150.50"),
        Currency.from_str("USD"),
        Money(Decimal("1.99"), Currency.from_str("USD")),
        LiquiditySide.TAKER,
        UUID4(),
        1_700_000_000_000_000_000,
        1_700_000_000_000_000_001,
    )
    return Position(instrument=instrument, fill=fill)


class TestPositionSnapshotActorConfig:
    def test_default_values(self) -> None:
        cfg = PositionSnapshotActorConfig()
        assert cfg.postgres_host == "sam-postgres"
        assert cfg.postgres_port == 5432
        assert cfg.postgres_db == "sam_trader"
        assert cfg.postgres_user == "sam"
        assert cfg.postgres_password == "sam_secret"
        assert cfg.snapshot_interval_secs == 60
        assert cfg.instrument_ids == []

    def test_custom_values(self, actor_config: PositionSnapshotActorConfig) -> None:
        assert actor_config.postgres_host == "test-host"
        assert actor_config.snapshot_interval_secs == 1


class TestPositionSnapshotActor:
    def test_is_actor_subclass(self, actor_config: PositionSnapshotActorConfig) -> None:
        actor = PositionSnapshotActor(actor_config)
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_no_event_loop(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        registered_actor.on_start()
        assert registered_actor._pool is None
        assert registered_actor._snapshot_task is None

    def test_on_start_creates_pool_and_loop(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        async def _test() -> None:
            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
            ) as mock_pool:
                mock_pool.return_value = MagicMock()
                registered_actor.on_start()
                await asyncio.sleep(0.01)
                mock_pool.assert_awaited_once()
                assert registered_actor._snapshot_task is not None

        asyncio.run(_test())

    def test_snapshot_positions_skips_when_pool_not_ready(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        # Should not raise when pool is None.
        asyncio.run(registered_actor._snapshot_positions())

    def test_position_changed_upsert(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        """A position in cache is upserted to PostgreSQL on snapshot."""

        async def _test() -> None:
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                # Seed cache with a position and a quote tick for unrealized PnL
                pos = _make_position(InstrumentId.from_str("AAPL.XNAS"))
                registered_actor.cache.add_position(pos, OmsType.NETTING)

                quote = TestDataStubs.quote_tick(TestInstrumentProvider.equity())
                registered_actor.cache.add_quote_tick(quote)

                await registered_actor._snapshot_positions()

                assert mock_conn.execute.call_count == 1
                sql, *params = mock_conn.execute.call_args_list[0][0]
                assert params[0] == "ORB-001"  # strategy_id
                assert params[1] == "AAPL.XNAS"  # instrument_id
                assert params[2] == "XNAS"  # venue
                assert params[3] == 100  # net_quantity (signed, long)
                assert params[4] == 150.5  # avg_px
                assert isinstance(params[5], float)  # unrealized_pnl
                assert params[6] == -1.99  # realized_pnl

        asyncio.run(_test())

    def test_periodic_snapshot_poll(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        """The periodic loop triggers snapshot at least once."""

        async def _test() -> None:
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                # Seed cache
                pos = _make_position(InstrumentId.from_str("AAPL.XNAS"))
                registered_actor.cache.add_position(pos, OmsType.NETTING)

                # Wait for at least one snapshot interval (1s)
                await asyncio.sleep(1.2)

                # Should have executed at least one upsert
                assert mock_conn.execute.call_count >= 1

                # Cancel the snapshot task to avoid hanging
                if registered_actor._snapshot_task is not None:
                    registered_actor._snapshot_task.cancel()
                    try:
                        await registered_actor._snapshot_task
                    except asyncio.CancelledError:
                        pass

        asyncio.run(_test())

    def test_on_stop_closes_pool(self, registered_actor: PositionSnapshotActor) -> None:
        async def _test() -> None:
            mock_pool = AsyncMock()
            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)
                assert registered_actor._pool is not None

                registered_actor.on_stop()
                await asyncio.sleep(0.01)
                assert registered_actor._pool is None
                mock_pool.close.assert_awaited_once()

        asyncio.run(_test())

    def test_on_stop_no_loop(self, registered_actor: PositionSnapshotActor) -> None:
        # Should not raise when no event loop is running.
        registered_actor.on_stop()

    def test_filter_instrument_ids(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        """Only positions matching instrument_ids are snapshotted."""

        async def _test() -> None:
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            filter_actor = PositionSnapshotActor(
                PositionSnapshotActorConfig(
                    postgres_host="test-host",
                    postgres_port=5432,
                    postgres_db="test_db",
                    postgres_user="test_user",
                    postgres_password="test_pass",
                    instrument_ids=["MSFT.XNAS"],  # Does not match AAPL.XNAS
                )
            )
            filter_actor.register_base(
                portfolio=TestComponentStubs.portfolio(),
                msgbus=TestComponentStubs.msgbus(),
                cache=TestComponentStubs.cache(),
                clock=TestComponentStubs.clock(),
            )

            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ):
                filter_actor.on_start()
                await asyncio.sleep(0.01)

                aapl = _make_position(InstrumentId.from_str("AAPL.XNAS"))
                filter_actor.cache.add_position(aapl, OmsType.NETTING)

                await filter_actor._snapshot_positions()

                # AAPL should be skipped because filter is MSFT.XNAS
                assert mock_conn.execute.call_count == 0

        asyncio.run(_test())

    def test_short_position_negative_quantity(
        self, registered_actor: PositionSnapshotActor
    ) -> None:
        """Short positions write negative net_quantity."""

        async def _test() -> None:
            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                pos = _make_position(
                    InstrumentId.from_str("AAPL.XNAS"), side=OrderSide.SELL
                )
                registered_actor.cache.add_position(pos, OmsType.NETTING)

                await registered_actor._snapshot_positions()

                _sql, *params = mock_conn.execute.call_args_list[0][0]
                assert params[3] == -100  # net_quantity negative for short

        asyncio.run(_test())
