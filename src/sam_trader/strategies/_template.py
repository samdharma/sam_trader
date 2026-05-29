"""Copy-paste template for new SAM Trader strategies.

This file is **not** imported by ``__init__.py`` (the leading underscore
prevents that).  Copy it to a new module, rename the classes, and implement
your logic.

Design rules (see ``docs/reference/SAM_TRADER_V3_PLAN.md``):
- Inherit from ``nautilus_trader.trading.strategy.Strategy``.
- Use a frozen ``StrategyConfig`` dataclass for parameters.
- Load the strategy via YAML bundles (``config/bundles.yaml``).
- Never hard-code strategy imports in ``main.py``.
- Use venue-aware order helpers from ``strategies.common`` for IB safety.
"""

from __future__ import annotations

import pickle
from collections import deque
from typing import Literal

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderAccepted, OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from sam_trader.strategies.common import make_bracket

# =============================================================================
# Strategy config
# =============================================================================
# Every strategy MUST have a dedicated ``StrategyConfig`` subclass so the
# ``BundleLoader`` can construct it from YAML.  Keep fields flat (no nested
# dataclasses) so msgspec serialization works cleanly.


class TemplateStrategyConfig(StrategyConfig, frozen=True):  # type: ignore[call-arg]
    """Configuration for ``TemplateStrategy`` instances.

    Copy this class, rename it, and add/remove fields for your strategy.

    Parameters
    ----------
    instrument_id : str
        The instrument to trade (e.g. ``"TSLA.NASDAQ"``).
    bar_type : str
        The bar aggregation the strategy listens to
        (e.g. ``"TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"``).
    trade_size : int, default 100
        Position size per entry signal.
    entry_order_type : {"MARKET", "LIMIT", "STOP_MARKET"}, default "MARKET"
        Order type used for entry signals.
    stop_loss_ticks : int, default 10
        Number of ticks away from entry for the stop-loss trigger.
    take_profit_ticks : int, default 30
        Number of ticks away from entry for the take-profit limit.
    max_position : int, default 500
        Maximum absolute position size.
    max_daily_loss : int, default 1000
        Maximum allowed loss for the day.
    session_start : str, default ""
        Local session start time (``HH:MM:SS`` or ``HH:MM``).  Empty disables.
    session_end : str, default ""
        Local session end time (same format).  Empty disables.
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
    max_consecutive_rejections : int, default 10
        Number of consecutive order rejections before the strategy
        auto-disables.  Set to 0 to disable the circuit breaker.
        Resets on the first accepted order.
    lunch_pause_enabled : bool, default False
        If ``True``, the strategy pauses during a lunch break window.
    lunch_start : str, default ""
        Lunch pause start time in ``HH:MM`` format (instrument local timezone).
        HK default: ``"12:00"``.
    lunch_end : str, default ""
        Lunch pause end time in ``HH:MM`` format (instrument local timezone).
        HK default: ``"13:00"``.

    # -- Add your own parameters below ----------------------------------------
    # window : int, default 20
    #     Example: lookback window for an indicator.

    """

    instrument_id: str
    bar_type: str
    trade_size: int = 100
    entry_order_type: Literal["MARKET", "LIMIT", "STOP_MARKET"] = "MARKET"
    stop_loss_ticks: int = 10
    take_profit_ticks: int = 30
    max_position: int = 500
    max_daily_loss: int = 1000
    session_start: str = ""
    session_end: str = ""
    venue: str = ""
    bundle_id: str = "unknown"
    exchange: str = ""
    futu_code: str = ""
    market: str = "US"
    max_consecutive_rejections: int = 10
    lunch_pause_enabled: bool = False
    lunch_start: str = ""
    lunch_end: str = ""

    # -- Example custom fields (uncomment and adapt as needed) -----------------
    # window: int = 20


# =============================================================================
# Strategy implementation
# =============================================================================


class TemplateStrategy(Strategy):
    """Well-documented template showing every common hook and pattern.

    Copy this class, rename it, and fill in the logic.  The methods below are
    ordered by typical life-cycle: ``on_start`` → ``on_bar`` → ``on_stop``.

    Parameters
    ----------
    config : TemplateStrategyConfig
        The configuration for the instance.

    """

    def __init__(self, config: TemplateStrategyConfig) -> None:
        super().__init__(config)

        # Parsed identifiers — set in ``on_start`` so the YAML bundle can pass
        # plain strings.
        self.instrument: Instrument | None = None
        self.instrument_id: InstrumentId | None = None
        self.bar_type: BarType | None = None

        # -- Add your own state variables here ---------------------------------
        # self._closes: deque[float] = deque(maxlen=config.window + 1)
        self._closes: deque[float] = deque()

        # Risk / position state
        self._daily_loss: float = 0.0
        self._position_qty: float = 0.0
        self._position_avg_px: float = 0.0

        # Order rejection circuit breaker
        self._rejection_count: int = 0
        self._rejection_disabled: bool = False

    # -------------------------------------------------------------------------
    # on_start
    # -------------------------------------------------------------------------
    # Called once when the TradingNode starts.  Use it to:
    #   1. Parse config strings into Nautilus types.
    #   2. Resolve the instrument from the cache.
    #   3. Subscribe to market data (bars, quotes, trades, etc.).
    #   4. Register custom indicators.
    #   5. Initialise any internal state that depends on the trading runtime.
    # -------------------------------------------------------------------------

    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        # 1. Parse identifiers --------------------------------------------------
        # ``instrument_id`` and ``bar_type`` are strings in the config so YAML
        # bundles can construct the config without importing Nautilus types.
        self.instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self.bar_type = BarType.from_str(self.config.bar_type)

        # 2. Resolve instrument -------------------------------------------------
        # ``self.cache`` is provided by Nautilus after ``register()`` is called.
        # If the instrument is missing, log an error and stop gracefully.
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.instrument_id}")
            self.stop()
            return

        # 3. Subscribe to data --------------------------------------------------
        # Subscribe to the bar type defined in config.  The TradingNode will
        # route matching bars to ``on_bar()`` automatically.
        self.subscribe_bars(self.bar_type)

        # -- Additional subscriptions (uncomment as needed) ---------------------
        # self.subscribe_quote_ticks(self.instrument_id)
        # self.subscribe_trade_ticks(self.instrument_id)

        # 4. Lunch pause scheduling (optional) ----------------------------------
        # If your strategy needs to pause during a lunch break (e.g., HK market
        # 12:00-13:00 HKT), add ``lunch_pause_enabled``, ``lunch_start``, and
        # ``lunch_end`` to your config and call ``_schedule_lunch_alerts()`` here:
        #
        #   if self.config.lunch_pause_enabled:
        #       self._schedule_lunch_alerts()
        #
        # See ``orb.py`` or ``momentum.py`` for the full implementation pattern
        # (``_get_timezone_name()``, ``_schedule_single_lunch_alert()``,
        # ``_on_lunch_pause()``, ``_on_lunch_resume()``).

        # 5. Register indicators ------------------------------------------------
        # Nautilus has built-in indicator adapters.  Example:
        #
        #   from nautilus_trader.indicators.average.ema import ExponentialMovingAverage
        #   self.ema = ExponentialMovingAverage(self.config.window)
        #   self.register_indicator_for_bars(self.bar_type, self.ema)
        #
        # Once registered, the indicator is updated automatically before
        # ``on_bar()`` is invoked.

    # -------------------------------------------------------------------------
    # on_bar
    # -------------------------------------------------------------------------
    # Called every time a bar of the subscribed ``BarType`` is published.
    # Use it to:
    #   1. Update custom indicators (if not using ``register_indicator_for_bars``).
    #   2. Evaluate entry/exit signals.
    #   3. Submit orders via ``self.order_factory``.
    # -------------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        """Actions to be performed when the strategy receives a bar."""
        # Guard against bars from unexpected aggregations.
        if self.bar_type is None or bar.bar_type != self.bar_type:
            return

        # Guard against bars with no price range (e.g. pre-market idle bar).
        if bar.is_single_price():
            return

        # Circuit breaker: skip all bar processing while disabled.
        # Add this guard to every strategy's ``on_bar``.
        # if self._rejection_disabled:
        #     return

        # -- Session time guards -----------------------------------------------
        # If your strategy accumulates data before trading (e.g. an opening
        # range), gate accumulation behind a session start check.  Example:
        #
        #   if not self._range_established:
        #       if not self._in_range_accumulation_window():
        #           return
        #       self._update_range(bar)
        #       return
        #
        # The ``_in_range_accumulation_window()`` / ``_in_session()`` pattern
        # compares the current clock time against ``config.session_start``.
        # Without this guard, pre-market bars are incorrectly included.

        # -- Update indicators / state -----------------------------------------
        close_price = float(bar.close)
        self._closes.append(close_price)

        # Example: skip until enough history
        # if len(self._closes) < self.config.window + 1:
        #     return

        # -- Evaluate signal ---------------------------------------------------
        # Example: simple momentum signal
        # momentum = self._closes[-1] - self._closes[0]
        # if momentum > 0:
        #     self._enter_long(bar)
        # elif momentum < 0:
        #     self._enter_short(bar)

        # NOTE: Do NOT submit orders directly here for a real strategy.
        #       Call a private helper (``_enter_long``, ``_enter_short``, etc.)
        #       so the logic is testable and reusable.

    # -------------------------------------------------------------------------
    # Order helpers
    # -------------------------------------------------------------------------
    # ``self.order_factory`` creates Order objects.  The strategy then submits
    # them with ``self.submit_order()`` or ``self.submit_order_list()``.
    #
    # Bracket orders (entry + SL + TP) are the preferred pattern.
    #
    # **Venue-aware patterns:**
    #
    # 1. Using ``make_bracket`` from ``strategies.common`` (RECOMMENDED):
    #    Automatically injects IB-safe defaults based on ``instrument_id.venue``.
    #
    #    order_list = make_bracket(
    #        self.order_factory,
    #        instrument_id=self.instrument_id,
    #        order_side=OrderSide.BUY,
    #        quantity=self.instrument.make_qty(trade_size),
    #        time_in_force=TimeInForce.GTC,
    #        sl_trigger_price=self.instrument.make_price(sl_price),
    #        tp_price=self.instrument.make_price(tp_price),
    #    )
    #    self.submit_order_list(order_list)
    #
    # 2. Using ``self.order_factory.bracket()`` directly with venue guard:
    #    Required when you need to set ``entry_order_type`` or other kwargs
    #    that ``make_bracket`` does not expose.
    #
    #    bracket_kwargs = {
    #        "instrument_id": self.instrument_id,
    #        "order_side": OrderSide.BUY,
    #        "quantity": self.instrument.make_qty(trade_size),
    #        "time_in_force": TimeInForce.GTC,
    #        "sl_trigger_price": self.instrument.make_price(sl_price),
    #        "tp_price": self.instrument.make_price(tp_price),
    #    }
    #    if self.config.venue == "IB":
    #        bracket_kwargs.setdefault("tp_post_only", False)
    #    order_list = self.order_factory.bracket(**bracket_kwargs)
    #    self.submit_order_list(order_list)
    #
    # 3. Standalone limit order via ``make_limit`` (RECOMMENDED):
    #
    #    tp_order = make_limit(
    #        self.order_factory,
    #        instrument_id=self.instrument_id,
    #        order_side=OrderSide.SELL,
    #        quantity=self.instrument.make_qty(qty),
    #        price=self.instrument.make_price(tp_price),
    #        time_in_force=TimeInForce.GTC,
    #    )
    #    self.submit_order(tp_order)
    #
    # 4. Standalone limit order directly with venue guard:
    #
    #    tp_kwargs = {
    #        "instrument_id": self.instrument_id,
    #        "order_side": OrderSide.SELL,
    #        "quantity": self.instrument.make_qty(qty),
    #        "price": self.instrument.make_price(tp_price),
    #        "time_in_force": TimeInForce.GTC,
    #    }
    #    if self.config.venue == "IB":
    #        tp_kwargs.setdefault("post_only", False)
    #    tp_order = self.order_factory.limit(**tp_kwargs)
    #    self.submit_order(tp_order)
    # -------------------------------------------------------------------------

    def _enter_long(self, last_bar: Bar) -> None:
        """Enter a long position with a bracket order.

        Steps:
        1. Verify ``self.instrument`` is loaded.
        2. Check risk limits via ``_position_allowed()``.
        3. Compute SL / TP prices from config tick distances.
        4. Build bracket order list (venue-aware).
        5. Submit with ``submit_order_list()``.
        """
        if not self.instrument or self.instrument_id is None:
            self.log.error("No instrument loaded")
            return

        if not self._position_allowed():
            return

        trade_size = self.config.trade_size
        tick_size = float(self.instrument.price_increment)
        entry_price = float(last_bar.close)
        sl_price = entry_price - self.config.stop_loss_ticks * tick_size
        tp_price = entry_price + self.config.take_profit_ticks * tick_size

        # -- Venue-aware bracket order -----------------------------------------
        # Example using make_bracket (recommended) — auto-detects IB venue:
        order_list = make_bracket(
            self.order_factory,
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(trade_size),
            time_in_force=TimeInForce.GTC,
            sl_trigger_price=self.instrument.make_price(sl_price),
            tp_price=self.instrument.make_price(tp_price),
        )

        # -- Alternative: direct factory call with venue guard ------------------
        # Use this when you need entry_order_type or other kwargs.
        #
        # bracket_kwargs: dict = {
        #     "instrument_id": self.instrument_id,
        #     "order_side": OrderSide.BUY,
        #     "quantity": self.instrument.make_qty(trade_size),
        #     "time_in_force": TimeInForce.GTC,
        #     "sl_trigger_price": self.instrument.make_price(sl_price),
        #     "tp_price": self.instrument.make_price(tp_price),
        # }
        # if self.config.entry_order_type == "MARKET":
        #     bracket_kwargs["entry_order_type"] = OrderType.MARKET
        # elif self.config.entry_order_type == "LIMIT":
        #     bracket_kwargs["entry_order_type"] = OrderType.LIMIT
        #     bracket_kwargs["entry_price"] = self.instrument.make_price(entry_price)
        # if self.config.venue == "IB":
        #     bracket_kwargs.setdefault("tp_post_only", False)
        # order_list = self.order_factory.bracket(**bracket_kwargs)

        self.submit_order_list(order_list)

    def _enter_short(self, last_bar: Bar) -> None:
        """Enter a short position with a bracket order.

        Mirrors ``_enter_long`` but flips the side and inverts SL/TP math.
        """
        if not self.instrument or self.instrument_id is None:
            self.log.error("No instrument loaded")
            return

        if not self._position_allowed():
            return

        trade_size = self.config.trade_size
        tick_size = float(self.instrument.price_increment)
        entry_price = float(last_bar.close)
        sl_price = entry_price + self.config.stop_loss_ticks * tick_size
        tp_price = entry_price - self.config.take_profit_ticks * tick_size

        order_list = make_bracket(
            self.order_factory,
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(trade_size),
            time_in_force=TimeInForce.GTC,
            sl_trigger_price=self.instrument.make_price(sl_price),
            tp_price=self.instrument.make_price(tp_price),
        )

        self.submit_order_list(order_list)

    # -------------------------------------------------------------------------
    # Risk helpers
    # -------------------------------------------------------------------------
    # ``self.portfolio`` tracks net positions.  Use it to enforce limits before
    # submitting new orders.  ``portfolio.net_position()`` returns signed size;
    # ``portfolio.is_flat()`` / ``is_net_long()`` / ``is_net_short()`` are
    # convenience booleans.
    # -------------------------------------------------------------------------

    def _position_allowed(self) -> bool:
        """Return ``True`` if a new position is allowed under risk limits."""
        if self.instrument_id is None:
            return False
        current_qty = float(abs(self.portfolio.net_position(self.instrument_id)))
        new_qty = float(self.config.trade_size)
        if (current_qty + new_qty) > self.config.max_position:
            return False
        return True

    def _max_daily_loss_exceeded(self) -> bool:
        """Return ``True`` if accumulated daily loss has reached the limit."""
        if self._daily_loss >= self.config.max_daily_loss:
            self.log.warning(
                f"max_daily_loss exceeded: {self._daily_loss:.2f} >= "
                f"{self.config.max_daily_loss:.2f}. Skipping entry."
            )
            return True
        return False

    # -------------------------------------------------------------------------
    # Order event handling
    # -------------------------------------------------------------------------
    # ``on_order_accepted`` and ``on_order_rejected`` implement the
    # rejection circuit breaker.  Copy these methods verbatim.
    # -------------------------------------------------------------------------

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """Reset the rejection circuit breaker on successful order acceptance."""
        if self._rejection_count > 0:
            self.log.info(
                f"Order accepted — resetting rejection counter "
                f"(was {self._rejection_count})."
            )
        self._rejection_count = 0

    def on_order_rejected(self, event: OrderRejected) -> None:
        """Track consecutive rejections and trip circuit breaker."""
        if self.config.max_consecutive_rejections <= 0:
            return

        self._rejection_count += 1
        reason = getattr(event, "reason", "unknown")
        self.log.warning(
            f"Order rejected ({self._rejection_count}/"
            f"{self.config.max_consecutive_rejections} consecutive): "
            f"client_order_id={event.client_order_id}, reason={reason}"
        )

        if self._rejection_count >= self.config.max_consecutive_rejections:
            self._rejection_disabled = True
            self.log.error(
                f"CIRCUIT BREAKER TRIPPED: {self.config.max_consecutive_rejections} "
                f"consecutive order rejections. Strategy DISABLED for "
                f"{self.config.instrument_id}. Manual restart required."
            )

    # -------------------------------------------------------------------------
    # Fill handling
    # -------------------------------------------------------------------------
    # ``on_order_filled`` is called for every ``OrderFilled`` event that
    # belongs to this strategy.  Use it to update position tracking, trigger
    # protective orders, or accumulate realised P&L.
    # -------------------------------------------------------------------------

    def on_order_filled(self, event: OrderFilled) -> None:
        """Update position tracking on fill."""
        fill_qty = float(event.last_qty.as_double())
        fill_px = float(event.last_px.as_double())
        commission = float(event.commission.as_double())

        side = 1 if event.order_side == OrderSide.BUY else -1
        signed_qty = fill_qty * side

        # Update average price with FIFO logic
        prev_qty = self._position_qty
        new_qty = prev_qty + signed_qty
        if new_qty == 0:
            self._position_avg_px = 0.0
        elif abs(new_qty) <= abs(prev_qty):
            # Reducing position — keep original avg price
            pass
        else:
            # Increasing position — recalc weighted average
            total_cost = prev_qty * self._position_avg_px + signed_qty * fill_px
            self._position_avg_px = total_cost / new_qty

        self._position_qty = new_qty

        # Accumulate realised loss (simplified — only when closing)
        if abs(new_qty) < abs(prev_qty) and prev_qty != 0:
            pnl = signed_qty * (self._position_avg_px - fill_px)
            if pnl < 0:
                self._daily_loss += abs(pnl) + commission

    # -------------------------------------------------------------------------
    # on_stop
    # -------------------------------------------------------------------------
    # Called once when the TradingNode stops (graceful shutdown, error, or
    # maintenance window).  Use it to:
    #   1. Cancel all pending orders.
    #   2. Close all open positions (optional — depends on strategy design).
    #   3. Unsubscribe from data to avoid leaks during restart.
    # -------------------------------------------------------------------------

    def on_stop(self) -> None:
        """Actions to be performed on strategy stop."""
        if self.instrument_id is not None and self.bar_type is not None:
            self.cancel_all_orders(self.instrument_id)
            self.close_all_positions(self.instrument_id)
            self.unsubscribe_bars(self.bar_type)

    # -------------------------------------------------------------------------
    # Optional life-cycle hooks
    # -------------------------------------------------------------------------
    # Implement only if your strategy needs state persistence or clean-up
    # beyond what ``on_start`` / ``on_stop`` provide.
    # -------------------------------------------------------------------------

    def on_reset(self) -> None:
        """Actions to be performed on strategy reset.

        Called when the strategy is reset (e.g. between backtest runs).
        Clear all mutable state so the next run starts fresh.
        """
        self._closes.clear()
        self._daily_loss = 0.0
        self._position_qty = 0.0
        self._position_avg_px = 0.0
        self._rejection_count = 0
        self._rejection_disabled = False

    def on_save(self) -> dict[str, bytes]:
        """Actions to be performed when the strategy is saved.

        Return a serialisable dict.  Nautilus persists this when
        ``TradingNode.save_state()`` is called (e.g. before a restart).
        """
        return {
            "state": pickle.dumps(
                {
                    "_closes": list(self._closes),
                    "_daily_loss": self._daily_loss,
                    "_position_qty": self._position_qty,
                    "_position_avg_px": self._position_avg_px,
                    "_rejection_count": self._rejection_count,
                    "_rejection_disabled": self._rejection_disabled,
                }
            )
        }

    def on_load(self, state: dict[str, bytes]) -> None:
        """Actions to be performed when the strategy is loaded.

        ``state`` is the dict previously returned by ``on_save()``.
        Restore mutable state so the strategy resumes exactly where it left off.
        """
        raw = state.get("state")
        if raw is None:
            return
        data = pickle.loads(raw)
        self._closes = deque(data.get("_closes", []))
        self._daily_loss = data.get("_daily_loss", 0.0)
        self._position_qty = data.get("_position_qty", 0.0)
        self._position_avg_px = data.get("_position_avg_px", 0.0)
        self._rejection_count = data.get("_rejection_count", 0)
        self._rejection_disabled = data.get("_rejection_disabled", False)
        if self._rejection_disabled:
            self.log.warning(
                f"Loaded state: rejection circuit breaker is ACTIVE "
                f"({self._rejection_count} consecutive rejections). "
                f"Bar processing is disabled until manual reset."
            )

    def on_dispose(self) -> None:
        """Actions to be performed when the strategy is disposed.

        Called once when the strategy is permanently destroyed.  Free any
        resources that are not automatically garbage-collected.
        """
