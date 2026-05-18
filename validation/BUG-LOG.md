# Phase 6 Bug Log

Bugs discovered during paper-trading validation. Per D-04/D-05: any bug that
prevents an order being placed or causes an incorrect order (wrong ticker,
wrong action type, wrong quantity) is BLOCKING and must be fixed in-place
before Phase 6 closes. Parser edge cases / log noise are non-blocking unless
they corrupt the order path.

| ID | Date | Severity (blocking/non-blocking) | Symptom | Root Cause | Fix Reference (commit/file) | Status |
|----|------|----------------------------------|---------|------------|-----------------------------|--------|
| BUG-01 | 2026-05-17 | blocking | ticker=None for CPER profit-booking post — `assert_signal_processed` FAIL: expected CPER/partial_close, got None/partial_close | Two-part root cause: (1) `scraper.fetch_post` used `article.text.split("\n")[0]` which truncates at viewport line-wrap, dropping `$TICKER` to line 2; (2) `og:title` meta (the fix for part 1) omits the `$` prefix, using `(CPER)` instead of `($CPER)`, so `TICKER_RE` still missed it. Final fix: parser now tries `TICKER_PAREN_RE` `\(([A-Z]{1,5})\)` as a fallback when `$TICKER` regex finds nothing. Scraper also updated to prefer `og:title`. | `bravos/ingestion/parser.py` — added `TICKER_PAREN_RE` fallback; `bravos/ingestion/scraper.py` — prefer `og:title` meta; `tests/test_scraper.py` — updated mock | Fixed |
