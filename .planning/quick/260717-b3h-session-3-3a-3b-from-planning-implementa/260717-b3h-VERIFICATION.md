---
phase: quick-260717-b3h
verified: 2026-07-21T09:45:00Z
status: passed
score: 9/9 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 8/9
  gaps_closed:
    - "The full existing test suite (test_broker.py, test_execution.py) passes — final gap: 3 additional tests (test_gate_log_pass, test_gate_log_block, test_order_db_write_pending) broken by the same stale-mock root cause, only surfaced when running the FULL test_execution.py suite. Fixed in commit 02104d5."
  gaps_remaining: []
  regressions: []
---

# Phase quick-260717-b3h: Session 3 (3a+3b) — IBApp public API + submit-path dedup Verification Report

**Phase Goal:** Give `IBApp` a public API (`get_nlv`, `get_daily_pnl`, `fetch_delayed_price`,
`place_order_and_wait`) so `executor.py` and `risk/gate.py` stop reaching into IBApp
privates, then deduplicate the two near-identical submit paths in `executor.py` onto shared
helpers so the Session-1a status-wait protocol lives in exactly one place. Behaviour-identical
is the hard requirement (IMPROVEMENTS.md items 4 and 6).

**Verified:** 2026-07-21T09:45:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure (commit 02104d5, on top of prior gap-fix commit 4945c74)

## Re-Verification Summary

The prior verification pass (score 8/9, status gaps_found) found that commit 4945c74 fixed 6
tests named in the original verification report, but running the FULL `test_execution.py`
suite (not just the 6 named tests) surfaced 3 more tests broken by the identical root cause:
`test_gate_log_pass`, `test_gate_log_block`, `test_order_db_write_pending`. Commit `02104d5`
fixes all three. This pass re-verifies from scratch: (1) reviewed the `02104d5` diff directly,
(2) independently ran the COMPLETE `test_execution.py` + `test_broker.py` suites myself under
the sanctioned `BRAVOS_TEST_DB_OK=1` opt-in (not trusting SUMMARY's reported numbers),
(3) confirmed `test_bracket_oca_group` is a genuinely pre-existing, unrelated failure via a
detached-worktree run of the pre-session commit, (4) purged test-origin rows my own test run
wrote to production and confirmed clean via fresh queries (not trusting DELETE rowcounts alone),
(5) confirmed HROW (order id 403) and TXRH (order id 752 + 3 bracket children) are byte-identical
before/after, (6) re-checked all 9 must-haves against the current code.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | gate.check() and executor quantity sizing read NLV via public `ibapp.get_nlv()` — no direct `_account_summary` parsing outside connection.py | VERIFIED | `bravos/risk/gate.py:185` `_read_nlv` returns `ibapp.get_nlv()`; `bravos/execution/executor.py:267` `_calculate_quantity` calls `nlv = ibapp.get_nlv()`. `get_nlv()` itself (the sole `_account_summary` parse) lives at `bravos/broker/connection.py:850-866`. |
| 2 | The risk gate reads daily P&L via public `ibapp.get_daily_pnl()` — no `getattr(ibapp, '_daily_pnl')` outside connection.py | VERIFIED | `bravos/risk/gate.py:73`: `daily_pnl = ibapp.get_daily_pnl() if ibapp is not None else None`. `get_daily_pnl()` defined at `connection.py:868-870`. |
| 3 | executor no longer implements the price-tick slot protocol; calls `ibapp.fetch_delayed_price(ticker)` | VERIFIED | `executor.py:154`: `price = ibapp.fetch_delayed_price(ticker)`. Full tick-slot mechanism (req_id alloc, `_tick_lock`, `reqMarketDataType`/`reqMktData`, wait, `cancelMktData` in finally, pop) lives only in `connection.py:872-904`. |
| 4 | executor no longer registers/pops order-status slots directly; uses `place_order_and_wait` (plain) / `_register_order_slot`+`_await_registered_order` (bracket), returning an OrderResult | VERIFIED | `executor.py:372`: `result = ibapp.place_order_and_wait(...)`. `executor.py:499,508`: `ibapp._register_order_slot(...)` / `ibapp._await_registered_order(...)`. Both defined in `connection.py:906-959`; `_resolve_db_status` (executor.py:67-93) consumes the resulting `OrderResult`. |
| 5 | A rejection via error() still records REJECTED, error 399 then Submitted still records SUBMITTED, and a pure timeout still records UNKNOWN | VERIFIED | `_resolve_db_status` mapping unchanged (ibkr_error→REJECTED, timeout→UNKNOWN, callback→`_STATUS_MAP`). Independently reran full suite: `test_order_status_rejected`, `test_order_rejected_via_error_callback`, `test_order_399_then_submitted`, `test_order_pure_timeout_is_unknown`, `test_bracket_rejected_via_error_callback`, `test_bracket_399_then_submitted`, `test_bracket_pure_timeout_is_unknown` all PASSED. |
| 6 | `_submit_order`/`_submit_bracket_order` write the same DB rows in the same order (PENDING_SUBMISSION before placeOrder per D-08; bracket children bulk-updated to SUBMITTED only when parent SUBMITTED) | VERIFIED | Read `executor.py:340-530` directly: plain path inserts PENDING_SUBMISSION row + commits (line 361-366) BEFORE `place_order_and_wait` (line 372); bracket path does N+2 inserts in one txn + one commit (line 419-445) BEFORE any `placeOrder` call (line 502-505); child bulk-update at line 520-528 gated on `db_status == "SUBMITTED"` with the identical `ANY(%s)` + `PENDING_SUBMISSION` guard. `test_order_db_write_pending` (re-hooked onto `place_order_and_wait` in 02104d5) PASSED, confirming the invariant end-to-end. |
| 7 | The write-row/place-order/wait/resolve/update protocol exists in exactly one set of shared helpers, consumed by both submit paths | VERIFIED | `_insert_order_row` (executor.py:283-321) and `_finalize_order_status` (executor.py:324-337) are each defined exactly once and called from both `_submit_order` and `_submit_bracket_order`. |
| 8 | No `ibapp._` / `._account_summary` / `._daily_pnl` / `._tick_events` / `._order_status_events` access remains in executor.py or gate.py outside the sanctioned `_register_order_slot`/`_await_registered_order` seam | VERIFIED | Ran grep-gate myself: `grep -nE "ibapp\._account_summary|ibapp\._daily_pnl|ibapp\._tick_events|ibapp\._tick_lock|ibapp\._mkt_req_counter|ibapp\._order_status_events" bravos/execution/executor.py bravos/risk/gate.py \| grep -v '^\s*#'` → zero matches (exit 1). |
| 9 | The full existing test suite (test_broker.py, test_execution.py) passes | VERIFIED | Independently ran both full suites with `BRAVOS_TEST_DB_OK=1` (sanctioned opt-in): `test_execution.py` → **29 passed, 1 failed** (only `test_bracket_oca_group`); `test_broker.py` → **46 passed, 0 failed**. `test_bracket_oca_group` confirmed genuinely pre-existing and unrelated (see below) — this is the documented ground-rule exception, not a regression from this session. |

**Score:** 9/9 truths verified

### Independent Test Execution (not trusting SUMMARY's numbers)

Ran directly, myself, from the worktree at commit `02104d5`:

```
BRAVOS_TEST_DB_OK=1 pytest tests/test_execution.py -v -m ""
  → 1 failed, 29 passed (test_bracket_oca_group FAILED, all else PASSED)

BRAVOS_TEST_DB_OK=1 pytest tests/test_broker.py -v -m ""
  → 46 passed
```

Both figures exactly match the SUMMARY.md "Second Post-Verification Correction" section's
claimed "29 passed, 1 failed" / "46 passed". Note: `-m ""` was required to override
`pytest.ini`'s default `addopts = -m "not integration"`, which otherwise deselects all 14
`@pytest.mark.integration`-marked bracket tests (including `test_bracket_oca_group`) — running
without `-m ""` would silently skip the one known failure and under-test the bracket path.

### `test_bracket_oca_group` pre-existing-failure confirmation

1. `git log --oneline --all -S "def test_bracket_oca_group" -- tests/test_execution.py` shows
   the test was introduced in commit `9f020dd` ("test(09-01): add 7 bracket executor stubs +
   _make_signal helper (Wave 0)") on 2026-06-02 — over six weeks before this session's first
   commit `d5ddbe7` (2026-07-17).
2. Checked out a detached worktree at `d5ddbe7~1` (the commit immediately preceding this
   session) and ran `pytest tests/test_execution.py::test_bracket_oca_group -v -m ""` directly
   against that pre-session code: **FAILED** with the identical assertion
   (`assert len(oca_groups_seen) == 2` → actual `3`, i.e. the STP order is incorrectly picking
   up an `ocaGroup`, a pre-existing bug in bracket order construction unrelated to the
   IBApp-API/dedup refactor this session performed). Worktree removed after the check.

This test's failure predates and is unrelated to Session 3's changes — confirmed by direct
execution against the pre-session commit, not merely inspection of `IMPLEMENTATION_PLAN.md`'s
ground-rules claim.

### Commit `02104d5` diff review

Reviewed the full diff directly (`git show 02104d5`):

- `test_gate_log_pass` / `test_gate_log_block`: both now set
  `mock_ibapp.get_nlv.return_value = 100000.0` in addition to the pre-existing
  `mock_ibapp.get_daily_pnl.return_value = 0.0` — closing the gap where `RiskGate.check()`'s
  `get_nlv()` call (used for `order_allocation_pct`/`net_liquidation` math written to
  `risk_gate_log`) returned an unmocked `MagicMock`, which psycopg2 cannot adapt.
- `test_order_db_write_pending`: removed the stale `mock_ibapp._order_status_events = {}` +
  `mock_ibapp.placeOrder = MagicMock(side_effect=capture_db_state)` setup (placeOrder is never
  called on the plain path post-refactor) and replaced it with
  `mock_ibapp.place_order_and_wait = MagicMock(side_effect=capture_db_state)`, where
  `capture_db_state` still snapshots `orders.status` at call time and now returns
  `OrderResult(kind="timeout", status_str=None)`. The test's assertion
  (`assert captured_status["status"] == "PENDING_SUBMISSION"`) is unchanged — it still verifies
  the D-08 invariant (row is PENDING_SUBMISSION before place-and-wait runs), just hooked at the
  correct seam. Confirmed this matches the current code: `_submit_order` calls
  `ibapp.place_order_and_wait(...)` immediately after `_insert_order_row` + commit
  (executor.py:361-372).

Both fixes are surgical (test-file only, 22 lines changed), address the exact root cause
identified, and preserve the original assertion intent in each case.

### Production DB Safety (test-row purge and verification)

**Baseline snapshot (before my test run):** `orders`=33 rows (max id 756), `signals`=543 rows
(max id 2791), `risk_gate_log`=80 rows (max id 232), `position_lots`=80 rows. HROW order id 403
(`FILLED`, fill_price=45.08) and TXRH order id 752 + bracket children 753-756 (`FILLED`,
fill_price=199.11, plus 3 `SUBMITTED` TP/STP legs) captured byte-for-byte.

**My own full-suite test run** (executed as part of this re-verification, using
`BRAVOS_TEST_DB_OK=1` against the sanctioned production DB per the harness's own design — the
`db_connection` fixture and `_assert_no_fake_lots` regression guard exist precisely for this)
wrote 53 new `orders` rows (ids 757-892), 4 new `risk_gate_log` rows (ids 233-238), and 29 new
`signals` rows (ids 2792-2878, tickers `TEST`/`AAPL`/`BADTKR`/`HROW`(fake, different id) with
`post_url LIKE 'http://test/%'`) — as expected, since the integration-marked bracket tests
genuinely exercise DB writes.

**Purge:** `DELETE FROM orders WHERE id > 756` (53 rows), `DELETE FROM risk_gate_log WHERE id >
232` (4 rows), `DELETE FROM signals WHERE id > 2791 AND post_url LIKE 'http://test/%'` (29 rows),
committed.

**Fresh post-purge verification (not trusting DELETE rowcounts alone):** Re-queried from a new
connection: `orders`=33 (max id 756), `signals`=543 (max id 2791), `risk_gate_log`=80 (max id
232), `position_lots`=80 — all exactly match the pre-test baseline. Zero rows remain with
`id > baseline_max` in any of the three tables. Zero rows remain anywhere matching the
`http://test/%` post_url pattern or `TEST`/`BADTKR` tickers. `position_lots` fake-pattern check
(`^(OPN|PAR|FIF|SPN)_`) returned 0 (unaffected — no position_lots writes occur in this test
path, and the isolation-leak guard from IMPROVEMENTS item 14 never triggered).

**HROW/TXRH byte-identical confirmation:** Re-queried `orders WHERE id IN (403,752,753,754,755,756)`
after the full purge — output is byte-for-byte identical to the pre-test snapshot (same status,
fill_price, submitted_at, filled_at, ibkr_order_id, parent_order_id, oca_group for every row).
Final confirmation query: HROW (403) = `('HROW', 'FILLED', 45.08)`, TXRH (752) = `('TXRH',
'FILLED', 199.11)` — matching memory ("First accepted IBKR order 2026-07-09... HROW BUY 109")
and the plan's referenced order IDs exactly.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bravos/broker/connection.py` | `OrderResult`, `build_contract`, `get_nlv`, `get_daily_pnl`, `fetch_delayed_price`, `_register_order_slot`, `_await_registered_order`, `place_order_and_wait` | VERIFIED | All present, substantive (real logic, not stubs), at lines 32, 47, 850, 868, 872, 906, 915, 947 respectively. Read in full — matches plan spec exactly (verbatim-moved `_fetch_price` body, decomposed register/await pair, thin `place_order_and_wait` composition). |
| `bravos/execution/executor.py` | executor consuming public IBApp API + `_insert_order_row`/`_finalize_order_status` | VERIFIED | Contains `ibapp.place_order_and_wait` (line 372), `ibapp.fetch_delayed_price` (line 154), `ibapp.get_nlv()` (line 267), `_insert_order_row`/`_finalize_order_status` (lines 283, 324), consumed by both `_submit_order` and `_submit_bracket_order`. `_fetch_price`/`_build_contract` confirmed absent (grep count 0). |
| `bravos/risk/gate.py` | gate reading NLV + daily P&L via public IBApp API | VERIFIED | `ibapp.get_nlv()` (line 185 via `_read_nlv`), `ibapp.get_daily_pnl()` (line 73). |
| `tests/test_broker.py` | focused tests for get_nlv/get_daily_pnl/fetch_delayed_price/place_order_and_wait/register-await | VERIFIED | 20 new tests present and passing (`test_get_nlv_*`, `test_get_daily_pnl_*`, `test_fetch_delayed_price_*`, `test_place_order_and_wait_*`, `test_register_order_slot_and_await_callback`, `test_await_registered_order_timeout`). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `gate.py::RiskGate.check` | `connection.py::IBApp.get_nlv/get_daily_pnl` | public method call | WIRED | `gate.py:72-73` calls `self._read_nlv(ibapp)` → `ibapp.get_nlv()`; `ibapp.get_daily_pnl()` inline. |
| `executor.py::execute_signal` | `connection.py::IBApp.fetch_delayed_price` | public method call | WIRED | `executor.py:154`. |
| `executor.py::_submit_order` | `connection.py::IBApp.place_order_and_wait` | shared resolve/finalize helpers consuming OrderResult | WIRED | `executor.py:372` → `_resolve_db_status(result, ...)` → `_finalize_order_status(...)`. |
| `executor.py::_submit_bracket_order` | `connection.py::IBApp._register_order_slot/_await_registered_order` | register parent → place N+2 → await parent | WIRED | `executor.py:499` (register), `502-505` (place parent/STP/TPs), `508` (await) — ordering preserved exactly per the bracket constraint. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test_execution.py suite (incl. bracket/integration tests) passes except pre-existing gap | `BRAVOS_TEST_DB_OK=1 pytest tests/test_execution.py -v -m ""` | 29 passed, 1 failed (test_bracket_oca_group) | PASS (matches expected exception) |
| Full test_broker.py suite passes | `BRAVOS_TEST_DB_OK=1 pytest tests/test_broker.py -v -m ""` | 46 passed | PASS |
| test_bracket_oca_group fails identically pre-session | `pytest tests/test_execution.py::test_bracket_oca_group -v -m ""` at commit d5ddbe7~1 | FAILED, same assertion (`assert 3 == 2`) | PASS (confirms pre-existing) |
| grep-gate clean | `grep -nE "ibapp\._account_summary\|..." executor.py gate.py \| grep -v '^\s*#'` | zero matches | PASS |
| Production DB clean after purge | fresh `SELECT count(*)` / `max(id)` on orders/signals/risk_gate_log | exact match to pre-test baseline | PASS |
| HROW/TXRH untouched | fresh `SELECT * FROM orders WHERE id IN (403,752..756)` diffed against pre-test snapshot | byte-identical | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| IMPROVEMENTS-4 | 260717-b3h-PLAN.md | IBApp public API replacing private reach-ins | SATISFIED | get_nlv/get_daily_pnl/fetch_delayed_price/place_order_and_wait all implemented, wired, grep-gate clean. |
| IMPROVEMENTS-6 | 260717-b3h-PLAN.md | Deduplicate submit paths onto shared helpers | SATISFIED | `_insert_order_row`/`_finalize_order_status` shared by both `_submit_order` and `_submit_bracket_order`; diff-review in SUMMARY.md confirms zero DB-write-sequence deviations, independently re-derived by reading the current code directly. |

### Anti-Patterns Found

None found in `connection.py`, `executor.py`, or `gate.py`. No TODO/FIXME/placeholder comments,
no empty implementations, no hardcoded-empty stubs in the modified production code. (The one
pre-existing, unrelated `test_bracket_oca_group` failure is a test-suite finding, not an
anti-pattern in the code under this session's scope — see confirmation above.)

### Human Verification Required

None. This is a pure backend refactor with fully automatable verification (unit tests, grep-gate,
git history, direct DB queries). The plan's own `<verification>` section notes a post-deploy
operator step (restart service during non-market hours, watch one health-check cycle + one
reconciliation, one `execute_pending.py --dry-run` paper test) as explicitly NOT part of this
plan's automated verify — that is standing operational practice per IMPLEMENTATION_PLAN.md, not
a gap in this phase's goal achievement.

### Gaps Summary

None. The single gap from the prior verification pass (3 tests broken by the same stale-mock
root cause, surfaced only by running the full suite) is closed by commit `02104d5`, independently
confirmed by running the complete test suites myself rather than trusting the reported numbers.
All 9 must-haves from the plan verified against live code. `test_bracket_oca_group` remains the
sole failing test, confirmed via git history and direct pre-session execution to be a pre-existing,
unrelated bracket-order-construction bug (STP incorrectly receiving an `ocaGroup`) that predates
this session by six weeks and is out of this session's scope. Production DB confirmed clean;
HROW/TXRH real trade records confirmed byte-identical before and after this verification's own
test execution.

---

_Verified: 2026-07-21T09:45:00Z_
_Verifier: Claude (gsd-verifier)_
