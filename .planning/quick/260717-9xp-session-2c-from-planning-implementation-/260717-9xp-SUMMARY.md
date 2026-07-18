---
phase: quick-260717-9xp
plan: 01
subsystem: gmail-alert-dispatch
tags: [gmail, retry, dispatch, honesty, dedup]
dependency-graph:
  requires: ["quick-260716-82s (bravos/db.py — get_connection call sites)", "quick-260716-9ro (bravos/runtime.py — process_alert's pause path reads runtime.ibkr_paused)"]
  provides: ["process_alert(url) -> str explicit result contract", "scripts/run_ingestion.py::_handle_alert_url testable seam", "scripts/run_gmail.py bounded per-Message-ID retry"]
  affects: ["alert socket handler honesty (OK/ERR)", "Gmail poller retry/mark-seen behavior"]
tech-stack:
  added: []
  patterns: ["explicit string-return contract instead of decorator-swallowed None", "in-memory per-key failure counter with DB mark-seen as the durable terminal state"]
key-files:
  created:
    - tests/test_gmail.py
  modified:
    - bravos/ingestion/scraper.py
    - scripts/run_ingestion.py
    - scripts/run_gmail.py
    - tests/test_scraper.py
    - tests/test_ingestion.py
decisions:
  - "process_alert's whole body wrapped in one try/except Exception -> return 'failed'; this replaces the @catch_cycle_exceptions swallow-and-return-None behavior that decorator provided, but the caller now learns of failure"
  - "@catch_cycle_exceptions removed from process_alert only; definition and run_cycle usage (Chrome-crash self-heal, quick-260708-7lk) untouched"
  - "is_new is False (dedup path) returns 'duplicate' before the confidence/execute block runs at all — cleaner than the old is_new-guard-in-every-condition style"
  - "_dispatch_url_to_daemon keeps its -> bool contract; a new module-level _last_socket_response global stashes the raw ERR reason for the 10-strike alert body (least-invasive option named in the plan, chosen over an out-param or wrapper)"
  - "_dispatch_failures / _last_dispatch_error are in-memory-only by design — a process restart resets them, which only extends retries (never drops a message), because DB _is_message_id_seen is the real dedup and mark-seen is the terminal state"
metrics:
  duration: "~40min"
  completed: "2026-07-17"
---

# Quick Task 260717-9xp: Session 2c — bounded Gmail retry + honest process_alert contract Summary

Made the Gmail alert-dispatch path honest and bounded (IMPROVEMENTS.md item 17). `process_alert` now returns an explicit `"stored" | "duplicate" | "failed"` result instead of always returning `None` via the swallow-all `@catch_cycle_exceptions` decorator; the alert socket handler maps that result to a truthful `OK`/`ERR` reply; and the Gmail poller caps retries at 10 consecutive failures per Message-ID (mark-seen + one operator email), while a successful dispatch that updates 0 rows in `signals` now falls back to mark-seen so it can never loop forever (the mechanism behind the July 7 HROW incident).

## Tasks Completed

1. **process_alert returns explicit result; socket handler maps to OK/ERR** — commit `8ea73df`
   - Removed `@catch_cycle_exceptions` from `process_alert` only (definition + `run_cycle` usage untouched).
   - Rewrote `process_alert(self, url) -> str`: whole body in one `try/except Exception` → `logger.exception` + `return "failed"`.
   - Re-login failure → `return "failed"` (was a bare `return`).
   - `is_new is False` → `return "duplicate"` immediately (dedup path, no execute_signal touch).
   - `is_new is True` and `signal_id is None` (unexpected `_store_signal` edge case) → `return "failed"` so the poller retries.
   - New insert path runs `record_parse_outcome` + the existing high-confidence pause/queue/execute_signal block unchanged, then `return "stored"`. `execute_signal`'s own try/except still swallows its own errors — a stored signal whose order later fails is still `"stored"`.
   - Added `scripts/run_ingestion.py::_handle_alert_url(url) -> str` seam: `"ERR scraper not ready"` if `_scraper is None`; catches any (unexpected) raise from `process_alert` and returns `f"ERR {exc}"`; maps `"stored"`/`"duplicate"` → `"OK"`, everything else (including defensive `None`) → `f"ERR process_alert={result}"`.
   - `_run_alert_socket_server`'s inline try/except (always replied `OK` on success or `ERR <exc>` on raise — never reflected a real failure result) replaced with `reply = _handle_alert_url(url); conn.sendall(...)`.
   - Tests: 4 new in `tests/test_scraper.py` (duplicate, low-confidence stored/no-execute, fetch-raise failed, reauth-fail failed-and-fetch-never-called); 6 new in `tests/test_ingestion.py` (`_handle_alert_url` OK/ERR mapping for stored, duplicate, failed, None, raise, scraper-not-ready).

2. **Bounded poller retry — 10-strike alert, mark-seen, 0-rows fallback** — commit `fe69c26`
   - Added `DISPATCH_FAILURE_ALERT_THRESHOLD = 10`, `_dispatch_failures: dict[str, int]`, `_last_dispatch_error: dict[str, str]` (module-level, in-memory, documented as reset-safe on restart).
   - `_set_gmail_message_id` now returns `cur.rowcount` (captured before commit) instead of `None`.
   - `_dispatch_url_to_daemon` keeps its `bool` return; stashes the raw daemon/local error string into a new module-level `_last_socket_response` on every failure path (non-OK response, `FileNotFoundError`, or generic exception).
   - `process_emails` per-url block rewritten: on success, 0-row fallback to `_mark_message_id_seen` + counter/error dict cleared; on failure, counter incremented, error stashed, and at the 10th consecutive failure `_mark_message_id_seen` + `send_alert(subject with "10x", body with message-id/url/last-error)` fire once, then the counter is popped so a later arrival starts fresh.
   - `_is_message_id_seen`, `_mark_message_id_seen`'s body, `_dispatch_url_to_daemon`'s success semantics (`True` only on exact `"OK"`), the IMAP code, and `main()`'s loop are all untouched (confirmed via diff hunk review — only 6 hunks touched, all within the documented scope).
   - New `tests/test_gmail.py` (pure mock — `_fetch_matching_seq_nums`/`_get_message_id_header`/`_is_message_id_seen`/`_fetch_full_message`/`_extract_urls`/`_dispatch_url_to_daemon`/`_set_gmail_message_id`/`_mark_message_id_seen`/`send_alert` all monkeypatched, `process_emails` driven directly): retry preserved through 9 failures, 10th-strike mark-seen+alert-once+counter-clear, success-after-failures clears counter and restarts count, 0-row fallback, >0-row no-fallback, bare-success clears residual counter/error, `_set_gmail_message_id` rowcount unit test.

3. **Test verification (Task 3, restart skipped)** — no commit (verification only)
   - `grep -n "catch_cycle_exceptions" bravos/ingestion/scraper.py scripts/run_ingestion.py` → definition + `run_cycle` usage shown, NOT `process_alert`.
   - Full suite (`pytest -q`): 24 passed, 17 deselected, then the Session 0b production-DB guard exits at the first `db_connection`-fixture test it reaches in file-alphabetical order (`test_broker.py`) — same guard-trip point as pre-change baseline, not a regression.
   - Individually: `tests/test_scraper.py` (14 passed), `tests/test_gmail.py` (8 passed), `tests/test_notifications.py` (6 passed), `tests/test_db.py` (3 passed) all green.
   - `tests/test_parser.py::test_multi_ticker_forces_low_confidence` — confirmed still failing (documented pre-existing failure, parser.py untouched by this task).
   - `tests/test_ingestion.py`, `tests/test_execution.py` (contains `test_bracket_oca_group`) — both refused by the production-DB guard at their `db_connection`-fixture tests, matching documented baseline behavior.
   - Service restart (`sudo systemctl restart bravos-trading.service bravos-gmail.service`) skipped per dispatch constraints — orchestrator deploys.
   - No `BRAVOS_TEST_DB_OK`, no production DB writes at any point.

## Deviations from Plan

None — plan executed as written, adjusted only for the Session 2a/2b reality already reflected in the current codebase (bravos/db.py, bravos/runtime.py) as called out in the dispatch constraints; no additional deviations found during implementation.

## Self-Check: PASSED

- FOUND: bravos/ingestion/scraper.py (`def process_alert` returns str, decorator removed)
- FOUND: scripts/run_ingestion.py (`_handle_alert_url` seam, socket handler delegates to it)
- FOUND: scripts/run_gmail.py (`_dispatch_failures`, `DISPATCH_FAILURE_ALERT_THRESHOLD`, rowcount fallback)
- FOUND: tests/test_gmail.py (8 tests, pure mock)
- FOUND: commits 8ea73df, fe69c26 on branch worktree-agent-a73a954ac7f02064c
