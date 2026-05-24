# CSAM Trader V2 — ORB Strategy Enhancement Specification

> **Status:** Design complete — tickets created, ready for implementation  
> **Epic:** `csam_trader-ocj`  
> **Label:** `phase-6 ORB`  
> **Derived from:** `~/Documents/ai_agent_docs/orb_strategy_evaluation.md`

---

## 1. Overview

The baseline `OrbStrategy` is solid, well-tested, and production-ready. This specification adds **contextual awareness** — enabling per-instrument tuning under different market conditions without breaking existing behavior.

All new features are **opt-in** (disabled by default). Existing bundles continue to work unchanged.

---

## 2. New Config Surface

### 2.1 OrbConfig additions

```python
class OrbConfig(StrategyConfig, frozen=True):
    # ... existing fields ...

    # Entry order type (gap remediation: v2 post-mortem 21-May)
    entry_order_type: str = "MARKET"               # "MARKET" | "LIMIT" | "STOP_MARKET"

    # Volume confirmation
    volume_ma_period: PositiveInt = 20
    volume_breakout_multiple: float = 0.0          # 0 = disabled

    # Session time guard
    session_start: str = "09:30:00"
    max_trade_time: str = "11:00:00"
    session_hard_stop: str = "16:00:00"

    # ATR-based dynamic stops
    use_atr_based_stops: bool = False
    atr_sl_multiple: float = 1.0
    atr_tp_multiple: float = 2.0

    # Prior-day & pre-market context
    prior_day_context_mode: str = "off"            # "off" | "bias" | "filter"
    premarket_lookback_minutes: PositiveInt = 30
    aggressive_long_multiplier: float = 1.5
    aggressive_short_multiplier: float = 0.5

    # ATR anomaly detection
    max_range_atr_multiple: float = 0.0            # 0 = disabled
    max_breakout_bar_atr_multiple: float = 0.0     # 0 = disabled

    # Dynamic position sizing (implemented in ticket 9z3.8.8)
    risk_per_trade_pct: float = 0.0                # 0 = use fixed trade_size
    account_risk_currency: float = 0.0
    max_trades_per_day: PositiveInt = 0            # 0 = unlimited
```

### 2.2 Runtime state additions

```python
# In OrbStrategy.__init__()
self._volume_history: list[float] = []
self._trades_today: int = 0
self._prior_day_close: float | None = None
self._premarket_high: float | None = None
self._premarket_low: float | None = None
self._premarket_volume: float = 0.0
```

---

## 3. Feature Specifications

### 3.1 Volume-Confirmed Breakouts (`csam_trader-b80`)

**Where to hook:** `_start_confirmation()`  
**Logic:**
1. Compute volume SMA over `volume_ma_period` bars from `_volume_history`.
2. If `volume_breakout_multiple > 0` and `bar.volume < sma * volume_breakout_multiple`:
   - Log: `"Breakout volume {bar.volume} below threshold {sma * mult}; skipping confirmation"`
   - Return without starting confirmation.
3. Otherwise, proceed with existing confirmation logic.

**Edge cases:**
- Insufficient history (< `volume_ma_period` bars) → skip filter, allow confirmation.
- `volume_breakout_multiple == 0.0` → filter disabled entirely.

---

### 3.2 ATR-Based Dynamic Stops (`csam_trader-41u`)

**Where to hook:** `_enter_long()`, `_enter_short()`, `_submit_protective_orders()`  
**Logic:**
1. If `use_atr_based_stops` is False → use existing fixed-tick logic (no change).
2. If True:
   - `atr = self._compute_atr(self.config.atr_period)`
   - If ATR is None (insufficient bars) → fall back to fixed ticks, log warning.
   - `sl_distance = atr * self.config.atr_sl_multiple`
   - `tp_distance = atr * self.config.atr_tp_multiple`
   - Convert distance to ticks if needed for Nautilus order factory, or use price directly.

**Rationale:** A 2:1 R:R based on ATR is mathematically consistent regardless of instrument or volatility regime.

---

### 3.3 Session Time Guard (`csam_trader-07y`)

**Where to hook:** `on_bar()`  
**Logic:**
1. After range is established, check `self.clock.utc_now().time()`.
2. If `max_trade_time` is set and current time > `max_trade_time`:
   - Log: `"Past max_trade_time; ignoring breakouts"`
   - Do not call `_start_confirmation()`.
3. If `session_hard_stop` is set and current time >= `session_hard_stop`:
   - If in position, `self.close_all_positions(self.config.instrument_id)`.
   - Cancel all orders.
   - Log: `"Session hard stop reached; position closed"`.

**Timezone:** Use exchange-local time or UTC consistently. Document assumption in config comments.

---

### 3.4 Prior-Day & Pre-Market Context (`csam_trader-ppf`)

**Data source options:**
1. **Extended-hours bar subscription** — subscribe to pre-market bars in `on_start()`.
2. **Actor msgbus** — an external actor publishes `PriorDayContext` events; strategy listens via `msgbus.subscribe()`.

**Logic:**
1. Compute bias score from prior-day close and pre-market range.
   - `gap_pct = (premarket_close - prior_day_close) / prior_day_close`
   - `premarket_range_pct = (premarket_high - premarket_low) / prior_day_close`
2. If `prior_day_context_mode == "bias"`:
   - Bullish context → `effective_size = trade_size * aggressive_long_multiplier` for longs, `* aggressive_short_multiplier` for shorts.
   - Bearish context → reverse.
3. If `prior_day_context_mode == "filter"`:
   - Skip entries that run against the bias entirely.

**Note:** This ticket may be split if actor/msgbus integration proves complex.

---

### 3.5 ATR Anomaly Detection (`csam_trader-na0`)

**Where to hook:** `_check_atr_filter()` (upper bound), `_start_confirmation()` (stop-run bar)  
**Logic:**
1. **Upper bound** in `_check_atr_filter()`:
   - If `max_range_atr_multiple > 0` and `range_width > max_range_atr_multiple * atr`:
     - Log warning: `"Opening range unusually wide: possible gap-and-trap"`
     - `self.stop()` (or reduce size, depending on config — default to stop).
2. **Stop-run bar** in `_start_confirmation()`:
   - Compute breakout bar TR = `bar.high - bar.low`.
   - If `max_breakout_bar_atr_multiple > 0` and `TR > max_breakout_bar_atr_multiple * atr`:
     - If volume is weak (below SMA or absolute threshold), log warning and skip confirmation.

---

### 3.6 Dynamic Position Sizing & Max Trades (`csam_trader-84r`) — ✅ Implemented

**Where to hook:** `_enter_long()`, `_enter_short()`  
**Implementation:** `OrbStrategy._compute_trade_size()` and `MomentumStrategy._compute_trade_size()`
**Logic:**
1. **Max trades:**
   - Increment `_trades_today` on each successful entry.
   - If `max_trades_per_day > 0` and `_trades_today >= max_trades_per_day`:
     - Log: `"Max trades per day reached; skipping entry"`
     - Return without entering.
2. **Dynamic sizing:**
   - If `risk_per_trade_pct > 0` and `account_risk_currency > 0`:
     - `risk_dollars = account_risk_currency * risk_per_trade_pct`
     - `size = int(risk_dollars / max(sl_distance, tick_size))`
     - Clamp to `[1, max_position]`.
   - If ATR is available, scale size inversely with ATR/price ratio (higher volatility → smaller size).
   - Fallback to fixed `trade_size` when dynamic sizing is disabled.

**Reset:** `_trades_today` resets in `on_reset()` (new day).

---

### 3.7 Per-Instrument Presets (`csam_trader-1a6`)

**File:** `config/orb_presets.yaml`

```yaml
presets:
  tsla:
    first_candle_minutes: 15
    volume_breakout_multiple: 1.5
    atr_sl_multiple: 1.5
    atr_tp_multiple: 3.0
    max_trade_time: "10:30:00"
    session_hard_stop: "16:00:00"

  spy:
    first_candle_minutes: 30
    volume_breakout_multiple: 1.2
    atr_sl_multiple: 1.0
    atr_tp_multiple: 2.0
    max_trade_time: "11:00:00"

  aapl:
    first_candle_minutes: 15
    volume_breakout_multiple: 1.3
    atr_sl_multiple: 1.0
    atr_tp_multiple: 2.0
```

**Bundle syntax:**
```yaml
- id: "tsla-orb-preset"
  enabled: true
  preset: tsla          # loads defaults from orb_presets.yaml
  strategy:
    path: csam_trader.strategies.orb:OrbStrategy
    config:
      instrument_id: "TSLA.NASDAQ"
      bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
      # Override specific preset values:
      trade_size: 200
```

**BundleLoader changes:**
- Load `orb_presets.yaml` at startup.
- If `preset` key present in bundle, merge preset defaults with bundle-level overrides.
- Missing preset → raise `ValueError` with clear message.

---

## 4. Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| Existing bundle with no new fields | Unchanged — all defaults disable new features. |
| New fields set to defaults | Same as above. |
| Partial config (some new fields, not others) | Missing fields use defaults; specified fields activate features. |
| State save/load with old state | New fields default-initialize; old fields restore correctly. |

---

## 5. Test Strategy

### 5.1 Unit test coverage targets

| Feature | Target Tests |
|---------|-------------|
| Volume filter | disabled default, above threshold allows, below threshold blocks, insufficient history skips filter |
| ATR stops | disabled uses fixed ticks, enabled computes correct SL/TP, fallback when ATR unavailable |
| Session guard | trade before cutoff allowed, trade after cutoff blocked, hard stop closes position, disabled guards pass through |
| Context bias | off mode no effect, bias mode scales size correctly, filter mode skips counter-bias trades |
| Anomaly detection | upper bound stops strategy, stop-run bar blocked, disabled mode no effect |
| Dynamic sizing | fixed size default, risk-normalized size computed correctly, max trades blocks after limit, counter resets — ✅ Implemented in 9z3.8.8 |
| Presets | preset loads correctly, overrides take precedence, missing preset raises error |
| Integration | all features together with no regressions in existing tests |

### 5.2 Regression requirements

- All existing `test_orb.py` tests must pass without modification.
- Existing bundle configs must load and behave identically.
- State round-trip (save/load) must preserve all new scalar fields.

---

## 6. Implementation Order

1. **Session time guard** (`csam_trader-07y`) — simplest, touches `on_bar()` only.
2. **Volume confirmation** (`csam_trader-b80`) — additive, low risk.
3. **ATR dynamic stops** (`csam_trader-41u`) — modifies entry logic, needs careful testing.
4. **ATR anomaly detection** (`csam_trader-na0`) — builds on ATR infrastructure from #3.
5. **Dynamic sizing + max trades** (`csam_trader-84r`) — modifies sizing and entry gating.
6. **Prior-day context** (`csam_trader-ppf`) — may require actor/msgbus integration; most complex.
7. **Per-instrument presets** (`csam_trader-1a6`) — BundleLoader change, can be done in parallel.
8. **Tests + docs** (`csam_trader-4rn`) — final validation gate.

---

*Document version: 1.0*  
*Tickets: csam_trader-ocj (epic), b80, 41u, 07y, ppf, na0, 84r, 1a6, 4rn*
