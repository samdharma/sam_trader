"""Momentum at open strategy."""

from __future__ import annotations

import pickle
from collections import deque
from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

_VENUE_TO_TZ: dict[str, str] = {
    "NASDAQ": "America/New_York",
    "NYSE": "America/New_York",
    "HKEX": "Asia/Hong_Kong",
}


class MomentumStrategyConfig(StrategyConfig, frozen=True):  # type: ignore[call-arg]
    """Configuration for ``MomentumStrategy`` instances.

    Parameters
    ----------
    instrument_id : str
        The instrument ID for the strategy (e.g. ``"TSLA.NASDAQ"``).
    bar_type : str
        The bar type for the strategy (e.g.
        ``"TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"``).
    window : int, default 20
        The momentum lookback window in bars.
    session_start : str, default ""
        Local session start time (``HH:MM:SS`` or ``HH:MM``) in the
        instrument's local timezone.  Empty string disables the guard.
    session_end : str, default ""
        Local session end time (same format).  Empty string disables.
    trade_size : int, default 100
        The position size per trade.
    allowed_directions : tuple[str, ...], default ("LONG", "SHORT")
        Allowed trade directions.  ``("LONG",)`` filters short signals;
        ``("SHORT",)`` filters long signals.
    entry_order_type : {"MARKET", "LIMIT", "STOP_MARKET"}, default "MARKET"
        Order type used for momentum entries.
    stop_loss_ticks : int, default 10
        Number of ticks away from entry for the stop-loss trigger.
    take_profit_ticks : int, default 30
        Number of ticks away from entry for the take-profit limit.
    max_position : int, default 500
        Maximum absolute position size.
    max_daily_loss : int, default 1000
        Maximum allowed loss for the day.
    risk_per_trade_pct : float, default 0.0
        Fraction of account capital to risk per trade. 0.0 disables
        dynamic sizing and uses fixed ``trade_size``.
    account_risk_currency : float, default 0.0
        Account capital available for risk calculation.
    venue : str, default ""
        Target venue (``"FUTU"`` or ``"IB"``) injected by the bundle loader.
    bundle_id : str, default "unknown"
        Bundle identifier injected by the bundle loader.
    exchange : str, default ""
        Exchange override injected by the bundle loader.
    futu_code : str, default ""
        Futu security code injected by the bundle loader.
    market : str, default "US"
        Target market (``"US"`` or ``"HK"``) injected by the bundle loader.

    """

    instrument_id: str
    bar_type: str
    window: int = 20
    session_start: str = ""
    session_end: str = ""
    trade_size: int = 100
    allowed_directions: tuple[str, ...] = ("LONG", "SHORT")
    entry_order_type: Literal["MARKET", "LIMIT", "STOP_MARKET"] = "MARKET"
    stop_loss_ticks: int = 10
    take_profit_ticks: int = 30
    max_position: int = 500
    max_daily_loss: int = 1000
    risk_per_trade_pct: float = 0.0
    account_risk_currency: float = 0.0
    venue: str = ""
    bundle_id: str = "unknown"
    exchange: str = ""
    futu_code: str = ""
    market: str = "US"


class MomentumStrategy(Strategy):
    """A momentum strategy that trades within a session window.

    Computes momentum as the difference between the current bar close and the
    close ``window`` bars ago.  Enters long when momentum is positive and short
    when momentum is negative, but only during the configured session window.

    Parameters
    ----------
    config : MomentumStrategyConfig
        The configuration for the instance.

    """

    def __init__(self, config: MomentumStrategyConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.instrument_id: InstrumentId | None = None
        self.bar_type: BarType | None = None

        # Momentum state
        self._closes: deque[float] = deque(maxlen=config.window + 1)

        # Risk / position state
        self._daily_loss: float = 0.0
        self._position_qty: float = 0.0
        self._position_avg_px: float = 0.0

        # Session time guards
        self._session_start_time: time | None = self._parse_time(config.session_start)
        self._session_end_time: time | None = self._parse_time(config.session_end)

        # Trade counter
        self._trades_today: int = 0

        # STOP_MARKET entry tracking
        self._entry_order = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_time(self, value: str) -> time | None:
        """Parse a HH:MM:SS or HH:MM string into a time object."""
        if not value or not value.strip():
            return None
        parts = value.strip().split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            second = int(parts[2]) if len(parts) > 2 else 0
            return time(hour, minute, second)
        except (ValueError, IndexError):
            self.log.warning(f"Invalid time string '{value}'; treating as disabled")
            return None

    def _get_et_time(self) -> time:
        """Return current clock time converted to the instrument's local timezone."""
        venue = ""
        if self.instrument_id is not None:
            venue = self.instrument_id.venue.value
        elif self.config.instrument_id:
            try:
                venue = InstrumentId.from_str(self.config.instrument_id).venue.value
            except Exception:
                pass
        tz_name = _VENUE_TO_TZ.get(venue, "America/New_York")
        local = self.clock.utc_now().astimezone(ZoneInfo(tz_name))
        return time(local.hour, local.minute, local.second)

    def _in_session(self) -> bool:
        """Return True if the current clock time is within the session window."""
        now_et = self._get_et_time()
        if self._session_start_time is not None and now_et < self._session_start_time:
            return False
        if self._session_end_time is not None and now_et > self._session_end_time:
            return False
        return True

    def _compute_momentum(self) -> float:
        """Compute momentum as current close minus close ``window`` bars ago."""
        current = self._closes[-1]
        previous = self._closes[0]
        return current - previous

    def _max_daily_loss_exceeded(self) -> bool:
        """Return True if accumulated daily loss has reached the limit."""
        if self._daily_loss >= self.config.max_daily_loss:
            self.log.warning(
                f"max_daily_loss exceeded: {self._daily_loss:.2f} >= "
                f"{self.config.max_daily_loss:.2f}. Skipping entry."
            )
            return True
        return False

    def _position_allowed(
        self,
        entry_price: float | None = None,
        trade_size: int | None = None,
    ) -> bool:
        """Return True if a new position is allowed under risk limits."""
        if trade_size is None:
            trade_size = self.config.trade_size
        current_qty = float(abs(self.portfolio.net_position(self.instrument_id)))
        new_qty = float(trade_size)
        if (current_qty + new_qty) > self.config.max_position:
            return False
        return True

    def _get_sl_distance(self) -> float | None:
        """Return the stop-loss distance in price terms."""
        if self.instrument is None:
            return None
        tick_size = float(self.instrument.price_increment)
        return int(self.config.stop_loss_ticks) * tick_size

    def _compute_trade_size(
        self, direction: int, entry_price: float | None = None
    ) -> int:
        """Compute the trade size from risk parameters."""
        from sam_trader.strategies.common import compute_risk_based_size

        if self.config.risk_per_trade_pct <= 0:
            return int(self.config.trade_size)

        sl_distance = self._get_sl_distance()
        if sl_distance is None or sl_distance <= 0:
            return int(self.config.trade_size)

        tick_size = float(self.instrument.price_increment) if self.instrument else 0.01
        return compute_risk_based_size(
            risk_per_trade_pct=self.config.risk_per_trade_pct,
            account_risk_currency=self.config.account_risk_currency,
            sl_distance=sl_distance,
            tick_size=tick_size,
            max_position=self.config.max_position,
            trade_size=self.config.trade_size,
            atr=None,
            entry_price=entry_price,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        self.instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self.bar_type = BarType.from_str(self.config.bar_type)
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.instrument_id}")
            self.stop()
            return

        self.subscribe_bars(self.bar_type)
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_trade_ticks(self.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        """Actions to be performed when the strategy receives a bar."""
        if self.bar_type is None or bar.bar_type != self.bar_type:
            return
        if bar.is_single_price():
            return

        if not self._in_session():
            return

        close_price = float(bar.close)
        self._closes.append(close_price)

        if len(self._closes) < self.config.window + 1:
            return  # Not enough bars yet

        momentum = self._compute_momentum()

        if momentum > 0:
            if self.portfolio.is_flat(self.instrument_id):
                if "LONG" in self.config.allowed_directions:
                    self._enter_long(bar)
            elif self.portfolio.is_net_short(self.instrument_id):
                self.close_all_positions(self.instrument_id)
                if "LONG" in self.config.allowed_directions:
                    self._enter_long(bar)
        elif momentum < 0:
            if self.portfolio.is_flat(self.instrument_id):
                if "SHORT" in self.config.allowed_directions:
                    self._enter_short(bar)
            elif self.portfolio.is_net_long(self.instrument_id):
                self.close_all_positions(self.instrument_id)
                if "SHORT" in self.config.allowed_directions:
                    self._enter_short(bar)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def _enter_long(self, last_bar: Bar) -> None:
        """Enter a long position with a bracket order."""
        if not self.instrument:
            self.log.error("No instrument loaded")
            return

        if self._max_daily_loss_exceeded():
            return

        entry_price = float(last_bar.close)
        trade_size = self._compute_trade_size(1, entry_price)

        if not self._position_allowed(entry_price, trade_size):
            return

        tick_size = float(self.instrument.price_increment)
        sl_price = entry_price - self.config.stop_loss_ticks * tick_size
        tp_price = entry_price + self.config.take_profit_ticks * tick_size

        if self.config.entry_order_type == "STOP_MARKET":
            self._submit_stop_market_entry(
                OrderSide.BUY,
                trade_size,
                trigger_price=entry_price,
            )
            return

        bracket_kwargs: dict = {
            "instrument_id": self.instrument_id,
            "order_side": OrderSide.BUY,
            "quantity": self.instrument.make_qty(trade_size),
            "time_in_force": TimeInForce.GTC,
            "sl_trigger_price": self.instrument.make_price(sl_price),
            "tp_price": self.instrument.make_price(tp_price),
        }

        if self.config.entry_order_type == "MARKET":
            bracket_kwargs["entry_order_type"] = OrderType.MARKET
        elif self.config.entry_order_type == "LIMIT":
            bracket_kwargs["entry_order_type"] = OrderType.LIMIT
            bracket_kwargs["entry_price"] = self.instrument.make_price(entry_price)

        if self.config.venue == "IB":
            bracket_kwargs.setdefault("tp_post_only", False)

        order_list = self.order_factory.bracket(**bracket_kwargs)
        self.submit_order_list(order_list)
        self._trades_today += 1

    def _enter_short(self, last_bar: Bar) -> None:
        """Enter a short position with a bracket order."""
        if not self.instrument:
            self.log.error("No instrument loaded")
            return

        if self._max_daily_loss_exceeded():
            return

        entry_price = float(last_bar.close)
        trade_size = self._compute_trade_size(-1, entry_price)

        if not self._position_allowed(entry_price, trade_size):
            return

        tick_size = float(self.instrument.price_increment)
        sl_price = entry_price + self.config.stop_loss_ticks * tick_size
        tp_price = entry_price - self.config.take_profit_ticks * tick_size

        if self.config.entry_order_type == "STOP_MARKET":
            self._submit_stop_market_entry(
                OrderSide.SELL,
                trade_size,
                trigger_price=entry_price,
            )
            return

        bracket_kwargs: dict = {
            "instrument_id": self.instrument_id,
            "order_side": OrderSide.SELL,
            "quantity": self.instrument.make_qty(trade_size),
            "time_in_force": TimeInForce.GTC,
            "sl_trigger_price": self.instrument.make_price(sl_price),
            "tp_price": self.instrument.make_price(tp_price),
        }

        if self.config.entry_order_type == "MARKET":
            bracket_kwargs["entry_order_type"] = OrderType.MARKET
        elif self.config.entry_order_type == "LIMIT":
            bracket_kwargs["entry_order_type"] = OrderType.LIMIT
            bracket_kwargs["entry_price"] = self.instrument.make_price(entry_price)

        if self.config.venue == "IB":
            bracket_kwargs.setdefault("tp_post_only", False)

        order_list = self.order_factory.bracket(**bracket_kwargs)
        self.submit_order_list(order_list)
        self._trades_today += 1

    def _submit_stop_market_entry(
        self,
        order_side: OrderSide,
        trade_size: int,
        trigger_price: float | None,
    ) -> None:
        """Submit a standalone stop-market entry order."""
        if self.instrument is None or trigger_price is None:
            return
        entry_order = self.order_factory.stop_market(
            instrument_id=self.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(trade_size),
            trigger_price=self.instrument.make_price(trigger_price),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(entry_order)
        self._entry_order = entry_order
        self._trades_today += 1

    def _submit_protective_orders(self, order_side: OrderSide) -> None:
        """Submit SL and TP orders after a STOP_MARKET entry fill."""
        if self.instrument is None or self._position_qty == 0:
            return

        side = 1 if self._position_qty > 0 else -1
        entry_price = self._position_avg_px
        tick_size = float(self.instrument.price_increment)
        qty = abs(self._position_qty)

        if side == 1:
            sl_price = entry_price - self.config.stop_loss_ticks * tick_size
            tp_price = entry_price + self.config.take_profit_ticks * tick_size
        else:
            sl_price = entry_price + self.config.stop_loss_ticks * tick_size
            tp_price = entry_price - self.config.take_profit_ticks * tick_size

        sl_order = self.order_factory.stop_market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL if side == 1 else OrderSide.BUY,
            quantity=self.instrument.make_qty(qty),
            trigger_price=self.instrument.make_price(sl_price),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(sl_order)

        tp_kwargs: dict = {
            "instrument_id": self.instrument_id,
            "order_side": OrderSide.SELL if side == 1 else OrderSide.BUY,
            "quantity": self.instrument.make_qty(qty),
            "price": self.instrument.make_price(tp_price),
            "time_in_force": TimeInForce.GTC,
        }
        if self.config.venue == "IB":
            tp_kwargs.setdefault("post_only", False)

        tp_order = self.order_factory.limit(**tp_kwargs)
        self.submit_order(tp_order)

    # ------------------------------------------------------------------
    # Fill handling
    # ------------------------------------------------------------------

    def on_order_filled(self, event: OrderFilled) -> None:
        """Update position tracking and handle STOP_MARKET protective orders."""
        fill_qty = float(event.last_qty.as_double())
        fill_px = float(event.last_px.as_double())
        commission = float(event.commission.as_double())

        prev_qty = self._position_qty
        prev_avg_px = self._position_avg_px

        if event.order_side == OrderSide.BUY:
            new_qty = prev_qty + fill_qty
        else:
            new_qty = prev_qty - fill_qty

        # Count trades opened from flat
        if prev_qty == 0 and new_qty != 0:
            self._trades_today += 1

        # Daily loss tracking
        if prev_qty > 0 and event.order_side == OrderSide.SELL:
            closed_qty = min(fill_qty, prev_qty)
            pnl = (fill_px - prev_avg_px) * closed_qty - commission
            if pnl < 0:
                self._daily_loss += abs(pnl)
        elif prev_qty < 0 and event.order_side == OrderSide.BUY:
            closed_qty = min(fill_qty, abs(prev_qty))
            pnl = (prev_avg_px - fill_px) * closed_qty - commission
            if pnl < 0:
                self._daily_loss += abs(pnl)

        # Update average price
        if new_qty == 0:
            self._position_avg_px = 0.0
        elif prev_qty == 0:
            self._position_avg_px = fill_px
        elif (prev_qty > 0 and new_qty > 0) or (prev_qty < 0 and new_qty < 0):
            if abs(new_qty) > abs(prev_qty):
                total_value = abs(prev_qty) * prev_avg_px + fill_qty * fill_px
                self._position_avg_px = total_value / abs(new_qty)
        else:
            self._position_avg_px = fill_px

        self._position_qty = new_qty

        # STOP_MARKET entry fill → submit protective orders
        if (
            self._entry_order is not None
            and event.client_order_id == self._entry_order.client_order_id
        ):
            self._submit_protective_orders(event.order_side)
            self._entry_order = None

        # Position fully closed → reset entry tracking
        if new_qty == 0:
            self._entry_order = None

    # ------------------------------------------------------------------
    # Stop / reset / save / load
    # ------------------------------------------------------------------

    def on_stop(self) -> None:
        """Actions to be performed on strategy stop."""
        if self.instrument_id is not None and self.bar_type is not None:
            self.cancel_all_orders(self.instrument_id)
            self.close_all_positions(self.instrument_id)
            self.unsubscribe_bars(self.bar_type)

    def on_reset(self) -> None:
        """Actions to be performed on strategy reset."""
        self._closes.clear()
        self._daily_loss = 0.0
        self._position_qty = 0.0
        self._position_avg_px = 0.0
        self._trades_today = 0
        self._entry_order = None

    def on_save(self) -> dict[str, bytes]:
        """Actions to be performed when the strategy is saved."""
        return {
            "state": pickle.dumps(
                {
                    "_closes": list(self._closes),
                    "_daily_loss": self._daily_loss,
                    "_position_qty": self._position_qty,
                    "_position_avg_px": self._position_avg_px,
                    "_trades_today": self._trades_today,
                }
            )
        }

    def on_load(self, state: dict[str, bytes]) -> None:
        """Actions to be performed when the strategy is loaded."""
        raw = state.get("state")
        if raw is None:
            return
        data = pickle.loads(raw)
        closes = data.get("_closes", [])
        self._closes.clear()
        self._closes.extend(closes)
        self._daily_loss = data.get("_daily_loss", 0.0)
        self._position_qty = data.get("_position_qty", 0.0)
        self._position_avg_px = data.get("_position_avg_px", 0.0)
        self._trades_today = data.get("_trades_today", 0)

    def on_dispose(self) -> None:
        """Actions to be performed when the strategy is disposed."""
