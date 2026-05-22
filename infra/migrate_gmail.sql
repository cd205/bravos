-- Add gmail_message_id for dedup: one row per Gmail Message-ID, never reprocess.
-- Safe to run multiple times (IF NOT EXISTS / DO NOTHING pattern).
ALTER TABLE signals ADD COLUMN IF NOT EXISTS gmail_message_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS signals_gmail_message_id_uq
    ON signals (gmail_message_id)
    WHERE gmail_message_id IS NOT NULL;
