---
phase: 02-signal-ingestion
plan: 04
subsystem: testing
tags: [postgres, psycopg2, cloud-sql, integration-tests, deduplication, audit-trail]

# Dependency graph
requires:
  - phase: 02-01
    provides: signals table schema with parse_method + scraped_at columns (v2 migration)
provides:
  - DB-level integration tests verifying dedup (ON CONFLICT DO NOTHING), raw_html storage, and audit field population
  - Validation that Cloud SQL Auth Proxy connection works from pytest
affects: [02-05, phase-3, phase-4]

# Tech tracking
tech-stack:
  added: []
  patterns: [pytest integration tests connect via Cloud SQL Auth Proxy on 127.0.0.1:5432; each test cleans up its own fixture rows via DELETE WHERE]

key-files:
  created: []
  modified:
    - tests/test_ingestion.py

key-decisions:
  - "Un-skipping tests is the canonical way to activate Wave 0 stubs — no rewrite needed, the test body was complete"
  - "v2 migration (infra/migrate_signals_v2.sql) adds parse_method + scraped_at; was applied to live Cloud SQL before running tests"

patterns-established:
  - "Wave 0 skip-stub pattern: full test body written inside @pytest.mark.skip at stub time; later plan removes decorator only"

requirements-completed: [INGST-06, AUDIT-03, AUDIT-06]

# Metrics
duration: ~15min
completed: 2026-05-09
---

# Phase 02 Plan 04: DB Integration Tests Summary

**Three psycopg2 integration tests passing against live Cloud SQL — dedup (ON CONFLICT DO NOTHING), raw_html storage, and parse_method/scraped_at audit fields verified at the database layer**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-09
- **Completed:** 2026-05-09
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Removed 3 `@pytest.mark.skip` decorators from `tests/test_ingestion.py` — all test bodies were already complete (Wave 0 stub pattern)
- Applied v2 migration (`infra/migrate_signals_v2.sql`) to live Cloud SQL to add `parse_method` and `scraped_at` columns
- All 3 DB integration tests pass against live Cloud SQL via Auth Proxy:
  - `test_dedup_on_conflict` (INGST-03): duplicate post_url INSERT silently ignored, count stays at 1
  - `test_raw_html_stored` (INGST-06): raw_html column stores and retrieves full HTML unchanged
  - `test_audit_fields_populated` (AUDIT-01): parse_method and scraped_at are non-null after INSERT

## Task Commits

Each task was committed atomically:

1. **Task 1: Un-skip DB ingestion tests and verify they pass** - `75d4ecd` (feat)

**Plan metadata:** (docs commit — this summary)

## Files Created/Modified

- `tests/test_ingestion.py` - Removed 3 `@pytest.mark.skip` decorators; test bodies unchanged from Wave 0 stubs

## Decisions Made

- Un-skipping tests is the canonical activation mechanism for Wave 0 stubs — no rewrite needed because full INSERT/SELECT/DELETE logic was written at stub time
- v2 migration applied to live Cloud SQL before un-skipping tests; migration adds `parse_method VARCHAR(20)` and `scraped_at TIMESTAMPTZ` columns to the signals table

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Schema migration validated: dedup constraint, raw_html column, and audit fields all behave correctly at the DB layer
- Cloud SQL Auth Proxy connection confirmed working from pytest (127.0.0.1:5432)
- Ready for 02-05: Daemon entry point + end-to-end integration validation

---
*Phase: 02-signal-ingestion*
*Completed: 2026-05-09*
