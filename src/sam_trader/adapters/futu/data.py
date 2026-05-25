"""Futu live market data client.

Adapts the push-loop pattern from nautilus-futu:
    callback → asyncio.Queue → _run_push_loop → _handle_data
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from futu import RET_OK, OpenQuoteContext, SubType
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.data.messages import (
    RequestBars,
    RequestData,
    SubscribeBars,
    SubscribeOrderBook,
    SubscribeQuoteTicks,
    SubscribeTradeTicks,
    UnsubscribeBars,
    UnsubscribeOrderBook,
    UnsubscribeQuoteTicks,
    UnsubscribeTradeTicks,
)
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.identifiers import ClientId, InstrumentId

from sam_trader.adapters.futu.common import instrument_id_to_futu_security
from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.adapters.futu.connection import get_cached_futu_quote_context
from sam_trader.adapters.futu.constants import FUTU_VENUE
from sam_trader.adapters.futu.parsing.market_data import (
    CurKlineHandler,
    OrderBookHandler,
    StockQuoteHandler,
    TickerHandler,
    parse_futu_bars,
)
from sam_trader.adapters.futu.subscription_manager import DataType as SubDataType
from sam_trader.adapters.futu.subscription_manager import (
    FutuSubscriptionManager,
)

logger = logging.getLogger(__name__)

# Map (step, BarAggregation) → Futu SubType string for K-line subscriptions.
_BAR_SPEC_TO_FUTU_SUBTYPE: dict[tuple[int, int], str] = {
    (1, BarAggregation.MINUTE): SubType.K_1M,
    (3, BarAggregation.MINUTE): SubType.K_3M,
    (5, BarAggregation.MINUTE): SubType.K_5M,
    (10, BarAggregation.MINUTE): SubType.K_10M,
    (15, BarAggregation.MINUTE): SubType.K_15M,
    (30, BarAggregation.MINUTE): SubType.K_30M,
    (1, BarAggregation.HOUR): SubType.K_60M,
    (2, BarAggregation.HOUR): SubType.K_120M,
    (4, BarAggregation.HOUR): SubType.K_240M,
    (1, BarAggregation.DAY): SubType.K_DAY,
    (1, BarAggregation.WEEK): SubType.K_WEEK,
    (1, BarAggregation.MONTH): SubType.K_MON,
    (1, BarAggregation.YEAR): SubType.K_YEAR,
}


def _bar_type_to_futu_subtype(bar_type: BarType) -> str | None:
    """Return the Futu SubType string for a Nautilus BarType, or None."""
    spec = bar_type.spec
    key = (spec.step, spec.aggregation)
    return _BAR_SPEC_TO_FUTU_SUBTYPE.get(key)


class FutuLiveDataClient(LiveMarketDataClient):
    """Live market data client for Futu OpenD.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    client : OpenQuoteContext or None
        Optional pre-created quote context. If None, one is fetched from the
        shared cache on connect.
    msgbus : MessageBus
        The Nautilus message bus.
    cache : Cache
        The Nautilus cache.
    clock : LiveClock
        The live clock.
    instrument_provider : InstrumentProvider
        The instrument provider.
    config : FutuDataClientConfig
        Configuration for this client.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: OpenQuoteContext | None,
        msgbus: MessageBus,
        cache: Any,
        clock: LiveClock,
        instrument_provider: InstrumentProvider,
        config: FutuDataClientConfig,
        subscription_manager: FutuSubscriptionManager | None = None,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(f"FUTU-{config.client_id}"),
            venue=FUTU_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )
        self._config = config
        self._quote_ctx = client
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._push_task: asyncio.Task | None = None
        self._subscription_manager = subscription_manager

        # Subscription tracking for reconnection restoration
        self._quote_tick_subs: set[InstrumentId] = set()
        self._trade_tick_subs: set[InstrumentId] = set()
        self._bar_subs: dict[BarType, InstrumentId] = {}
        self._order_book_subs: set[InstrumentId] = set()

        # Push handlers registered on the quote context
        self._handlers: list[Any] = []

    # -----------------------------------------------------------------------
    # Connection lifecycle
    # -----------------------------------------------------------------------

    async def _connect(self) -> None:
        if self._quote_ctx is None:
            self._quote_ctx = get_cached_futu_quote_context(
                self._config.host,
                self._config.port,
                self._config.trd_env,
            )

        # Pre-load instruments so strategies can find them on start
        if self._instrument_provider is not None:
            load_ids = getattr(self._config, "load_ids", None)
            if load_ids:
                self._log.info(
                    f"Loading {len(load_ids)} instrument(s): {load_ids}"
                )
                await self._instrument_provider.load_ids_async(
                    list(load_ids)
                )
                # Push instruments to the Nautilus cache via data pipeline
                for iid in load_ids:
                    instrument = self._instrument_provider.find(iid)
                    if instrument is not None:
                        self._handle_data(instrument)
                        self._log.info(f"Pushed instrument {iid} to cache")
                    else:
                        self._log.warning(
                            f"Instrument {iid} not found in provider after loading"
                        )

        self._setup_handlers()
        self._push_task = self._loop.create_task(
            self._run_push_loop(),
            name="futu_push_loop",
        )

        # Restore any subscriptions from a previous session
        await self._restore_subscriptions()

        # Backfill historical bars for restored bar subscriptions
        await self._backfill_bars()

    async def _disconnect(self) -> None:
        if self._push_task is not None and not self._push_task.done():
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass
            self._push_task = None

        if self._quote_ctx is not None:
            try:
                self._quote_ctx.unsubscribe_all()
            except Exception as e:
                self._log.exception(f"Error unsubscribing all on disconnect: {e}", e)
            self._clear_handlers()

    # -----------------------------------------------------------------------
    # Push loop
    # -----------------------------------------------------------------------

    async def _run_push_loop(self) -> None:
        """Poll the asyncio.Queue and dispatch items to the message bus."""
        try:
            while True:
                item = await self._queue.get()
                self._handle_data(item)
        except asyncio.CancelledError:
            self._log.debug("Push loop cancelled")
            raise

    # -----------------------------------------------------------------------
    # Handler registration
    # -----------------------------------------------------------------------

    def _setup_handlers(self) -> None:
        """Register push handlers on the quote context."""
        if self._quote_ctx is None:
            return

        handlers = [
            StockQuoteHandler(self._queue, self._loop),
            TickerHandler(self._queue, self._loop),
            OrderBookHandler(self._queue, self._loop),
        ]

        for handler in handlers:
            ret = self._quote_ctx.set_handler(handler)
            if ret != RET_OK:
                self._log.warning(
                    f"Failed to set handler {type(handler).__name__}",
                )
            self._handlers.append(handler)

    def _clear_handlers(self) -> None:
        """Clear the local handler list (Futu SDK keeps handlers on the ctx)."""
        self._handlers.clear()

    # -----------------------------------------------------------------------
    # Subscription restoration
    # -----------------------------------------------------------------------

    async def _restore_subscriptions(self) -> None:
        """Re-subscribe all previously tracked subscriptions after reconnect."""
        if self._quote_ctx is None:
            return

        for instrument_id in list(self._quote_tick_subs):
            code = instrument_id_to_futu_security(instrument_id)
            self._quote_ctx.subscribe([code], [SubType.QUOTE])

        for instrument_id in list(self._trade_tick_subs):
            code = instrument_id_to_futu_security(instrument_id)
            self._quote_ctx.subscribe([code], [SubType.TICKER])

        for bar_type, instrument_id in list(self._bar_subs.items()):
            code = instrument_id_to_futu_security(instrument_id)
            subtype = _bar_type_to_futu_subtype(bar_type)
            if subtype is not None:
                self._quote_ctx.subscribe([code], [subtype])
                self._add_kline_handler(bar_type)

        for instrument_id in list(self._order_book_subs):
            code = instrument_id_to_futu_security(instrument_id)
            self._quote_ctx.subscribe([code], [SubType.ORDER_BOOK])

    # -----------------------------------------------------------------------
    # Historical bar backfill
    # -----------------------------------------------------------------------

    async def _backfill_bars(self) -> None:
        """Request historical k-lines for each tracked bar subscription."""
        if self._quote_ctx is None:
            return

        for bar_type, instrument_id in list(self._bar_subs.items()):
            code = instrument_id_to_futu_security(instrument_id)
            subtype = _bar_type_to_futu_subtype(bar_type)
            if subtype is None:
                continue
            try:
                ret, data, _page_req_key = self._quote_ctx.request_history_kline(
                    code,
                    ktype=subtype,
                    max_count=100,
                )
                if ret == RET_OK and data is not None and not data.empty:
                    bars = parse_futu_bars(data.to_dict("records"), bar_type)
                    for bar in bars:
                        self._handle_data(bar)
            except Exception as e:
                self._log.exception(f"Backfill failed for {bar_type}: {e}", e)

    # -----------------------------------------------------------------------
    # Subscribe
    # -----------------------------------------------------------------------

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        instrument_id = command.instrument_id
        code = instrument_id_to_futu_security(instrument_id)
        if self._quote_ctx is None:
            self._log.error("Quote context not available for subscribe")
            return
        if self._subscription_manager is not None:
            ok = await self._subscription_manager.subscribe(
                instrument_id, SubDataType.QUOTE
            )
            if not ok:
                self._log.error(
                    f"Quote tick subscription rejected for {instrument_id}",
                )
                return
        ret, data = self._quote_ctx.subscribe([code], [SubType.QUOTE])
        if ret == RET_OK:
            self._quote_tick_subs.add(instrument_id)
            self._log.info(f"Subscribed quote ticks for {instrument_id}")
        else:
            if self._subscription_manager is not None:
                await self._subscription_manager.unsubscribe(
                    instrument_id, SubDataType.QUOTE
                )
            self._log.error(
                f"Failed to subscribe quote ticks for {instrument_id}: {data}"
            )

    async def _subscribe_trade_ticks(self, command: SubscribeTradeTicks) -> None:
        instrument_id = command.instrument_id
        code = instrument_id_to_futu_security(instrument_id)
        if self._quote_ctx is None:
            self._log.error("Quote context not available for subscribe")
            return
        if self._subscription_manager is not None:
            ok = await self._subscription_manager.subscribe(
                instrument_id, SubDataType.TRADE_TICK
            )
            if not ok:
                self._log.error(
                    f"Trade tick subscription rejected for {instrument_id}",
                )
                return
        ret, data = self._quote_ctx.subscribe([code], [SubType.TICKER])
        if ret == RET_OK:
            self._trade_tick_subs.add(instrument_id)
            self._log.info(f"Subscribed trade ticks for {instrument_id}")
        else:
            if self._subscription_manager is not None:
                await self._subscription_manager.unsubscribe(
                    instrument_id, SubDataType.TRADE_TICK
                )
            self._log.error(
                f"Failed to subscribe trade ticks for {instrument_id}: {data}"
            )

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        bar_type = command.bar_type
        instrument_id = bar_type.instrument_id
        code = instrument_id_to_futu_security(instrument_id)
        subtype = _bar_type_to_futu_subtype(bar_type)
        if subtype is None:
            self._log.error(f"Unsupported bar type for Futu: {bar_type}")
            return
        if self._quote_ctx is None:
            self._log.error("Quote context not available for subscribe")
            return
        if self._subscription_manager is not None:
            ok = await self._subscription_manager.subscribe(
                instrument_id, SubDataType.KLINE
            )
            if not ok:
                self._log.error(
                    f"Bar subscription rejected by quota manager for {bar_type}"
                )
                return
        ret, data = self._quote_ctx.subscribe([code], [subtype])
        if ret == RET_OK:
            self._bar_subs[bar_type] = instrument_id
            self._add_kline_handler(bar_type)
            self._log.info(f"Subscribed bars for {bar_type}")
        else:
            if self._subscription_manager is not None:
                await self._subscription_manager.unsubscribe(
                    instrument_id, SubDataType.KLINE
                )
            self._log.error(f"Failed to subscribe bars for {bar_type}: {data}")

    async def _subscribe_order_book_deltas(self, command: SubscribeOrderBook) -> None:
        instrument_id = command.instrument_id
        code = instrument_id_to_futu_security(instrument_id)
        if self._quote_ctx is None:
            self._log.error("Quote context not available for subscribe")
            return
        if self._subscription_manager is not None:
            ok = await self._subscription_manager.subscribe(
                instrument_id, SubDataType.ORDER_BOOK
            )
            if not ok:
                self._log.error(
                    f"Order book subscription rejected for {instrument_id}",
                )
                return
        ret, data = self._quote_ctx.subscribe([code], [SubType.ORDER_BOOK])
        if ret == RET_OK:
            self._order_book_subs.add(instrument_id)
            self._log.info(f"Subscribed order book for {instrument_id}")
        else:
            if self._subscription_manager is not None:
                await self._subscription_manager.unsubscribe(
                    instrument_id, SubDataType.ORDER_BOOK
                )
            self._log.error(
                f"Failed to subscribe order book for {instrument_id}: {data}"
            )

    # -----------------------------------------------------------------------
    # Unsubscribe
    # -----------------------------------------------------------------------

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        instrument_id = command.instrument_id
        self._quote_tick_subs.discard(instrument_id)
        if self._subscription_manager is not None:
            await self._subscription_manager.unsubscribe(
                instrument_id, SubDataType.QUOTE
            )
        if self._quote_ctx is None:
            return
        code = instrument_id_to_futu_security(instrument_id)
        self._quote_ctx.unsubscribe([code], [SubType.QUOTE])

    async def _unsubscribe_trade_ticks(self, command: UnsubscribeTradeTicks) -> None:
        instrument_id = command.instrument_id
        self._trade_tick_subs.discard(instrument_id)
        if self._subscription_manager is not None:
            await self._subscription_manager.unsubscribe(
                instrument_id, SubDataType.TRADE_TICK
            )
        if self._quote_ctx is None:
            return
        code = instrument_id_to_futu_security(instrument_id)
        self._quote_ctx.unsubscribe([code], [SubType.TICKER])

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        bar_type = command.bar_type
        self._bar_subs.pop(bar_type, None)
        instrument_id = bar_type.instrument_id
        if self._subscription_manager is not None:
            await self._subscription_manager.unsubscribe(
                instrument_id, SubDataType.KLINE
            )
        if self._quote_ctx is None:
            return
        code = instrument_id_to_futu_security(instrument_id)
        subtype = _bar_type_to_futu_subtype(bar_type)
        if subtype is None:
            return
        self._quote_ctx.unsubscribe([code], [subtype])

    async def _unsubscribe_order_book_deltas(
        self, command: UnsubscribeOrderBook
    ) -> None:
        instrument_id = command.instrument_id
        self._order_book_subs.discard(instrument_id)
        if self._subscription_manager is not None:
            await self._subscription_manager.unsubscribe(
                instrument_id, SubDataType.ORDER_BOOK
            )
        if self._quote_ctx is None:
            return
        code = instrument_id_to_futu_security(instrument_id)
        self._quote_ctx.unsubscribe([code], [SubType.ORDER_BOOK])

    # -----------------------------------------------------------------------
    # Requests (not yet implemented)
    # -----------------------------------------------------------------------

    async def _request(self, request: RequestData) -> None:
        self._log.warning(f"Request not implemented: {request.data_type}")

    async def _request_bars(self, request: RequestBars) -> None:
        if self._quote_ctx is None:
            self._log.warning("Quote context not available for request bars")
            return

        bar_type = request.bar_type
        instrument_id = bar_type.instrument_id
        code = instrument_id_to_futu_security(instrument_id)
        subtype = _bar_type_to_futu_subtype(bar_type)
        if subtype is None:
            self._log.error(f"Unsupported bar type for Futu: {bar_type}")
            return

        try:
            ret, data, _page_req_key = self._quote_ctx.request_history_kline(
                code,
                ktype=subtype,
                max_count=request.limit or 1000,
            )
            if ret == RET_OK and data is not None and not data.empty:
                bars = parse_futu_bars(data.to_dict("records"), bar_type)
                for bar in bars:
                    self._handle_data(bar)
        except Exception as e:
            self._log.exception(f"Request bars failed for {bar_type}: {e}", e)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _add_kline_handler(self, bar_type: BarType) -> None:
        """Register a CurKlineHandler for the given BarType."""
        handler = CurKlineHandler(self._queue, bar_type, self._loop)
        if self._quote_ctx is not None:
            ret = self._quote_ctx.set_handler(handler)
            if ret != RET_OK:
                self._log.warning(
                    f"Failed to set CurKlineHandler for {bar_type}",
                )
        self._handlers.append(handler)
