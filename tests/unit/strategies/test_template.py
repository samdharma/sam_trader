"""Unit tests for ``TemplateStrategy`` — validates the copy-paste template."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs

from sam_trader.strategies._template import TemplateStrategy, TemplateStrategyConfig

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


def _make_config(**kwargs: Any) -> TemplateStrategyConfig:
    defaults: dict[str, Any] = {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": str(BAR_TYPE),
        "trade_size": 100,
        "entry_order_type": "MARKET",
        "stop_loss_ticks": 10,
        "take_profit_ticks": 30,
        "max_position": 500,
        "max_daily_loss": 1000,
    }
    defaults.update(kwargs)
    return TemplateStrategyConfig(**defaults)


def _register_strategy(strategy: TemplateStrategy) -> None:
    strategy.register(
        trader_id=TestIdStubs.trader_id(),
        portfolio=TestComponentStubs.portfolio(),
        msgbus=TestComponentStubs.msgbus(),
        cache=TestComponentStubs.cache(),
        clock=TestComponentStubs.clock(),
    )


def _mock_instrument(strategy: TemplateStrategy) -> None:
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


class TestTemplateStrategyConfig:
    def test_default_values(self) -> None:
        cfg = _make_config()
        assert cfg.trade_size == 100
        assert cfg.entry_order_type == "MARKET"
        assert cfg.stop_loss_ticks == 10
        assert cfg.take_profit_ticks == 30
        assert cfg.max_position == 500
        assert cfg.max_daily_loss == 1000
        assert cfg.venue == ""
        assert cfg.bundle_id == "unknown"

    def test_custom_values(self) -> None:
        cfg = _make_config(
            entry_order_type="LIMIT",
            stop_loss_ticks=20,
            venue="IB",
        )
        assert cfg.entry_order_type == "LIMIT"
        assert cfg.stop_loss_ticks == 20
        assert cfg.venue == "IB"


# ---------------------------------------------------------------------------
# Strategy lifecycle
# ---------------------------------------------------------------------------


class TestTemplateStrategyLifecycle:
    def test_on_start_subscribes_and_sets_state(self) -> None:
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        assert strategy.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert strategy.bar_type == BAR_TYPE
        assert strategy.instrument is not None

    def test_on_start_stops_when_no_instrument(self) -> None:
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        strategy.stop = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy.stop.assert_called_once()

    def test_on_stop_cancels_and_unsubscribes(self) -> None:
        strategy = TemplateStrategy(_make_config())
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
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        strategy._closes.append(1.0)
        strategy._daily_loss = 100.0
        strategy.on_reset()

        assert len(strategy._closes) == 0
        assert strategy._daily_loss == 0.0

    def test_on_save_and_load_roundtrip(self) -> None:
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        strategy._closes.append(1.0)
        strategy._closes.append(2.0)
        strategy._daily_loss = 50.0
        strategy._position_qty = 100.0
        strategy._position_avg_px = 150.0

        state = strategy.on_save()
        assert "state" in state

        new_strategy = TemplateStrategy(_make_config())
        _register_strategy(new_strategy)
        _mock_instrument(new_strategy)
        new_strategy.on_start()
        new_strategy.on_load(state)

        assert list(new_strategy._closes) == [1.0, 2.0]
        assert new_strategy._daily_loss == 50.0
        assert new_strategy._position_qty == 100.0
        assert new_strategy._position_avg_px == 150.0


# ---------------------------------------------------------------------------
# Venue-aware order patterns
# ---------------------------------------------------------------------------


class TestTemplateStrategyVenueAwareOrders:
    def test_enter_long_uses_make_bracket(self) -> None:
        """Long entry builds a bracket order via the venue-aware helper."""
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]
        bar = _make_bar("150.00", "151.00", "149.00", "150.00")

        with patch("sam_trader.strategies._template.make_bracket") as mock_make_bracket:
            mock_make_bracket.return_value = MagicMock()
            strategy._enter_long(bar)

        mock_make_bracket.assert_called_once()
        call_kwargs = mock_make_bracket.call_args.kwargs
        assert call_kwargs["order_side"] == OrderSide.BUY
        assert call_kwargs["instrument_id"] == strategy.instrument_id
        strategy.submit_order_list.assert_called_once()

    def test_enter_short_uses_make_bracket(self) -> None:
        """Short entry builds a bracket order via the venue-aware helper."""
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        strategy.submit_order_list = MagicMock()  # type: ignore[method-assign]
        bar = _make_bar("150.00", "151.00", "149.00", "150.00")

        with patch("sam_trader.strategies._template.make_bracket") as mock_make_bracket:
            mock_make_bracket.return_value = MagicMock()
            strategy._enter_short(bar)

        mock_make_bracket.assert_called_once()
        call_kwargs = mock_make_bracket.call_args.kwargs
        assert call_kwargs["order_side"] == OrderSide.SELL
        strategy.submit_order_list.assert_called_once()

    def test_ib_venue_guard_in_bracket_kwargs(self) -> None:
        """When venue=IB, the bracket kwargs dict contains tp_post_only=False."""
        strategy = TemplateStrategy(_make_config(venue="IB"))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        bar = _make_bar("150.00", "151.00", "149.00", "150.00")
        tick_size = float(
            strategy.instrument.price_increment  # type: ignore[union-attr]
        )
        entry_price = float(bar.close)
        sl_price = entry_price - strategy.config.stop_loss_ticks * tick_size
        tp_price = entry_price + strategy.config.take_profit_ticks * tick_size

        # Reproduce the direct-factory pattern from _template.py comments
        bracket_kwargs: dict = {
            "instrument_id": strategy.instrument_id,
            "order_side": OrderSide.BUY,
            "quantity": strategy.instrument.make_qty(  # type: ignore[union-attr]
                strategy.config.trade_size
            ),
            "time_in_force": MagicMock(),
            "sl_trigger_price": (
                strategy.instrument.make_price(sl_price)  # type: ignore[union-attr]
            ),
            "tp_price": (
                strategy.instrument.make_price(tp_price)  # type: ignore[union-attr]
            ),
        }
        if strategy.config.venue == "IB":
            bracket_kwargs.setdefault("tp_post_only", False)

        assert bracket_kwargs["tp_post_only"] is False


# ---------------------------------------------------------------------------
# Risk helpers
# ---------------------------------------------------------------------------


class TestTemplateStrategyRisk:
    def test_position_allowed_when_under_limit(self) -> None:
        strategy = TemplateStrategy(_make_config(max_position=500, trade_size=100))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        assert strategy._position_allowed() is True

    def test_position_allowed_when_over_limit(self) -> None:
        strategy = TemplateStrategy(_make_config(max_position=100, trade_size=200))
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        assert strategy._position_allowed() is False

    def test_max_daily_loss_exceeded(self) -> None:
        strategy = TemplateStrategy(_make_config(max_daily_loss=100))
        _register_strategy(strategy)
        strategy._daily_loss = 150.0

        assert strategy._max_daily_loss_exceeded() is True

    def test_max_daily_loss_not_exceeded(self) -> None:
        strategy = TemplateStrategy(_make_config(max_daily_loss=100))
        _register_strategy(strategy)
        strategy._daily_loss = 50.0

        assert strategy._max_daily_loss_exceeded() is False


# ---------------------------------------------------------------------------
# on_bar
# ---------------------------------------------------------------------------


class TestTemplateStrategyOnBar:
    def test_ignores_wrong_bar_type(self) -> None:
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        wrong_bar = _make_bar("150.00", "151.00", "149.00", "150.00")
        # Patch bar_type so it does not match
        wrong_bar = MagicMock()
        wrong_bar.bar_type = BarType.from_str("TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL")
        wrong_bar.is_single_price.return_value = False

        strategy.on_bar(wrong_bar)  # should not raise

    def test_ignores_single_price_bar(self) -> None:
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        bar = MagicMock()
        bar.bar_type = BAR_TYPE
        bar.is_single_price.return_value = True

        strategy.on_bar(bar)  # should not raise and not append to _closes
        assert len(strategy._closes) == 0

    def test_appends_close_price(self) -> None:
        strategy = TemplateStrategy(_make_config())
        _register_strategy(strategy)
        _mock_instrument(strategy)
        strategy.on_start()

        bar = _make_bar("150.00", "151.00", "149.00", "150.00")
        strategy.on_bar(bar)

        assert len(strategy._closes) == 1
        assert strategy._closes[0] == 150.0
