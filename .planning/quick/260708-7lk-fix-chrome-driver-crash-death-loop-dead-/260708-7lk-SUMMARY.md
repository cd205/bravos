---
phase: quick-260708-7lk
plan: 01
subsystem: ingestion
tags: [scraper, selenium, chromedriver, crash-safety, self-healing, schedule]
requires: []
provides:
  - crash-safe _check_session catching transport-level failures (MaxRetryError/ConnectionError)
  - shared _recover_scraper driver-recreation primitive
  - self-healing crash-safe run_cycle with consecutive-failure alerting
affects: [bravos/ingestion/scraper.py, scripts/run_ingestion.py]
tech-stack:
  added: []
  patterns:
    - "transport-level exceptions (urllib3 MaxRetryError, ConnectionError) treated as dead-driver signals alongside WebDriverException"
    - "single shared recovery primitive reused by nightly restart and per-cycle self-healing"
key-files:
  created: []
  modified:
    - bravos/ingestion/scraper.py
    - scripts/run_ingestion.py
    - tests/test_scraper.py
    - tests/test_ingestion.py
decisions:
  - "Threshold alert uses >= (fires every failing cycle at/beyond 3) mirroring the pre-existing per-cycle re-auth alert pattern"
  - "run_cycle null-guard now retries recovery each cycle (deviation from literal plan text — see Deviations)"
  - "Old per-cycle 'Scraper Re-Authentication Failed' alert replaced by recovery flow + threshold alert (login failure now self-heals instead of only alerting)"
metrics:
  duration: ~35min (across interrupted session)
  completed: 2026-07-08
---

# Quick Task 260708-7lk: Fix Chrome Driver Crash Death-Loop Summary

**One-liner:** Dead chromedriver (urllib3 MaxRetryError) can no longer escape _check_session/run_cycle — run_cycle is @catch_cycle_exceptions-decorated and self-heals via a shared _recover_scraper primitive, with email alert after 3 consecutive failed recoveries.

## What Was Done

### Task 1 — Crash-safe _check_session (commit 343972b)
- `bravos/ingestion/scraper.py`: `_check_session` now catches `(WebDriverException, MaxRetryError, ConnectionError)` instead of only `WebDriverException`. A dead chromedriver fails at the HTTP transport layer (urllib3 raises `MaxRetryError` / `ConnectionRefusedError` before Selenium can wrap it), which previously escaped and crashed the caller. Returns False, never raises; callers own the recovery decision.
- Added `from urllib3.exceptions import MaxRetryError` import (transitive dep of selenium, already installed).
- Tests: `test_check_session_returns_false_on_dead_driver` (MaxRetryError) and `test_check_session_returns_false_on_connection_refused` (ConnectionRefusedError) in `tests/test_scraper.py`.

### Task 2 — Self-healing crash-safe run_cycle (commit caa93e6)
- `scripts/run_ingestion.py`:
  - Extracted the driver-recreation body of `_restart_chrome_driver()` into module-level `_recover_scraper(reason: str) -> bool`; `_restart_chrome_driver` (06:00 UTC nightly job, registration unchanged) now delegates to it — one shared primitive, no duplication.
  - Added `_attempt_driver_recovery(reason)` helper wrapping `_recover_scraper` with the consecutive-failure counter (`_consecutive_recovery_failures`, `RECOVERY_ALERT_THRESHOLD = 3`) and threshold email alert via `send_alert` ("Scraper Driver Recovery Failing", includes ISO timestamp + failure count + keeps-retrying note).
  - `run_cycle` is now decorated with `@catch_cycle_exceptions` (imported from `bravos.ingestion.scraper`) so nothing escapes into `schedule.run_pending()` — schedule always records the run, killing the every-second retry storm and keeping the 06:00 restart and 14:30 RiskGate reset jobs firing.
  - Dead-driver detection: on failed session check, the cheap `_login()` re-auth path runs first, guarded against `(WebDriverException, MaxRetryError, ConnectionError)`; if login returns False or raises, the driver is treated as dead and `_attempt_driver_recovery("driver-dead recovery")` recreates it on the same cycle (no waiting for 06:00 UTC).
  - Recovery guarded: if `startup()` raises, `_scraper` stays None and the daemon keeps running.
  - Periodic-reconciliation block at the end of run_cycle unchanged.
- Alert socket path verified (no modification needed): `process_alert` calls the now-crash-safe `_check_session` and is itself decorated with `@catch_cycle_exceptions`; the socket handler's own try/except logs and replies `ERR`. A URL arriving during driver death cannot crash the socket thread.

### Task 3 — Regression tests (commit 98c3391)
- `tests/test_ingestion.py`: new pure-mock section (no DB fixture, no live Chrome), `import scripts.run_ingestion as daemon` with monkeypatched globals:
  - `test_run_cycle_survives_dead_driver` — no exception escapes.
  - `test_run_cycle_triggers_recovery_on_dead_driver` — `_recover_scraper` called with a reason containing "recovery".
  - `test_run_cycle_login_recovers_expired_session_without_recreation` — cheap re-login path skips recreation.
  - `test_run_cycle_alerts_after_threshold_failures` — after 3 failed recoveries `send_alert` fired; run_cycle never raised across iterations.

## Verification

- `python -m pytest tests/test_scraper.py tests/test_ingestion.py -q` → **15 passed** (8 scraper incl. 2 new, 3 live-DB ingestion, 4 new run_cycle).
- Task 2 AST verify (`_recover_scraper` function exists) → PASS.
- `import scripts.run_ingestion` → clean; committed HEAD version also exec-verified standalone (run_cycle confirmed wrapped by decorator).
- Manual read: `_check_session` catches MaxRetryError/ConnectionError; `run_cycle` decorated and calls `_recover_scraper` on dead-driver detection; `_restart_chrome_driver` and `run_cycle` share the single primitive.
- Per-commit deletion check: no file deletions in any commit.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] run_cycle null-guard now retries recovery**
- **Found during:** Task 2
- **Issue:** The plan's literal text ("leave _scraper=None; the next cycle's null-guard returns early and the cycle after retries recovery") is internally inconsistent: with a plain early-return null-guard, a single failed recovery would leave `_scraper=None` forever (until 06:00), recovery would never retry, and the counter could never reach the alert threshold — contradicting the plan's own must_have truths ("retrying continues every cycle", "leaves _scraper=None and the daemon keeps running (retries next cycle)").
- **Fix:** The null-guard path calls `_attempt_driver_recovery("scraper-missing recovery")` (same counter/alert bookkeeping) instead of only logging, then returns. Safe: the schedule loop is single-threaded, so no concurrent-recovery race with the nightly job.
- **Files modified:** scripts/run_ingestion.py
- **Commit:** caa93e6

**2. [Minor] Threshold alert fires at >= threshold (every failing cycle from the 3rd on), not only exactly once**
- Rationale: mirrors the pre-existing per-cycle re-auth alert pattern the plan told us to follow, and a transient email failure cannot permanently suppress the alert. Test contract unaffected.

### Notes
- The old per-cycle "Scraper Re-Authentication Failed" alert was replaced by the recovery flow: login failure now triggers driver recreation (usually self-heals silently); persistent failure alerts via the new threshold alert. This matches the plan's Step C flow, which does not retain the old alert.
- Working tree contained unrelated pre-existing uncommitted WIP (IBKR pause/resume) intermixed in `scraper.py` and `run_ingestion.py`. Task commits were hunk-staged (`git apply --cached`) so only this task's changes were committed; the WIP remains untouched in the working tree.

## Commits

| Task | Commit | Message |
| ---- | ------ | ------- |
| 1 | 343972b | fix(quick-260708-7lk): make _check_session crash-safe against dead chromedriver |
| 2 | caa93e6 | fix(quick-260708-7lk): self-healing crash-safe run_cycle via shared _recover_scraper |
| 3 | 98c3391 | test(quick-260708-7lk): regression tests for crash-safe self-healing run_cycle |

## Known Stubs

None — no placeholder values or unwired data paths introduced.

## Self-Check: PASSED

- bravos/ingestion/scraper.py — FOUND (contains MaxRetryError catch)
- scripts/run_ingestion.py — FOUND (contains _recover_scraper, @catch_cycle_exceptions on run_cycle)
- tests/test_scraper.py — FOUND (2 new dead-driver tests)
- tests/test_ingestion.py — FOUND (4 new run_cycle tests)
- Commits 343972b, caa93e6, 98c3391 — FOUND in git log
