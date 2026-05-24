# Build Phase 12 — Strategy Enhancement & Dashboard Enrichment (FUTURE)

> **Status:** Planning / Reference Only — NOT for current build  
> **Purpose:** Document what we will add in Phase 12 to close indicator/pattern/strategy gaps identified during QuantConnect Lean analysis.  
> **Prerequisite:** Phases 0–11 complete (Platform fully operational with live trading)  
> **Analysis Source:** QuantConnect Lean comparison conducted May 2026  
> **Rule:** ALL implementations follow NautilusTrader recommended patterns. Zero code copied from Lean. Functional logic only — implemented in Nautilus-native Python.

---

## 1. Missing Indicators — Critical Gap Closure

Nautilus covers ~60% of the top-30 most-used indicators. We identified 10 indicators that are missing and materially impact strategy development. All are "trivially formulaic" — the logic is well-documented, the implementation is straightforward on Nautilus.

### 1.1 Priority Ranking

| # | Indicator | Why Missing Hurts | Complexity | Lean Reference |
|---|-----------|-------------------|------------|---------------|
| 1 | **ADX** (Average Directional Index) | Trend strength filter. Nautilus has `DirectionalMovement` (+DI/-DI only). ADX = smoothed DX. Used in 40%+ of trend strategies. | Low | [AverageDirectionalIndex.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/AverageDirectionalIndex.cs) |
| 2 | **Parabolic SAR** | Trailing stop + trend reversal. Extremely common in breakout/trend-following. | Low | [ParabolicStopAndReverse.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/ParabolicStopAndReverse.cs) |
| 3 | **SuperTrend** | Popular ATR-based trend indicator. Single-line buy/sell signal. Used heavily by retail algos. | Low | [SuperTrend.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SuperTrend.cs) |
| 4 | **Williams %R** | Simple overbought/oversold (-100 to 0). Widely taught, commonly used as confirmation. | Trivial | [WilliamsPercentR.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/WilliamsPercentR.cs) |
| 5 | **MFI** (Money Flow Index) | Volume-weighted RSI. Stronger signal than RSI alone for confirming breakouts. | Low | [MoneyFlowIndex.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/MoneyFlowIndex.cs) |
| 6 | **Heikin-Ashi** | Smoother OHLCV transform. Removes noise, makes trends visually obvious. | Trivial | [HeikinAshi.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/HeikinAshi.cs) |
| 7 | **TRIX** | Triple-smoothed momentum. Filters whipsaws, popular in crypto and mean-reversion. | Low | [Trix.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/Trix.cs) |
| 8 | **ZigZag** | Swing high/low detection. Essential for pattern-based strategies (support/resistance, Elliott Wave, harmonic patterns). | Medium | [ZigZag.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/ZigZag.cs) |
| 9 | **Beta** (rolling) | Market sensitivity. Required for hedging calculations, pairs-trade ratio sizing. | Medium | [Beta.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/Beta.cs) |
| 10 | **Rolling Sharpe / Sortino** | Real-time risk-adjusted return monitoring. Essential for live strategy evaluation dashboard. | Low | [SharpeRatio.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SharpeRatio.cs) · [SortinoRatio.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/SortinoRatio.cs) |

### 1.2 Implementation Pattern (Nautilus-Native)

Nautilus indicators may be compiled (Rust/pyo3). Custom indicators in Python should follow one of these patterns:

**Option A — Python function wrapper:**
```python
def adx(high: list[float], low: list[float], close: list[float], period: int = 14) -> float:
    """Compute ADX from arrays. Use with on_bar() or on_quote_tick()."""
    ...
```

**Option B — If Nautilus supports Python `Indicator` subclass:**
```python
from nautilus_trader.indicators.base import Indicator

class AverageDirectionalIndex(Indicator):
    """ADX trend strength indicator."""
    def __init__(self, period: int = 14):
        super().__init__()
        self._period = period
        ...

    def handle_bar(self, bar):
        """Update from OHLCV bar."""
        ...
```

**Verify at implementation time** which pattern Nautilus v1.227+ supports for custom Python indicators. Refer to:
- Nautilus docs: `docs/indicators.md` (in Nautilus documentation)
- Existing pattern: `sam_trader/strategies/orb.py` for indicator usage pattern

### 1.3 File Structure

```
src/sam_trader/indicators/       # New package
├── __init__.py                   # Re-exports all custom indicators
├── trend.py                      # ADX, SuperTrend, ParabolicSAR, HeikinAshi
├── momentum.py                   # WilliamsR, MFI, TRIX, ZigZag
└── risk.py                       # Beta, RollingSharpe, RollingSortino
```

Tests:
```
tests/unit/indicators/
├── test_trend.py
├── test_momentum.py
└── test_risk.py
```

---

## 2. Candlestick Pattern Recognition — Top 8

Nautilus has ZERO candlestick pattern recognition. Lean has 40+. We need the 8 most-used single/multi-candle patterns.

### 2.1 Top 8 Patterns (Most Useful)

| # | Pattern | Type | Signal | Candle Count | Lean Reference |
|---|---------|------|--------|-------------|---------------|
| 1 | **Doji** | Single | Indecision / potential reversal | 1 | [Doji.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Doji.cs) |
| 2 | **Hammer** | Single | Bullish reversal (downtrend) | 1 | [Hammer.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Hammer.cs) |
| 3 | **Shooting Star** | Single | Bearish reversal (uptrend) | 1 | [ShootingStar.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/ShootingStar.cs) |
| 4 | **Engulfing** | Double | Bullish/Bearish reversal | 2 | [Engulfing.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Engulfing.cs) |
| 5 | **Morning Star** | Triple | Bullish reversal (downtrend bottom) | 3 | [MorningStar.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/MorningStar.cs) |
| 6 | **Evening Star** | Triple | Bearish reversal (uptrend top) | 3 | [EveningStar.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/EveningStar.cs) |
| 7 | **Harami** | Double | Reversal/continuation (bullish/bearish) | 2 | [Harami.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Harami.cs) |
| 8 | **Piercing / Dark Cloud** | Double | Bullish reversal (piercing) / Bearish reversal (dark cloud) | 2 | [Piercing.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/Piercing.cs) · [DarkCloudCover.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/CandlestickPatterns/DarkCloudCover.cs) |

> **Note on InvertedHammer / HangingMan / DragonflyDoji / GravestoneDoji:**  
> These are shape-identical to Hammer/Shooting Star/Doji — only context (trend location) differs.  
> Implement Hammer and Shooting Star as base recognizers; add context checks to classify sub-types.

### 2.2 Recognition Logic Pattern

All patterns share the same structural logic. Implement a base utility class:

```python
# Pattern recognition from OHLCV data
# Format: each pattern is a function that takes list of Bars and returns bool + direction

def is_doji(open_: float, high: float, low: float, close: float, 
            body_threshold: float = 0.05) -> bool:
    """Doji: body <= threshold% of (high-low) range."""
    body = abs(close - open_)
    range_ = high - low
    if range_ == 0:
        return True
    return (body / range_) <= body_threshold

def is_hammer(prev_bars: list, current: Bar, body_ratio: float = 0.3,
              lower_shadow_ratio: float = 2.0) -> int:
    """Hammer in downtrend → bullish reversal signal.
    Returns: 1 = bullish hammer, -1 = hanging man (bearish), 0 = no pattern.
    
    Rules:
      - Small real body at upper end of range
      - Long lower shadow (>= 2x body)
      - Little/no upper shadow
      - Preceded by downtrend (check prev_bars)
    """
    ...
```

### 2.3 File Structure

```
src/sam_trader/indicators/
├── __init__.py
├── candles.py                   # Base candle utilities + single-candle patterns
│     is_doji()                  #   Doji detection + sub-types
│     is_hammer()                #   Hammer / Hanging Man / Inverted Hammer
│     is_shooting_star()         #   Shooting Star
├── candle_patterns.py           # Multi-candle patterns
│     is_engulfing()             #   Bullish / Bearish engulfing
│     is_morning_star()          #   Morning star (3-candle)
│     is_evening_star()          #   Evening star (3-candle)
│     is_harami()                #   Harami (bullish/bearish)
│     is_dark_cloud_cover()      #   Dark cloud cover (bearish)
│     is_piercing_line()         #   Piercing line (bullish)
└── ...
```

Tests:
```
tests/unit/indicators/
├── test_candles.py
└── test_candle_patterns.py
```

---

## 3. Strategy Registry — Top 5 Algorithm Framework Models to Port

From Lean's 32 Framework models (14 Alpha + 10 Portfolio + 3 Execution + 5 Risk), we port the **5 most impactful** as Nautilus `Strategy` subclasses. These are NOT code copies — we extract the functional signal logic and implement it using Nautilus `Strategy` patterns (on_bar, on_quote_tick, submit_order, etc.).

### 3.1 Top 5 — Ranked by Impact

| # | Model | Type | Signal Logic | Lean Reference |
|---|-------|------|-------------|---------------|
| 1 | **RSI Strategy** | Alpha (Signal) | RSI crosses below 30 → BUY bracket; crosses above 70 → SELL bracket. Period configurable. | [RsiAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/RsiAlphaModel.py) |
| 2 | **EMA Cross Strategy** | Alpha (Signal) | Fast EMA crosses above Slow EMA → BUY; crosses below → SELL. Periods configurable. | [EmaCrossAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/EmaCrossAlphaModel.py) |
| 3 | **MACD Strategy** | Alpha (Signal) | MACD line crosses above signal line → BUY; crosses below → SELL. Standard 12/26/9 periods. | [MacdAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py) |
| 4 | **Momentum (Returns) Strategy** | Alpha (Signal) | Rank N symbols by period returns. Buy top K, short bottom K. Period + count configurable. | [HistoricalReturnsAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/HistoricalReturnsAlphaModel.py) |
| 5 | **Bollinger Band Mean Reversion** | Alpha (Signal) | Price touches lower band → BUY (mean reversion up). Price touches upper band → SELL. Period + k configurable. | *Combine [BollingerBands.cs](https://github.com/QuantConnect/Lean/blob/master/Indicators/BollingerBands.cs) with RsiAlphaModel pattern* |

### 3.2 Nautilus Strategy Pattern

Each ported strategy follows this Nautilus-native pattern:

```python
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.model.data import Bar
from dataclasses import dataclass

@dataclass(frozen=True)
class RsiStrategyConfig(StrategyConfig):
    """Configuration for RSI threshold strategy."""
    instrument_id: str
    bar_type: str
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0
    trade_size: int = 100
    # bracket order config inherited from StrategyConfig

class RsiStrategy(Strategy):
    """RSI overbought/oversold strategy — Nautilus-native implementation.
    
    Functional logic from Lean's RsiAlphaModel, implemented per
    Nautilus Strategy subclass pattern (on_bar, submit_order, etc.).
    """
    
    def __init__(self, config: RsiStrategyConfig):
        super().__init__(config)
        self._config = config
        self._rsi = None  # Initialized in on_start()
    
    def on_start(self):
        """Subscribe to bar data, initialize RSI indicator."""
        self.subscribe_bars(self._config.bar_type)
        # Initialize RSI from nautilus_trader.indicators
    
    def on_bar(self, bar: Bar):
        """Process each bar. Check RSI crossing thresholds."""
        ...
        if rsi_value < self._config.oversold:
            self.submit_order(...)  # Buy bracket order
        elif rsi_value > self._config.overbought:
            self.submit_order(...)  # Sell bracket order
```

### 3.3 File Structure

```
src/sam_trader/strategies/registry/    # New package
├── __init__.py
├── rsi_strategy.py                    # RSI threshold strategy
├── ema_cross_strategy.py              # EMA crossover strategy
├── macd_strategy.py                   # MACD signal cross strategy
├── momentum_rank_strategy.py          # Top-K by returns strategy
├── bollinger_mean_reversion.py        # Bollinger band mean reversion
└── _base.py                           # Shared utilities for registry strategies
```

Tests:
```
tests/unit/strategies/registry/
├── test_rsi_strategy.py
├── test_ema_cross_strategy.py
├── test_macd_strategy.py
├── test_momentum_rank_strategy.py
└── test_bollinger_mean_reversion.py
```

Bundle config entries:
```yaml
# config/bundles.yaml — example registry strategy entries
bundles:
  - id: "tsla-rsi-14-futu"
    enabled: false
    venue: FUTU
    strategy:
      path: sam_trader.strategies.registry.rsi_strategy:RsiStrategy
      config:
        instrument_id: "TSLA.NASDAQ"
        bar_type: "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        period: 14
        oversold: 30
        overbought: 70
        trade_size: 10
    bracket:
      stop_loss_ticks: 10
      take_profit_ticks: 30
```

---

## 4. Dashboard — Key Information to Expose

The Phase 10 dashboard (basic HTML) should be extended in Phase 12 with richer analytics inspired by Lean's `Report` module.

### 4.1 Data Sources Already Available (Phase 6–8)

These data points ALREADY exist in our infrastructure. The dashboard just needs to read and display them:

| Data Point | Source | Location | Phase Built |
|------------|--------|----------|-------------|
| Realized P&L (per strategy, per day) | Redis | `sam:pnl:{strategy}:{date}` | Phase 6 (RealizedPnLTrackerActor) |
| Trade fills (all venues) | PostgreSQL | `fills` table (ts_event, instrument_id, venue, side, qty, price, commission) | Phase 6 (TradeJournalActor) |
| Current positions | PostgreSQL | `positions` table (instrument_id, venue, net_qty, avg_px) | Phase 8 (PositionSnapshotActor) |
| Order history | PostgreSQL | `orders` table (status, type, side, qty, price, filled_qty) | Phase 6 (TradeJournalActor) |
| Performance stats | PostgreSQL | `performance_stats` table | Phase 8 (PerformanceAnalyzer) |
| Service health | Docker | `docker inspect` / health check endpoints | Phase 0 |

### 4.2 Dashboard Sections — Tier 1 (Phase 12 Enhancement)

Inspired by Lean's `Report/ReportElements/`. Each section has a reference to the Lean implementation for logic, formula, and rendering ideas.

| Section | What It Shows | Lean Reference | Data Needed | Complexity |
|---------|-------------|---------------|-------------|------------|
| **System Health** | Green/red per service (PG, Redis, Futu, Trader, Services, IB) | N/A (Docker health checks) | Docker inspect | Easy |
| **Equity Curve** | Cumulative P&L over time with benchmark overlay. Line chart. | [CumulativeReturnsReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/CumulativeReturnsReportElement.cs) | PG `fills` → daily P&L aggregation | Easy |
| **Drawdown Chart** | Peak-to-trough drawdown timeline. Red shaded area. | [DrawdownReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/DrawdownReportElement.cs) · [MaxDrawdownReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/MaxDrawdownReportElement.cs) | Equity curve computation | Medium |
| **Performance Summary** | Net P&L, Win Rate, Sharpe (20d), Max DD, Expectancy — top-row KPI cards | [SharpeRatioReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/SharpeRatioReportElement.cs) · [CAGRReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/CAGRReportElement.cs) | PG `fills` + computation | Medium |
| **Current Positions** | Table: symbol, venue, qty, avg px, mark price, unrealized P&L, P&L% | N/A (live positions) | PG `positions` + PG `fills.latest_price` | Easy |
| **Recent Fills** | Last 20 fills: timestamp, symbol, side, qty, price, venue. BUY green, SELL red. | N/A (live fills feed) | PG `fills` ORDER BY ts_event DESC LIMIT 20 | Trivial |
| **Strategy P&L Table** | Per-strategy: realized P&L today, win rate, total trades | N/A (strategy breakdown) | Redis `sam:pnl:{strategy}:{date}` + PG query | Easy |
| **Drawdown Recovery** | Days to recover from each drawdown event. List of DD events. | [MaxDrawdownRecoveryReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/MaxDrawdownRecoveryReportElement.cs) | Drawdown computation | Easy |
| **Total Fees** | Commissions by venue, month-to-date | N/A (fee summary) | PG `fills.commission` SUM | Trivial |

### 4.3 Dashboard Sections — Tier 2 (Phase 12+)

| Section | What It Shows | Lean Reference | Complexity |
|---------|-------------|---------------|------------|
| **Monthly Returns Heatmap** | Calendar-grid heatmap (green = profit, red = loss) | [MonthlyReturnsReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/MonthlyReturnsReportElement.cs) | Medium |
| **Annual Returns** | Year-by-year return bar chart | [AnnualReturnsReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/AnnualReturnsReportElement.cs) | Easy |
| **Rolling Sharpe** | 20-day rolling Sharpe ratio line chart | [RollingSharpeReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/RollingSharpeReportElement.cs) | Medium |
| **Rolling Beta** | 20-day rolling beta vs benchmark line chart | [RollingPortfolioBetaReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/RollingPortfolioBetaReportElement.cs) | Medium (needs benchmark data) |
| **Asset Allocation** | Pie/donut chart: position sizing by instrument | [AssetAllocationReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/AssetAllocationReportElement.cs) | Easy |
| **Trades Per Day** | Bar chart: number of trades by day/week | [TradesPerDayReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/TradesPerDayReportElement.cs) | Easy |
| **Returns Per Trade** | Histogram of individual trade P&L | [ReturnsPerTradeReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/ReturnsPerTradeReportElement.cs) | Medium |
| **Exposure Over Time** | Long/short ratio time series | [ExposureReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/ExposureReportElement.cs) | Medium |
| **Sortino Ratio** | Downside-only risk-adjusted return | [SortinoRatioReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/SortinoRatioReportElement.cs) | Easy |
| **Information Ratio** | Excess return per unit of tracking error | [InformationRatioReportElement.cs](https://github.com/QuantConnect/Lean/blob/master/Report/ReportElements/InformationRatioReportElement.cs) | Medium |

### 4.4 Dashboard API Endpoints

The Phase 10 dashboard serves static HTML. Phase 12 extends it with these JSON API endpoints:

```
sam-services (port 8080)

Tier 1 (Phase 12):
  GET /api/equity-curve?days=30     → [{date, equity, benchmark}]          # Daily equity points
  GET /api/drawdown                 → {current_dd_pct, max_dd_pct, events: [{start, end, depth, recovery_days}]}
  GET /api/performance              → {net_pnl, win_rate, sharpe_20d, expectancy, total_fees, total_trades}
  GET /api/strategy-pnl             → [{strategy_id, pnl_today, win_rate, trades}]
  GET /api/positions               → [{symbol, venue, qty, avg_px, mark, unrealized_pnl, pnl_pct}]

Tier 2 (Phase 12+):
  GET /api/monthly-returns          → [[{year, month, return_pct}]]        # Calendar heatmap data
  GET /api/rolling-sharpe?window=20 → [{date, sharpe}]                     # Rolling risk metric
  GET /api/asset-allocation         → [{symbol, weight_pct, market_value}] # Pie chart data
  GET /api/trade-distribution       → [{pnl_bucket, count}]                # Returns-per-trade histogram
  GET /api/exposure                 → [{date, long_pct, short_pct}]        # Long/short over time
```

### 4.5 `sam report` CLI Command

Inspired by Lean's `Report.cs`, add a report generation command:

```bash
sam report                    # Generate 30-day HTML performance report
sam report --days 60          # Custom lookback
sam report --json             # Machine-readable JSON output
sam report --compare-backtest # Overlay backtest equity vs live
sam report --strategy orb-15m # Single-strategy report
```

---

## 5. Summary — Phase 12 Deliverables

| Deliverable | Items | Effort Estimate | Dependencies |
|-------------|-------|----------------|-------------|
| **Indicators** | 10 missing indicators (ADX, ParabolicSAR, SuperTrend, WilliamsR, MFI, HeikinAshi, TRIX, ZigZag, Beta, RollingSharpe/Sortino) | 15–20 hours | None (formula-based) |
| **Candlestick Patterns** | 8 patterns (Doji, Hammer, ShootingStar, Engulfing, MorningStar, EveningStar, Harami, Piercing/DarkCloud) + base utilities | 10–15 hours | Bar data from data engine |
| **Strategy Registry** | 5 ported strategies (RSI, EMA Cross, MACD, Momentum Rank, Bollinger MR) as Nautilus Strategy subclasses | 15–25 hours | Phase 9 bundle system, indicator library |
| **Dashboard Tier 1** | 9 sections: health, equity, drawdown, performance, positions, fills, strategy P&L, drawdown recovery, fees | 20–30 hours | Phase 10 dashboard, PG/Redis data |
| **Dashboard Tier 2** | 8 sections: monthly heatmap, annual returns, rolling Sharpe, rolling beta, allocation, trades/day, trade distribution, exposure | 20–30 hours | Tier 1 complete |
| **CLI Report** | `sam report` command with HTML/JSON output | 10–15 hours | Dashboard API |
| **Tests** | Unit + integration tests for all above | 15–25 hours | Per-deliverable |
| **TOTAL** | | **105–160 hours** | Phases 0–11 complete |

---

## 6. Reference Index

### 6.1 Lean Source Files (Algorithm Framework — the 5 we port)

| Model | Lean Source |
|-------|------------|
| RSI Alpha | [RsiAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/RsiAlphaModel.py) |
| EMA Cross Alpha | [EmaCrossAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/EmaCrossAlphaModel.py) |
| MACD Alpha | [MacdAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py) |
| Historical Returns Alpha | [HistoricalReturnsAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/HistoricalReturnsAlphaModel.py) |

### 6.2 Lean Source Files (Remaining Framework Models — for future reference)

| Model | Lean Source |
|-------|------------|
| Base Pairs Trading | [BasePairsTradingAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/BasePairsTradingAlphaModel.py) |
| Pearson Pairs Trading | [PearsonCorrelationPairsTradingAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/PearsonCorrelationPairsTradingAlphaModel.py) |
| Constant Alpha | [ConstantAlphaModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/ConstantAlphaModel.py) |
| Equal Weighting PC | [EqualWeightingPortfolioConstructionModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Portfolio/EqualWeightingPortfolioConstructionModel.py) |
| Confidence Weighted PC | [ConfidenceWeightedPortfolioConstructionModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Portfolio/ConfidenceWeightedPortfolioConstructionModel.py) |
| Risk Parity PC | [RiskParityPortfolioConstructionModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Portfolio/RiskParityPortfolioConstructionModel.py) |
| Max Drawdown Risk (per security) | [MaximumDrawdownPercentPerSecurity.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/MaximumDrawdownPercentPerSecurity.py) |
| Max Drawdown Risk (portfolio) | [MaximumDrawdownPercentPortfolio.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/MaximumDrawdownPercentPortfolio.py) |
| Trailing Stop Risk | [TrailingStopRiskManagementModel.py](https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Risk/TrailingStopRiskManagementModel.py) |

### 6.3 Lean Performance Metrics (Report Elements)

Full list: [Report/ReportElements/](https://github.com/QuantConnect/Lean/tree/master/Report/ReportElements)

| Report Element | Source |
|---------------|--------|
| Statistics Builder (all formulas) | [StatisticsBuilder.cs](https://github.com/QuantConnect/Lean/blob/master/Common/Statistics/StatisticsBuilder.cs) |
| Performance Metrics (metric names) | [PerformanceMetrics.cs](https://github.com/QuantConnect/Lean/blob/master/Common/Statistics/PerformanceMetrics.cs) |
| Report Engine (HTML generation) | [Report.cs](https://github.com/QuantConnect/Lean/blob/master/Report/Report.cs) |
| Report Template (HTML structure) | [template.html](https://github.com/QuantConnect/Lean/blob/master/Report/template.html) |

### 6.4 SAM Trader Reference Docs

| Doc | Path |
|-----|------|
| SAM Trader V3 Plan | `docs/reference/SAM_TRADER_V3_PLAN.md` |
| Build Phase 9 (Pre-Market) | `docs/reference/BUILD_PHASE_9.md` |
| Build Phase 10 (Safety & Dashboard) | `docs/reference/BUILD_PHASE_10.md` |
| QuantConnect Analysis | `~/Documents/ai_agent_docs/quantconnect_lean_analysis.html` |

### 6.5 NautilusTrader Extension Points

> **⚠️ Verify at implementation time** — Nautilus APIs may change between versions.

| Extension | Nautilus Pattern | Reference |
|-----------|-----------------|-----------|
| Custom indicator | Subclass `Indicator` or function-based | Check Nautilus docs for `indicators` module |
| Strategy subclass | `from nautilus_trader.trading.strategy import Strategy` | See `sam_trader/strategies/orb.py` for pattern |
| Strategy config | `@dataclass(frozen=True)` subclass of `StrategyConfig` | See OrbStrategy pattern |
| Order submission | `self.submit_order()` / `self.submit_order_list()` | Nautilus Strategy API |
| Bracket orders | `order_factory.bracket()` pattern | See bundle config `bracket:` section |

---

*Last updated: 2026-05-24 — Phase 12 planning reference. NOT for current build cycle.*  
*All Lean references point to exact source files for implementation-time lookup. ZERO code shall be copied — functional logic only, implemented per Nautilus patterns.*
