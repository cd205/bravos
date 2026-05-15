---
phase: 06-paper-trading-validation
plan: "01"
subsystem: test-suite
tags: [test-fix, migration, schema, broker-tests]
dependency_graph:
  requires: []
  provides: [green-test-suite, risk_gate_log-table]
  affects: [tests/test_execution.py, tests/test_broker.py]
tech_stack:
  added: []
  patterns: [os.urandom unique test ids, mock._count_open_positions for gate isolation]
key_files:
  created: []
  modified:
    - tests/test_execution.py
    - tests/test_broker.py
decisions:
  - "Patch RiskGate._count_open_positions in test_gate_log_pass alongside _is_market_hours — live DB accumulates position_lots from prior test runs, breaking the test's ability to reach the pass path"
  - "Use os.urandom(3) + 10000 offset for unique ibkr_order_id in test_order_db_write_pending — same os.urandom pattern already used for post_url in file"
metrics:
  duration: "~12min"
  completed: "2026-05-15T13:11:46Z"
  tasks_completed: 3
  files_modified: 2
---

# Phase 6 Plan 1: Green Test Suite — Apply Migration + Fix 3 Failing Tests Summary

Achieved a fully green test suite (69 passed, 0 failed, 0 skipped) by applying the risk_gate_log schema migration, fixing two test isolation bugs, and unskipping 8 Wave 0 broker stubs.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Apply migrate_phase4.sql + fix test_gate_log_pass isolation | a0feaed | tests/test_execution.py |
| 2 | Fix test_order_db_write_pending stale-row contamination | b3d92d3 | tests/test_execution.py |
| 3 | Unskip 8 Wave 0 broker stubs (plan 03-1) | 32aec32 | tests/test_broker.py |

## Verification

Full standard suite result:

```
69 passed, 0 failed, 0 skipped in 14.58s
```

Baseline was 58 passed, 3 failed, 8 skipped. Target achieved exactly.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_gate_log_pass false Gate 2 block from accumulated live DB positions**

- **Found during:** Task 1 verification
- **Issue:** After applying the migration, `test_gate_log_pass` still failed: the live DB has 68 open position_lots from prior test runs, exceeding MAX_OPEN_POSITIONS=20. Gate 2 fired and blocked the test from reaching the pass path. The test only mocked `_is_market_hours` but not `_count_open_positions`.
- **Fix:** Added `patch.object(RiskGate, "_count_open_positions", return_value=0)` alongside `_is_market_hours` mock. The test intent is to verify `risk_gate_log` row written on a gate-pass; eliminating environmental DB noise is correct isolation.
- **Files modified:** tests/test_execution.py (lines 138-141, patch context added)
- **Commit:** a0feaed

## Known Stubs

None — all files wired with real implementations. No placeholder text or empty data flows detected.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced. Migration applied `CREATE TABLE IF NOT EXISTS risk_gate_log` (idempotent DDL, pre-existing in codebase). Password sourced from PGPASSWORD env var, not committed (T-06-02 disposition: mitigate — satisfied).

## Self-Check: PASSED

- tests/test_execution.py: FOUND (modified)
- tests/test_broker.py: FOUND (modified)
- Commit a0feaed: FOUND
- Commit b3d92d3: FOUND
- Commit 32aec32: FOUND
- Full suite: 69 passed, 0 failed, 0 skipped (verified)
