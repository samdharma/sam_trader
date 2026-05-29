-- SAM Trader V3 — Backtest results persistence
-- Stores BacktestResult objects from NautilusTrader backtesting engine.
-- Schema per docs/reference/BUILD_PLAN_12.1.md §5.

CREATE TABLE IF NOT EXISTS backtest_results (
    id              SERIAL PRIMARY KEY,
    run_id          VARCHAR(64) NOT NULL UNIQUE,
    run_config_id   VARCHAR(64) NOT NULL,
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    bar_type        VARCHAR(64) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          VARCHAR(16) NOT NULL
                        CHECK (status IN ('running', 'completed', 'failed')),
    total_events    INTEGER,
    total_orders    INTEGER,
    total_positions INTEGER,
    elapsed_secs    NUMERIC(12, 3),
    stats_pnls      JSONB,
    stats_returns   JSONB,
    equity_curve    JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    strategy_family VARCHAR(64),
    strategy_version VARCHAR(32),
    tags            JSONB
);

CREATE INDEX IF NOT EXISTS idx_bt_results_strategy ON backtest_results(strategy_id);
CREATE INDEX IF NOT EXISTS idx_bt_results_date ON backtest_results(start_date, end_date);
