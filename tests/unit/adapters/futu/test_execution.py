"""Unit tests for FutuLiveExecutionClient."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

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
from sam_trader.adapters.futu.execution import (
    FutuLiveExecutionClient,
    OrderRateLimiter,
    _parse_order_rate_limit,
)


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
        # Mock _await_account_registered to avoid 30s timeout in tests —
        # requires a live Portfolio on the message bus which isn't
        # available in unit tests.  generate_account_state() still runs
        # so we can verify it is called.
        client._await_account_registered = AsyncMock(  # type: ignore[method-assign]
            return_value=None
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
    time_in_force: TimeInForce = TimeInForce.DAY,
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
        time_in_force=time_in_force,
    )


def _make_market_order(
    instrument_id: str = "AAPL.NASDAQ",
    side: OrderSide = OrderSide.BUY,
    qty: int = 100,
    client_order_id: ClientOrderId | None = None,
    time_in_force: TimeInForce = TimeInForce.DAY,
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
        time_in_force=time_in_force,
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

    def test_discover_accounts_simulate_only(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Mixed REAL + SIMULATE accounts: only SIMULATE populate venue aliases.

        Even if ``get_acc_list`` returns both REAL and SIMULATE accounts
        (e.g. due to mock bypassing the API-level trd_env filter),
        ``_register_venue_account_aliases`` defensively excludes REAL.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100, 200, 300],
                    "trd_env": [0, 1, 0],  # SIMULATE, REAL, SIMULATE
                    "trdmarket_auth": [[2], [2], [2]],  # all US
                    "sim_acc_type": [2, 2, 2],
                }
            ),
        )
        client = make_client()  # trd_market="US"
        event_loop.run_until_complete(client._discover_accounts())

        # Only SIMULATE accounts registered; last one wins per venue
        # US market (code 2) maps to both NASDAQ and NYSE
        assert len(client._venue_account_aliases) >= 1
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-300")
        assert client._venue_account_aliases[Venue("NYSE")] == AccountId("FUTU-300")
        # Default account is the first SIMULATE account
        assert client._account_id == AccountId("FUTU-100")

    def test_discover_replaces_placeholder(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Factory-provided placeholder AccountId is replaced on connect.

        The factory creates a client with AccountId("FUTU-1") (from
        config.client_id).  During ``_connect`` → ``_discover_accounts``,
        this placeholder must be replaced by the first discovered
        SIMULATE paper trading account ID.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [234387941],
                    "trd_env": [0],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()  # placeholder: FUTU-1
        assert client._account_id == AccountId("FUTU-1")
        assert client._initial_account_id == AccountId("FUTU-1")

        event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-234387941")

    def test_empty_account_list(self, event_loop, make_client, mock_trade_ctx):
        """Empty account list: no aliases registered, placeholder kept."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(),
        )
        client = make_client()
        placeholder = client._account_id  # FUTU-1

        event_loop.run_until_complete(client._discover_accounts())

        assert client._venue_account_aliases == {}
        assert client._account_id == placeholder

    def test_only_real_accounts(self, event_loop, make_client, mock_trade_ctx):
        """Only REAL accounts returned: no aliases, placeholder kept.

        When every account in the response has ``trd_env`` REAL (1),
        ``_register_venue_account_aliases`` skips them all and logs a
        warning.  The factory-provided placeholder is left unchanged.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [999, 888],
                    "trd_env": [1, 1],  # all REAL
                    "trdmarket_auth": [[2], [2]],
                    "sim_acc_type": [2, 2],
                }
            ),
        )
        client = make_client()
        placeholder = client._account_id

        event_loop.run_until_complete(client._discover_accounts())

        assert client._venue_account_aliases == {}
        assert client._account_id == placeholder

    def test_register_venue_aliases_hk_stock(self, event_loop, make_client):
        """HK STOCK account (sim_acc_type=0, trdmarket_auth=[1]) → HKEX."""
        client = make_client()
        accounts = [
            {
                "acc_id": 500,
                "trd_env": 0,
                "trdmarket_auth": [1],  # FUTU_TRD_MARKET_HK
                "sim_acc_type": 0,  # STOCK
            },
        ]
        client._register_venue_account_aliases(accounts)

        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-500")

    def test_register_venue_aliases_us_stock_and_option(self, event_loop, make_client):
        """US STOCK_AND_OPTION account (sim_acc_type=2, trdmarket_auth=[2]) → NASDAQ."""
        client = make_client()
        accounts = [
            {
                "acc_id": 600,
                "trd_env": 0,
                "trdmarket_auth": [2],  # FUTU_TRD_MARKET_US
                "sim_acc_type": 2,  # STOCK_AND_OPTION
            },
        ]
        client._register_venue_account_aliases(accounts)

        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-600")

    def test_register_venue_aliases_excludes_real(self, event_loop, make_client):
        """REAL accounts (trd_env=1 or 'REAL') are excluded from aliases."""
        client = make_client()
        accounts = [
            {"acc_id": 100, "trd_env": 0, "trdmarket_auth": [2]},  # SIMULATE
            {"acc_id": 200, "trd_env": 1, "trdmarket_auth": [2]},  # REAL (int)
            {"acc_id": 300, "trd_env": "REAL", "trdmarket_auth": [2]},  # REAL (str)
        ]
        client._register_venue_account_aliases(accounts)

        # Only the SIMULATE account (100) should be registered
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-100")

    def test_register_venue_aliases_string_market_codes(self, event_loop, make_client):
        """trdmarket_auth list of string market names (SDK v10.6+) is parsed.

        Bug: int(m) crashed on 'HK', 'US' etc.
        """
        client = make_client()
        accounts = [
            {
                "acc_id": 19064358,
                "trd_env": 0,
                "trdmarket_auth": ["HK", "US", "HKCC", "SG", "HKFUND", "USFUND", "JP"],
                "sim_acc_type": 0,  # STOCK
            },
        ]
        client._register_venue_account_aliases(accounts)

        # Known venues should be registered
        acc = AccountId("FUTU-19064358")
        assert client._venue_account_aliases[Venue("HKEX")] == acc
        assert client._venue_account_aliases[Venue("NASDAQ")] == acc
        assert client._venue_account_aliases[Venue("SGX")] == acc
        # Fund markets are not in FUTU_TRD_MARKET_TO_VENUE (no venue mapping)
        assert Venue("HKEX") in client._venue_account_aliases
        assert Venue("NASDAQ") in client._venue_account_aliases

    def test_register_venue_aliases_mixed_int_str_codes(self, event_loop, make_client):
        """Mixed int and string market codes in trdmarket_auth list."""
        client = make_client()
        accounts = [
            {
                "acc_id": 500,
                "trd_env": 0,
                "trdmarket_auth": [1, "US", "3"],  # int, name, numeric string
                "sim_acc_type": 0,
            },
        ]
        client._register_venue_account_aliases(accounts)

        # All three codes (HK=1, US=2, CN=3) should be parsed
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-500")
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-500")
        assert client._venue_account_aliases[Venue("SSE")] == AccountId("FUTU-500")

    def test_register_venue_aliases_unknown_code_filtered(
        self,
        event_loop,
        make_client,
    ):
        """Unknown market codes are filtered out (None), not crashing."""
        client = make_client()
        accounts = [
            {
                "acc_id": 500,
                "trd_env": 0,
                "trdmarket_auth": ["HK", "UNKNOWN_MARKET", None, ""],
                "sim_acc_type": 0,
            },
        ]
        # Should not raise
        client._register_venue_account_aliases(accounts)

        # Only HK (1) should be registered
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-500")
        assert len(client._venue_account_aliases) == 1

    def test_resolve_account_id_per_market(self, event_loop, make_client):
        """HK instrument → HK paper acc_id, US → US, unknown → default."""
        client = make_client()
        client._venue_account_aliases = {
            Venue("HKEX"): AccountId("FUTU-888"),
            Venue("NASDAQ"): AccountId("FUTU-999"),
        }
        client._account_id = AccountId("FUTU-001")  # default fallback

        # HK instrument → HKEX alias
        assert client._resolve_account_id(
            InstrumentId.from_str("00700.HKEX")
        ) == AccountId("FUTU-888")

        # US instrument → NASDAQ alias
        assert client._resolve_account_id(
            InstrumentId.from_str("AAPL.NASDAQ")
        ) == AccountId("FUTU-999")

        # Unknown venue → default fallback
        assert client._resolve_account_id(
            InstrumentId.from_str("7203.XTKS")
        ) == AccountId("FUTU-001")

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
                    "trd_env": [0, 0],
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
                    "trd_env": [0, 0],
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
            paper_acc_type="STOCK",
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
        client._await_account_registered = AsyncMock(return_value=None)

        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [300, 400],
                    "trd_env": [0, 0],
                    "trdmarket_auth": [[1], [1]],
                    "sim_acc_type": [2, 0],  # STOCK_AND_OPTION, STOCK
                }
            ),
        )
        event_loop.run_until_complete(client._discover_accounts())

        # Only account 400 (sim_acc_type=0) should be registered for HK
        assert Venue("HKEX") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId("FUTU-400")

    def test_discover_accounts_calls_get_acc_list_with_no_args(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """_discover_accounts calls get_acc_list() with NO arguments.

        Futu API v10.6.6608's get_acc_list() takes no kwargs.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [999],
                    "trd_env": [0],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()
        event_loop.run_until_complete(client._discover_accounts())

        mock_trade_ctx.get_acc_list.assert_called_once()
        # No kwargs should be passed (the old trd_env kwarg is removed)
        assert mock_trade_ctx.get_acc_list.call_args.kwargs == {}

    def test_discover_accounts_post_filters_simulate_only(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """get_acc_list() may return both REAL and SIMULATE accounts.

        _discover_accounts() must post-filter to keep only SIMULATE
        (trd_env == TrdEnv.SIMULATE).  REAL accounts are excluded.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100, 200, 300],
                    "trd_env": [0, 1, 0],  # SIMULATE, REAL, SIMULATE
                    "trdmarket_auth": [[2], [2], [2]],
                    "sim_acc_type": [2, 2, 2],
                }
            ),
        )
        client = make_client()
        event_loop.run_until_complete(client._discover_accounts())

        # Only the two SIMULATE accounts (100, 300) should be processed.
        # Real account 200 should be filtered out before venue registration.
        assert client._account_id == AccountId("FUTU-100")
        # NASDAQ alias should be set to the last SIMULATE account (300)
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId("FUTU-300")

    def test_discover_accounts_handles_string_trdmarket_auth(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """trdmarket_auth as comma-separated string is parsed correctly."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [777],
                    "trd_env": [0],
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
            paper_acc_type="STOCK",
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

    # ── String trd_env / sim_acc_type (Futu SDK v10.6+ returns strings) ──

    def test_trd_env_string_simulate(self, event_loop, make_client, mock_trade_ctx):
        """trd_env as string "SIMULATE" is recognised as paper trading."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            paper_acc_type="STOCK",
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
        client._await_account_registered = AsyncMock(return_value=None)

        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [19064358],
                    "trd_env": ["SIMULATE"],
                    "trdmarket_auth": [[1]],
                    "sim_acc_type": [0],  # int still works
                }
            ),
        )
        event_loop.run_until_complete(client._discover_accounts())

        # Should be registered despite trd_env being a string
        assert len(client._venue_account_aliases) >= 1
        assert Venue("HKEX") in client._venue_account_aliases

    def test_trd_env_string_real_excluded(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """trd_env as string "REAL" is excluded from paper trading."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [999],
                    "trd_env": ["REAL"],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()  # trd_market="US"
        event_loop.run_until_complete(client._discover_accounts())

        # REAL string account excluded; falls back to env
        assert len(client._venue_account_aliases) == 0

    def test_sim_acc_type_string_stock_hk(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """sim_acc_type as string "STOCK" matches HK config."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            paper_acc_type="STOCK",
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
        client._await_account_registered = AsyncMock(return_value=None)

        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [19064358],
                    "trd_env": ["SIMULATE"],
                    "trdmarket_auth": [[1]],
                    "sim_acc_type": ["STOCK"],  # string!
                }
            ),
        )
        event_loop.run_until_complete(client._discover_accounts())

        assert Venue("HKEX") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId(
            "FUTU-19064358"
        )

    def test_sim_acc_type_string_stock_and_option_us(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """sim_acc_type as string "STOCK_AND_OPTION" matches US config."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [19064362],
                    "trd_env": ["SIMULATE"],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": ["STOCK_AND_OPTION"],  # string!
                }
            ),
        )
        client = make_client()  # trd_market="US"
        event_loop.run_until_complete(client._discover_accounts())

        assert Venue("NASDAQ") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId(
            "FUTU-19064362"
        )

    def test_bug_scenario_hk_simulate_filters_correctly(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Exact bug scenario: HK market, 3 accounts, string trd_env/sim_acc_type.

        Data as reported on 29-May HK session:
        - acc_id 281756477933385889: REAL, N/A, [HK, US, ...]
        - acc_id 19064358:           SIMULATE, STOCK, [HK]
        - acc_id 19064361:           SIMULATE, OPTION, [HK]

        HK + SIMULATE + paper_acc_type=STOCK → selects acc_id 19064358
        (STOCK, HK-authorised).
        """
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            paper_acc_type="STOCK",
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
        client._await_account_registered = AsyncMock(return_value=None)

        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [281756477933385889, 19064358, 19064361],
                    "trd_env": ["REAL", "SIMULATE", "SIMULATE"],
                    "sim_acc_type": ["N/A", "STOCK", "OPTION"],
                    "trdmarket_auth": [[1, 2], [1], [1]],
                }
            ),
        )
        event_loop.run_until_complete(client._discover_accounts())

        # Only acc_id 19064358 (STOCK, HK) should be selected
        assert client._account_id == AccountId("FUTU-19064358")
        assert Venue("HKEX") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("HKEX")] == AccountId(
            "FUTU-19064358"
        )

    # ── REAL mode tests ──

    def test_real_mode_uses_env_account(self, event_loop, make_client, mock_trade_ctx):
        """REAL trd_env uses FUTU_ACCOUNT_ID from environment."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="REAL",
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
        client._await_account_registered = AsyncMock(return_value=None)

        with patch.dict(os.environ, {"FUTU_ACCOUNT_ID": "999888777"}):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-999888777")

    def test_real_mode_warns_when_no_env_account(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """REAL trd_env without FUTU_ACCOUNT_ID logs warning."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="REAL",
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
        client._await_account_registered = AsyncMock(return_value=None)
        placeholder = client._account_id

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        # Account ID unchanged (stays at placeholder)
        assert client._account_id == placeholder

    # ── Account discovery failure / fallback tests ──

    def test_paper_account_id_override_on_no_match(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """When no paper account matches, FUTU_PAPER_ACCOUNT_ID is used as override."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100, 200],
                    "trd_env": ["SIMULATE", "SIMULATE"],
                    "trdmarket_auth": [[1], [1]],
                    "sim_acc_type": ["OPTION", "OPTION"],  # no STOCK for HK
                }
            ),
        )
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            paper_acc_type="STOCK",
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
        client._await_account_registered = AsyncMock(return_value=None)

        with patch.dict(os.environ, {"FUTU_PAPER_ACCOUNT_ID": "my-paper-456"}):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-my-paper-456")

    def test_ignores_futu_account_id_on_no_match(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """FUTU_ACCOUNT_ID is NOT used as a trading account fallback.

        FUTU_ACCOUNT_ID is the OpenD login account, not a trading
        account.  When discovery fails and only FUTU_ACCOUNT_ID is
        set (no FUTU_PAPER_ACCOUNT_ID), the placeholder is kept.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100, 200],
                    "trd_env": ["SIMULATE", "SIMULATE"],
                    "trdmarket_auth": [[1], [1]],
                    "sim_acc_type": ["OPTION", "OPTION"],  # no STOCK for HK
                }
            ),
        )
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="SIMULATE",
            trd_market="HK",
            paper_acc_type="STOCK",
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
        client._await_account_registered = AsyncMock(return_value=None)
        placeholder = client._account_id

        # Only FUTU_ACCOUNT_ID set — should NOT be used for trading
        with patch.dict(os.environ, {"FUTU_ACCOUNT_ID": "281756477933385889"}):
            event_loop.run_until_complete(client._discover_accounts())

        # Placeholder kept — FUTU_ACCOUNT_ID is NOT used as trading account
        assert client._account_id == placeholder

    def test_discovery_failure_keeps_placeholder_no_env(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """No match and no FUTU_PAPER_ACCOUNT_ID — placeholder kept, CRITICAL logged."""
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100],
                    "trd_env": ["REAL"],  # all REAL, none SIMULATE
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": ["STOCK_AND_OPTION"],
                }
            ),
        )
        client = make_client()
        placeholder = client._account_id

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == placeholder
        assert client._venue_account_aliases == {}

    def test_paper_account_id_override_on_empty_response(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Empty get_acc_list response uses FUTU_PAPER_ACCOUNT_ID override."""
        mock_trade_ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
        client = make_client()

        with patch.dict(os.environ, {"FUTU_PAPER_ACCOUNT_ID": "explicit-paper-789"}):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-explicit-paper-789")

    def test_registers_account_with_portfolio_after_discovery(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Account discovery registers the discovered account with Portfolio.

        After successful account discovery via ``_discover_accounts()``,
        the Nautilus Portfolio must be notified via ``generate_account_state()``
        so it can track orders, positions, and P&L for the discovered account.
        Without this step, the Portfolio rejects order events with:
        "Cannot update order: no account registered for FUTU-XXXXX".

        Acceptance Criterion (AC #4):
        Unit test — mock account discovery → verify generate_account_state()
        called with correct balance containing the discovered AccountId.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [19064357],
                    "trd_env": [0],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()
        # Mock generate_account_state so we can verify it is called.
        # (_await_account_registered is already mocked by make_client.)
        client.generate_account_state = MagicMock()  # type: ignore[method-assign]

        event_loop.run_until_complete(client._discover_accounts())

        # Verify generate_account_state was called (registers with Portfolio)
        assert (
            client.generate_account_state.called
        ), "generate_account_state must be called after account discovery"
        call_kwargs = client.generate_account_state.call_args.kwargs
        assert len(call_kwargs["balances"]) == 1
        balance = call_kwargs["balances"][0]
        assert str(balance.total) == "1000000.00 USD"
        assert str(balance.free) == "1000000.00 USD"
        assert call_kwargs["margins"] == []
        assert call_kwargs["reported"] is True

    def test_no_portfolio_registration_when_discovery_fails(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Portfolio registration skipped when discovery fails and no override.

        When account discovery finds no matching accounts and no override
        env var is set, the placeholder account ID is kept and the Portfolio
        is NOT notified (no generate_account_state call).
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100],
                    "trd_env": ["REAL"],  # all REAL, no SIMULATE
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()
        client.generate_account_state = MagicMock()  # type: ignore[method-assign]

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        # Placeholder kept — no Portfolio registration needed
        assert client._account_id == client._initial_account_id
        assert (
            not client.generate_account_state.called
        ), "generate_account_state must NOT be called when no account discovered"

    def test_registers_account_with_portfolio_on_paper_override(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Portfolio is notified when FUTU_PAPER_ACCOUNT_ID override is used.

        When discovery fails but FUTU_PAPER_ACCOUNT_ID is set, the override
        account must be registered with the Portfolio.
        """
        mock_trade_ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
        client = make_client()
        client.generate_account_state = MagicMock()  # type: ignore[method-assign]

        with patch.dict(os.environ, {"FUTU_PAPER_ACCOUNT_ID": "override-789"}):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-override-789")
        assert (
            client.generate_account_state.called
        ), "generate_account_state must be called when override account is used"

    def test_real_mode_registers_account_with_portfolio(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """REAL mode registers account with Portfolio when FUTU_ACCOUNT_ID is set."""
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="REAL",
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
        client._await_account_registered = AsyncMock(return_value=None)
        client.generate_account_state = MagicMock()  # type: ignore[method-assign]

        with patch.dict(os.environ, {"FUTU_ACCOUNT_ID": "999888777"}):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-999888777")
        assert (
            client.generate_account_state.called
        ), "generate_account_state must be called in REAL mode"

    def test_multi_venue_accounts_all_registered(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """All discovered venue aliases share one Portfolio account registration.

        When a single trading account is authorised for multiple markets
        (e.g., US authorised for both NASDAQ and NYSE), the Portfolio is
        registered once for the single underlying account.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [19064357],
                    "trd_env": [0],
                    "trdmarket_auth": [[1, 2]],  # HK + US (multi-market)
                    "sim_acc_type": [2],
                }
            ),
        )
        client = make_client()
        client.generate_account_state = MagicMock()  # type: ignore[method-assign]

        event_loop.run_until_complete(client._discover_accounts())

        # Both venues mapped to the same account
        assert Venue("HKEX") in client._venue_account_aliases
        assert Venue("NASDAQ") in client._venue_account_aliases
        assert Venue("NYSE") in client._venue_account_aliases
        assert client._venue_account_aliases[Venue("NASDAQ")] == AccountId(
            "FUTU-19064357"
        )

        # Portfolio registered once (single generate_account_state call)
        assert (
            client.generate_account_state.call_count == 1
        ), "Only one Portfolio registration per account discovery"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleHelpers:
    """Tests for _is_simulate_trd_env and _matches_sim_acc_type helpers."""

    def test_is_simulate_trd_env_int_0(self):
        from sam_trader.adapters.futu.execution import _is_simulate_trd_env

        assert _is_simulate_trd_env(0) is True

    def test_is_simulate_trd_env_int_1(self):
        from sam_trader.adapters.futu.execution import _is_simulate_trd_env

        assert _is_simulate_trd_env(1) is False

    def test_is_simulate_trd_env_str_simulate(self):
        from sam_trader.adapters.futu.execution import _is_simulate_trd_env

        assert _is_simulate_trd_env("SIMULATE") is True

    def test_is_simulate_trd_env_str_simulate_lower(self):
        from sam_trader.adapters.futu.execution import _is_simulate_trd_env

        assert _is_simulate_trd_env("simulate") is True

    def test_is_simulate_trd_env_str_real(self):
        from sam_trader.adapters.futu.execution import _is_simulate_trd_env

        assert _is_simulate_trd_env("REAL") is False

    def test_is_simulate_trd_env_none(self):
        from sam_trader.adapters.futu.execution import _is_simulate_trd_env

        assert _is_simulate_trd_env(None) is False

    def test_matches_sim_acc_type_int_match(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type(0, 0, "STOCK") is True

    def test_matches_sim_acc_type_int_no_match(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type(1, 0, "STOCK") is False

    def test_matches_sim_acc_type_str_match(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type("STOCK", 0, "STOCK") is True

    def test_matches_sim_acc_type_str_match_lower(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type("stock", 0, "STOCK") is True

    def test_matches_sim_acc_type_str_no_match(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type("OPTION", 0, "STOCK") is False

    def test_matches_sim_acc_type_stock_and_option(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type("STOCK_AND_OPTION", 2, "STOCK_AND_OPTION") is True

    def test_matches_sim_acc_type_none(self):
        from sam_trader.adapters.futu.execution import _matches_sim_acc_type

        assert _matches_sim_acc_type(None, 0, "STOCK") is False

    # ------------------------------------------------------------------
    # _parse_market_code
    # ------------------------------------------------------------------

    def test_parse_market_code_int(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code(1) == 1
        assert _parse_market_code(2) == 2
        assert _parse_market_code(15) == 15  # JP

    def test_parse_market_code_numeric_string(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code("1") == 1
        assert _parse_market_code("2") == 2

    def test_parse_market_code_market_name_upper(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code("HK") == 1
        assert _parse_market_code("US") == 2
        assert _parse_market_code("SG") == 6
        assert _parse_market_code("JP") == 15
        assert _parse_market_code("HKCC") == 4

    def test_parse_market_code_market_name_lower(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code("hk") == 1
        assert _parse_market_code("us") == 2

    def test_parse_market_code_fund_markets(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code("HKFUND") == 113
        assert _parse_market_code("USFUND") == 123
        assert _parse_market_code("JPFUND") == 126

    def test_parse_market_code_unknown_returns_none(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code("UNKNOWN") is None
        assert _parse_market_code("") is None

    def test_parse_market_code_non_str_non_int(self):
        from sam_trader.adapters.futu.execution import _parse_market_code

        assert _parse_market_code(None) is None
        assert _parse_market_code(3.14) is None
        assert _parse_market_code([]) is None


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


# ---------------------------------------------------------------------------
# Time-in-force (TIF) execution-level tests — scenarios 1-3, 6, 12
# ---------------------------------------------------------------------------


class TestOrderTimeInForceExecution:
    """Scenario 1-3: Order type (time_in_force) at execution level.

    Verifies:
      - DAY orders in SIMULATE are submitted as-is (scenario 1)
      - GTC orders in SIMULATE are auto-converted to DAY (scenario 2)
      - GTC orders in REAL are preserved as GTC (scenario 3)
    """

    def test_day_order_in_simulate_no_conversion(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Scenario 1: DAY orders in SIMULATE pass through unchanged.

        When trd_env=SIMULATE and the order already has time_in_force=DAY,
        the defense-in-depth GTC→DAY conversion must NOT trigger — the
        order is already in the correct TIF.
        """
        client = make_client()
        order = _make_limit_order(time_in_force=TimeInForce.DAY)
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["time_in_force"] == "DAY"
        assert call_kwargs["trd_env"] == "SIMULATE"

    def test_gtc_order_in_simulate_auto_converts_to_day(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Scenario 2: GTC orders in SIMULATE are auto-converted to DAY.

        Futu paper trading rejects GTC orders with:
          "Paper trading does not support GTC orders"

        The execution client's defense-in-depth logic in ``_submit_order``
        overrides GTC → DAY before calling ``place_order``.  This prevents
        rejections that would otherwise trip the circuit breaker.
        """
        client = make_client()
        order = _make_limit_order(time_in_force=TimeInForce.GTC)
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        # GTC is auto-converted to DAY at the execution layer
        assert call_kwargs["time_in_force"] == "DAY"

    def test_gtc_order_in_real_preserved(self, event_loop, make_client, mock_trade_ctx):
        """Scenario 3: GTC orders in REAL mode are preserved as GTC.

        In live trading (trd_env=REAL), Futu supports GTC orders.
        The execution client must NOT override them.
        """
        cfg = FutuExecClientConfig(
            host="test-host",
            port=11111,
            trd_env="REAL",
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
        order = _make_limit_order(time_in_force=TimeInForce.GTC)
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        # GTC preserved in REAL mode
        assert call_kwargs["time_in_force"] == "GTC"
        assert call_kwargs["trd_env"] == "REAL"

    def test_ioc_order_in_simulate_preserved(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """IOC orders in SIMULATE are not affected by GTC→DAY override."""
        client = make_client()
        order = _make_limit_order(time_in_force=TimeInForce.IOC)
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["time_in_force"] == "IOC"


class TestCircuitBreakerTIFSafety:
    """Scenario 6: GTC auto-correction prevents circuit breaker trip.

    The execution client converts GTC→DAY in SIMULATE before calling
    the Futu API.  This means Futu never sees a GTC order and never
    returns a rejection — the circuit breaker does NOT trip.

    Additionally, at the strategy level, the resolved TIF is already
    DAY when FUTU_TRD_ENV=SIMULATE, so the auto-correction is
    defense-in-depth.
    """

    def test_gtc_conversion_prevents_place_order_rejection(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """GTC→DAY conversion means place_order never receives GTC.

        The conversion happens BEFORE place_order is called, so Futu
        never processes a GTC tif — no rejection is generated.
        """
        client = make_client()
        order = _make_limit_order(time_in_force=TimeInForce.GTC)
        cmd = TestCommandStubs.submit_order_command(order)

        with patch.object(client, "generate_order_rejected") as mock_rej:
            event_loop.run_until_complete(client._submit_order(cmd))

        # No rejection emitted — GTC was auto-corrected to DAY
        mock_rej.assert_not_called()

    def test_day_order_never_triggers_gtc_override(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """DAY orders pass straight through — no conversion log/overhead."""
        client = make_client()
        order = _make_limit_order(time_in_force=TimeInForce.DAY)
        cmd = TestCommandStubs.submit_order_command(order)

        with patch.object(client, "generate_order_rejected") as mock_rej:
            event_loop.run_until_complete(client._submit_order(cmd))

        mock_rej.assert_not_called()
        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["time_in_force"] == "DAY"


# ---------------------------------------------------------------------------
# Account mismatch regression — scenario 12
# ---------------------------------------------------------------------------


class TestAccountIdRouting:
    """Scenario 12: Verify FUTU-1 placeholder never reaches live order routing.

    After account discovery, the placeholder account ID (e.g., ``FUTU-1``)
    must be replaced by the discovered paper trading account.  Orders
    must route with the correct ``acc_id``, not the factory placeholder.
    """

    def test_order_uses_discovered_account_after_discovery(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """After discovery, orders use the discovered account, not FUTU-1."""
        client = make_client()
        # Simulate discovery finding a paper trading account
        client._venue_account_aliases[Venue("NASDAQ")] = AccountId("FUTU-19064357")
        client._account_id = AccountId("FUTU-19064357")
        client._set_account_id(AccountId("FUTU-19064357"))

        order = _make_limit_order(instrument_id="AAPL.NASDAQ")
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        # Must use the discovered account, not the factory placeholder
        assert call_kwargs["acc_id"] == 19064357

    def test_order_uses_placeholder_when_no_discovery(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Without discovery, the placeholder is used (expected behavior).

        When account discovery fails and no FUTU_PAPER_ACCOUNT_ID is set,
        the placeholder remains.  This is an expected failure mode — the
        operator must create a paper trading account first.
        """
        client = make_client()  # placeholder: FUTU-1
        assert client._account_id == AccountId("FUTU-1")

        order = _make_limit_order(instrument_id="AAPL.NASDAQ")
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["acc_id"] == 1

    def test_order_uses_venue_alias_over_default_account(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Per-venue alias takes priority over default account for routing."""
        client = make_client()
        # HK account and US account are different
        client._account_id = AccountId("FUTU-19064357")  # default = US
        client._venue_account_aliases[Venue("HKEX")] = AccountId("FUTU-19064358")

        # Order for HK instrument → should use HK account
        order = _make_limit_order(instrument_id="00700.HKEX")
        cmd = TestCommandStubs.submit_order_command(order)

        event_loop.run_until_complete(client._submit_order(cmd))

        call_kwargs = mock_trade_ctx.place_order.call_args.kwargs
        assert call_kwargs["acc_id"] == 19064358  # HK account, not US default


# ---------------------------------------------------------------------------
# Account discovery — scenario 9: Critical log on failure without env var
# ---------------------------------------------------------------------------


class TestAccountDiscoveryCriticalLog:
    """Scenario 9: Clear CRITICAL log when discovery fails with no override.

    When get_acc_list() returns no matching paper accounts and
    FUTU_PAPER_ACCOUNT_ID is not set, the _handle_account_discovery_failure
    method must emit an ERROR-level log with a clear diagnostic message
    and keep the placeholder account ID so orders fail with identifiable
    errors rather than silently routing to the wrong account.
    """

    def test_critical_log_on_failure_no_override(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Scenario 9: Clear diagnostic when discovery fails with no override.

        When get_acc_list() returns no matching paper accounts and
        FUTU_PAPER_ACCOUNT_ID is not set, the placeholder is preserved.
        Orders will fail with identifiable errors rather than silently
        routing to the wrong account.

        Cython Logger is read-only — we verify the *outcome* (state),
        not the log call itself.
        """
        mock_trade_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [100],
                    "trd_env": ["REAL"],  # all REAL, none SIMULATE
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": ["STOCK_AND_OPTION"],
                }
            ),
        )
        client = make_client()
        placeholder = client._account_id

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        # Placeholder kept — orders will fail with identifiable error
        assert client._account_id == placeholder
        assert client._venue_account_aliases == {}

    def test_placeholder_kept_ensures_orders_fail_identifiably(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Placeholder account kept so orders fail with identifiable error.

        Rather than silently using the wrong account, the placeholder
        ensures order failures are obvious and debuggable.
        """
        mock_trade_ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
        client = make_client()
        placeholder = client._account_id

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        # Placeholder remains unchanged — no silent fallback to wrong account
        assert client._account_id == placeholder


# ---------------------------------------------------------------------------
# Account discovery — scenario 8: Empty response fallback to env override
# ---------------------------------------------------------------------------


class TestAccountDiscoveryEmptyFallback:
    """Scenario 8: Empty get_acc_list response falls back to FUTU_PAPER_ACCOUNT_ID.

    When the Futu API returns an empty account list (no paper accounts
    created), the system falls back to the FUTU_PAPER_ACCOUNT_ID env var
    if configured.
    """

    def test_empty_response_falls_back_to_paper_override(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Empty response → FUTU_PAPER_ACCOUNT_ID used."""
        mock_trade_ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
        client = make_client()

        with patch.dict(os.environ, {"FUTU_PAPER_ACCOUNT_ID": "explicit-paper-789"}):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == AccountId("FUTU-explicit-paper-789")

    def test_empty_response_no_override_logs_warning(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Empty response without override → placeholder kept.

        Cython Logger is read-only — verify state outcome.
        """
        mock_trade_ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())
        client = make_client()
        placeholder = client._account_id

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        assert client._account_id == placeholder


# ---------------------------------------------------------------------------
# Rate limiter — env var parsing
# ---------------------------------------------------------------------------


class TestParseOrderRateLimit:
    """Tests for ``_parse_order_rate_limit`` env-var parser."""

    def test_default_when_none(self):
        max_orders, window = _parse_order_rate_limit(None)
        assert max_orders == 10
        assert window == 30.0

    def test_default_when_empty_string(self):
        max_orders, window = _parse_order_rate_limit("")
        assert max_orders == 10
        assert window == 30.0

    def test_valid_count_per_seconds(self):
        max_orders, window = _parse_order_rate_limit("5/60")
        assert max_orders == 5
        assert window == 60.0

    def test_valid_float_window(self):
        max_orders, window = _parse_order_rate_limit("15/30.5")
        assert max_orders == 15
        assert window == 30.5

    def test_wrong_format_returns_default(self):
        max_orders, window = _parse_order_rate_limit("abc")
        assert max_orders == 10
        assert window == 30.0

    def test_negative_count_returns_default(self):
        max_orders, window = _parse_order_rate_limit("-1/30")
        assert max_orders == 10
        assert window == 30.0

    def test_zero_window_returns_default(self):
        max_orders, window = _parse_order_rate_limit("10/0")
        assert max_orders == 10
        assert window == 30.0

    def test_negative_window_returns_default(self):
        max_orders, window = _parse_order_rate_limit("10/-5")
        assert max_orders == 10
        assert window == 30.0

    def test_extra_slashes_returns_default(self):
        max_orders, window = _parse_order_rate_limit("10/30/extra")
        assert max_orders == 10
        assert window == 30.0


# ---------------------------------------------------------------------------
# Rate limiter — core logic
# ---------------------------------------------------------------------------


class TestOrderRateLimiter:
    """Tests for ``OrderRateLimiter`` sliding-window throttle."""

    def test_under_limit_passes_immediately(self, event_loop):
        """Orders under the limit are not delayed."""
        limiter = OrderRateLimiter(max_orders=5, window_seconds=30.0)

        for _ in range(5):
            was_delayed, delay = event_loop.run_until_complete(limiter.acquire())
            assert was_delayed is False
            assert delay == 0.0

        assert limiter.total_submitted == 5
        assert limiter.total_delayed == 0

    def test_exceeds_limit_is_delayed(self, event_loop):
        """Beyond the limit, acquire() returns was_delayed=True."""
        limiter = OrderRateLimiter(max_orders=3, window_seconds=0.3)

        # First 3 pass immediately
        for _ in range(3):
            was_delayed, _ = event_loop.run_until_complete(limiter.acquire())
            assert was_delayed is False

        # 4th should be delayed
        was_delayed, delay = event_loop.run_until_complete(limiter.acquire())
        assert was_delayed is True
        assert delay > 0
        assert limiter.total_delayed >= 1

    def test_delay_capped_at_max(self, event_loop):
        """Delay is capped at 5 seconds even if window is large."""
        limiter = OrderRateLimiter(max_orders=1, window_seconds=999.0)

        # First order passes immediately
        event_loop.run_until_complete(limiter.acquire())

        # Second must wait, but capped at 5s
        was_delayed, delay = event_loop.run_until_complete(limiter.acquire())
        assert was_delayed is True
        # Capped at 5.0 with tolerance for scheduling
        assert delay <= 5.0 + 0.5

    def test_small_window_allows_recovery(self, event_loop):
        """With a very small window, orders recover after waiting."""
        limiter = OrderRateLimiter(max_orders=2, window_seconds=0.1)

        # Fill up
        event_loop.run_until_complete(limiter.acquire())
        event_loop.run_until_complete(limiter.acquire())

        # This should be delayed for ~0.1s (the window)
        was_delayed, _ = event_loop.run_until_complete(limiter.acquire())
        assert was_delayed is True

    def test_statistics_accumulate(self, event_loop):
        """total_submitted and total_delayed track across calls."""
        limiter = OrderRateLimiter(max_orders=2, window_seconds=0.2)

        event_loop.run_until_complete(limiter.acquire())
        event_loop.run_until_complete(limiter.acquire())
        event_loop.run_until_complete(
            limiter.acquire()
        )  # at least 1 of the next 2 is delayed
        event_loop.run_until_complete(limiter.acquire())

        assert limiter.total_submitted == 4
        assert limiter.total_delayed >= 1


# ---------------------------------------------------------------------------
# Rate limiter — integration with FutuLiveExecutionClient
# ---------------------------------------------------------------------------


class TestSubmitOrderRateLimit:
    """Tests that ``_submit_order`` is throttled by the rate limiter."""

    def test_rate_limiter_created_with_defaults(self, event_loop, make_client):
        """Client creates a rate limiter with default values when env is unset."""
        with patch.dict(os.environ, {}, clear=True):
            client = make_client()

        assert client._order_rate_limiter is not None
        assert client._order_rate_limiter.max_orders == 10
        assert client._order_rate_limiter.window_seconds == 30.0

    def test_rate_limiter_respects_env_var(self, event_loop, make_client):
        """FUTU_ORDER_RATE_LIMIT is parsed and used."""
        with patch.dict(os.environ, {"FUTU_ORDER_RATE_LIMIT": "5/60"}):
            client = make_client()

        assert client._order_rate_limiter.max_orders == 5
        assert client._order_rate_limiter.window_seconds == 60.0

    def test_rapid_submissions_are_throttled(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """6 submissions with max_orders=3, small window → some delayed."""
        with patch.dict(os.environ, {"FUTU_ORDER_RATE_LIMIT": "10/30"}):
            client = make_client()

        # Tiny window and small limit for fast test
        client._order_rate_limiter.max_orders = 3
        client._order_rate_limiter.window_seconds = 0.3

        async def _submit_n_orders(n: int):
            for i in range(n):
                order = _make_limit_order(
                    client_order_id=ClientOrderId(f"O-RL-{i:03d}"),
                )
                cmd = TestCommandStubs.submit_order_command(order)
                await client._submit_order(cmd)

        event_loop.run_until_complete(_submit_n_orders(6))

        # At least some orders should have been delayed
        assert client._order_rate_limiter.total_submitted == 6
        assert client._order_rate_limiter.total_delayed >= 1
        assert mock_trade_ctx.place_order.call_count == 6

    def test_submit_order_list_throttled_per_order(
        self, event_loop, make_client, mock_trade_ctx
    ):
        """Each order in a bracket list goes through the rate limiter."""
        from nautilus_trader.model.identifiers import OrderListId
        from nautilus_trader.model.orders.list import OrderList

        with patch.dict(os.environ, {"FUTU_ORDER_RATE_LIMIT": "2/30"}):
            client = make_client()

        # Tiny window for test speed
        client._order_rate_limiter.max_orders = 2
        client._order_rate_limiter.window_seconds = 0.2

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

        event_loop.run_until_complete(
            client._submit_order_list(
                TestCommandStubs.submit_order_list_command(order_list)
            )
        )

        # All 3 orders submitted; at least 1 was delayed (window size 2)
        assert client._order_rate_limiter.total_submitted == 3
        assert client._order_rate_limiter.total_delayed >= 1
        assert mock_trade_ctx.place_order.call_count == 3
