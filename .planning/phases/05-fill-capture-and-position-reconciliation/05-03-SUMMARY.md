---
plan: 05-03
phase: 05
status: complete
self_check: PASSED
tags:
  - phase-05
  - exec-details
  - order-status
  - periodic-reconciliation
subsystem: broker/execution
dependency-graph:
  requires:
    - 05-01  # test scaffolding and schema migration
    - 05-02  # positions.py (open_lot, partial_close_lot, close_lot)
  provides:
    - IBApp.execDetails callback (fill capture)
    - IBApp.execDetailsEnd callback
    - IBApp.run_periodic_reconciliation (IBKR-04)
    - _handle_exec_details module-level helper (EXEC-05)
    - _update_order_filled module-level helper (EXEC-06)
    - _update_order_partial module-level helper (EXEC-06)
    - scripts/run_ingestion.py daemon wiring (ibapp._db_conn + periodic recon call)
    - All 10 Phase 5 tests passing
  affects:
    - Phase 6+ (dashboard reads from position_lots + executions now populated by fills)
tech-stack:
  added: []
  patterns:
    - Thin EWrapper callback delegate to module-level helper (testable without IBApp)
    - Deferred import inside function body to avoid circular dependency
    - Idempotent INSERT via ON CONFLICT (exec_id) DO NOTHING
    - concurrent-reqPositions guard in periodic reconciliation
    - Dedicated api-thread DB connection (never shared with main thread)
key-files:
  created: []
  modified:
    - bravos/broker/connection.py
    - scripts/run_ingestion.py
    - tests/test_positions.py
decisions:
  - "Deferred import of bravos.execution.positions inside _handle_exec_details body (not at module top) — mirrors executor.py's deferred import of ibapp singleton; avoids circular dependency at module load time."
  - "run_periodic_reconciliation uses a concurrent-reqPositions guard (if not self._positions_done.is_set(): return) — prevents concurrent reqPositions calls corrupting _positions list (RESEARCH Pitfall 3)."
  - "Two distinct DB connections per cycle: ibapp._db_conn (long-lived, api-thread-owned for fill callbacks) vs _recon_db_conn (short-lived, opened+closed per run_cycle for periodic reconciliation). Matches RESEARCH Pitfall 1 threading guidance."
  - "orderStatus Phase 5 extension appended AFTER Phase 4 slot-notification block — Phase 4 path fully preserved, no behavioral change to existing executor flow."
metrics:
  duration: ~28 minutes
  completed: 2026-05-15T09:10:15Z
  tasks_completed: 3
  files_changed: 3
---

# Phase 5 Plan 03: Fill Capture and Periodic Reconciliation Wiring — Summary

## What Was Built

Integration plan: wired IBKR fill capture callbacks and periodic reconciliation into the running daemon. `positions.py` is unchanged — this plan only adds the callback wiring and dispatches to positions.

### `bravos/broker/connection.py` — ~183 lines added

**New IBApp attributes:**
- `self._db_conn = None` — dedicated DB connection for fill callbacks on the ibkr-api thread. Set by `run_ingestion.py` main() after `run_startup_reconciliation` succeeds. Never shared across threads (psycopg2 connections are not thread-safe).

**New IBApp callbacks:**
- `IBApp.execDetails(reqId, contract, execution)` — canonical fill capture callback (EXEC-05, D-01, D-04). Guards `_db_conn is None` (warns and returns), then delegates to `_handle_exec_details`. Thin callback — all logic in the module-level helper.
- `IBApp.execDetailsEnd(reqId)` — batch completion signal; informational log only (D-04: fills processed per-event, not in batch).

**Extended IBApp callback:**
- `IBApp.orderStatus` — Phase 4 slot-notification block preserved exactly as-is. Phase 5 appends a `_db_conn is not None` guard block: `status == "Filled"` calls `_update_order_filled`; `status == "PartiallyFilled"` calls `_update_order_partial`. Other statuses still owned by Phase 4.

**New IBApp method:**
- `IBApp.run_periodic_reconciliation(db_conn, timeout=30)` — periodic reconciliation (IBKR-04, D-08, D-10). Guards against concurrent `reqPositions()` (`if not self._positions_done.is_set(): return`). Clears `_positions` and `_positions_done`, calls `reqPositions()`, waits `timeout` seconds, then calls `_write_position_snapshot` and `_reconcile_against_db` — both Phase 3 module-level helpers, reused unchanged. Logs WARNING on timeout; continues with partial data.

**New module-level helpers:**
- `_handle_exec_details(db_conn, execution, contract)` — single helper that: (1) SELECT orders by ibkr_order_id → returns early with ERROR log if not found; (2) INSERT into executions with `ON CONFLICT (exec_id) DO NOTHING` (idempotent); (3) deferred-import `from bravos.execution import positions`; (4) dispatches to `positions.open_lot` (BUY) or `positions.partial_close_lot` (SELL).
- `_update_order_filled(db_conn, ibkr_order_id, avg_fill_price)` — `UPDATE orders SET status='FILLED', fill_price=%s, filled_at=NOW() WHERE ibkr_order_id=%s`. Commits. (EXEC-06, D-02).
- `_update_order_partial(db_conn, ibkr_order_id, avg_fill_price)` — `UPDATE orders SET status='PARTIAL', fill_price=%s WHERE ibkr_order_id=%s`. Does NOT set `filled_at` (order not complete yet). Commits. (EXEC-06, D-02).

### `scripts/run_ingestion.py` — ~36 lines added

**`main()` — startup wiring (EDIT A):**
After `run_startup_reconciliation` returns (success or failure): opens a fresh DB connection via `_get_db_connection()` and assigns it to `_ibapp._db_conn`. Wrapped in try/except — on failure, logs exception and sets `_ibapp._db_conn = None` (fill captures skipped, daemon continues). This connection is intentionally long-lived (api-thread-owned for the daemon process lifetime).

**`run_cycle()` — periodic reconciliation (EDIT B):**
After session health check: guards `broker_module.ibapp is not None and broker_module.ibapp.is_connected()`, then opens a fresh `_recon_db_conn` per cycle, calls `run_periodic_reconciliation`, closes conn in `finally` block (prevents connection leak on exception). Wrapped in outer try/except — daemon continues on failure. Uses a separate short-lived connection from `ibapp._db_conn` (RESEARCH Pitfall 1 compliance).

### `tests/test_positions.py` — 5 skip decorators removed

All 5 Plan 05-03 `@pytest.mark.skip` decorators removed. Test bodies unchanged (Wave 0 stubs were fully implemented — only the decorator needed removal):
1. `test_exec_details_writes_execution_row` — EXEC-05 mocked unit test
2. `test_exec_details_idempotent` — EXEC-05 idempotency mocked unit test
3. `test_order_status_filled` — EXEC-06 mocked unit test
4. `test_order_status_partial` — EXEC-06 mocked unit test
5. `test_periodic_reconciliation_mismatch` — IBKR-04 DB integration test

## Test Results

**Executed on bravos-vm1 (Cloud SQL Auth Proxy running):**

```
10 passed in 0.42s
```

All 10 Phase 5 tests passed — 4 mocked unit tests (no DB needed) + 6 DB integration tests (db_connection fixture via Cloud SQL Auth Proxy).

**Regression check — Phase 3 tests:**
```
15 passed, 8 skipped
```

**Regression check — Phase 4 tests (pre-existing failures excluded):**
The 3 pre-existing failures (`test_gate_log_pass`, `test_gate_log_block`, `test_order_db_write_pending`) are documented in 05-02-SUMMARY.md and pre-date this plan. No new failures introduced.

## Decisions Made

1. **Deferred import pattern:** `from bravos.execution import positions` is inside `_handle_exec_details` body, not at module top. Mirrors `executor.py` line 77 which imports `ibapp` at call-time to avoid circular dependency. At module load time, `bravos.broker.connection` would not yet know about `bravos.execution.positions` without creating a circular import chain.

2. **concurrent-reqPositions guard:** `run_periodic_reconciliation` checks `if not self._positions_done.is_set(): return` before issuing a new `reqPositions()`. If a prior call is still in flight (callback not yet received), skips this cycle and re-enters next cycle. Prevents `_positions` list corruption (RESEARCH Pitfall 3).

3. **Two distinct DB connections:** `ibapp._db_conn` is long-lived (api-thread-owned, fill callbacks); `_recon_db_conn` is short-lived (opened+closed per `run_cycle`, main-thread-owned, periodic reconciliation). This satisfies RESEARCH Pitfall 1: psycopg2 connections must not be shared across threads.

4. **`execDetailsEnd` is informational only:** D-04 specifies each fill is processed immediately in `execDetails`. `execDetailsEnd` only logs an INFO message. No batch accumulation needed.

5. **Phase 5 completion:** All of EXEC-05, EXEC-06, IBKR-04, POS-01, POS-02, POS-03 now have implementing code and test coverage across Plans 05-02 and 05-03.

## Deviations from Plan

### Deviation: Worktree Path Safety

**Found during:** Task 1 (pre-commit)

**Issue:** Initial file edits were written to the main repo (`/home/chris_s_dodd/bravos/bravos/broker/connection.py`) instead of the worktree (`/home/chris_s_dodd/bravos/.claude/worktrees/agent-a919651e42e151499/bravos/broker/connection.py`). The pre-commit HEAD assertion caught this: the main repo is on `main` (protected), blocking the commit.

**Fix:** Copied the modified `connection.py` from the main repo to the worktree, then restored the main repo file to HEAD via `git checkout -- bravos/broker/connection.py`. Subsequent Tasks 2 and 3 were edited directly in the worktree using the worktree absolute path.

**Impact:** No code impact — the content of the file is correct. Only the editing workflow needed correction.

## Known Stubs

None. All three helpers (`_handle_exec_details`, `_update_order_filled`, `_update_order_partial`) are complete production implementations. No hardcoded values, placeholders, or incomplete branches.

## Threat Flags

None. No new network endpoints, auth paths, or trust boundary changes introduced. DB writes use parameterized queries (`%s` placeholders) throughout. The new DB connection (`ibapp._db_conn`) reuses the existing `_get_db_connection()` pattern with credentials from environment variables.

## Self-Check: PASSED
