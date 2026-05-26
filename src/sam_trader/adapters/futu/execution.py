"""Futu live execution client.

Submits/modifies/cancels orders via Futu OpenD trade context.
Handles order/fill push data via asyncio.Queue → _run_push_loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from futu import (
    RET_OK,
    ContextStatus,
    ModifyOrderOp,
    OpenSecTradeContext,
    TrdEnv,
)
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import (
    CancelOrder,
    GenerateFillReports,
    GenerateOrderStatusReports,
    GeneratePositionStatusReports,
    ModifyOrder,
    SubmitOrder,
    SubmitOrderList,
)
from nautilus_trader.execution.reports import (
    FillReport,
    OrderStatusReport,
    PositionStatusReport,
)
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import (
    AccountType,
    OmsType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    InstrumentId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Price, Quantity
from nautilus_trader.model.orders import (
    LimitOrder,
    MarketOrder,
    Order,
)

from sam_trader.adapters.futu.common import instrument_id_to_futu_security
from sam_trader.adapters.futu.config import FutuExecClientConfig
from sam_trader.adapters.futu.connection import (
    get_cached_futu_trade_context,
    unlock_futu_trade,
)
from sam_trader.adapters.futu.constants import (
    FUTU_TRD_MARKET_TO_VENUE,
    FUTU_VENUE,
    nautilus_order_side_to_futu,
    nautilus_order_type_to_futu,
)
from sam_trader.adapters.futu.parsing.orders import (
    TradeDealHandler,
    TradeOrderHandler,
    parse_futu_fill_to_report,
    parse_futu_order_to_report,
    parse_futu_position_to_report,
)

logger = logging.getLogger(__name__)


class FutuLiveExecutionClient(LiveExecutionClient):
    """Live execution client for Futu OpenD.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    client : OpenSecTradeContext or None
        Optional pre-created trade context. If None, one is fetched from the
        shared cache on connect.
    msgbus : MessageBus
        The Nautilus message bus.
    cache : Cache
        The Nautilus cache.
    clock : LiveClock
        The live clock.
    instrument_provider : InstrumentProvider
        The instrument provider.
    config : FutuExecClientConfig
        Configuration for this client.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: OpenSecTradeContext | None,
        msgbus: MessageBus,
        cache: Any,
        clock: LiveClock,
        instrument_provider: InstrumentProvider,
        config: FutuExecClientConfig,
        account_id: AccountId | None = None,
        venue: Venue | None = None,
    ) -> None:
        account_id = account_id or AccountId(f"FUTU-{config.client_id}")
        client_id = ClientId(account_id.get_issuer())
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=venue or FUTU_VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=Currency.from_str("USD"),
            instrument_provider=instrument_provider,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._set_account_id(account_id)
        self._config = config
        self._trade_ctx = client
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._push_task: asyncio.Task | None = None

        # Venue → AccountId mapping for multi-market accounts
        self._venue_account_aliases: dict[Venue, AccountId] = {}

        # Default account ID (updated after account discovery)
        self._account_id: AccountId = account_id

    # -----------------------------------------------------------------------
    # Connection lifecycle
    # -----------------------------------------------------------------------

    async def _connect(self) -> None:
        if self._trade_ctx is None or self._trade_ctx.status != ContextStatus.READY:
            if self._trade_ctx is not None:
                try:
                    self._trade_ctx.close()
                except Exception:
                    pass
            self._trade_ctx = get_cached_futu_trade_context(
                self._config.host,
                self._config.port,
                self._config.trd_env,
                self._config.trd_market,
            )

        # Unlock trade (required for real trading; no-op for simulate)
        if self._config.trd_env == "REAL" and self._config.unlock_pwd_md5:
            unlock_futu_trade(self._trade_ctx, self._config.unlock_pwd_md5)
        elif self._config.trd_env == "REAL":
            self._log.info(
                "Real trading environment: unlock_pwd_md5 not configured; "
                "ensure trade is unlocked externally"
            )

        # Account auto-discovery
        await self._discover_accounts()

        # Position reconciliation
        await self._reconcile_positions()

        # Set up push handlers
        self._setup_handlers()

        # Start push loop
        self._push_task = self._loop.create_task(
            self._run_push_loop(),
            name="futu_exec_push_loop",
        )

        self._log.info(
            f"Futu execution client connected: {self._config.client_key}",
        )

    async def _disconnect(self) -> None:
        if self._push_task is not None and not self._push_task.done():
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass
            self._push_task = None

        self._trade_ctx = None
        self._log.info("Futu execution client disconnected")

    # -----------------------------------------------------------------------
    # Account discovery & venue aliases
    # -----------------------------------------------------------------------

    async def _discover_accounts(self) -> None:
        """Discover accounts via ``get_acc_list`` and register venue aliases."""
        if self._trade_ctx is None:
            return

        try:
            ret, data = self._trade_ctx.get_acc_list()
            if ret != RET_OK or data is None or data.empty:
                self._log.warning("Account discovery returned no accounts")
                return

            accounts: list[dict[str, Any]] = data.to_dict("records")
            self._register_venue_account_aliases(accounts)
        except Exception as e:
            self._log.exception(f"Account discovery failed: {e}", e)

    def _register_venue_account_aliases(self, accounts: list[dict[str, Any]]) -> None:
        """Map Futu market codes to Nautilus venues and account IDs.

        Parameters
        ----------
        accounts : list[dict[str, Any]]
            List of account dictionaries from ``get_acc_list``.

        """
        for acc in accounts:
            acc_id_val = acc.get("acc_id")
            market = acc.get("trdMarket")
            if acc_id_val is None or market is None:
                continue

            acc_id = AccountId(f"FUTU-{acc_id_val}")
            venue = FUTU_TRD_MARKET_TO_VENUE.get(market)
            if venue is not None:
                self._venue_account_aliases[venue] = acc_id
                self._log.info(
                    f"Registered venue alias: {venue} -> {acc_id}",
                )

            # Use the first discovered account as the default
            if self._account_id == AccountId("FUTU-001"):
                self._account_id = acc_id

    def _resolve_account_id(self, instrument_id: InstrumentId) -> AccountId:
        """Resolve the account ID for an instrument based on venue aliases."""
        venue = instrument_id.venue
        return self._venue_account_aliases.get(venue, self._account_id)

    # -----------------------------------------------------------------------
    # Position reconciliation
    # -----------------------------------------------------------------------

    async def _reconcile_positions(self) -> None:
        """Fetch current positions and emit PositionStatusReports."""
        if self._trade_ctx is None:
            return

        try:
            trd_env = (
                TrdEnv.SIMULATE if self._config.trd_env == "SIMULATE" else TrdEnv.REAL
            )
            ret, data = self._trade_ctx.position_list_query(
                trd_env=trd_env,
            )
            if ret != RET_OK or data is None or data.empty:
                return

            positions: list[dict[str, Any]] = data.to_dict("records")
            for pos in positions:
                try:
                    report = parse_futu_position_to_report(
                        pos,
                        self._account_id,
                    )
                    self._send_position_status_report(report)
                except Exception as e:
                    self._log.exception(f"Failed to parse position: {pos}", e)
        except Exception as e:
            self._log.exception(f"Position reconciliation failed: {e}", e)

    # -----------------------------------------------------------------------
    # Push handlers & loop
    # -----------------------------------------------------------------------

    def _setup_handlers(self) -> None:
        """Register push handlers on the trade context."""
        if self._trade_ctx is None:
            return

        handlers = [
            TradeOrderHandler(self._queue, self._account_id, self._loop),
            TradeDealHandler(self._queue, self._account_id, self._loop),
        ]

        for handler in handlers:
            ret = self._trade_ctx.set_handler(handler)
            if ret != RET_OK:
                self._log.warning(
                    f"Failed to set handler {type(handler).__name__}",
                )

    async def _run_push_loop(self) -> None:
        """Poll the asyncio.Queue and dispatch reports to the message bus."""
        try:
            while True:
                item = await self._queue.get()
                self._handle_report(item)
        except asyncio.CancelledError:
            self._log.debug("Exec push loop cancelled")
            raise

    def _handle_report(self, item: Any) -> None:
        """Dispatch a report from the queue to the message bus."""
        if isinstance(item, OrderStatusReport):
            self._send_order_status_report(item)
        elif isinstance(item, FillReport):
            self._send_fill_report(item)
        elif isinstance(item, PositionStatusReport):
            self._send_position_status_report(item)
        else:
            self._log.warning(f"Unknown report type in push loop: {type(item)}")

    # -----------------------------------------------------------------------
    # Order submission
    # -----------------------------------------------------------------------

    async def _submit_order(self, command: SubmitOrder) -> None:
        order = command.order
        instrument_id = order.instrument_id
        account_id = self._resolve_account_id(instrument_id)

        code = instrument_id_to_futu_security(instrument_id)
        price = self._order_price_to_futu(order)
        qty = str(int(order.quantity))
        trd_side = nautilus_order_side_to_futu(order.side)
        order_type = nautilus_order_type_to_futu(order.order_type)
        tif = self._tif_to_futu(order.time_in_force)
        trd_env = self._trd_env_to_futu()

        if self._trade_ctx is None:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=instrument_id,
                client_order_id=order.client_order_id,
                reason="Trade context not available",
                ts_event=self._clock.timestamp_ns(),
            )
            return

        try:
            ret, data = self._trade_ctx.place_order(
                price=price,
                qty=qty,
                code=code,
                trd_side=trd_side,
                order_type=order_type,
                time_in_force=tif,
                trd_env=trd_env,
                acc_id=int(account_id.get_id()) if account_id.get_id() else 0,
            )
        except Exception as e:
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=instrument_id,
                client_order_id=order.client_order_id,
                reason=str(e),
                ts_event=self._clock.timestamp_ns(),
            )
            return

        if ret != RET_OK:
            reason = str(data) if data is not None else "place_order failed"
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=instrument_id,
                client_order_id=order.client_order_id,
                reason=reason,
                ts_event=self._clock.timestamp_ns(),
            )
            return

        # Extract venue order ID from response
        venue_order_id: VenueOrderId | None = None
        if data is not None and not data.empty:
            order_id_val = data.iloc[0].get("order_id")
            if order_id_val is not None:
                venue_order_id = VenueOrderId(str(order_id_val))

        # Emit submitted event
        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

        # Emit accepted event immediately (Futu returns order ID on placement)
        if venue_order_id is not None:
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                ts_event=self._clock.timestamp_ns(),
            )

    async def _submit_order_list(self, command: SubmitOrderList) -> None:
        """Submit each order in the list sequentially (used for bracket orders)."""
        for submit_order in command.order_list.orders:
            await self._submit_order(
                SubmitOrder(
                    trader_id=command.trader_id,
                    strategy_id=command.strategy_id,
                    client_id=command.client_id,
                    order=submit_order,
                    command_id=UUID4(),
                    ts_init=self._clock.timestamp_ns(),
                )
            )

    # -----------------------------------------------------------------------
    # Order modification
    # -----------------------------------------------------------------------

    async def _modify_order(self, command: ModifyOrder) -> None:
        instrument_id = command.instrument_id
        account_id = self._resolve_account_id(instrument_id)
        venue_order_id = command.venue_order_id

        price = str(command.price) if command.price is not None else "0"
        qty = str(int(command.quantity)) if command.quantity is not None else "0"
        trd_env = self._trd_env_to_futu()

        if self._trade_ctx is None:
            self.generate_order_modify_rejected(
                strategy_id=command.strategy_id,
                instrument_id=instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason="Trade context not available",
                ts_event=self._clock.timestamp_ns(),
            )
            return

        try:
            ret, data = self._trade_ctx.modify_order(
                modify_order_op=ModifyOrderOp.NORMAL,
                order_id=str(venue_order_id),
                qty=qty,
                price=price,
                trd_env=trd_env,
                acc_id=int(account_id.get_id()) if account_id.get_id() else 0,
            )
        except Exception as e:
            self.generate_order_modify_rejected(
                strategy_id=command.strategy_id,
                instrument_id=instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason=str(e),
                ts_event=self._clock.timestamp_ns(),
            )
            return

        if ret != RET_OK:
            reason = str(data) if data is not None else "modify_order failed"
            self.generate_order_modify_rejected(
                strategy_id=command.strategy_id,
                instrument_id=instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason=reason,
                ts_event=self._clock.timestamp_ns(),
            )
            return

        # Emit updated event
        self.generate_order_updated(
            strategy_id=command.strategy_id,
            instrument_id=instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=venue_order_id,
            quantity=command.quantity or Quantity.from_int(0),
            price=command.price or Price.from_str("0"),
            trigger_price=None,
            ts_event=self._clock.timestamp_ns(),
        )

    # -----------------------------------------------------------------------
    # Order cancellation
    # -----------------------------------------------------------------------

    async def _cancel_order(self, command: CancelOrder) -> None:
        instrument_id = command.instrument_id
        account_id = self._resolve_account_id(instrument_id)
        venue_order_id = command.venue_order_id
        trd_env = self._trd_env_to_futu()

        if self._trade_ctx is None:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason="Trade context not available",
                ts_event=self._clock.timestamp_ns(),
            )
            return

        try:
            ret, data = self._trade_ctx.modify_order(
                modify_order_op=ModifyOrderOp.CANCEL,
                order_id=str(venue_order_id),
                qty="0",
                price="0",
                trd_env=trd_env,
                acc_id=int(account_id.get_id()) if account_id.get_id() else 0,
            )
        except Exception as e:
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason=str(e),
                ts_event=self._clock.timestamp_ns(),
            )
            return

        if ret != RET_OK:
            reason = str(data) if data is not None else "cancel_order failed"
            self.generate_order_cancel_rejected(
                strategy_id=command.strategy_id,
                instrument_id=instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=venue_order_id,
                reason=reason,
                ts_event=self._clock.timestamp_ns(),
            )
            return

        # Emit canceled event (immediate confirmation from Futu)
        self.generate_order_canceled(
            strategy_id=command.strategy_id,
            instrument_id=instrument_id,
            client_order_id=command.client_order_id,
            venue_order_id=venue_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

    # -----------------------------------------------------------------------
    # Reconciliation reports (required by LiveExecutionClient)
    # -----------------------------------------------------------------------

    async def generate_order_status_reports(
        self,
        command: GenerateOrderStatusReports,
    ) -> list[OrderStatusReport]:
        """Query Futu for order status reports.

        Uses ``history_order_list_query`` to retrieve recent orders and
        maps each row to a :class:`OrderStatusReport`.

        Parameters
        ----------
        command : GenerateOrderStatusReports
            The reconciliation command (filters are best-effort).

        Returns
        -------
        list[OrderStatusReport]

        """
        if self._trade_ctx is None:
            return []

        reports: list[OrderStatusReport] = []
        trd_env = TrdEnv.SIMULATE if self._config.trd_env == "SIMULATE" else TrdEnv.REAL
        acc_id = int(self._account_id.get_id()) if self._account_id.get_id() else 0

        code = ""
        if command.instrument_id is not None:
            try:
                code = instrument_id_to_futu_security(command.instrument_id)
            except ValueError:
                pass

        try:
            ret, data = self._trade_ctx.history_order_list_query(
                code=code,
                trd_env=trd_env,
                acc_id=acc_id,
            )
            if ret != RET_OK or data is None or data.empty:
                return reports

            for _, row in data.iterrows():
                try:
                    order_dict = row.to_dict()
                    report = parse_futu_order_to_report(order_dict, self._account_id)
                    reports.append(report)
                except Exception:
                    continue
        except Exception as e:
            self._log.exception(f"Failed to generate order status reports: {e}", e)

        return reports

    async def generate_fill_reports(
        self,
        command: GenerateFillReports,
    ) -> list[FillReport]:
        """Query Futu for fill/deal reports.

        Uses ``history_deal_list_query`` to retrieve recent fills and
        maps each row to a :class:`FillReport`.

        Parameters
        ----------
        command : GenerateFillReports
            The reconciliation command.

        Returns
        -------
        list[FillReport]

        """
        if self._trade_ctx is None:
            return []

        reports: list[FillReport] = []
        trd_env = TrdEnv.SIMULATE if self._config.trd_env == "SIMULATE" else TrdEnv.REAL
        acc_id = int(self._account_id.get_id()) if self._account_id.get_id() else 0

        code = ""
        if command.instrument_id is not None:
            try:
                code = instrument_id_to_futu_security(command.instrument_id)
            except ValueError:
                pass

        try:
            ret, data = self._trade_ctx.history_deal_list_query(
                code=code,
                trd_env=trd_env,
                acc_id=acc_id,
            )
            if ret != RET_OK or data is None or data.empty:
                return reports

            for _, row in data.iterrows():
                try:
                    deal_dict = row.to_dict()
                    report = parse_futu_fill_to_report(deal_dict, self._account_id)
                    reports.append(report)
                except Exception:
                    continue
        except Exception as e:
            self._log.exception(f"Failed to generate fill reports: {e}", e)

        return reports

    async def generate_position_status_reports(
        self,
        command: GeneratePositionStatusReports,
    ) -> list[PositionStatusReport]:
        """Query Futu for position status reports.

        Uses ``position_list_query`` to retrieve current positions and
        maps each row to a :class:`PositionStatusReport`.

        Parameters
        ----------
        command : GeneratePositionStatusReports
            The reconciliation command.

        Returns
        -------
        list[PositionStatusReport]

        """
        if self._trade_ctx is None:
            return []

        reports: list[PositionStatusReport] = []
        trd_env = TrdEnv.SIMULATE if self._config.trd_env == "SIMULATE" else TrdEnv.REAL
        acc_id = int(self._account_id.get_id()) if self._account_id.get_id() else 0

        code = ""
        if command.instrument_id is not None:
            try:
                code = instrument_id_to_futu_security(command.instrument_id)
            except ValueError:
                pass

        try:
            ret, data = self._trade_ctx.position_list_query(
                code=code,
                trd_env=trd_env,
                acc_id=acc_id,
            )
            if ret != RET_OK or data is None or data.empty:
                return reports

            for _, row in data.iterrows():
                try:
                    pos_dict = row.to_dict()
                    report = parse_futu_position_to_report(pos_dict, self._account_id)
                    reports.append(report)
                except Exception:
                    continue
        except Exception as e:
            self._log.exception(f"Failed to generate position status reports: {e}", e)

        return reports

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _order_price_to_futu(order: Order) -> float:
        """Extract price from a Nautilus order for Futu API."""
        if isinstance(order, MarketOrder):
            return 0.0
        if isinstance(order, LimitOrder):
            return float(order.price)
        # Default: try to read price attribute
        try:
            return float(order.price)  # type: ignore[union-attr]
        except Exception:
            return 0.0

    @staticmethod
    def _tif_to_futu(tif: TimeInForce) -> str:
        """Map Nautilus TimeInForce to Futu time_in_force string."""
        if tif == TimeInForce.GTC:
            return "GTC"
        elif tif == TimeInForce.IOC:
            return "IOC"
        else:
            return "DAY"

    def _trd_env_to_futu(self) -> str:
        """Return the Futu trading environment string."""
        return "SIMULATE" if self._config.trd_env == "SIMULATE" else "REAL"
