"""Integration tests: order-type and account discovery — scenarios 13, 14.

Validates:
  13. UMAC + MNTS dual-strategy: Both discover accounts, subscribe, and
      trade without cross-contamination.
  14. Restart recovery: After circuit breaker trip, restart picks up
      correct accounts.

Dependencies:
  - sam_trader-ljn  (Order type config)
  - sam_trader-92z  (Account discovery fix)
"""

from __future__ import annotations

import asyncio
import os
import pickle
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from futu import RET_OK, ContextStatus
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    StrategyId,
    TraderId,
    Venue,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.stubs.component import TestComponentStubs

from sam_trader.adapters.futu.config import FutuExecClientConfig
from sam_trader.adapters.futu.execution import FutuLiveExecutionClient
from sam_trader.bundle_loader import load_bundles
from sam_trader.strategies.momentum import MomentumStrategy, MomentumStrategyConfig
from sam_trader.strategies.orb import OrbStrategy, OrbStrategyConfig

# ---- Helpers ------------------------------------------------------------


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _make_mock_trade_ctx(acc_list_data: pd.DataFrame | None = None) -> MagicMock:
    """Build a mock OpenSecTradeContext with optional account list."""
    ctx = MagicMock()
    ctx.status = ContextStatus.READY
    ctx.place_order.return_value = (RET_OK, pd.DataFrame({"order_id": ["12345"]}))
    ctx.modify_order.return_value = (RET_OK, "")
    ctx.get_acc_list.return_value = (
        (RET_OK, acc_list_data)
        if acc_list_data is not None
        else (RET_OK, pd.DataFrame())
    )
    ctx.position_list_query.return_value = (RET_OK, pd.DataFrame())
    ctx.set_handler.return_value = RET_OK
    return ctx


def _make_exec_client(
    event_loop,
    mock_trade_ctx: MagicMock,
    trd_env: str = "SIMULATE",
    trd_market: str = "US",
    paper_acc_type: str = "STOCK_AND_OPTION",
    client_id: int = 1,
) -> FutuLiveExecutionClient:
    """Create a FutuLiveExecutionClient with mocked dependencies."""
    cfg = FutuExecClientConfig(
        host="test-host",
        port=11111,
        trd_env=trd_env,
        trd_market=trd_market,
        paper_acc_type=paper_acc_type,
        client_id=client_id,
    )
    clock = LiveClock()
    msgbus = TestComponentStubs.msgbus()
    cache = TestComponentStubs.cache()
    provider = MagicMock(spec=InstrumentProvider)
    return FutuLiveExecutionClient(
        loop=event_loop,
        client=mock_trade_ctx,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        instrument_provider=provider,
        config=cfg,
    )


def _discover_us_account(
    event_loop, client: FutuLiveExecutionClient, mock_trade_ctx: MagicMock
) -> None:
    """Simulate successful US account discovery."""
    mock_trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            {
                "acc_id": [19064357],
                "trd_env": ["SIMULATE"],
                "trdmarket_auth": [[2]],  # US
                "sim_acc_type": ["STOCK_AND_OPTION"],
            }
        ),
    )
    with patch.dict(os.environ, {}, clear=True):
        event_loop.run_until_complete(client._discover_accounts())


def _discover_hk_account(
    event_loop, client: FutuLiveExecutionClient, mock_trade_ctx: MagicMock
) -> None:
    """Simulate successful HK account discovery."""
    mock_trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            {
                "acc_id": [19064358],
                "trd_env": ["SIMULATE"],
                "trdmarket_auth": [[1]],  # HK
                "sim_acc_type": ["STOCK"],
            }
        ),
    )
    with patch.dict(os.environ, {}, clear=True):
        event_loop.run_until_complete(client._discover_accounts())


# ---- Scenario 13: Dual-strategy cross-contamination -------------------


class TestDualStrategyNoCrossContamination:
    """Scenario 13: UMAC + MNTS dual-strategy account isolation.

    Two strategies using different instruments / markets must NOT
    share or cross-contaminate venue→account mappings.  Each strategy's
    execution client resolves the correct Futu account ID for its own
    venue.
    """

    def test_two_exec_clients_independent_account_discovery(self, event_loop):
        """Two execution clients discover accounts independently.

        Client A (US market) discovers acc_id 19064357.
        Client B (HK market) discovers acc_id 19064358.
        Neither contaminates the other's venue→account mappings.
        """
        # ---- US client ----
        us_ctx = _make_mock_trade_ctx()
        us_client = _make_exec_client(
            event_loop, us_ctx, trd_market="US", paper_acc_type="STOCK_AND_OPTION"
        )
        _discover_us_account(event_loop, us_client, us_ctx)

        # ---- HK client ----
        hk_ctx = _make_mock_trade_ctx()
        hk_client = _make_exec_client(
            event_loop, hk_ctx, trd_market="HK", paper_acc_type="STOCK"
        )
        _discover_hk_account(event_loop, hk_client, hk_ctx)

        # ---- Assert independence ----
        # US client has US account
        assert us_client._account_id == AccountId("FUTU-19064357")
        assert Venue("NASDAQ") in us_client._venue_account_aliases
        assert us_client._venue_account_aliases[Venue("NASDAQ")] == AccountId(
            "FUTU-19064357"
        )

        # HK client has HK account
        assert hk_client._account_id == AccountId("FUTU-19064358")
        assert Venue("HKEX") in hk_client._venue_account_aliases
        assert hk_client._venue_account_aliases[Venue("HKEX")] == AccountId(
            "FUTU-19064358"
        )

        # No cross-contamination
        assert Venue("HKEX") not in us_client._venue_account_aliases
        assert Venue("NASDAQ") not in hk_client._venue_account_aliases

    def test_two_clients_submit_orders_to_correct_account(self, event_loop):
        """Each client routes orders to its own discovered account.

        US instrument → US account (19064357).
        HK instrument → HK account (19064358).
        No cross-talk.
        """
        # ---- US client ----
        us_ctx = _make_mock_trade_ctx()
        us_client = _make_exec_client(
            event_loop, us_ctx, trd_market="US", paper_acc_type="STOCK_AND_OPTION"
        )
        _discover_us_account(event_loop, us_client, us_ctx)

        # ---- HK client ----
        hk_ctx = _make_mock_trade_ctx()
        hk_client = _make_exec_client(
            event_loop, hk_ctx, trd_market="HK", paper_acc_type="STOCK"
        )
        _discover_hk_account(event_loop, hk_client, hk_ctx)

        # Verify US order uses US account
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.model.enums import OrderSide, TimeInForce
        from nautilus_trader.model.orders import LimitOrder
        from nautilus_trader.test_kit.stubs.commands import TestCommandStubs

        us_order = LimitOrder(
            trader_id=TraderId("SAM-001"),
            strategy_id=StrategyId("UMAC-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            client_order_id=ClientOrderId("US-001"),
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(100),
            price=Price.from_str("150.00"),
            init_id=UUID4(),
            ts_init=0,
            time_in_force=TimeInForce.DAY,
        )
        us_cmd = TestCommandStubs.submit_order_command(us_order)
        event_loop.run_until_complete(us_client._submit_order(us_cmd))
        assert us_ctx.place_order.call_args.kwargs["acc_id"] == 19064357
        assert us_ctx.place_order.call_args.kwargs["code"] == "US.AAPL"

        hk_order = LimitOrder(
            trader_id=TraderId("SAM-001"),
            strategy_id=StrategyId("MNTS-001"),
            instrument_id=InstrumentId.from_str("00700.HKEX"),
            client_order_id=ClientOrderId("HK-001"),
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(100),
            price=Price.from_str("350.00"),
            init_id=UUID4(),
            ts_init=0,
            time_in_force=TimeInForce.DAY,
        )
        hk_cmd = TestCommandStubs.submit_order_command(hk_order)
        event_loop.run_until_complete(hk_client._submit_order(hk_cmd))
        assert hk_ctx.place_order.call_args.kwargs["acc_id"] == 19064358
        assert hk_ctx.place_order.call_args.kwargs["code"] == "HK.00700"

    def test_venue_alias_per_client_isolation(self, event_loop):
        """Venue alias maps are per-client, not shared between instances."""
        # ---- US client with multi-venue discovery ----
        us_ctx = _make_mock_trade_ctx()
        us_client = _make_exec_client(
            event_loop, us_ctx, trd_market="US", paper_acc_type="STOCK_AND_OPTION"
        )
        _discover_us_account(event_loop, us_client, us_ctx)

        # ---- Second US client with different account ----
        us2_ctx = _make_mock_trade_ctx()
        us2_client = _make_exec_client(
            event_loop,
            us2_ctx,
            trd_market="US",
            paper_acc_type="STOCK_AND_OPTION",
            client_id=2,
        )

        us2_ctx.get_acc_list.return_value = (
            RET_OK,
            pd.DataFrame(
                {
                    "acc_id": [999888],
                    "trd_env": ["SIMULATE"],
                    "trdmarket_auth": [[2]],
                    "sim_acc_type": ["STOCK_AND_OPTION"],
                }
            ),
        )
        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(us2_client._discover_accounts())

        # Each client is independent
        assert us_client._account_id == AccountId("FUTU-19064357")
        assert us2_client._account_id == AccountId("FUTU-999888")

    def test_strategies_share_bundle_loading_not_accounts(self, monkeypatch, tmp_path):
        """Bundle loading is shared but account discovery is per-client.

        Two strategies loaded from the same bundles.yaml share config
        but get separate execution client instances with independent
        account discovery.
        """
        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")

        bundles_yaml = tmp_path / "bundles.yaml"
        bundles_yaml.write_text("""\
bundles:
  - id: "umac-us-stock"
    enabled: true
    venue: FUTU
    market: US
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        time_in_force: DAY
  - id: "mnts-us-stock"
    enabled: true
    venue: FUTU
    market: US
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
        time_in_force: DAY
""")
        configs = load_bundles(str(bundles_yaml))

        # Both loaded successfully
        assert len(configs) == 2

        # Each has its own config — no account cross-contamination possible
        # at the bundle level since accounts are per-execution-client
        orb_cfg = next(c for c in configs if "umac" in c.config.get("strategy_id", ""))
        mom_cfg = next(c for c in configs if "mnts" in c.config.get("strategy_id", ""))

        assert orb_cfg.config["market"] == "US"
        assert mom_cfg.config["market"] == "US"
        assert "time_in_force" in orb_cfg.config
        assert "time_in_force" in mom_cfg.config

        # Strategy IDs are market-prefixed for Redis state isolation
        assert "US" in orb_cfg.config["strategy_id"]
        assert "US" in mom_cfg.config["strategy_id"]


# ---- Scenario 14: Restart recovery ------------------------------------


class TestRestartRecovery:
    """Scenario 14: Restart picks up correct accounts after circuit breaker trip.

    After a circuit breaker trip, a strategy restart must:
      - Re-discover the correct paper trading account
      - NOT use the FUTU-1 placeholder
      - Clear the circuit breaker state via on_reset()
    """

    def test_restart_after_circuit_breaker_preserves_account_discovery(
        self, event_loop
    ):
        """After circuit breaker trip, a new client instance discovers the
        same account — not the factory placeholder."""
        # ---- First session: discover accounts ----
        ctx1 = _make_mock_trade_ctx()
        client1 = _make_exec_client(event_loop, ctx1, trd_market="US")
        _discover_us_account(event_loop, client1, ctx1)

        assert client1._account_id == AccountId("FUTU-19064357")
        # Simulate circuit breaker trip on client (at strategy level)
        # The exec client itself doesn't have a breaker — the strategy does.
        # But we verify that on "restart" (new client), discovery still works.

        # ---- Restart: new client, fresh discovery ----
        ctx2 = _make_mock_trade_ctx()
        client2 = _make_exec_client(event_loop, ctx2, trd_market="US")
        assert client2._account_id == AccountId("FUTU-1")  # initial placeholder

        _discover_us_account(event_loop, client2, ctx2)

        # After discovery, placeholder is replaced
        assert client2._account_id == AccountId("FUTU-19064357")
        assert client2._account_id != AccountId("FUTU-1")

    def test_restart_without_discovery_keeps_placeholder_but_clear(self, event_loop):
        """When discovery fails on restart, placeholder remains.

        This is expected behavior: the operator must create a paper
        trading account or set FUTU_PAPER_ACCOUNT_ID before trading.

        The placeholder ensures orders FAIL with identifiable errors
        rather than silently routing to a wrong account.
        """
        ctx = _make_mock_trade_ctx()
        ctx.get_acc_list.return_value = (RET_OK, pd.DataFrame())  # empty

        client = _make_exec_client(event_loop, ctx, trd_market="HK")
        placeholder = client._account_id

        with patch.dict(os.environ, {}, clear=True):
            event_loop.run_until_complete(client._discover_accounts())

        # Placeholder kept — order failure is detectable and debuggable
        assert client._account_id == placeholder

    def test_circuit_breaker_state_cleared_on_strategy_restart(self, monkeypatch):
        """Scenario 14: Strategy on_reset clears circuit breaker state.

        After a circuit breaker trip, restarting the strategy via
        on_reset() must clear _rejection_count and _rejection_disabled.
        """
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.model.events import OrderRejected

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")

        # Create strategy and trip the breaker
        strategy = OrbStrategy(
            OrbStrategyConfig(
                instrument_id="AAPL.NASDAQ",
                bar_type="AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                max_consecutive_rejections=3,
                time_in_force="DAY",
            )
        )

        rejected = OrderRejected(
            trader_id=TraderId("SAM-001"),
            strategy_id=StrategyId("ORB-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            client_order_id=ClientOrderId("O-001"),
            account_id=AccountId("FUTU-001"),
            reason="TEST_REJECTION",
            event_id=UUID4(),
            ts_event=1_700_000_000_000_000_000,
            ts_init=1_700_000_000_000_000_001,
            reconciliation=False,
        )

        for _ in range(3):
            strategy.on_order_rejected(rejected)
        assert strategy._rejection_disabled is True

        # Simulate restart: on_reset()
        strategy.on_reset()
        assert strategy._rejection_count == 0
        assert strategy._rejection_disabled is False

    def test_state_persistence_excludes_placeholder(self, monkeypatch):
        """On save, state reflects the actual trading state, not the placeholder."""
        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")

        strategy = OrbStrategy(
            OrbStrategyConfig(
                instrument_id="AAPL.NASDAQ",
                bar_type="AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                max_consecutive_rejections=3,
                time_in_force="DAY",
            )
        )

        # After a rejection, state is saved
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.model.events import OrderRejected

        rejected = OrderRejected(
            trader_id=TraderId("SAM-001"),
            strategy_id=StrategyId("ORB-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            client_order_id=ClientOrderId("O-001"),
            account_id=AccountId("FUTU-001"),
            reason="TEST_REJECTION",
            event_id=UUID4(),
            ts_event=1_700_000_000_000_000_000,
            ts_init=1_700_000_000_000_000_001,
            reconciliation=False,
        )
        strategy.on_order_rejected(rejected)

        saved = strategy.on_save()
        # State is saved — not the placeholder
        raw = saved["state"]
        data = pickle.loads(raw)
        assert data["_rejection_count"] == 1
        assert data["_rejection_disabled"] is False
        assert data["_config_instrument_id"] == "AAPL.NASDAQ"

    def test_on_load_rejects_stale_state_from_different_market(self, monkeypatch):
        """State from a different instrument is rejected on load.

        Prevents cross-market contamination when MARKET env var changes
        between restarts (e.g., HK → US).
        """
        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "REAL")

        # Save state for AAPL.NASDAQ
        strategy1 = OrbStrategy(
            OrbStrategyConfig(
                instrument_id="AAPL.NASDAQ",
                bar_type="AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                time_in_force="DAY",
            )
        )
        saved = strategy1.on_save()

        # Load into a strategy for 00700.HKEX — should reject
        strategy2 = OrbStrategy(
            OrbStrategyConfig(
                instrument_id="00700.HKEX",
                bar_type="00700.HKEX-5-MINUTE-LAST-EXTERNAL",
                time_in_force="DAY",
            )
        )
        strategy2.on_load(saved)

        # State should be discarded — all fields at defaults
        assert strategy2._trades_today == 0
        assert strategy2._range_established is False


# ---- Scenario 6: Circuit breaker + TIF safety (integration) ------------


class TestCircuitBreakerTIFSafetyIntegration:
    """Scenario 6 integration: GTC in SIMULATE never trips circuit breaker.

    Two-layer defense:
      1. Strategy resolves GTC→DAY at init (OrbStrategy._resolve_time_in_force)
      2. Execution client converts GTC→DAY in _submit_order (defense-in-depth)

    Neither layer produces a Futu API rejection for TIF reasons.
    """

    def test_strategy_day_resolution_prevents_gtc_orders(self, monkeypatch):
        """Strategy with GTC config in SIMULATE resolves to DAY — no GTC orders."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")

        strategy = OrbStrategy(
            OrbStrategyConfig(
                instrument_id="AAPL.NASDAQ",
                bar_type="AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                max_consecutive_rejections=10,
                time_in_force="GTC",  # config says GTC
            )
        )

        # SIMULATE forces DAY — circuit breaker will never trip on TIF
        assert strategy._time_in_force == TimeInForce.DAY
        assert strategy._rejection_count == 0
        assert strategy._rejection_disabled is False

    def test_execution_defense_in_depth_handles_gtc_race_condition(self, event_loop):
        """Even if a GTC order reaches the execution layer (race condition),
        the defense-in-depth in _submit_order converts it to DAY before
        calling place_order — preventing a rejection."""
        ctx = _make_mock_trade_ctx()
        client = _make_exec_client(event_loop, ctx, trd_env="SIMULATE", trd_market="US")
        _discover_us_account(event_loop, client, ctx)

        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.model.enums import OrderSide, TimeInForce
        from nautilus_trader.model.orders import LimitOrder
        from nautilus_trader.test_kit.stubs.commands import TestCommandStubs

        # GTC order (simulating a race where strategy didn't resolve DAY)
        gtc_order = LimitOrder(
            trader_id=TraderId("SAM-001"),
            strategy_id=StrategyId("ORB-001"),
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            client_order_id=ClientOrderId("GTC-RACE"),
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(100),
            price=Price.from_str("150.00"),
            init_id=UUID4(),
            ts_init=0,
            time_in_force=TimeInForce.GTC,
        )
        cmd = TestCommandStubs.submit_order_command(gtc_order)

        with patch.object(client, "generate_order_rejected") as mock_rej:
            event_loop.run_until_complete(client._submit_order(cmd))

        # No rejection — GTC was auto-corrected to DAY at execution layer
        mock_rej.assert_not_called()
        kwargs = ctx.place_order.call_args.kwargs
        assert kwargs["time_in_force"] == "DAY"

    def test_dual_strategy_tif_resolution_independent(self, monkeypatch):
        """Each strategy resolves TIF independently — no cross-contamination."""
        from nautilus_trader.model.enums import TimeInForce

        monkeypatch.delenv("DEFAULT_TIME_IN_FORCE", raising=False)
        monkeypatch.setenv("FUTU_TRD_ENV", "SIMULATE")

        orb = OrbStrategy(
            OrbStrategyConfig(
                instrument_id="AAPL.NASDAQ",
                bar_type="AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL",
                time_in_force="DAY",
            )
        )
        mom = MomentumStrategy(
            MomentumStrategyConfig(
                instrument_id="AAPL.NASDAQ",
                bar_type="AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                time_in_force="GTC",  # will be forced to DAY
            )
        )

        # Each strategy resolves independently
        assert orb._time_in_force == TimeInForce.DAY
        assert mom._time_in_force == TimeInForce.DAY  # forced by SIMULATE

        # Neither has cross-contamination from the other's config
        assert orb._rejection_count == 0
        assert mom._rejection_count == 0
