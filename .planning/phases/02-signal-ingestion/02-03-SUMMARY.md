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
  - "WordPress login fields: name='username' and name='password' (WooCommerce /my-account/) — not the generic WP 'log'/'pwd' used in wp-login.php"
  - "POST_BODY_SELECTOR confirmed as .entry-content against live Bravos site"
  - "Login uses By.ID with visible-element filtering — duplicate hidden fields on page require filtering to find the visible ones"
  - "Post URLs come from Bravos notification emails under /news-feed/ path — scraper uses fetch_post(url) + process_alert(url) instead of _get_recent_posts()"
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
duration: ~60min
completed: 2026-05-08
---

# Phase 02 Plan 03: BravosScraper Implementation Summary

**BravosScraper with Selenium login, session management, and confirmed live selectors — .entry-content body, WooCommerce username/password fields, email-triggered fetch_post(url) pattern**

## Performance

- **Duration:** ~60 min
- **Started:** 2026-05-08T09:10:42Z
- **Completed:** 2026-05-08 (all tasks complete including live selector confirmation)
- **Tasks:** 2 of 2 complete
- **Files modified:** 3

## Accomplishments

- BravosScraper class fully implemented with startup, _login (3-retry + JS click fallback), _check_session, _get_recent_posts, _get_post_content, _store_signal, run_cycle, shutdown
- Selector constants added to settings.py — TRADE_ALERT_CATEGORY_URL, LOGIN_URL, ARTICLE_SELECTOR, ARTICLE_LINK_SELECTOR, POST_BODY_SELECTOR, LOGIN_USERNAME_FIELD, LOGIN_PASSWORD_FIELD, LOGIN_SUBMIT_XPATH, POSTS_PER_CYCLE, MAX_REAUTH_ATTEMPTS
- All 6 mock-based scraper tests passing (0 skipped)
- catch_cycle_exceptions decorator ensures daemon does not crash on per-cycle failures
- ON CONFLICT (post_url) DO NOTHING ensures idempotent ingestion (AUDIT-06)

## Task Commits

1. **Task 1: Implement BravosScraper class and un-skip scraper tests** - `9aebd3f` (feat)
2. **fix(02-03): WooCommerce login fields username/password** - `0d9c11b` (fix)
3. **Task 2: Selector discovery — confirmed selectors and Gmail-triggered architecture** - `2de0c73` (feat)

## Files Created/Modified

- `/home/chris_s_dodd/bravos/bravos/ingestion/scraper.py` - Full BravosScraper implementation replacing stub (236 lines)
- `/home/chris_s_dodd/bravos/bravos/config/settings.py` - Added 13 scraping/selector constants
- `/home/chris_s_dodd/bravos/tests/test_scraper.py` - Removed all 6 @pytest.mark.skip decorators; all tests active and passing

## Decisions Made

- WordPress login fields are name="username" and name="password" (WooCommerce), not "log"/"pwd" (wp-login.php) — fixed after live selector discovery
- POST_BODY_SELECTOR confirmed as `.entry-content` against live Bravos site — other candidate selectors (.post-content, article .content) not present
- Login uses By.ID with visible-element filtering — duplicate hidden fields on the /my-account/ page require finding the visible input, not just the first match
- Post URLs come from Bravos notification emails under /news-feed/ path — architecture shifted from polling /category/portfolio-update/ to fetch_post(url) + process_alert(url) triggered by email notification
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

None — all selector constants confirmed against live Bravos site.

## Next Phase Readiness

- Scraper is ready to integrate with the polling/notification daemon (02-04)
- fetch_post(url) + process_alert(url) API is stable — downstream plans can depend on this interface
- Architecture note: 02-04 daemon plan should reflect email-triggered pattern rather than category-page polling

---
*Phase: 02-signal-ingestion*
*Completed: 2026-05-08*

## Self-Check: PASSED

- bravos/ingestion/scraper.py: FOUND
- bravos/config/settings.py: FOUND
- tests/test_scraper.py: FOUND
- .planning/phases/02-signal-ingestion/02-03-SUMMARY.md: FOUND
- Commit 9aebd3f: FOUND
