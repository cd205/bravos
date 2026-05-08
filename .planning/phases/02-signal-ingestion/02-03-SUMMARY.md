---
phase: 02-signal-ingestion
plan: 03
subsystem: scraping
tags: [selenium, webdriver, chrome, wordpress, bravos, scraper, login, session]

# Dependency graph
requires:
  - phase: 02-signal-ingestion
    provides: "parse_signal() from 02-02-PLAN.md — scraper calls this on each post"
  - phase: 02-signal-ingestion
    provides: "get_secret() from secrets_config.py — scraper uses this for credentials"
provides:
  - BravosScraper class with login, session management, category scraping, post extraction, DB storage
  - setup_chrome_driver() helper with anti-detection flags
  - catch_cycle_exceptions decorator for daemon resilience
  - Selector constants in settings.py (TRADE_ALERT_CATEGORY_URL, LOGIN_URL, ARTICLE_SELECTOR, etc.)
affects: [03-execution, 04-daemon, scraper-integration-tests]

# Tech tracking
tech-stack:
  added: [selenium 4.x, webdriver-manager 4.x, google-cloud-secret-manager 2.28.0]
  patterns:
    - "BravosScraper class with startup/run_cycle/shutdown lifecycle"
    - "catch_cycle_exceptions decorator prevents daemon crashes on scrape failure"
    - "ON CONFLICT (post_url) DO NOTHING for idempotent signal ingestion"
    - "3-attempt login retry with JS click fallback"

key-files:
  created: []
  modified:
    - bravos/ingestion/scraper.py
    - bravos/config/settings.py
    - tests/test_scraper.py

key-decisions:
  - "WordPress login fields: name='log' (username) and name='pwd' (password) — standard WP /my-account/ form"
  - "Selector defaults are WordPress standard patterns (article, h2 a, .entry-content) — must be confirmed against live Bravos site in Task 2"
  - "google-cloud-secret-manager installed to satisfy secrets_config.py import (was missing from environment)"

patterns-established:
  - "All scraper tests use mocked WebDriver — no live browser or GCP credentials required for CI"
  - "setup_chrome_driver returns None on failure (not raises) — callers check for None"

requirements-completed:
  - INGST-01
  - INGST-02
  - INGST-07
  - INGST-03
  - AUDIT-06

# Metrics
duration: 32min
completed: 2026-05-08
---

# Phase 02 Plan 03: BravosScraper Implementation Summary

**BravosScraper class with login automation, session expiry detection, category scraping, and DB-idempotent signal storage — awaiting live selector confirmation (Task 2 checkpoint)**

## Performance

- **Duration:** ~32 min
- **Started:** 2026-05-08T09:10:42Z
- **Completed:** 2026-05-08T09:42:34Z (Task 1); Task 2 pending human checkpoint
- **Tasks:** 1 of 2 complete (Task 2 is checkpoint:human-verify)
- **Files modified:** 3

## Accomplishments

- BravosScraper class fully implemented with startup, _login (3-retry + JS click fallback), _check_session, _get_recent_posts, _get_post_content, _store_signal, run_cycle, shutdown
- Selector constants added to settings.py — TRADE_ALERT_CATEGORY_URL, LOGIN_URL, ARTICLE_SELECTOR, ARTICLE_LINK_SELECTOR, POST_BODY_SELECTOR, LOGIN_USERNAME_FIELD, LOGIN_PASSWORD_FIELD, LOGIN_SUBMIT_XPATH, POSTS_PER_CYCLE, MAX_REAUTH_ATTEMPTS
- All 6 mock-based scraper tests passing (0 skipped)
- catch_cycle_exceptions decorator ensures daemon does not crash on per-cycle failures
- ON CONFLICT (post_url) DO NOTHING ensures idempotent ingestion (AUDIT-06)

## Task Commits

1. **Task 1: Implement BravosScraper class and un-skip scraper tests** - `9aebd3f` (feat)
2. **Task 2: Selector discovery — verify live site selectors** - PENDING (checkpoint:human-verify)

## Files Created/Modified

- `/home/chris_s_dodd/bravos/bravos/ingestion/scraper.py` - Full BravosScraper implementation replacing stub (236 lines)
- `/home/chris_s_dodd/bravos/bravos/config/settings.py` - Added 13 scraping/selector constants
- `/home/chris_s_dodd/bravos/tests/test_scraper.py` - Removed all 6 @pytest.mark.skip decorators; all tests active and passing

## Decisions Made

- WordPress login fields use name="log" and name="pwd" — standard WooCommerce /my-account/ form defaults; confirmed in LOGIN_USERNAME_FIELD and LOGIN_PASSWORD_FIELD constants
- Selector defaults are WordPress standard patterns (article, h2 a, .entry-content) — appropriate starting point before live discovery
- google-cloud-secret-manager package installed — was missing from environment, required for secrets_config.py module import even when get_secret is mocked in tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed google-cloud-secret-manager package**
- **Found during:** Task 1 (first test run)
- **Issue:** `from google.cloud import secretmanager` fails with ImportError — package not installed in miniconda environment
- **Fix:** `pip install google-cloud-secret-manager` (version 2.28.0)
- **Files modified:** none (environment-level fix)
- **Verification:** All 6 scraper tests pass after install
- **Committed in:** 9aebd3f (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential for test execution — secrets_config.py imports google.cloud at module level regardless of mocking. No scope creep.

## Issues Encountered

None beyond the Rule 3 dependency fix above.

## Known Stubs

None — all selector constants are plausible WordPress defaults. However, they are unconfirmed against the live Bravos site, which is the explicit purpose of Task 2 (checkpoint:human-verify). Until Task 2 confirms selectors, the constants in settings.py may need updating.

## Next Phase Readiness

- After Task 2 selector confirmation: scraper is ready to integrate with the polling daemon (02-04)
- Blocker: live site selectors must be confirmed before daemon integration
- BravosScraper API is complete and stable — downstream plans can depend on the class interface

---
*Phase: 02-signal-ingestion*
*Completed: 2026-05-08 (partial — checkpoint pending)*

## Self-Check: PASSED

- bravos/ingestion/scraper.py: FOUND
- bravos/config/settings.py: FOUND
- tests/test_scraper.py: FOUND
- .planning/phases/02-signal-ingestion/02-03-SUMMARY.md: FOUND
- Commit 9aebd3f: FOUND
