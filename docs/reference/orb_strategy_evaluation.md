# CSAM Trader V2 — ORB Strategy Functional Evaluation

> **Date:** 2026-05-19  
> **Scope:** Opening Range Breakout (ORB) strategy under NautilusTrader  
> **Status:** Evaluation only — no implementation changes  
> **Prepared for:** Offline review and follow-up discussion

---

## 1. Executive Summary

The current `OrbStrategy` implementation is **solid, well-tested, and production-ready** for a baseline ORB system. It covers opening range establishment, confirmation bars, ATR-based range quality filtering, bracket orders, trailing stops, scale-out take-profits, and active risk management.

However, it is currently a **context-blind** strategy. It does not consider:

- Volume conviction at the breakout
- Prior-day or pre-market price action
- Time-of-day decay (ORB edge fades after the morning)
- Volatility-adjusted position sizing or stop distances

This document evaluates what exists, identifies configuration flexibility gaps, and proposes a phased roadmap for enhancing the strategy with contextual awareness — enabling per-instrument tuning under different market conditions.

---

## 2. Current Implementation — Functional Evaluation

### 2.1 Core Logic Flow

```
Start
  │
  ▼
on_start() → Load instrument, subscribe bars/ticks, buying power pre-flight
  │
  ▼
on_bar() → Ignore if wrong bar_type or single-price bar
  │
  ├── Range not established? → _update_range(bar)
  │      └── Accumulate H/L for first_candle_minutes
  │      └── Once established → _check_atr_filter()
  │
  ├── Already in position? → _manage_position(bar) [trailing stop updates]
  │
  ├── Active confirmation? → _handle_confirmation(bar)
  │      └── Higher lows (long) or lower highs (short)
  │      └── Reset on failure
  │
  └── Look for breakout → _start_confirmation(direction, bar)
         └── confirmation_bars == 1 ? Immediate entry
         └── confirmation_bars >= 2 ? Wait for N confirming bars
```

### 2.2 Feature-by-Feature Assessment

| Feature | Implementation | Assessment |
|---|---|---|
| **Opening Range H/L** | Accumulates high/low over `first_candle_minutes`, adapts to bar granularity | Solid — works with any bar size (1-min, 5-min, 15-min) |
| **Confirmation Bars** | Requires N consecutive bars with higher lows (long) or lower highs (short). Resets on failure | Good — provides tuneable aggression vs. patience |
| **ATR Range Filter** | Computes ATR(atr_period) over bar history. Stops strategy if `range_width < min_range_atr_multiple × ATR` | Basic but functional — prevents trading dead sessions |
| **Exit Architecture** | Two modes: (1) native Nautilus bracket orders, (2) manual order management for advanced exits | Excellent — clean separation, backward compatible |
| **Trailing Stop** | Breakeven at `trail_after_ticks`, then trails at `trail_distance_ticks`. SL only tightens | Good — simple, effective, no widening risk |
| **Scale-Out Take-Profits** | TP1 at `take_profit_1_ticks` (pct-based), TP2 at `take_profit_2_ticks`, remainder runs | Good — enables partial profit capture |
| **Risk Management** | `max_position` (share count), `max_daily_loss` (realized P&L), buying power checks | Solid — actively enforced before every entry |
| **State Persistence** | `on_save`/`on_load` via pickle preserves all internal state | Good — enables graceful restarts without losing context |
| **Test Coverage** | 2,000+ lines of unit tests | Very Good — config, edge cases, state round-trips all covered |

### 2.3 Architectural Strengths

- **Bundle-driven configuration:** No hard-coded strategies in `main.py`. Adding a new ORB instance is purely YAML configuration.
- **Frozen dataclass configs:** Type-safe, validated at load time via Nautilus/msgspec.
- **Per-instrument isolation:** Each bundle is fully independent. TSLA can run 15-min ORB while AAPL runs 5-min ORB with entirely different risk parameters.
- **Graceful restart support:** All state is preserved across restarts via `on_save`/`on_load`.

---

## 3. Configuration Flexibility Assessment

### 3.1 Current Config Surface

```yaml
# Core parameters (OrbConfig)
instrument_id: "TSLA.NASDAQ"
bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
first_candle_minutes: 15
trade_size: 100
confirmation_bars: 1
atr_period: 14
min_range_atr_multiple: 0.0   # 0 = disabled

# Bracket parameters (BracketConfig)
bracket:
  stop_loss_ticks: 10
  take_profit_ticks: 30
  trail_after_ticks: 0          # 0 = no trailing
  trail_distance_ticks: 8
  take_profit_1_ticks: 0        # 0 = single TP mode
  take_profit_1_pct: 50.0
  take_profit_2_ticks: 0
  take_profit_2_pct: 25.0

# Risk parameters (RiskConfig)
risk:
  max_position: 500
  max_daily_loss: 1000
```

### 3.2 What Is Flexible Today

| Aspect | Flexibility | Notes |
|---|---|---|
| Timeframe | High | `first_candle_minutes` adapts to any bar granularity |
| Confirmation sensitivity | High | `confirmation_bars` from 1 (aggressive) to N (conservative) |
| Range quality | Medium | `min_range_atr_multiple` filters narrow ranges |
| Position sizing | Low | Fixed `trade_size` per bundle — no volatility adjustment |
| Exit mechanics | High | SL/TP distances, trailing, scale-outs all independent per bundle |
| Risk limits | Medium | Max position and daily loss are share/currency-based, not %-based |

### 3.3 What Is Rigid Today

| Aspect | Current Limitation | Impact |
|---|---|---|
| **Position sizing** | `trade_size` is static | Cannot do "risk 1% of account per trade" sizing |
| **Stop distances** | Fixed tick distances | 10 ticks on TSLA ≠ 10 ticks on SPY in risk terms |
| **Time-of-day guards** | No session boundaries | Will trade ORB breakouts at any time of day |
| **Directional bias** | Symmetric long/short | No way to favor one side based on market context |
| **Volume** | Completely ignored | Cannot filter low-conviction breakouts |
| **Prior day context** | No memory of yesterday | Misses well-documented momentum carry edge |
| **Pre-market context** | No pre-market data | Cannot see gap direction or pre-market range |

---

## 4. Areas for Improvement — Prioritized

### 4.1 High Impact — Tactical Edge

#### A. Volume-Confirmed Breakouts

**The Problem:** A breakout on low volume is often a fake-out. A breakout with volume 1.5× the recent average has significantly higher conviction.

**Proposed Addition:**
```yaml
volume_ma_period: 20              # bars for volume SMA
volume_breakout_multiple: 1.5     # require volume > 1.5× avg to start confirmation
```

**Where to hook:** In `_start_confirmation()`, check `bar.volume` against a running volume SMA. Only start the confirmation sequence if volume exceeds the threshold.

**Backward compatibility:** Set `volume_breakout_multiple: 0.0` to disable entirely.

**Why this matters:** Volume is the single most effective fake-out filter for breakout strategies. This alone would improve win rate significantly.

---

#### B. ATR-Based Dynamic Stops (Volatility-Normalized Risk)

**The Problem:** Fixed `stop_loss_ticks` / `take_profit_ticks` do not adapt to volatility. 10 ticks on TSLA at earnings is trivial; 10 ticks on SPY is substantial.

**Proposed Addition:**
```yaml
use_atr_based_stops: false
atr_sl_multiple: 1.0              # SL = 1.0 × ATR
atr_tp_multiple: 2.0              # TP = 2.0 × ATR (2:1 reward-to-risk)
```

**Where to hook:** In `_enter_long()` / `_enter_short()`, if `use_atr_based_stops` is true, compute SL and TP distances using the same ATR already calculated for the range filter, instead of fixed tick counts.

**Benefit:** One bundle config can work across multiple volatility regimes without constant retuning. A 2:1 R:R based on ATR is mathematically consistent regardless of instrument.

---

#### C. Session Time Guard — "ORB Decay"

**The Problem:** The ORB edge decays rapidly after the open. A breakout at 11:00 AM is statistically very different from a breakout at 9:35 AM. The strategy currently has no time awareness.

**Proposed Addition:**
```yaml
session_start: "09:30:00"         # when to begin accumulating the range
max_trade_time: "11:00:00"        # stop looking for new breakouts after this time
session_hard_stop: "16:00:00"     # close any open position at this time
```

**Where to hook:** In `on_bar()`, after range establishment, check `self.clock.utc_now().time()` against `max_trade_time`. If past cutoff, ignore further breakouts. At `session_hard_stop`, close any open position.

**Benefit:** Prevents late-day low-conviction entries. Aligns with the reality that opening range dynamics fade.

---

### 4.2 Medium Impact — Context & Selectivity

#### D. Prior-Day Performance Bias (Directional Aggression)

**Your Question:** *"Should knowing if the stock gained previous day and/or pre-market help decide on being aggressive or not?"*

**Answer: Yes.** This is a well-documented edge in ORB and intraday momentum literature.

**The Logic:**

| Context | Bias | Action |
|---|---|---|
| Strong prior day + gap up pre-market | Bullish | Be more aggressive on long breakouts; reduce or skip shorts |
| Weak prior day + gap down pre-market | Bearish | Be more aggressive on short breakouts; reduce or skip longs |
| Inside day / doji + flat pre-market | Neutral / low conviction | Reduce size or skip entirely |

**Proposed Addition:**
```yaml
prior_day_context_mode: "off"     # "off" | "bias" | "filter"
premarket_lookback_minutes: 30    # minutes before 9:30 to evaluate
aggressive_long_multiplier: 1.5   # scale up longs when bullish context
aggressive_short_multiplier: 0.5  # scale down shorts when bullish context
```

**Data requirement:** This requires prior-day close and pre-market bars. Nautilus can subscribe to extended-hours data via IB if configured. Alternatively, an **Actor** can fetch this and publish it to the strategy via `msgbus`.

**Where to hook:** In `on_start()`, either subscribe to pre-market bars or listen for actor-published context. Store `prior_day_close`, `premarket_high`, `premarket_low`, `premarket_volume`. In `_enter_long()` / `_enter_short()`, apply the multiplier to `trade_size` or skip the trade if it runs against the bias.

---

#### E. Pre-Market Range Context

**The Problem:** If pre-market already broke a significant level, the "opening range" may simply be continuation, not a true breakout. The pre-market high/low can be more important than the first 15-minute high/low.

**Proposed Addition:**
```yaml
use_premarket_range: false
premarket_range_weight: 0.5       # blend pre-market extremes into ORB range
```

**Logic:** If `use_premarket_range` is true, the effective breakout level becomes a blend of the opening range and the pre-market extremes, weighted by `premarket_range_weight`.

---

#### F. ATR for Market Manipulation / Fake-Out Detection

**Your Question:** *"Should ATR be used to check for market manipulation?"*

**Answer:** ATR alone cannot detect manipulation, but ATR-derived anomaly detection can flag suspicious price action:

| Metric | What It Catches | Proposed Config |
|---|---|---|
| **Opening range vs. overnight ATR** | Today's range >3× overnight ATR may indicate a gap-and-trap | `max_range_atr_multiple: 3.0` |
| **Breakout bar TR vs. ATR** | Breakout bar with TR > 3× ATR on low volume = potential stop-run | `max_breakout_bar_atr_multiple: 3.0` |
| **Range position vs. prior day** | Opening range entirely outside prior day's range = exceptional gap | Compare `_range_high`/`_range_low` to prior day H/L |

**Proposed Addition:**
```yaml
max_range_atr_multiple: 0.0       # upper bound filter (0 = disabled)
max_breakout_bar_atr_multiple: 0.0 # flag stop-run bars (0 = disabled)
```

---

### 4.3 Lower Impact — Polish & Robustness

#### G. Dynamic Position Sizing (Risk-Normalized)

**The Problem:** Fixed `trade_size` means risk varies wildly with volatility and instrument price.

**Proposed Addition:**
```yaml
risk_per_trade_pct: 0.0           # 0 = use fixed trade_size
account_risk_currency: 0.0        # e.g., $100 risk per trade
```

**Logic:** `trade_size = account_risk_currency / (stop_loss_distance)`. When these are set, they override the fixed `trade_size`.

---

#### H. Maximum Trades Per Day

**The Problem:** After 2-3 failed ORBs, the setup is likely wrong for the day. The current strategy will keep trying.

**Proposed Addition:**
```yaml
max_trades_per_day: 0             # 0 = unlimited
```

**Logic:** Increment a counter on each entry. Block new entries once the limit is reached. Resets on `on_reset()` (new day).

---

## 5. Recommended Evolution Path

The goal is **configuration tuning for different instruments under different conditions**. Here is the suggested implementation roadmap:

### Phase 1: Context-Aware ORB (Immediate Priority)

Add a focused set of "market context" parameters. These require no external ML or complex infrastructure — just new config fields and strategy logic:

```yaml
# Example: TSLA ORB with context awareness
- id: "tsla-orb-contextual"
  enabled: true
  venue: IB
  strategy:
    path: csam_trader.strategies.orb:OrbStrategy
    config:
      instrument_id: "TSLA.NASDAQ"
      bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
      first_candle_minutes: 15
      
      # Phase 1 additions:
      session_start: "09:30:00"
      max_trade_time: "11:00:00"
      
      volume_ma_period: 20
      volume_breakout_multiple: 1.5
      
      use_atr_based_stops: true
      atr_sl_multiple: 1.0
      atr_tp_multiple: 2.5
      
      prior_day_context_mode: "bias"
      aggressive_long_multiplier: 1.5
      aggressive_short_multiplier: 0.5
      
      trade_size: 100
  bracket:
    stop_loss_ticks: 10        # fallback when use_atr_based_stops: false
    take_profit_ticks: 30
  risk:
    max_position: 500
    max_daily_loss: 1000
```

### Phase 2: Per-Instrument Presets (Short Term)

Create a preset library so you don't have to tune every parameter for every new instrument:

```yaml
# config/orb_presets.yaml
presets:
  tsla:
    first_candle_minutes: 15
    volume_breakout_multiple: 1.5
    atr_sl_multiple: 1.5
    atr_tp_multiple: 3.0
    max_trade_time: "10:30:00"
    
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

Bundles can reference presets and override specific values.

### Phase 3: ML-Assisted Parameter Optimization (Medium Term)

Use backtest results to optimize parameters per instrument:

1. Run grid search over `first_candle_minutes`, `confirmation_bars`, `volume_breakout_multiple`, `atr_sl_multiple`, `atr_tp_multiple`.
2. Store optimal parameters in a tuning database or enriched bundle config.
3. Re-evaluate monthly via the Ralph loop or scheduled job.

---

## 6. Direct Answers to Your Questions

| Question | Short Answer | Recommendation |
|---|---|---|
| **Prior day gain → aggression?** | Yes, this is a real edge. | Add `prior_day_context_mode` config. Use an Actor or extended-hours data subscription to push prior close + pre-market data. Bias position sizing, not direction elimination. |
| **Volume consideration?** | Absolutely essential. | Add volume SMA filter. Require breakout volume > 1.2–1.5× average. This alone will filter many fake-outs. |
| **ATR for manipulation?** | Use ATR for anomaly detection, not manipulation directly. | Add `max_range_atr_multiple` (upper bound) and `max_breakout_bar_atr_multiple`. Flag bars with TR > 3× ATR on weak volume. |
| **Config tuning per instrument?** | The bundle system is already perfect for this. | Add Phase 1 params, then create per-instrument presets. Backtest each preset against historical data. |

---

## 7. Summary: Current vs. Proposed

| Capability | Current | Proposed (Phase 1) |
|---|---|---|
| Opening range H/L | ✅ | ✅ |
| Confirmation bars | ✅ | ✅ |
| ATR range filter (lower bound) | ✅ | ✅ + upper bound |
| Fixed SL/TP in ticks | ✅ | ✅ + ATR-based option |
| Trailing stop | ✅ | ✅ |
| Scale-out take-profits | ✅ | ✅ |
| Volume confirmation | ❌ | ✅ |
| Session time guard | ❌ | ✅ |
| Prior day / pre-market context | ❌ | ✅ |
| Dynamic position sizing | ❌ | ✅ |
| Max trades per day | ❌ | ✅ |
| Per-instrument presets | ❌ | ✅ |

---

## 8. Next Steps (Pending Your Review)

1. **Review this document** and identify which enhancements align with your trading thesis.
2. **Prioritize** which Phase 1 items to implement first.
3. **Ask follow-up questions** — especially on:
   - Data sources for prior-day/pre-market context
   - How you want to handle the `msgbus` actor integration
   - Specific instruments you want to tune first
4. **Decide on implementation scope** — I can implement any or all of these incrementally, with tests and documentation.

---

*Document version: 1.0*  
*Do not implement without explicit approval.*
