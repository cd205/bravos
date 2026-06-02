---
plan: 09-02
phase: 09-bracket-orders
status: complete
completed: 2026-06-02
commits:
  - 0ea4621
  - 971f4ee
  - c39f2ec
self_check: PASSED
---

## Summary

Extended the parser with TP/SL regex extraction, wired stop_loss and take_profits into the scraper INSERT, and created the Phase 9 schema migration.

## What Was Built

### Parser extensions (bravos/ingestion/parser.py)
- `STOP_LOSS_RE` — matches structured ("Suggested Stop Loss (SL): $257") and prose ("stop at $195") forms (D-01, D-04)
- `TAKE_PROFIT_RE` — matches structured ("Take Profit (TP): $320, $350") and prose ("price targets of ...") forms (D-02, D-04)
- `PRICE_IN_LIST_RE` — helper to extract dollar amounts from TP match groups
- `parse_signal()` extended: returns `stop_loss` (float|None) and `take_profits` (list[float]) on both early-return and main paths (D-03)
- 6 parser test stubs from 09-01 un-skipped and passing

### Scraper persistence (bravos/ingestion/scraper.py)
- `_store_signal()` INSERT extended with `stop_loss` and `take_profits` columns (D-03)
- Binds `signal_data.get("stop_loss")` (float|None → NUMERIC) and `signal_data.get("take_profits", [])` (list → ARRAY)

### Schema migration (infra/migrate_phase9.sql)
- `stop_loss NUMERIC(10,2)` added to signals table (D-05)
- `take_profits NUMERIC(10,2)[] DEFAULT '{}'` added to signals table (D-06)
- `parent_order_id INTEGER REFERENCES orders(id)` added to orders table (D-07)
- `oca_group TEXT` added to orders table (D-08)
- All four columns use `ADD COLUMN IF NOT EXISTS` — idempotent (D-09)
- Applied to live Cloud SQL DB via Auth Proxy; idempotency verified on second run

## Key Files

- `bravos/ingestion/parser.py` — STOP_LOSS_RE, TAKE_PROFIT_RE, PRICE_IN_LIST_RE, extended parse_signal()
- `bravos/ingestion/scraper.py` — extended _store_signal() INSERT
- `infra/migrate_phase9.sql` — 4-column idempotent migration

## Test Results

6 parser stubs un-skipped and passing. Full suite: 26+ passed, remaining stubs skipped, pre-existing failure unchanged.

## Deviations

None.
