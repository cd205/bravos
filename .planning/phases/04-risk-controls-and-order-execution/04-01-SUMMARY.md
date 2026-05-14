---
phase: 04-risk-controls-and-order-execution
plan: "01"
subsystem: risk-scaffold
tags: [risk-controls, settings, scaffold, test-stubs, migration]
dependency_graph:
  requires: []
  provides:
    - infra/migrate_phase4.sql (risk_gate_log DDL)
    - bravos/config/settings.py (risk control constants)
    - bravos/risk (importable package)
    - bravos/execution (importable package)
    - tests/test_execution.py (Wave-0 skip-stub tests)
  affects:
    - plans 04-03 (RiskGate implements gate.py, unskips 8 tests)
    - plans 04-04 (Executor implements executor.py, unskips 7 tests)
tech_stack:
  added: []
  patterns:
    - os.environ.get with typed cast for env-var overridable constants
    - Wave-0 skip-stub test pattern (full bodies inside @pytest.mark.skip)
    - CREATE TABLE IF NOT EXISTS idempotent migration (Phase 2 precedent)
key_files:
  created:
    - infra/migrate_phase4.sql
    - bravos/risk/__init__.py
    - bravos/execution/__init__.py
    - tests/test_execution.py
  modified:
    - bravos/config/settings.py
decisions:
  - Phase 4 follows Phase 2 precedent — migration file written but NOT applied (Cloud SQL Auth Proxy not running on dev VM; apply on bravos-vm1)
  - Wave-0 test stubs use lazy imports inside test bodies so pytest collection succeeds before gate.py/executor.py exist
  - risk/__init__.py and execution/__init__.py use single-line docstrings matching broker/__init__.py (not the comment style)
metrics:
  duration: "4 minutes"
  completed_date: "2026-05-14T10:30:09Z"
  tasks_completed: 4
  files_changed: 5
---

# Phase 04 Plan 01: Phase 4 Scaffold Summary

Phase 4 scaffold — risk_gate_log DDL migration, four typed env-var risk constants in settings.py, two empty Python package markers, and 15 Wave-0 skip-stub tests covering RISK-01..04 and EXEC-01..04.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create infra/migrate_phase4.sql with risk_gate_log DDL | a7bf335 | infra/migrate_phase4.sql |
| 2 | Add risk control constants to bravos/config/settings.py | 49a1c60 | bravos/config/settings.py |
| 3 | Create empty package __init__.py files for bravos/risk/ and bravos/execution/ | 284855a | bravos/risk/__init__.py, bravos/execution/__init__.py |
| 4 | Create tests/test_execution.py with 15 Wave-0 skip-stub tests | 5a75319 | tests/test_execution.py |

## Verification Results

- `infra/migrate_phase4.sql`: CREATE TABLE IF NOT EXISTS risk_gate_log with 12 columns, FK to signals(id), GRANT statements — PASS
- `bravos/config/settings.py`: MAX_OPEN_POSITIONS=20, MAX_ALLOCATION_PCT=0.25, DAILY_LOSS_THRESHOLD=-5000.0, WEIGHT_PCT_PER_UNIT=0.05 — all importable with correct defaults — PASS
- `bravos.risk` and `bravos.execution` importable — PASS
- `tests/test_execution.py`: 15 skipped in 0.03s, 0 errors — PASS
- Existing tests: 27 passed, 8 skipped (no regressions) — PASS

## Decisions Made

1. **Migration not applied live** — Following Phase 2 precedent: migration file written and committed, but NOT executed against Cloud SQL. Cloud SQL Auth Proxy not running on dev VM. Apply on bravos-vm1 before Plan 04-03 runs integration tests that need risk_gate_log.

2. **Lazy imports in test stubs** — All `from bravos.risk.gate import ...` and `from bravos.execution.executor import ...` calls are inside test function bodies. This is required per T-04-02 threat mitigation: pytest collection must succeed before plans 04-03/04-04 create those modules.

3. **Single-line docstring package markers** — `bravos/risk/__init__.py` and `bravos/execution/__init__.py` each have one docstring line, matching the docstring style used in `bravos/broker/__init__.py` (not the comment style).

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. The test file intentionally contains skipped tests; these are Wave-0 stubs, not data stubs. The skip decorators are removed by downstream plans 04-03 and 04-04 when the implementing modules land.

## Threat Flags

None. All new files are internal scaffolding with no new network endpoints, auth paths, or trust boundaries.

## Self-Check: PASSED

- [x] infra/migrate_phase4.sql exists
- [x] bravos/config/settings.py has 4 new constants
- [x] bravos/risk/__init__.py exists and is importable
- [x] bravos/execution/__init__.py exists and is importable
- [x] tests/test_execution.py has 15 skipped tests
- [x] Commits a7bf335, 49a1c60, 284855a, 5a75319 all present
