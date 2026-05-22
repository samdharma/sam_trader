
## Iteration 20
- **Task**: P2: Futu config dataclasses — FutuDataClientConfig, FutuExecClientConfig
- **Task ID**: sam_trader-9z3.3.3
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/config.py` with two frozen msgspec Struct subclasses: `FutuDataClientConfig` (inherits `LiveDataClientConfig`) and `FutuExecClientConfig` (inherits `LiveExecClientConfig`). Added fields: host, port, trd_env, trd_market, client_id with defaults host='futu-opend', port=11111, trd_env='SIMULATE', trd_market='US', client_id=1. Added `client_key` property returning (host, port, trd_env) tuple for shared context caching (matches connection.py pattern). Tests cover default values, env override (custom construction), and frozen immutability with `# type: ignore[misc]` for mypy on read-only property assignments inside `pytest.raises` blocks.
- **Files Changed**: `src/sam_trader/adapters/futu/config.py`, `tests/unit/adapters/futu/test_config.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 6/6 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-data-client: FutuLiveDataClient).

## Iteration 21
- **Task**: P2: FutuLiveDataClient — push-loop architecture, subscription lifecycle
- **Task ID**: sam_trader-9z3.3.5
- **Status**: COMPLETE
- **Decisions**: Existing implementation from rolled-back iteration was already present and complete. Verified all acceptance criteria against code and tests. FutuLiveDataClient subclasses LiveMarketDataClient, uses asyncio.Queue push-loop pattern from nautilus-futu, supports subscribe/unsubscribe for quote ticks, trade ticks, bars, and order book deltas. Includes reconnection subscription restoration and historical bar backfill at connect time. Config-driven via FutuDataClientConfig.
- **Files Changed**: `src/sam_trader/adapters/futu/common.py`, `src/sam_trader/adapters/futu/data.py`, `tests/unit/adapters/futu/test_data.py`, `tests/integration/adapters/futu/test_data_subscription.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 18/18 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-sub-mgr: Futu subscription quota manager).

## Iteration 22
- **Task**: P2: Futu subscription quota manager
- **Task ID**: sam_trader-9z3.3.6
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/subscription_manager.py` with `FutuSubscriptionManager` class. Tracks subscriptions per `DataType` enum (QUOTE, TRADE_TICK, ORDER_BOOK, KLINE) with configurable limits defaulting to Futu limits (100/100/50/100). Thread-safe via `asyncio.Lock` per data type. Bundle subscriptions (`is_bundle=True`) trigger eviction of oldest ad-hoc subscriptions when quota is full. Idle release via `release_idle(timeout_seconds=60)` returns evicted entries so caller can unsubscribe from Futu. WARNING logged at 80% limit, ERROR at 95% limit.
- **Files Changed**: `src/sam_trader/adapters/futu/subscription_manager.py`, `tests/unit/adapters/futu/test_subscription_manager.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 13/13 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-exit-data: market data exit gate). Subscription manager is currently standalone; wiring into `FutuLiveDataClient` will happen in a future ticket when quota enforcement is needed at runtime.

## Iteration 23
- **Task**: [EXIT] P2: Market data subscription → QuoteTick flow
- **Task ID**: sam_trader-9z3.3.7
- **Status**: COMPLETE
- **Decisions**: Wired `FutuSubscriptionManager` into `FutuLiveDataClient` via optional constructor parameter. Subscribe/unsubscribe methods now increment/decrement quota tracking before/after Futu SDK calls. Failed Futu subscriptions roll back the quota entry. Added comprehensive integration tests covering: (1) full quote tick flow (subscribe → receive → verify bid/ask/last → unsubscribe), (2) multiple concurrent instrument subscriptions, (3) subscription quota manager increment/decrement across multiple data types.
- **Files Changed**: `src/sam_trader/adapters/futu/data.py`, `tests/integration/adapters/futu/test_data_subscription.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targetted; 8/8 integration tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Phase 2 exit gate complete. Ready for Phase 3 (Futu Execution Adapter).

## Iteration 24
- **Task**: P3: Futu order parsing — OrderStatusReport, FillReport, PositionStatusReport
- **Task ID**: sam_trader-9z3.4.1
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/parsing/orders.py` with `TradeOrderHandler`, `TradeDealHandler`, and `parse_futu_position_to_report`. Maps Futu push data to NautilusTrader `OrderStatusReport`, `FillReport`, and `PositionStatusReport`. Added TIF and position side constants to `constants.py`. Handles all Futu order status codes (both string and int enum values). `TradeOrderHandler` pushes `OrderStatusReport` onto `asyncio.Queue`. `TradeDealHandler` pushes `FillReport` onto `asyncio.Queue`. Timestamp parser handles both string (`createTime`) and float (`createTimestamp`) protobuf fields. Adapted patterns from nautilus-futu parsing/orders.py (MIT).
- **Files Changed**: `src/sam_trader/adapters/futu/constants.py`, `src/sam_trader/adapters/futu/parsing/orders.py`, `tests/unit/adapters/futu/test_parsing_orders.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 15/15 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Phase 3 ticket 1 of 3 complete. Ready for sam_trader-9z3.4.2 (FutuLiveExecutionClient skeleton and order methods).

## Iteration 25
- **Task**: P3: Futu order parsing — OrderStatusReport, FillReport, PositionStatusReport
- **Task ID**: sam_trader-9z3.4.1
- **Status**: COMPLETE
- **Decisions**: Verified existing implementation from Iteration 24. Code was already committed (4f2479a) and all 15 unit tests pass. Closed beads ticket which had been left in `in_progress` state from rolled-back iteration tracking.
- **Files Changed**: `.beads/issues.jsonl`, `.beads/interactions.jsonl`, `docs/agent/PROGRESS.md`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 15/15 tests passed)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.4.2 (FutuLiveExecutionClient skeleton and order methods).

## Iteration 20
- **Task**: P2: Futu config dataclasses — FutuDataClientConfig, FutuExecClientConfig
- **Task ID**: sam_trader-9z3.3.3
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/config.py` with two frozen msgspec Struct subclasses: `FutuDataClientConfig` (inherits `LiveDataClientConfig`) and `FutuExecClientConfig` (inherits `LiveExecClientConfig`). Added fields: host, port, trd_env, trd_market, client_id with defaults host='futu-opend', port=11111, trd_env='SIMULATE', trd_market='US', client_id=1. Added `client_key` property returning (host, port, trd_env) tuple for shared context caching (matches connection.py pattern). Tests cover default values, env override (custom construction), and frozen immutability with `# type: ignore[misc]` for mypy on read-only property assignments inside `pytest.raises` blocks.
- **Files Changed**: `src/sam_trader/adapters/futu/config.py`, `tests/unit/adapters/futu/test_config.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 6/6 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-data-client: FutuLiveDataClient).

## Iteration 21
- **Task**: P2: FutuLiveDataClient — push-loop architecture, subscription lifecycle
- **Task ID**: sam_trader-9z3.3.5
- **Status**: COMPLETE
- **Decisions**: Existing implementation from rolled-back iteration was already present and complete. Verified all acceptance criteria against code and tests. FutuLiveDataClient subclasses LiveMarketDataClient, uses asyncio.Queue push-loop pattern from nautilus-futu, supports subscribe/unsubscribe for quote ticks, trade ticks, bars, and order book deltas. Includes reconnection subscription restoration and historical bar backfill at connect time. Config-driven via FutuDataClientConfig.
- **Files Changed**: `src/sam_trader/adapters/futu/common.py`, `src/sam_trader/adapters/futu/data.py`, `tests/unit/adapters/futu/test_data.py`, `tests/integration/adapters/futu/test_data_subscription.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 18/18 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-sub-mgr: Futu subscription quota manager).

## Iteration 22
- **Task**: P2: Futu subscription quota manager
- **Task ID**: sam_trader-9z3.3.6
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/subscription_manager.py` with `FutuSubscriptionManager` class. Tracks subscriptions per `DataType` enum (QUOTE, TRADE_TICK, ORDER_BOOK, KLINE) with configurable limits defaulting to Futu limits (100/100/50/100). Thread-safe via `asyncio.Lock` per data type. Bundle subscriptions (`is_bundle=True`) trigger eviction of oldest ad-hoc subscriptions when quota is full. Idle release via `release_idle(timeout_seconds=60)` returns evicted entries so caller can unsubscribe from Futu. WARNING logged at 80% limit, ERROR at 95% limit.
- **Files Changed**: `src/sam_trader/adapters/futu/subscription_manager.py`, `tests/unit/adapters/futu/test_subscription_manager.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 13/13 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-exit-data: market data exit gate). Subscription manager is currently standalone; wiring into `FutuLiveDataClient` will happen in a future ticket when quota enforcement is needed at runtime.

## Iteration 23
- **Task**: [EXIT] P2: Market data subscription → QuoteTick flow
- **Task ID**: sam_trader-9z3.3.7
- **Status**: COMPLETE
- **Decisions**: Wired `FutuSubscriptionManager` into `FutuLiveDataClient` via optional constructor parameter. Subscribe/unsubscribe methods now increment/decrement quota tracking before/after Futu SDK calls. Failed Futu subscriptions roll back the quota entry. Added comprehensive integration tests covering: (1) full quote tick flow (subscribe → receive → verify bid/ask/last → unsubscribe), (2) multiple concurrent instrument subscriptions, (3) subscription quota manager increment/decrement across multiple data types.
- **Files Changed**: `src/sam_trader/adapters/futu/data.py`, `tests/integration/adapters/futu/test_data_subscription.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targetted; 8/8 integration tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Phase 2 exit gate complete. Ready for Phase 3 (Futu Execution Adapter).

## Iteration 24
- **Task**: P3: Futu order parsing — OrderStatusReport, FillReport, PositionStatusReport
- **Task ID**: sam_trader-9z3.4.1
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/parsing/orders.py` with `TradeOrderHandler`, `TradeDealHandler`, and `parse_futu_position_to_report`. Maps Futu push data to NautilusTrader `OrderStatusReport`, `FillReport`, and `PositionStatusReport`. Added TIF and position side constants to `constants.py`. Handles all Futu order status codes (both string and int enum values). `TradeOrderHandler` pushes `OrderStatusReport` onto `asyncio.Queue`. `TradeDealHandler` pushes `FillReport` onto `asyncio.Queue`. Timestamp parser handles both string (`createTime`) and float (`createTimestamp`) protobuf fields. Adapted patterns from nautilus-futu parsing/orders.py (MIT).
- **Files Changed**: `src/sam_trader/adapters/futu/constants.py`, `src/sam_trader/adapters/futu/parsing/orders.py`, `tests/unit/adapters/futu/test_parsing_orders.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 15/15 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Phase 3 ticket 1 of 3 complete. Ready for sam_trader-9z3.4.2 (FutuLiveExecutionClient skeleton and order methods).

## Iteration 25
- **Task**: P3: Futu order parsing — OrderStatusReport, FillReport, PositionStatusReport
- **Task ID**: sam_trader-9z3.4.1
- **Status**: COMPLETE
- **Decisions**: Verified existing implementation from Iteration 24. Code was already committed (4f2479a) and all 15 unit tests pass. Closed beads ticket which had been left in `in_progress` state from rolled-back iteration tracking.
- **Files Changed**: `.beads/issues.jsonl`, `.beads/interactions.jsonl`, `docs/agent/PROGRESS.md`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 15/15 tests passed)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.4.2 (FutuLiveExecutionClient skeleton and order methods).

## Iteration 26
- **Task**: P3: FutuLiveExecutionClient skeleton, connection, trade unlock, venue aliases
- **Task ID**: sam_trader-9z3.4.2
- **Status**: COMPLETE
- **Decisions**: Existing execution.py and test_execution.py were already present from prior iteration. Added missing `unlock_pwd_md5` field to `FutuExecClientConfig` and wired `unlock_futu_trade()` call in `_connect()` when REAL env + password configured. Created `tests/integration/adapters/futu/test_execution_flow.py` with 4 integration tests covering limit order lifecycle, trade unlock in REAL env, account discovery, and fill report push loop dispatch. All acceptance criteria met.
- **Files Changed**: `src/sam_trader/adapters/futu/config.py`, `src/sam_trader/adapters/futu/execution.py`, `tests/unit/adapters/futu/test_config.py`, `tests/integration/adapters/futu/test_execution_flow.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 32/32 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.4.4 (FutuLiveExecutionClient order methods — submit, modify, cancel). Note: order methods are already implemented; next ticket may focus on refinement or push handler wiring.

## Iteration 27
- **Task**: P3: FutuLiveExecutionClient order methods — submit, modify, cancel
- **Task ID**: sam_trader-9z3.4.4
- **Status**: COMPLETE
- **Decisions**: Code was already fully implemented in prior iterations. Verified all acceptance criteria: `_submit_order` maps to `place_order` with correct parameter translation; `_modify_order` maps to `modify_order` with `ModifyOrderOp.NORMAL`; `_cancel_order` maps to `modify_order` with `ModifyOrderOp.CANCEL`; bracket orders supported via `_submit_order_list` which iterates child orders sequentially. All 22 unit tests pass including targeted tests for submit, modify, cancel, bracket, connection lifecycle, account discovery, position reconciliation, and push loop.
- **Files Changed**: No code changes required (already implemented). Updated `docs/agent/PROGRESS.md` and `.beads/` state.
- **Validation Result**: PASS (pytest tests/unit/adapters/futu/test_execution.py — 22/22 passed)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.4.5 (FutuLiveExecutionClient push handler wiring).

## Iteration 28
- **Task**: P3: FutuLiveExecutionClient push handler wiring
- **Task ID**: sam_trader-9z3.4.5
- **Status**: COMPLETE
- **Decisions**: Created `tests/unit/adapters/futu/test_execution_push.py` with end-to-end push handler wiring tests. `test_order_push` verifies TradeOrderHandler callback → OrderStatusReport → _run_push_loop → message bus. `test_fill_push` verifies TradeDealHandler callback → FillReport → _run_push_loop → message bus. Both tests mock the Futu SDK push callback by patching `TradeOrderHandlerBase.on_recv_rsp` and `TradeDealHandlerBase.on_recv_rsp` to return DataFrames, then capture the dispatched report via monkey-patched `_send_order_status_report` and `_send_fill_report`. The execution client wiring (`_setup_handlers`, `_run_push_loop`, `_handle_report`) was already implemented in prior iterations and is fully validated.
- **Files Changed**: `tests/unit/adapters/futu/test_execution_push.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 2/2 new tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.4.6 (account discovery & position reconciliation).

## Iteration 29
- **Task**: P3: FutuLiveExecutionClient account discovery and position reconciliation
- **Task ID**: sam_trader-9z3.4.6
- **Status**: COMPLETE
- **Decisions**: Verified all acceptance criteria were already implemented in prior iterations: `_discover_accounts()` auto-discovers accounts via `get_acc_list`; `_register_venue_account_aliases()` maps Futu market codes to Nautilus venues and account IDs for multi-market support; `_reconcile_positions()` fetches positions on connect and emits `PositionStatusReport` events. Integration test `test_limit_order_lifecycle` and all 22 unit tests pass. No code changes required.
- **Files Changed**: `docs/agent/PROGRESS.md`, `.beads/issues.jsonl`
- **Validation Result**: PASS (test_limit_order_lifecycle + 22 unit tests passed; ralph_validate.sh --tier=targeted passed)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.4.3 (Phase 3 exit test: full order submission → fill → OrderFilled flow).

## Iteration 30
- **Task**: [EXIT] P3: Order submission → fill → OrderFilled flow
- **Task ID**: sam_trader-9z3.4.3
- **Status**: COMPLETE
- **Decisions**: Added `test_full_order_lifecycle` integration test covering all Phase 3 exit criteria: account auto-discovery via `get_acc_list`, LIMIT order submission in SIMULATE env, OrderAccepted event generation, OrderFilled event dispatch with correct price/qty/commission verification, and OrderCancelled event generation. The test was already committed in prior work (64de349); verified it passes validation gate.
- **Files Changed**: `tests/integration/adapters/futu/test_execution_flow.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 5/5 integration tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Phase 3 exit gate complete. Ready for Phase 4 (Futu Instrument Provider & TradingNode Integration).

## Iteration 31
- **Task**: Phase 3: Futu Execution Adapter (parent ticket closure)
- **Task ID**: sam_trader-9z3.4
- **Status**: COMPLETE
- **Decisions**: Closed parent feature ticket sam_trader-9z3.4. All 6 child tasks are complete and validated: sam_trader-9z3.4.1 (order parsing), sam_trader-9z3.4.2 (skeleton/connect/unlock), sam_trader-9z3.4.4 (order methods), sam_trader-9z3.4.5 (push handler wiring), sam_trader-9z3.4.6 (account discovery & position reconciliation), sam_trader-9z3.4.3 (exit test). Phase 3 exit gate passed.
- **Files Changed**: `.beads/issues.jsonl`, `.beads/interactions.jsonl`, `docs/agent/PROGRESS.md`, `docs/reference/BUILD_PHASE_3.md`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; no modified files, gate passed)
- **Blockers / Notes**: None. Phase 3 fully complete. Ready for Phase 4 (Futu Instrument Provider & TradingNode Integration).

## Iteration 32
- **Task**: P4: Futu instrument parsing — Equity, OptionContract, FuturesContract
- **Task ID**: sam_trader-9z3.5.1
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/parsing/instruments.py` with `_parse_futu_equity`, `_parse_futu_option`, `_parse_futu_future`, and `_precision_from_spread`. Used existing `security_to_instrument_id` from market_data.py for symbology mapping. Market-based precision fallback defaults: US=2, HK=3, SH=2, SZ=2. Currency derived from venue via `_venue_to_currency`. `parse_futu_instrument` dispatcher routes by `stock_type` field. Adapted patterns from nautilus-futu parsing/instruments.py (MIT). Cython constructor testing: used `id` (not `instrument_id`) and `quote_currency` (not `currency`) for assertions.
- **Files Changed**: `src/sam_trader/adapters/futu/parsing/instruments.py`, `tests/unit/adapters/futu/test_parsing_instruments.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 17/17 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.5.2 (FutuInstrumentProvider).

## Iteration 33
- **Task**: P4: FutuInstrumentProvider — load HK+US instruments from Futu
- **Task ID**: sam_trader-9z3.5.2
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/instrument_provider.py` subclassing `InstrumentProvider`. `load_all_async` queries `get_stock_basicinfo` for US, HK, SH, SZ markets. `load_ids_async` converts Nautilus IDs to Futu codes via `instrument_id_to_futu_security` and queries specific securities. `load_from_position_data` auto-loads unknown instruments from position data. Caching via base class `self.add()`. Symbology: HK.00700 → 00700.HKEX, US.AAPL → AAPL.NASDAQ. NYSE symbols map to US.* for Futu but resolve back to NASDAQ (Futu uses single US market prefix). Used `asyncio.get_running_loop().run_in_executor` for blocking Futu SDK calls. Integration test file renamed to `test_provider_integration.py` to avoid pytest basename collision.
- **Files Changed**: `src/sam_trader/adapters/futu/instrument_provider.py`, `tests/unit/adapters/futu/test_instrument_provider.py`, `tests/integration/adapters/futu/test_provider_integration.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 21/21 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.5.3 (Futu factories: FutuLiveDataClientFactory, FutuLiveExecClientFactory).

## Iteration 34
- **Task**: P4: Bundle support for Futu venue
- **Task ID**: sam_trader-9z3.5.5
- **Status**: COMPLETE
- **Decisions**: Rewrote `bundle_loader.py` from stub to full YAML → `ImportableStrategyConfig` loader. Validates venue is `FUTU` or `IB`. Auto-derives `config_path` from `strategy.path` by appending `Config` to class name. Merges `bracket` and `risk` dicts into strategy config. For `FUTU` venue, converts `instrument_id` to `futu_code` via `instrument_id_to_futu_security`. Injects `venue` into config for strategy routing. Created `config/bundles.example.yaml` with Futu (TSLA.NASDAQ, 00700.HKEX) and IB (NVDA.NASDAQ) examples.
- **Files Changed**: `src/sam_trader/bundle_loader.py`, `config/bundles.example.yaml`, `tests/unit/test_bundle_loader.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 10/10 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.5.6 (Phase 4 exit test: Futu-only TradingNode).

## Iteration 35
- **Task**: P4: Futu factories — FutuLiveDataClientFactory, FutuLiveExecClientFactory
- **Task ID**: sam_trader-9z3.5.3
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/factories.py` with `FutuLiveDataClientFactory` and `FutuLiveExecClientFactory` subclasses of Nautilus `LiveDataClientFactory` / `LiveExecClientFactory`. Shared context helpers `_get_shared_quote_context` and `_get_shared_trade_context` delegate to existing `connection.py` cache functions. One `OpenQuoteContext` + `OpenSecTradeContext` per `(host, port, trd_env)`. Each factory creates a `FutuInstrumentProvider` using the shared quote context. Exec factory derives `AccountId` from `config.client_id`. Tests cover data client creation, exec client creation, and shared context reuse.
- **Files Changed**: `src/sam_trader/adapters/futu/factories.py`, `tests/unit/adapters/futu/test_factories.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 5/5 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.5.6 (Phase 4 exit test: Futu-only TradingNode).

## Iteration 36
- **Task**: P4: Wire Futu factories into main.py TradingNode
- **Task ID**: sam_trader-9z3.5.4
- **Status**: COMPLETE
- **Decisions**: Fixed invalid `account_id` kwarg passed to `FutuExecClientConfig` (not a defined field — `LiveExecClientConfig` doesn't expose it). Added `unlock_pwd_md5=cfg.futu_unlock_pwd_md5` wiring to pass the trade-unlock password through to the execution client config. Added two targeted unit tests: `test_futu_factories_registered` verifies config values from env vars are injected into `data_clients["FUTU"]` and `exec_clients["FUTU"]`, and that both factory classes are registered on `node._builder`; `test_futu_disabled_flag` verifies no Futu entries exist when `FUTU_ENABLED=false`.
- **Files Changed**: `src/sam_trader/main.py`, `tests/unit/test_main.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 4/4 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.5.6 (Phase 4 exit test: Futu-only TradingNode).

## Iteration 37
- **Task**: [EXIT] P4: Futu-only TradingNode — subscribe, receive data, instruments resolve
- **Task ID**: sam_trader-9z3.5.6
- **Status**: COMPLETE
- **Decisions**: Created `src/sam_trader/strategies/test_echo.py` with `EchoStrategyConfig` and `EchoStrategy` — a minimal test strategy that captures quote ticks and bars. Created `tests/integration/test_futu_node.py` with `test_futu_trading_node_with_bundle` validating all Phase 4 exit criteria: (1) TradingNode builds with Futu factories only (IB disabled), (2) Futu bundle loads with TSLA.NASDAQ, (3) strategy is instantiated via `StrategyFactory.create`, (4) quote ticks pushed through mocked Futu data client reach the message bus via `_handle_data`, (5) instrument resolution works (`TSLA.NASDAQ` → `US.TSLA`), (6) bar data arrives for configured `BarType`. Monkeypatched factory helpers to avoid real Futu connection. Added `# type: ignore[call-arg]` for mypy on `StrategyConfig` subclass with `frozen=True`.
- **Files Changed**: `src/sam_trader/strategies/test_echo.py`, `tests/integration/test_futu_node.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targetted; 1/1 integration test passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Phase 4 exit gate complete. Ready for Phase 5 (IBKR Adapter Re-integration).

## Iteration 38
- **Task**: P5: IBKR config wiring in main.py
- **Task ID**: sam_trader-9z3.6.1
- **Status**: COMPLETE
- **Decisions**: All acceptance criteria were already implemented in prior iterations (SamTraderConfig has ib_enabled flag, main.py constructs InteractiveBrokersDataClientConfig and InteractiveBrokersExecClientConfig from SamTraderConfig, IB env vars wired into main.py). Added targeted test file `tests/unit/test_main_ib_config.py` with 3 tests: `test_ib_config_loads` (verifies config values and factory registration), `test_ib_disabled_flag` (verifies no IB entries when disabled), `test_ib_read_only_no_exec_client` (verifies exec client omitted in read-only mode). Installed `nautilus-ibapi==10.45.1` in venv and added to `pyproject.toml` dependencies so IB adapter imports work in tests.
- **Files Changed**: `tests/unit/test_main_ib_config.py`, `pyproject.toml`
- **Validation Result**: PASS (ralph_validate.sh --tier=targetted; 3/3 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.6.5 (IBKR factory registration in main.py).

## Iteration 39
- **Task**: Futu OpenD: switch to debian:stable-slim + tini init
- **Task ID**: sam_trader-9z3.1.13
- **Status**: COMPLETE
- **Decisions**: Replaced ubuntu:22.04 with debian:stable-slim base image. Added tini as PID 1 init system via ENTRYPOINT ["/usr/bin/tini", "--"]. Installed only required packages (ca-certificates, curl, libssl3, tini, procps). Removed recursive chown on /bin/futu-opend binary directory. Aligned HEALTHCHECK timeout (10s) and start-period (60s) with 3-layer health check pattern from BUILD_PHASE_0.md. Documented actual compressed image size (~441MB, dominated by ~405MB Futu binary download).
- **Files Changed**: `docker/Dockerfile.futu-opend`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; docker build --platform linux/amd64 succeeded on Apple Silicon, container starts, tini PID 1 verified, FutuOpenD --help runs)
- **Blockers / Notes**: None. Ready for sam_trader-9z3.1.14 (Futu OpenD: Python XML startup replaces sed-based start.sh).
