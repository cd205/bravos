-- Phase 5: Fill Capture and Position Reconciliation — schema migration
-- Verifies Phase 5 columns exist on the live DB. Safe to run even if columns
-- already exist (IF NOT EXISTS makes this idempotent).
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase5.sql

-- orders table: fill capture columns (already in schema.sql; guard for early-deployed DBs)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fill_price NUMERIC(10,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;

-- executions table: exec_id UNIQUE is already defined in schema.sql.
-- position_lots and broker_positions_snapshot tables are also already in schema.sql.
-- No new tables or constraints are needed in Phase 5.

COMMENT ON COLUMN orders.fill_price IS 'Avg fill price from IBKR orderStatus Filled callback (Phase 5)';
COMMENT ON COLUMN orders.filled_at IS 'Timestamp when order reached FILLED state (Phase 5)';

GRANT ALL ON orders TO bravos;
