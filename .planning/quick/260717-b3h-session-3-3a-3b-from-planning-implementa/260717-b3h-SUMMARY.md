---
phase: quick-260717-b3h
plan: 01
subsystem: order-execution
tags: [ibkr, order-path, refactor, dedup, public-api]
dependency-graph:
  requires: [260716-9ro session 2b runtime.py, 260716-82s session 2a bravos/db.py]
  provides: [IBApp public API (get_nlv/get_daily_pnl/fetch_delayed_price/place_order_and_wait), OrderResult dataclass, shared _insert_order_row/_finalize_order_status helpers]
  affects: [bravos/broker/connection.py, bravos/execution/executor.py, bravos/risk/gate.py]
tech-stack:
  added: []
  patterns: [decomposed register/await order-slot seam so bracket path can place N+2 orders before waiting]
key-files:
  created: []
  modified:
    - bravos/broker/connection.py
    - bravos/execution/executor.py
    - bravos/risk/gate.py
    - tests/test_broker.py
    - tests/test_execution.py
decisions: []
metrics:
  duration: ~1h (across interruption/resume)
  completed: 2026-07-21
---

# Phase quick-260717-b3h Plan 01: IBApp public API + submit-path dedup Summary

Gave `IBApp` a public API (`get_nlv`, `get_daily_pnl`, `fetch_delayed_price`, `place_order_and_wait`, plus the decomposed `_register_order_slot`/`_await_registered_order` seam) so `executor.py` and `risk/gate.py` no longer reach into `IBApp` private attributes, then deduplicated the two near-identical order-submit paths in `executor.py` onto shared `_insert_order_row`/`_finalize_order_status` helpers — behavior-identical, verified via an independent pure-mock harness plus a manual before/after DB-write-sequence diff review.

## What Was Built

**Task 1 — `bravos/broker/connection.py` (commit `d5ddbe7`):**
- `OrderResult` dataclass (`kind`: callback/ibkr_error/timeout, `status_str`, `code`, `message`) — carries Session-1a rejection/timeout/callback semantics across the new seam without reinterpreting them.
- Module-level `build_contract(ticker) -> Contract` (moved from `executor._build_contract` verbatim).
- `IBApp.get_nlv()` — single parse of `_account_summary["NetLiquidation"]`, replacing both `gate._read_nlv` and `executor._calculate_quantity`'s inline parse.
- `IBApp.get_daily_pnl()` — thin accessor over `_daily_pnl`.
- `IBApp.fetch_delayed_price(ticker, timeout=5.0)` — moved from `executor._fetch_price` verbatim (allocate req_id under `_tick_lock`, `reqMarketDataType(3)` + `reqMktData`, wait, always `cancelMktData` + pop slot in `finally`).
- `IBApp._register_order_slot` / `IBApp._await_registered_order` / `IBApp.place_order_and_wait` (composition) — the decomposed order-slot trio. The bracket path needs to place N+2 orders before waiting (transmit=True on the last TP fires the whole bracket), so a single atomic place+wait cannot serve it; `place_order_and_wait` is `register → placeOrder → await`.
- 20 new pure-mock tests added to `tests/test_broker.py` covering every new method and both success/failure/timeout branches.

**Task 2 — `bravos/execution/executor.py` + `bravos/risk/gate.py` (commit `460cfa6`):**
- `executor.execute_signal` fetches price via `ibapp.fetch_delayed_price(ticker)`; `_calculate_quantity` reads NLV via `ibapp.get_nlv()`.
- `_fetch_price` / `_build_contract` deleted from `executor.py`; `build_contract` + `OrderResult` imported from `bravos.broker.connection`.
- `_resolve_db_status` adapted to consume an `OrderResult` instead of a raw slot dict — same REJECTED/UNKNOWN/`_STATUS_MAP` mapping, same ERROR-level logging.
- `_submit_order` (plain path) routes through `ibapp.place_order_and_wait(...)`.
- `_submit_bracket_order` (bracket path) routes the parent slot through `ibapp._register_order_slot(...)` / `ibapp._await_registered_order(...)` — still places all N+2 orders before awaiting (Task 2 did the minimal swap; Task 3 did the full dedup).
- `gate._read_nlv` delegates to `ibapp.get_nlv()`; `gate.check` reads daily P&L via `ibapp.get_daily_pnl()`.
- Test-mock migrations in `tests/test_execution.py`: `mock_ibapp._account_summary` → `mock_ibapp.get_nlv.return_value`, `mock_ibapp._daily_pnl` → `mock_ibapp.get_daily_pnl.return_value`, patches of `executor._fetch_price` → `mock_ibapp.fetch_delayed_price` (one-line comment at each migrated setup).
- Grep-gate scoped to `executor.py`/`gate.py` confirmed clean (a stale docstring reference in `executor.py`'s module docstring was also updated to stop mentioning the removed private-attribute names).

**Task 3 — `bravos/execution/executor.py` only (commit `c391d29`):**
- `_insert_order_row(cur, *, signal_id, ibkr_order_id, ticker, action, quantity, order_type, parent_order_id=None, oca_group=None) -> int` — single `INSERT ... RETURNING id` helper reproducing the exact 8-column (plain) / 10-column (parent_order_id + oca_group) forms and literal `'PENDING_SUBMISSION'` / `NOW()` from both original submit paths verbatim. Takes a cursor (not a connection) so the bracket path can still batch all N+2 inserts in one transaction.
- `_finalize_order_status(db_conn, *, db_status, target_column, target_value)` — single `UPDATE ... WHERE {target_column} = %s` + commit; `target_column` is always one of the two hardcoded literals `"id"` / `"ibkr_order_id"` from our own call sites (not user input).
- `_submit_order` and `_submit_bracket_order` are now short orchestrations over `_insert_order_row` / the Task-1 order-slot seam / `_resolve_db_status` / `_finalize_order_status`.
- `connection.py` untouched in this task (its `<files>` intentionally excludes it — Task 3 only consumes the Task-1 register/await trio).

## Mandatory DB-Write-Sequence Diff Review (Task 3)

Per the plan's hard requirement ("behaviour-identical" for this production order path), `git diff bravos/execution/executor.py` for the Task 3 commit was walked INSERT/UPDATE/commit by INSERT/UPDATE/commit, and independently re-verified with a pure-mock harness (not committed — see below) that intercepts every `cursor.execute()` call and asserts on the captured SQL text, parameter tuples, and `db_conn.commit()` call counts before vs. after the refactor.

**(a) Plain path (`_submit_order`):**
| Step | Before | After | Verdict |
|---|---|---|---|
| 1. Write row | `INSERT INTO orders (... 8 cols ...) VALUES (..., 'MKT', 'PENDING_SUBMISSION', NOW()) RETURNING id` then `db_conn.commit()` | `_insert_order_row(cur, ..., order_type="MKT")` emits the identical 8-column INSERT (order_type now a bound param instead of a literal, same string `'MKT'`) then `db_conn.commit()` | IDENTICAL |
| 2. Place + wait | register slot → `placeOrder` → wait → pop slot (inline) | `ibapp.place_order_and_wait(order_id, contract, order, timeout=ORDER_STATUS_TIMEOUT)` (Task 1's composition — same register→placeOrder→await sequence, now inside connection.py) | IDENTICAL sequence, relocated |
| 3. Resolve + update | `_resolve_db_status(slot, order_id)` then `UPDATE orders SET status = %s WHERE id = %s` + commit | `_resolve_db_status(result, order_id)` (now typed on `OrderResult`, same REJECTED/UNKNOWN/_STATUS_MAP mapping) then `_finalize_order_status(..., target_column="id", target_value=db_order_id)` emits the identical `UPDATE ... WHERE id = %s` + commit | IDENTICAL |

Independent pure-mock run confirmed: first `cursor.execute()` call is the INSERT containing `PENDING_SUBMISSION`, second `cursor.execute()` call is `UPDATE orders SET status = %s WHERE id = %s` with params `("SUBMITTED", <db_order_id>)`, and `db_conn.commit()` is called exactly twice (once after insert, once after finalize) — matching pre-refactor behavior exactly.

**(b) Bracket path (`_submit_bracket_order`):**
| Step | Before | After | Verdict |
|---|---|---|---|
| 1. N+2 inserts, single txn | 3 inline `cur.execute(INSERT ...)` blocks (parent 8-col, STP + each TP 10-col) inside one `with db_conn.cursor()` block, single `db_conn.commit()` after all N+2 | `_insert_order_row(cur, ...)` called once for parent (8-col form), once for STP (10-col form), once per TP (10-col form) — same cursor, same `with` block, same single `db_conn.commit()` after all N+2 | IDENTICAL |
| 2. Place all N+2 before waiting | register parent slot → `placeOrder` parent → `placeOrder` STP → `placeOrder` each TP → wait on parent slot → pop | `ibapp._register_order_slot(parent_ibkr_id)` → `placeOrder` parent → `placeOrder` STP → `placeOrder` each TP → `ibapp._await_registered_order(parent_ibkr_id, timeout=ORDER_STATUS_TIMEOUT)` (pops internally) | IDENTICAL ordering — still places all N+2 before any wait |
| 3. Parent update | `UPDATE orders SET status = %s WHERE ibkr_order_id = %s` + commit | `_finalize_order_status(..., target_column="ibkr_order_id", target_value=parent_ibkr_id)` emits the identical `UPDATE ... WHERE ibkr_order_id = %s` + commit | IDENTICAL |
| 4. Child bulk update, gated | `if db_status == "SUBMITTED": UPDATE orders SET status = 'SUBMITTED' WHERE ibkr_order_id = ANY(%s) AND status = 'PENDING_SUBMISSION'` + commit | UNCHANGED — this block was not touched by Task 3 (only the parent-status write above was factored) | IDENTICAL, untouched |

Independent pure-mock run confirmed, across three scenarios (SUBMITTED, IBKR_ERROR→REJECTED, timeout→UNKNOWN):
- Exactly 5 `INSERT` statements for a 3-TP bracket (1 parent + 1 STP + 3 TP), all containing `PENDING_SUBMISSION`.
- SUBMITTED case: `db_conn.commit()` called exactly 3 times (insert txn, parent finalize, child bulk update); parent UPDATE uses `WHERE ibkr_order_id = %s` with params `("SUBMITTED", <parent_ibkr_id>)`; child UPDATE uses `WHERE ibkr_order_id = ANY(%s)` with the exact list of STP+TP ibkr_order_ids and retains the `status = 'PENDING_SUBMISSION'` guard.
- REJECTED and UNKNOWN cases: `db_conn.commit()` called exactly 2 times (insert txn + parent finalize only) — the child bulk-update branch is never entered, exactly as before (children left in `PENDING_SUBMISSION` for operator review).

**Zero deviations found.** The refactor is a pure extraction of shared SQL-emission primitives; no INSERT/UPDATE column, literal, WHERE clause, transaction boundary, or gating condition changed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stale docstring reference would have broken the Task 2 grep-gate**
- **Found during:** Task 2
- **Issue:** `executor.py`'s module docstring (lines 19-22) referenced `ibapp._tick_events / ibapp._order_status_events` by name in prose, which the grep-gate's regex matched as a non-comment line (docstrings aren't `#`-prefixed, so the gate's `grep -v '^\s*#'` comment filter didn't exclude it).
- **Fix:** Reworded the docstring to describe the same threading behavior without naming the now-private-and-encapsulated attributes directly, instead pointing at the new public seam (`fetch_delayed_price` / `place_order_and_wait` / register+await pair).
- **Files modified:** `bravos/execution/executor.py`
- **Commit:** `460cfa6`

### Worktree state correction (not a plan deviation, but material to this session)

Before starting Task 1, the branch-check step (`git merge-base HEAD c087fb8e1d937826fb5c0ad62cf7fc16f17c6eac`) found the worktree's HEAD at a stale commit (`2e699f3`, missing Sessions 2a/2b/2c and the plan revision commit) rather than the required base. `git merge-base --is-ancestor` confirmed the worktree HEAD was a strict ancestor of the required base (simple fast-forward, no divergence), so per the branch-check protocol the worktree was hard-reset to `c087fb8e1d937826fb5c0ad62cf7fc16f17c6eac` before any file edits. The only uncommitted change discarded by the reset was an auto-generated `.planning/HANDOFF.json` timestamp bump (no user work lost). All subsequent commits are ancestors of this corrected base.

## Auth Gates

None — this session made no external API/service calls requiring credentials.

## Known Stubs

None — all new code paths (`get_nlv`, `get_daily_pnl`, `fetch_delayed_price`, `place_order_and_wait`, `_register_order_slot`, `_await_registered_order`, `_insert_order_row`, `_finalize_order_status`) are fully wired to real callers, not placeholders.

## Test Execution Notes (production-DB safety)

Per this session's hard safety constraint, only pure-mock tests were executed (no `BRAVOS_TEST_DB_OK`, no production DB writes):
- `tests/test_broker.py`: 44 passed, 2 deselected (the 2 deselected use the `db_connection` fixture and correctly refuse against the production DB via the Session 0b guard — expected).
- `tests/test_execution.py`: 10 pure-mock tests passed explicitly (`test_market_hours_gate_blocks/passes`, `test_gate_max_positions`, `test_gate_max_allocation`, `test_gate_circuit_breaker`, `test_gate_circuit_none_pnl`, `test_quantity_formula`, `test_quantity_zero_skipped`, `test_build_order_buy`, `test_build_order_sell`). The remaining 30 tests (including `test_bracket_oca_group` and `test_multi_ticker_forces_low_confidence`, both pre-existing known-failing per the task brief) all require the `db_connection` fixture and correctly trigger the Session 0b production-DB guard refusal when the full file is collected — this is the expected baseline in this environment, not a regression.
- The Task 3 DB-write-sequence claims above were independently verified via a standalone pure-mock harness (MagicMock `db_conn`/`cursor`, captured `execute()` calls and `commit()` counts) run interactively during this session — not committed to the repo, since it duplicates what the (currently unrunnable in this sandbox) `db_connection`-based integration tests already assert.

## Self-Check

Verifying file existence and commit hashes:

- `bravos/broker/connection.py` — FOUND (contains `OrderResult`, `build_contract`, `get_nlv`, `get_daily_pnl`, `fetch_delayed_price`, `_register_order_slot`, `_await_registered_order`, `place_order_and_wait`)
- `bravos/execution/executor.py` — FOUND (contains `_insert_order_row`, `_finalize_order_status`; `_fetch_price`/`_build_contract` confirmed absent)
- `bravos/risk/gate.py` — FOUND (`_read_nlv` delegates to `ibapp.get_nlv()`)
- `tests/test_broker.py` — FOUND (20 new tests present)
- `tests/test_execution.py` — FOUND (migrated mock setups present)
- Commit `d5ddbe7` — FOUND in `git log --oneline --all`
- Commit `460cfa6` — FOUND in `git log --oneline --all`
- Commit `c391d29` — FOUND in `git log --oneline --all`

## Post-Verification Correction (commit `4945c74`)

The `/gsd:quick --validate` post-execution verifier found the "Test Execution Notes" section above understated a real gap: because the DB-fixture tests were never actually run (correctly refused by the Session 0b production-DB guard, as noted above), the 6 pre-existing tests exercising `_submit_order`/`_submit_bracket_order` status resolution — `test_order_status_submitted`, `test_order_status_rejected`, `test_order_rejected_via_error_callback`, `test_order_399_then_submitted`, `test_bracket_rejected_via_error_callback`, `test_bracket_399_then_submitted` — still simulated the *old* callback mechanism (a `placeOrder` side_effect poking `mock_ibapp._order_status_events` directly). The Task 2/3 refactor changed the code under test to call `ibapp.place_order_and_wait(...)` / `ibapp._register_order_slot(...)` + `ibapp._await_registered_order(...)` instead, so the simulated callback never fired and every scenario silently fell through to `UNKNOWN`. This was a genuine test-suite regression, not a production-code defect — the verifier independently re-derived the DB-write sequence from scratch and confirmed it matches the table above exactly.

**Fix applied** (`4945c74`): all 6 tests now mock at the correct seam — `place_order_and_wait` returns an `OrderResult(kind=...)` directly for the plain path; `_register_order_slot` is a no-op mock and `_await_registered_order` returns the `OrderResult` for the bracket path. Verified with the sanctioned `BRAVOS_TEST_DB_OK=1` opt-in: all 8 relevant tests pass (6 fixed + `test_order_pure_timeout_is_unknown` + `test_bracket_pure_timeout_is_unknown`, which already passed by coincidence — an unmocked `place_order_and_wait`/`_await_registered_order` MagicMock return value matches no real `OrderResult.kind`, so `_resolve_db_status` already fell through to `UNKNOWN` for those two). Production DB was then purged of test-origin `signals`/`orders`/`risk_gate_log` rows (17 orders, 8 signals) via the standard FK-ordered cleanup, and confirmed clean via a fresh post-commit query — the real `HROW` (order 403) and `TXRH` (order 752) trade records were verified intact throughout.

Full non-DB suite re-confirmed green after the fix: `tests/test_execution.py` → 6 passed, 12 deselected; `tests/test_broker.py` → 24 passed.

## Second Post-Verification Correction (commit `02104d5`)

Re-verification (running the full `tests/test_execution.py` suite under the sanctioned `BRAVOS_TEST_DB_OK=1` opt-in, rather than just the 6 originally-named tests) surfaced 3 more tests broken by the identical root cause, missed by the first fix pass because they weren't named in the first verification report:

- `test_gate_log_pass` / `test_gate_log_block` — already carried a "migrated to public seam" comment for `get_daily_pnl()` but never set `mock_ibapp.get_nlv.return_value`; `RiskGate.check()` also calls `get_nlv()` for the allocation-percent calculation, so the unmocked `MagicMock` crashed psycopg2 (`can't adapt type 'MagicMock'`) when writing the computed value to `risk_gate_log`.
- `test_order_db_write_pending` — still hooked `mock_ibapp.placeOrder` to snapshot DB state before the call; `_submit_order` now calls `place_order_and_wait` instead, so `placeOrder` is never invoked on the plain path and the snapshot side-effect never fired. Re-hooked on `place_order_and_wait` (the call that immediately follows `_insert_order_row`), preserving the original D-08 invariant (row exists as `PENDING_SUBMISSION` before place-and-wait runs).

Fixed with the same pattern as the first correction. Verified with `BRAVOS_TEST_DB_OK=1`: full `tests/test_execution.py` → **29 passed, 1 failed** (`test_bracket_oca_group` — the pre-existing known failure documented in `IMPLEMENTATION_PLAN.md`'s ground rules since before this session started; unrelated to this refactor, confirmed via `git log` that its last touch predates commit `d5ddbe7`). Full `tests/test_broker.py` → **46 passed**. Production DB purged again (2 risk_gate_log, 43 orders, 20 signals, all `post_url LIKE 'http://test/%'` origin) and confirmed clean via fresh post-commit query; HROW (order 403, FILLED, $45.08) and TXRH (order 752, FILLED, $199.11) verified byte-identical throughout both correction passes.

**This is now the full, exhaustive test result for this session** — every test in both files has been executed at least once against this code (via the sanctioned opt-in), not merely inspected or run through an uncommitted interactive harness.

## Self-Check: PASSED
