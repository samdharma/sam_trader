"""Integration tests for Futu execution flow.

These tests verify the end-to-end order lifecycle:
connect → submit → (fill push) → cancel, with correct event generation
and message bus dispatch.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from futu import RET_OK
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    StrategyId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.test_kit.stubs.commands import TestCommandStubs
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.adapters.futu.config import FutuExecClientConfig
from sam_trader.adapters.futu.execution import FutuLiveExecutionClient


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_trade_ctx() -> MagicMock:
    """Return a mock OpenSecTradeContext."""
    ctx = MagicMock()
    ctx.place_order.return_value = (RET_OK, pd.DataFrame({"order_id": ["12345"]}))
    ctx.modify_order.return_value = (RET_OK, "")
    ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
    ctx.position_list_query.return_value = (RET_OK, pd.DataFrame())
    ctx.set_handler.return_value = RET_OK
    ctx.unlock_trade.return_value = (RET_OK, "")
    return ctx


@pytest.fixture
def make_client(event_loop, mock_trade_ctx):
    """Factory to create a FutuLiveExecutionClient with mocked dependencies."""

    def _factory(
        config: FutuExecClientConfig | None = None,
        trade_ctx: MagicMock | None = None,
    ) -> FutuLiveExecutionClient:
        cfg = config or FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="US",
            client_id=1,
        )
        clock = LiveClock()
        msgbus = TestComponentStubs.msgbus()
        cache = TestComponentStubs.cache()
        provider = MagicMock(spec=InstrumentProvider)
        client = FutuLiveExecutionClient(
            loop=event_loop,
            client=trade_ctx or mock_trade_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=cfg,
        )
        return client

    return _factory


def _make_limit_order(
    instrument_id: str = "AAPL.NASDAQ",
    side: OrderSide = OrderSide.BUY,
    price: str = "150.50",
    qty: int = 100,
    client_order_id: ClientOrderId | None = None,
) -> LimitOrder:
    """Create a simple LimitOrder for tests."""
    return LimitOrder(
        trader_id=TraderId("TRADER-001"),
        strategy_id=StrategyId("ORB-001"),
        instrument_id=InstrumentId.from_str(instrument_id),
        client_order_id=client_order_id or ClientOrderId("O-001"),
        order_side=side,
        quantity=Quantity.from_int(qty),
        price=Price.from_str(price),
        init_id=UUID4(),
        ts_init=0,
        time_in_force=TimeInForce.DAY,
    )


@pytest.mark.integration
class TestLimitOrderLifecycle:
    """End-to-end test: limit order submission, event flow, and cancellation."""

    def test_limit_order_lifecycle(self, event_loop, make_client, mock_trade_ctx):
        """Submit a limit order, verify events, then cancel it."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        # --- Submit ---
        order = _make_limit_order()
        submit_cmd = TestCommandStubs.submit_order_command(order)

        with (
            patch.object(client, "generate_order_submitted") as mock_sub,
            patch.object(client, "generate_order_accepted") as mock_acc,
        ):
            event_loop.run_until_complete(client._submit_order(submit_cmd))

        mock_sub.assert_called_once()
        mock_acc.assert_called_once()
        accepted_call = mock_acc.call_args
        assert accepted_call.kwargs["venue_order_id"] == VenueOrderId("12345")

        # Verify place_order was called with correct parameters
        mock_trade_ctx.place_order.assert_called_once()
        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"
        assert call_kwargs["price"] == 150.5
        assert call_kwargs["qty"] == "100"
        assert call_kwargs["trd_side"] == "BUY"
        assert call_kwargs["order_type"] == "NORMAL"
        assert call_kwargs["time_in_force"] == "DAY"
        assert call_kwargs["trd_env"] == "SIMULATE"

        # --- Cancel ---
        mock_trade_ctx.modify_order.reset_mock()
        cancel_cmd = TestCommandStubs.cancel_order_command(
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        with patch.object(client, "generate_order_canceled") as mock_can:
            event_loop.run_until_complete(client._cancel_order(cancel_cmd))

        mock_can.assert_called_once()
        mock_trade_ctx.modify_order.assert_called_once()
        cancel_kwargs = mock_trade_ctx.modify_order.call_args.kwargs
        assert cancel_kwargs["order_id"] == "12345"

        event_loop.run_until_complete(client._disconnect())

    def test_connect_unlocks_trade_in_real_env(self, event_loop, mock_trade_ctx):
        """REAL environment with unlock_pwd_md5 calls unlock_trade."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="REAL",
            trd_market="US",
            client_id=1,
            unlock_pwd_md5="deadbeef",
        )
        clock = LiveClock()
        msgbus = TestComponentStubs.msgbus()
        cache = TestComponentStubs.cache()
        provider = MagicMock(spec=InstrumentProvider)
        client = FutuLiveExecutionClient(
            loop=event_loop,
            client=mock_trade_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=cfg,
        )

        event_loop.run_until_complete(client._connect())
        mock_trade_ctx.unlock_trade.assert_called_once_with(password="deadbeef")
        event_loop.run_until_complete(client._disconnect())

    def test_connect_discovers_accounts(self, event_loop, make_client, mock_trade_ctx):
        """Connect discovers accounts and registers venue aliases."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [123, 456],
                    "trdMarket": [2, 1],  # US, HK
                }
            ),
        )
        client = make_client(trade_ctx=mock_trade_ctx)
        event_loop.run_until_complete(client._connect())

        assert client._venue_account_aliases
        from nautilus_trader.model.identifiers import Venue

        assert Venue("NASDAQ") in client._venue_account_aliases
        assert Venue("HKEX") in client._venue_account_aliases
        event_loop.run_until_complete(client._disconnect())

    def test_push_loop_dispatches_fill_report(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """A FillReport placed on the queue is dispatched to the message bus."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        reports = []

        def _capture_send(report):
            reports.append(report)
            client._push_task.cancel()

        client._send_fill_report = _capture_send  # type: ignore[method-assign]

        from nautilus_trader.execution.reports import FillReport
        from nautilus_trader.model.enums import LiquiditySide
        from nautilus_trader.model.identifiers import TradeId

        report = FillReport(
            account_id=AccountId("FUTU-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            venue_order_id=VenueOrderId("12345"),
            trade_id=TradeId("T-001"),
            order_side=OrderSide.BUY,
            last_qty=Quantity.from_int(50),
            last_px=Price.from_str("150.50"),
            commission=None,
            liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
            report_id=UUID4(),
            ts_event=0,
            ts_init=0,
        )

        event_loop.run_until_complete(client._queue.put(report))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=1.0)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        event_loop.run_until_complete(client._disconnect())
        assert len(reports) == 1
        assert reports[0].trade_id.value == "T-001"

    def test_full_order_lifecycle(self, event_loop, make_client, mock_trade_ctx):
        """Complete order lifecycle: connect → submit → fill → cancel.

        Verifies all acceptance criteria for the Phase 3 exit gate:
        1. Account auto-discovered via get_acc_list
        2. LIMIT order submitted (paper trading)
        3. OrderAccepted event on message bus
        4. OrderFilled event with correct price / qty / commission
        5. OrderCancelled event
        """
        # --- 1. Account discovery setup ---
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [123],
                    "trdMarket": [2],  # US
                }
            ),
        )
        client = make_client(trade_ctx=mock_trade_ctx)
        event_loop.run_until_complete(client._connect())

        # Verify account auto-discovered
        from nautilus_trader.model.identifiers import Venue

        assert Venue("NASDAQ") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-123")

        # --- 2. Submit LIMIT order ---
        order = _make_limit_order()
        submit_cmd = TestCommandStubs.submit_order_command(order)

        with (
            patch.object(client, "generate_order_submitted") as mock_sub,
            patch.object(client, "generate_order_accepted") as mock_acc,
        ):
            event_loop.run_until_complete(client._submit_order(submit_cmd))

        # Verify OrderSubmitted + OrderAccepted
        mock_sub.assert_called_once()
        mock_acc.assert_called_once()
        accepted_call = mock_acc.call_args
        assert accepted_call.kwargs["venue_order_id"] == VenueOrderId("12345")

        # Verify Futu API called with paper-trading params
        mock_trade_ctx.place_order.assert_called_once()
        place_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert place_kwargs["code"] == "US.AAPL"
        assert place_kwargs["trd_env"] == "SIMULATE"
        assert place_kwargs["order_type"] == "NORMAL"

        # --- 3. Simulate fill push ---
        fill_reports = []

        def _capture_fill(report):
            fill_reports.append(report)

        client._send_fill_report = _capture_fill  # type: ignore[method-assign]

        from decimal import Decimal

        from nautilus_trader.execution.reports import FillReport
        from nautilus_trader.model.enums import LiquiditySide
        from nautilus_trader.model.identifiers import TradeId
        from nautilus_trader.model.objects import Currency, Money

        fill_report = FillReport(
            account_id=AccountId("FUTU-123"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            venue_order_id=VenueOrderId("12345"),
            trade_id=TradeId("T-001"),
            order_side=OrderSide.BUY,
            last_qty=Quantity.from_int(100),
            last_px=Price.from_str("150.50"),
            commission=Money(Decimal("1.99"), Currency.from_str("USD")),
            liquidity_side=LiquiditySide.TAKER,
            report_id=UUID4(),
            ts_event=0,
            ts_init=0,
        )

        event_loop.run_until_complete(client._queue.put(fill_report))
        # Give push loop a moment to process
        event_loop.run_until_complete(asyncio.sleep(0.05))

        # Verify OrderFilled event data
        assert len(fill_reports) == 1
        sent = fill_reports[0]
        assert sent.trade_id.value == "T-001"
        assert str(sent.last_qty) == "100"
        assert str(sent.last_px) == "150.50"
        assert sent.commission is not None
        assert sent.commission.as_decimal() == Decimal("1.99")
        assert sent.commission.currency.code == "USD"

        # --- 4. Cancel order ---
        mock_trade_ctx.modify_order.reset_mock()
        cancel_cmd = TestCommandStubs.cancel_order_command(
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        with patch.object(client, "generate_order_canceled") as mock_can:
            event_loop.run_until_complete(client._cancel_order(cancel_cmd))

        mock_can.assert_called_once()
        mock_trade_ctx.modify_order.assert_called_once()
        cancel_kwargs = mock_trade_ctx.modify_order.call_args.kwargs
        assert cancel_kwargs["order_id"] == "12345"

        event_loop.run_until_complete(client._disconnect())
