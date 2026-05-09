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

**schedule-based daemon with SIGTERM graceful shutdown wrapping BravosScraper, plus 5-test integration suite covering login/session/fetch/store/dedup against live bravosresearch.com**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-05-09T05:53:04Z
- **Completed:** 2026-05-09T05:58:00Z
- **Tasks:** 1 of 2 (Task 2 is a human-verify checkpoint on bravos-vm1)
- **Files modified:** 2

## Accomplishments

- Created `scripts/run_ingestion.py` daemon with SIGTERM/SIGINT handler, schedule loop at 300s, BravosScraper session health check, and graceful Chrome driver shutdown
- Created `tests/test_ingestion_integration.py` with 5 integration tests (INGST-01/02/03/06/07 coverage): login, session check, fetch_post content, signal stored with raw_html, dedup end-to-end
- `pytest.ini` already had `integration` marker from prior plan — no change needed
- All 18 unit tests pass (parser + scraper); DB-dependent tests require Cloud SQL Auth Proxy (expected on dev machine)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create daemon entry point and integration test** - `a94ddfc` (feat)

**Task 2 (human-verify):** Pending — requires running daemon on bravos-vm1 and verifying signals in Cloud SQL.

**Plan metadata:** (docs commit — this summary)

## Files Created/Modified

- `scripts/run_ingestion.py` — Daemon entry point: SIGTERM handler, schedule loop, BravosScraper session health check, graceful shutdown
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

## Issues Encountered

None.

## User Setup Required

**Task 2 (blocking checkpoint) requires human validation on bravos-vm1:**

1. SSH into bravos-vm1:
   ```bash
   gcloud compute ssh bravos-vm1 --zone=us-central1-a
   ```

2. Run integration tests (set `TEST_ALERT_URL` to a real Trade Alert post URL):
   ```bash
   cd ~/bravos
   TEST_ALERT_URL="https://bravosresearch.com/your-real-post-url/" \
   BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password) \
     ~/miniconda3/bin/pytest tests/test_ingestion_integration.py -m integration -x -v
   ```
   Expected: 5 tests pass.

3. Run the daemon for 1-2 cycles (~6 minutes):
   ```bash
   cd ~/bravos
   BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password) \
     timeout 360 python scripts/run_ingestion.py
   ```
   Expected log sequence:
   - "Starting ingestion daemon..."
   - "BravosScraper started — driver active, logged in"
   - "Running initial scrape cycle..."
   - "Session health check: OK — driver active and authenticated"
   - "Ingestion daemon started — polling every 300s"
   - After ~5 min: second health check cycle
   - After timeout: "Received signal 15 — initiating graceful shutdown"

4. Verify signals in database (signals arrive only when `process_alert(url)` is called by Gmail poller — check existing rows):
   ```bash
   PGPASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password) \
     psql -h 127.0.0.1 -U bravos -d bravos_trading \
     -c "SELECT id, ticker, action_type, confidence, parse_method, scraped_at FROM signals ORDER BY id DESC LIMIT 10;"
   ```

5. Resume signal: type "phase 2 verified" if pipeline works, or describe any issues found.

## Next Phase Readiness

- Daemon entry point complete — can be managed via systemd service in Phase 3/deployment
- Integration test suite ready for VM execution — gated on human verification
- Phase 2 capstone: all scraping/parsing/storage components built, tested at unit level, and wired into the running daemon
- Blocking checkpoint: Task 2 human-verify must pass before Phase 2 can be marked complete

## Known Stubs

- `KNOWN_ALERT_URL` defaults to `https://bravosresearch.com/?p=1` (placeholder) — must be overridden via `TEST_ALERT_URL` env var on VM to point to a real member-accessible Trade Alert post

---
*Phase: 02-signal-ingestion*
*Completed: 2026-05-09 (Task 1 complete; Task 2 pending human verification)*
