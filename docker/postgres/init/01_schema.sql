-- SAM Trader V3 — PostgreSQL initialization
-- Creates tables for trade journal, orders, and positions.
-- Ported from v2 with additions: trd_market column on fills.

CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL PRIMARY KEY,
    client_order_id VARCHAR(64)  NOT NULL UNIQUE,
    venue_order_id  VARCHAR(64),
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    side            VARCHAR(8)   NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type      VARCHAR(16)  NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_MARKET', 'STOP_LIMIT')),
    quantity        NUMERIC(24, 8) NOT NULL,
    price           NUMERIC(24, 8),
    status          VARCHAR(16)  NOT NULL CHECK (status IN ('SUBMITTED', 'ACCEPTED', 'REJECTED', 'CANCELED', 'PARTIALLY_FILLED', 'FILLED')),
    ts_submitted    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ts_updated      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_instrument ON orders(instrument_id);

CREATE TABLE IF NOT EXISTS fills (
    id              SERIAL PRIMARY KEY,
    trade_id        VARCHAR(64)  NOT NULL UNIQUE,
    client_order_id VARCHAR(64)  NOT NULL REFERENCES orders(client_order_id),
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    side            VARCHAR(8)   NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty             NUMERIC(24, 8) NOT NULL,
    price           NUMERIC(24, 8) NOT NULL,
    commission      NUMERIC(24, 8) DEFAULT 0.0,
    venue           VARCHAR(64)  NOT NULL,
    trd_market      VARCHAR(64),
    ts_event        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fills_ts_event ON fills(ts_event);
CREATE INDEX IF NOT EXISTS idx_fills_instrument ON fills(instrument_id);

CREATE TABLE IF NOT EXISTS positions (
    id              SERIAL PRIMARY KEY,
    strategy_id     VARCHAR(128) NOT NULL,
    instrument_id   VARCHAR(128) NOT NULL,
    net_quantity    NUMERIC(24, 8) NOT NULL DEFAULT 0.0,
    avg_px          NUMERIC(24, 8),
    unrealized_pnl  NUMERIC(24, 8) DEFAULT 0.0,
    realized_pnl    NUMERIC(24, 8) DEFAULT 0.0,
    ts_opened       TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_positions_instrument ON positions(instrument_id);
