---
phase: 01-infrastructure-setup
plan: 01
subsystem: testing
tags: [pytest, selenium, psycopg2, test-stubs, wave-0, gcp-vm, opt-trade-vm4, miniconda, ibapi]

requires: []
provides:
  - "pytest test scaffold with 8 skip-marked infrastructure stubs"
  - "conftest.py fixtures: db_connection, chrome_options"
  - "pytest.ini with testpaths=tests and timeout=60"
  - "requirements-dev.txt with pinned pytest and pytest-timeout versions"
  - "confirmed opt-trade-vm4 versions for all components (Python, ibapi, IBC, Gateway, Chrome, OS)"
  - "01-DECISIONS.md documenting 7 deviations from original CONTEXT.md decisions"
  - "01-RESEARCH.md updated with CONFIRMED values (was LOW/MEDIUM confidence)"
affects:
  - 01-02-ibgateway
  - 01-03-postgresql
  - 01-04-secrets
  - 01-05-chrome
  - 01-06-python-env
  - 01-07-validation
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
    - ".planning/phases/01-infrastructure-setup/01-DECISIONS.md"
  modified:
    - ".planning/phases/01-infrastructure-setup/01-RESEARCH.md"

key-decisions:
  - "Used @pytest.mark.skip (full body written) rather than pass stubs — subsequent plans simply remove the decorator"
  - "db_connection fixture falls back to 'change_me_at_deploy' password when BRAVOS_DB_PASSWORD env var is not set"
  - "chrome_options fixture includes both stability flags and anti-detection flags per selenium-scraper skill"
  - "DEV-01: Python 3.13.5 + miniconda3 (supersedes D-12 Python 3.11 + D-13 venv)"
  - "DEV-02: pip install ibapi==9.81.1.post1 (supersedes D-15 official zip)"
  - "DEV-03: Ubuntu 24.04 LTS (Noble) — use ubuntu-2404-lts-amd64 image"
  - "DEV-04: ibgateway.service Restart=always, RestartSec=15 (not Restart=no)"
  - "DEV-05/06/07: IBC at /opt/ibcalpha/, Gateway at /opt/ibgateway/, Xvfb display :99"

patterns-established:
  - "Wave 0 test stubs: all infrastructure tests written up-front with skip markers; guards against broken installs"
  - "conftest.py as the single source for shared test fixtures"
  - "Mirror opt-trade-vm4 exactly: confirm versions before installing on bravos_vm1"

requirements-completed:
  - DEPL-01
  - DEPL-03
  - DEPL-04
  - DEPL-05

known-stubs:
  - file: "tests/test_infrastructure.py"
    line: 325
    stub: "assert (major, minor) == (3, 11)"
    reason: "Python version assertion is 3.11 but confirmed version is 3.13; plan 01-06 must update to (3, 13) when un-skipping"

duration: 45min
completed: 2026-05-03
---

# Phase 01 Plan 01: Infrastructure Test Scaffold and VM Investigation Summary

**pytest Wave 0 scaffold with 8 skip-marked stubs, plus opt-trade-vm4 investigation confirming Ubuntu 24.04, Python 3.13.5+miniconda3, ibapi via pip, and IBC at /opt/ibcalpha/**

## Performance

- **Duration:** ~45 min (across two agent sessions)
- **Started:** 2026-05-03T06:27:33Z
- **Completed:** 2026-05-03
- **Tasks:** 2 of 2 (Task 1 auto; Task 2 human-action checkpoint — documentation only)
- **Files modified:** 6

## Accomplishments

- Created `pytest.ini` with testpaths and timeout settings
- Created `tests/conftest.py` with `db_connection` and `chrome_options` fixtures
- Created `tests/test_infrastructure.py` with 8 fully-written, skip-decorated test stubs
- `pytest --co` collects all 8 tests; `pytest -q` shows 8 skipped with 0 failures
- Operator investigated opt-trade-vm4 via SSH — captured confirmed versions for all components
- Updated `01-RESEARCH.md`: all LOW/MEDIUM confidence estimates replaced with CONFIRMED values
- Created `01-DECISIONS.md` documenting 7 deviations from original CONTEXT.md decisions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Wave 0 validation infrastructure** - `aa67a65` (chore)
2. **Task 2: Document opt-trade-vm4 findings** - `fce246f` (docs)

Previous checkpoint metadata commit: `24b3415`

## Files Created/Modified

- `/home/chris_s_dodd/bravos/pytest.ini` — pytest config: testpaths=tests, timeout=60
- `/home/chris_s_dodd/bravos/requirements-dev.txt` — pytest==8.3.5, pytest-timeout==2.3.1
- `/home/chris_s_dodd/bravos/tests/conftest.py` — db_connection fixture (psycopg2), chrome_options fixture (Selenium)
- `/home/chris_s_dodd/bravos/tests/test_infrastructure.py` — 8 skip-marked test stubs
- `/home/chris_s_dodd/bravos/.planning/phases/01-infrastructure-setup/01-DECISIONS.md` — 7 deviations from CONTEXT.md
- `/home/chris_s_dodd/bravos/.planning/phases/01-infrastructure-setup/01-RESEARCH.md` — updated with CONFIRMED versions

## opt-trade-vm4 Reference Versions (CONFIRMED)

| Component | Confirmed Value |
|-----------|-----------------|
| OS | Ubuntu 24.04 LTS (Noble) |
| Python | 3.13.5 via miniconda3 |
| Package manager | miniconda3 at ~/miniconda3/ |
| ibapi | 9.81.1.post1 (pip install) |
| ibapi location | ~/miniconda3/lib/python3.13/site-packages |
| IB Gateway | /opt/ibgateway/ (binary: /usr/local/bin/ibgateway) |
| IBC | IbcAlpha at /opt/ibcalpha/current/ |
| IBC startup script | /opt/ibcalpha/start_ib_gateway.sh |
| Xvfb | Display :99 (xvfb@99.service) |
| ibgateway.service | Restart=always, RestartSec=15, User=ubuntu |
| Chrome | google-chrome-stable 139.0.7258.66 |
| PostgreSQL | NOT installed (bravos_vm1-only install) |
| ~/Jts/ | Does NOT exist |

## bravos_vm1 Provisioning Status

bravos_vm1 provisioning is a manual GCP operation that must be performed by the operator outside this agent. The operator has the investigation output and the correct VM creation commands in the prompt context and 01-DECISIONS.md.

**VM creation command (for operator):**
```bash
gcloud iam service-accounts create bravos-vm-sa \
  --display-name="Bravos VM Service Account"

SA_EMAIL="bravos-vm-sa@$(gcloud config get-value project).iam.gserviceaccount.com"

gcloud compute instances create bravos-vm1 \
  --machine-type=e2-standard-2 \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-ssd \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --zone=us-east1-b \
  --service-account="${SA_EMAIL}" \
  --scopes=https://www.googleapis.com/auth/cloud-platform
```

## Decisions Made

- Wrote full test bodies inside skip-decorated functions rather than `pass` — future plans remove the skip, not rewrite the test
- `db_connection` fixture reads password from `BRAVOS_DB_PASSWORD` env var with `change_me_at_deploy` fallback
- `chrome_options` includes all anti-detection flags from the selenium-scraper skill (production-verified)
- **DEV-01:** Mirror opt-trade-vm4: use Python 3.13.5 + miniconda3 (not Python 3.11 + venv)
- **DEV-02:** Mirror opt-trade-vm4: `pip install ibapi==9.81.1.post1` (not official zip download)
- **DEV-03:** Use ubuntu-2404-lts-amd64 image (not ubuntu-2204-lts)
- **DEV-04:** ibgateway.service: `Restart=always, RestartSec=15` (not `Restart=no`)
- **DEV-05/06/07:** IBC at `/opt/ibcalpha/`, Gateway at `/opt/ibgateway/`, Xvfb on display `:99`

## Deviations from Plan

### Confirmed Deviations from CONTEXT.md (via opt-trade-vm4 investigation)

**1. [Rule N/A - Human Investigation] Python 3.11+venv superseded by Python 3.13+miniconda3**
- **Found during:** Task 2 (opt-trade-vm4 investigation)
- **Issue:** CONTEXT.md D-12/D-13 specified Python 3.11 + venv; opt-trade-vm4 uses Python 3.13.5 + miniconda3
- **Fix:** Operator decision — mirror opt-trade-vm4 exactly
- **Files modified:** 01-RESEARCH.md, 01-DECISIONS.md
- **Impact:** Plan 01-06 must install miniconda3 instead of deadsnakes PPA + venv

**2. [Rule N/A - Human Investigation] ibapi installation method superseded**
- **Found during:** Task 2 (opt-trade-vm4 investigation)
- **Issue:** CONTEXT.md D-15 specified official IB zip; opt-trade-vm4 uses pip install ibapi==9.81.1.post1
- **Fix:** Operator decision — use pip with pinned version
- **Files modified:** 01-RESEARCH.md, 01-DECISIONS.md
- **Impact:** Plan 01-06 uses `pip install ibapi==9.81.1.post1` instead of zip extraction

**3. [Rule N/A - Human Investigation] Ubuntu 24.04 (not 22.04)**
- **Found during:** Task 2 (opt-trade-vm4 investigation)
- **Issue:** Research.md recommended Ubuntu 22.04; opt-trade-vm4 is Ubuntu 24.04
- **Fix:** Operator decision — use ubuntu-2404-lts-amd64 image
- **Files modified:** 01-RESEARCH.md, 01-DECISIONS.md
- **Impact:** Plan 01-01 VM creation command updated to 24.04

**4. [Rule N/A - Human Investigation] ibgateway.service Restart=always**
- **Found during:** Task 2 (opt-trade-vm4 investigation)
- **Issue:** Research.md recommended Restart=no; opt-trade-vm4 uses Restart=always, RestartSec=15
- **Fix:** Operator decision — mirror opt-trade-vm4
- **Files modified:** 01-RESEARCH.md, 01-DECISIONS.md
- **Impact:** Plan 01-02 ibgateway.service uses Restart=always

**5. [Rule N/A - Human Investigation] IBC path, Gateway path, Xvfb display**
- **Found during:** Task 2 (opt-trade-vm4 investigation)
- **Issue:** Research.md had /opt/ibc/, ~/Jts/, display :1; opt-trade-vm4 has /opt/ibcalpha/, /opt/ibgateway/, display :99
- **Fix:** Operator decision — mirror opt-trade-vm4 paths exactly
- **Files modified:** 01-RESEARCH.md, 01-DECISIONS.md
- **Impact:** Plans 01-02 and 01-05 use confirmed paths

---

**Total deviations:** 5 confirmed (all from opt-trade-vm4 investigation; operator-approved)
**Impact on plan:** All deviations necessary for correctness — bravos_vm1 must mirror opt-trade-vm4 exactly.

## Known Stubs

| File | Line | Stub | Resolution |
|------|------|------|------------|
| tests/test_infrastructure.py | 325 | `assert (major, minor) == (3, 11)` | Must update to `(3, 13)` in plan 01-06 when un-skipping test_python_version |

## Issues Encountered

None beyond the confirmed deviations from CONTEXT.md documented above.

## Next Phase Readiness

- Wave 0 test scaffold is complete and committed (`aa67a65`)
- opt-trade-vm4 versions confirmed and documented in 01-RESEARCH.md and 01-DECISIONS.md
- All subsequent Phase 1 plans now have authoritative version targets
- bravos_vm1 provisioning is pending operator action (gcloud command in DECISIONS.md DEV-03)
- Plan 01-02 (IB Gateway + IBC) can proceed once bravos_vm1 is provisioned

## Self-Check: PASSED

- FOUND: 01-01-SUMMARY.md
- FOUND: 01-DECISIONS.md (created)
- FOUND: 01-RESEARCH.md (updated)
- FOUND: commit fce246f (opt-trade-vm4 docs + DECISIONS.md)
- FOUND: commit aa67a65 (test scaffold)

---
*Phase: 01-infrastructure-setup*
*Completed: 2026-05-03*
