---
phase: quick-260716-7dx
verified: 2026-07-16T06:10:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
---

# Quick Task 260716-7dx: Session 1b — Fill Replay on Connect (reqExecutions) Verification Report

**Task Goal:** Session 1b from .planning/IMPLEMENTATION_PLAN.md — fill replay on connect via reqExecutions (IMPROVEMENTS.md item 16)
**Verified:** 2026-07-16T06:10:00Z
**Status:** passed
**Re-verification:** No — initial verification

**Note on location:** Code was implemented in isolated worktree `/home/chris_s_dodd/bravos/.claude/worktrees/agent-a99eb37920f3fa3b9` on branch `worktree-agent-a99eb37920f3fa3b9` (commits `60d9c42`, `bf8349f`, `ceb219a`), not yet merged to `main`. All verification below was performed by reading and executing code directly in that worktree.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | On every (re)connect the daemon requests today's executions from IBKR and replays them through the existing fill path | ✓ VERIFIED | `replay_todays_fills()` (connection.py:625) builds a same-day `ExecutionFilter` and calls `self.reqExecutions(REQ_ID_EXECUTIONS, filt)`. Wired into `scripts/run_ingestion.py::_connect_ibkr` (line 132, used by both `main()` line 183 and `handle_resume()` line 470 — covers initial connect + SIGUSR2 resume) and `connection.py::_refresh_after_connect` (line 729 — covers internal auto-reconnect). All three post-connect paths call it. |
| 2 | A replayed execution whose exec_id is already in the executions table is a true no-op — no duplicate position_lots row is created | ✓ VERIFIED | `_handle_exec_details` INSERT now ends `ON CONFLICT (exec_id) DO NOTHING RETURNING id`; `cur.fetchone()` captured as `inserted` inside the cursor block. `if inserted is None: ... return` (connection.py:942-947) precedes the `positions.open_lot`/`partial_close_lot` dispatch. Test `test_replay_duplicate_exec_id_no_double_dispatch` confirms `open_lot`/`partial_close_lot` NOT called when RETURNING yields None — PASSED. |
| 3 | A replayed fill for an ibkr_order_id with no orders row is logged and skipped, not crashed | ✓ VERIFIED | Unmodified pre-existing branch: `if row is None: logger.error(...); return` (connection.py:917-922), executed before the INSERT. Test `test_replay_unknown_order_id_skipped` confirms no raise, no lot dispatch — PASSED. |
| 4 | A fresh unseen fill for a known order inserts one execution row and dispatches exactly one lot | ✓ VERIFIED | Test `test_replay_fresh_fill_dispatches_once`: `fetchone.side_effect=[(42,"BUY"),(7,)]` → `open_lot` called once with `("AAPL", 5, 150.0, mock_conn)` — PASSED. |
| 5 | reqExecutions is issued only AFTER ibapp._db_conn is set, so replayed fills never hit the "_db_conn not set" guard | ✓ VERIFIED | In `run_ingestion.py::_connect_ibkr`, `ibapp._db_conn = _get_db_connection()` (line 122-125) precedes `ibapp.replay_todays_fills()` (line 132). In `connection.py::_refresh_after_connect`, `self._db_conn = self._open_short_lived_db_conn()` (line 719) precedes `self.replay_todays_fills()` (line 729). Plan's own AST-index ordering assertion re-run and passes: "ordering ok". `replay_todays_fills()` itself also independently guards `if self._db_conn is None: ... return` as defense in depth. Test `test_replay_issued_after_db_conn` confirms no-op when `_db_conn` is None — PASSED. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bravos/broker/connection.py` | REQ_ID_EXECUTIONS constant, replay_todays_fills() method, conditional lot dispatch in _handle_exec_details | ✓ VERIFIED | `REQ_ID_EXECUTIONS = 9004` (line 57, no collision with 9001/9002/9003/2000+ reqIds — checked via grep). `replay_todays_fills()` method present (line 625-664), connection- and db_conn-guarded, wrapped in try/except, never raises. `_handle_exec_details` RETURNING-id gate present (line 926-947). |
| `scripts/run_ingestion.py` | replay_todays_fills() call after _db_conn assignment in _connect_ibkr | ✓ VERIFIED | Line 132, inside `_connect_ibkr`, after the `ibapp._db_conn = _get_db_connection()` try/except block, before `reqPnL`. Wrapped in try/except logging `.exception(...)`. |
| `tests/test_broker.py` | replay-safety tests (duplicate no-op, unknown order skip, fresh dispatch, call-order) | ✓ VERIFIED | 4 new tests added (lines 405-end): `test_replay_duplicate_exec_id_no_double_dispatch`, `test_replay_unknown_order_id_skipped`, `test_replay_fresh_fill_dispatches_once`, `test_replay_issued_after_db_conn`. All 4 executed and PASSED (`pytest tests/test_broker.py -k replay -q` → "4 passed, 27 deselected"). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `IBApp.replay_todays_fills` | `EClient.reqExecutions` | `reqExecutions(REQ_ID_EXECUTIONS, ExecutionFilter with same-day time)` | ✓ WIRED | connection.py:658 — `self.reqExecutions(REQ_ID_EXECUTIONS, filt)` where `filt.time = datetime.now().strftime("%Y%m%d-00:00:00")`. Confirmed via grep and unit test capturing the actual call args. |
| `scripts/run_ingestion._connect_ibkr` | `IBApp.replay_todays_fills` | call placed AFTER ibapp._db_conn = _get_db_connection() | ✓ WIRED | Confirmed via grep + AST-index ordering assertion (i_db < i_replay). |
| `connection._handle_exec_details INSERT` | `positions.open_lot / partial_close_lot` | conditional on RETURNING id — dispatch only when the row was actually inserted | ✓ WIRED | connection.py:930 `RETURNING id`; line 934 `inserted = cur.fetchone()`; line 942 `if inserted is None: ... return` gates the dispatch block below it. Verified structurally and via all three replay tests (duplicate/fresh/unknown). |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| REQ_ID_EXECUTIONS==9004, replay_todays_fills exists, RETURNING id in _handle_exec_details | `python -c "from bravos.broker.connection import REQ_ID_EXECUTIONS, IBApp; ..."` (plan's own verify command) | `ok` | ✓ PASS |
| Ordering: _db_conn assignment precedes replay call in run_ingestion.py | `python -c "... assert i_db < i_replay ..."` (plan's own verify command) | `ordering ok` | ✓ PASS |
| Replay-only test subset | `pytest tests/test_broker.py -k replay -q` | `4 passed, 27 deselected` | ✓ PASS |
| Full pure-mock test_broker.py suite (regression check) | `pytest tests/test_broker.py -q` | `24 passed` | ✓ PASS |
| test_positions.py must-stay-green exec_details tests | `pytest tests/test_positions.py -q -k exec_details` | `2 passed, 8 deselected` | ✓ PASS |
| Reconnect regression test (uses _refresh_after_connect, which now calls replay_todays_fills) | `pytest tests/test_broker.py -k reconnect -v` | `6 passed` (incl. `test_reconnect_success_reruns_reconciliation`) | ✓ PASS |
| No production DB touched | Verified `BRAVOS_TEST_DB_OK` unset in shell env throughout; DB-fixture tests correctly refused by `conftest.py` production-DB guard (`pytest.exit`), not run | Guard fired as designed, 0 DB writes | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| IMPROVEMENTS-16 | 260716-7dx-PLAN.md | Fill-capture gap: fills lost when placing client isn't connected; fix = reqExecutions with same-day ExecutionFilter on (re)connect, using existing idempotent ON CONFLICT path | ✓ SATISFIED | IMPROVEMENTS.md item 16's exact prescribed fix ("Add reqExecutions (with a same-day ExecutionFilter) to run_startup_reconciliation / _refresh_after_connect, inserting any missing rows via the existing idempotent ON CONFLICT (exec_id) DO NOTHING path") is implemented as `replay_todays_fills()` wired into all three post-connect paths, with the additional double-count fix (RETURNING id gate) that IMPROVEMENTS.md did not originally call out but the plan correctly identified as a prerequisite for safe replay. |

### Anti-Patterns Found

None. Scanned the full diff (`bravos/broker/connection.py`, `scripts/run_ingestion.py`, `tests/test_broker.py`) for TODO/FIXME/XXX/HACK/placeholder markers, empty implementations, hardcoded stub returns, and console.log-only handlers. No matches.

### Human Verification Required

None required to confirm the phase goal (all truths are verifiable in-repo via code inspection and unit tests). One item is explicitly deferred to production deploy per the plan's own `<operator_followups>` section (not a goal-blocking gap):

- Post-deploy confirmation that `journalctl` shows `"reqExecutions issued (same-day fill replay ...)"` on the next `bravos-trading.service` restart during non-market hours, and that no new duplicate HROW lot appears in `position_lots`. This requires a live IBKR Gateway connection and is correctly scoped as an operator follow-up in both PLAN.md and SUMMARY.md, not as an in-scope automated truth for this quick task.

### Gaps Summary

No gaps. All 5 must-have truths, all 3 required artifacts, and all 3 key links are verified directly against the worktree code (not just SUMMARY.md claims). The implementation matches the plan's `<action>` specifications precisely — constant, method, gating logic, wiring order, and test assertions all correspond line-for-line to what was specified. Tests were independently re-run in this verification session (not just trusted from SUMMARY.md) and confirmed: 4/4 new replay tests pass, 24/24 full test_broker.py pure-mock suite passes, 2/2 must-stay-green test_positions.py exec_details tests pass, 6/6 reconnect-path tests pass (including the regression test that exercises `_refresh_after_connect`, which now also calls `replay_todays_fills`). No production DB was touched during verification (`BRAVOS_TEST_DB_OK` never set; production-DB guard fired as designed and expected).

The 3 commits (`60d9c42`, `bf8349f`, `ceb219a`) exist on branch `worktree-agent-a99eb37920f3fa3b9` and are scoped exactly to the plan's `files_modified` list — no unrelated files were touched by this plan's commits.

---

_Verified: 2026-07-16T06:10:00Z_
_Verifier: Claude (gsd-verifier)_
