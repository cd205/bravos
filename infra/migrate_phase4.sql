-- Phase 4: Risk Controls — schema migration
-- Adds risk_gate_log table required by RISK-04 (every gate decision logged)
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase4.sql

CREATE TABLE IF NOT EXISTS risk_gate_log (
    id                   SERIAL PRIMARY KEY,
    signal_id            INTEGER REFERENCES signals(id),
    checked_at           TIMESTAMPTZ DEFAULT NOW(),
    gate_passed          BOOLEAN NOT NULL,
    reason               TEXT NOT NULL,
    open_positions       INTEGER,
    max_positions        INTEGER,
    order_allocation_pct NUMERIC(6,4),
    max_allocation_pct   NUMERIC(6,4),
    net_liquidation      NUMERIC(14,2),
    daily_pnl            NUMERIC(14,2),
    daily_pnl_threshold  NUMERIC(14,2)
);

COMMENT ON TABLE risk_gate_log IS 'Every risk gate decision — pass and block — per RISK-04';

GRANT ALL ON risk_gate_log TO bravos;
GRANT ALL ON SEQUENCE risk_gate_log_id_seq TO bravos;
