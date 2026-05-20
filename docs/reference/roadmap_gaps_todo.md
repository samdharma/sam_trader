# CSAM Trader V2 — Roadmap Gaps & TODO

> **Date:** 2026-05-18  
> **Context:** Phases 0–6 are complete. This document captures what is missing versus a full autonomous trading and portfolio management system. It is intended to guide detailed requirements for subsequent phases.

---

## ✅ What We Have (Phases 0–6)

| Layer | Components |
|-------|-----------|
| **Execution** | IB Gateway, Nautilus TradingNode, data + exec clients, bracket orders |
| **Strategy Engine** | YAML bundle loader, ORB / Momentum strategies, backtest-to-live parity |
| **Infrastructure** | PostgreSQL (fills/orders/positions), Redis (state cache), Parquet (historical catalog) |
| **DevOps** | Single-script deploy, graceful restart with state persistence, health checks |
| **Basic Monitoring** | TradeJournalActor, HealthMonitorActor |

---

## ❌ Key Gaps for an Autonomous Trading System

### 1. Portfolio Management *(Critical — mostly missing)*
- **No real-time P&L / NAV tracking.** Postgres stores fills, but there is no running equity curve or open P&L calculator.
- **No position aggregation across bundles.** Each strategy is siloed; you cannot see total exposure per symbol across all strategies.
- **No rebalancing engine.** No logic to trim winners, add to losers, or maintain target weights.
- **No capital allocation.** Pre-market docs describe an AI Portfolio Manager, but it is not implemented.

### 2. Risk Management *(Critical — rudimentary)*
- **Per-bundle risk only.** Risk limits are static YAML fields (`max_position`, `max_daily_loss`). No portfolio-level heat monitor.
- **No dynamic position sizing.** No Kelly criterion, volatility targeting, or ATR-based sizing.
- **No circuit breaker / kill switch at runtime.** Pre-market docs mention one, but live Nautilus has no emergency halt actor.
- **No pre-trade risk gate.** Orders go straight to IB Gateway without a centralized risk check.
- **No margin / buying-power monitoring.**

### 3. Autonomous Alpha Discovery *(The biggest gap)*
- **No automated universe selection.** You manually define `IB_SYMBOLS` and `bundles.yaml`.
- **No gap scanner or pre-market pipeline.** The 6 EPIC pre-market docs (gap discovery, AI analysis, regime detection, orchestration) are requirements only — none are wired into the live system.
- **No strategy adaptation.** Strategies do not adjust parameters based on market regime.
- **No news / sentiment / alternative data ingestion.**

### 4. Monitoring, Alerting & Observability *(Operational blind spots)*
- **No alerting channel.** The plan explicitly lists this as an *open question* (Slack? Email? Telegram?).
- **No dashboard.** No Grafana, no web UI, no real-time view of positions, P&L, or system health.
- **No anomaly detection.** No actor watching for unusual fill patterns, repeated order rejections, or data feed stalls.

### 5. Broker & Order Resilience *(Execution safety)*
- **No order reconciliation.** Nautilus state and IBKR actuals can drift (drops, rejects, manual intervention). No nightly reconciliation job.
- **No paper → live promotion workflow.** It is a config flag flip with no validation gate beyond backtest criteria.
- **No multi-account support.** Listed as an open question; all bundles route to a single `IB_ACCOUNT_ID`.

### 6. Compliance & Reporting *(Back-office)*
- **No performance attribution.** You cannot determine which strategy/book contributed to returns.
- **No end-of-day / end-of-month reporting.**
- **Minimal audit trail.** Fills table + git history is a start, but no centralized audit log of config changes, restarts, or risk events.

---

## 🎯 Bottom Line

**For a *manual* or *semi-automated* trading system:** Phases 0–6 give you a production-grade execution backbone. You can deploy strategies, backtest them, journal trades, and restart gracefully.

**For an *autonomous* trading system:** You are missing the "brain." The missing pieces are:
1. **Pre-market pipeline** (scan → analyze → size → allocate)
2. **Portfolio-level risk & P&L engine**
3. **Runtime safety controls** (kill switch, circuit breaker, reconciliation)
4. **Alerting & dashboards** so you know when autonomy fails

The pre-market requirement docs (`docs/reference/pre-market_req/`) map out exactly the autonomy layer you need — but that entire stack (gap scanners, AI analyzer, position sizer, regime detection, orchestrator) is **not yet implemented** and sits outside the Phase 0–6 roadmap.
