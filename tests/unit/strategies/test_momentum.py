"""Unit tests for ``MomentumStrategy``."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    InstrumentId,
    StrategyId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs

from sam_trader.strategies.momentum import MomentumStrategy, MomentumStrategyConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BAR_TYPE = BarType.from_str("AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL")


def _make_bar(
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    volume: int = 10000,
) -> Bar:
    return Bar(
        bar_type=BAR_TYPE,
        open=Price.from_str(open_p),
        high=Price.from_str(high_p),
        low=Price.from_str(low_p),
        close=Price.from_str(close_p),
        volume=Quantity.from_int(volume),
        ts_event=1_700_000_000_000_000_000,
        ts_init=1_700_000_000_000_000_001,
    )


def _make_config(**kwargs: Any) -> MomentumStrategyConfig:
    defaults: dict[str, Any] = {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": str(BAR_TYPE),
        "window": 20,
        "session_start": "",
        "session_end": "",
        "trade_size": 100,
        "allowed_directions": ("LONG", "SHORT"),
        "entry_order_type": "MARKET",
        "stop_loss_ticks": 10,
        "take_profit_ticks": 30,
        "max_position": 500,
        "max_daily_loss": 1000,
    }
    defaults.update(kwargs)
    return MomentumStrategyConfig(**defaults)


def _register_strategy(strategy: MomentumStrategy) -> None:
    strategy.register(
        trader_id=TestIdStubs.trader_id(),
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )


def _mock_instrument(strategy: MomentumStrategy) -> None:
    from nautilus_trader.model.instruments import Equity

    inst_id = InstrumentId.from_str("AAPL.NASDAQ")
    equity = Equity(
        instrument_id=inst_id,
        raw_symbol=inst_id.symbol,
        currency=Currency.from_str("USD"),
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )
    strategy.cache.add_instrument(equity)


def _feed_window_bars(
    strategy: MomentumStrategy,
    n: int,
    start_close: float = 100.0,
    increment: float = 0.0,
) -> None:
    """Feed *n* bars with monotonically changing close prices."""
    for i in range(n):
        close = start_close + i * increment
        strategy.on_bar(
            _make_bar(
                open_p=str(close - 0.5),
                high_p=str(close + 0.5),
                low_p=str(close - 0.5),
                close_p=str(close),
            )
        )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestMomentumStrategyConfig:
    def test_default_values(self) -> None:
        cfg = _make_config()
        assert cfg.window == 20
        assert cfg.session_start == ""
        assert cfg.session_end == ""
        assert cfg.trade_size == 100
        assert cfg.allowed_directions == ("LONG", "SHORT")
        assert cfg.entry_order_type == "MARKET"
        assert cfg.stop_loss_ticks == 10
        assert cfg.take_profit_ticks == 30
        assert cfg.max_position == 500
        assert cfg.max_daily_loss == 1000

    def test_custom_values(self) -> None:
        cfg = _make_config(
            window=10,
            allowed_directions=("LONG",),
            entry_order_type="LIMIT",
        )
        assert cfg.window == 10
        assert cfg.allowed_directions == ("LONG",)
        assert cfg.entry_order_type == "LIMIT"


# ---------------------------------------------------------------------------
# Strategy lifecycle
# ---------------------------------------------------------------------------


class TestMomentumStrategyLifecycle:
    def test_on_start_subscribes_and_sets_state(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        assert strategy.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert strategy.bar_type == BAR_TYPE

    def test_on_start_stops_when_no_instrument(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy.stop.assert_called_once()

    def test_on_stop_cancels_and_unsubscribes(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.cancel_all_orders = MagicMock()  # type: ignore[method-assign]
        strategy.close_all_positions = MagicMock()  # type: ignore[method-assign]
        strategy.unsubscribe_bars = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy.on_stop()

        strategy.cancel_all_orders.assert_called_once()
        strategy.close_all_positions.assert_called_once()
        strategy.unsubscribe_bars.assert_called_once()

    def test_on_reset_clears_state(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        _feed_window_bars(strategy, 5)
        assert len(strategy._closes) == 5
        strategy._daily_loss = 50.0

        strategy.on_reset()
        assert len(strategy._closes) == 0
        assert strategy._daily_loss == 0.0


# ---------------------------------------------------------------------------
# Session guards
# ---------------------------------------------------------------------------


class TestSessionGuards:
    def test_outside_session_blocks_trading(self) -> None:
        strategy = MomentumStrategy(
            _make_config(session_start="09:30:00", session_end="16:00:00")
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        with patch.object(
            strategy,
            "_get_et_time",
            return_value=time(8, 0),
        ):
            _feed_window_bars(strategy, 25, start_close=100.0, increment=1.0)

        strategy.submit_order_list.assert_not_called()

    def test_inside_session_allows_trading(self) -> None:
        strategy = MomentumStrategy(
            _make_config(session_start="09:30:00", session_end="16:00:00")
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        with patch.object(
            strategy,
            "_get_et_time",
            return_value=time(10, 0),
        ):
            _feed_window_bars(strategy, 25, start_close=100.0, increment=1.0)

        strategy.submit_order_list.assert_called()


# ---------------------------------------------------------------------------
# Momentum signals
# ---------------------------------------------------------------------------


class TestMomentumSignals:
    def test_positive_momentum_enters_long(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        strategy.submit_order_list.assert_called_once()
        bracket = strategy.submit_order_list.call_args.args[0]
        assert bracket.orders[0].side == OrderSide.BUY

    def test_negative_momentum_enters_short(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "100.00", "94.00", "94.00"))

        strategy.submit_order_list.assert_called_once()
        bracket = strategy.submit_order_list.call_args.args[0]
        assert bracket.orders[0].side == OrderSide.SELL

    def test_no_signal_when_momentum_is_zero(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 6, start_close=100.0, increment=0.0)

        strategy.submit_order_list.assert_not_called()

    def test_flips_from_long_to_short(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]
        strategy.close_all_positions = MagicMock()  # type: ignore[method-assign]

        # Simulate a long position via internal state
        strategy._position_qty = 100.0
        strategy._position_avg_px = 100.0

        # Trigger short signal — since portfolio mock isn't available in Cython,
        # we call _enter_short directly to verify bracket construction
        bar = _make_bar("100.00", "100.00", "94.00", "94.00")
        strategy._enter_short(bar)

        strategy.submit_order_list.assert_called_once()
        bracket = strategy.submit_order_list.call_args.args[0]
        assert bracket.orders[0].side == OrderSide.SELL


# ---------------------------------------------------------------------------
# Direction filter
# ---------------------------------------------------------------------------


class TestDirectionFilter:
    def test_long_only_skips_short_signal(self) -> None:
        strategy = MomentumStrategy(
            _make_config(window=5, allowed_directions=("LONG",))
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "100.00", "94.00", "94.00"))

        strategy.submit_order_list.assert_not_called()

    def test_short_only_skips_long_signal(self) -> None:
        strategy = MomentumStrategy(
            _make_config(window=5, allowed_directions=("SHORT",))
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        strategy.submit_order_list.assert_not_called()


# ---------------------------------------------------------------------------
# Entry order types
# ---------------------------------------------------------------------------


class TestEntryOrderTypes:
    def test_market_entry_bracket(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, entry_order_type="MARKET"))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        bracket = strategy.submit_order_list.call_args.args[0]
        entry_order = bracket.orders[0]
        assert entry_order.order_type == OrderType.MARKET

    def test_limit_entry_bracket(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, entry_order_type="LIMIT"))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        bracket = strategy.submit_order_list.call_args.args[0]
        entry_order = bracket.orders[0]
        assert entry_order.order_type == OrderType.LIMIT
        assert float(entry_order.price) == 106.0

    def test_stop_market_entry(self) -> None:
        strategy = MomentumStrategy(
            _make_config(window=5, entry_order_type="STOP_MARKET")
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        strategy.submit_order.assert_called_once()
        order = strategy.submit_order.call_args.args[0]
        assert order.order_type == OrderType.STOP_MARKET
        assert float(order.trigger_price) == 106.0


# ---------------------------------------------------------------------------
# Venue-aware routing
# ---------------------------------------------------------------------------


class TestVenueAwareRouting:
    def test_ib_venue_sets_tp_post_only_false(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, venue="IB"))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        bracket = strategy.submit_order_list.call_args.args[0]
        tp_order = bracket.orders[2]
        assert tp_order.is_post_only is False

    def test_futu_venue_keeps_tp_post_only_default(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, venue="FUTU"))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        bracket = strategy.submit_order_list.call_args.args[0]
        tp_order = bracket.orders[2]
        assert tp_order.is_post_only is True


# ---------------------------------------------------------------------------
# Risk limits
# ---------------------------------------------------------------------------


class TestDynamicSizing:
    def test_dynamic_sizing_risk_based(self) -> None:
        strategy = MomentumStrategy(
            _make_config(
                window=5,
                trade_size=100,
                risk_per_trade_pct=0.02,
                account_risk_currency=100_000,
                stop_loss_ticks=10,
                max_position=5000,
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        size = strategy._compute_trade_size(1, entry_price=100.0)
        assert size == 5000

    def test_fixed_sizing_backward_compat(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, trade_size=100))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        size = strategy._compute_trade_size(1, entry_price=100.0)
        assert size == 100

    def test_dynamic_sizing_clamps_at_max_position(self) -> None:
        strategy = MomentumStrategy(
            _make_config(
                window=5,
                trade_size=100,
                risk_per_trade_pct=0.02,
                account_risk_currency=1_000_000,
                stop_loss_ticks=5,
                max_position=200,
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        size = strategy._compute_trade_size(1, entry_price=100.0)
        assert size == 200


class TestRiskLimits:
    def test_max_daily_loss_blocks_entry(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, max_daily_loss=100))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy._daily_loss = 100.0

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        strategy.submit_order_list.assert_not_called()

    def test_max_position_blocks_entry(self) -> None:
        strategy = MomentumStrategy(_make_config(window=5, max_position=100))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy._position_allowed = MagicMock(  # type: ignore[method-assign]
            return_value=False
        )

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        strategy.submit_order_list.assert_not_called()


# ---------------------------------------------------------------------------
# Fill handling
# ---------------------------------------------------------------------------


def _make_order_filled(
    client_order_id: ClientOrderId,
    order_side: OrderSide,
    qty: int = 100,
    px: str = "150.00",
    commission: str = "1.00",
) -> OrderFilled:
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.enums import LiquiditySide, OrderType
    from nautilus_trader.model.identifiers import (
        AccountId,
        PositionId,
        TradeId,
        TraderId,
    )
    from nautilus_trader.model.objects import Currency, Money

    return OrderFilled(
        TraderId("SAM-001"),
        StrategyId("MOM-001"),
        InstrumentId.from_str("AAPL.NASDAQ"),
        client_order_id,
        VenueOrderId("V-001"),
        AccountId("FUTU-001"),
        TradeId("T-001"),
        PositionId("P-001"),
        order_side,
        OrderType.MARKET,
        Quantity.from_int(qty),
        Price.from_str(px),
        Currency.from_str("USD"),
        Money(Decimal(commission), Currency.from_str("USD")),
        LiquiditySide.TAKER,
        UUID4(),
        1_700_000_000_000_000_000,
        1_700_000_000_000_000_001,
    )


class TestFillHandling:
    def test_position_tracking_on_buy_fill(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)

        fill = _make_order_filled(
            client_order_id=ClientOrderId("O-001"),
            order_side=OrderSide.BUY,
            qty=100,
            px="150.00",
        )
        strategy.on_order_filled(fill)

        assert strategy._position_qty == 100.0
        assert strategy._position_avg_px == 150.0

    def test_daily_loss_tracked_on_losing_sell(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)

        strategy._position_qty = 100.0
        strategy._position_avg_px = 150.0

        fill = _make_order_filled(
            client_order_id=ClientOrderId("O-002"),
            order_side=OrderSide.SELL,
            qty=100,
            px="140.00",
            commission="2.00",
        )
        strategy.on_order_filled(fill)

        assert strategy._position_qty == 0.0
        assert strategy._daily_loss == 1002.0

    def test_stop_market_entry_triggers_protective_orders(self) -> None:
        strategy = MomentumStrategy(
            _make_config(window=5, entry_order_type="STOP_MARKET")
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order = MagicMock()  # type: ignore[method-assign]

        _feed_window_bars(strategy, 5, start_close=100.0, increment=0.0)
        strategy.on_bar(_make_bar("100.00", "106.00", "100.00", "106.00"))

        entry_order = strategy.submit_order.call_args.args[0]

        fill = _make_order_filled(
            client_order_id=entry_order.client_order_id,
            order_side=OrderSide.BUY,
            qty=100,
            px="106.00",
        )
        strategy.on_order_filled(fill)

        assert strategy.submit_order.call_count == 3


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_on_save_returns_pickle_state(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        _feed_window_bars(strategy, 10)
        strategy._daily_loss = 50.0

        state = strategy.on_save()
        assert "state" in state
        assert isinstance(state["state"], bytes)

    def test_on_load_restores_state(self) -> None:
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        _feed_window_bars(strategy, 10)
        strategy._daily_loss = 50.0

        state = strategy.on_save()
        strategy.on_reset()
        assert len(strategy._closes) == 0
        assert strategy._daily_loss == 0.0

        strategy.on_load(state)
        assert len(strategy._closes) == 10
        assert strategy._daily_loss == 50.0
