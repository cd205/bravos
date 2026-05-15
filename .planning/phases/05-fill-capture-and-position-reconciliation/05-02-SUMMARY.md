---
plan: 05-02
phase: 05
status: complete
self_check: PASSED
tags:
  - phase-05
  - position-lots
  - fifo
  - positions-module
subsystem: execution
dependency-graph:
  requires:
    - 05-01  # test scaffolding and schema migration
  provides:
    - bravos.execution.positions (open_lot, partial_close_lot, close_lot)
    - 5 integration tests passing against live DB
  affects:
    - 05-03  # will wire positions.py into execDetails callback
tech-stack:
  added: []
  patterns:
    - FIFO lot closure via ORDER BY lot_opened_at ASC
    - AUDIT-04 append-new-closed-row for partial closes
    - Pure DB module pattern (no ibapi imports)
key-files:
  created:
    - bravos/execution/positions.py
  modified:
    - tests/test_positions.py
decisions:
  - "close_lot() added as thin public wrapper per D-03 (locked); delegates to partial_close_lot() with full open quantity per D-06 discretion. Three public exports: open_lot, partial_close_lot, close_lot."
  - "AUDIT-04/AUDIT-06 satisfied via INSERT new closed row + UPDATE surviving lot quantity (not destructive UPDATE quantity=quantity-n)"
  - "Ticker format in test fixtures fixed to use short prefixes (OPN_/FIF_/PAR_/SPN_/CLO_ + 3-byte hex) to respect VARCHAR(10) schema constraint (Rule 1 bug in 05-01 stubs)"
metrics:
  duration: ~15 minutes
  completed: 2026-05-15
  tasks_completed: 2
  files_changed: 2
---

# Phase 5 Plan 02: Position Lot Management Module — Summary

## What Was Built

**`bravos/execution/positions.py` — new module (~139 lines):**

Position lot management module with FIFO lot closure. No ibapi imports — independently testable against the live DB via the `db_connection` fixture.

Three public exports (D-03 locked API contract):
- `open_lot(ticker, shares, entry_price, db_conn)` — inserts a new open `position_lots` row. `lot_opened_at` uses schema default `NOW()`. Commits on success (POS-01).
- `partial_close_lot(ticker, shares_to_close, exit_price, db_conn)` — FIFO lot closure. Iterates lots `ORDER BY lot_opened_at ASC`. Full lots closed entirely (UPDATE `lot_closed_at/exit_price/pnl`). Partial lot: UPDATE surviving lot quantity + INSERT new closed row (AUDIT-04/AUDIT-06). Commits on success (POS-02 + POS-03).
- `close_lot(ticker, exit_price, db_conn)` — thin wrapper; queries total open quantity then delegates to `partial_close_lot()` (D-03/D-06).

**FIFO algorithm (D-05):** `SELECT id, quantity, entry_price FROM position_lots WHERE ticker=%s AND lot_closed_at IS NULL ORDER BY lot_opened_at ASC` — oldest lot fully consumed before next lot touched.

**AUDIT-04/AUDIT-06 compliance:** When a lot is only partially closed, a NEW row is appended (`INSERT INTO position_lots ... SELECT ticker, lot_opened_at, %s, entry_price, NOW(), %s, %s FROM position_lots WHERE id=%s`) with the closed quantity/exit_price/pnl. The surviving open row has its `quantity` reduced. No destructive in-place mutation.

**D-07 pnl formula:** `pnl = (exit_price - entry_price_f) * quantity_closed` applied per lot.

**`tests/test_positions.py` — 5 tests un-skipped:**

The 5 Plan 05-02 integration tests are now active (using the `db_connection` fixture pointing at Cloud SQL Auth Proxy):
1. `test_open_lot_writes_row` — POS-01 coverage
2. `test_fifo_closes_oldest_lot_first` — POS-03 FIFO coverage
3. `test_fifo_partial_close_one_lot` — POS-03 + AUDIT-04 partial-close coverage
4. `test_fifo_close_spanning_multiple_lots` — POS-03 multi-lot spanning coverage
5. `test_close_lot_sets_fields` — POS-02 full-close field verification

The 5 Plan 05-03 tests remain `@pytest.mark.skip(reason="implementing: 05-03-*")`.

## Test Results

Integration tests executed against live Cloud SQL Auth Proxy (available on this VM):

```
5 passed, 5 skipped in 0.38s
```

All 5 Plan 05-02 tests passed against the live DB. No regressions in the wider suite (pre-existing `test_gate_log_pass` failure in `test_execution.py` is out of scope — present before this plan, caused by a missing `risk_gate_log` table in the DB).

## Decisions Made

1. **close_lot() API wrapper:** Added per D-03 (locked decision). Delegates to `partial_close_lot()` with `COALESCE(SUM(quantity), 0)` for the full open quantity. Satisfies the locked API contract without duplicating FIFO logic (D-06 discretion).
2. **AUDIT-04/AUDIT-06 pattern confirmed:** `INSERT ... SELECT` from the surviving lot row copies `ticker`, `lot_opened_at`, `entry_price` into the new closed row — making it traceable back to the original lot entry.
3. **Ticker fixture format fixed:** Plan 05-01 stubs generated tickers like `AAPL_05P1_{8-hex}` (18 chars) which exceed `position_lots.ticker VARCHAR(10)`. Fixed to short prefixes + 3-byte (6-char) hex (max 10 chars total). This is a Rule 1 bug fix.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test ticker length exceeding VARCHAR(10)**
- **Found during:** Task 2 — first integration test run
- **Issue:** Plan 05-01 stubs generated tickers like `AAPL_05P1_{4-byte-hex}` = 18 chars, exceeding `position_lots.ticker VARCHAR(10)`. All 5 un-skipped tests and the still-skipped RECON test had this issue.
- **Error:** `psycopg2.errors.StringDataRightTruncation: value too long for type character varying(10)`
- **Fix:** Changed ticker format to `{3-4 char prefix}_{3-byte-hex}` (max 10 chars): `OPN_`, `FIF_`, `PAR_`, `SPN_`, `CLO_`, `REC_` + 6 hex chars = 10 chars exactly.
- **Files modified:** `tests/test_positions.py` (6 ticker generation lines)
- **Commit:** 50009ff (included in Task 2 commit)

## Known Stubs

None. `positions.py` is a complete production implementation — no hardcoded values, placeholders, or incomplete branches.

## Self-Check: PASSED
