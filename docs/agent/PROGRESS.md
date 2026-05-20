
## Iteration 20
- **Task**: P2: Futu config dataclasses — FutuDataClientConfig, FutuExecClientConfig
- **Task ID**: sam_trader-9z3.3.3
- **Status**: COMPLETE
- **Decisions**: Created `adapters/futu/config.py` with two frozen msgspec Struct subclasses: `FutuDataClientConfig` (inherits `LiveDataClientConfig`) and `FutuExecClientConfig` (inherits `LiveExecClientConfig`). Added fields: host, port, trd_env, trd_market, client_id with defaults host='futu-opend', port=11111, trd_env='SIMULATE', trd_market='US', client_id=1. Added `client_key` property returning (host, port, trd_env) tuple for shared context caching (matches connection.py pattern). Tests cover default values, env override (custom construction), and frozen immutability with `# type: ignore[misc]` for mypy on read-only property assignments inside `pytest.raises` blocks.
- **Files Changed**: `src/sam_trader/adapters/futu/config.py`, `tests/unit/adapters/futu/test_config.py`
- **Validation Result**: PASS (ralph_validate.sh --tier=targeted; 6/6 tests passed, black/isort/flake8/mypy all green)
- **Blockers / Notes**: None. Ready for next phase-2 ticket (sam-p2-data-client: FutuLiveDataClient).
