"""Unit tests for TradeJournalActor."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderType
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
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.actors.trade_journal import TradeJournalActor, TradeJournalActorConfig


@pytest.fixture
def actor_config() -> TradeJournalActorConfig:
    return TradeJournalActorConfig(
        postgres_host="test-host",
        postgres_port=5432,
        postgres_db="test_db",
        postgres_user="test_user",
        postgres_password="test_pass",
        instrument_ids=["TSLA.NASDAQ", "00700.HKEX"],
    )


@pytest.fixture
def registered_actor(
    actor_config: TradeJournalActorConfig,
) -> TradeJournalActor:
    actor = TradeJournalActor(actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


def _make_order_filled(instrument_id: InstrumentId) -> OrderFilled:
    return OrderFilled(
        TraderId("SAM-001"),
        StrategyId("ORB-001"),
        instrument_id,
        ClientOrderId("O-001"),
        VenueOrderId("V-001"),
        AccountId("FUTU-001"),
        TradeId("T-001"),
        PositionId("P-001"),
        OrderSide.BUY,
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


class TestTradeJournalActorConfig:
    def test_default_values(self) -> None:
        cfg = TradeJournalActorConfig()
        assert cfg.postgres_host == "sam-postgres"
        assert cfg.postgres_port == 5432
        assert cfg.postgres_db == "sam_trader"
        assert cfg.postgres_user == "sam"
        assert cfg.postgres_password == "sam_secret"
        assert cfg.instrument_ids == []

    def test_custom_values(self, actor_config: TradeJournalActorConfig) -> None:
        assert actor_config.postgres_host == "test-host"
        assert actor_config.instrument_ids == ["TSLA.NASDAQ", "00700.HKEX"]


class TestTradeJournalActor:
    def test_is_actor_subclass(self, actor_config: TradeJournalActorConfig) -> None:
        actor = TradeJournalActor(actor_config)
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_no_event_loop(self, registered_actor: TradeJournalActor) -> None:
        # When no event loop is running, on_start should log and return.
        registered_actor.on_start()
        assert registered_actor._pool is None

    def test_on_start_subscribes_to_fills(
        self, registered_actor: TradeJournalActor
    ) -> None:
        async def _test() -> None:
            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
            ) as mock_pool:
                mock_pool.return_value = MagicMock()
                registered_actor.on_start()
                # Allow the pool task to run
                await asyncio.sleep(0.01)
                mock_pool.assert_awaited_once()

        asyncio.run(_test())

    def test_on_order_filled_drops_when_pool_not_ready(
        self, registered_actor: TradeJournalActor
    ) -> None:
        fill = _make_order_filled(InstrumentId.from_str("TSLA.NASDAQ"))
        registered_actor.on_order_filled(fill)
        # Should not raise; fill is simply dropped with a log warning.

    def test_on_order_filled_persists_to_db(
        self, registered_actor: TradeJournalActor
    ) -> None:
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

                fill = _make_order_filled(InstrumentId.from_str("TSLA.NASDAQ"))
                registered_actor.on_order_filled(fill)
                await asyncio.sleep(0.05)

                # Both upsert_order and write_fill should have executed.
                assert mock_conn.execute.call_count == 2

                # Verify fill SQL parameters
                calls = mock_conn.execute.call_args_list
                # Second call is _write_fill
                _sql, *params = calls[1][0]
                assert params[0] == "T-001"  # trade_id
                assert params[1] == "O-001"  # client_order_id
                assert params[3] == "ORB-001"  # strategy_id
                assert params[4] == "TSLA.NASDAQ"  # instrument_id
                assert params[5] == "NASDAQ"  # venue
                assert params[6] == "BUY"  # side
                assert params[9] == 1.99  # commission
                assert params[10] == "USD"  # currency

        asyncio.run(_test())

    def test_venue_tagging_futu(self, registered_actor: TradeJournalActor) -> None:
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

                fill = _make_order_filled(InstrumentId.from_str("00700.HKEX"))
                registered_actor.on_order_filled(fill)
                await asyncio.sleep(0.05)

                calls = mock_conn.execute.call_args_list
                _sql, *params = calls[1][0]
                assert params[5] == "HKEX"  # venue from instrument_id

        asyncio.run(_test())

    def test_on_stop_closes_pool(self, registered_actor: TradeJournalActor) -> None:
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

    def test_on_stop_no_loop(self, registered_actor: TradeJournalActor) -> None:
        # Should not raise when no event loop is running.
        registered_actor.on_stop()

    def test_ns_to_datetime_with_zero(self) -> None:
        result = TradeJournalActor._ns_to_datetime(0)
        assert result.tzinfo is not None

    def test_ns_to_datetime_with_value(self) -> None:
        ns = 1_700_000_000_000_000_000
        result = TradeJournalActor._ns_to_datetime(ns)
        assert result.year == 2023
        assert result.tzinfo is not None
