---
phase: 02-signal-ingestion
plan: 05
subsystem: infra
tags: [schedule, daemon, sigterm, selenium, integration-tests, pytest]

# Dependency graph
requires:
  - phase: 02-02
    provides: trade alert parser (parse_signal) used by scraper
  - phase: 02-03
    provides: BravosScraper with startup/process_alert/shutdown interface
  - phase: 02-04
    provides: Cloud SQL integration confirmed — dedup, raw_html, audit fields verified
provides:
  - Daemon entry point (scripts/run_ingestion.py) with schedule loop and SIGTERM graceful shutdown
  - Integration test suite (tests/test_ingestion_integration.py) covering login, session, fetch, store, dedup against live site
affects: [phase-3, deployment, systemd-service]

# Tech tracking
tech-stack:
  added: [schedule (in-process scheduler)]
  patterns:
    - "Daemon pattern: schedule.every(N).seconds.do(fn) + while not _shutdown: schedule.run_pending(); time.sleep(1)"
    - "SIGTERM handler sets global _shutdown flag — main loop exits cleanly, scraper.shutdown() called once"
    - "Integration tests use @pytest.mark.integration and scope='module' fixture for shared BravosScraper instance (one login per test run)"
    - "Session health check cycle: Gmail-triggered arch means alerts come via email; schedule loop keeps Chrome driver session warm"
    - "sys.path insert(0, repo_root) in daemon entry point — allows direct invocation without PYTHONPATH env var"

key-files:
  created:
    - scripts/run_ingestion.py
    - tests/test_ingestion_integration.py
  modified: []

key-decisions:
  - "Daemon schedule loop runs session health check (not category-page polling) — Gmail-triggered architecture means post URLs arrive via email; loop keeps Chrome driver warm"
  - "Integration tests use KNOWN_ALERT_URL env var (TEST_ALERT_URL) to allow specifying a real post URL on VM without hardcoding"

patterns-established:
  - "Integration test module fixture pattern: scope='module' live_scraper fixture — one BravosScraper.startup() shared across all tests in module"

requirements-completed: [INGST-02, INGST-07, AUDIT-01, AUDIT-02]

# Metrics
duration: ~5min
completed: 2026-05-09
---

# Phase 02 Plan 05: Ingestion Daemon + Integration Tests Summary

**schedule-based daemon with SIGTERM graceful shutdown wrapping BravosScraper, plus 5-test integration suite covering login/session/fetch/store/dedup against live bravosresearch.com — all 5 tests verified passing on bravos-vm1**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-05-09T05:53:04Z
- **Completed:** 2026-05-09T05:58:00Z
- **Tasks:** 2 of 2 (Task 2 human-verify: PASSED on bravos-vm1)
- **Files modified:** 2

## Accomplishments

- Created `scripts/run_ingestion.py` daemon with SIGTERM/SIGINT handler, schedule loop at 300s, BravosScraper session health check, and graceful Chrome driver shutdown
- Created `tests/test_ingestion_integration.py` with 5 integration tests (INGST-01/02/03/06/07 coverage): login, session check, fetch_post content, signal stored with raw_html, dedup end-to-end
- `pytest.ini` already had `integration` marker from prior plan — no change needed
- All 18 unit tests pass (parser + scraper); DB-dependent tests require Cloud SQL Auth Proxy (expected on dev machine)
- **Human verification (Task 2): PASSED on bravos-vm1** — 5/5 integration tests passed, daemon ran cleanly with startup/health-check/graceful-SIGTERM sequence confirmed
- Post-verification fix: added `sys.path.insert(0, repo_root)` to daemon entry point so it runs without requiring `PYTHONPATH` env var (commit c4d0e7b)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create daemon entry point and integration test** — `a94ddfc` (feat)
2. **Task 2: sys.path fix for direct daemon invocation** — `c4d0e7b` (fix) — adds repo root to sys.path so daemon works without PYTHONPATH
3. **Plan metadata: docs commit** — `4c538da` (docs) — summary + STATE.md at checkpoint

## Files Created/Modified

- `scripts/run_ingestion.py` — Daemon entry point: SIGTERM handler, schedule loop, BravosScraper session health check, graceful shutdown, sys.path fix for direct invocation
- `tests/test_ingestion_integration.py` — 5 integration tests marked `@pytest.mark.integration`, shared `live_scraper` module fixture, `TEST_ALERT_URL` env var override

## Decisions Made

- Daemon schedule cycle runs a session health-check (not category polling) — matches the Gmail-triggered architecture confirmed in 02-03: post URLs arrive via email, not category page polling. The loop keeps Chrome driver warm between alerts.
- Integration tests accept `TEST_ALERT_URL` env var to allow specifying a real member-accessible post URL on the VM without hardcoding a URL that may change.

## Deviations from Plan

### Auto-noted: Files pre-existed from prior work

Both files (`scripts/run_ingestion.py` and `tests/test_ingestion_integration.py`) were found already written but untracked by git — created during 02-03 selector discovery work (Gmail-triggered architecture update). The content was verified against all acceptance criteria before committing:
- `schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(...)` present
- `signal.signal(signal.SIGTERM, handle_shutdown)` present
- `from bravos.ingestion.scraper import BravosScraper` present
- `_scraper.shutdown()` in shutdown path
- `pytest.mark.integration`, `test_login_succeeds`, `test_dedup_end_to_end` all present

The daemon uses a session health-check cycle rather than the original plan's `scraper.run_cycle()` call — this aligns with the updated Gmail-triggered architecture from 02-03 and is architecturally correct.

### Post-verification fix (Rule 1 — Bug): sys.path for direct invocation

**Found during:** Task 2 (VM verification)
**Issue:** Running `python scripts/run_ingestion.py` directly failed with `ModuleNotFoundError: No module named 'bravos'` unless `PYTHONPATH` was set explicitly
**Fix:** Added `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` near top of `scripts/run_ingestion.py` so the repo root is always on the path
**Files modified:** `scripts/run_ingestion.py`
**Commit:** `c4d0e7b`

## Issues Encountered

None beyond the sys.path import fix resolved in c4d0e7b.

## Verification Results (Task 2 — bravos-vm1)

**Date:** 2026-05-09
**Environment:** bravos-vm1, GCP, Cloud SQL Auth Proxy active

**Integration tests (5/5 passed):**
- `test_login_succeeds` — PASSED
- `test_session_check_after_login` — PASSED
- `test_fetch_post_returns_content` — PASSED
- `test_signal_stored_with_raw_html` — PASSED
- `test_dedup_end_to_end` — PASSED

**Daemon run verified:**
- Startup sequence correct: "Starting ingestion daemon..." → session health check OK → graceful SIGTERM shutdown
- No crashes during observed run
- Chrome driver closed cleanly on shutdown

## Next Phase Readiness

- Daemon entry point complete — can be managed via systemd service in Phase 3/deployment
- Integration test suite verified against live site on bravos-vm1
- Phase 2 capstone: all scraping/parsing/storage components built, tested at unit level, wired into running daemon, and validated end-to-end on VM
- Phase 2 is COMPLETE — all 5 plans verified

## Known Stubs

None. `TEST_ALERT_URL` env var pattern is intentional (not a stub — it allows VM-specific URL injection without hardcoding).

## Self-Check: PASSED

- `scripts/run_ingestion.py` — confirmed exists (commits a94ddfc, c4d0e7b)
- `tests/test_ingestion_integration.py` — confirmed exists (commit a94ddfc)
- All 3 commits confirmed in git log: a94ddfc, c4d0e7b, 4c538da

---
*Phase: 02-signal-ingestion*
*Completed: 2026-05-09 — all tasks verified on bravos-vm1*
