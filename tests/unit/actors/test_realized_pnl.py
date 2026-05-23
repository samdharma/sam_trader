"""Unit tests for RealizedPnLTrackerActor."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

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

from sam_trader.actors.realized_pnl import (
    RealizedPnLTrackerActor,
    RealizedPnLTrackerActorConfig,
)


def _make_order_filled(
    instrument_id: InstrumentId,
    strategy_id: str = "ORB-001",
    side: OrderSide = OrderSide.BUY,
    qty: str = "100",
    price: str = "150.50",
    ts_event: int = 1_700_000_000_000_000_000,
) -> OrderFilled:
    return OrderFilled(
        TraderId("SAM-001"),
        StrategyId(strategy_id),
        instrument_id,
        ClientOrderId("O-001"),
        VenueOrderId("V-001"),
        AccountId("FUTU-001"),
        TradeId("T-001"),
        PositionId("P-001"),
        side,
        OrderType.LIMIT,
        Quantity.from_str(qty),
        Price.from_str(price),
        Currency.from_str("USD"),
        Money(Decimal("1.99"), Currency.from_str("USD")),
        LiquiditySide.TAKER,
        UUID4(),
        ts_event,
        ts_event + 1,
    )


@pytest.fixture
def actor_config() -> RealizedPnLTrackerActorConfig:
    return RealizedPnLTrackerActorConfig(
        redis_host="test-redis",
        redis_port=6379,
        redis_password="",
        redis_db=0,
        key_prefix="sam:pnl",
        instrument_ids=["TSLA.NASDAQ", "AAPL.NASDAQ"],
    )


@pytest.fixture
def registered_actor(
    actor_config: RealizedPnLTrackerActorConfig,
) -> RealizedPnLTrackerActor:
    actor = RealizedPnLTrackerActor(actor_config)
    actor.register_base(
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )
    return actor


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.set = AsyncMock(return_value=True)
    redis.close = AsyncMock(return_value=None)
    return redis


class TestRealizedPnLTrackerActorConfig:
    def test_default_values(self) -> None:
        cfg = RealizedPnLTrackerActorConfig()
        assert cfg.redis_host == "sam-redis"
        assert cfg.redis_port == 6379
        assert cfg.redis_password == ""
        assert cfg.redis_db == 0
        assert cfg.key_prefix == "sam:pnl"
        assert cfg.instrument_ids == []

    def test_custom_values(self, actor_config: RealizedPnLTrackerActorConfig) -> None:
        assert actor_config.redis_host == "test-redis"
        assert actor_config.instrument_ids == ["TSLA.NASDAQ", "AAPL.NASDAQ"]


class TestRealizedPnLTrackerActor:
    def test_is_actor_subclass(
        self, actor_config: RealizedPnLTrackerActorConfig
    ) -> None:
        actor = RealizedPnLTrackerActor(actor_config)
        from nautilus_trader.common.actor import Actor

        assert isinstance(actor, Actor)

    def test_on_start_no_event_loop(
        self, registered_actor: RealizedPnLTrackerActor
    ) -> None:
        registered_actor.on_start()
        assert registered_actor._redis is None

    def test_on_start_subscribes_to_fills(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)
                mock_redis.ping.assert_awaited_once()

        asyncio.run(_test())

    def test_on_order_filled_drops_when_redis_not_ready(
        self, registered_actor: RealizedPnLTrackerActor
    ) -> None:
        fill = _make_order_filled(InstrumentId.from_str("TSLA.NASDAQ"))
        registered_actor.on_order_filled(fill)
        # Should not raise; fill is dropped with a log warning.

    def test_buy_fill_opens_long_lot(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                fill = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                )
                registered_actor.on_order_filled(fill)
                await asyncio.sleep(0.01)

                key = ("ORB-001", "TSLA.NASDAQ")
                assert key in registered_actor._lots
                assert len(registered_actor._lots[key]) == 1
                lot = registered_actor._lots[key][0]
                assert lot[0] == Decimal("150.00")
                assert lot[1] == Decimal("100")
                assert lot[2] == 1  # long side
                assert registered_actor._realized.get(key, Decimal("0")) == Decimal("0")

        asyncio.run(_test())

    def test_sell_fill_closes_long_lot_realizes_profit(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                buy = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                )
                sell = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.SELL,
                    qty="100",
                    price="160.00",
                )
                registered_actor.on_order_filled(buy)
                registered_actor.on_order_filled(sell)
                await asyncio.sleep(0.02)

                key = ("ORB-001", "TSLA.NASDAQ")
                assert len(registered_actor._lots.get(key, [])) == 0
                assert registered_actor._realized[key] == Decimal("1000.00")
                assert registered_actor.get_realized_pnl("ORB-001") == Decimal(
                    "1000.00"
                )

        asyncio.run(_test())

    def test_sell_fill_closes_long_lot_realizes_loss(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                buy = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                )
                sell = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.SELL,
                    qty="100",
                    price="140.00",
                )
                registered_actor.on_order_filled(buy)
                registered_actor.on_order_filled(sell)
                await asyncio.sleep(0.02)

                assert registered_actor.get_realized_pnl("ORB-001") == Decimal(
                    "-1000.00"
                )

        asyncio.run(_test())

    def test_partial_sell_matches_fifo(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                # Two buys at different prices
                buy1 = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="50",
                    price="100.00",
                )
                buy2 = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="50",
                    price="110.00",
                )
                # Sell 75 — should match 50 from buy1 and 25 from buy2
                sell = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.SELL,
                    qty="75",
                    price="120.00",
                )
                registered_actor.on_order_filled(buy1)
                registered_actor.on_order_filled(buy2)
                registered_actor.on_order_filled(sell)
                await asyncio.sleep(0.03)

                key = ("ORB-001", "TSLA.NASDAQ")
                lots = registered_actor._lots[key]
                assert len(lots) == 1
                # Remaining lot is from buy2: 50 - 25 = 25 shares at $110
                assert lots[0][0] == Decimal("110.00")
                assert lots[0][1] == Decimal("25")
                assert lots[0][2] == 1

                # Realized = (120-100)*50 + (120-110)*25 = 1000 + 250 = 1250
                assert registered_actor.get_realized_pnl("ORB-001") == Decimal(
                    "1250.00"
                )

        asyncio.run(_test())

    def test_short_sell_then_buy_back_realizes_profit(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                # Short sell at $160
                short = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.SELL,
                    qty="100",
                    price="160.00",
                )
                # Buy back at $150
                cover = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                )
                registered_actor.on_order_filled(short)
                registered_actor.on_order_filled(cover)
                await asyncio.sleep(0.02)

                key = ("ORB-001", "TSLA.NASDAQ")
                assert len(registered_actor._lots.get(key, [])) == 0
                # Realized = (150-160)*100*(-1) = (-10)*100*(-1) = 1000
                assert registered_actor._realized[key] == Decimal("1000.00")

        asyncio.run(_test())

    def test_multiple_strategies_isolated(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                strat_a_buy = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    strategy_id="ORB-A",
                    side=OrderSide.BUY,
                    qty="100",
                    price="100.00",
                )
                strat_a_sell = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    strategy_id="ORB-A",
                    side=OrderSide.SELL,
                    qty="100",
                    price="110.00",
                )
                strat_b_buy = _make_order_filled(
                    InstrumentId.from_str("AAPL.NASDAQ"),
                    strategy_id="ORB-B",
                    side=OrderSide.BUY,
                    qty="100",
                    price="200.00",
                )
                strat_b_sell = _make_order_filled(
                    InstrumentId.from_str("AAPL.NASDAQ"),
                    strategy_id="ORB-B",
                    side=OrderSide.SELL,
                    qty="100",
                    price="190.00",
                )
                registered_actor.on_order_filled(strat_a_buy)
                registered_actor.on_order_filled(strat_a_sell)
                registered_actor.on_order_filled(strat_b_buy)
                registered_actor.on_order_filled(strat_b_sell)
                await asyncio.sleep(0.04)

                assert registered_actor.get_realized_pnl("ORB-A") == Decimal("1000.00")
                assert registered_actor.get_realized_pnl("ORB-B") == Decimal("-1000.00")

        asyncio.run(_test())

    def test_persists_to_redis(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                buy = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                )
                sell = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.SELL,
                    qty="100",
                    price="160.00",
                )
                registered_actor.on_order_filled(buy)
                registered_actor.on_order_filled(sell)
                await asyncio.sleep(0.02)

                mock_redis.set.assert_awaited()
                calls = mock_redis.set.call_args_list
                # Last call should have the total P&L
                last_call = calls[-1]
                assert last_call[0][0] == "sam:pnl:ORB-001:2023-11-14"
                assert last_call[0][1] == "1000.00"

        asyncio.run(_test())

    def test_day_rollover_resets_state(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)

                # Fill on day 1
                buy_d1 = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                    ts_event=1_700_000_000_000_000_000,  # 2023-11-14
                )
                sell_d1 = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.SELL,
                    qty="100",
                    price="160.00",
                    ts_event=1_700_000_000_000_000_000,
                )
                registered_actor.on_order_filled(buy_d1)
                registered_actor.on_order_filled(sell_d1)
                await asyncio.sleep(0.02)

                assert registered_actor.get_realized_pnl("ORB-001") == Decimal(
                    "1000.00"
                )

                # Fill on day 2 — should reset
                buy_d2 = _make_order_filled(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    side=OrderSide.BUY,
                    qty="100",
                    price="150.00",
                    ts_event=1_700_086_400_000_000_000,  # +1 day
                )
                registered_actor.on_order_filled(buy_d2)
                await asyncio.sleep(0.01)

                # After reset, realized should be 0 (new lot opened, not closed)
                assert registered_actor.get_realized_pnl("ORB-001") == Decimal("0")
                assert len(registered_actor._lots) == 1

        asyncio.run(_test())

    def test_on_stop_unsubscribes_and_closes_redis(
        self,
        registered_actor: RealizedPnLTrackerActor,
        mock_redis: AsyncMock,
    ) -> None:
        async def _test() -> None:
            with patch(
                "sam_trader.actors.realized_pnl.aioredis.Redis",
                return_value=mock_redis,
            ):
                registered_actor.on_start()
                await asyncio.sleep(0.01)
                assert registered_actor._redis is not None

                registered_actor.on_stop()
                await asyncio.sleep(0.01)
                assert registered_actor._redis is None
                mock_redis.close.assert_awaited_once()

        asyncio.run(_test())

    def test_ns_to_date_with_zero(self) -> None:
        result = RealizedPnLTrackerActor._ns_to_date(0)
        assert len(result) == 10  # YYYY-MM-DD

    def test_ns_to_date_with_value(self) -> None:
        ns = 1_700_000_000_000_000_000
        result = RealizedPnLTrackerActor._ns_to_date(ns)
        assert result == "2023-11-14"
