# Account Discovery Fix — 29 May 2026

## Problem

Sandbox observed error:
```
[ERROR] Portfolio: Cannot update order: no account registered for FUTU-1
```

Account discovery finds `FUTU-19064357` (paper trading account for US market) but Nautilus order routing uses `FUTU-1` (the factory placeholder), causing a mismatch between the account ID used for broker API calls and the account ID registered with the Nautilus engine.

## Root Cause

The bug is a **missing `_set_account_id()` call after account discovery updates the account ID**.

### Flow Trace

```
1. factories.py
   account_id = AccountId(f"FUTU-{config.client_id}")  # → FUTU-1

2. FutuLiveExecutionClient.__init__()
   self._set_account_id(FUTU-1)       # ← Cython ExecutionClient.account_id = FUTU-1
   self._account_id = FUTU-1           # ← Python instance variable
   self._initial_account_id = FUTU-1

3. _connect()
   await self._discover_accounts()     # → finds FUTU-19064357
   self._setup_handlers()              # → handlers get self._account_id = FUTU-19064357 ✓

4. _register_venue_account_aliases()
   self._account_id = AccountId("FUTU-19064357")  # ← Python var updated
   # BUG: _set_account_id() NOT called
   # Cython ExecutionClient.account_id is still FUTU-1 ✗
```

### Two Copies of account_id

| Layer | Variable | After init | After discovery |
|-------|----------|------------|-----------------|
| Cython `ExecutionClient` | `self.account_id` (readonly, set via `_set_account_id()`) | `FUTU-1` | `FUTU-1` ✗ |
| Python `FutuLiveExecutionClient` | `self._account_id` (instance variable) | `FUTU-1` | `FUTU-19064357` ✓ |

The Cython property `ExecutionClient.account_id` is the one used internally by Nautilus for:
- `generate_order_submitted()` — the Cython method uses `self.account_id` (no parameter for account_id in the signature)
- `generate_order_accepted()` — same
- All other order event generators — same

### Impact

1. **Orders placed to Futu** use correct account: `_resolve_account_id()` reads Python `self._account_id` → `FUTU-19064357` → Futu `place_order(acc_id=19064357)` → ✅
2. **Nautilus order events** use wrong account: `generate_order_submitted()` reads Cython `self.account_id` → `FUTU-1` → ✗
3. **Push handlers** (`TradeOrderHandler`, `TradeDealHandler`) use Python `self._account_id` → `FUTU-19064357` → reports carry correct account → but Nautilus only knows about `FUTU-1`

Result: portfolio can't reconcile orders because the account ID used in Nautilus events (`FUTU-1`) doesn't match the one in push reports (`FUTU-19064357`), and neither matches the one the portfolio expects.

## Fix

### Code Fix (execution.py)

Add `self._set_account_id(acc_id)` calls in two places:

1. `_register_venue_account_aliases()` — when the discovered account replaces the placeholder
2. `_handle_account_discovery_failure()` — when `FUTU_PAPER_ACCOUNT_ID` override is used

### Config Additions

1. `.env`: Add `FUTU_PAPER_ACCOUNT_ID=<value>` as the per-market paper trading account override
2. `config/market_config.yaml`: Already has `futu_paper_acc_type` under both US and HK markets (added by WIP commit 7059b58)

### Merge Decision on Upstream WIP (7059b58)

**Decision: ADOPT** — The WIP has already been merged into `origin/master` (commit `7059b58` → `c822125`). The refactor correctly:

- ✅ Removes `FUTU_ACCOUNT_ID` fallback for paper trading (FUTU_ACCOUNT_ID is the OpenD *login* account, not a trading account)
- ✅ Introduces `paper_acc_type` from `market_config.yaml` for per-market paper account type filtering
- ✅ Introduces `FUTU_PAPER_ACCOUNT_ID` for explicit paper trading account override
- ✅ Adds better logging with rationale (acc_id, sim_acc_type, venues, market)

The WIP is correct in design but has one bug: missing `_set_account_id()` calls. This fix completes the WIP.

### Additional Fix: NYSE Venue Mapping

`FUTU_TRD_MARKET_TO_VENUE` maps `FUTU_TRD_MARKET_US` (2) → `NASDAQ_VENUE`. US instruments may also have venue `NYSE`. While `_resolve_account_id()` correctly falls back to `self._account_id` for unmapped venues, the `_venue_account_aliases` should also include NYSE for completeness.

**Fix:** In `_register_venue_account_aliases()`, when a market code maps to NASDAQ, also add the NYSE alias (same account ID). Both NASDAQ and NYSE are the same US market on Futu (TrdMarket US).

## Verification

1. Start sam-trader with MARKET=US, FUTU_TRD_MARKET=US
2. Check logs for: `Account discovery: selected FUTU-19064357`
3. Submit a paper order → verify `acc_id=19064357` in Futu API call
4. Check Nautilus logs: no "no account registered" errors after startup
5. Verify push fill reports carry correct account ID

## Files Changed

- `src/sam_trader/adapters/futu/execution.py` — add `_set_account_id()` calls
- `.env.example` — add `FUTU_PAPER_ACCOUNT_ID` template
- `.env` — add `FUTU_PAPER_ACCOUNT_ID` value
