"""Integration test: OrbStrategy full lifecycle with Futu bundle.

Validates the Phase 7 exit criteria:
1. Load OrbStrategy bundle for TSLA.NASDAQ (Futu)
2. Strategy starts, receives bar data, detects breakout
3. Submits bracket order, fills journaled to PostgreSQL
4. Strategy state persists across restart
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    AccountType,
    LiquiditySide,
    OmsType,
    OrderSide,
    OrderType,
)
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
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.trading.config import StrategyFactory

from sam_trader.actors.trade_journal import TradeJournalActor, TradeJournalActorConfig
from sam_trader.bundle_loader import load_bundles
from sam_trader.strategies.orb import OrbStrategy, OrbStrategyConfig

BUNDLES_YAML = """\
bundles:
  - id: "tsla-orb-15m-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 10
        trade_size: 10
        confirmation_bars: 1
        entry_order_type: MARKET
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
    risk:
      max_position: 500
      max_daily_loss: 1000
"""


def _make_bar(
    bar_type: BarType,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    ts: int,
) -> Bar:
    return Bar(
        bar_type=bar_type,
        open=Price.from_str(open_p),
        high=Price.from_str(high_p),
        low=Price.from_str(low_p),
        close=Price.from_str(close_p),
        volume=Quantity.from_int(1000),
        ts_event=ts,
        ts_init=ts + 1,
    )


def _make_equity(instrument_id_str: str) -> Equity:
    instrument_id = InstrumentId.from_str(instrument_id_str)
    return Equity(
        instrument_id=instrument_id,
        raw_symbol=instrument_id.symbol,
        currency=Currency.from_str("USD"),
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )


def _make_order_filled_event(instrument_id: InstrumentId) -> OrderFilled:
    from nautilus_trader.core.uuid import UUID4

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
        OrderType.MARKET,
        Quantity.from_int(10),
        Price.from_str("102.80"),
        Currency.from_str("USD"),
        Money(Decimal("1.00"), Currency.from_str("USD")),
        LiquiditySide.TAKER,
        UUID4(),
        1_704_067_200_000_000_000,
        1_704_067_200_000_000_001,
    )


@pytest.mark.integration
class TestOrbStrategyLifecycle:
    def test_orb_bundle_loads_for_futu(self, tmp_path: Path) -> None:
        """AC1: OrbStrategy bundle loads for TSLA.NASDAQ (Futu)."""
        bundles_path = tmp_path / "bundles.yaml"
        bundles_path.write_text(BUNDLES_YAML)

        configs = load_bundles(bundles_path)
        assert len(configs) == 1

        cfg = configs[0]
        assert cfg.config["instrument_id"] == "TSLA.NASDAQ"
        assert cfg.config["venue"] == "FUTU"
        assert cfg.config["futu_code"] == "US.TSLA"
        assert cfg.config["bundle_id"] == "tsla-orb-15m-futu"
        assert cfg.config["stop_loss_ticks"] == 10
        assert cfg.config["take_profit_ticks"] == 30
        assert cfg.config["max_position"] == 500
        assert cfg.config["max_daily_loss"] == 1000

    def test_strategy_detects_breakout_and_submits_bracket(
        self,
        tmp_path: Path,
    ) -> None:
        """AC2+AC3: Strategy receives bars, detects breakout, submits bracket order."""
        bundles_path = tmp_path / "bundles.yaml"
        bundles_path.write_text(BUNDLES_YAML)
        configs = load_bundles(bundles_path)

        engine = BacktestEngine()
        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        bar_type = BarType.from_str("TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL")

        engine.add_venue(
            venue=instrument_id.venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(100_000, Currency.from_str("USD"))],
        )

        equity = _make_equity("TSLA.NASDAQ")
        engine.add_instrument(equity)

        strategy = StrategyFactory.create(configs[0])
        engine.add_strategy(strategy)

        # Feed 3 bars: 2 to establish range, 1 to breakout
        base_ts = 1_704_067_200_000_000_000
        bars = [
            _make_bar(
                bar_type,
                "100.00",
                "101.00",
                "99.50",
                "100.00",
                base_ts,
            ),
            _make_bar(
                bar_type,
                "100.50",
                "102.00",
                "100.00",
                "101.00",
                base_ts + 300_000_000_000,
            ),
            _make_bar(
                bar_type,
                "102.50",
                "103.00",
                "102.00",
                "102.80",
                base_ts + 600_000_000_000,
            ),
        ]
        engine.add_data(bars)
        engine.run()

        # Verify orders were submitted (3 bracket + optional position close)
        orders = list(engine.cache.orders())
        assert len(orders) >= 3

        # Find bracket orders by tags
        entry_orders = [o for o in orders if "ENTRY" in (o.tags or [])]
        sl_orders = [o for o in orders if "STOP_LOSS" in (o.tags or [])]
        tp_orders = [o for o in orders if "TAKE_PROFIT" in (o.tags or [])]

        assert len(entry_orders) == 1
        assert len(sl_orders) == 1
        assert len(tp_orders) == 1

        entry_order = entry_orders[0]
        sl_order = sl_orders[0]
        tp_order = tp_orders[0]

        assert entry_order.side == OrderSide.BUY
        assert sl_order.side == OrderSide.SELL
        assert tp_order.side == OrderSide.SELL

        # Verify entry filled (MARKET fills immediately)
        entry_fills = [e for e in entry_order.events if "Fill" in type(e).__name__]
        assert len(entry_fills) == 1

        # SL and TP may be CANCELED when on_stop fires at backtest end
        assert sl_order.status_string() in ("ACCEPTED", "CANCELED")
        assert tp_order.status_string() in ("ACCEPTED", "CANCELED")

        engine.dispose()

    def test_fills_journaled_to_postgresql(self) -> None:
        """AC3: OrderFilled events are journaled to PostgreSQL with venue tag."""
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
                actor.on_start()
                await asyncio.sleep(0.01)

                fill = _make_order_filled_event(InstrumentId.from_str("TSLA.NASDAQ"))
                actor.on_order_filled(fill)
                await asyncio.sleep(0.05)

                # Both upsert_order and write_fill should have executed
                assert mock_conn.execute.call_count == 2

                # Verify fill SQL parameters
                calls = mock_conn.execute.call_args_list
                _sql, *params = calls[1][0]
                assert params[0] == "T-001"  # trade_id
                assert params[3] == "ORB-001"  # strategy_id
                assert params[4] == "TSLA.NASDAQ"  # instrument_id
                assert params[5] == "NASDAQ"  # venue from instrument_id
                assert params[6] == "BUY"  # side
                assert params[9] == 1.0  # commission
                assert params[10] == "USD"  # currency

        asyncio.run(_test())

    def test_state_persists_across_restart(self) -> None:
        """AC4: Strategy state persists across restart."""
        config = OrbStrategyConfig(
            instrument_id="TSLA.NASDAQ",
            bar_type="TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL",
            first_candle_minutes=10,
            trade_size=10,
            confirmation_bars=1,
        )
        strategy = OrbStrategy(config)

        strategy.register(
            trader_id=TestIdStubs.trader_id(),
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )

        instrument = _make_equity("TSLA.NASDAQ")
        strategy.cache.add_instrument(instrument)
        strategy.start()

        # Feed bars to establish range
        bar_type = BarType.from_str("TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL")
        base_ts = 1_704_067_200_000_000_000
        strategy.on_bar(
            _make_bar(bar_type, "100.00", "101.00", "99.50", "100.00", base_ts)
        )
        strategy.on_bar(
            _make_bar(
                bar_type,
                "100.50",
                "102.00",
                "100.00",
                "101.00",
                base_ts + 300_000_000_000,
            )
        )

        assert strategy._range_established is True
        assert strategy._range_high == 102.0
        assert strategy._range_low == 99.5
        assert strategy._bars_seen == 2

        # Save state
        state = strategy.on_save()
        assert "state" in state
        assert isinstance(state["state"], bytes)

        # Create new strategy and load state
        strategy2 = OrbStrategy(config)
        strategy2.register(
            trader_id=TestIdStubs.trader_id(),
            portfolio=TestComponentStubs.portfolio(),
            msgbus=TestComponentStubs.msgbus(),
            cache=TestComponentStubs.cache(),
            clock=TestComponentStubs.clock(),
        )
        strategy2.cache.add_instrument(instrument)
        strategy2.start()

        assert strategy2._range_established is False
        assert strategy2._range_high is None

        strategy2.on_load(state)
        assert strategy2._range_established is True
        assert strategy2._range_high == 102.0
        assert strategy2._range_low == 99.5
        assert strategy2._bars_seen == 2
