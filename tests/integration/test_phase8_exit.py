"""Phase 8 EXIT integration tests.

Validates all Phase 8 components work together:
1. PerformanceAnalyzer writes stats to performance_stats PG table
2. PositionSnapshotActor upserts positions to PG positions table
3. LiveRiskEngine rate limits are configured and wired
4. Slippage tracking is populated for limit orders in fills table
5. sam performance CLI displays stats from performance_stats table
"""

from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal
from typing import Any
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
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from sam_trader.actors.position_snapshot import (
    PositionSnapshotActor,
    PositionSnapshotActorConfig,
)
from sam_trader.actors.trade_journal import TradeJournalActor, TradeJournalActorConfig
from sam_trader.services.cli import main
from sam_trader.services.performance_analyzer import PerformanceAnalyzer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fill_event(
    instrument_id: InstrumentId,
    order_type: OrderType = OrderType.LIMIT,
    side: OrderSide = OrderSide.BUY,
    price: str = "150.50",
) -> OrderFilled:
    return OrderFilled(
        TraderId("SAM-001"),
        StrategyId("ORB-001"),
        instrument_id,
        ClientOrderId("O-001"),
        VenueOrderId("V-001"),
        AccountId("FUTU-001"),
        TradeId("T-001"),
        PositionId("P-001"),
        side,
        order_type,
        Quantity.from_str("100"),
        Price.from_str(price),
        Currency.from_str("USD"),
        Money(Decimal("1.99"), Currency.from_str("USD")),
        LiquiditySide.TAKER,
        UUID4(),
        1_700_000_000_000_000_000,
        1_700_000_000_000_000_001,
    )


def _make_position(instrument_id: InstrumentId) -> Position:
    fill = OrderFilled(
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
    return Position(instrument=TestInstrumentProvider.equity(), fill=fill)


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPerformanceAnalyzerWritesStats:
    """AC: PerformanceAnalyzer completes and writes stats to performance_stats table."""

    def test_performance_analyzer_writes_stats(self) -> None:
        """End-to-end: fills -> compute stats -> store in PG."""

        async def _test() -> None:
            analyzer = PerformanceAnalyzer("fake-dsn")

            mock_conn = AsyncMock()
            mock_pool = MagicMock()
            mock_pool.acquire.return_value.__aenter__ = AsyncMock(
                return_value=mock_conn
            )
            mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_pool.close = AsyncMock()

            # PG returns one strategy with two fills (a closed trade)
            mock_conn.fetch.side_effect = [
                [{"strategy_id": "orb-tsla"}],  # _get_strategies
                [  # _get_fills for orb-tsla
                    {
                        "trade_id": "t1",
                        "strategy_id": "orb-tsla",
                        "instrument_id": "TSLA.NASDAQ",
                        "side": "BUY",
                        "qty": 10.0,
                        "price": 150.0,
                        "commission": 1.0,
                        "slippage": 0.0,
                        "ts_event": datetime.datetime(
                            2024, 1, 1, 10, 0, 0, tzinfo=datetime.timezone.utc
                        ),
                    },
                    {
                        "trade_id": "t2",
                        "strategy_id": "orb-tsla",
                        "instrument_id": "TSLA.NASDAQ",
                        "side": "SELL",
                        "qty": 10.0,
                        "price": 160.0,
                        "commission": 1.0,
                        "slippage": 0.0,
                        "ts_event": datetime.datetime(
                            2024, 1, 2, 10, 0, 0, tzinfo=datetime.timezone.utc
                        ),
                    },
                ],
                [],  # _get_all_fills (portfolio aggregate — empty for simplicity)
            ]

            with patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ):
                results = await analyzer.compute_and_store(lookback_days=30)

            assert "orb-tsla" in results
            stats = results["orb-tsla"]
            assert stats["WinRate"] == 1.0
            assert stats["MaxWinner"] == pytest.approx(98.0, rel=1e-3)

            # Verify PG upsert was called for each stat
            assert mock_conn.execute.call_count == len(stats)

        asyncio.run(_test())


@pytest.mark.integration
class TestPositionSnapshotActorWrites:
    """AC: positions upserted to PG positions table within 60s of position change."""

    def test_position_snapshot_actor_writes(self) -> None:
        """End-to-end: cache position -> snapshot -> PG upsert executed."""

        async def _test() -> None:
            actor = PositionSnapshotActor(
                PositionSnapshotActorConfig(
                    postgres_host="test-host",
                    postgres_port=5432,
                    postgres_db="test_db",
                    postgres_user="test_user",
                    postgres_password="test_pass",
                    snapshot_interval_secs=60,
                )
            )
            actor.register_base(
                portfolio=TestComponentStubs.portfolio(),
                msgbus=TestComponentStubs.msgbus(),
                cache=TestComponentStubs.cache(),
                clock=TestComponentStubs.clock(),
            )

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
                actor.on_start()
                await asyncio.sleep(0.01)

                pos = _make_position(InstrumentId.from_str("AAPL.XNAS"))
                actor.cache.add_position(pos, OmsType.NETTING)

                quote = TestDataStubs.quote_tick(TestInstrumentProvider.equity())
                actor.cache.add_quote_tick(quote)

                await actor._snapshot_positions()

                assert mock_conn.execute.call_count == 1
                sql, *params = mock_conn.execute.call_args_list[0][0]
                assert params[0] == "ORB-001"  # strategy_id
                assert params[1] == "AAPL.XNAS"  # instrument_id
                assert params[2] == "XNAS"  # venue
                assert params[3] == 100  # net_quantity
                assert params[4] == 150.5  # avg_px
                assert isinstance(params[5], float)  # unrealized_pnl
                assert params[6] == -1.99  # realized_pnl

        asyncio.run(_test())


@pytest.mark.integration
class TestLiveRiskEngineRateLimit:
    """AC: rate limits enforced — submitting >100 orders/sec triggers rejection."""

    def test_live_risk_engine_rate_limit_configured(self) -> None:
        """LiveRiskEngine is wired with default rate limit of 100/00:00:01."""
        import asyncio

        from nautilus_trader.live.node import TradingNode

        from sam_trader.main import build_trading_node

        # Set minimal env so node builds
        env_overrides = {
            "IB_ENABLED": "false",
            "FUTU_ENABLED": "false",
            "BUNDLES_PATH": "config/nonexistent_bundles.yaml",
            "STATE_SAVE_ENABLED": "false",
            "STATE_LOAD_ENABLED": "false",
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for key, value in env_overrides.items():
                import os

                os.environ[key] = value

            node = build_trading_node()
            assert isinstance(node, TradingNode)

            risk_cfg = node._config.risk_engine
            assert risk_cfg is not None
            assert risk_cfg.max_order_submit_rate == "100/00:00:01"
            assert risk_cfg.max_order_modify_rate == "100/00:00:01"
            assert risk_cfg.bypass is False
        finally:
            loop.close()
            asyncio.set_event_loop(None)


@pytest.mark.integration
class TestSlippageTracking:
    """AC: fills table has slippage column populated for limit orders."""

    def test_slippage_tracking(self) -> None:
        """LIMIT order fill produces signed slippage in the PG INSERT."""

        async def _test() -> None:
            actor = TradeJournalActor(
                TradeJournalActorConfig(
                    postgres_host="test-host",
                    postgres_port=5432,
                    postgres_db="test_db",
                    postgres_user="test_user",
                    postgres_password="test_pass",
                    instrument_ids=["TSLA.NASDAQ"],
                )
            )
            actor.register_base(
                portfolio=TestComponentStubs.portfolio(),
                msgbus=TestComponentStubs.msgbus(),
                cache=TestComponentStubs.cache(),
                clock=TestComponentStubs.clock(),
            )

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
                actor.on_start()
                await asyncio.sleep(0.01)

                # Seed cache with a LIMIT order at 150.00
                limit_order = TestExecStubs.limit_order(
                    price=Price.from_str("150.00"),
                    client_order_id=ClientOrderId("O-001"),
                )
                actor.cache.add_order(limit_order, None, None)

                # Fill at 150.50 (worse for BUY side => +0.50 slippage)
                fill = _make_fill_event(
                    InstrumentId.from_str("TSLA.NASDAQ"),
                    order_type=OrderType.LIMIT,
                    side=OrderSide.BUY,
                    price="150.50",
                )
                actor.on_order_filled(fill)
                await asyncio.sleep(0.05)

                # Both upsert_order and write_fill executed
                assert mock_conn.execute.call_count == 2

                # Second call is _write_fill — verify slippage param
                calls = mock_conn.execute.call_args_list
                _sql, *params = calls[1][0]
                assert params[11] == 0.5  # slippage = 150.50 - 150.00

        asyncio.run(_test())


@pytest.mark.integration
class TestSamPerformanceCli:
    """AC: sam performance --days 1 displays stats from performance_stats table."""

    @patch("sam_trader.services.cli.asyncpg.connect")
    def test_sam_performance_cli(self, mock_connect: Any) -> None:
        """CLI queries PG and renders formatted table output."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "strategy_id": "orb-tsla",
                "stat_name": "SharpeRatio",
                "stat_value": Decimal("1.2345"),
            },
            {
                "strategy_id": "orb-tsla",
                "stat_name": "WinRate",
                "stat_value": Decimal("0.65"),
            },
        ]
        mock_connect.return_value = mock_conn

        rc = main(["performance", "--days", "1"])
        assert rc == 0

        # Verify the query used the correct days parameter
        calls = mock_conn.fetch.call_args_list
        assert len(calls) == 1
        assert calls[0][0][1] == 1  # days param

    @patch("sam_trader.services.cli.asyncpg.connect")
    def test_sam_performance_cli_json(self, mock_connect: Any) -> None:
        """CLI --json emits structured output."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {
                "strategy_id": "orb-tsla",
                "stat_name": "CAGR",
                "stat_value": Decimal("0.12"),
            },
        ]
        mock_connect.return_value = mock_conn

        rc = main(["--json", "performance", "--days", "7"])
        assert rc == 0
