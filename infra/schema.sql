-- Bravos Trading System — Database Schema
-- Applied to: bravos_trading database on Cloud SQL (bravos-db, us-central1)
-- User: bravos
-- Run via Cloud SQL Auth Proxy on 127.0.0.1:5432

CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    post_url TEXT UNIQUE NOT NULL,
    post_title TEXT NOT NULL,
    raw_html TEXT,
    ticker VARCHAR(10),
    action_type VARCHAR(20),
    weight_from INTEGER,
    weight_to INTEGER,
    reference_price NUMERIC(10,2),
    confidence VARCHAR(10),
    parsed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    signal_id INTEGER REFERENCES signals(id),
    ibkr_order_id INTEGER,
    ticker VARCHAR(10) NOT NULL,
    action VARCHAR(10) NOT NULL,
    quantity INTEGER,
    order_type VARCHAR(10) DEFAULT 'MKT',
    status VARCHAR(20) DEFAULT 'pending',
    submitted_at TIMESTAMPTZ,
    filled_at TIMESTAMPTZ,
    fill_price NUMERIC(10,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS position_lots (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    lot_opened_at TIMESTAMPTZ DEFAULT NOW(),
    quantity INTEGER NOT NULL,
    entry_price NUMERIC(10,2),
    lot_closed_at TIMESTAMPTZ,
    exit_price NUMERIC(10,2),
    pnl NUMERIC(12,2)
);

CREATE TABLE IF NOT EXISTS executions (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    exec_id TEXT UNIQUE,
    shares INTEGER,
    price NUMERIC(10,2),
    commission NUMERIC(8,2),
    exec_time TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS broker_positions_snapshot (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    position INTEGER,
    avg_cost NUMERIC(10,2),
    market_value NUMERIC(12,2),
    snapshot_at TIMESTAMPTZ DEFAULT NOW()
);

GRANT ALL ON ALL TABLES IN SCHEMA public TO bravos;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO bravos;
