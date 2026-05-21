"""Tests for Futu execution client push handler wiring.

Verifies the end-to-end path:
  Futu push callback → Handler → asyncio.Queue → _run_push_loop
  → _handle_report → message bus
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from futu import RET_OK
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.execution.reports import FillReport, OrderStatusReport
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    InstrumentId,
    TradeId,
    VenueOrderId,
)
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
    return ctx


@pytest.fixture
def make_client(event_loop, mock_trade_ctx):
    """Factory to create a FutuLiveExecutionClient with mocked dependencies."""

    def _factory() -> FutuLiveExecutionClient:
        cfg = FutuExecClientConfig(
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
            client=mock_trade_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=cfg,
        )
        return client

    return _factory


# -----------------------------------------------------------------------------
# Order push
# -----------------------------------------------------------------------------


class TestOrderPush:
    """Tests for TradeOrderHandler → OrderStatusReport → message bus."""

    def test_order_push(self, event_loop, make_client, mock_trade_ctx):
        """A Futu order push is converted to OrderStatusReport and sent to bus."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        reports: list[OrderStatusReport] = []

        def _capture_send(report: OrderStatusReport) -> None:
            reports.append(report)
            if client._push_task is not None:
                client._push_task.cancel()

        client._send_order_status_report = _capture_send  # type: ignore[method-assign]

        # Locate the TradeOrderHandler registered on the mock context
        handler = None
        for call in mock_trade_ctx.set_handler.call_args_list:
            h = call.args[0]
            if type(h).__name__ == "TradeOrderHandler":
                handler = h
                break

        assert handler is not None, "TradeOrderHandler was not registered"

        order_df = pd.DataFrame(
            {
                "code": ["US.AAPL"],
                "trd_side": ["BUY"],
                "order_type": ["NORMAL"],
                "order_status": ["SUBMITTED"],
                "qty": [100],
                "price": [150.25],
                "order_id": ["12345"],
                "create_time": ["2024-01-15 09:30:00"],
                "updated_time": ["2024-01-15 09:30:01"],
                "dealt_qty": [0],
                "dealt_avg_price": [0],
                "time_in_force": ["DAY"],
            }
        )

        with patch(
            "sam_trader.adapters.futu.parsing.orders.TradeOrderHandlerBase.on_recv_rsp",
            return_value=(0, order_df),
        ):
            handler.on_recv_rsp(MagicMock())

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=0.5)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        event_loop.run_until_complete(client._disconnect())

        assert len(reports) == 1
        report = reports[0]
        assert isinstance(report, OrderStatusReport)
        assert report.venue_order_id.value == "12345"
        assert report.order_status == OrderStatus.ACCEPTED
        assert report.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert report.order_side == OrderSide.BUY
        assert report.order_type == OrderType.LIMIT
        assert report.time_in_force == TimeInForce.DAY
        assert str(report.quantity) == "100"


# -----------------------------------------------------------------------------
# Fill push
# -----------------------------------------------------------------------------


class TestFillPush:
    """Tests for TradeDealHandler → FillReport → message bus."""

    def test_fill_push(self, event_loop, make_client, mock_trade_ctx):
        """A Futu deal push is converted to FillReport and sent to bus."""
        client = make_client()
        event_loop.run_until_complete(client._connect())

        reports: list[FillReport] = []

        def _capture_send(report: FillReport) -> None:
            reports.append(report)
            if client._push_task is not None:
                client._push_task.cancel()

        client._send_fill_report = _capture_send  # type: ignore[method-assign]

        # Locate the TradeDealHandler registered on the mock context
        handler = None
        for call in mock_trade_ctx.set_handler.call_args_list:
            h = call.args[0]
            if type(h).__name__ == "TradeDealHandler":
                handler = h
                break

        assert handler is not None, "TradeDealHandler was not registered"

        deal_df = pd.DataFrame(
            {
                "code": ["US.AAPL"],
                "deal_id": ["FILL-001"],
                "order_id": ["12345"],
                "qty": [50],
                "price": [150.25],
                "trd_side": ["BUY"],
                "create_time": ["2024-01-15 09:30:00"],
            }
        )

        with patch(
            "sam_trader.adapters.futu.parsing.orders.TradeDealHandlerBase.on_recv_rsp",
            return_value=(0, deal_df),
        ):
            handler.on_recv_rsp(MagicMock())

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=0.5)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        event_loop.run_until_complete(client._disconnect())

        assert len(reports) == 1
        report = reports[0]
        assert isinstance(report, FillReport)
        assert report.trade_id == TradeId("FILL-001")
        assert report.venue_order_id == VenueOrderId("12345")
        assert report.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert report.order_side == OrderSide.BUY
        assert str(report.last_qty) == "50"
        assert str(report.last_px) == "150.25"
        assert report.liquidity_side == LiquiditySide.NO_LIQUIDITY_SIDE
