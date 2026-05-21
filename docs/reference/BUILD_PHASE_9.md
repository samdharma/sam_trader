# Build Phase 9 — Pre-Market Pipeline

> **Status:** Not Started  
> **Goal:** Gap scanner → AI analysis → risk manager → bundle generator → readiness report. Full autonomous pre-market pipeline.  
> **Prev Phase:** [BUILD_PHASE_8.md](./BUILD_PHASE_8.md) — sam-services Container  
> **Next Phase:** [BUILD_PHASE_10.md](./BUILD_PHASE_10.md) — Safety & Dashboard

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    Pre-Market Pipeline                        │
│                    (runs inside sam-services)                 │
├──────────────────────────────────────────────────────────────┤
│  1. Gap Scanner                                               │
│     └── Scan Futu pre-market data for gap candidates         │
│     └── Filters: threshold, blacklist, trend-down            │
├──────────────────────────────────────────────────────────────┤
│  2. AI Scoring Engine                                         │
│     └── LLM evaluation of candidates                         │
│     └── Grades: STRONG_BUY, BUY, HOLD, SKIP                  │
├──────────────────────────────────────────────────────────────┤
│  3. Risk Manager                                              │
│     └── Monte Carlo position sizer                           │
│     └── Pre-trade checks (exposure, daily loss, margin)      │
│     └── Portfolio heat monitor                               │
├──────────────────────────────────────────────────────────────┤
│  4. Market Regime Detection                                   │
│     └── HMM classifier (trending, ranging, volatile)         │
│     └── Regime-aware parameter adaptation                    │
├──────────────────────────────────────────────────────────────┤
│  5. Orchestrator                                              │
│     └── Sequential: scan → AI → risk → regime → bundles      │
│     └── Sanity check → Bundle YAML generator                 │
│     └── Readiness report (console + webhook)                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Ticket Breakdown

| Ticket | Title | Scope | Assessment |
|--------|-------|-------|------------|
| `sam-p9-gapscan` | Gap scanner | Pre-market gap scan, filter rules | ✅ Medium |
| `sam-p9-ai` | AI scoring engine | LLM candidate evaluation | ✅ Medium |
| `sam-p9-risk` | Risk manager | Monte Carlo + pre-trade checks + heat monitor | ⚠️ **LARGE** — 3 major components |
| `sam-p9-regime` | Market regime detection | HMM classifier + adaptation | ✅ Medium |
| `sam-p9-orch` | Pipeline orchestrator | Sequential executor + bundle creator + report | ⚠️ **LARGE** — 4 sub-components |
| `sam-p9-verify` | Verify pipeline | End-to-end integration test | ✅ Medium |

### 2.1 Decomposition: `sam-p9-risk`

Decompose into:

| New Ticket | Title | Scope | Depends On |
|------------|-------|-------|------------|
| `sam_trader-9z3.10.7` | Monte Carlo position sizer | Position size simulation | `sam-p9-gapscan` |
| `sam_trader-9z3.10.8` | Pre-trade risk checks | Max exposure, daily loss, margin checks | `sam_trader-9z3.10.7` |
| `sam_trader-9z3.10.9` | Portfolio heat monitor | Real-time heat tracking | `sam_trader-9z3.10.8` |

### 2.2 Decomposition: `sam-p9-orch`

Decompose into:

| New Ticket | Title | Scope | Depends On |
|------------|-------|-------|------------|
| `sam_trader-9z3.10.10` | Pipeline sequential executor | Run scan → AI → risk → regime in order | `sam_trader-9z3.10.9`, `sam-p9-regime` |
| `sam_trader-9z3.10.11` | Bundle YAML generator | Convert candidates to valid bundle YAML | `sam_trader-9z3.10.10` |
| `sam_trader-9z3.10.12` | Readiness report | Console + webhook notification | `sam_trader-9z3.10.11` |

---

## 3. Key Design Notes

### 3.1 Gap Scanner Input

- Source: Futu pre-market data (via `FutuLiveDataClient` if already connected, or cached previous close)
- Time: 09:15–09:30 ET (pre-market window)
- Threshold: configurable % gap (default 2%)

### 3.2 AI Scoring Prompt Template

```
Candidate: {symbol}
Gap: {gap_pct}%
Volume ratio: {volume_ratio}
News sentiment: {sentiment}
Market regime: {regime}

Grade this trade opportunity as STRONG_BUY, BUY, HOLD, or SKIP.
Provide 3 bullet points of reasoning.
```

### 3.3 Monte Carlo Sizer

```python
import numpy as np

def monte_carlo_size(
    capital: float,
    risk_per_trade: float,  # e.g., 0.01 = 1%
    stop_loss_pct: float,
    num_simulations: int = 10_000,
) -> float:
    returns = np.random.normal(loc=0, scale=daily_volatility, size=num_simulations)
    var_95 = np.percentile(returns, 5)
    max_risk_dollars = capital * risk_per_trade
    position_size = max_risk_dollars / (capital * stop_loss_pct)
    return position_size
```

---

*Last updated: 2026-05-21*
