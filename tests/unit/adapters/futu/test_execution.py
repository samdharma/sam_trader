"""Unit tests for FutuLiveExecutionClient."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from futu import RET_OK, ContextStatus, ModifyOrderOp
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
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, MarketOrder
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
    ctx.status = ContextStatus.READY
    ctx.place_order.return_value = (RET_OK, pd.DataFrame({"order_id": ["12345"]}))
    ctx.modify_order.return_value = (RET_OK, "")
    ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
    ctx.position_list_query.return_value = (RET_OK, pd.DataFrame())
    ctx.set_handler.return_value = RET_OK
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_market_order(
    instrument_id: str = "AAPL.NASDAQ",
    side: OrderSide = OrderSide.BUY,
    qty: int = 100,
    client_order_id: ClientOrderId | None = None,
) -> MarketOrder:
    """Create a simple MarketOrder for tests."""
    return MarketOrder(
        trader_id=TraderId("TRADER-001"),
        strategy_id=StrategyId("ORB-001"),
        instrument_id=InstrumentId.from_str(instrument_id),
        client_order_id=client_order_id or ClientOrderId("O-002"),
        order_side=side,
        quantity=Quantity.from_int(qty),
        init_id=UUID4(),
        ts_init=0,
        time_in_force=TimeInForce.DAY,
    )


# ---------------------------------------------------------------------------
# Submit order
# ---------------------------------------------------------------------------


class TestSubmitOrder:
    """Tests for order submission."""

    def test_submit_limit_order(self, event_loop, make_client, mock_trade_ctx):
        """A LIMIT order is mapped correctly to place_order."""
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        mock_trade_ctx.place_order.assert_called_once()
        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["code"] == "US.AAPL"
        assert call_kwargs["price"] == 150.5
        assert call_kwargs["qty"] == "100"
        assert call_kwargs["trd_side"] == "BUY"
        assert call_kwargs["order_type"] == "NORMAL"
        assert call_kwargs["time_in_force"] == "DAY"
        assert call_kwargs["trd_env"] == "SIMULATE"

    def test_submit_market_order(self, event_loop, make_client, mock_trade_ctx):
        """A MARKET order is mapped correctly to place_order."""
        client = make_client()
        order = _make_market_order()
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["price"] == 0.0
        assert call_kwargs["order_type"] == "MARKET"

    def test_submit_sell_order(self, event_loop, make_client, mock_trade_ctx):
        """A SELL order maps to the correct trd_side."""
        client = make_client()
        order = _make_limit_order(side=OrderSide.SELL)
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["trd_side"] == "SELL"

    def test_submit_order_generates_events(self, event_loop, make_client):
        """Successful submission generates Submitted and Accepted events."""
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.submit_order_command(order)

        with (
            patch.object(client, "generate_order_submitted") as mock_sub,
            patch.object(client, "generate_order_accepted") as mock_acc,
        ):
            event_loop.run_until_complete(client._submit_order(cmd))

        mock_sub.assert_called_once()
        mock_acc.assert_called_once()

    def test_submit_order_rejected_on_failure(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Failed place_order generates OrderRejected."""
        mock_trade_ctx.place_order.return_value = (-1, "Insufficient funds")
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.submit_order_command(order)

        with patch.object(client, "generate_order_rejected") as mock_rej:
            event_loop.run_until_complete(client._submit_order(cmd))

        mock_rej.assert_called_once()
        reason = mock_rej.call_args.kwargs.get("reason", "")
        assert "Insufficient funds" in reason

    def test_submit_order_uses_venue_alias_account(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Orders for HK instruments use the HK account alias."""
        client = make_client()
        client._venue_account_aliases[Venue("HKEX")] = AccountId("FUTU-999")

        order = _make_limit_order(instrument_id="00700.HKEX")
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["acc_id"] == 999


# ---------------------------------------------------------------------------
# Modify order
# ---------------------------------------------------------------------------


class TestModifyOrder:
    """Tests for order modification."""

    def test_modify_order(self, event_loop, make_client, mock_trade_ctx):
        """A modify command maps to modify_order with NORMAL op."""
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.modify_order_command(
            price=Price.from_str("155.00"),
            quantity=Quantity.from_int(50),
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        event_loop.run_until_complete(client._modify_order(cmd))

        mock_trade_ctx.modify_order.assert_called_once()
        call_kwargs = mock_trade_ctx.modify_order.call_args.kwargs
        assert call_kwargs["modify_order_op"] == ModifyOrderOp.NORMAL
        assert call_kwargs["order_id"] == "12345"
        assert call_kwargs["price"] == "155.00"
        assert call_kwargs["qty"] == "50"

    def test_modify_order_generates_updated(self, event_loop, make_client):
        """Successful modification generates OrderUpdated."""
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.modify_order_command(
            price=Price.from_str("155.00"),
            quantity=Quantity.from_int(50),
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        with patch.object(client, "generate_order_updated") as mock_upd:
            event_loop.run_until_complete(client._modify_order(cmd))

        mock_upd.assert_called_once()

    def test_modify_order_rejected_on_failure(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Failed modify_order generates OrderModifyRejected."""
        mock_trade_ctx.modify_order.return_value = (-1, "Order not found")
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.modify_order_command(
            price=Price.from_str("155.00"),
            order=order,
            venue_order_id=VenueOrderId("12345"),
        )

        with patch.object(client, "generate_order_modify_rejected") as mock_rej:
            event_loop.run_until_complete(client._modify_order(cmd))

        mock_rej.assert_called_once()


# ---------------------------------------------------------------------------
# Cancel order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """Tests for order cancellation."""

    def test_cancel_order(self, event_loop, make_client, mock_trade_ctx):
        """A cancel command maps to modify_order with CANCEL op."""
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.cancel_order_command(
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        event_loop.run_until_complete(client._cancel_order(cmd))

        mock_trade_ctx.modify_order.assert_called_once()
        call_kwargs = mock_trade_ctx.modify_order.call_args.kwargs
        assert call_kwargs["modify_order_op"] == ModifyOrderOp.CANCEL
        assert call_kwargs["order_id"] == "12345"
        assert call_kwargs["price"] == "0"
        assert call_kwargs["qty"] == "0"

    def test_cancel_order_generates_canceled(self, event_loop, make_client):
        """Successful cancellation generates OrderCanceled."""
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.cancel_order_command(
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        with patch.object(client, "generate_order_canceled") as mock_can:
            event_loop.run_until_complete(client._cancel_order(cmd))

        mock_can.assert_called_once()

    def test_cancel_order_rejected_on_failure(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Failed cancel generates OrderCancelRejected."""
        mock_trade_ctx.modify_order.return_value = (-1, "Already filled")
        client = make_client()
        order = _make_limit_order()
        cmd = TestCommandStubs.cancel_order_command(
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("12345"),
        )

        with patch.object(client, "generate_order_cancel_rejected") as mock_rej:
            event_loop.run_until_complete(client._cancel_order(cmd))

        mock_rej.assert_called_once()


# ---------------------------------------------------------------------------
# Bracket order list
# ---------------------------------------------------------------------------


class TestSubmitOrderList:
    """Tests for bracket order list submission."""

    def test_submit_bracket_orders(self, event_loop, make_client, mock_trade_ctx):
        """A bracket order list submits each child order sequentially."""
        from nautilus_trader.model.identifiers import OrderListId
        from nautilus_trader.model.orders.list import OrderList

        client = make_client()
        entry = _make_limit_order(client_order_id=ClientOrderId("O-ENTRY"))
        sl = _make_limit_order(
            client_order_id=ClientOrderId("O-SL"),
            side=OrderSide.SELL,
            price="149.00",
        )
        tp = _make_limit_order(
            client_order_id=ClientOrderId("O-TP"),
            side=OrderSide.SELL,
            price="160.00",
        )

        order_list = OrderList(
            order_list_id=OrderListId("OL-001"),
            orders=[entry, sl, tp],
        )

        # Patch _submit_order to count calls
        with patch.object(client, "_submit_order") as mock_submit:
            event_loop.run_until_complete(
                client._submit_order_list(
                    TestCommandStubs.submit_order_list_command(order_list)
                )
            )

        assert mock_submit.call_count == 3


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Tests for connection lifecycle."""

    def test_connect_starts_push_loop(self, event_loop, make_client, mock_trade_ctx):
        client = make_client()
        event_loop.run_until_complete(client._connect())

        assert client._push_task is not None
        assert not client._push_task.done()
        event_loop.run_until_complete(client._disconnect())

    def test_connect_sets_up_handlers(self, event_loop, make_client, mock_trade_ctx):
        client = make_client()
        event_loop.run_until_complete(client._connect())

        assert mock_trade_ctx.set_handler.call_count >= 2
        event_loop.run_until_complete(client._disconnect())

    def test_disconnect_cancels_push_loop(
        self, event_loop, make_client, mock_trade_ctx
    ):
        client = make_client()
        event_loop.run_until_complete(client._connect())
        task = client._push_task
        assert task is not None

        event_loop.run_until_complete(client._disconnect())

        assert task.cancelled() or task.done()
        assert client._push_task is None

    def test_connect_refreshes_stale_context(self, event_loop, make_client):
        """If _trade_ctx is not READY, _connect() must fetch a fresh context."""
        mock_stale = MagicMock()
        mock_stale.status = ContextStatus.CLOSED
        mock_stale.set_handler.return_value = RET_OK
        client = make_client(trade_ctx=mock_stale)

        mock_fresh = MagicMock()
        mock_fresh.status = ContextStatus.READY
        mock_fresh.set_handler.return_value = RET_OK
        mock_fresh.get_acc_list.return_value = (RET_OK, pd.DataFrame())
        mock_fresh.position_list_query.return_value = (RET_OK, pd.DataFrame())

        with patch(
            "sam_trader.adapters.futu.execution.get_cached_futu_trade_context"
        ) as mock_get:
            mock_get.return_value = mock_fresh
            event_loop.run_until_complete(client._connect())

        mock_get.assert_called_once_with("test-host", 11111, "SIMULATE", "US")
        assert client._trade_ctx is mock_fresh
        mock_stale.close.assert_called_once()

    def test_disconnect_sets_context_to_none(
        self, event_loop, make_client, mock_trade_ctx
    ):
        client = make_client()
        event_loop.run_until_complete(client._connect())
        assert client._trade_ctx is not None

        event_loop.run_until_complete(client._disconnect())

        assert client._trade_ctx is None


# ---------------------------------------------------------------------------
# Account discovery & venue aliases
# ---------------------------------------------------------------------------


class TestAccountDiscovery:
    """Tests for get_acc_list and venue alias registration."""

    def test_discover_accounts_registers_aliases(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Accounts with trdmarket_auth lists are mapped to venues.

        For US config (default), only sim_acc_type=2 (STOCK_AND_OPTION)
        accounts pass the filter.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [123, 456],
                    "trdmarket_auth": [[2], [2, 1]],  # US-only, US+HK
                    "sim_acc_type": [2, 2],  # both STOCK_AND_OPTION
                }
            ),
        )
        client = make_client()  # trd_market="US"
        event_loop.run_until_complete(client._discover_accounts())

        # Account 456 authorised for both markets — last registration wins per venue
        # (both 123 and 456 are authorised for NASDAQ; 456 is processed second)
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-456")
        # Account 456: also authorised for HKEX (1)
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-456")
        # First account (123) sets default _account_id
        assert client._account_id == AccountId("FUTU-123")

    def test_discover_accounts_filters_by_sim_acc_type(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Account discovery filters by sim_acc_type per market config.

        US config expects STOCK_AND_OPTION (2); non-matching accounts
        (e.g., sim_acc_type=1 for OPTION-only) should be excluded.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100, 200],
                    "trdmarket_auth": [[2], [2]],
                    "sim_acc_type": [1, 2],  # OPTION, STOCK_AND_OPTION
                }
            ),
        )
        client = make_client()  # trd_market="US" → expects sim_acc_type=2
        event_loop.run_until_complete(client._discover_accounts())

        # Only account 200 (sim_acc_type=2) should be registered
        assert Venue("NASDAQ") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-200")

    def test_discover_accounts_hk_filters_stock_only(self, event_loop, mock_trade_ctx):
        """HK config expects STOCK (0); non-matching rejected."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
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

        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [300, 400],
                    "trdmarket_auth": [[1], [1]],
                    "sim_acc_type": [2, 0],  # STOCK_AND_OPTION, STOCK
                }
            ),
        )
        event_loop.run_until_complete(client._discover_accounts())

        # Only account 400 (sim_acc_type=0) should be registered for HK
        assert Venue("HKEX") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-400")

    def test_discover_accounts_passes_trd_env_simulate(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """_discover_accounts calls get_acc_list with trd_env=TrdEnv.SIMULATE."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [999],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()
        event_loop.run_until_complete(client._discover_accounts())

        mock_trade_ctx.get_acc_list.assert_called_once()
        from futu import TrdEnv

        assert (
            mock_trade_ctx.get_acc_list.call_args.kwargs.get("trd_env")
            == TrdEnv.SIMULATE
        )

    def test_discover_accounts_handles_string_trdmarket_auth(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """trdmarket_auth as comma-separated string is parsed correctly."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [777],
                    "trdmarket_auth": ["1,2"],  # CSV string
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()
        event_loop.run_until_complete(client._discover_accounts())

        # Both HKEX (1) and NASDAQ (2) should be registered
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-777")
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-777")

    def test_resolve_account_id_uses_alias(self, event_loop, make_client):
        client = make_client()
        client._venue_account_aliases[Venue("NASDAQ")] = AccountId("FUTU-999")

        aapl = InstrumentId.from_str("AAPL.NASDAQ")
        resolved = client._resolve_account_id(aapl)
        assert resolved == AccountId("FUTU-999")

    def test_resolve_account_id_fallback(self, event_loop, make_client):
        client = make_client()
        client._account_id = AccountId("FUTU-001")

        aapl = InstrumentId.from_str("AAPL.NASDAQ")
        resolved = client._resolve_account_id(aapl)
        assert resolved == AccountId("FUTU-001")

    def test_register_aliases_updates_client_id_default(self, event_loop, make_client):
        """Account discovery should override client_id-based default (FUTU-1).

        Reproduces the zero-padding bug: the old code compared against
        AccountId("FUTU-001") which never matched AccountId("FUTU-1"),
        so discovered accounts were silently ignored.
        """
        client = make_client()
        assert client._account_id == AccountId("FUTU-1")
        assert client._initial_account_id == AccountId("FUTU-1")

        accounts = [
            {"acc_id": 234387941, "trdmarket_auth": [1]},  # HK
        ]
        client._register_venue_account_aliases(accounts)

        # Should have updated from FUTU-1 (client_id default) to discovered account
        assert client._account_id == AccountId("FUTU-234387941")

    def test_register_aliases_updates_placeholder_account(
        self, event_loop, mock_trade_ctx
    ):
        """Discovery updates placeholder account ID (FUTU-1) to discovered acc_id."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
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

        assert client._account_id == AccountId("FUTU-1")
        assert client._initial_account_id == AccountId("FUTU-1")

        accounts = [
            {"acc_id": 234387941, "trdmarket_auth": [1]},  # HK
        ]
        client._register_venue_account_aliases(accounts)

        # Discovery replaces the placeholder
        assert client._account_id == AccountId("FUTU-234387941")
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId(
            "FUTU-234387941"
        )


# ---------------------------------------------------------------------------
# Position reconciliation
# ---------------------------------------------------------------------------


class TestPositionReconciliation:
    """Tests for position reconciliation on connect."""

    def test_reconcile_positions(self, event_loop, make_client, mock_trade_ctx):
        mock_trade_ctx.position_list_query.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "code": ["US.AAPL"],
                    "qty": [100],
                    "position_side": [0],  # LONG
                    "cost_price": [150.0],
                }
            ),
        )
        client = make_client()

        with patch.object(client, "_send_position_status_report") as mock_send:
            event_loop.run_until_complete(client._reconcile_positions())

        mock_send.assert_called_once()
        report = mock_send.call_args.args[0]
        assert report.instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert str(report.quantity) == "100"


# ---------------------------------------------------------------------------
# Reconciliation report generation
# ---------------------------------------------------------------------------


class TestReconciliationReports:
    """Tests for generate_order_status_reports, generate_fill_reports,
    generate_position_status_reports.
    """

    def test_generate_order_status_reports(
        self, event_loop, make_client, mock_trade_ctx
    ):
        mock_trade_ctx.history_order_list_query.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "code": ["US.AAPL"],
                    "order_id": ["12345"],
                    "trd_side": ["BUY"],
                    "order_type": ["NORMAL"],
                    "order_status": ["SUBMITTED"],
                    "qty": [100],
                    "dealt_qty": [0],
                    "price": [150.0],
                    "create_time": ["2026-05-22 10:00:00"],
                    "updated_time": ["2026-05-22 10:01:00"],
                }
            ),
        )
        client = make_client()
        from nautilus_trader.execution.messages import GenerateOrderStatusReports

        cmd = GenerateOrderStatusReports.from_dict(
            {
                "instrument_id": "AAPL.NASDAQ",
                "venue_order_id": None,
                "open_only": False,
                "start": None,
                "end": None,
                "command_id": str(UUID4()),
                "ts_init": 0,
            }
        )
        reports = event_loop.run_until_complete(
            client.generate_order_status_reports(cmd)
        )

        assert len(reports) == 1
        assert reports[0].venue_order_id.value == "12345"
        assert reports[0].instrument_id == InstrumentId.from_str("AAPL.NASDAQ")

    def test_generate_order_status_reports_empty_on_failure(
        self, event_loop, make_client, mock_trade_ctx
    ):
        mock_trade_ctx.history_order_list_query.return_value = (-1, "Error")
        client = make_client()
        from nautilus_trader.execution.messages import GenerateOrderStatusReports

        cmd = GenerateOrderStatusReports.from_dict(
            {
                "instrument_id": None,
                "venue_order_id": None,
                "open_only": False,
                "start": None,
                "end": None,
                "command_id": str(UUID4()),
                "ts_init": 0,
            }
        )
        reports = event_loop.run_until_complete(
            client.generate_order_status_reports(cmd)
        )

        assert reports == []

    def test_generate_fill_reports(self, event_loop, make_client, mock_trade_ctx):
        mock_trade_ctx.history_deal_list_query.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "code": ["US.AAPL"],
                    "deal_id": ["D-001"],
                    "order_id": ["12345"],
                    "trd_side": ["BUY"],
                    "qty": [50],
                    "price": [150.5],
                    "create_time": ["2026-05-22 10:00:00"],
                }
            ),
        )
        client = make_client()
        from nautilus_trader.execution.messages import GenerateFillReports

        cmd = GenerateFillReports.from_dict(
            {
                "instrument_id": "AAPL.NASDAQ",
                "venue_order_id": None,
                "start": None,
                "end": None,
                "command_id": str(UUID4()),
                "ts_init": 0,
            }
        )
        reports = event_loop.run_until_complete(client.generate_fill_reports(cmd))

        assert len(reports) == 1
        assert reports[0].trade_id.value == "D-001"
        assert reports[0].instrument_id == InstrumentId.from_str("AAPL.NASDAQ")

    def test_generate_fill_reports_empty_on_failure(
        self, event_loop, make_client, mock_trade_ctx
    ):
        mock_trade_ctx.history_deal_list_query.return_value = (-1, "Error")
        client = make_client()
        from nautilus_trader.execution.messages import GenerateFillReports

        cmd = GenerateFillReports.from_dict(
            {
                "instrument_id": None,
                "venue_order_id": None,
                "start": None,
                "end": None,
                "command_id": str(UUID4()),
                "ts_init": 0,
            }
        )
        reports = event_loop.run_until_complete(client.generate_fill_reports(cmd))

        assert reports == []

    def test_generate_position_status_reports(
        self, event_loop, make_client, mock_trade_ctx
    ):
        mock_trade_ctx.position_list_query.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "code": ["US.AAPL"],
                    "qty": [100],
                    "position_side": [0],  # LONG
                    "cost_price": [150.0],
                }
            ),
        )
        client = make_client()
        from nautilus_trader.execution.messages import GeneratePositionStatusReports

        cmd = GeneratePositionStatusReports.from_dict(
            {
                "instrument_id": "AAPL.NASDAQ",
                "start": None,
                "end": None,
                "command_id": str(UUID4()),
                "ts_init": 0,
            }
        )
        reports = event_loop.run_until_complete(
            client.generate_position_status_reports(cmd)
        )

        assert len(reports) == 1
        assert reports[0].instrument_id == InstrumentId.from_str("AAPL.NASDAQ")
        assert str(reports[0].quantity) == "100"

    def test_generate_position_status_reports_empty_on_failure(
        self, event_loop, make_client, mock_trade_ctx
    ):
        mock_trade_ctx.position_list_query.return_value = (-1, "Error")
        client = make_client()
        from nautilus_trader.execution.messages import GeneratePositionStatusReports

        cmd = GeneratePositionStatusReports.from_dict(
            {
                "instrument_id": None,
                "start": None,
                "end": None,
                "command_id": str(UUID4()),
                "ts_init": 0,
            }
        )
        reports = event_loop.run_until_complete(
            client.generate_position_status_reports(cmd)
        )

        assert reports == []


# ---------------------------------------------------------------------------
# Push loop
# ---------------------------------------------------------------------------


class TestPushLoop:
    """Tests for the exec push loop."""

    def test_push_loop_processes_order_status_report(
        self, event_loop, make_client, mock_trade_ctx
    ):
        client = make_client()
        event_loop.run_until_complete(client._connect())

        reports = []

        def _capture_send(report):
            reports.append(report)
            client._push_task.cancel()

        client._send_order_status_report = _capture_send  # type: ignore[method-assign]

        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.execution.reports import OrderStatusReport
        from nautilus_trader.model.enums import OrderSide, OrderStatus, OrderType
        from nautilus_trader.model.identifiers import AccountId
        from nautilus_trader.model.objects import Quantity

        report = OrderStatusReport(
            account_id=AccountId("FUTU-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            venue_order_id=VenueOrderId("123"),
            order_side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            order_status=OrderStatus.ACCEPTED,
            quantity=Quantity.from_int(100),
            filled_qty=Quantity.from_int(0),
            report_id=UUID4(),
            ts_accepted=0,
            ts_last=0,
            ts_init=0,
        )

        event_loop.run_until_complete(client._queue.put(report))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(client._push_task, timeout=0.5)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        event_loop.run_until_complete(client._disconnect())
        assert len(reports) == 1
        assert reports[0].venue_order_id.value == "123"

    def test_push_loop_processes_fill_report(
        self, event_loop, make_client, mock_trade_ctx
    ):
        client = make_client()
        event_loop.run_until_complete(client._connect())

        reports = []

        def _capture_send(report):
            reports.append(report)
            client._push_task.cancel()

        client._send_fill_report = _capture_send  # type: ignore[method-assign]

        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.execution.reports import FillReport
        from nautilus_trader.model.enums import LiquiditySide, OrderSide
        from nautilus_trader.model.identifiers import AccountId, TradeId
        from nautilus_trader.model.objects import Price, Quantity

        report = FillReport(
            account_id=AccountId("FUTU-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            venue_order_id=VenueOrderId("123"),
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
                asyncio.wait_for(client._push_task, timeout=0.5)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        event_loop.run_until_complete(client._disconnect())
        assert len(reports) == 1
        assert reports[0].trade_id.value == "T-001"
