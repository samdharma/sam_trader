-- SAM Trader V3 — PostgreSQL initialization
-- Creates tables for trade journal, orders, positions, and performance analysis.
-- Ported from v2 with additions:
--   - venue column on orders and positions
--   - venue_order_id, currency, ts_init on fills
--   - trd_market on fills for Futu market code
-- Phase 8 additions:
--   - slippage column on fills (execution quality tracking)
--   - performance_stats table (Nautilus PortfolioAnalyzer results)

CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL PRIMARY KEY,
    client_order_id VARCHAR(64)  NOT NULL UNIQUE,
    venue_order_id  VARCHAR(64),
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    venue           VARCHAR(10)  NOT NULL,
    side            VARCHAR(8)   NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type      VARCHAR(24)  NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_MARKET', 'STOP_LIMIT', 'MARKET_TO_LIMIT', 'MARKET_IF_TOUCHED', 'LIMIT_IF_TOUCHED', 'TRAILING_STOP_MARKET', 'TRAILING_STOP_LIMIT')),
    quantity        NUMERIC(24, 8) NOT NULL,
    price           NUMERIC(24, 8),
    status          VARCHAR(16)  NOT NULL CHECK (status IN ('SUBMITTED', 'ACCEPTED', 'REJECTED', 'CANCELED', 'PARTIALLY_FILLED', 'FILLED')),
    ts_submitted    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ts_updated      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_instrument ON orders(instrument_id);
CREATE INDEX IF NOT EXISTS idx_orders_venue ON orders(venue);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id);

CREATE TABLE IF NOT EXISTS fills (
    id              SERIAL PRIMARY KEY,
    trade_id        VARCHAR(64)  NOT NULL UNIQUE,
    client_order_id VARCHAR(64)  NOT NULL REFERENCES orders(client_order_id),
    venue_order_id  VARCHAR(64),
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    venue           VARCHAR(10)  NOT NULL,
    trd_market      VARCHAR(10),
    side            VARCHAR(8)   NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty             NUMERIC(24, 8) NOT NULL,
    price           NUMERIC(24, 8) NOT NULL,
    commission      NUMERIC(24, 8) DEFAULT 0.0,
    currency        VARCHAR(3)   NOT NULL,
    slippage        NUMERIC(24, 8),
    ts_event        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ts_init         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Idempotent migration for existing databases (must run after table exists)
ALTER TABLE fills ADD COLUMN IF NOT EXISTS slippage NUMERIC(24, 8);

CREATE INDEX IF NOT EXISTS idx_fills_ts_event ON fills(ts_event);
CREATE INDEX IF NOT EXISTS idx_fills_instrument ON fills(instrument_id);
CREATE INDEX IF NOT EXISTS idx_fills_venue ON fills(venue);
CREATE INDEX IF NOT EXISTS idx_fills_strategy ON fills(strategy_id);

CREATE TABLE IF NOT EXISTS positions (
    id              SERIAL PRIMARY KEY,
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    venue           VARCHAR(10)  NOT NULL,
    net_quantity    NUMERIC(24, 8) NOT NULL DEFAULT 0.0,
    avg_px          NUMERIC(24, 8),
    unrealized_pnl  NUMERIC(24, 8) DEFAULT 0.0,
    realized_pnl    NUMERIC(24, 8) DEFAULT 0.0,
    ts_opened       TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, instrument_id, venue)
);

CREATE INDEX IF NOT EXISTS idx_positions_instrument ON positions(instrument_id);
CREATE INDEX IF NOT EXISTS idx_positions_venue ON positions(venue);
CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(strategy_id);

-- ---------------------------------------------------------------------------
-- Performance analysis (Phase 8: Nautilus PortfolioAnalyzer integration)
-- Populated nightly by sam-services cron (02:00 HKT)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS performance_stats (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    strategy_id     VARCHAR(128) NOT NULL,
    stat_name       VARCHAR(64) NOT NULL,
    stat_value      NUMERIC(24, 8),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, strategy_id, stat_name)
);

CREATE INDEX IF NOT EXISTS idx_perf_stats_date ON performance_stats(date);
CREATE INDEX IF NOT EXISTS idx_perf_stats_strategy ON performance_stats(strategy_id);

-- ---------------------------------------------------------------------------
-- End-of-day reports (Phase 6 DM: EndOfDayReporterActor)
-- Populated daily at eod_report_time by EndOfDayReporterActor.
-- Consumed by sam report CLI and dashboard.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_reports (
    id              SERIAL PRIMARY KEY,
    market          VARCHAR(10)  NOT NULL,
    date            DATE         NOT NULL,
    report_json     JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (market, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(date);
CREATE INDEX IF NOT EXISTS idx_daily_reports_market ON daily_reports(market);
