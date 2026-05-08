-- Phase 2: Signal Ingestion — schema migration
-- Adds audit columns required by AUDIT-01 and INGST-06
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_signals_v2.sql

ALTER TABLE signals
  ADD COLUMN IF NOT EXISTS parse_method VARCHAR(10),
  ADD COLUMN IF NOT EXISTS scraped_at   TIMESTAMPTZ DEFAULT NOW();

COMMENT ON COLUMN signals.parse_method IS 'regex or spacy — which parser produced this result';
COMMENT ON COLUMN signals.scraped_at   IS 'Timestamp of the scrape cycle that retrieved this post';
