---
phase: 03-ibkr-connection
plan: 02
subsystem: broker
tags: [ibkr, ibapi, heartbeat, reconnect, threading, backoff]

dependency_graph:
  requires:
    - phase: 03-01
      provides: IBApp class skeleton with _trigger_reconnect/heartbeat stubs raising NotImplementedError
  provides:
    - IBApp.start_heartbeat_monitor() — starts daemon thread ibkr-heartbeat
    - IBApp._heartbeat_loop() — sends reqCurrentTime every 60s, triggers reconnect on 10s timeout
    - IBApp._trigger_reconnect() — lock-guarded, spawns ibkr-reconnect daemon thread once
    - IBApp._reconnect_loop() — exponential backoff (5/10/20/40/80s), then 60s forever
    - IBApp.start_background_reconnect() — initial-connect-failed entry point for Plan 4
  affects:
    - Plan 03-3: run_startup_reconciliation stub still raises NotImplementedError (unchanged)
    - Plan 04: run_ingestion.py calls start_heartbeat_monitor() after connect_and_run() succeeds
    - Plan 04: run_ingestion.py calls start_background_reconnect() on initial connect failure

tech-stack:
  added: []
  patterns:
    - Lock-guarded _reconnecting flag prevents duplicate reconnect thread spawns
    - _stop_event.wait(timeout=N) as the sleep mechanism — exits cleanly on shutdown without polling
    - CLOSE-WAIT drain: time.sleep(5) before remaining backoff, ensuring TCP socket drains
    - Exponential backoff capped: _RETRY_DELAYS exhausted transitions to 60s-forever

key-files:
  created: []
  modified:
    - bravos/broker/connection.py
    - tests/test_broker.py

key-decisions:
  - "Run tests with /home/chris_s_dodd/miniconda3/bin/python — /usr/bin/python3 (3.12.3) lacks ibapi; conda base (3.13.5) has ibapi==9.81.1.post1"
  - "Wave 1 (heartbeat) and Wave 2 (reconnect) committed together — _heartbeat_loop calls _trigger_reconnect so they are interdependent"
  - "03-2 tests permanently unskipped (7 tests now pass) — not re-skipped after verification per plan note 'or leave them unskipped'"

patterns-established:
  - "miniconda3 python for all broker tests: /home/chris_s_dodd/miniconda3/bin/python -m pytest"

requirements-completed:
  - IBKR-01
  - IBKR-02

duration: 5min
completed: "2026-05-10"
---

# Phase 3 Plan 2: Heartbeat Thread + Reconnect Logic Summary

**Heartbeat monitor (60s interval, 10s timeout) and exponential-backoff reconnect state machine (5s/10s/20s/40s/80s then 60s forever) with CLOSE-WAIT drain and duplicate-thread guard — all 7 03-2 tests pass.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-05-10T08:15:17Z
- **Completed:** 2026-05-10T08:20:00Z
- **Tasks:** 2 (Wave 1 + Wave 2 implemented together)
- **Files modified:** 2

## Accomplishments

- Replaced all 4 `NotImplementedError` stubs from Plan 03-1 with working implementations
- Heartbeat loop exits cleanly via `_stop_event.wait(timeout=HEARTBEAT_INTERVAL)` — no busy-wait
- Reconnect guard prevents duplicate threads: lock acquired, `_reconnecting` checked before spawning
- CLOSE-WAIT drain (5s sleep before remaining backoff) implemented per D-06
- `start_background_reconnect()` added for Plan 4's initial-connect-failed startup mode

## Task Commits

1. **Wave 1 + Wave 2: heartbeat monitor + reconnect state machine** — `b68282e` (feat)

**Plan metadata:** (final commit — see below)

## Files Created/Modified

- `bravos/broker/connection.py` — Added `start_heartbeat_monitor`, `_heartbeat_loop`, `_trigger_reconnect`, `_reconnect_loop`, `start_background_reconnect`; removed all 4 NotImplementedError stubs from Plan 03-2
- `tests/test_broker.py` — Removed `@pytest.mark.skip(reason="plan: 03-2")` decorators from 7 tests; all 7 now pass

## Decisions Made

1. **miniconda3 python required for broker tests** — `/usr/bin/python3` (3.12.3 system Python) lacks `ibapi`. Must use `/home/chris_s_dodd/miniconda3/bin/python -m pytest` (Python 3.13.5 with ibapi==9.81.1.post1). This matches the DEV-01 decision from Phase 01.
2. **Wave 1 and Wave 2 committed together** — `_heartbeat_loop` calls `_trigger_reconnect`, making them functionally interdependent. Splitting into two commits would leave an intermediate broken state.
3. **03-2 tests left permanently unskipped** — Plan noted "or leave them unskipped — they should pass". All 7 pass cleanly.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- `/usr/bin/python3` (system Python 3.12.3) lacks `ibapi` — tests fail with `ModuleNotFoundError: No module named 'ibapi'`. Resolved by using `/home/chris_s_dodd/miniconda3/bin/python`. This is consistent with project DEV-01 decision. Not a deviation — expected environment constraint.

## User Setup Required

None — no external service configuration required.

## Known Stubs

One remaining stub from Plan 03-1 (unchanged, out of scope for this plan):

| Method | File | Implementing Plan |
|--------|------|-------------------|
| `IBApp.run_startup_reconciliation()` | bravos/broker/connection.py | 03-3 |

## Next Phase Readiness

- Plan 03-3 can proceed: `run_startup_reconciliation()` stub is the only remaining NotImplementedError in IBApp
- IBApp now has full reconnect capability — Plan 04 (run_ingestion.py) can call `start_heartbeat_monitor()` after `connect_and_run()` and `start_background_reconnect()` on failure
- Test suite: 7 passing (03-2), 16 skipped (8 from 03-1 wave, 8 from 03-3 wave)

---
*Phase: 03-ibkr-connection*
*Completed: 2026-05-10*
