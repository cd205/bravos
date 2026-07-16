---
phase: quick-260716-7dx
plan: 01
subsystem: broker-connection
tags: [ibkr, fill-replay, reconnect, idempotency]
dependency-graph:
  requires: []
  provides:
    - REQ_ID_EXECUTIONS constant (9004)
    - IBApp.replay_todays_fills()
    - RETURNING-id gated lot dispatch in _handle_exec_details
  affects:
    - bravos/broker/connection.py
    - scripts/run_ingestion.py
tech-stack:
  added: []
  patterns:
    - "same-day ExecutionFilter.time scoping to prevent stale-fill replay"
    - "RETURNING id gate to distinguish INSERT-happened vs ON CONFLICT no-op"
key-files:
  created: []
  modified:
    - bravos/broker/connection.py
    - scripts/run_ingestion.py
    - tests/test_broker.py
decisions:
  - "Same-day ExecutionFilter (time=today 00:00:00) chosen over empty filter — scopes replay to TODAY so the manually-backfilled HROW Jul-9 lot (exec_id never recorded) can never be re-dispatched"
  - "Lot dispatch gated on RETURNING id fetch (not a second SELECT) — single round trip, atomic with the INSERT inside the same cursor block"
  - "No shared bootstrap extracted between run_ingestion._connect_ibkr and connection._refresh_after_connect — mirror-and-comment pattern retained per Session 4a scope boundary"
metrics:
  duration: "~25min"
  completed: "2026-07-16"
---

# Phase quick-260716-7dx Plan 01: Fill-Replay on (Re)Connect Summary

Adds `reqExecutions`-based same-day fill replay on every IBKR (re)connect path, gated by a `RETURNING id` check in `_handle_exec_details` so replayed already-recorded fills never double-dispatch a `position_lots` entry.

## What Was Built

**Task 1 — `REQ_ID_EXECUTIONS` + `replay_todays_fills()` + RETURNING-id gate** (commit `60d9c42`):
- `REQ_ID_EXECUTIONS = 9004` added next to `REQ_ID_ACCOUNT_SUMMARY`/`REQ_ID_PNL` in `bravos/broker/connection.py`.
- `IBApp.replay_todays_fills()`: builds an `ExecutionFilter` scoped to today (`time="%Y%m%d-00:00:00"`), calls `self.reqExecutions(REQ_ID_EXECUTIONS, filt)`. Guards: no-op with a warning log when not connected or `_db_conn` is unset; wrapped in try/except so a failure never raises.
- `_handle_exec_details`: the `INSERT INTO executions ... ON CONFLICT (exec_id) DO NOTHING` now appends `RETURNING id`, and `cur.fetchone()` is captured inside the same cursor block. Lot dispatch (`open_lot`/`partial_close_lot`) is now gated on that fetch being non-`None` — a replayed, already-recorded fill is a true no-op and returns early with an INFO log, never reaching the lot-dispatch code. The unknown-order branch (order SELECT returns `None`) is unchanged.

**Task 2 — Wiring into both post-connect sequences** (commit `bf8349f`):
- `scripts/run_ingestion.py::_connect_ibkr`: `ibapp.replay_todays_fills()` called immediately after the `ibapp._db_conn = _get_db_connection()` block, before the `reqPnL` subscription. This single call point covers both the initial connect (`main()`) and the SIGUSR2 resume path (`handle_resume()`), since both reuse `_connect_ibkr`.
- `bravos/broker/connection.py::_refresh_after_connect`: mirrored call (`self.replay_todays_fills()`) after `self._db_conn = self._open_short_lived_db_conn()`, before the `reqPnL` re-subscribe — covers the internal auto-reconnect path.
- Both call sites wrapped in try/except logging `.exception(...)` so a replay failure never aborts the (re)connect.
- No shared bootstrap was extracted — the mirror-and-comment pattern was preserved per the plan's explicit Session 4a scope boundary.

**Task 3 — Replay-safety tests** (commit `ceb219a`):
- `test_replay_duplicate_exec_id_no_double_dispatch`: `fetchone.side_effect = [(42,"BUY"), None]` (order found, RETURNING no-op) → `open_lot`/`partial_close_lot` not called; INSERT with `ON CONFLICT (exec_id) DO NOTHING` still verified present.
- `test_replay_unknown_order_id_skipped`: order SELECT returns `None` → function returns without raising, no lot dispatch.
- `test_replay_fresh_fill_dispatches_once`: `fetchone.side_effect = [(42,"BUY"), (7,)]` (order found, fresh RETURNING id) → `open_lot` called exactly once with `("AAPL", 5, 150.0, mock_conn)`.
- `test_replay_issued_after_db_conn`: fresh `IBApp` with `_connected` forced set and `_db_conn` set → `replay_todays_fills()` calls `reqExecutions` with `REQ_ID_EXECUTIONS` and a filter `.time` starting with today's `%Y%m%d`; then with `_db_conn = None` → no `reqExecutions` call (verified via a captured-args no-op check).

## Verification

- `grep -n "RETURNING id" bravos/broker/connection.py` → present (INSERT clause + comment).
- `grep -n "replay_todays_fills" scripts/run_ingestion.py bravos/broker/connection.py` → present at all three wire-up points (method def, `_connect_ibkr` call, `_refresh_after_connect` call).
- Ordering assertion (`ibapp._db_conn = _get_db_connection()` index < `replay_todays_fills` index in `run_ingestion.py`) → confirmed via inline Python AST-index check.
- `pytest tests/test_broker.py -q -k "replay"` → 4/4 pass.
- `pytest tests/test_broker.py tests/test_positions.py -q` (pure-mock tests, run WITHOUT `BRAVOS_TEST_DB_OK` per safety constraint) → 24 passed inline before the Session 0b production-DB guard correctly `pytest.exit()`'d on the two pre-existing DB-fixture tests (`test_position_snapshot_written`, `test_reconcile_no_db_write_on_mismatch`) — this is expected, documented behavior, not a failure of this plan's work. Re-running with those two deselected: **29 passed, 0 failed** across `test_broker.py` + the exec-details tests in `test_positions.py`.
- `test_exec_details_writes_execution_row` and `test_exec_details_idempotent` in `test_positions.py` (explicitly called out in the plan as must-stay-green) — both pass: their reused `fetchone.return_value = (42, "BUY")` is truthy for both the order-lookup and the `RETURNING id` fetch, so `open_lot` still dispatches exactly once.
- No production DB was touched or written to at any point — all new/verified tests are pure-mock.

## Deviations from Plan

None — plan executed exactly as written. All three tasks matched their `<action>` specs precisely; no Rule 1-4 fixes were required.

## Known Pre-Existing Failures (out of scope, not touched)

- `tests/test_parser.py::test_multi_ticker_forces_low_confidence` — fails (`medium` != `low`), unrelated to `connection.py`/`run_ingestion.py`. Confirmed pre-existing per plan constraints; left untouched.
- `test_bracket_oca_group` — not exercised in this session's runs but listed as a known pre-existing failure per plan constraints; not investigated (out of scope for this plan's files).

## Operator Follow-ups (not code tasks — for the human after deploy)

1. **HROW duplicate-lot reasoning (RESOLVED by design, no action required):** The same-day `ExecutionFilter` (time = today 00:00:00) means the Jul-9 HROW BUY 109 fill is never returned by `reqExecutions` on any day after Jul-9. Even if it were, its `exec_id` is not in the `executions` table, so the `RETURNING id` gate would let it through — but it can't be returned, so manually-created lot 811 is safe. No duplicate 109-share HROW lot will appear. No pre-deploy `exec_id` backfill is needed because the filter is day-scoped. (If the operator ever widens the filter, they must first insert HROW's real `exec_id` into `executions` to keep the gate effective.)
2. **Post-deploy verification:** restart `bravos-trading.service` during non-market hours, watch `journalctl` for `"reqExecutions issued (same-day fill replay ...)"` and one clean reconciliation. Then confirm no NEW duplicate HROW lot appeared in `position_lots`.
3. This retires the pending manual-backfill item for HROW only insofar as SAME-DAY fills now auto-capture; the historical Jul-9 lot 811 remains the manual record (correct, unchanged).
4. **Optional (document, don't automate):** set Master Client ID = 1 in Gateway API settings via VNC so the daemon sees fills from all clientIds, maximizing replay coverage.

## Self-Check: PASSED

- FOUND: bravos/broker/connection.py (REQ_ID_EXECUTIONS, replay_todays_fills, RETURNING id gate all present)
- FOUND: scripts/run_ingestion.py (replay_todays_fills wired after _db_conn assignment)
- FOUND: tests/test_broker.py (4 new replay tests present and passing)
- FOUND commit 60d9c42: feat(260716-7dx): add REQ_ID_EXECUTIONS + replay_todays_fills; gate lot dispatch on RETURNING id
- FOUND commit bf8349f: feat(260716-7dx): wire replay_todays_fills() into both post-connect sequences
- FOUND commit ceb219a: test(260716-7dx): verify replay-safety (duplicate no-op, unknown order skip, fresh dispatch, call order)
