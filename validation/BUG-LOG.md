# Phase 6 Bug Log

Bugs discovered during paper-trading validation. Per D-04/D-05: any bug that
prevents an order being placed or causes an incorrect order (wrong ticker,
wrong action type, wrong quantity) is BLOCKING and must be fixed in-place
before Phase 6 closes. Parser edge cases / log noise are non-blocking unless
they corrupt the order path.

| ID | Date | Severity (blocking/non-blocking) | Symptom | Root Cause | Fix Reference (commit/file) | Status |
|----|------|----------------------------------|---------|------------|-----------------------------|--------|
| BUG-01 | 2026-05-17 | blocking | ticker=None for CPER profit-booking post — `assert_signal_processed` FAIL: expected CPER/partial_close, got None/partial_close | `scraper.fetch_post` extracted title via `article.text.split("\n")[0]`; long titles wrap in the headless viewport and the `$TICKER` symbol lands on line 2, invisible to the regex | `bravos/ingestion/scraper.py` — prefer `og:title` meta content (full untruncated string) over article-text first line; `tests/test_scraper.py` — updated mock to exercise fallback path | Fixed |
