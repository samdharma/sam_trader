# Sandbox Fixes — 29 May 2026

**Context:** Pre-US-session prep (09:30 ET). FUTU paper trading, market=US. trd-env=SIMULATE.

## Applied

| # | What | File | Detail |
|---|------|------|--------|
| 1 | `ib_enabled: false` for US | `config/market_config.yaml` | Was `true`; IB is disabled for this session. Committed locally (`fc8acc1`), NOT pushed — origin is 5 commits ahead. `.env` already has `IB_ENABLED=false` so effective config is correct regardless. |
| 2 | Docker images updated | — | `ghcr.io/gnzsnz/ib-gateway:stable` pulled to 28-May build. `sam-trader`, `sam-futu-opend`, `sam-services` rebuilt locally 29-May 16:14 HKT. All public images (`postgres:16-alpine`, `redis:7-alpine`) at latest. |

## Deliberately NOT Pulled

**5 upstream commits** on `origin/master` (ahead of local `b9f5e49`):

```
c822125 fix(backtest): _extract_result_stats
0d709f6 fix(backtest): dashboard POST /run
7059b58 wip: account discovery + multi-market config      ← ⚠️ BREAKING
6bfabf3 docs: Phase 12.1 EXIT gate
5eae396 feat(backtest): integration tests
```

**Risk:** `7059b58` refactors Futu account discovery — removes `FUTU_ACCOUNT_ID` fallback for paper trading, requires new `FUTU_PAPER_ACCOUNT_ID` env var and `futu_paper_acc_type` in `market_config.yaml`. Pulling without those config additions would break paper trading orders.

## To Merge After Session

1. Pull upstream → rebase local `fc8acc1` on top
2. Add `FUTU_PAPER_ACCOUNT_ID=<value>` to `.env`
3. Add `futu_paper_acc_type: "STOCK_AND_OPTION"` to `config/market_config.yaml` under US
4. Rebuild `sam-trader` + `sam-services`
5. Push
