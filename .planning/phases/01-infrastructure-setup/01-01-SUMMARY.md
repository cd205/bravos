---
phase: 01-infrastructure-setup
plan: 01
subsystem: testing
tags: [pytest, selenium, psycopg2, test-stubs, wave-0]

requires: []
provides:
  - "pytest test scaffold with 8 skip-marked infrastructure stubs"
  - "conftest.py fixtures: db_connection, chrome_options"
  - "pytest.ini with testpaths=tests and timeout=60"
  - "requirements-dev.txt with pinned pytest and pytest-timeout versions"
affects:
  - 01-02-postgresql
  - 01-03-secrets
  - 01-04-chrome
  - 01-05-ibgateway
  - 01-06-python-env
  - all phase-1 plans (test stubs must be un-skipped as components are installed)

tech-stack:
  added:
    - "pytest==8.3.5"
    - "pytest-timeout==2.3.1"
  patterns:
    - "Skip-decorated test stubs: write full test body, use @pytest.mark.skip; remove decorator when component is installed"
    - "Fixture isolation: db_connection and chrome_options defined in conftest.py, usable by all tests"

key-files:
  created:
    - "pytest.ini"
    - "requirements-dev.txt"
    - "tests/conftest.py"
    - "tests/test_infrastructure.py"
  modified: []

key-decisions:
  - "Used @pytest.mark.skip (full body written) rather than pass stubs — subsequent plans simply remove the decorator"
  - "db_connection fixture falls back to 'change_me_at_deploy' password when BRAVOS_DB_PASSWORD env var is not set"
  - "chrome_options fixture includes both stability flags and anti-detection flags per selenium-scraper skill"

patterns-established:
  - "Wave 0 test stubs: all infrastructure tests written up-front with skip markers; guards against broken installs"
  - "conftest.py as the single source for shared test fixtures"

requirements-completed:
  - DEPL-01
  - DEPL-03
  - DEPL-04
  - DEPL-05

duration: 8min
completed: 2026-05-03
---

# Phase 01 Plan 01: Infrastructure Test Scaffold Summary

**pytest Wave 0 scaffold with 8 skip-marked stubs covering DEPL-01/03/04/05 — all tests collect and skip cleanly from a fresh clone**

## Performance

- **Duration:** 8 min
- **Started:** 2026-05-03T06:27:33Z
- **Completed:** 2026-05-03T06:35:00Z
- **Tasks:** 1 of 2 (Task 2 is a human-action checkpoint — operator must provision VM)
- **Files modified:** 4

## Accomplishments

- Created `pytest.ini` with testpaths and timeout settings
- Created `tests/conftest.py` with `db_connection` and `chrome_options` fixtures
- Created `tests/test_infrastructure.py` with 8 fully-written, skip-decorated test stubs
- `pytest --co` collects all 8 tests; `pytest -q` shows 8 skipped with 0 failures

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Wave 0 validation infrastructure** - `aa67a65` (chore)

Task 2 is a `checkpoint:human-action` — the operator must SSH into opt-trade-vm4 and provision bravos_vm1. No code commit for Task 2.

## Files Created/Modified

- `/home/chris_s_dodd/bravos/pytest.ini` — pytest config: testpaths=tests, timeout=60
- `/home/chris_s_dodd/bravos/requirements-dev.txt` — pytest==8.3.5, pytest-timeout==2.3.1
- `/home/chris_s_dodd/bravos/tests/conftest.py` — db_connection fixture (psycopg2), chrome_options fixture (Selenium)
- `/home/chris_s_dodd/bravos/tests/test_infrastructure.py` — 8 skip-marked test stubs

## Decisions Made

- Wrote full test bodies inside skip-decorated functions rather than `pass` — future plans remove the skip, not rewrite the test
- `db_connection` fixture reads password from `BRAVOS_DB_PASSWORD` env var with `change_me_at_deploy` fallback
- `chrome_options` includes all anti-detection flags from the selenium-scraper skill (production-verified)

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## Checkpoint: Task 2 Pending Human Action

Task 2 requires the operator to:

1. SSH into `opt-trade-vm4` and capture version output (OS, Python, IB Gateway, IBC, PostgreSQL, Chrome)
2. Provision `bravos-vm1` on GCP with: e2-standard-2, 50GB SSD, Ubuntu LTS, service account attached
3. Verify SSH access via `gcloud compute ssh bravos-vm1 --zone=us-east1-b --command="echo ok"`

When complete, the operator should paste the opt-trade-vm4 version output and VM creation output. The continuation agent will document the versions in this SUMMARY under "opt-trade-vm4 Reference Versions" and "bravos-vm1 Provisioning Outcome."

## Next Phase Readiness

- Wave 0 test scaffold is complete and committed
- Subsequent plans (01-02 through 01-07) can immediately reference tests/test_infrastructure.py
- Each plan removes the `@pytest.mark.skip` decorator from its corresponding test once the component is installed
- bravos_vm1 provisioning is pending operator action (Task 2)

---
*Phase: 01-infrastructure-setup*
*Completed: 2026-05-03*
