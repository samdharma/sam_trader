"""Opening Range Breakout strategy."""

from __future__ import annotations

import pickle
from datetime import time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.events import OrderAccepted, OrderFilled
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

_VENUE_TO_TZ: dict[str, str] = {
    "NASDAQ": "America/New_York",
    "NYSE": "America/New_York",
    "HKEX": "Asia/Hong_Kong",
}


class OrbStrategyConfig(StrategyConfig, frozen=True):  # type: ignore[call-arg]
    """Configuration for ``OrbStrategy`` instances.

    Parameters
    ----------
    instrument_id : str
        The instrument ID for the strategy (e.g. ``"TSLA.NASDAQ"``).
    bar_type : str
        The bar type for the strategy (e.g.
        ``"TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"``).
    first_candle_minutes : int, default 15
        The number of minutes used to establish the opening range.
    trade_size : int, default 100
        The position size per trade.
    confirmation_bars : int, default 1
        Number of consecutive confirming bars required after a breakout
        before entering.
    atr_period : int, default 14
        Lookback period for ATR computation used by the range quality filter.
    min_range_atr_multiple : float, default 0.0
        Minimum required ratio of ``range_width / ATR(atr_period)``. If the
        opening range is narrower than this multiple, the strategy stops for
        the session. 0.0 disables the filter.
    entry_order_type : {"MARKET", "LIMIT", "STOP_MARKET"}, default "MARKET"
        Order type used for breakout entries.
    stop_loss_ticks : int, default 10
        Number of ticks away from entry for the stop-loss trigger.
    take_profit_ticks : int, default 30
        Number of ticks away from entry for the take-profit limit.
    max_position : int, default 500
        Maximum absolute position size.
    max_daily_loss : int, default 1000
        Maximum allowed loss for the day.
    max_trades_per_day : int, default 0
        Maximum number of entries per session.  0 disables the limit.
    trade_cooldown_seconds : int, default 0
        Seconds to wait after position goes flat before entering again.
        0 disables the cooldown.
    session_start : str, default ""
        When to begin accumulating the opening range in the instrument's
        local timezone. Empty string disables.
    max_trade_time : str, default ""
        Stop looking for new breakouts after this time in the instrument's
        local timezone. Empty string disables.
    session_hard_stop : str, default ""
        Close any open position at this time in the instrument's local
        timezone. Empty string disables.
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
    lunch_pause_enabled : bool, default False
        If ``True``, the strategy pauses during a lunch break window.
    lunch_start : str, default ""
        Lunch pause start time in ``HH:MM`` format (instrument local timezone).
        HK default: ``"12:00"``.
    lunch_end : str, default ""
        Lunch pause end time in ``HH:MM`` format (instrument local timezone).
        HK default: ``"13:00"``.

    """

    instrument_id: str
    bar_type: str
    first_candle_minutes: int = 15
    trade_size: int = 100
    confirmation_bars: int = 1
    atr_period: int = 14
    min_range_atr_multiple: float = 0.0
    entry_order_type: Literal["MARKET", "LIMIT", "STOP_MARKET"] = "MARKET"
    stop_loss_ticks: int = 10
    take_profit_ticks: int = 30
    max_position: int = 500
    max_daily_loss: int = 1000
    max_trades_per_day: int = 0
    trade_cooldown_seconds: int = 0
    session_start: str = ""
    max_trade_time: str = ""
    session_hard_stop: str = ""
    risk_per_trade_pct: float = 0.0
    account_risk_currency: float = 0.0
    venue: str = ""
    bundle_id: str = "unknown"
    exchange: str = ""
    futu_code: str = ""
    market: str = "US"
    lunch_pause_enabled: bool = False
    lunch_start: str = ""
    lunch_end: str = ""


class OrbStrategy(Strategy):
    """An opening range breakout strategy.

    Computes the high/low of the first ``first_candle_minutes`` worth of bars.
    After the range is established, enters long when a bar breaks above the
    range high and short when a bar breaks below the range low.

    Parameters
    ----------
    config : OrbStrategyConfig
        The configuration for the instance.

    """

    def __init__(self, config: OrbStrategyConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.instrument_id: InstrumentId | None = None
        self.bar_type: BarType | None = None

        # Range state
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._bars_seen: int = 0
        self._range_established: bool = False
        self._first_candle_bars: int = 0

        # Risk / position state
        self._daily_loss: float = 0.0
        self._position_qty: float = 0.0
        self._position_avg_px: float = 0.0

        # Confirmation state
        self._confirmation_count: int = 0
        self._confirmation_direction: int | None = None
        self._confirmation_prev_low: float | None = None
        self._confirmation_prev_high: float | None = None

        # Bar history for ATR
        self._bar_history: list[tuple[float, float, float]] = []

        # Session time guards
        self._session_start_time: time | None = self._parse_time(config.session_start)
        self._max_trade_time: time | None = self._parse_time(config.max_trade_time)
        self._session_hard_stop: time | None = self._parse_time(
            config.session_hard_stop
        )

        # Trade counter
        self._trades_today: int = 0

        # Rate-limiting: cooldown between trades
        self._last_flat_time_ns: int = 0

        # Entry-order tracking for acceptance-based trade counting
        self._pending_entry_order_ids: set[ClientOrderId] = set()

        # Cached ATR
        self._cached_atr: float | None = None

        # Lunch pause state
        self._lunch_start_time: time | None = self._parse_time(config.lunch_start)
        self._lunch_end_time: time | None = self._parse_time(config.lunch_end)

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

    def _get_timezone_name(self) -> str:
        """Return the IANA timezone name for the instrument's venue."""
        venue = ""
        if self.instrument_id is not None:
            venue = self.instrument_id.venue.value
        elif self.config.instrument_id:
            try:
                venue = InstrumentId.from_str(self.config.instrument_id).venue.value
            except Exception:
                pass
        return _VENUE_TO_TZ.get(venue, "America/New_York")

    def _get_et_time(self) -> time:
        """Return current clock time converted to the instrument's local timezone."""
        tz_name = self._get_timezone_name()
        local = self.clock.utc_now().astimezone(ZoneInfo(tz_name))
        return time(local.hour, local.minute, local.second)

    def _past_session_hard_stop(self) -> bool:
        """Return True if current time is past session_hard_stop."""
        if self._session_hard_stop is None:
            return False
        return self._get_et_time() >= self._session_hard_stop

    def _past_max_trade_time(self) -> bool:
        """Return True if current time is past max_trade_time."""
        if self._max_trade_time is None:
            return False
        return self._get_et_time() > self._max_trade_time

    def _in_range_accumulation_window(self) -> bool:
        """Return True if we are past session_start (or it is disabled)."""
        if self._session_start_time is None:
            return True
        return self._get_et_time() >= self._session_start_time

    def _max_daily_loss_exceeded(self) -> bool:
        """Return True if accumulated daily loss has reached the limit."""
        if self._daily_loss >= self.config.max_daily_loss:
            self.log.warning(
                f"max_daily_loss exceeded: {self._daily_loss:.2f} >= "
                f"{self.config.max_daily_loss:.2f}. Skipping entry."
            )
            return True
        return False

    def _max_trades_per_day_reached(self) -> bool:
        """Return True if the daily trade limit has been reached."""
        if self.config.max_trades_per_day <= 0:
            return False
        if self._trades_today >= self.config.max_trades_per_day:
            self.log.warning(
                f"max_trades_per_day reached: {self._trades_today} >= "
                f"{self.config.max_trades_per_day}. Skipping entry."
            )
            return True
        return False

    def _in_cooldown(self, now_ns: int | None = None) -> bool:
        """Return True if the cooldown period has not yet elapsed.

        Parameters
        ----------
        now_ns : int | None, optional
            Current timestamp in nanoseconds.  When ``None``, reads
            ``self.clock.timestamp_ns()``.  Exposed for testability
            because the Cython ``LiveClock.timestamp_ns`` is read-only.

        """
        if self.config.trade_cooldown_seconds <= 0:
            return False
        if self._last_flat_time_ns == 0:
            return False
        if now_ns is None:
            now_ns = self.clock.timestamp_ns()
        elapsed_ns = now_ns - self._last_flat_time_ns
        cooldown_ns = self.config.trade_cooldown_seconds * 1_000_000_000
        if elapsed_ns < cooldown_ns:
            remaining = (cooldown_ns - elapsed_ns) / 1_000_000_000
            self.log.info(
                f"Cooldown active: {remaining:.1f}s remaining before next entry."
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
            atr=self._cached_atr,
            entry_price=entry_price,
        )

    def _get_sl_distance(self) -> float | None:
        """Return the stop-loss distance in price terms."""
        if self.instrument is None:
            return None
        tick_size = float(self.instrument.price_increment)
        return int(self.config.stop_loss_ticks) * tick_size

    def _compute_atr(self, period: int) -> float | None:
        """Compute simple ATR over the bar history."""
        if len(self._bar_history) < period + 1:
            return None
        tr_values: list[float] = []
        for i in range(1, len(self._bar_history)):
            high, low, close = self._bar_history[i]
            prev_high, prev_low, prev_close = self._bar_history[i - 1]
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            tr_values.append(max(tr1, tr2, tr3))
        return sum(tr_values[-period:]) / period

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

        interval_td = self.bar_type.spec.timedelta
        minutes_per_bar = max(1, int(interval_td.total_seconds()) // 60)
        self._first_candle_bars = max(
            1, self.config.first_candle_minutes // minutes_per_bar
        )

        self.subscribe_bars(self.bar_type)
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_trade_ticks(self.instrument_id)

        # Schedule lunch pause alerts if enabled
        if self.config.lunch_pause_enabled:
            self._schedule_lunch_alerts()

    def on_bar(self, bar: Bar) -> None:
        """Actions to be performed when the strategy receives a bar."""
        if self.bar_type is None or bar.bar_type != self.bar_type:
            return
        if bar.is_single_price():
            return

        # Session hard stop: close any open position
        if self._past_session_hard_stop():
            if not self.portfolio.is_flat(self.instrument_id):
                self.log.info("Session hard stop reached - closing all positions")
                self.close_all_positions(self.instrument_id)
                self.cancel_all_orders(self.instrument_id)
            return

        if not self._range_established:
            if not self._in_range_accumulation_window():
                return
            self._update_range(bar)
            return

        if self._range_high is None or self._range_low is None:
            return

        # If already in a position, bracket orders handle exits
        if not self.portfolio.is_flat(self.instrument_id):
            return

        # Handle active confirmation sequence
        if self._confirmation_direction is not None:
            self._handle_confirmation(bar)
            return

        # Skip new breakouts after max_trade_time
        if self._past_max_trade_time():
            return

        # Rate-limit checks: max trades per day + cooldown between trades
        if self._max_trades_per_day_reached():
            return
        if self._in_cooldown():
            return

        # Look for fresh breakout
        if float(bar.high) > self._range_high:
            self._start_confirmation(1, bar)
        elif float(bar.low) < self._range_low:
            self._start_confirmation(-1, bar)

    def _update_range(self, bar: Bar) -> None:
        """Update the opening range with the given bar."""
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)

        self._bar_history.append((high, low, close))

        if self._range_high is None:
            self._range_high = high
            self._range_low = low
        else:
            current_high = self._range_high
            current_low = self._range_low
            if current_high is not None and current_low is not None:
                self._range_high = max(current_high, high)
                self._range_low = min(current_low, low)

        self._bars_seen += 1
        if self._bars_seen >= self._first_candle_bars:
            self._range_established = True
            self._cached_atr = self._compute_atr(self.config.atr_period)
            self._check_atr_filter()
            self.log.info(
                f"ORB range established after {self._bars_seen} bars: "
                f"{self._range_low:.5f} - {self._range_high:.5f}"
            )

    def _check_atr_filter(self) -> None:
        """Stop the strategy if the opening range is too narrow."""
        assert self._range_high is not None
        assert self._range_low is not None
        range_width = self._range_high - self._range_low

        if self.config.min_range_atr_multiple > 0:
            atr = self._cached_atr or self._compute_atr(self.config.atr_period)
            if atr is None:
                self.log.warning(
                    f"Not enough bars to compute ATR({self.config.atr_period}); "
                    f"skipping range quality filter."
                )
                return
            threshold = self.config.min_range_atr_multiple * atr
            if range_width < threshold:
                self.log.warning(
                    f"Opening range too narrow: width={range_width:.5f}, "
                    f"threshold={threshold:.5f} "
                    f"({self.config.min_range_atr_multiple:.1f}xATR). "
                    f"Stopping strategy for the session."
                )
                self.stop()

    # ------------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------------

    def _start_confirmation(self, direction: int, bar: Bar) -> None:
        """Begin a confirmation sequence after a breakout."""
        self._confirmation_direction = direction
        self._confirmation_count = 1
        self._confirmation_prev_low = float(bar.low)
        self._confirmation_prev_high = float(bar.high)

        if self._confirmation_count >= self.config.confirmation_bars:
            if direction == 1:
                self._enter_long(bar)
            else:
                self._enter_short(bar)
            self._reset_confirmation()
        else:
            side = "LONG" if direction == 1 else "SHORT"
            self.log.info(
                f"Waiting for confirmation: direction={side}, "
                f"count=1/{self.config.confirmation_bars}"
            )

    def _handle_confirmation(self, bar: Bar) -> None:
        """Process the next bar in an active confirmation sequence."""
        direction = self._confirmation_direction
        assert direction is not None
        assert self._confirmation_prev_low is not None
        assert self._confirmation_prev_high is not None

        if direction == 1:  # long
            if float(bar.low) > self._confirmation_prev_low:
                self._confirmation_count += 1
                self._confirmation_prev_low = float(bar.low)
                if self._confirmation_count >= self.config.confirmation_bars:
                    self._enter_long(bar)
                    self._reset_confirmation()
                else:
                    self.log.info(
                        "Waiting for confirmation: "
                        f"count={self._confirmation_count}/"
                        f"{self.config.confirmation_bars}"
                    )
            else:
                self.log.info(
                    "Confirmation failed, resetting: "
                    f"bar.low={float(bar.low):.5f} <= "
                    f"prev_low={self._confirmation_prev_low:.5f}"
                )
                self._reset_confirmation()
        else:  # short
            if float(bar.high) < self._confirmation_prev_high:
                self._confirmation_count += 1
                self._confirmation_prev_high = float(bar.high)
                if self._confirmation_count >= self.config.confirmation_bars:
                    self._enter_short(bar)
                    self._reset_confirmation()
                else:
                    self.log.info(
                        "Waiting for confirmation: "
                        f"count={self._confirmation_count}/"
                        f"{self.config.confirmation_bars}"
                    )
            else:
                self.log.info(
                    "Confirmation failed, resetting: "
                    f"bar.high={float(bar.high):.5f} >= "
                    f"prev_high={self._confirmation_prev_high:.5f}"
                )
                self._reset_confirmation()

    def _reset_confirmation(self) -> None:
        """Clear confirmation state."""
        self._confirmation_count = 0
        self._confirmation_direction = None
        self._confirmation_prev_low = None
        self._confirmation_prev_high = None

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def _enter_long(self, bar: Bar) -> None:
        """Enter a long position with a bracket order."""
        if not self.instrument:
            self.log.error("No instrument loaded")
            return

        if self._max_daily_loss_exceeded():
            return

        entry_price = float(bar.close)
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
                trigger_price=self._range_high,
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
            bracket_kwargs["entry_price"] = self.instrument.make_price(self._range_high)

        # IB venue safety — Interactive Brokers rejects post-only orders
        if self.config.venue == "IB":
            bracket_kwargs.setdefault("tp_post_only", False)

        order_list = self.order_factory.bracket(**bracket_kwargs)
        self.submit_order_list(order_list)
        self._pending_entry_order_ids.add(order_list.orders[0].client_order_id)

    def _enter_short(self, bar: Bar) -> None:
        """Enter a short position with a bracket order."""
        if not self.instrument:
            self.log.error("No instrument loaded")
            return

        if self._max_daily_loss_exceeded():
            return

        entry_price = float(bar.close)
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
                trigger_price=self._range_low,
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
            bracket_kwargs["entry_price"] = self.instrument.make_price(self._range_low)

        if self.config.venue == "IB":
            bracket_kwargs.setdefault("tp_post_only", False)

        order_list = self.order_factory.bracket(**bracket_kwargs)
        self.submit_order_list(order_list)
        self._pending_entry_order_ids.add(order_list.orders[0].client_order_id)

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
        self._pending_entry_order_ids.add(entry_order.client_order_id)

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
    # Order event handling
    # ------------------------------------------------------------------

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """Count accepted entry orders toward the daily trade limit.

        Only entry orders (not stop-loss / take-profit legs of bracket
        orders) are counted.  An order is recognised as an entry order
        when its ``client_order_id`` was recorded at submission time.

        """
        if event.client_order_id in self._pending_entry_order_ids:
            self._pending_entry_order_ids.discard(event.client_order_id)
            self._trades_today += 1
            self.log.info(
                f"Entry order accepted ({event.client_order_id}). "
                f"Trades today: {self._trades_today}/"
                f"{self.config.max_trades_per_day or '∞'}"
            )

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

        # Position fully closed → reset entry tracking + record flat time
        if new_qty == 0:
            self._entry_order = None
            self._last_flat_time_ns = self.clock.timestamp_ns()

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
        self._range_high = None
        self._range_low = None
        self._bars_seen = 0
        self._range_established = False
        self._first_candle_bars = 0
        self._daily_loss = 0.0
        self._position_qty = 0.0
        self._position_avg_px = 0.0
        self._reset_confirmation()
        self._bar_history = []
        self._trades_today = 0
        self._cached_atr = None
        self._entry_order = None
        self._last_flat_time_ns = 0
        self._pending_entry_order_ids = set()

    def on_save(self) -> dict[str, bytes]:
        """Actions to be performed when the strategy is saved."""
        return {
            "state": pickle.dumps(
                {
                    "_range_high": self._range_high,
                    "_range_low": self._range_low,
                    "_bars_seen": self._bars_seen,
                    "_range_established": self._range_established,
                    "_first_candle_bars": self._first_candle_bars,
                    "_daily_loss": self._daily_loss,
                    "_position_qty": self._position_qty,
                    "_position_avg_px": self._position_avg_px,
                    "_confirmation_count": self._confirmation_count,
                    "_confirmation_direction": self._confirmation_direction,
                    "_confirmation_prev_low": self._confirmation_prev_low,
                    "_confirmation_prev_high": self._confirmation_prev_high,
                    "_bar_history": self._bar_history,
                    "_trades_today": self._trades_today,
                    "_cached_atr": self._cached_atr,
                    "_last_flat_time_ns": self._last_flat_time_ns,
                }
            )
        }

    def on_load(self, state: dict[str, bytes]) -> None:
        """Actions to be performed when the strategy is loaded."""
        raw = state.get("state")
        if raw is None:
            return
        data = pickle.loads(raw)
        self._range_high = data.get("_range_high")
        self._range_low = data.get("_range_low")
        self._bars_seen = data.get("_bars_seen", 0)
        self._range_established = data.get("_range_established", False)
        self._first_candle_bars = data.get("_first_candle_bars", 0)
        self._daily_loss = data.get("_daily_loss", 0.0)
        self._position_qty = data.get("_position_qty", 0.0)
        self._position_avg_px = data.get("_position_avg_px", 0.0)
        self._confirmation_count = data.get("_confirmation_count", 0)
        self._confirmation_direction = data.get("_confirmation_direction")
        self._confirmation_prev_low = data.get("_confirmation_prev_low")
        self._confirmation_prev_high = data.get("_confirmation_prev_high")
        self._bar_history = data.get("_bar_history", [])
        self._trades_today = data.get("_trades_today", 0)
        self._cached_atr = data.get("_cached_atr")
        self._last_flat_time_ns = data.get("_last_flat_time_ns", 0)

    def on_dispose(self) -> None:
        """Actions to be performed when the strategy is disposed."""

    # ------------------------------------------------------------------
    # Lunch pause
    # ------------------------------------------------------------------

    def _schedule_lunch_alerts(self) -> None:
        """Schedule both lunch pause and resume alerts for the next occurrence."""
        if self._lunch_start_time is None or self._lunch_end_time is None:
            self.log.warning(
                "Lunch pause enabled but lunch_start or lunch_end not valid; "
                "skipping lunch pause scheduling."
            )
            return
        self._schedule_single_lunch_alert(
            "orb_lunch_pause", self._lunch_start_time, self._on_lunch_pause
        )
        self._schedule_single_lunch_alert(
            "orb_lunch_resume", self._lunch_end_time, self._on_lunch_resume
        )

    def _schedule_single_lunch_alert(
        self,
        name: str,
        target_time: time,
        callback,
    ) -> None:
        """Schedule a single lunch time alert at *target_time* (local time).

        Converts the local time to a naive UTC datetime for
        ``LiveClock.set_time_alert()``.  If today's occurrence has already
        passed, schedules for tomorrow.
        """
        tz_name = self._get_timezone_name()
        tz = ZoneInfo(tz_name)
        now_utc = self.clock.utc_now()
        now_local = now_utc.astimezone(tz)

        target_local = now_local.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=target_time.second,
            microsecond=0,
        )
        if target_local <= now_local:
            target_local += timedelta(days=1)

        target_utc = target_local.astimezone(ZoneInfo("UTC"))
        target_utc_naive = target_utc.replace(tzinfo=None)

        self.clock.set_time_alert(name, target_utc_naive, callback, override=True)
        self.log.info(
            f"Lunch pause: '{name}' alert scheduled for "
            f"{target_utc_naive.isoformat()} UTC"
        )

    def _on_lunch_pause(self, alert=None) -> None:  # noqa: ARG002
        """Callback executed at lunch_start — pauses the strategy."""
        self.pause()
        self.log.info(
            f"Lunch pause: strategy paused at "
            f"{self.config.lunch_start} ({self._get_timezone_name()})"
        )
        # Re-schedule for tomorrow
        if self._lunch_start_time is not None:
            self._schedule_single_lunch_alert(
                "orb_lunch_pause", self._lunch_start_time, self._on_lunch_pause
            )

    def _on_lunch_resume(self, alert=None) -> None:  # noqa: ARG002
        """Callback executed at lunch_end — resumes the strategy."""
        self.resume()
        self.log.info(
            f"Lunch pause: strategy resumed at "
            f"{self.config.lunch_end} ({self._get_timezone_name()})"
        )
        # Re-schedule for tomorrow
        if self._lunch_end_time is not None:
            self._schedule_single_lunch_alert(
                "orb_lunch_resume", self._lunch_end_time, self._on_lunch_resume
            )
