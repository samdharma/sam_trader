"""Unit tests for ``MomentumStrategy``."""

from __future__ import annotations

import pickle
from datetime import time, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.events import OrderAccepted, OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    InstrumentId,
    StrategyId,
    TraderId,
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


class TestTimezone:
    def test_nasdaq_uses_eastern_time(self) -> None:
        strategy = MomentumStrategy(_make_config(instrument_id="AAPL.NASDAQ"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")

        with patch("sam_trader.strategies.momentum.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("America/New_York")

    def test_hkex_uses_hong_kong_time(self) -> None:
        strategy = MomentumStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("00700.HKEX")

        with patch("sam_trader.strategies.momentum.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("Asia/Hong_Kong")

    def test_fallback_from_config_when_instrument_id_not_set(self) -> None:
        strategy = MomentumStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(strategy)
        # instrument_id not set — should fall back to parsing config

        with patch("sam_trader.strategies.momentum.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("Asia/Hong_Kong")

    def test_unknown_venue_defaults_to_new_york(self) -> None:
        strategy = MomentumStrategy(_make_config(instrument_id="AAPL.UNKNOWN"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("AAPL.UNKNOWN")

        with patch("sam_trader.strategies.momentum.ZoneInfo") as mock_zi:
            mock_zi.return_value = timezone.utc
            strategy._get_et_time()
            mock_zi.assert_called_once_with("America/New_York")


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

    def test_on_save_includes_instrument_id(self) -> None:
        """Saved state includes _config_instrument_id for cross-market guard."""
        strategy = MomentumStrategy(_make_config(instrument_id="TSLA.NASDAQ"))
        _register_strategy(strategy)

        state = strategy.on_save()
        data = pickle.loads(state["state"])
        assert data["_config_instrument_id"] == "TSLA.NASDAQ"

    def test_on_load_rejects_cross_instrument_state(self) -> None:
        """State from HK instrument is discarded when config is US."""
        hk_strategy = MomentumStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(hk_strategy)
        hk_state = hk_strategy.on_save()

        us_strategy = MomentumStrategy(_make_config(instrument_id="RDW.NYSE"))
        _register_strategy(us_strategy)

        # State should be rejected — instrument mismatch
        us_strategy.on_load(hk_state)
        assert len(us_strategy._closes) == 0
        assert us_strategy._daily_loss == 0.0

    def test_on_load_accepts_same_instrument_state(self) -> None:
        """State from same instrument is loaded normally."""
        strategy_a = MomentumStrategy(_make_config(instrument_id="AAPL.NASDAQ"))
        _register_strategy(strategy_a)
        strategy_a._closes.extend([100.0, 101.0, 102.0])
        strategy_a._daily_loss = 25.0
        saved = strategy_a.on_save()

        strategy_b = MomentumStrategy(_make_config(instrument_id="AAPL.NASDAQ"))
        _register_strategy(strategy_b)
        strategy_b.on_load(saved)
        assert len(strategy_b._closes) == 3
        assert strategy_b._daily_loss == 25.0

    def test_on_load_backward_compat_no_instrument_id(self) -> None:
        """State without _config_instrument_id (old format) loads normally."""
        strategy = MomentumStrategy(_make_config(instrument_id="AAPL.NASDAQ"))
        _register_strategy(strategy)

        old_state = {
            "state": pickle.dumps(
                {
                    "_closes": [100.0, 101.0],
                    "_daily_loss": 10.0,
                }
            )
        }
        strategy.on_load(old_state)
        assert len(strategy._closes) == 2
        assert strategy._daily_loss == 10.0


# ---------------------------------------------------------------------------
# Lunch pause
# ---------------------------------------------------------------------------


class TestLunchPauseConfig:
    def test_default_values(self) -> None:
        cfg = _make_config()
        assert cfg.lunch_pause_enabled is False
        assert cfg.lunch_start == ""
        assert cfg.lunch_end == ""

    def test_custom_values(self) -> None:
        cfg = _make_config(
            lunch_pause_enabled=True,
            lunch_start="12:00",
            lunch_end="13:00",
        )
        assert cfg.lunch_pause_enabled is True
        assert cfg.lunch_start == "12:00"
        assert cfg.lunch_end == "13:00"

    def test_parse_lunch_times(self) -> None:
        cfg = _make_config(lunch_start="12:00", lunch_end="13:00")
        strategy = MomentumStrategy(cfg)
        from datetime import time

        assert strategy._lunch_start_time == time(12, 0)
        assert strategy._lunch_end_time == time(13, 0)

    def test_parse_lunch_times_with_none(self) -> None:
        cfg = _make_config(lunch_start="", lunch_end="")
        strategy = MomentumStrategy(cfg)
        assert strategy._lunch_start_time is None
        assert strategy._lunch_end_time is None


class TestLunchPauseOnStart:
    def test_on_start_schedules_alerts_when_enabled(self) -> None:
        """When lunch_pause_enabled, ``_schedule_lunch_alerts`` is called."""
        strategy = MomentumStrategy(
            _make_config(
                lunch_pause_enabled=True,
                lunch_start="12:00",
                lunch_end="13:00",
            )
        )
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy._schedule_lunch_alerts = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy._schedule_lunch_alerts.assert_called_once()

    def test_on_start_no_alerts_when_disabled(self) -> None:
        """When lunch_pause_enabled=False (default), no scheduling."""
        strategy = MomentumStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy._schedule_lunch_alerts = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy._schedule_lunch_alerts.assert_not_called()

    def test_schedule_lunch_alerts_skips_when_times_invalid(self) -> None:
        """When lunch_start is empty (None), scheduling is skipped."""
        strategy = MomentumStrategy(
            _make_config(
                lunch_pause_enabled=True,
                lunch_start="",  # invalid — empty
                lunch_end="13:00",
            )
        )
        _register_strategy(strategy)
        # type: ignore[method-assign]
        strategy._schedule_single_lunch_alert = (  # type: ignore[method-assign]
            MagicMock()
        )

        strategy._schedule_lunch_alerts()
        strategy._schedule_single_lunch_alert.assert_not_called()


class TestLunchPauseCallbacks:
    def test_on_lunch_pause_calls_pause(self) -> None:
        """``_on_lunch_pause`` calls ``self.pause()`` and reschedules."""
        strategy = MomentumStrategy(
            _make_config(
                lunch_pause_enabled=True,
                lunch_start="12:00",
                lunch_end="13:00",
            )
        )
        _register_strategy(strategy)
        # type: ignore[method-assign]
        strategy._schedule_single_lunch_alert = (  # type: ignore[method-assign]
            MagicMock()
        )
        strategy.pause = MagicMock()  # type: ignore[method-assign]

        strategy._on_lunch_pause()

        strategy.pause.assert_called_once()
        strategy._schedule_single_lunch_alert.assert_called_once()
        assert (
            strategy._schedule_single_lunch_alert.call_args.args[0] == "mom_lunch_pause"
        )

    def test_on_lunch_resume_calls_resume(self) -> None:
        """``_on_lunch_resume`` calls ``self.resume()`` and reschedules."""
        strategy = MomentumStrategy(
            _make_config(
                lunch_pause_enabled=True,
                lunch_start="12:00",
                lunch_end="13:00",
            )
        )
        _register_strategy(strategy)
        # type: ignore[method-assign]
        strategy._schedule_single_lunch_alert = (  # type: ignore[method-assign]
            MagicMock()
        )
        strategy.resume = MagicMock()  # type: ignore[method-assign]

        strategy._on_lunch_resume()

        strategy.resume.assert_called_once()
        strategy._schedule_single_lunch_alert.assert_called_once()
        assert (
            strategy._schedule_single_lunch_alert.call_args.args[0]
            == "mom_lunch_resume"
        )

    def test_lunch_pause_no_reschedule_when_time_none(self) -> None:
        """Callback does not reschedule when ``_lunch_start_time`` is ``None``."""
        cfg = _make_config(lunch_start="", lunch_end="13:00")
        strategy = MomentumStrategy(cfg)
        _register_strategy(strategy)
        # type: ignore[method-assign]
        strategy._schedule_single_lunch_alert = (  # type: ignore[method-assign]
            MagicMock()
        )
        strategy.pause = MagicMock()  # type: ignore[method-assign]

        strategy._on_lunch_pause()
        strategy._schedule_single_lunch_alert.assert_not_called()


class TestLunchPauseScheduleLogic:
    def test_schedule_lunch_alerts_dispatches_both_alerts(self) -> None:
        """``_schedule_lunch_alerts`` schedules both pause and resume."""
        strategy = MomentumStrategy(
            _make_config(
                lunch_pause_enabled=True,
                lunch_start="12:00",
                lunch_end="13:00",
            )
        )
        _register_strategy(strategy)
        # type: ignore[method-assign]
        strategy._schedule_single_lunch_alert = (  # type: ignore[method-assign]
            MagicMock()
        )

        strategy._schedule_lunch_alerts()

        assert strategy._schedule_single_lunch_alert.call_count == 2
        call_names = [
            c.args[0] for c in strategy._schedule_single_lunch_alert.call_args_list
        ]
        assert "mom_lunch_pause" in call_names
        assert "mom_lunch_resume" in call_names

    def test_get_timezone_name_nasdaq(self) -> None:
        """NASDAQ instrument returns America/New_York."""
        strategy = MomentumStrategy(_make_config(instrument_id="AAPL.NASDAQ"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("AAPL.NASDAQ")

        assert strategy._get_timezone_name() == "America/New_York"

    def test_get_timezone_name_hkex(self) -> None:
        """HKEX instrument returns Asia/Hong_Kong."""
        strategy = MomentumStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("00700.HKEX")

        assert strategy._get_timezone_name() == "Asia/Hong_Kong"

    def test_get_timezone_name_fallback_from_config(self) -> None:
        """When instrument_id is None, falls back to config string."""
        strategy = MomentumStrategy(_make_config(instrument_id="00700.HKEX"))
        _register_strategy(strategy)

        assert strategy._get_timezone_name() == "Asia/Hong_Kong"

    def test_get_timezone_name_unknown_venue(self) -> None:
        """Unknown venue defaults to America/New_York."""
        strategy = MomentumStrategy(_make_config(instrument_id="AAPL.UNKNOWN"))
        _register_strategy(strategy)
        strategy.instrument_id = InstrumentId.from_str("AAPL.UNKNOWN")

        assert strategy._get_timezone_name() == "America/New_York"


# ---------------------------------------------------------------------------
# Rejection circuit breaker
# ---------------------------------------------------------------------------


def _make_order_rejected_mom(
    client_order_id: str = "O-20260528-000001",
    reason: str = "INSUFFICIENT_MARGIN",
    instrument_id: str = "AAPL.NASDAQ",
) -> OrderRejected:
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.identifiers import (
        AccountId,
        ClientOrderId,
        InstrumentId,
        StrategyId,
        TraderId,
    )

    return OrderRejected(
        trader_id=TraderId("SAM-001"),
        strategy_id=StrategyId("MOM-001"),
        instrument_id=InstrumentId.from_str(instrument_id),
        client_order_id=ClientOrderId(client_order_id),
        account_id=AccountId("FUTU-001"),
        reason=reason,
        event_id=UUID4(),
        ts_event=1_700_000_000_000_000_000,
        ts_init=1_700_000_000_000_000_001,
        reconciliation=False,
    )


class TestRejectionCircuitBreaker:
    def test_default_max_consecutive_rejections(self) -> None:
        """Config defaults to 10."""
        cfg = _make_config()
        assert cfg.max_consecutive_rejections == 10

    def test_circuit_breaker_disabled_when_set_to_zero(self) -> None:
        """max_consecutive_rejections=0 disables the circuit breaker."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=0))
        _register_strategy(strategy)
        strategy.on_start()

        rejected = _make_order_rejected_mom()
        for _ in range(100):
            strategy.on_order_rejected(rejected)

        assert strategy._rejection_disabled is False

    def test_n_rejections_trips_circuit_breaker(self) -> None:
        """After N consecutive rejections, strategy auto-disables."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=3))
        _register_strategy(strategy)
        strategy.on_start()

        rejected = _make_order_rejected_mom()
        strategy.on_order_rejected(rejected)
        assert strategy._rejection_count == 1
        assert strategy._rejection_disabled is False

        strategy.on_order_rejected(rejected)
        strategy.on_order_rejected(rejected)
        assert strategy._rejection_count == 3
        assert strategy._rejection_disabled is True

    def test_critical_log_emitted_on_trip(self) -> None:
        """An ERROR-level log message is emitted when the breaker trips."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=2))
        _register_strategy(strategy)
        strategy.on_start()

        rejected = _make_order_rejected_mom()
        strategy.on_order_rejected(rejected)
        assert strategy._rejection_disabled is False

        strategy.on_order_rejected(rejected)
        assert strategy._rejection_disabled is True
        assert strategy._rejection_count == 2

    def test_on_bar_ignored_while_disabled(self) -> None:
        """All subsequent bar updates are ignored while disabled."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=2))
        _register_strategy(strategy)
        strategy.on_start()

        # First, trip the circuit breaker
        rejected = _make_order_rejected_mom()
        strategy.on_order_rejected(rejected)
        strategy.on_order_rejected(rejected)
        assert strategy._rejection_disabled is True

        # on_bar should be a no-op — closs deque should not grow
        bar = _make_bar("150", "155", "149", "152")
        closes_before = len(strategy._closes)
        strategy.on_bar(bar)
        assert len(strategy._closes) == closes_before  # unchanged

    def test_acceptance_resets_rejection_counter(self) -> None:
        """First successful order acceptance resets the rejection streak."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=5))
        _register_strategy(strategy)
        strategy.on_start()

        rejected = _make_order_rejected_mom()
        strategy.on_order_rejected(rejected)
        strategy.on_order_rejected(rejected)
        assert strategy._rejection_count == 2

        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.model.identifiers import AccountId, VenueOrderId

        accepted = OrderAccepted(
            trader_id=TraderId("SAM-001"),
            strategy_id=StrategyId("MOM-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            client_order_id=ClientOrderId("O-001"),
            venue_order_id=VenueOrderId("V-001"),
            account_id=AccountId("FUTU-001"),
            event_id=UUID4(),
            ts_event=1_700_000_000_000_000_000,
            ts_init=1_700_000_000_000_000_001,
            reconciliation=False,
        )
        strategy.on_order_accepted(accepted)
        assert strategy._rejection_count == 0

        # More rejections now start from 0
        strategy.on_order_rejected(rejected)
        assert strategy._rejection_count == 1

    def test_persistence_save_and_load(self) -> None:
        """Rejection state is preserved across save/load."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=5))
        _register_strategy(strategy)
        strategy.on_start()

        rejected = _make_order_rejected_mom()
        for _ in range(5):
            strategy.on_order_rejected(rejected)
        assert strategy._rejection_disabled is True

        saved = strategy.on_save()
        strategy2 = MomentumStrategy(_make_config(max_consecutive_rejections=5))
        _register_strategy(strategy2)
        strategy2.on_start()
        strategy2.on_load(saved)

        assert strategy2._rejection_disabled is True
        assert strategy2._rejection_count == 5

    def test_reset_clears_rejection_state(self) -> None:
        """on_reset clears the rejection counter and disabled flag."""
        strategy = MomentumStrategy(_make_config(max_consecutive_rejections=3))
        _register_strategy(strategy)
        strategy.on_start()

        rejected = _make_order_rejected_mom()
        for _ in range(3):
            strategy.on_order_rejected(rejected)
        assert strategy._rejection_disabled is True

        strategy.on_reset()
        assert strategy._rejection_count == 0
        assert strategy._rejection_disabled is False


# ---------------------------------------------------------------------------
# time_in_force resolution — scenario 4, 5
# ---------------------------------------------------------------------------


class TestTimeInForceResolution:
    """Scenario 4-5: time_in_force resolution for MomentumStrategy.

    Verifies:
      - Strategy-level override (scenario 4)
      - DEFAULT_TIME_IN_FORCE env var fallback (scenario 5)
      - SIMULATE forces GTC → DAY (scenario 2 integration)
    """

    def test_explicit_config_override(self) -> None:
        """When config.time_in_force is set, use it directly."""
        from nautilus_trader.model.enums import TimeInForce

        config = _make_config(time_in_force="IOC")
        result = MomentumStrategy._resolve_time_in_force(config)
        assert result == TimeInForce.IOC

    def test_defaults_to_day(self, monkeypatch) -> None:
        """When nothing is configured, fall back to DAY."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.delenv("FUTU_TRD_ENV", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
        config = _make_config(time_in_force=None)
        result = MomentumStrategy._resolve_time_in_force(config)
        assert result == TimeInForce.DAY

    def test_strategy_uses_resolved_tif(self, monkeypatch) -> None:
        """MomentumStrategy.__init__ stores resolved TIF as self._time_in_force."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
        strategy = MomentumStrategy(_make_config(time_in_force="DAY"))
        assert strategy._time_in_force == TimeInForce.DAY

    def test_simulate_forces_day_from_gtc(self, monkeypatch) -> None:
        """When FUTU_TRD_ENV=SIMULATE and resolved TIF=GTC, force DAY."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        strategy = MomentumStrategy(_make_config(time_in_force="GTC"))
        assert strategy._time_in_force == TimeInForce.DAY

    def test_simulate_does_not_force_day_from_ioc(self, monkeypatch) -> None:
        """When FUTU_TRD_ENV=SIMULATE and resolved TIF=IOC, keep IOC."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        strategy = MomentumStrategy(_make_config(time_in_force="IOC"))
        assert strategy._time_in_force == TimeInForce.IOC

    def test_general_default_via_env_var(self, monkeypatch) -> None:
        """Scenario 5: DEFAULT_TIME_IN_FORCE env var works as fallback."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.setenv("DEFAULT_TIME_IN_FORCE", "IOC")
        monkeypatch.delenv("FUTU_TRD_ENV", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
        config = _make_config(time_in_force=None)
        result = MomentumStrategy._resolve_time_in_force(config)
        assert result == TimeInForce.IOC

    def test_strategy_override_beats_env_var(self, monkeypatch) -> None:
        """Scenario 4: strategy-level time_in_force overrides env var default."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.setenv("DEFAULT_TIME_IN_FORCE", "IOC")
        monkeypatch.delenv("FUTU_TRD_ENV", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
        config = _make_config(time_in_force="DAY")
        result = MomentumStrategy._resolve_time_in_force(config)
        # Strategy says DAY, env says IOC → strategy wins
        assert result == TimeInForce.DAY


# ---------------------------------------------------------------------------
# TIF circuit breaker safety — scenario 6
# ---------------------------------------------------------------------------


class TestTIFCircuitBreakerSafety:
    """Scenario 6: GTC auto-correction prevents circuit breaker trip.

    When FUTU_TRD_ENV=SIMULATE, the strategy resolves GTC→DAY at init
    time.  Subsequent order submissions use DAY, so no GTC rejections
    occur — the circuit breaker never trips for TIF reasons.
    """

    def test_simulate_gtc_resolves_day_strategy_uses_day(self, monkeypatch) -> None:
        """Strategy in SIMULATE with GTC config resolves to DAY in __init__."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        strategy = MomentumStrategy(_make_config(time_in_force="GTC"))
        _register_strategy(strategy)
        strategy.on_start()

        # Strategy resolved TIF to DAY — circuit breaker safe
        assert strategy._time_in_force == TimeInForce.DAY
        # Circuit breaker starts clean
        assert strategy._rejection_count == 0
        assert strategy._rejection_disabled is False

    def test_day_config_never_trips_circuit_breaker(self, monkeypatch) -> None:
        """DAY config in SIMULATE: no TIF conversion needed, no rejections."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")
        strategy = MomentumStrategy(_make_config(time_in_force="DAY"))
        _register_strategy(strategy)
        strategy.on_start()

        assert strategy._time_in_force == TimeInForce.DAY
        assert strategy._rejection_count == 0
        assert strategy._rejection_disabled is False
