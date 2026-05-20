"""Tests for Futu order parsing."""

import asyncio
from typing import Any

import pytest
from nautilus_trader.execution.reports import (
    FillReport,
    OrderStatusReport,
    PositionStatusReport,
)
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    InstrumentId,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Money

from sam_trader.adapters.futu.parsing.orders import (
    TradeDealHandler,
    TradeOrderHandler,
    parse_futu_fill_to_report,
    parse_futu_order_to_report,
    parse_futu_position_to_report,
)

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------


@pytest.fixture
def account_id() -> AccountId:
    return AccountId("SAM-001")


@pytest.fixture
def ts_ns() -> int:
    return 1_234_567_890_000_000_000


# ------------------------------------------------------------------------------
# OrderStatusReport tests
# ------------------------------------------------------------------------------


class TestOrderStatusReportParsing:
    """Tests for parse_futu_order_to_report."""

    def test_order_submitted(self, account_id: AccountId, ts_ns: int):
        """Futu WAITING_SUBMIT maps to Nautilus SUBMITTED."""
        order = {
            "code": "US.AAPL",
            "trd_side": "BUY",
            "order_type": "NORMAL",
            "order_status": "WAITING_SUBMIT",
            "qty": 100,
            "price": 150.25,
            "order_id": "12345",
            "create_time": "2024-01-15 09:30:00",
            "updated_time": "2024-01-15 09:30:01",
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "time_in_force": "DAY",
        }

        report = parse_futu_order_to_report(order, account_id)

        assert isinstance(report, OrderStatusReport)
        assert report.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert report.venue_order_id == VenueOrderId("12345")
        assert report.order_side == OrderSide.BUY
        assert report.order_type == OrderType.LIMIT
        assert report.order_status == OrderStatus.SUBMITTED
        assert report.time_in_force == TimeInForce.DAY
        assert str(report.quantity) == "100"
        assert str(report.filled_qty) == "0"
        assert str(report.price) == "150.25"
        assert report.account_id == account_id
        assert report.ts_accepted > 0
        assert report.ts_last > 0

    def test_order_filled(self, account_id: AccountId):
        """Futu FILLED_ALL maps to Nautilus FILLED."""
        order = {
            "code": "US.TSLA",
            "trd_side": "SELL",
            "order_type": "MARKET",
            "order_status": "FILLED_ALL",
            "qty": 50,
            "price": 0,
            "order_id": "99999",
            "create_time": "2024-01-15 10:00:00",
            "updated_time": "2024-01-15 10:00:05",
            "dealt_qty": 50,
            "dealt_avg_price": 250.0,
            "time_in_force": "DAY",
        }

        report = parse_futu_order_to_report(order, account_id)

        assert report.order_status == OrderStatus.FILLED
        assert str(report.filled_qty) == "50"
        assert str(report.avg_px) == "250.0"

    def test_order_cancelled(self, account_id: AccountId):
        """Futu CANCELLED_ALL maps to Nautilus CANCELED."""
        order = {
            "code": "HK.00700",
            "trd_side": "BUY",
            "order_type": "NORMAL",
            "order_status": "CANCELLED_ALL",
            "qty": 200,
            "price": 400.0,
            "order_id": "88888",
            "create_time": "2024-01-15 09:30:00",
            "updated_time": "2024-01-15 09:31:00",
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "time_in_force": "GTC",
        }

        report = parse_futu_order_to_report(order, account_id)

        assert report.instrument_id == InstrumentId.from_str("00700.HKEX")
        assert report.order_status == OrderStatus.CANCELED
        assert report.time_in_force == TimeInForce.GTC

    def test_order_rejected(self, account_id: AccountId):
        """Futu SUBMIT_FAILED maps to Nautilus REJECTED."""
        order = {
            "code": "US.AAPL",
            "trd_side": "SELL_SHORT",
            "order_type": "NORMAL",
            "order_status": "SUBMIT_FAILED",
            "qty": 10,
            "price": 150.0,
            "order_id": "77777",
            "create_time": "2024-01-15 09:30:00",
            "updated_time": "2024-01-15 09:30:01",
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "time_in_force": "IOC",
        }

        report = parse_futu_order_to_report(order, account_id)

        assert report.order_side == OrderSide.SELL
        assert report.order_status == OrderStatus.REJECTED
        assert report.time_in_force == TimeInForce.IOC

    def test_order_int_enums(self, account_id: AccountId):
        """Parsing works with integer enum values too."""
        order = {
            "code": "US.AAPL",
            "trd_side": 1,  # BUY
            "order_type": 1,  # NORMAL
            "order_status": 5,  # SUBMITTED
            "qty": 100,
            "price": 150.25,
            "order_id": "12345",
            "create_timestamp": 1_234_567_890.0,
            "update_timestamp": 1_234_567_891.0,
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "time_in_force": 0,  # DAY
        }

        report = parse_futu_order_to_report(order, account_id)

        assert report.order_status == OrderStatus.ACCEPTED
        assert report.time_in_force == TimeInForce.DAY

    def test_order_missing_price(self, account_id: AccountId):
        """Market orders may have no price field."""
        order = {
            "code": "US.AAPL",
            "trd_side": "BUY",
            "order_type": "MARKET",
            "order_status": "SUBMITTED",
            "qty": 100,
            "order_id": "12345",
            "create_time": "2024-01-15 09:30:00",
            "updated_time": "2024-01-15 09:30:01",
            "dealt_qty": 0,
        }

        report = parse_futu_order_to_report(order, account_id)

        assert report.price is None
        assert report.order_type == OrderType.MARKET


# ------------------------------------------------------------------------------
# FillReport tests
# ------------------------------------------------------------------------------


class TestFillReportParsing:
    """Tests for parse_futu_fill_to_report."""

    def test_basic_fill(self, account_id: AccountId):
        fill = {
            "code": "US.AAPL",
            "deal_id": "FILL-001",
            "order_id": "12345",
            "qty": 50,
            "price": 150.25,
            "trd_side": "BUY",
            "create_time": "2024-01-15 09:30:00",
        }

        report = parse_futu_fill_to_report(fill, account_id)

        assert isinstance(report, FillReport)
        assert report.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert report.venue_order_id == VenueOrderId("12345")
        assert report.trade_id == TradeId("FILL-001")
        assert report.order_side == OrderSide.BUY
        assert str(report.last_qty) == "50"
        assert str(report.last_px) == "150.25"
        assert report.commission == Money(0, Currency.from_str("USD"))
        assert report.liquidity_side == LiquiditySide.NO_LIQUIDITY_SIDE
        assert report.ts_event > 0

    def test_fill_hkd_venue(self, account_id: AccountId):
        fill = {
            "code": "HK.00700",
            "deal_id": "FILL-002",
            "order_id": "88888",
            "qty": 100,
            "price": 400.0,
            "trd_side": "SELL",
            "create_timestamp": 1_234_567_890.0,
        }

        report = parse_futu_fill_to_report(fill, account_id)

        assert report.instrument_id == InstrumentId.from_str("00700.HKEX")
        assert report.order_side == OrderSide.SELL
        assert report.commission == Money(0, Currency.from_str("HKD"))


# ------------------------------------------------------------------------------
# PositionStatusReport tests
# ------------------------------------------------------------------------------


class TestPositionStatusReportParsing:
    """Tests for parse_futu_position_to_report."""

    def test_long_position(self, account_id: AccountId):
        position = {
            "code": "US.AAPL",
            "qty": 100,
            "position_side": "LONG",
            "cost_price": 150.25,
        }

        report = parse_futu_position_to_report(position, account_id)

        assert isinstance(report, PositionStatusReport)
        assert report.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert report.position_side == PositionSide.LONG
        assert str(report.quantity) == "100"
        assert str(report.avg_px_open) == "150.25"

    def test_short_position(self, account_id: AccountId):
        position = {
            "code": "US.TSLA",
            "qty": 50,
            "position_side": "SHORT",
        }

        report = parse_futu_position_to_report(position, account_id)

        assert report.position_side == PositionSide.SHORT
        assert str(report.quantity) == "50"

    def test_flat_position(self, account_id: AccountId):
        position = {
            "code": "US.AAPL",
            "qty": 0,
            "position_side": "LONG",
        }

        report = parse_futu_position_to_report(position, account_id)

        assert report.position_side == PositionSide.FLAT
        assert str(report.quantity) == "0"


# ------------------------------------------------------------------------------
# Handler tests
# ------------------------------------------------------------------------------


class TestTradeOrderHandler:
    """Tests for TradeOrderHandler push callback."""

    def test_handler_instantiation(self, account_id: AccountId):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        h = TradeOrderHandler(queue, account_id, loop=loop)
        assert h._queue is queue
        assert h._account_id == account_id
        assert h._loop is loop

    def test_handler_puts_report_on_queue(self, account_id: AccountId):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        TradeOrderHandler(queue, account_id, loop=loop)

        order = {
            "code": "US.AAPL",
            "trd_side": "BUY",
            "order_type": "NORMAL",
            "order_status": "SUBMITTED",
            "qty": 100,
            "price": 150.25,
            "order_id": "12345",
            "create_time": "2024-01-15 09:30:00",
            "updated_time": "2024-01-15 09:30:01",
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "time_in_force": "DAY",
        }
        report = parse_futu_order_to_report(order, account_id)
        loop.run_until_complete(queue.put(report))

        result = loop.run_until_complete(asyncio.wait_for(queue.get(), timeout=1.0))
        assert isinstance(result, OrderStatusReport)
        assert result.order_status == OrderStatus.ACCEPTED


class TestTradeDealHandler:
    """Tests for TradeDealHandler push callback."""

    def test_handler_instantiation(self, account_id: AccountId):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        h = TradeDealHandler(queue, account_id, loop=loop)
        assert h._queue is queue
        assert h._account_id == account_id
        assert h._loop is loop

    def test_handler_puts_fill_on_queue(self, account_id: AccountId):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        TradeDealHandler(queue, account_id, loop=loop)

        fill = {
            "code": "US.AAPL",
            "deal_id": "FILL-001",
            "order_id": "12345",
            "qty": 50,
            "price": 150.25,
            "trd_side": "BUY",
            "create_time": "2024-01-15 09:30:00",
        }
        report = parse_futu_fill_to_report(fill, account_id)
        loop.run_until_complete(queue.put(report))

        result = loop.run_until_complete(asyncio.wait_for(queue.get(), timeout=1.0))
        assert isinstance(result, FillReport)
        assert str(result.last_qty) == "50"
