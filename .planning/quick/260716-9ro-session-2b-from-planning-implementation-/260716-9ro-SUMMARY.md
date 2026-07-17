---
phase: quick-260716-9ro
plan: 01
subsystem: runtime-state
tags: [architecture, runtime-state, pause-resume, singleton, refactor]
dependency-graph:
  requires: ["quick-260716-82s (db helper — landed first; call sites read current state)"]
  provides: ["bravos/runtime.py process-state module"]
  affects: ["ibapp singleton location", "pause/resume state location", "scraper no longer imports scripts"]
tech-stack:
  added: []
  patterns: ["single runtime module for process-wide mutable state; attribute-access (runtime.ibapp) instead of name imports to avoid stale bindings"]
key-files:
  created:
    - bravos/runtime.py
  modified:
    - bravos/ingestion/scraper.py
    - bravos/execution/executor.py
    - bravos/broker/connection.py
    - scripts/run_ingestion.py
    - scripts/execute_pending.py
    - scripts/validate_pipeline.py
    - tests/test_execution.py
    - tests/test_ingestion.py
    - tests/test_scraper.py
decisions:
  - "Canonical singleton lives at bravos.runtime.ibapp; readers use attribute access at call time (runtime.ibapp) — a from-import re-export is forbidden because it would bind a stale name on reassignment"
  - "connection.py keeps a mirror-written deprecated `ibapp` attribute for one release; all writers (run_ingestion main+resume, execute_pending, validate_pipeline) write runtime.ibapp and mirror broker_module.ibapp"
  - "run_ingestion's _ibkr_paused/_paused_signal_queue globals removed outright (no tests referenced them); scraper reads runtime.ibkr_paused/runtime.paused_signal_queue directly with no try/except import guard"
metrics:
  duration: "~45min across interrupted sessions"
  completed: "2026-07-17"
---

# Quick Task 260716-9ro: bravos/runtime.py shared process state Summary

Created `bravos/runtime.py` as the single home for process-wide mutable state (ibapp singleton, IBKR pause flag, paused-signal queue), removing the library→script import inversion where `bravos/ingestion/scraper.py` imported `scripts.run_ingestion` to read pause state.

## Tasks Completed

1. **Create runtime.py + migrate ibapp singleton** — commit `f581a8f`
   - `bravos/runtime.py`: `ibapp = None`, `ibkr_paused = False`, `paused_signal_queue = queue.Queue()`
   - executor.py's call-time read → `from bravos import runtime` + `runtime.ibapp`
   - Writers updated to set `runtime.ibapp` (mirroring `broker_module.ibapp` for one release): run_ingestion.py (main + handle_resume), execute_pending.py, validate_pipeline.py
   - Test patch sites re-pointed (3 in test_execution.py, 4 in test_ingestion.py)
2. **Pause state migration (TDD)** — commits `80d8005` (RED), `610c3d4` (GREEN)
   - scraper.py drops `import scripts.run_ingestion as _daemon` and its try/except fallback; reads runtime directly
   - handle_pause/handle_resume write `runtime.ibkr_paused`; queue flush reads `runtime.paused_signal_queue`; old globals removed
   - 2 new pure-mock pause-path tests in test_scraper.py

## Verification (run by orchestrator inline after executor interruption)

- Gate 1 — `grep -rn "import scripts" bravos/ --include="*.py"` → 0 rows
- Gate 2 — sanity imports (bravos.runtime, scraper, run_ingestion, execute_pending, check_positions, validate_pipeline) → IMPORTS_OK
- Suites: test_scraper 10 passed (incl. 2 new pause tests); test_broker 24 passed; test_execution 6 passed/12 deselected; test_db 3 passed; test_parser 17 passed + the documented pre-existing test_multi_ticker_forces_low_confidence failure; test_ingestion refused by the Session 0b production-DB guard — verified byte-identical refusal on main baseline (not a regression)
- No production DB writes at any point (no BRAVOS_TEST_DB_OK)

## Deviations from Plan

- Task 3's service restart deferred to the orchestrator (deploy after merge), per dispatch constraints.
- Final verification + SUMMARY were completed inline by the orchestrator after the executor was repeatedly interrupted by host-process exits; all three code commits are the executor's own.

## Self-Check: PASSED

- FOUND: bravos/runtime.py (ibapp, ibkr_paused, paused_signal_queue)
- FOUND: zero `import scripts` under bravos/
- FOUND: commits f581a8f, 80d8005, 610c3d4 on branch worktree-agent-9ro-86868
