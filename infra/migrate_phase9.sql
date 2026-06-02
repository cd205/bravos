-- Phase 9: Bracket Orders — schema migration
-- Verifies Phase 9 columns exist on the live DB. Safe to run even if columns
-- already exist (IF NOT EXISTS makes this idempotent).
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase9.sql

-- signals table: TP/SL storage (D-05, D-06)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(10,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS take_profits NUMERIC(10,2)[] DEFAULT '{}';

-- orders table: bracket linkage (D-07, D-08)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parent_order_id INTEGER REFERENCES orders(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS oca_group TEXT;

COMMENT ON COLUMN signals.stop_loss IS 'Stop loss price from alert body (Phase 9)';
COMMENT ON COLUMN signals.take_profits IS 'Take profit levels from alert body as array (Phase 9)';
COMMENT ON COLUMN orders.parent_order_id IS 'FK to orders.id of parent order (bracket child orders only) (Phase 9)';
COMMENT ON COLUMN orders.oca_group IS 'OCA group name for TP child orders (Phase 9)';

GRANT ALL ON signals TO bravos;
GRANT ALL ON orders TO bravos;
