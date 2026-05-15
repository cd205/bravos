---
phase: 05-fill-capture-and-position-reconciliation
verified: 2026-05-15T09:24:24Z
status: human_needed
score: 4/4 roadmap success criteria verified
overrides_applied: 0
human_verification:
  - test: "Run the 6 DB integration tests in tests/test_positions.py against the live Cloud SQL Auth Proxy on bravos-vm1: BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password) python -m pytest tests/test_positions.py -v"
    expected: "10 passed (4 mocked unit tests + 6 DB integration tests including test_periodic_reconciliation_mismatch)"
    why_human: "DB integration tests (test_open_lot_writes_row, test_fifo_*, test_close_lot_sets_fields, test_periodic_reconciliation_mismatch) require Cloud SQL Auth Proxy running on bravos-vm1. Cannot verify without live DB connection. 4 mocked unit tests already confirmed passing in automated checks."
---

# Phase 5: Fill Capture and Position Reconciliation — Verification Report

**Phase Goal:** The system correctly captures every fill (including partial fills), maintains accurate per-lot position state with FIFO assignment, and periodically reconciles internal state against IBKR's authoritative position data
**Verified:** 2026-05-15T09:24:24Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Fill price and fill quantity are captured from ibapi execution callbacks and stored as per-execution records; an order is only marked FILLED when total filled quantity matches order quantity | VERIFIED | `_handle_exec_details` in `connection.py` line 714: inserts `executions` row with `ON CONFLICT (exec_id) DO NOTHING`; `_update_order_filled` at line 773 sets `status='FILLED', fill_price, filled_at=NOW()`; `_update_order_partial` at line 792 sets `status='PARTIAL'`. `IBApp.orderStatus` branches on `status == "Filled"` vs `"PartiallyFilled"` (lines 524-528). Unit tests `test_order_status_filled` and `test_order_status_partial` and `test_exec_details_writes_execution_row` all pass. |
| 2 | Partial fills accumulate correctly — position state updates incrementally and reflects actual filled quantity at all times | VERIFIED | `IBApp.execDetails` delegates to `_handle_exec_details` (line 555), which dispatches to `positions.open_lot` (BUY) or `positions.partial_close_lot` (SELL) per fill event. `orderStatus` independently updates order status via `_update_order_partial` for `PartiallyFilled` without blocking the position write path. Phase 4 slot-notification preserved. |
| 3 | When a position with multiple open lots is partially or fully closed, FIFO lot assignment is applied and remaining open quantity is preserved | VERIFIED | `partial_close_lot` in `positions.py` line 61: `ORDER BY lot_opened_at ASC` (oldest first). Lines 87-105: partial-close path shrinks surviving lot quantity AND appends a new closed row (AUDIT-04/AUDIT-06 compliance). `close_lot` wraps `partial_close_lot` with full open quantity (D-06). 4 FIFO integration tests (test_fifo_closes_oldest_lot_first, test_fifo_partial_close_one_lot, test_fifo_close_spanning_multiple_lots, test_close_lot_sets_fields) are un-skipped and documented as passing on bravos-vm1. |
| 4 | The system runs reqPositions() on a periodic schedule; any discrepancy between internal position state and IBKR's authoritative data is logged and flagged for review | VERIFIED | `IBApp.run_periodic_reconciliation` at line 613: guards concurrent reqPositions, calls `reqPositions()`, waits `_positions_done`, then calls `_write_position_snapshot` and `_reconcile_against_db`. `_reconcile_against_db` at line 670 logs `WARNING` with "RECONCILE MISMATCH" on mismatch, never writes to `position_lots`. `run_cycle()` in `run_ingestion.py` lines 105-121 calls `run_periodic_reconciliation` per scrape cycle inside try/except with fresh DB connection closed in `finally`. |

**Score:** 4/4 roadmap success criteria verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `infra/migrate_phase5.sql` | Idempotent SQL migration adding fill_price, filled_at columns | VERIFIED | File exists, 18 lines. Contains 2x `ADD COLUMN IF NOT EXISTS` guards (fill_price, filled_at), 2x COMMENT ON COLUMN, 1x GRANT. No CREATE TABLE, no DROP statements. |
| `bravos/execution/positions.py` | Position lot management: open_lot(), partial_close_lot(), close_lot() | VERIFIED | File exists, 139 lines. Exports all 3 functions. FIFO via `ORDER BY lot_opened_at ASC`. 2x INSERT INTO position_lots (open_lot + partial-close append). 2x UPDATE position_lots. No ibapi imports. No f-strings in SQL. |
| `bravos/broker/connection.py` | execDetails callback, extended orderStatus, run_periodic_reconciliation, 3 helpers, _db_conn attribute | VERIFIED | File exists, 817 lines. All required functions present and importable. `_db_conn = None` in `__init__`. `ON CONFLICT (exec_id) DO NOTHING` present. Deferred import at line 761 (inside function, not at module top). |
| `scripts/run_ingestion.py` | ibapp._db_conn lifecycle + periodic reconciliation call site | VERIFIED | File exists, 241 lines. `_ibapp._db_conn = _get_db_connection()` at line 163 (after `run_startup_reconciliation` at line 150). `run_periodic_reconciliation` called at line 113 inside `run_cycle()`. Both wrapped in try/except. `_recon_db_conn` closed in finally block. |
| `tests/test_positions.py` | All 10 Phase 5 tests un-skipped | VERIFIED | File exists. 10 test functions (`grep -c "^def test_"` = 10). 0 `@pytest.mark.skip` decorators remaining. 10 tests collected by pytest. All 6 requirement IDs referenced in docstrings. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `IBApp.execDetails` | `_handle_exec_details` | `_handle_exec_details(self._db_conn, execution, contract)` at line 555 | WIRED | Thin callback delegates immediately; guards `_db_conn is None` |
| `_handle_exec_details` | `executions` table | `INSERT INTO executions ... ON CONFLICT (exec_id) DO NOTHING` at line 750 | WIRED | Idempotency guard confirmed in code and passing test |
| `_handle_exec_details` | `bravos.execution.positions` | Deferred `from bravos.execution import positions` at line 761 | WIRED | Inside function body (not module top) — avoids circular import |
| `IBApp.orderStatus` | `_update_order_filled / _update_order_partial` | `if status == "Filled"` branch at lines 524-528 | WIRED | Phase 4 slot-notification block runs first (unchanged), then Phase 5 fill branches |
| `IBApp.run_periodic_reconciliation` | `_write_position_snapshot + _reconcile_against_db` | Called at lines 644-645 | WIRED | Reuses Phase 3 helpers; concurrent-reqPositions guard at line 628 |
| `scripts/run_ingestion.py:run_cycle` | `ibapp.run_periodic_reconciliation` | Called at line 113, inside `run_cycle()` (lines 83-122) | WIRED | After session health check; guarded by `ibapp.is_connected()`; `_recon_db_conn` closed in finally |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `_handle_exec_details` | `execution.execId`, `.shares`, `.price` | IBKR `execDetails` callback | Yes — real ibapi execution object fields | FLOWING |
| `_update_order_filled` | `avg_fill_price`, `ibkr_order_id` | IBKR `orderStatus` callback `avgFillPrice` parameter | Yes — real IBKR parameters | FLOWING |
| `positions.open_lot` | `ticker`, `shares`, `entry_price` | Propagated from `_handle_exec_details` via `contract.symbol`, `execution.shares`, `execution.price` | Yes — real fill data | FLOWING |
| `partial_close_lot` FIFO SELECT | `lots` list | `position_lots` table WHERE `lot_closed_at IS NULL ORDER BY lot_opened_at ASC` | Yes — real DB query, not static | FLOWING |
| `run_periodic_reconciliation` | `self._positions` | `reqPositions()` + `positionEnd()` callback | Yes — populated by live IBKR response | FLOWING (live account) / requires human test |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| positions.py imports cleanly | `python -c "from bravos.execution.positions import open_lot, partial_close_lot, close_lot"` | `import ok` | PASS |
| connection.py symbols present | `python -c "from bravos.broker.connection import IBApp, _handle_exec_details, _update_order_filled, _update_order_partial; ..."` | `ok` | PASS |
| 4 mocked unit tests pass | `pytest tests/test_positions.py -k "exec_details or order_status_filled or order_status_partial" -v` | `4 passed` | PASS |
| All 10 tests collected | `pytest tests/test_positions.py --collect-only -q` | `10 tests collected` | PASS |
| No skip decorators remaining | `grep -c "@pytest.mark.skip" tests/test_positions.py` | `0` | PASS |
| Regression check (Phase 3/4 tests) | `pytest tests/test_broker.py tests/test_execution.py -x` | `1 failed` (pre-existing `test_gate_log_pass` — missing `risk_gate_log` table, documented in 05-02-SUMMARY.md), `21 passed, 8 skipped` | PASS (no new regressions) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| EXEC-05 | 05-03 | System captures actual fill price and fill quantity from ibapi execution callbacks and stores per-execution records | SATISFIED | `_handle_exec_details` inserts into `executions` table with idempotent `ON CONFLICT (exec_id) DO NOTHING`. `IBApp.execDetails` delegates to it. Tests `test_exec_details_writes_execution_row` and `test_exec_details_idempotent` pass. |
| EXEC-06 | 05-03 | System handles partial fills correctly — order only marked FILLED when total filled quantity matches order quantity | SATISFIED | `_update_order_filled` sets `status='FILLED'` (called from `orderStatus` on `status=="Filled"`). `_update_order_partial` sets `status='PARTIAL'` (called on `"PartiallyFilled"`). Tests `test_order_status_filled` and `test_order_status_partial` pass. |
| IBKR-04 | 05-03 | System periodically reconciles internal position state against IBKR's authoritative position data; discrepancies are logged and flagged | SATISFIED | `run_periodic_reconciliation` wired into `run_cycle()`. `_reconcile_against_db` logs WARNING on mismatch, never auto-corrects. Test `test_periodic_reconciliation_mismatch` un-skipped (requires DB for integration assertion — see Human Verification). |
| POS-01 | 05-02 | System maintains an internal record of all open positions (lots) with entry price, weight, quantity, and associated signal | PARTIALLY SATISFIED | `open_lot()` inserts ticker, quantity, entry_price into `position_lots`. NOTE: REQUIREMENTS.md text specifies "weight" and "associated signal" but `position_lots` schema has no weight or signal_id column, and neither does `open_lot()` pass these fields. The ROADMAP Phase 5 success criteria do not mandate weight/signal tracking — this gap appears to be a v1 schema scope limitation acknowledged in the schema design. Test `test_open_lot_writes_row` verifies what is implemented (requires DB). |
| POS-02 | 05-02 | System tracks closed positions with entry price, exit price, realized P&L, and trade duration | SATISFIED | `partial_close_lot()` sets `lot_closed_at`, `exit_price`, `pnl` on closed rows. Trade duration is computable from `lot_opened_at - lot_closed_at` (both columns stored). D-07 pnl formula: `(exit_price - entry_price) * quantity_closed` applied per lot. Test `test_close_lot_sets_fields` verifies (requires DB). |
| POS-03 | 05-02 | System correctly applies FIFO lot assignment when reducing or closing a position that has multiple open lots | SATISFIED | `partial_close_lot()` uses `ORDER BY lot_opened_at ASC`. Partial-close of one lot appends a new closed row (AUDIT-04/AUDIT-06). Tests `test_fifo_closes_oldest_lot_first`, `test_fifo_partial_close_one_lot`, `test_fifo_close_spanning_multiple_lots` verify (require DB). |

### Anti-Patterns Found

| File | Pattern | Severity | Assessment |
|------|---------|----------|------------|
| None found | — | — | No TODO/FIXME/placeholder patterns found in `positions.py`, `connection.py`, or `run_ingestion.py`. No `return null`, `return {}`, or empty handler bodies. No f-strings in SQL contexts. All SQL uses `%s` parameterized queries. |

### Human Verification Required

#### 1. DB Integration Tests on bravos-vm1

**Test:** On bravos-vm1 with Cloud SQL Auth Proxy running:
```
BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password) \
  python -m pytest tests/test_positions.py -v
```
**Expected:** `10 passed` (4 mocked unit tests already confirmed + 6 DB integration tests)

The 6 DB integration tests are:
- `test_open_lot_writes_row` — POS-01: verifies INSERT into position_lots
- `test_fifo_closes_oldest_lot_first` — POS-03: verifies FIFO ordering
- `test_fifo_partial_close_one_lot` — POS-03/AUDIT-04: verifies append-only split
- `test_fifo_close_spanning_multiple_lots` — POS-03: verifies multi-lot spanning
- `test_close_lot_sets_fields` — POS-02: verifies exit_price/pnl fields
- `test_periodic_reconciliation_mismatch` — IBKR-04: verifies WARNING logging on mismatch and no auto-correction

**Why human:** These tests use the `db_connection` pytest fixture which connects to Cloud SQL Auth Proxy on `127.0.0.1:5432`. The Cloud SQL Auth Proxy is not running in the verification environment. Per the Phase 3 precedent documented in STATE.md, DB integration tests are executed on bravos-vm1 only. The 05-03-SUMMARY.md documents these as passing (`10 passed in 0.42s`) on bravos-vm1, but the verifier cannot confirm this without the live DB connection.

### Gaps Summary

No blocking gaps found. All 4 ROADMAP Phase 5 success criteria are verified by code inspection and automated behavioral checks. The only deferred verification is the DB integration test execution on bravos-vm1 — required due to infrastructure constraints (Cloud SQL Auth Proxy availability), not a code deficiency.

**Notable observation (non-blocking):** `position_lots` schema and `open_lot()` implementation do not store `weight` or `signal_id` as referenced in REQUIREMENTS.md POS-01 text. The ROADMAP Phase 5 success criteria do not mandate these fields, and the schema design decision (no weight/signal_id in position_lots) appears intentional — the lot is linked to IBKR executions rather than directly to signals. This is a REQUIREMENTS.md text / schema inconsistency to revisit in a future phase if needed.

---

_Verified: 2026-05-15T09:24:24Z_
_Verifier: Claude (gsd-verifier)_
