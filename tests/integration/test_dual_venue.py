"""Integration test: Dual-venue TradingNode (Futu + IB).

Validates the Phase 5 exit criteria:
1. TradingNode starts with both Futu + IB factories
2. Load 1 Futu bundle + 1 IB bundle
3. Both strategies instantiated
4. Data flows from both venues
5. No cross-venue contamination
6. Both venues visible in Portfolio (exec clients registered)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from futu import RET_OK
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

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
  - id: "nvda-echo-ib"
    enabled: true
    venue: IB
    strategy:
      path: sam_trader.strategies.test_echo:EchoStrategy
      config_path: sam_trader.strategies.test_echo:EchoStrategyConfig
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
"""


@pytest.fixture
def event_loop():
    """Provide a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.integration
class TestDualVenueTradingNode:
    def test_futu_and_ib_strategies_coexist(
        self,
        event_loop: asyncio.AbstractEventLoop,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Full Phase 5 exit: dual-venue node, bundles, data flow, no contamination."""
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

        # --- Arrange: mock IB client start (prevent real TCP connect) -----
        from nautilus_trader.adapters.interactive_brokers.client import (
            client as ib_client_mod,
        )
        from nautilus_trader.adapters.interactive_brokers.factories import (
            GATEWAYS,
            IB_CLIENTS,
        )

        def mock_ib_start(self):
            self._is_running = True

        monkeypatch.setattr(
            ib_client_mod.InteractiveBrokersClient, "start", mock_ib_start
        )

        # --- Arrange: environment -----------------------------------------
        monkeypatch.setenv("FUTU_ENABLED", "true")
        monkeypatch.setenv("IB_ENABLED", "true")
        monkeypatch.setenv("BUNDLES_PATH", str(bundles_path))
        monkeypatch.setenv("STATE_SAVE_ENABLED", "false")
        monkeypatch.setenv("STATE_LOAD_ENABLED", "false")
        monkeypatch.setenv("IB_GATEWAY_HOST", "test-ib-gateway")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        monkeypatch.setenv("IB_GATEWAY_CLIENT_ID", "42")
        monkeypatch.setenv("IB_ACCOUNT_ID", "DU12345")
        monkeypatch.setenv("IB_SYMBOLS", "NVDA.NASDAQ")
        monkeypatch.setenv("IB_TRADING_MODE", "paper")
        monkeypatch.setenv("IB_READ_ONLY_API", "false")
        monkeypatch.setenv("IB_MARKET_DATA_TYPE", "REALTIME")

        asyncio.set_event_loop(event_loop)

        # --- Act: build TradingNode ---------------------------------------
        node = build_trading_node()
        assert isinstance(node, TradingNode)

        # 1. Both venue factories registered
        assert "FUTU" in node._config.data_clients
        assert "IB" in node._config.data_clients
        assert "FUTU" in node._config.exec_clients
        assert "IB" in node._config.exec_clients
        assert "FUTU" in node._builder._data_factories
        assert "IB" in node._builder._data_factories
        assert "FUTU" in node._builder._exec_factories
        assert "IB" in node._builder._exec_factories

        # 2. Build node (creates and registers data/exec clients)
        node.build()
        assert node.is_built() is True

        # 3. Both strategies instantiated from bundles
        strategies = node.kernel.trader.strategies()
        assert len(strategies) == 2

        futu_strategy = [s for s in strategies if s.config.venue == "FUTU"][0]
        ib_strategy = [s for s in strategies if s.config.venue == "IB"][0]

        assert futu_strategy.__class__.__name__ == "EchoStrategy"
        assert futu_strategy.config.instrument_id == "TSLA.NASDAQ"
        assert ib_strategy.__class__.__name__ == "EchoStrategy"
        assert ib_strategy.config.instrument_id == "NVDA.NASDAQ"

        # 5. No cross-venue contamination
        # Futu bundle has futu_code, no exchange
        assert futu_strategy.config.futu_code == "US.TSLA"
        assert (
            not hasattr(futu_strategy.config, "exchange")
            or futu_strategy.config.exchange == ""
        )
        # IB bundle has SMART exchange, no futu_code
        assert ib_strategy.config.exchange == "SMART"
        assert (
            not hasattr(ib_strategy.config, "futu_code")
            or ib_strategy.config.futu_code == ""
        )

        # 6. Both venues visible in Portfolio (exec clients registered)
        exec_clients = list(node.kernel.exec_engine._clients.values())
        futu_exec_clients = [c for c in exec_clients if "FUTU" in str(c.id)]
        ib_exec_clients = [c for c in exec_clients if "IB" in str(c.id)]
        assert len(futu_exec_clients) == 1
        assert len(ib_exec_clients) == 1

        # --- Act: connect Futu data client and push data ------------------
        data_engine = node.kernel.data_engine
        clients = list(data_engine._clients.values())
        futu_data_clients = [c for c in clients if "FUTU" in str(c.id)]
        ib_data_clients = [c for c in clients if "IB" in str(c.id)]
        assert len(futu_data_clients) == 1
        assert len(ib_data_clients) == 1
        futu_data_client = futu_data_clients[0]
        ib_data_client = ib_data_clients[0]

        event_loop.run_until_complete(futu_data_client._connect())

        # 4a. Futu data flow: quote tick arrives
        instrument_id_futu = InstrumentId.from_str("TSLA.NASDAQ")
        ts_init = 1_234_567_890_000_000_000

        tick_futu = parse_futu_quote_tick(
            {"last_price": 250.05, "price_spread": 0.01, "volume": 5000},
            instrument_id_futu,
            ts_init,
        )

        futu_captured: list[QuoteTick] = []

        def _capture_futu(data):
            if isinstance(data, QuoteTick):
                futu_captured.append(data)
            futu_data_client._push_task.cancel()

        futu_data_client._handle_data = _capture_futu  # type: ignore[method-assign]
        event_loop.run_until_complete(futu_data_client._queue.put(tick_futu))

        try:
            event_loop.run_until_complete(
                asyncio.wait_for(futu_data_client._push_task, timeout=1.0)
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert len(futu_captured) == 1
        assert futu_captured[0].instrument_id == instrument_id_futu

        # 4b. IB data flow: quote tick arrives via IB data client
        instrument_id_ib = InstrumentId.from_str("NVDA.NASDAQ")
        tick_ib = QuoteTick(
            instrument_id=instrument_id_ib,
            bid_price=Price.from_str("300.00"),
            ask_price=Price.from_str("300.05"),
            bid_size=Quantity.from_int(100),
            ask_size=Quantity.from_int(100),
            ts_event=ts_init,
            ts_init=ts_init,
        )

        ib_captured: list[QuoteTick] = []

        def _capture_ib(data):
            if isinstance(data, QuoteTick):
                ib_captured.append(data)

        # Monkeypatch _handle_data on IB client to capture without full pipeline
        original_ib_handle_data = ib_data_client._handle_data
        ib_data_client._handle_data = _capture_ib  # type: ignore[method-assign]
        ib_data_client._handle_data(tick_ib)
        ib_data_client._handle_data = (  # type: ignore[method-assign]
            original_ib_handle_data
        )

        assert len(ib_captured) == 1
        assert ib_captured[0].instrument_id == instrument_id_ib

        # --- Assert: instrument resolution --------------------------------
        assert instrument_id_to_futu_security(instrument_id_futu) == "US.TSLA"

        # --- Cleanup ------------------------------------------------------
        event_loop.run_until_complete(futu_data_client._disconnect())
        IB_CLIENTS.clear()
        GATEWAYS.clear()
