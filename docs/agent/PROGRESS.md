
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
