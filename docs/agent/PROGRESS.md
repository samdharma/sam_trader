
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
