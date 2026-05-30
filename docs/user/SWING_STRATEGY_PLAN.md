# Swing Trading Strategy Plan: V1 → V1.5 → V2.0

> **Status:** Draft — Brainstorming Consolidation  
> **Author:** AI Advisor + Sam Dharma  
> **Scope:** Functional specification for config-driven algorithmic swing trading on US/Nasdaq equities via IBKR/NautilusTrader  
> **Constraint:** Long-only for V1. Short capability deferred to V2.0.

---

## 1. Executive Summary

This document defines a **hybrid discretionary-systematic swing trading strategy** where the human operator provides the qualitative edge (fundamental research, instrument selection, conviction levels) and the algorithm provides quantitative discipline (entry timing, regime-aware risk management, systematic exits).

The strategy is explicitly designed as a **low-frequency, high-conviction** system: maximum 5 open positions, maximum 5 new trades per month, hold periods of days to weeks (hard cap 15 trading days, extendable to 25 via conviction flag).

**Core Differentiators:**
- **Hand-picked watchlist** (not scanner-driven universe)
- **Entry zone concept** — operator defines a price range; algo evaluates conditional triggers within that range
- **Regime-aware dynamic exits** — daily regime input from the operator's first-party system modulates stop behavior and entry permission
- **Active / Parked watchlist lifecycle** — instruments graduate or degrade based on realized performance

**Platform Context:** Built for execution on **Interactive Brokers (IBKR)** via **IB Gateway**, orchestrated through **NautilusTrader v1.227.0**. All functional specifications are expressed in platform-agnostic terms; Nautilus-specific implementation notes are provided where they clarify execution semantics.

---

## 2. Strategy Philosophy & Literature Foundation

### 2.1 The Edge: Discretionary Selection + Systematic Execution

Academic and practitioner literature consistently finds that systematic execution outperforms discretionary execution *when the same signal is applied*, primarily due to elimination of emotional deviation from stop rules and position sizing (QuantInsti, 2026; Finzer, 2025). However, pure systematic strategies struggle with alpha generation because scan-driven universe selection often captures spurious correlations.

This strategy inverts the typical quant approach:
- **Alpha generation:** Human fundamental research + technical zone selection
- **Alpha preservation:** Algorithmic execution, regime-conditioned risk management, and hard time/trade limits

As noted in swing trading literature (Tradier, 2025), the discipline of swing trading "lies not just in spotting good setups, but also in having strict rules for entry, exit, and risk management." This system encodes that discipline.

### 2.2 Timeframe Selection: The 4-Hour Bar

V1 evaluates on **4-hour (4H) bars**.

**Rationale:**
- Daily bars are too slow for regime transitions and earnings reactions. A bearish regime flip on Monday may not trigger a daily-bar exit until Tuesday close, exposing the position to an additional overnight gap.
- 1-hour bars introduce excessive noise for multi-day holds; stop placement becomes a function of intraday volatility rather than swing structure.
- 4H bars provide 6 evaluation points per trading day—sufficient responsiveness for regime changes and trailing stop management without overfitting to intraday wicks (LuxAlgo, 2025).

**Multi-timeframe alignment (MTA):** Although V1 executes on 4H, trend confirmation uses daily-bar indicators (50-day SMA, 20/50 EMA alignment). This follows established MTA methodology: higher timeframes establish directional bias; trading timeframes generate execution signals (LearnPriceAction, 2021; QuantInfo, 2023).

### 2.3 Regime-Aware Adaptation

Research on regime detection demonstrates that static parameters fail across market states. Hidden Markov Models (HMM), Random Forest classifiers, and rule-based volatility filters all show that strategy adaptation to regime improves risk-adjusted returns and reduces drawdowns (QuestDB, 2026; QuantInsti, 2026; LSEG, 2023).

This strategy treats regime as a **first-class input** that modulates:
1. Entry permission (halt in bearish)
2. Trailing stop width (widen in bullish, tighten in neutral, flatten in bearish)
3. Position sizing aggressiveness (via upstream sizing engine, but V1 applies strategy-level caps)

### 2.4 Position Sizing & The 1-2% Rule

The strategy delegates position sizing to an upstream portfolio-sizing algorithm, but enforces a **strategy-level maximum risk per trade of 1.5% of account equity** and a **maximum position exposure of 20%** per instrument. This aligns with institutional best practices: "With a 1% risk per trade, you would need 50 consecutive losing trades to draw down 50%—a statistical near-impossibility for any trader with even a modest edge" (TradeAlgo, 2026).

---

## 3. V1 Detailed Specification

### 3.1 Universe & Instrument Selection

#### 3.1.1 Active Watchlist
- **Maximum size:** 5 instruments
- **Source:** Operator-selected via fundamental/technical research
- **Instruments:** US equities listed on Nasdaq or NYSE (primary focus on Nasdaq for V1)
- **Liquidity filter (hard):** Average daily volume > 1,000,000 shares over trailing 20 days; minimum price $10.00
- **Volatility filter (hard):** ATR(14) / Close < 0.08 (8% daily range) — excludes hyper-volatile meme names where stop placement becomes unreliable

#### 3.1.2 Parked Watchlist
- Instruments removed from Active due to repeated stop-outs or thesis invalidation
- Operator may promote back to Active after re-evaluation
- No automatic promotion; requires explicit config change

#### 3.1.3 Instrument Lifecycle
```
Operator Research → Configured in Active Watchlist
                         ↓
              Position Opened → Stop-Out or Time Exit
                         ↓
         Thesis Still Valid? → YES: Remain Active
                         ↓ NO
                   Move to Parked Watchlist
                         ↓
         Operator Re-evaluation → Promote to Active / Remove
```

### 3.2 Bar Configuration & Data Requirements

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Primary bar** | 4H | Responsive without noise |
| **Trend bar** | 1D | Daily indicators for HTF alignment |
| **Volume lookback** | 20 bars | Standard SMA window for relative volume |
| **ATR lookback** | 14 bars | Wilder's standard; balances responsiveness and smoothing |
| **EMA/SMA lookback** | 20, 50, 200 | Classic swing trading alignment (Tradier, 2025) |
| **ADX lookback** | 14 bars | Standard trend strength measurement |

**Data quality requirement:** Adjusted close prices mandatory. Splits and dividends unadjusted will corrupt ATR and moving average calculations.

### 3.3 Entry Logic: The Zone + Confirmation Model

The operator defines an **entry zone** (`entry_zone_low`, `entry_zone_high`) for each instrument. The algo does not chase price; it waits for price to enter the zone and confirm strength.

#### 3.3.1 Zone Penetration Condition
```
bar.low <= entry_zone_high AND bar.close >= entry_zone_low
```
**Interpretation:** Price visited or closed within the operator's desired accumulation range. Using `low` and `close` prevents entries where the stock gaps far above the zone (chasing) or crashes through it (falling knife).

#### 3.3.2 Trend Confirmation (Daily-Bar Filters)
All conditions must be `True`:

| Filter | Rule | Purpose |
|--------|------|---------|
| **Primary trend** | Daily Close > 50-day SMA | Ensures medium-term uptrend (LuxAlgo, 2025) |
| **Momentum alignment** | Daily 20 EMA > 50 EMA | Short-term momentum aligned with medium-term |
| **Trend strength** | Daily ADX(14) > 20 | Avoids choppy, directionless markets (AlchemyMarkets, 2025) |
| **Relative volume** | 4H Volume > 1.2 × SMA(20, 4H Volume) | Confirms institutional participation (Finzer, 2025) |

#### 3.3.3 Regime Gate
```
upstream_regime in ['bullish', 'neutral']
```
- `bullish`: Full strategy parameters
- `neutral`: Entry allowed, but trailing stop tightens post-entry (see §3.5.2)
- `bearish`: **No new entries.** Existing positions managed under bearish exit rules.

#### 3.3.4 Portfolio Slot Availability
```
open_positions < 5 AND monthly_new_entries < 5
```
- **Monthly counter resets on calendar month start.**
- **Re-entries count as new entries.** This enforces selectivity.

#### 3.3.5 Earnings Blackout Gate
```
days_to_earnings > earnings_blackout_days  (default: 2)
```
- If earnings announcement is within `earnings_blackout_days`, entry is blocked.
- This is a **configurable default**; operator may set to 0 for specific names.

#### 3.3.6 Order Execution
Upon all conditions met on a 4H bar close:
- **Order type:** Market order at next bar open
- **Rationale:** Limit orders inside a zone often fill on continuation-through (i.e., the stock keeps dropping). A market order at open after a confirming close ensures the setup validated. For V1, execution certainty outweighs slippage optimization given the low frequency.

### 3.4 Exit Logic: The Three-Layer Exit System

V1 uses a **layered exit architecture** (Finzer, 2025; TradingBrokers, 2025). Each layer is independent; whichever triggers first closes the position.

#### Layer 1: Hard Stop-Loss (Capital Preservation)
```
stop_price = entry_price - (1.5 × ATR(14))
```
- Evaluated on each 4H bar close.
- If `bar.close < stop_price` → exit at next open.
- **Never widened.** This is the catastrophic loss limit.

#### Layer 2: Dynamic Trailing Stop (Profit Protection)
The trailing stop behavior is **regime-dependent**:

| Regime | Trail Rule | Technical Basis |
|--------|-----------|----------------|
| **Bullish** | `HighestClose - (2.0 × ATR(14))` | Wide trail allows swings to develop; aligns with trend-following research that finds wider stops improve expectancy in trending markets (QuantInsti, 2026) |
| **Neutral** | `HighestClose - (1.0 × ATR(14))` | Tightened to protect against chop; chop is the primary profit destroyer in mean-reverting conditions (ExtremeToMean, 2024) |
| **Bearish** | **Immediate flatten** at next bar open | Macro risk dominates individual setup; regime detection research shows early exit in predicted crash states outperforms trailing stops (LSEG, 2023) |

**Breakeven Rule (universal):**
Once position reaches `+1.0R` profit (where `1R = 1.5 × ATR(14)`), the hard stop is **lifted to entry price**. This guarantees no losing trade becomes a significant loser after being profitable.

**Profit Lock Rule (universal):**
Once position reaches `+2.0R` profit, the trailing stop floor is set to `entry_price + (0.5 × ATR(14))`. Even if the trail hasn't triggered, this floor prevents round-trips from +2R back to breakeven.

#### Layer 3: Time-Based Exit (Opportunity Cost)
```
if holding_bars >= max_hold_bars (default: 15 trading days ≈ 90 4H bars):
    exit at next bar open
```
- **Conviction extension:** If `conviction_hold: true` in instrument config, `max_hold_bars` extends to 25 trading days (~150 4H bars).
- **Mid-trade revision:** Operator may toggle `conviction_hold` via live command while position is open. This is the only mid-trade parameter revision permitted in V1.

#### 3.4.1 Earnings Exit (Event Risk)
```
if days_to_earnings <= earnings_blackout_days AND position_is_open:
    flatten at next daily close before announcement
```
- **Default behavior:** Flatten.
- **Configurable per instrument:** Operator may override to `hold_through_earnings: true`.

### 3.5 Regime Integration Specification

#### 3.5.1 Regime Input Contract
The upstream regime system provides a daily regime label for the *broad market* (e.g., S&P 500 or Nasdaq composite). V1 treats this as a **systematic risk overlay** applied to all positions.

**Recommended regime taxonomy:**
- `bullish` — uptrend, low/moderate volatility
- `neutral` — ranging, moderate volatility
- `bearish` — downtrend, elevated volatility or crash risk
- `unknown` — treat as `neutral` (conservative default)

#### 3.5.2 Regime-State Machine

| State | Entry Permission | Trailing Stop | Existing Position Action |
|-------|-----------------|---------------|-------------------------|
| `bullish` | Allowed | 2.0× ATR | Normal management |
| `neutral` | Allowed | 1.0× ATR | Tighten trail; breakeven rule still active |
| `bearish` | **Blocked** | N/A | **Flatten all positions** at next 4H bar open |
| `unknown` | Allowed | 1.0× ATR | Tighten trail |

**Hysteresis recommendation:** To avoid whipsawing around regime boundaries, require **2 consecutive days** of bearish regime before flattening existing positions. New entries respect the regime immediately.

### 3.6 Position Sizing & Portfolio Heat

#### 3.6.1 Sizing Architecture
- **Primary sizing:** Delegated to upstream portfolio sizing engine (operator's algo system)
- **Strategy floor/ceiling:** V1 enforces its own bounds regardless of upstream input

| Parameter | Default | Hard Limit |
|-----------|---------|------------|
| Risk per trade | 1.0% of equity | Max 1.5% |
| Position exposure | 15% of equity | Max 20% |
| Max portfolio heat | 5% total at-risk | Max 6% |

**Portfolio heat calculation:**
```
heat = Σ (position_value × (stop_distance / entry_price)) / total_equity
```
If a new entry would cause `heat > max_portfolio_heat`, the entry is **deferred** (not rejected—retried on next bar if conditions still hold).

#### 3.6.2 Correlation Guard
- **V1 simplified:** Operator ensures sector diversity via watchlist curation.
- **V1.5 enhancement:** Automatic sector concentration limit (see §4).

### 3.7 Watchlist Management: Active vs. Parked

#### 3.7.1 Active Watchlist Rules
- Max 5 instruments
- Each instrument has a complete config block (see §3.9)
- Operator may add/remove instruments via config change (requires strategy reload or hot-config capability)
- **Stop-out handling:** When a position hits hard stop or time exit with loss:
  1. Instrument is **flagged** for review
  2. If the same instrument triggers a second consecutive loss, it is **automatically moved to Parked**
  3. Operator must explicitly promote it back to Active

#### 3.7.2 Parked Watchlist Rules
- Instruments that have failed twice consecutively or where operator has lost conviction
- No automatic monitoring for entry
- Operator may promote back to Active after re-evaluation (fundamental thesis unchanged, technical picture improved)
- Promoted instrument **resets its loss counter**

### 3.8 State Machine: Trade Lifecycle

```
[INACTIVE] ──(operator config)──> [WATCHING]
                                      │
                    (zone + filters + slots) │
                                      ▼
                                 [PENDING ENTRY]
                                      │
                         (next bar market order) │
                                      ▼
                                  [OPEN]
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
     (hard stop hit)          (trailing stop hit)      (time exit)
              │                       │                       │
              ▼                       ▼                       ▼
         [STOPPED OUT]          [TRAILED OUT]          [TIME EXIT]
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      ▼
                              [POST-TRADE REVIEW]
                                      │
                     (2nd consecutive loss?) ──YES──> [MOVE TO PARKED]
                                      │ NO
                                      ▼
                                [RETURN TO WATCHING]
```

### 3.9 Configuration Schema (V1)

```yaml
strategy:
  id: "swing_v1_long"
  name: "Swing V1 Long-Only"
  version: "1.0.0"
  tags: ["swing", "v1", "long_only"]

runtime:
  primary_bar_type: "4H"
  trend_bar_type: "1D"
  evaluation_timing: "bar_close"
  execution_timing: "next_bar_open"

constraints:
  max_open_positions: 5
  max_new_entries_per_month: 5
  max_hold_trading_days: 15
  conviction_hold_extension_days: 10
  max_risk_per_trade_pct: 1.5
  max_position_exposure_pct: 20.0
  max_portfolio_heat_pct: 6.0

entry_filters:
  zone_penetration: true
  trend:
    close_above_50sma: true
    ema20_above_ema50: true
    adx_min: 20
  relative_volume:
    multiplier_vs_sma20: 1.2
  regime_gate:
    allowed: ["bullish", "neutral"]
  earnings_blackout:
    enabled: true
    default_days: 2

exit_rules:
  hard_stop:
    atr_multiple: 1.5
    atr_lookback: 14
  breakeven:
    trigger_at_r_multiple: 1.0
  profit_lock:
    trigger_at_r_multiple: 2.0
    floor_atr_multiple: 0.5
  trailing_stop:
    bullish_atr_multiple: 2.0
    neutral_atr_multiple: 1.0
  time_exit:
    max_bars: 90        # 15 trading days × 6 4H bars
    conviction_extension_bars: 60  # +10 days
  earnings:
    flatten_before_blackout: true

regime:
  upstream_source: "operator_regime_engine"
  bearish_flatten_hysteresis_days: 2
  unknown_treat_as: "neutral"

instruments:
  - symbol: "TSLA"
    entry_zone_low: 220.0
    entry_zone_high: 240.0
    conviction_hold: false
    max_position_pct: 20.0
    sector: "technology"
    earnings_blackout_days: 2
    hold_through_earnings: false
    # Re-entry: allowed while in Active watchlist and monthly budget available
```

### 3.10 NautilusTrader Implementation Notes

- **Bar aggregation:** Use `BarType` with `4H` and `1D` resolutions. Subscribe to both; 1D used for trend filters, 4H for execution logic.
- **Order types:** Use `MarketOrder` for entries (next bar). Use `TrailingStopMarket` orders managed programmatically rather than exchange-native trailing stops, because regime changes require dynamic trail width adjustments that IBKR native trails cannot express.
- **Timers:** Use `Clock` to schedule earnings checks (daily at 09:30 ET) and monthly counter resets.
- **State persistence:** Position state, entry prices, highest close, and R-multiple progress must persist across strategy restarts. Use Nautilus `Cache` or external Redis (per SAM Trader V3 architecture).
- **Config hot-reload:** Nautilus strategies can reload config via `on_reset` or external signal. Mid-trade `conviction_hold` toggle should be handled via a custom command message or config watcher.

---

## 4. V1.5 Roadmap: Enhanced Discipline

**Theme:** Preserve what works in V1; add profit harvesting and concentration controls.

| Feature | Description | Priority |
|---------|-------------|----------|
| **Partial Exits (Scale-Out)** | Sell 40% of position at `+2.0R`; let 60% run with trailing stop. This addresses the "giveback problem" identified in swing trading literature (Benzinga, 2026; QuantStrategy.io, 2026). | High |
| **Sector Correlation Guard** | Max 2 positions per GICS sector; block entry if new instrument correlates >0.85 with existing holding (60-day return correlation). Prevents disguised concentration. | High |
| **Re-Entry Cooldown** | After stop-out, instrument enters a 5-trading-day cooldown before it can re-trigger. Prevents whipsaw re-entries in the same zone. | Medium |
| **Dynamic ATR Lookback** | Use volatility regime to adjust ATR lookback: 14 in normal conditions, 7 in high-vol to increase responsiveness. | Medium |
| **Volume Profile Confirmation** | Require volume on entry bar to exceed not just the SMA, but the **prior 10 bars' maximum volume** (strong conviction spike). | Low |
| **Operator Override Panel** | Simple CLI/GUI to: toggle `conviction_hold`, force-flatten a position, move instrument to Parked, or pause all entries. | Medium |

**Literature basis for partial exits:** Research on pyramiding and position scaling shows that "taking 20-30% of the total position off the table after the third scaling point is reached... is crucial for managing psychological stress and locking in capital" (QuantStrategy.io, 2026). V1.5 applies this in reverse: scale *out* of winners rather than scale *in*.

---

## 5. V2.0 Roadmap: Full Systematic Swing

**Theme:** Remove remaining manual gates; add short capability and adaptive intelligence.

| Feature | Description | Complexity |
|---------|-------------|------------|
| **Short-Side Swing** | Mirror of V1 long logic for short entries: downtrend, zone below price, regime bearish or neutral. Requires uptick rule awareness (SEC 242.204) and hard-to-borrow checks via IBKR. | High |
| **Instrument Scanner / Ranking** | Operator may optionally enable a quantitative scanner that ranks Nasdaq stocks by setup quality (trend strength + volume + proximity to support) and suggests Active watchlist candidates. Human retains veto. | High |
| **Regime-Aware Sizing** | Upstream sizing engine receives regime label and adjusts base risk: 1.5% in bullish, 1.0% in neutral, 0.5% in bearish (if short enabled). | Medium |
| **Walk-Forward Optimization (WFO)** | Monthly re-optimization of ATR multiples, ADX threshold, and relative volume multiplier on rolling 6-month windows. Parameters selected from the most stable cluster, not the peak performer (QuantBeckman, 2025; QuantConnect, 2025). | High |
| **Machine Learning Filter (Optional)** | A lightweight classifier (Random Forest or XGBoost) trained on post-trade outcomes predicts probability of success for each setup. Only take setups with `P(success) > 0.55`. Keeps human-selected watchlist but filters weak triggers. | Very High |
| **Multi-Strategy Ensemble** | Run Swing V2 alongside a mean-reversion swing module. Capital allocation between modules determined by regime: 80/20 trend/mean-rev in bullish, 50/50 in neutral, 0/100 in bearish (if mean-rev short enabled). | Very High |

**WFO Warning:** Walk-forward optimization is computationally expensive and requires careful window selection. As noted in practitioner literature, "window size selection impacts results, introducing biases, and while it adapts to market changes, it reacts to regime shifts rather than predicting them" (QuantInsti, 2025). V2.0 should treat WFO as a parameter suggestion tool, not an autopilot.

---

## 6. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Regime lag** — Upstream system identifies bearish regime 2-3 days after crash begins | Medium | High | Hysteresis on flattening helps, but add a **market circuit breaker**: if Nasdaq drops >3% in a single session, V1 flattens all positions immediately regardless of regime. |
| **Earnings gap-through** — Stock gaps 20% through stop before 4H bar evaluates | Low | High | Earnings blackout default is `flatten`; operator must explicitly override per instrument. No mitigation for surprise pre-announcements. |
| **Zone anchoring** — Operator sets zone based on old support; stock never revisits | High | Medium | Time-decay the zone: if price hasn't entered zone within 10 trading days of config, instrument is auto-flagged for review. |
| **Correlation blindness** — 5 positions all in correlated tech names | Medium | High | V1.5 sector guard. For V1, operator discipline is the only mitigation. |
| **Overfitting to 4H** — Excessive responsiveness to 4H noise | Medium | Medium | Daily-bar trend filters act as noise dampeners. If V1 shows excessive whipsaw, elevate primary bar to daily in V1.1. |
| **Config drift** — Operator changes zone or conviction mid-trade emotionally | Low | High | Only `conviction_hold` is editable mid-trade. All other parameters require strategy restart. Audit log all changes. |

---

## 7. Validation & Testing Plan

### 7.1 Backtesting Requirements (Pre-V1 Live)
- **Minimum data:** 2 years of 4H and daily bars for all Active watchlist candidates
- **Transaction costs:** $0.0035/share commission (IBKR tiered) + $0.005/share slippage model
- **Benchmark:** Buy-and-hold of equal-weighted Active watchlist
- **Metrics required:** Total return, Sharpe ratio, max drawdown, win rate, profit factor, average R per trade, max consecutive losses

### 7.2 Paper Trading Requirements
- **Duration:** Minimum 1 month (to capture at least 2-5 trades)
- **Success criteria:**
  - No single loss > 2% of equity (validates stop enforcement)
  - Regime transitions handled without manual intervention
  - All trades tagged correctly with version and strategy labels
  - Monthly trade count stays within 0–7 (validates selectivity)

### 7.3 Live Deployment Gates
| Gate | Criteria |
|------|----------|
| **G1: Config Validation** | All 5 instruments have complete config; no overlapping zones; sector diversity confirmed |
| **G2: Regime Online** | Upstream regime system publishing daily labels for 7 consecutive days without error |
| **G3: Capital Allocation** | Live capital ≤ 25% of intended full allocation for first month |
| **G4: Kill Switch** | Operator can pause all entries and flatten all positions within 60 seconds |

---

## 8. Decision Log

| Decision | Rationale | Reversible? |
|----------|-----------|-------------|
| 4H primary bar | Responsive to regime without intraday noise | Yes (can elevate to daily in V1.1) |
| All-or-nothing exits in V1 | Simplicity; partial exits add complexity prematurely | Yes (V1.5 adds scale-out) |
| Market order at next open | Execution certainty over slippage savings | Yes (can add limit-at-mid in V2) |
| Re-entries count as new trades | Enforces selectivity; prevents revenge trading | Yes (configurable in V1.5) |
| Conviction hold as only mid-trade edit | Prevents emotional zone-widening mid-loss | No (firm rule) |
| Default earnings flatten | Event risk is the #1 avoidable blow-up in swing trading | Yes (per-instrument override) |
| 2-loss auto-park | Forces operator re-evaluation; prevents automated bleeding | No (firm rule) |

---

## 9. References

1. Tradier. (2025). *Swing Trading Strategies: A Comprehensive Guide*. https://hub.tradier.com/articles/swing-trading-strategies-a-comprehensive-guide/
2. QuantInsti. (2026). *Systematic Trading: Strategies, Concepts & Quantitative Approach*. https://www.quantinsti.com/articles/systematic-trading/
3. LuxAlgo. (2025). *Swing Trading Strategies: Profiting from Market Volatility*. https://www.luxalgo.com/blog/swing-trading-strategies-profiting-from-market-volatility/
4. TradingBrokers. (2025). *Algorithmic Swing Trading*. https://tradingbrokers.com/algorithmic-swing-trading/
5. Finzer. (2025). *Algorithmic Trading Strategies: A Practical Guide*. https://finzer.io/en/blog/algorithmic-trading-strategies
6. TradeAlgo. (2026). *Swing Trading Risk Management: Position Sizing, Stop Losses, and Portfolio Rules*. https://www.tradealgo.com/trading-guides/stocks/swing-trading-risk-management-position-sizing-stop-losses-and-portfolio-rules
7. ExtremeToMean. (2024). *Mastering Mean Reversion Algo Trading*. https://extremetomean.com/mastering-mean-reversion-algo-trading-a-guide-to-quantitative-strategies-and-algorithms
8. QuestDB. (2026). *Market Regime Change Detection with ML*. https://questdb.com/glossary/market-regime-change-detection-with-ml/
9. QuantInsti. (2026). *Machine Learning for Market Regime Detection Using Random Forest*. https://blog.quantinsti.com/epat-project-machine-learning-market-regime-detection-random-forest-python/
10. LSEG Developers. (2023). *Market Regime Detection Using Statistical and ML Based Approaches*. https://developers.lseg.com/en/article-catalog/article/market-regime-detection
11. QuantStrategy.io. (2026). *The 3 Golden Rules for Pyramiding Success*. https://quantstrategy.io/blog/the-3-golden-rules-for-pyramiding-success-entry-points/
12. Benzinga. (2026). *Best Swing Trading Stocks*. https://www.benzinga.com/pro/blog/best-swing-trading-stocks
13. QuantConnect. (2025). *Walk-Forward Optimization Documentation*. https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization
14. QuantBeckman. (2025). *Walk-Forward CVCL Optimization*. https://www.quantbeckman.com/p/with-code-walk-forward-cvcl-optimization
15. LearnPriceAction. (2021). *Multiple Time Frame Trading Analysis*. https://learnpriceaction.com/wp-content/uploads/2021/04/Multiple-Time-Frame-Trading-Analysis.pdf
16. AlchemyMarkets. (2025). *Mean Reversion Explained*. https://alchemymarkets.com/education/strategies/mean-reversion/

---

## 10. Appendix: Glossary

| Term | Definition |
|------|------------|
| **R-Multiple** | Profit or loss expressed as a multiple of initial risk (1R = hard stop distance). |
| **Portfolio Heat** | Sum of risk exposure across all open positions as a percentage of total equity. |
| **Zone Penetration** | Price action that enters the operator-defined entry range during a bar. |
| **Regime** | The broad market state (bullish/neutral/bearish) as classified by the upstream system. |
| **Parked Watchlist** | Instruments removed from active monitoring due to thesis failure or operator decision. |
| **Hysteresis** | A delay or confirmation buffer applied to signals to prevent whipsaw reactions. |
| **WFO** | Walk-Forward Optimization: rolling-window parameter re-optimization on historical data. |

---

*End of Document — Ready for V1 specification extraction and ticket creation.*
