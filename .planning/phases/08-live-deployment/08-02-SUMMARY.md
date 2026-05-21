---
phase: 08-live-deployment
plan: "02"
subsystem: infra
tags: [chrome, selenium, schedule, systemd, daemon, deployment]

requires:
  - phase: 08-01
    provides: systemd unit files (bravos-trading.service, bravos-gmail.service) and env.example template

provides:
  - "_restart_chrome_driver() function in scripts/run_ingestion.py — in-process nightly Chrome driver recycling at 06:00 UTC"
  - "schedule.every().day.at('06:00').do(_restart_chrome_driver) job registered in main()"
  - "scripts/run_gmail.py — placeholder Gmail poller stub that keeps bravos-gmail.service from thrashing"
  - "tests/test_deployment.py — 3 unit tests verifying the three documented behaviors of _restart_chrome_driver"

affects:
  - 08-03-live-cutover
  - bravos-gmail.service
  - bravos-trading.service

tech-stack:
  added: []
  patterns:
    - "In-process Chrome driver recycling: set _scraper=None BEFORE shutdown(), assign new instance only AFTER startup() succeeds"
    - "Schedule UTC convention: schedule library uses VM local time (UTC), so '06:00' fires at 01:00 ET winter / 02:00 ET summer"
    - "Daemon stub pattern: run_gmail.py logs PLACEHOLDER banner + sleeps in 60s ticks so systemd Restart=always does not thrash"

key-files:
  created:
    - scripts/run_gmail.py
    - tests/test_deployment.py
  modified:
    - scripts/run_ingestion.py

key-decisions:
  - "Schedule string '06:00' (UTC) not '01:00' — schedule library uses VM local time; bravos-vm1 is UTC. '01:00' would fire at 8pm ET previous day, interrupting market hours"
  - "DST limitation accepted: 06:00 UTC = 01:00 ET winter / 02:00 ET summer. Both windows fall inside safe interval (after 12:15am ET Gateway restart, before 4am ET pre-market)"
  - "_scraper=None guard set BEFORE old_scraper.shutdown() so concurrent run_cycle() fires return early via existing null-guard at line 92"
  - "New BravosScraper instance only assigned to _scraper AFTER startup() succeeds — half-initialized instances (driver=None) must never reach process_alert()"
  - "run_gmail.py is a stub only — no imaplib, no process_alert integration; INGST-V2-01 is a separate future GSD phase"

patterns-established:
  - "TDD gate compliance: test(08-02) RED commit (7373723) precedes feat(08-02) GREEN commit (5bfbfcb)"

requirements-completed:
  - DEPL-02

duration: 8min
completed: "2026-05-21"
---

# Phase 8 Plan 02: Nightly Chrome Driver Restart and Gmail Poller Stub Summary

**In-process nightly Chrome driver recycling at 06:00 UTC via _restart_chrome_driver() with None-before-shutdown concurrency guard, plus run_gmail.py placeholder stub preventing bravos-gmail.service thrash**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-21T10:36:59Z
- **Completed:** 2026-05-21T10:40:45Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- Added `_restart_chrome_driver()` to `scripts/run_ingestion.py` with the correct concurrency-safe guard ordering: `_scraper=None` before shutdown, assignment after startup succeeds
- Registered `schedule.every().day.at("06:00").do(_restart_chrome_driver)` in `main()` — fires at 01:00 ET winter / 02:00 ET summer, inside the safe window after Gateway restart
- Created `scripts/run_gmail.py` stub that logs a PLACEHOLDER/INGST-V2-01 banner and sleeps forever, preventing `bravos-gmail.service` from thrashing under `Restart=always`
- Created `tests/test_deployment.py` with 3 passing unit tests covering shutdown lifecycle, None guard ordering, and startup failure swallowing

## Task Commits

1. **Task 3: Create tests/test_deployment.py (TDD RED)** - `7373723` (test)
2. **Task 1: Add _restart_chrome_driver() and 06:00 UTC schedule job** - `5bfbfcb` (feat / TDD GREEN)
3. **Task 2: Create scripts/run_gmail.py stub** - `481862e` (feat)

_Note: Plan tasks were committed in TDD order — test commit (RED) precedes implementation commit (GREEN)._

## Files Created/Modified

- `scripts/run_ingestion.py` — Added `_restart_chrome_driver()` function (lines 134-167) and `schedule.every().day.at("06:00")` job registration (lines 273-274) in `main()`
- `scripts/run_gmail.py` — New file: Gmail poller placeholder daemon stub for `bravos-gmail.service`
- `tests/test_deployment.py` — New file: 3 unit tests for `_restart_chrome_driver` behaviors (D-02/D-03)

## Test Results

All 3 tests pass under `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_deployment.py -v`:

- `test_nightly_chrome_restart` — PASSED: old shutdown called once, new instance constructed and started, module._scraper points at new instance
- `test_restart_sets_scraper_none_during_transition` — PASSED: `_scraper` is None at the exact moment `shutdown()` fires (concurrent run_cycle guard verified)
- `test_restart_handles_startup_failure` — PASSED: RuntimeError from `startup()` is swallowed, `_scraper` remains None, daemon continues

## Decisions Made

- **Schedule time is `"06:00"` UTC (not `"01:00"`)** — the `schedule` library uses VM local time, and bravos-vm1 runs UTC. Using `"01:00"` would fire at 01:00 UTC = ~8pm ET the previous day, in the middle of after-hours trading. This matches the existing `"14:30"` UTC convention for the RiskGate reset.
- **DST limitation accepted** — 06:00 UTC = 01:00 ET winter (EST UTC-5) / 02:00 ET summer (EDT UTC-4). Both windows are within the safe interval (after ~12:15am ET IB Gateway restart, before 4am ET pre-market opens). Documented in code comments per D-03.
- **`_scraper=None` guard before shutdown** — run_cycle's existing null-guard at line 92 (`if _scraper is None: return`) makes this safe for concurrent access without a threading lock. Test 2 verifies this contract.
- **run_gmail.py is a pure stub** — no IMAP, no `process_alert`, no Gmail polling logic. Acceptance criteria forbid `imaplib` and `process_alert` references. INGST-V2-01 is a tracked future feature with its own GSD phase.

## Deviations from Plan

None — plan executed exactly as written. All three tasks matched plan actions and acceptance criteria. Tests written in TDD RED commit before implementation GREEN commit.

## TDD Gate Compliance

- RED commit: `7373723` — `test(08-02): add failing tests for _restart_chrome_driver behavior`
- GREEN commit: `5bfbfcb` — `feat(08-02): add _restart_chrome_driver() and 06:00 UTC schedule job`

Gate sequence is valid: RED precedes GREEN.

## Issues Encountered

None.

## Known Stubs

- `scripts/run_gmail.py` is intentionally a stub. The `main()` function logs a PLACEHOLDER banner and sleeps indefinitely. No Gmail polling is implemented. This is tracked as INGST-V2-01 and will be implemented in a future GSD phase. The stub is required now so `bravos-gmail.service` has a valid ExecStart target without service thrash.

## Threat Surface Scan

No new threat surface beyond what the plan's `<threat_model>` already documented. Confirmed:
- T-08-06 (DoS via startup failure leaving daemon scraperless): mitigated by Test 3 + run_cycle null-guard
- T-08-09 (concurrent run_cycle on half-initialized scraper): mitigated by Test 2 + None-before-shutdown ordering
- T-08-10 (run_gmail.py masking Gmail polling absence): mitigated by PLACEHOLDER banner in startup log

## Next Phase Readiness

- `08-03-PLAN.md` (live cutover) can use `scripts/run_ingestion.py` and `scripts/run_gmail.py` as-is on bravos-vm1
- `bravos-trading.service` (from 08-01) points at `scripts/run_ingestion.py` — `_restart_chrome_driver` will activate automatically at first 06:00 UTC tick after daemon start
- `bravos-gmail.service` (from 08-01) points at `scripts/run_gmail.py` — stub runs cleanly without thrash

---
*Phase: 08-live-deployment*
*Completed: 2026-05-21*
