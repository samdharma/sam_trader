"""Unit tests for ``OrbStrategy``."""

from __future__ import annotations

from datetime import timezone
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

from sam_trader.strategies.orb import OrbStrategy, OrbStrategyConfig

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


def _make_config(**kwargs: Any) -> OrbStrategyConfig:
    defaults: dict[str, Any] = {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": str(BAR_TYPE),
        "first_candle_minutes": 15,
        "trade_size": 100,
        "confirmation_bars": 1,
        "atr_period": 14,
        "min_range_atr_multiple": 0.0,
        "entry_order_type": "MARKET",
        "stop_loss_ticks": 10,
        "take_profit_ticks": 30,
        "max_position": 500,
        "max_daily_loss": 1000,
    }
    defaults.update(kwargs)
    return OrbStrategyConfig(**defaults)


def _register_strategy(strategy: OrbStrategy) -> None:
    strategy.register(
        trader_id=TestIdStubs.trader_id(),
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )


def _mock_instrument(strategy: OrbStrategy) -> None:
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


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestOrbStrategyConfig:
    def test_default_values(self) -> None:
        cfg = _make_config()
        assert cfg.first_candle_minutes == 15
        assert cfg.trade_size == 100
        assert cfg.confirmation_bars == 1
        assert cfg.entry_order_type == "MARKET"
        assert cfg.stop_loss_ticks == 10
        assert cfg.take_profit_ticks == 30
        assert cfg.max_position == 500
        assert cfg.max_daily_loss == 1000

    def test_custom_values(self) -> None:
        cfg = _make_config(
            entry_order_type="LIMIT",
            confirmation_bars=2,
            min_range_atr_multiple=1.5,
        )
        assert cfg.entry_order_type == "LIMIT"
        assert cfg.confirmation_bars == 2
        assert cfg.min_range_atr_multiple == 1.5


# ---------------------------------------------------------------------------
# Strategy lifecycle
# ---------------------------------------------------------------------------


class TestOrbStrategyLifecycle:
    def test_on_start_subscribes_and_sets_state(self) -> None:
        strategy = OrbStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        assert strategy.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert strategy.bar_type == BAR_TYPE
        assert strategy._first_candle_bars == 3  # 15m / 5m

    def test_on_start_stops_when_no_instrument(self) -> None:
        strategy = OrbStrategy(_make_config())
        _register_strategy(strategy)
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy.stop.assert_called_once()

    def test_on_stop_cancels_and_unsubscribes(self) -> None:
        strategy = OrbStrategy(_make_config())
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
        strategy = OrbStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        # Feed bars to establish range
        for _ in range(3):
            strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.50"))

        assert strategy._range_established is True
        strategy.on_reset()
        assert strategy._range_established is False
        assert strategy._range_high is None
        assert strategy._range_low is None
        assert strategy._daily_loss == 0.0


# ---------------------------------------------------------------------------
# Range establishment
# ---------------------------------------------------------------------------


class TestRangeEstablishment:
    def test_range_established_after_first_candle_bars(self) -> None:
        strategy = OrbStrategy(_make_config(first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        assert strategy._first_candle_bars == 2  # 10m / 5m

        strategy.on_bar(_make_bar("100.00", "101.00", "99.50", "100.00"))
        assert strategy._range_established is False

        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        assert strategy._range_established is True
        assert strategy._range_high == 102.0
        assert strategy._range_low == 99.5

    def test_atr_filter_stops_on_narrow_range(self) -> None:
        strategy = OrbStrategy(
            _make_config(
                first_candle_minutes=10,
                atr_period=1,
                min_range_atr_multiple=5.0,  # impossible to satisfy
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        # Feed bars to establish range (2 bars for 10m / 5m)
        for i in range(2):
            strategy.on_bar(_make_bar("100.00", "100.01", "100.00", "100.00"))

        assert strategy._range_established is True
        strategy.stop.assert_called_once()

    def test_atr_filter_allows_wide_range(self) -> None:
        strategy = OrbStrategy(
            _make_config(
                first_candle_minutes=10,
                atr_period=1,
                min_range_atr_multiple=0.1,
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        # Feed 2 bars with wide range (high=103, low=99 → width=4)
        strategy.on_bar(_make_bar("100.00", "103.00", "99.00", "101.00"))
        strategy.on_bar(_make_bar("101.00", "102.00", "100.00", "101.50"))

        assert strategy._range_established is True
        strategy.stop.assert_not_called()


# ---------------------------------------------------------------------------
# Breakout and confirmation
# ---------------------------------------------------------------------------


class TestBreakoutAndConfirmation:
    def test_long_breakout_with_one_bar_confirmation(self) -> None:
        strategy = OrbStrategy(
            _make_config(confirmation_bars=1, first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Establish range: high=102, low=99
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        # Breakout above 102
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        strategy.submit_order_list.assert_called_once()
        call_kwargs = strategy.submit_order_list.call_args.args[0]
        # Bracket order list has 3 orders: entry, SL, TP
        assert len(call_kwargs.orders) == 3

    def test_long_breakout_with_two_bar_confirmation(self) -> None:
        strategy = OrbStrategy(
            _make_config(confirmation_bars=2, first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Establish range
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        # First breakout bar — starts confirmation
        strategy.on_bar(_make_bar("102.50", "103.00", "102.10", "102.80"))
        strategy.submit_order_list.assert_not_called()
        assert strategy._confirmation_count == 1

        # Second confirming bar — enters
        strategy.on_bar(_make_bar("102.80", "103.50", "102.20", "103.00"))
        strategy.submit_order_list.assert_called_once()

    def test_confirmation_fails_on_pullback(self) -> None:
        strategy = OrbStrategy(
            _make_config(confirmation_bars=2, first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Establish range
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        # First breakout bar
        strategy.on_bar(_make_bar("102.50", "103.00", "102.10", "102.80"))
        assert strategy._confirmation_direction == 1

        # Pullback — low drops below prev_low
        strategy.on_bar(_make_bar("102.80", "103.00", "101.50", "102.00"))
        strategy.submit_order_list.assert_not_called()
        assert strategy._confirmation_direction is None

    def test_short_breakout(self) -> None:
        strategy = OrbStrategy(
            _make_config(confirmation_bars=1, first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Establish range: high=102, low=99
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        # Breakout below 99
        strategy.on_bar(_make_bar("98.50", "99.00", "98.00", "98.20"))

        strategy.submit_order_list.assert_called_once()
        call_kwargs = strategy.submit_order_list.call_args.args[0]
        assert len(call_kwargs.orders) == 3


# ---------------------------------------------------------------------------
# Entry order types
# ---------------------------------------------------------------------------


class TestEntryOrderTypes:
    def test_market_entry_bracket(self) -> None:
        strategy = OrbStrategy(
            _make_config(entry_order_type="MARKET", first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        strategy.submit_order_list.assert_called_once()
        bracket = strategy.submit_order_list.call_args.args[0]
        entry_order = bracket.orders[0]
        assert entry_order.order_type == OrderType.MARKET

    def test_limit_entry_bracket(self) -> None:
        strategy = OrbStrategy(
            _make_config(entry_order_type="LIMIT", first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        strategy.submit_order_list.assert_called_once()
        bracket = strategy.submit_order_list.call_args.args[0]
        entry_order = bracket.orders[0]
        assert entry_order.order_type == OrderType.LIMIT
        assert float(entry_order.price) == 102.0  # range high

    def test_stop_market_entry(self) -> None:
        strategy = OrbStrategy(
            _make_config(entry_order_type="STOP_MARKET", first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        strategy.submit_order.assert_called_once()
        order = strategy.submit_order.call_args.args[0]
        assert order.order_type == OrderType.STOP_MARKET
        assert float(order.trigger_price) == 102.0


# ---------------------------------------------------------------------------
# Venue-aware routing
# ---------------------------------------------------------------------------


class TestVenueAwareRouting:
    def test_ib_venue_sets_tp_post_only_false(self) -> None:
        strategy = OrbStrategy(_make_config(venue="IB", first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        bracket = strategy.submit_order_list.call_args.args[0]
        tp_order = bracket.orders[2]
        assert tp_order.is_post_only is False

    def test_futu_venue_keeps_tp_post_only_default(self) -> None:
        strategy = OrbStrategy(_make_config(venue="FUTU", first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        bracket = strategy.submit_order_list.call_args.args[0]
        tp_order = bracket.orders[2]
        assert tp_order.is_post_only is True

    def test_stop_market_ib_venue_sets_post_only_false_on_tp(self) -> None:
        strategy = OrbStrategy(
            _make_config(
                venue="IB", entry_order_type="STOP_MARKET", first_candle_minutes=10
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        # Simulate entry fill → protective orders submitted
        entry_order = strategy.submit_order.call_args.args[0]
        strategy._entry_order = entry_order
        strategy._position_qty = 100.0
        strategy._position_avg_px = 102.0

        fill = _make_order_filled(
            client_order_id=entry_order.client_order_id,
            order_side=OrderSide.BUY,
        )
        strategy.on_order_filled(fill)

        # Third call should be TP limit order (breakout entry + SL + TP)
        calls = strategy.submit_order.call_args_list
        assert len(calls) == 3
        tp_order = calls[2].args[0]
        assert tp_order.is_post_only is False


# ---------------------------------------------------------------------------
# Risk limits
# ---------------------------------------------------------------------------


class TestDynamicSizing:
    def test_dynamic_sizing_risk_based(self) -> None:
        strategy = OrbStrategy(
            _make_config(
                first_candle_minutes=10,
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

        # SL distance = 10 ticks * 0.01 = 0.10
        # risk_dollars = 100_000 * 0.02 = 2_000
        # size = int(2_000 / 0.10) = 20_000 → clamped to max_position=5000
        size = strategy._compute_trade_size(1, entry_price=100.0)
        assert size == 5000

    def test_dynamic_sizing_fixed_fallback(self) -> None:
        strategy = OrbStrategy(_make_config(first_candle_minutes=10, trade_size=100))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        size = strategy._compute_trade_size(1, entry_price=100.0)
        assert size == 100

    def test_dynamic_sizing_clamps_at_max_position(self) -> None:
        strategy = OrbStrategy(
            _make_config(
                first_candle_minutes=10,
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

    def test_dynamic_sizing_atr_adjustment(self) -> None:
        strategy = OrbStrategy(
            _make_config(
                first_candle_minutes=10,
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
        strategy._cached_atr = 2.0

        base_size = strategy._compute_trade_size(1, entry_price=100.0)
        strategy._cached_atr = 5.0
        adjusted_size = strategy._compute_trade_size(1, entry_price=100.0)

        assert adjusted_size < base_size


class TestRiskLimits:
    def test_max_daily_loss_blocks_entry(self) -> None:
        strategy = OrbStrategy(_make_config(max_daily_loss=100))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Set daily loss at limit
        strategy._daily_loss = 100.0

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        strategy.submit_order_list.assert_not_called()

    def test_max_position_blocks_entry(self) -> None:
        strategy = OrbStrategy(_make_config(max_position=100, first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Simulate already at max position — _position_allowed returns False
        strategy._position_allowed = MagicMock(  # type: ignore[method-assign]
            return_value=False
        )

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        strategy.submit_order_list.assert_not_called()


# ---------------------------------------------------------------------------
# Rate-limiting: max_trades_per_day + trade_cooldown_seconds
# ---------------------------------------------------------------------------


class TestMaxTradesPerDay:
    def test_limit_blocks_entry_after_reached(self) -> None:
        """Strategy stops entering after max_trades_per_day reached."""
        strategy = OrbStrategy(
            _make_config(
                max_trades_per_day=2,
                confirmation_bars=1,
                first_candle_minutes=10,
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Establish range
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        # Trade 1: breakout enters
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))
        assert strategy.submit_order_list.call_count == 1
        assert strategy._trades_today == 1

        # Trade 2: next breakout enters
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))
        assert strategy.submit_order_list.call_count == 2
        assert strategy._trades_today == 2

        # Trade 3: should be blocked (limit reached)
        strategy.on_bar(_make_bar("103.00", "104.00", "102.50", "103.50"))
        assert strategy.submit_order_list.call_count == 2

    def test_disabled_allows_unlimited(self) -> None:
        """``max_trades_per_day=0`` (default) disables the limit."""
        strategy = OrbStrategy(
            _make_config(
                max_trades_per_day=0,
                confirmation_bars=1,
                first_candle_minutes=10,
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        for _ in range(5):
            strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        assert strategy.submit_order_list.call_count == 5


class TestTradeCooldown:
    def test_in_cooldown_when_within_window(self) -> None:
        """Unit-test ``_in_cooldown()`` — returns True when too recent."""
        strategy = OrbStrategy(_make_config(trade_cooldown_seconds=60))
        _register_strategy(strategy)

        base_ns = 1_700_000_000_000_000_000
        strategy._last_flat_time_ns = base_ns
        now_ns = base_ns + 30 * 1_000_000_000

        assert strategy._in_cooldown(now_ns) is True

    def test_in_cooldown_after_elapsed(self) -> None:
        """Unit-test ``_in_cooldown()`` — returns False after period elapsed."""
        strategy = OrbStrategy(_make_config(trade_cooldown_seconds=60))
        _register_strategy(strategy)

        base_ns = 1_700_000_000_000_000_000
        strategy._last_flat_time_ns = base_ns
        now_ns = base_ns + 61 * 1_000_000_000

        assert strategy._in_cooldown(now_ns) is False

    def test_in_cooldown_disabled(self) -> None:
        """Unit-test ``_in_cooldown()`` — returns False when disabled."""
        strategy = OrbStrategy(_make_config(trade_cooldown_seconds=0))
        _register_strategy(strategy)

        strategy._last_flat_time_ns = 1_700_000_000_000_000_000

        assert strategy._in_cooldown() is False

    def test_in_cooldown_first_trade(self) -> None:
        """Unit-test ``_in_cooldown()`` — returns False when no prior trade."""
        strategy = OrbStrategy(_make_config(trade_cooldown_seconds=60))
        _register_strategy(strategy)

        assert strategy._last_flat_time_ns == 0

        assert strategy._in_cooldown() is False

    def test_cooldown_blocks_entry_in_on_bar(self) -> None:
        """Cooldown enforced via on_bar — entry blocked when active."""
        strategy = OrbStrategy(
            _make_config(
                trade_cooldown_seconds=60,
                confirmation_bars=1,
                first_candle_minutes=10,
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]

        # Establish range
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        # First breakout enters (cooldown not yet active)
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))
        assert strategy.submit_order_list.call_count == 1

        # Simulate active cooldown
        strategy._in_cooldown = MagicMock(  # type: ignore[method-assign]
            return_value=True,
        )

        # Second breakout blocked by cooldown
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))
        assert strategy.submit_order_list.call_count == 1

        # Remove cooldown → entry allowed again
        strategy._in_cooldown = MagicMock(  # type: ignore[method-assign]
            return_value=False,
        )
        strategy.on_bar(_make_bar("103.00", "104.00", "102.50", "103.50"))
        assert strategy.submit_order_list.call_count == 2


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
        StrategyId("ORB-001"),
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
        strategy = OrbStrategy(_make_config())
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
        strategy = OrbStrategy(_make_config())
        _register_strategy(strategy)

        # Open long at 150
        strategy._position_qty = 100.0
        strategy._position_avg_px = 150.0

        # Close at 140 (loss)
        fill = _make_order_filled(
            client_order_id=ClientOrderId("O-002"),
            order_side=OrderSide.SELL,
            qty=100,
            px="140.00",
            commission="2.00",
        )
        strategy.on_order_filled(fill)

        assert strategy._position_qty == 0.0
        assert strategy._daily_loss == 1002.0  # (150-140)*100 + 2.0 commission

    def test_stop_market_entry_triggers_protective_orders(self) -> None:
        strategy = OrbStrategy(
            _make_config(entry_order_type="STOP_MARKET", first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy.submit_order = MagicMock()  # type: ignore[method-assign]

        # Establish range and trigger breakout
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))
        strategy.on_bar(_make_bar("102.50", "103.00", "102.00", "102.80"))

        entry_order = strategy.submit_order.call_args.args[0]

        # Simulate fill
        fill = _make_order_filled(
            client_order_id=entry_order.client_order_id,
            order_side=OrderSide.BUY,
            qty=100,
            px="102.00",
        )
        strategy.on_order_filled(fill)

        assert strategy.submit_order.call_count == 3  # breakout entry + SL + TP


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Session start
# ---------------------------------------------------------------------------


class TestSessionStart:
    def test_session_start_disabled_allows_all_bars(self) -> None:
        """When ``session_start=""`` (default), accumulate from first bar."""
        strategy = OrbStrategy(_make_config(session_start="", first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()
        strategy._in_range_accumulation_window = (  # type: ignore[method-assign]
            MagicMock(return_value=True)
        )

        assert strategy._range_established is False
        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.50"))
        # Bar should be accumulated; range_high/_low updated
        assert strategy._bars_seen == 1
        assert strategy._range_high == 101.0
        assert strategy._range_low == 99.0

    def test_session_start_ignores_pre_market_bars(self) -> None:
        """When ``session_start="09:30"``, bars before 09:30 are ignored."""
        strategy = OrbStrategy(
            _make_config(session_start="09:30", first_candle_minutes=10)
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        # Mock _get_et_time to return 09:29 (before session_start)
        from datetime import time

        strategy._get_et_time = MagicMock(  # type: ignore[method-assign]
            return_value=time(9, 29, 0)
        )

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.50"))
        assert strategy._bars_seen == 0  # bar ignored
        assert strategy._range_high is None

        # Now mock 09:30 (at session_start) — bar should be accumulated
        strategy._get_et_time = MagicMock(  # type: ignore[method-assign]
            return_value=time(9, 30, 0)
        )
        strategy.on_bar(_make_bar("100.00", "101.00", "99.50", "100.00"))
        assert strategy._bars_seen == 1
        assert strategy._range_high == 101.0
        assert strategy._range_low == 99.5

    def test_session_start_when_none_allows_bars(self) -> None:
        """When ``_session_start_time`` is ``None``, accumulation is always allowed."""
        strategy = OrbStrategy(_make_config(session_start="", first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        # With no session_start config, _session_start_time is None
        assert strategy._session_start_time is None
        assert strategy._in_range_accumulation_window() is True

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.50"))
        assert strategy._bars_seen == 1


class TestTimezone:
    def test_nasdaq_uses_eastern_time(self) -> None:
        strategy = OrbStrategy(_make_config(instrument_id="AAPL.NASDAQ"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")

        with patch("sam_trader.strategies.orb.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("America/New_York")

    def test_hkex_uses_hong_kong_time(self) -> None:
        strategy = OrbStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("00700.HKEX")

        with patch("sam_trader.strategies.orb.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("Asia/Hong_Kong")

    def test_fallback_from_config_when_instrument_id_not_set(self) -> None:
        strategy = OrbStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(strategy)
        # instrument_id not set — should fall back to parsing config

        with patch("sam_trader.strategies.orb.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("Asia/Hong_Kong")

    def test_unknown_venue_defaults_to_new_york(self) -> None:
        strategy = OrbStrategy(_make_config(instrument_id="AAPL.UNKNOWN"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("AAPL.UNKNOWN")

        with patch("sam_trader.strategies.orb.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("America/New_York")


class TestStatePersistence:
    def test_on_save_returns_pickle_state(self) -> None:
        strategy = OrbStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        state = strategy.on_save()
        assert "state" in state
        assert isinstance(state["state"], bytes)

    def test_on_load_restores_state(self) -> None:
        strategy = OrbStrategy(_make_config(first_candle_minutes=10))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        strategy.on_bar(_make_bar("100.00", "101.00", "99.00", "100.00"))
        strategy.on_bar(_make_bar("100.50", "102.00", "100.00", "101.00"))

        state = strategy.on_save()
        strategy.on_reset()
        assert strategy._range_established is False

        strategy.on_load(state)
        assert strategy._range_established is True
        assert strategy._range_high == 102.0
        assert strategy._range_low == 99.0
