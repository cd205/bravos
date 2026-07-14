---
phase: quick-260714-6gz
plan: 01
subsystem: execution
tags: [ibkr, ibapi, order-execution, bugfix, order-status]
dependency-graph:
  requires: []
  provides: ["honest-order-status-mapping"]
  affects: ["bravos/broker/connection.py error routing", "bravos/execution/executor.py order submission path (both plain and bracket)"]
tech-stack:
  added: []
  patterns: ["order-scoped error() routing into the existing _order_status_events slot mechanism"]
key-files:
  created: []
  modified:
    - bravos/broker/connection.py
    - bravos/execution/executor.py
    - tests/test_execution.py
    - tests/test_broker.py
decisions:
  - "399 is the sole evidence-backed entry in the informational allowlist — no other codes were added without a real observed incident, per the plan's constraint against inventing codes"
  - "Fixed both _submit_order and _submit_bracket_order in place (near-duplicate logic) rather than deduplicating the submit-path pattern — that refactor is tracked separately as IMPROVEMENTS.md item 6 and explicitly out of scope for this session"
  - "Bracket children are only bulk-updated to SUBMITTED when the parent's resolved db_status is actually SUBMITTED — previously they were unconditionally set to SUBMITTED after the parent resolved by any path"
metrics:
  duration: "~40min (across 3 resumed sessions after interruptions)"
  completed: "2026-07-14"
---

# Quick Task 260714-6gz: Rejection routing + honest order statuses Summary

Two real incidents (EXEL error 321 on 2026-07-02, HROW error 10268 on 2026-07-08) were both masked by the same bug: when IBKR rejects an order via the `error()` callback, `orderStatus` never fires, the executor's 3-second wait times out, and `_STATUS_MAP.get(final_status_raw, "SUBMITTED")` silently defaulted to SUBMITTED. The DB recorded successful submission for orders IBKR had actually rejected — discoverable only via manual log forensics.

## What Was Built

**`bravos/broker/connection.py`** — `IBApp.error()` now routes order-scoped errors into the same `_order_status_events` slot the executor already waits on:
- New `_ORDER_WARNING_CODES = {399}` allowlist (399 = "order accepted but queued until market open" — confirmed live on the first-ever accepted order, 2026-07-09; not a rejection).
- If `reqId` matches a registered slot and `errorCode` is not in the allowlist, the slot is written with `{"status": "IBKR_ERROR", "code": errorCode, "message": errorString}` and its event is set — waking the executor immediately instead of after a timeout.
- Errors for unregistered reqIds are untouched (no new slot created, no crash).

**`bravos/execution/executor.py`** — both `_submit_order` and `_submit_bracket_order`:
- An `IBKR_ERROR` slot result maps to DB status `REJECTED`, logged at ERROR with the code and message.
- A genuine timeout (slot event never set — no callback of any kind arrived) maps to a new DB status `UNKNOWN`, logged at ERROR ("order state unverified — check TWS manually"). The old `.get(..., "SUBMITTED")` fallback default is gone.
- `SUBMITTED` is now only ever assigned when a real `PreSubmitted`/`Submitted` callback was observed (directly, or after a 399 warning that doesn't touch the slot).
- Bracket children (STP + TP legs) are bulk-updated to `SUBMITTED` only when the parent's resolved status is actually `SUBMITTED` — previously this update ran unconditionally after the parent resolved by any path, which would have left children falsely marked SUBMITTED even on a parent rejection.

**Tests** — `tests/test_broker.py`: 3 new unit tests for `IBApp.error()` routing (rejection writes the slot, 399 is excluded, unregistered reqId is safe). `tests/test_execution.py`: 6 new integration tests covering all three scenarios (rejection→REJECTED, 399-then-Submitted→SUBMITTED, pure-timeout→UNKNOWN) for both `_submit_order` and `_submit_bracket_order`, including verifying bracket children stay `PENDING_SUBMISSION` (not falsely SUBMITTED) when the parent is rejected or times out.

## Tasks Completed

1. **Route order-scoped IBKR errors into the status slot** — commit `0024a07`
   - `bravos/broker/connection.py`: `_ORDER_WARNING_CODES` allowlist + routing logic in `error()`
2. **Map slot results to honest DB statuses in executor** — commit `e88c621`
   - `bravos/execution/executor.py`: `IBKR_ERROR`→REJECTED, timeout→UNKNOWN, real callback→existing `_STATUS_MAP`, for both submit paths; conditional bracket-children bulk-update
3. **Regression tests for rejection/399/timeout status mapping** — commit `971aa0d`
   - `tests/test_broker.py` + `tests/test_execution.py`: 9 new tests total across both submit paths and the connection-layer routing

## Deviations from Plan

None in scope or approach — plan executed as written. Execution spanned three resumed sessions after unrelated interruptions (transcript continuity preserved via SendMessage resume; no work was lost, only re-verified).

## Verification

- `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_execution.py tests/test_broker.py -q` → 6 passed, 12 deselected (integration-marked DB tests correctly refuse to run without `BRAVOS_TEST_DB_OK=1`, per the Session 0b isolation guard — this is the guard working as designed, not a failure).
- Full suite with explicit opt-in: `BRAVOS_TEST_DB_OK=1 pytest tests/test_execution.py tests/test_broker.py -q -m ""` → 56 passed, 1 pre-existing failure, 1 pre-existing isolation-leak error (both documented below and confirmed unrelated to this task's changes).
- **All 9 new tests for this task passed.**
- Post-run production cleanup: the DB-authorized runs (across three resumed attempts) leaked fake `position_lots` rows on two separate occasions (caught by the Session 0b regression finalizer, `ISOLATION LEAK` failure, each time), plus `NVDA` rows the finalizer's fake-ticker-only pattern doesn't catch, plus 651 test-origin `signals` rows (`post_url LIKE 'http://test/%'`), 723 dependent `orders` rows, and 152 dependent `risk_gate_log` rows accumulated from repeated test executions. **Correction to an earlier claim in this same document**: an initial purge pass was declared complete but a subsequent test run re-leaked more fake rows before the first purge was verified with a fresh query — the independent `/gsd:quick` verifier (`260714-6gz-VERIFICATION.md`) caught this discrepancy by querying production directly. A second, thorough purge across all FK-dependent tables (`risk_gate_log` → `orders` → `signals` → `position_lots`) was then performed and confirmed via fresh post-commit SELECTs, not DELETE row counts alone. Final state: `position_lots` contains only the real `HROW` lot (109 shares, order 403 status FILLED, signal 2553 intact); zero fake-ticker or test-origin rows remain in any table.

### Pre-existing failures (out of scope, not caused by this change)

- `test_bracket_oca_group` — fails identically before and after this task's changes (confirmed via `git log` on the file: last touched by the unrelated `e2f1f74` 10268 fix on 2026-07-08, not by this session). Already documented as a known pre-existing failure in `.planning/IMPLEMENTATION_PLAN.md`'s ground rules.
- `test_reconcile_no_db_write_on_mismatch` — the Session 0b regression finalizer correctly flagged an isolation leak from this unrelated fixture-only test. Not introduced by this task; the finalizer performed exactly its intended function. Purged as described above. **Follow-up worth filing**: the finalizer's leak pattern (`^(OPN|PAR|FIF|SPN)_`) does not catch real-ticker leaks like the NVDA rows found here — its coverage should be widened or a broader "any lot with no corresponding signal_id/order" heuristic considered.

## Diff Scope

`git diff --stat` confirms only `bravos/broker/connection.py` (+31/-1), `bravos/execution/executor.py` (+100/-32 net across both submit paths), `tests/test_broker.py` (+44), `tests/test_execution.py` (+209) changed across the 3 commits. No changes to `bravos/ingestion/`, `scripts/`, or any file outside the plan's declared scope. Quantity calculation, risk gate, and all other trading logic untouched.

## Self-Check: PASSED

- FOUND: `bravos/broker/connection.py` contains `_ORDER_WARNING_CODES` and the `IBKR_ERROR` slot-write routing in `error()`
- FOUND: `bravos/execution/executor.py` contains `REJECTED`/`UNKNOWN` status mapping in both `_submit_order` and `_submit_bracket_order`; no remaining `.get(..., "SUBMITTED")` fallback
- FOUND: `tests/test_broker.py` contains `test_error_order_scoped_writes_ibkr_error`, `test_error_399_does_not_reject_accepted_order`, `test_error_unregistered_reqid_is_safe`
- FOUND: `tests/test_execution.py` contains 6 new tests covering rejection/399/timeout for both submit paths
- FOUND: commits `0024a07`, `e88c621`, `971aa0d` (`git log --oneline` on the worktree branch confirms all three)
