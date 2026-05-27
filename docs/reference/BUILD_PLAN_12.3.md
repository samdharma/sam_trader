# BUILD_PLAN 12.3 — Strategy Library & Analytics Enrichment

> **Status:** Planning  
> **Goal:** Close indicator/pattern/strategy gaps identified during Lean analysis. Add 10 missing indicators, 8 candlestick patterns, 10 strategy implementations (5 Lean ports + 5 Nautilus-native wrappers), dashboard analytics enrichment (Tier 1 + Tier 2), and `sam report` CLI.  
> **Gates on:** Phase 11 EXIT — `sam_trader-9z3.12.9` (independent of 12.1/12.2)  
> **Rule:** ALL implementations follow NautilusTrader patterns. Zero code copied from Lean. Functional logic only — implemented Nautilus-native.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│              12.3 — Strategy Library & Analytics              │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────────────┐  ┌──────────────────────┐            │
│  │ 10 Indicators       │  │ 8 Candle Patterns    │            │
│  │ (src/indicators/)   │  │ (src/indicators/)    │            │
│  │                     │  │                      │            │
│  │ ADX, ParabolicSAR,  │  │ Doji, Hammer,        │            │
│  │ SuperTrend, W%R,    │  │ ShootingStar,        │            │
│  │ MFI, HeikinAshi,    │  │ Engulfing, Morning/  │            │
│  │ TRIX, ZigZag, Beta, │  │ EveningStar, Harami, │            │
│  │ RollingSharpe/Sortino│ │ Piercing, DarkCloud  │            │
│  └────────┬───────────┘  └──────────┬───────────┘            │
│           │                         │                         │
│           └──────────┬──────────────┘                         │
│                      ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ 10 Strategy Implementations (src/strategies/registry/)   │ │
│  │                                                          │ │
│  │ Lean ports (5): RSI, EMA Cross, MACD, Momentum Rank,    │ │
│  │                 Bollinger MR                             │ │
│  │ Nautilus-native (5): SuperTrend, VWAP Rev, Donchian,    │ │
│  │                      Stochastic, Z-Score MR              │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Dashboard Analytics (sam-services:8080)                  │ │
│  │                                                          │ │
│  │ Tier 1: Equity curve, drawdown, KPI cards, positions,   │ │
│  │         fills, strategy P&L, drawdown recovery, fees     │ │
│  │ Tier 2: Monthly heatmap, annual returns, rolling Sharpe/ │ │
│  │         Beta, allocation, trades/day, trade distribution │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ sam report CLI — HTML/JSON performance reports           │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Pre-Discovered — What Nautilus Already Has

### 2.1 Native Indicators (no custom code needed)

| Indicator | Nautilus Class | Used By |
|-----------|---------------|---------|
| RSI | `nautilus_trader.indicators.rsi.RelativeStrengthIndex` | RSI Strategy, MFI |
| EMA | `nautilus_trader.indicators.ema.ExponentialMovingAverage` | EMA Cross, MACD |
| SMA | `nautilus_trader.indicators.sma.SimpleMovingAverage` | Z-Score MR |
| MACD | `nautilus_trader.indicators.macd.MovingAverageConvergenceDivergence` | MACD Strategy |
| ATR | `nautilus_trader.indicators.atr.AverageTrueRange` | SuperTrend, ORB |
| VWAP | `nautilus_trader.indicators.vwap.VolumeWeightedAveragePrice` | VWAP Reversion |
| DonchianChannel | `nautilus_trader.indicators.donchian_channel.DonchianChannel` | Donchian Breakout |
| Stochastics | `nautilus_trader.indicators.stochastics.StochasticOscillator` | Stochastic Strategy |
| BollingerBands | `nautilus_trader.indicators.bollinger_bands.BollingerBands` | Bollinger MR |
| StandardDeviation | `nautilus_trader.indicators.stddev.StandardDeviation` | Z-Score MR |
| RateOfChange | `nautilus_trader.indicators.roc.RateOfChange` | Momentum strategy |
| ParabolicSAR | `nautilus_trader.indicators.parabolic_sar.ParabolicSAR` | Gap-strategy trailing |
| KeltnerChannel | `nautilus_trader.indicators.keltner_channel.KeltnerChannel` | Future use |
| DirectionalMovement | `nautilus_trader.indicators.directional_movement.DirectionalMovement` | ADX base (+DI/-DI) |
| IchimokuCloud | `nautilus_trader.indicators.ichimoku.IchimokuCloud` | Future use |

### 2.2 Indicators That Need Custom Implementation (10 total)

> **⚠️ Verify at build time** — check if any of these were added to Nautilus since v1.227.

| # | Indicator | Inputs | Formula Complexity | Lean Reference (logic only) |
|---|-----------|--------|-------------------|---------------------------|
| 1 | **ADX** | +DI, -DI from DirectionalMovement | ADX = smoothed DX where DX = abs(+DI - -DI)/(+DI + -DI) × 100 | [AverageDirectionalIndex.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/AverageDirectionalIndex.cs) |
| 2 | **SuperTrend** | ATR, HL/2 | Upper/Lower = HL/2 ± (multiplier × ATR); flips on close cross | [SuperTrend.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SuperTrend.cs) |
| 3 | **Williams %R** | H, L, C, period | %R = (HighestHigh - Close) / (HighestHigh - LowestLow) × -100 | [WilliamsPercentR.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/WilliamsPercentR.cs) |
| 4 | **MFI** | H, L, C, V, period | TypicalPrice, RawMoneyFlow, MoneyRatio → MFI = 100 - 100/(1+MoneyRatio) | [MoneyFlowIndex.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/MoneyFlowIndex.cs) |
| 5 | **Heikin-Ashi** | O, H, L, C | HA-Close = (O+H+L+C)/4; HA-Open = (prev HA-Open + prev HA-Close)/2 | [HeikinAshi.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/HeikinAshi.cs) |
| 6 | **TRIX** | C, period | Triple-smoothed EMA of log prices → rate of change | [Trix.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/Trix.cs) |
| 7 | **ZigZag** | H, L, deviation% | Swing high/low detection by price deviation threshold | [ZigZag.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/ZigZag.cs) |
| 8 | **Beta** | symbol returns, benchmark returns, period | Covariance(symbol, benchmark) / Variance(benchmark) | [Beta.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/Beta.cs) |
| 9 | **Rolling Sharpe** | returns, period, risk_free | (MeanReturn - RFR) / StdDevReturn × sqrt(252) | [SharpeRatio.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SharpeRatio.cs) |
| 10 | **Rolling Sortino** | returns, period, risk_free | (MeanReturn - RFR) / DownsideDeviation × sqrt(252) | [SortinoRatio.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SortinoRatio.cs) |

### 2.3 Indicator Implementation Pattern

```python
# Option A — Function-based (preferred for simple formulas):
def adx(high: list[float], low: list[float], close: list[float], period: int = 14) -> float:
    """ADX from +DI/-DI arrays. Call on_bar() or from Nautilus DirectionalMovement output."""
    ...

# Option B — Nautilus Indicator subclass (if supported in v1.227+):
from nautilus_trader.indicators.base import Indicator

class AverageDirectionalIndex(Indicator):
    def __init__(self, period: int = 14):
        super().__init__()
        self._period = period
        self._dm = DirectionalMovement(period)
        ...

    def handle_bar(self, bar: Bar) -> None:
        self._dm.handle_bar(bar)
        # Compute ADX from smoothed +DI/-DI
        ...
```

> **Verify at build time**: Check Nautilus docs for the correct Python indicator extension point.

---

## 3. Candlestick Pattern Recognition — Top 8

### 3.1 Pattern Reference

| # | Pattern | Candles | Signal | Logic Key |
|---|---------|---------|--------|-----------|
| 1 | **Doji** | 1 | Indecision | `abs(close-open) / (high-low) ≤ threshold` |
| 2 | **Hammer** | 1 | Bullish rev | Small real body at upper end, long lower shadow ≥ 2× body, preceded by downtrend |
| 3 | **Shooting Star** | 1 | Bearish rev | Small body at lower end, long upper shadow ≥ 2× body, preceded by uptrend |
| 4 | **Engulfing** | 2 | Reversal | Body2 completely engulfs body1, opposite color |
| 5 | **Morning Star** | 3 | Bullish rev | Long bearish → small body gaps down → long bullish closes into body1 |
| 6 | **Evening Star** | 3 | Bearish rev | Long bullish → small body gaps up → long bearish closes into body1 |
| 7 | **Harami** | 2 | Reversal | Body2 completely inside body1, opposite color |
| 8 | **Dark Cloud / Piercing** | 2 | Bearish/Bullish | Dark Cloud: bullish then bearish closes >50% into prior body; Piercing: opposite |

**Sub-type note:** InvertedHammer, HangingMan, DragonflyDoji, GravestoneDoji are shape-identical to Hammer/ShootingStar/Doji — only context (trend position) differs. Implement base recognizers; add context checks for sub-types.

### 3.2 Recognition Logic Pattern

```python
# Each pattern is a function taking Bars + context → bool + direction

def is_doji(open_: float, high: float, low: float, close: float,
            body_threshold: float = 0.05) -> bool:
    body = abs(close - open_)
    candle_range = high - low
    if candle_range == 0:
        return True
    return (body / candle_range) <= body_threshold


def is_engulfing(prev_open, prev_high, prev_low, prev_close,
                 curr_open, curr_high, curr_low, curr_close) -> int:
    """Returns: 1=bullish engulfing, -1=bearish engulfing, 0=no pattern."""
    prev_body = abs(prev_close - prev_open)
    curr_body = abs(curr_close - curr_open)
    prev_bullish = prev_close > prev_open
    curr_bullish = curr_close > curr_open

    if prev_bullish == curr_bullish:
        return 0

    # Current body must fully engulf previous body
    if curr_open <= prev_open and curr_close >= prev_close and curr_bullish:
        return 1   # Bullish engulfing
    if curr_open >= prev_open and curr_close <= prev_close and not curr_bullish:
        return -1  # Bearish engulfing
    return 0
```

---

## 4. Strategy Implementation Pattern (from Phase 7)

> **Source:** `sam_trader/strategies/orb.py` — the canonical Nautilus strategy pattern.

```python
from dataclasses import dataclass
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders import OrderSide, MarketOrder
from nautilus_trader.model.objects import Quantity, Price


@dataclass(frozen=True)
class RsiStrategyConfig(StrategyConfig):
    instrument_id: str
    bar_type: str
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0
    trade_size: int = 100


class RsiStrategy(Strategy):
    def __init__(self, config: RsiStrategyConfig) -> None:
        super().__init__(config)
        self._config = config
        self._instrument_id = InstrumentId.from_str(config.instrument_id)
        self._rsi = None

    def on_start(self) -> None:
        self.subscribe_bars(self._config.bar_type)
        self._rsi = RelativeStrengthIndex(self._config.period)

    def on_bar(self, bar: Bar) -> None:
        self._rsi.handle_bar(bar)
        rsi_value = self._rsi.value

        if rsi_value < self._config.oversold and not self.is_position_open:
            self._submit_bracket(OrderSide.BUY)
        elif rsi_value > self._config.overbought and self.is_position_open:
            self._submit_bracket(OrderSide.SELL)

    def _submit_bracket(self, side: OrderSide) -> None:
        bracket = self.order_factory.bracket(
            instrument_id=self._instrument_id,
            order_side=side,
            quantity=Quantity.from_int(self._config.trade_size),
            entry=Price.from_str("..."),
            stop_loss=Price.from_str("..."),
            take_profit=Price.from_str("..."),
        )
        self.submit_order_list(bracket)
```

### 4.1 10 Strategies — Implementation Matrix

| # | Strategy | Type | Config Fields | Custom Indicators Needed | Lines |
|---|----------|------|--------------|--------------------------|-------|
| 1 | **RSI** | Mean Rev | period, oversold, overbought | None (RSI native) | ~30 |
| 2 | **EMA Cross** | Trend | fast_period, slow_period | None (EMA native) | ~30 |
| 3 | **MACD** | Trend | fast, slow, signal periods | None (MACD native) | ~40 |
| 4 | **Momentum Rank** | Momentum | lookback, top_k, universe | None (ROC native) | ~60 |
| 5 | **Bollinger MR** | Mean Rev | period, stddev_mult | None (BollingerBands native) | ~35 |
| 6 | **SuperTrend** | Trend | atr_period, multiplier | SuperTrend (custom §2.2 #2) | ~30 |
| 7 | **VWAP Reversion** | Mean Rev | stdev_bands, session_reset | None (VWAP native) | ~40 |
| 8 | **Donchian** | Trend | period | None (DonchianChannel native) | ~25 |
| 9 | **Stochastic** | Mean Rev | %K, %D periods, thresholds | None (Stochastics native) | ~35 |
| 10 | **Z-Score MR** | Mean Rev | ma_period, entry_z | None (SMA + StdDev native) | ~25 |

**Bundle config entries** follow the same `bundles.yaml` pattern as Phase 7 (see §2.1 of BUILD_PLAN_12.2.md).

---

## 5. Dashboard Analytics — Data Sources (Already Exist)

> **Zero new data writers.** The dashboard reads existing Phase 6/8 data.

| Data Point | Source | Location | Phase Built |
|------------|--------|----------|-------------|
| Realized P&L (per strategy, per day) | Redis | `sam:pnl:{strategy}:{date}` | Phase 6 (RealizedPnLTrackerActor) |
| Trade fills (all venues) | PostgreSQL | `fills` table | Phase 6 (TradeJournalActor) |
| Current positions | PostgreSQL | `positions` table | Phase 8 (PositionSnapshotActor) |
| Order history | PostgreSQL | `orders` table | Phase 6 (TradeJournalActor) |
| Performance stats | PostgreSQL | `performance_stats` table | Phase 8 (PerformanceAnalyzer) |
| Service health | Docker | `docker inspect` / health endpoints | Phase 0 |

### 5.1 Computed Metrics (from existing data)

| Metric | Formula | Source Data |
|--------|---------|-------------|
| Equity curve | Cumulative sum of daily realized P&L | `fills` → daily P&L aggregation |
| Drawdown | Peak equity − current equity | Equity curve |
| Drawdown recovery | Days from trough to new peak | Equity curve |
| Win Rate | winning_trades / total_trades | `fills` grouped by trade |
| Sharpe (20d) | (Mean daily return − RFR) / StdDev daily return × √252 | Daily P&L series |
| Expectancy | (WinRate × AvgWin) − (LossRate × AvgLoss) | `fills` per trade |
| Monthly returns | Sum of daily P&L per calendar month | `fills` |
| Rolling Sharpe/Beta | 20-day window computation | Daily P&L + benchmark |
| Trade distribution | Histogram of per-trade P&L | `fills` grouped by trade_id |
| Exposure | sum(abs(position_value)) / equity | `positions` over time |

---

## 6. Dashboard API Endpoints (12.3)

```
Tier 1:
  GET  /api/equity-curve?days=30       → [{date, equity}]
  GET  /api/drawdown                   → {current_dd_pct, max_dd_pct, events: [...]}
  GET  /api/performance                → {net_pnl, win_rate, sharpe_20d, expectancy, total_fees, total_trades}
  GET  /api/strategy-pnl               → [{strategy_id, pnl_today, win_rate, trades}]
  GET  /api/positions                  → [{symbol, venue, qty, avg_px, mark, unrealized_pnl, pnl_pct}]

Tier 2:
  GET  /api/monthly-returns            → [[{year, month, return_pct}]]
  GET  /api/rolling-sharpe?window=20   → [{date, sharpe}]
  GET  /api/asset-allocation           → [{symbol, weight_pct, market_value}]
  GET  /api/trade-distribution         → [{pnl_bucket, count}]
  GET  /api/exposure                   → [{date, long_pct, short_pct}]
```

### 6.1 `sam report` CLI

```bash
sam report                           # 30-day HTML performance report
sam report --days 60                 # Custom lookback
sam report --json                    # Machine-readable JSON
sam report --compare-backtest        # Overlay backtest equity vs live
sam report --strategy orb-15m        # Single-strategy report
```

---

## 7. File Structure — New

```
src/sam_trader/
├── indicators/                        # NEW package
│   ├── __init__.py
│   ├── trend.py                       # ADX, SuperTrend, ParabolicSAR (native), HeikinAshi
│   ├── momentum.py                    # WilliamsR, MFI, TRIX, ZigZag
│   ├── risk.py                        # Beta, RollingSharpe, RollingSortino
│   ├── candles.py                     # Doji, Hammer, ShootingStar + base utilities
│   └── candle_patterns.py             # Engulfing, Stars, Harami, DarkCloud, Piercing
│
├── strategies/
│   └── registry/                      # NEW package (strategy implementations)
│       ├── __init__.py
│       ├── _base.py                   # Shared: bracket orders, risk checks
│       ├── rsi_strategy.py
│       ├── ema_cross_strategy.py
│       ├── macd_strategy.py
│       ├── momentum_rank_strategy.py
│       ├── bollinger_mean_reversion.py
│       ├── supertrend_strategy.py
│       ├── vwap_reversion_strategy.py
│       ├── donchian_breakout_strategy.py
│       ├── stochastic_strategy.py
│       └── zscore_reversion_strategy.py

sam-services/
└── dashboard/
    └── analytics/                     # NEW — Tier 1 + Tier 2 panels

tests/
├── unit/indicators/
│   ├── test_trend.py
│   ├── test_momentum.py
│   ├── test_risk.py
│   ├── test_candles.py
│   └── test_candle_patterns.py
└── unit/strategies/registry/
    ├── test_rsi_strategy.py
    ├── test_ema_cross_strategy.py
    ├── test_macd_strategy.py
    ├── test_momentum_rank_strategy.py
    ├── test_bollinger_mean_reversion.py
    ├── test_supertrend_strategy.py
    ├── test_vwap_reversion_strategy.py
    ├── test_donchian_breakout_strategy.py
    ├── test_stochastic_strategy.py
    └── test_zscore_reversion_strategy.py
```

---

## 8. Key Test Scenarios

### 8.1 Indicators

| # | Test | Validates |
|---|------|-----------|
| 1 | ADX vs known values (flat market → 0, strong trend → >40) | Formula correctness |
| 2 | SuperTrend flips direction on price cross | Entry signal generation |
| 3 | Heikin-Ashi bar construction from raw OHLCV | Smoothed bar values correct |
| 4 | ZigZag detects swing highs/lows at correct deviation % | Pattern detection input |
| 5 | Rolling Sharpe over known return series | Risk metric calculation |

### 8.2 Candlestick Patterns

| # | Test | Validates |
|---|------|-----------|
| 6 | Doji detected when body ≤ 5% of range | Threshold behavior |
| 7 | Hammer detected only in downtrend context | Context requirement |
| 8 | Engulfing: bearish rejected when same-color bodies | Direction check |
| 9 | Morning Star: 3-candle sequence matches exact pattern | Multi-candle logic |
| 10 | Piercing: >50% penetration into prior body | Threshold validation |

### 8.3 Strategies

| # | Test | Validates |
|---|------|-----------|
| 11 | RSI strategy buys on oversold, sells on overbought | Signal logic |
| 12 | EMA cross enters on crossover | Entry timing |
| 13 | Momentum rank selects top K by returns | Ranking logic |
| 14 | Z-Score MR enters at ±2σ, exits at mean | Reversion logic |
| 15 | All 10 strategies pass `bundle_validation.py` smoke test | Integration with bundle system |

### 8.4 Dashboard & Report

| # | Test | Validates |
|---|------|-----------|
| 16 | `/api/equity-curve` returns correct daily equity from fills | P&L aggregation |
| 17 | `/api/drawdown` detects peak-to-trough events | Drawdown computation |
| 18 | `/api/performance` computes Sharpe from daily P&L | Risk metric accuracy |
| 19 | `sam report` generates valid HTML with all sections | Report generation |
| 20 | `sam report --json` output is parseable | Machine-readable output |

---

## 9. Lean Reference Index (Functional Logic Only)

> **⛔ ZERO code copying.** These are references to understand functional signal logic. All implementations are Nautilus-native Python.

| Subject | Lean Source |
|---------|------------|
| RSI Alpha signals | [RsiAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/RsiAlphaModel.py) |
| EMA Cross signals | [EmaCrossAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/EmaCrossAlphaModel.py) |
| MACD Alpha signals | [MacdAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py) |
| Momentum Rank signals | [HistoricalReturnsAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/HistoricalReturnsAlphaModel.py) |
| Report template (HTML) | [template.html](https://github.com/QuantConnect/Lean/blob/master/Report/template.html) |
| Statistics formulas | [StatisticsBuilder.cs](https://github.com/QuantConnect/Lean/blob/master/Common/Statistics/StatisticsBuilder.cs) |
| Metric names | [PerformanceMetrics.cs](https://github.com/QuantConnect/Lean/blob/master/Common/Statistics/PerformanceMetrics.cs) |

---

*Last updated: 2026-05-27 — created from BUILD_PHASE_12_FUTURE.md §3*
