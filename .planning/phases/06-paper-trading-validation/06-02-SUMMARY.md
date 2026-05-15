---
phase: 06-paper-trading-validation
plan: 02
subsystem: testing
tags: [ibkr, psycopg2, selenium, validation, paper-trading, ibapi]

# Dependency graph
requires:
  - phase: 06-01
    provides: green test suite with risk_gate_log migration applied, all Wave 0 stubs unskipped

provides:
  - scripts/validate_pipeline.py — runnable end-to-end pipeline validation harness (D-07)
  - validation/BUG-LOG.md — D-06 blocking-policy bug log scaffold
  - validation/VALIDATION-REPORT.md — SC-1..SC-4 pass/fail report scaffold

affects: [06-03-paper-trading-live-run, phase-07-deployment]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Two-connection psycopg2 model enforced: api-thread _ibapp._db_conn vs separate main-thread assertion conn"
    - "PASS/FAIL per-scenario validation loop with final summary totals"
    - "Empty URL_LIST guard with sys.exit(2) + operator instructions"

key-files:
  created:
    - scripts/validate_pipeline.py
    - validation/BUG-LOG.md
    - validation/VALIDATION-REPORT.md
  modified: []

key-decisions:
  - "URL_LIST left as guarded placeholder per D-01 — operator provides real URLs before Plan 03 run"
  - "Market hours gate NOT bypassed (D-10) — out-of-hours gate blocks reported as signal-only PASS (expected)"
  - "run_startup() replicates run_ingestion.py startup sequence exactly per RESEARCH Don't Hand-Roll"
  - "validation/ chosen as directory for BUG-LOG.md and VALIDATION-REPORT.md per CONTEXT Claude-discretion decision"

patterns-established:
  - "validate_pipeline.py IBApp startup: connect_and_run → run_startup_reconciliation → _db_conn install → reqPnL → start_heartbeat_monitor"
  - "assert_signal_processed(): signals → risk_gate_log → orders assertion chain"
  - "wait_for_fill(): polling loop (not bare sleep) for IB paper fill latency (RESEARCH Pitfall 2)"

requirements-completed: [IBKR-05, EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-05, EXEC-06, RISK-01, RISK-02, RISK-03, RISK-04, IBKR-04, POS-01, POS-02, POS-03]

# Metrics
duration: 20min
completed: 2026-05-15
---

# Phase 6 Plan 02: Pipeline Validation Harness Summary

**Scripted end-to-end validation harness (validate_pipeline.py) replicating run_ingestion.py IBApp startup with two-connection psycopg2 model, per-scenario PASS/FAIL assertions, and D-06 scaffold documents**

## Performance

- **Duration:** 20 min
- **Started:** 2026-05-15T13:32:03Z
- **Completed:** 2026-05-15T13:52:08Z
- **Tasks:** 2
- **Files created:** 3

## Accomplishments

- Built `scripts/validate_pipeline.py` (449 lines): replicates the exact IBApp startup sequence from `run_ingestion.py`, implements the two-connection psycopg2 model (api-thread `_ibapp._db_conn` vs separate main-thread assertion conn), asserts the full signals → risk_gate_log → orders chain per URL, polls for fill/position_lot rows, and prints one PASS/FAIL line per scenario with a final summary
- Created `validation/BUG-LOG.md` scaffold per D-04/D-05 blocking-policy (bugs found during Plan 03 are logged here with severity and fix reference)
- Created `validation/VALIDATION-REPORT.md` scaffold with SC-1..SC-4 success criteria rows, per-scenario results table, and live observation period section (INGST-07, IBKR-02, IBKR-04) — all marked PENDING until Plan 03 live run

## Task Commits

Each task was committed atomically:

1. **Task 1: Build scripts/validate_pipeline.py harness** - `ca379dc` (feat)
2. **Task 2: Create validation artifact scaffolds** - `4bb5e18` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `scripts/validate_pipeline.py` — Full pipeline validation harness (D-07): IBApp startup, two-connection DB model, assert_signal_processed(), wait_for_fill(), PASS/FAIL per scenario, final summary, empty URL_LIST guard
- `validation/BUG-LOG.md` — D-04/D-05 blocking-policy bug log scaffold
- `validation/VALIDATION-REPORT.md` — SC-1..SC-4 + per-scenario results + live observation period scaffold

## Decisions Made

- **URL_LIST left as guarded placeholder**: Per D-01, the operator provides real Bravos post URLs. The script validates the list is non-empty and exits with a CRITICAL message + clear instructions if not. Not inventing or scraping URLs per RESEARCH Don't Hand-Roll.
- **Market hours gate NOT bypassed**: Per D-10, the validation script reports honestly when the gate blocks (expected behavior) rather than forcing orders outside market hours. Out-of-hours gate blocks are treated as signal-only PASS.
- **`validation/` directory chosen**: Claude's discretion per CONTEXT D-06 — matches RESEARCH Recommended Directory Structure.
- **run_startup() replicates run_ingestion.py exactly**: Per RESEARCH Don't Hand-Roll principle — no reinvention of the startup sequence.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## Known Stubs

- `URL_LIST` in `scripts/validate_pipeline.py` is intentionally empty with a commented-out placeholder. This is by design (D-01): the operator populates this list with real Bravos post URLs before running Plan 03. The script guards against running with an empty list via `sys.exit(2)` and a CRITICAL log with step-by-step instructions.

## User Setup Required

Before running Plan 03:
1. Populate `URL_LIST` in `scripts/validate_pipeline.py` with 10+ real Bravos Trade Alert URLs covering all 4 action types (D-01/D-02)
2. Start IB Gateway in paper mode on bravos-vm1 (port 4002)
3. Run during NYSE market hours (09:30–16:00 ET) to exercise the order→fill path (D-10)

## Next Phase Readiness

- `scripts/validate_pipeline.py` is ready to run the moment the operator provides the URL list and IB Gateway is live
- `validation/BUG-LOG.md` and `validation/VALIDATION-REPORT.md` are pre-scaffolded for Plan 03 to fill in
- No blockers — Plan 03 proceeds when operator is ready with the real URL list and market hours access

---
*Phase: 06-paper-trading-validation*
*Completed: 2026-05-15*
