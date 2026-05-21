"""Integration test: Futu-only TradingNode with bundle.

Validates the full Phase 4 exit criteria:
1. TradingNode starts with Futu factories only (ib_enabled=false)
2. Load 1 Futu bundle (TSLA.NASDAQ)
3. Strategy instantiated
4. Quote ticks arrive on message bus
5. Instrument resolution works (TSLA.NASDAQ → US.TSLA → QuoteTick)
6. Bar data arrives for configured bar_type
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from futu import RET_OK
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import Bar, QuoteTick
from nautilus_trader.model.identifiers import InstrumentId

from sam_trader.adapters.futu.common import instrument_id_to_futu_security
from sam_trader.adapters.futu.parsing.market_data import parse_futu_quote_tick
from sam_trader.main import build_trading_node

BUNDLES_YAML = """\
bundles:
  - id: "tsla-echo-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config_path: sam_trader.strategies.test_echo:EchoStrategyConfig
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
"""


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.integration
class TestFutuTradingNodeWithBundle:
    def test_futu_trading_node_with_bundle(
        self,
        event_loop: asyncio.AbstractEventLoop,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Full Phase 4 exit: Futu-only node, bundle, data flow."""
        # --- Arrange: bundles file ----------------------------------------
        bundles_path = tmp_path / "bundles.yaml"
        bundles_path.write_text(BUNDLES_YAML)

        # --- Arrange: mock Futu SDK contexts ------------------------------
        quote_ctx = MagicMock()
        quote_ctx.subscribe.return_value = (RET_OK, "")
        quote_ctx.unsubscribe.return_value = (RET_OK, "")
        quote_ctx.set_handler.return_value = RET_OK
        quote_ctx.unsubscribe_all.return_value = None
        quote_ctx.request_history_kline.return_value = (RET_OK, None, None)

        trade_ctx = MagicMock()
        trade_ctx.unlock_trade.return_value = (RET_OK, "")
        trade_ctx.set_handler.return_value = RET_OK

        # Monkeypatch factory helpers so no real Futu connection is needed
        import sam_trader.adapters.futu.factories as factories_mod

        monkeypatch.setattr(
            factories_mod,
            "_get_shared_quote_context",
            lambda config: quote_ctx,
        )
        monkeypatch.setattr(
            factories_mod,
            "_get_shared_trade_context",
            lambda config: trade_ctx,
        )

        # --- Arrange: environment -----------------------------------------
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("IB_ENABLED", "false")
        monkeypatch.setenv("BUNDLES_PATH", str(bundles_path))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")

        asyncio.set_event_loop(event_loop)

        # --- Act: build TradingNode ---------------------------------------
        node = build_trading_node()
        assert isinstance(node, TradingNode)

        # 1. Futu factories only
        assert "FUTU" in node._config.data_clients
        assert "FUTU" in node._config.exec_clients
        assert "IB" not in node._config.data_clients
        assert "IB" not in node._config.exec_clients

        # 2. Strategy instantiated from bundle
        strategies = node.kernel.trader.strategies()
        assert len(strategies) == 1
        strategy = strategies[0]
        assert strategy.__class__.__name__ == "EchoStrategy"
        assert strategy.config.instrument_id == "TSLA.NASDAQ"
        assert strategy.config.bar_type == "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"

        # 3. Build node (creates and registers data/exec clients)
        node.build()
        assert node.is_built() is True

        # Locate the Futu data client
        data_engine = node.kernel.data_engine
        clients = list(data_engine._clients.values())
        futu_data_clients = [c for c in clients if "FUTU" in str(c.id)]
        assert len(futu_data_clients) == 1
        data_client = futu_data_clients[0]

        # --- Act: connect and push data -----------------------------------
        event_loop.run_until_complete(data_client._connect())

        instrument_id = InstrumentId.from_str("TSLA.NASDAQ")
        ts_init = 1_234_567_890_000_000_000

        # 4. Quote tick arrives on message bus
        tick = parse_futu_quote_tick(
            {"last_price": 250.05, "price_spread": 0.01, "volume": 5000},
            instrument_id,
            ts_init,
        )

        quote_captured: list[QuoteTick] = []

        def _capture_quote(data):
            if isinstance(data, QuoteTick):
                quote_captured.append(data)
            data_client._push_task.cancel()

        data_client._handle_data = _capture_quote  # type: ignore[method-assign]
        event_loop.run_until_complete(data_client._queue.put(tick))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(data_client._push_task, timeout=1.0)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert len(quote_captured) == 1
        assert quote_captured[0].instrument_id == instrument_id

        # Restart push loop for bar test
        data_client._push_task = event_loop.create_task(
            data_client._run_push_loop(),
            name="futu_push_loop",
        )

        # 6. Bar data arrives for configured bar_type
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.objects import Price, Quantity

        bar_type = BarType.from_str("TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL")
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str("250.00"),
            high=Price.from_str("251.00"),
            low=Price.from_str("249.50"),
            close=Price.from_str("250.75"),
            volume=Quantity.from_int(10000),
            ts_event=ts_init,
            ts_init=ts_init,
        )

        bar_captured: list[Bar] = []

        def _capture_bar(data):
            if isinstance(data, Bar):
                bar_captured.append(data)
            data_client._push_task.cancel()

        data_client._handle_data = _capture_bar  # type: ignore[method-assign]
        event_loop.run_until_complete(data_client._queue.put(bar))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(data_client._push_task, timeout=1.0)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert len(bar_captured) == 1
        assert bar_captured[0].bar_type == bar_type

        # --- Assert: instrument resolution --------------------------------
        # 5. TSLA.NASDAQ → US.TSLA
        assert instrument_id_to_futu_security(instrument_id) == "US.TSLA"

        # --- Cleanup ------------------------------------------------------
        event_loop.run_until_complete(data_client._disconnect())
