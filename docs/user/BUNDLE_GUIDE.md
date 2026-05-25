# SAM Trader V3 — Bundle Guide

> **Audience:** Strategy operators and quants who configure trading bundles.  
> **Scope:** YAML schema, symbology rules, versioned bundle examples, and validation workflow.

---

## Table of Contents

1. [What Is a Bundle?](#1-what-is-a-bundle)
2. [Schema Reference](#2-schema-reference)
3. [Symbology](#3-symbology)
4. [Versioned Bundle Examples](#4-versioned-bundle-examples)
5. [Validation Workflow](#5-validation-workflow)
6. [Reference: `sam validate-bundles`](#6-reference-sam-validate-bundles)

---

## 1. What Is a Bundle?

A **bundle** is the atomic unit of strategy deployment in SAM Trader.  
Each bundle maps exactly one **instrument** + **strategy class** + **venue** + **risk parameters** to a running `Strategy` instance inside the `TradingNode`.

Bundles are declared in `config/bundles.yaml` and loaded at node build time via `BundleLoader`.  
No code changes are required to add, remove, or adjust strategies — only YAML edits.

```yaml
bundles:
  - id: "tsla-orb-15m-futu"
    enabled: true
    venue: FUTU
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 15
        trade_size: 5
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
    risk:
      max_position: 500
      max_daily_loss: 1000
```

---

## 2. Schema Reference

### 2.1 Top-Level Structure

```yaml
bundles:
  - id: <string>               # Unique bundle identifier (required)
    enabled: <bool>            # true = loaded, false = skipped (required)
    venue: <FUTU | IB>         # Broker venue (required)
    family: <string>           # Strategy family name (optional, for grouping)
    version: <string>          # SemVer string (optional, for version tracking)
    variant: <string>          # Descriptive variant tag (optional)
    strategy:
      path: <dotted.path:Class>  # Importable strategy class (required)
      config: <dict>           # Strategy-specific parameters (required)
    bracket: <dict>            # Bracket order parameters (optional)
    risk: <dict>               # Risk limit parameters (optional)
```

### 2.2 Field Details

#### `id`
- **Type:** `string`
- **Required:** Yes
- **Rules:** Unique across all bundles. Use kebab-case. Include venue suffix for clarity.
- **Examples:** `tsla-orb-15m-futu`, `nvda-momentum-5m-ib`, `orb-aggressive-tsla`

#### `enabled`
- **Type:** `boolean`
- **Required:** Yes
- **Purpose:** Disabled bundles are parsed but not instantiated. Use for temporary pauses or staging new configs.

#### `venue`
- **Type:** `string`
- **Required:** Yes
- **Allowed values:** `FUTU`, `IB`
- **Purpose:** Routes data and execution to the correct broker adapter.

#### `family`, `version`, `variant` *(optional metadata)*

| Field | Type | Purpose |
|-------|------|---------|
| `family` | `string` | Groups related bundles (e.g., `ORB_aggressive`, `ORB_bearish`) |
| `version` | `string` | SemVer tracking for backtest-to-live parity (e.g., `1.0.0`) |
| `variant` | `string` | Human-readable tag (e.g., `aggressive`, `bearish`, `conservative`) |

These fields are **not consumed by the engine** — they are used by:
- `sam bundle-diff` (detects version bumps)
- `sam snapshot` (captures active bundle metadata)
- Operational dashboards

#### `strategy.path`
- **Type:** `string`
- **Format:** `module.submodule:ClassName`
- **Required:** Yes
- **Examples:**
  - `sam_trader.strategies.orb:OrbStrategy`
  - `sam_trader.strategies.momentum:MomentumStrategy`

#### `strategy.config`
- **Type:** `dict`
- **Required:** Yes
- **Content:** Strategy-specific frozen `StrategyConfig` dataclass fields.
- **Common keys:**
  - `instrument_id` — Nautilus `InstrumentId` string
  - `bar_type` — Nautilus `BarType` string (`{SYMBOL}.{VENUE}-{MINUTES}-MINUTE-LAST-{INTERNAL|EXTERNAL}`)
  - `trade_size` — Position size in shares/contracts
  - `entry_order_type` — `MARKET`, `LIMIT`, `STOP_MARKET`
  - `allowed_directions` — `["LONG"]`, `["SHORT"]`, or `["LONG", "SHORT"]`

#### `bracket`
- **Type:** `dict`
- **Optional:** Yes (omit if strategy manages its own stops)
- **Fields:**
  - `stop_loss_ticks: <int>` — Stop-loss distance in price ticks
  - `take_profit_ticks: <int>` — Take-profit distance in price ticks

#### `risk`
- **Type:** `dict`
- **Optional:** Yes
- **Fields:**
  - `max_position: <int>` — Max absolute position in shares/contracts
  - `max_daily_loss: <int|float>` — Max realized loss per day in base currency

### 2.3 Full Schema Example (All Fields)

```yaml
bundles:
  - id: "aapl-orb-conservative-v2"
    enabled: true
    venue: FUTU
    family: ORB_conservative
    version: "2.1.0"
    variant: conservative
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "AAPL.NASDAQ"
        bar_type: "AAPL.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 15
        trade_size: 5
        entry_order_type: "LIMIT"
        allowed_directions: ["LONG"]
    bracket:
      stop_loss_ticks: 15
      take_profit_ticks: 40
    risk:
      max_position: 200
      max_daily_loss: 500
```

---

## 3. Symbology

### 3.1 Instrument ID Format

Nautilus `InstrumentId` uses the format:

```
{SYMBOL}.{EXCHANGE}
```

| Market | Symbol | Exchange | Full ID |
|--------|--------|----------|---------|
| US Equity | TSLA | NASDAQ | `TSLA.NASDAQ` |
| US Equity | AAPL | NASDAQ | `AAPL.NASDAQ` |
| US Equity | NVDA | NASDAQ | `NVDA.NASDAQ` |
| HK Equity | Tencent | HKEX | `00700.HKEX` |
| HK Equity | Alibaba | HKEX | `09988.HKEX` |

### 3.2 Bar Type Format

```
{INSTRUMENT_ID}-{MINUTES}-MINUTE-LAST-{SOURCE}
```

| Component | Value | Meaning |
|-----------|-------|---------|
| `INSTRUMENT_ID` | `TSLA.NASDAQ` | Instrument |
| `MINUTES` | `5`, `15`, `60` | Bar aggregation period |
| `LAST` | Fixed | Use last-trade price |
| `SOURCE` | `EXTERNAL` | Futu broker data |
| `SOURCE` | `INTERNAL` | IBKR broker data |

**Examples:**
- `TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL` — Futu 15-minute bars for TSLA
- `AAPL.NASDAQ-5-MINUTE-LAST-INTERNAL` — IBKR 5-minute bars for AAPL
- `00700.HKEX-5-MINUTE-LAST-EXTERNAL` — Futu 5-minute bars for Tencent

### 3.3 Venue Routing

| Venue | Data Client | Execution Client | Bar Source |
|-------|-------------|------------------|------------|
| `FUTU` | `FutuLiveDataClient` | `FutuLiveExecutionClient` | `EXTERNAL` |
| `IB` | `InteractiveBrokersLiveDataClient` | `InteractiveBrokersLiveExecClient` | `INTERNAL` |

> **Important:** Using `INTERNAL` with `venue: FUTU` or `EXTERNAL` with `venue: IB` will cause subscription failures.

---

## 4. Versioned Bundle Examples

### 4.1 ORB Aggressive — v1.0

Aggressive opening-range breakout with tight stops and larger size.

```yaml
  - id: "orb-aggressive-tsla"
    enabled: false
    venue: FUTU
    family: ORB_aggressive
    version: "1.0.0"
    variant: aggressive
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 5
        trade_size: 20
        entry_order_type: "MARKET"
    bracket:
      stop_loss_ticks: 5
      take_profit_ticks: 15
    risk:
      max_position: 1000
      max_daily_loss: 2000
```

### 4.2 ORB Bearish — v1.3

Bearish-biased ORB with wider stops and short-only direction.

```yaml
  - id: "orb-bearish-tsla"
    enabled: false
    venue: FUTU
    family: ORB_bearish
    version: "1.3.0"
    variant: bearish
    strategy:
      path: sam_trader.strategies.orb:OrbStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL"
        first_candle_minutes: 15
        trade_size: 10
        entry_order_type: "LIMIT"
        allowed_directions: ["SHORT"]
    bracket:
      stop_loss_ticks: 12
      take_profit_ticks: 25
    risk:
      max_position: 500
      max_daily_loss: 1000
```

### 4.3 Momentum Long-Only — v2.0 (IB)

Momentum strategy on NVDA via IBKR with conservative sizing.

```yaml
  - id: "nvda-momentum-long-ib"
    enabled: false
    venue: IB
    family: Momentum_long
    version: "2.0.0"
    variant: long_only
    strategy:
      path: sam_trader.strategies.momentum:MomentumStrategy
      config:
        instrument_id: "NVDA.NASDAQ"
        bar_type: "NVDA.NASDAQ-5-MINUTE-LAST-INTERNAL"
        window: 20
        session_start: "09:30:00"
        session_end: "16:00:00"
        trade_size: 50
        entry_order_type: "LIMIT"
        allowed_directions: ["LONG"]
    bracket:
      stop_loss_ticks: 15
      take_profit_ticks: 45
    risk:
      max_position: 200
      max_daily_loss: 500
```

### 4.4 Version Bump Workflow

When you iterate a bundle strategy, update the `version` field and run:

```bash
# Show pending changes vs last snapshot
docker exec sam-services sam bundle-diff

# Expected output for version bump:
#   VERSION BUMPS
#   ------------------------------------
#     ~ orb-aggressive-tsla  1.0.0 → 1.1.0

# Validate and apply
docker exec sam-services sam validate-bundles
docker exec sam-services sam apply
```

---

## 5. Validation Workflow

### 5.1 Local Validation (Before Apply)

Always validate `bundles.yaml` before applying:

```bash
docker exec sam-services sam validate-bundles
```

This checks:
1. **Schema** — all required fields present, types correct
2. **Strategy class** — `strategy.path` is importable
3. **Backtest gate** — strategy can be instantiated and run a 1-bar smoke backtest

Skip the backtest gate for faster feedback during editing:

```bash
docker exec sam-services sam validate-bundles --no-backtest
```

### 5.2 Validation Output

```json
{
  "command": "validate-bundles",
  "summary": "2 passed, 1 failed",
  "all_passed": false,
  "bundles": [
    {
      "id": "tsla-orb-15m-futu",
      "passed": true,
      "errors": [],
      "warnings": []
    },
    {
      "id": "nvda-momentum-5m-ib",
      "passed": false,
      "errors": [
        "InstrumentId 'NVDA.NASDAQ' not found in catalog"
      ],
      "warnings": []
    }
  ]
}
```

### 5.3 Dry-Run Apply

Preview what `sam apply` would do without making changes:

```bash
docker exec sam-services sam apply --dry-run
```

### 5.4 Check Deployment Window

Bundle changes should only be applied during the maintenance window:

```bash
docker exec sam-services sam deploy-window
```

Default window: **05:00–08:00 HKT** (configurable via `DEPLOY_WINDOW` env var).

---

## 6. Reference: `sam validate-bundles`

```bash
docker exec sam-services sam validate-bundles [options]
```

| Option | Description |
|--------|-------------|
| `--path <path>` | Validate a specific file (default: `config/bundles.yaml`) |
| `--no-backtest` | Skip the backtest smoke test (faster) |
| `--json` | Output structured JSON |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All bundles passed |
| `1` | One or more bundles failed |

---

*Last updated: 2026-05-25*  
*See also: [`DEPLOY_GUIDE.md`](./DEPLOY_GUIDE.md), [`OPERATOR_GUIDE.md`](./OPERATOR_GUIDE.md)*
