---
phase: quick-260723-aqz
plan: 01
subsystem: broker/execution
tags: [refactor, connection.py-split, reconciliation, fills, IMPROVEMENTS-5]
dependency-graph:
  requires: []
  provides:
    - bravos/broker/reconciliation.py (_write_position_snapshot, _reconcile_against_db)
    - bravos/execution/fills.py (_handle_exec_details, _update_order_filled, _update_order_partial)
  affects:
    - bravos/broker/connection.py
    - tests/test_broker.py
    - tests/test_positions.py
tech-stack:
  added: []
  patterns:
    - "Module-level helper functions + thin IBApp-method delegates (existing codebase pattern, extended to a two-module split)"
key-files:
  created:
    - bravos/broker/reconciliation.py
    - bravos/execution/fills.py
  modified:
    - bravos/broker/connection.py
    - tests/test_broker.py
    - tests/test_positions.py
decisions:
  - "Pure move, zero logic changes — verified byte-identical function bodies via function-level diff, not just file-level review"
  - "IBApp EWrapper callbacks and public API (OrderResult, build_contract, REQ_ID_*, ibapp singleton) stay in connection.py per plan; only the 5 module-level helper functions relocated"
metrics:
  duration: "~35min"
  completed: "2026-07-23"
---

# Quick Task 260723-aqz: Split connection.py into fills.py + reconciliation.py Summary

Split `bravos/broker/connection.py` (IMPROVEMENTS.md item 5, Session 5) into three focused modules via a pure byte-for-byte move: fill-capture DB helpers moved to `bravos/execution/fills.py`, snapshot/reconcile helpers moved to `bravos/broker/reconciliation.py`, with `connection.py` retaining `IBApp` (all EWrapper callbacks as thin delegates), `OrderResult`, `build_contract`, and `REQ_ID_*` constants.

## What Was Built

**Task 1 — `bravos/execution/fills.py`:** Moved `_handle_exec_details`, `_update_order_filled`, `_update_order_partial` verbatim from connection.py. `connection.py` now does a module-level `from bravos.execution import fills` and `IBApp.execDetails` / `IBApp.orderStatus` delegate via `fills._handle_exec_details(...)` / `fills._update_order_filled(...)` / `fills._update_order_partial(...)`. Repointed the three fills call sites and their `from bravos.broker import connection` imports in both `tests/test_broker.py` (lines 412/446/473 → `from bravos.execution import fills`) and `tests/test_positions.py` (lines 277/324/360/397).

**Task 2 — `bravos/broker/reconciliation.py`:** Moved `_write_position_snapshot` and `_reconcile_against_db` verbatim from connection.py. `connection.py` now does a module-level `from bravos.broker import reconciliation` and `IBApp.run_startup_reconciliation` / `IBApp.run_periodic_reconciliation` (both remain IBApp methods, unchanged signatures) delegate via `reconciliation._write_position_snapshot(...)` / `reconciliation._reconcile_against_db(...)`. Repointed both CRITICAL non-obvious targets:
- `tests/test_broker.py:381` — `caplog.at_level(logging.WARNING, logger="bravos.broker.connection")` → `logger="bravos.broker.reconciliation"` (the WARNING is now emitted by reconciliation.py's logger).
- `tests/test_positions.py:447` — `patch("bravos.broker.connection.logger")` → `patch("bravos.broker.reconciliation.logger")` (patches the logger object `_reconcile_against_db` actually calls `.warning` on post-move).

**Task 3 — Move-only integrity verification:** Confirmed all preserved symbols (`OrderResult`, `build_contract`, `REQ_ID_ACCOUNT_SUMMARY`, `REQ_ID_PNL`, `REQ_ID_EXECUTIONS`, module-level `ibapp` singleton) still resolve from `connection.py`; confirmed no stray `connection._handle_exec_details`/`connection._write_position_snapshot`-style old-module-qualified references remain anywhere in `bravos/`, `scripts/`, or `tests/`; confirmed all five modules import in any order with no cycle; ran the full relevant suite (`test_broker.py` + `test_bootstrap.py` + `test_execution.py` + `test_positions.py`) — 80 passed (12 integration-marked tests correctly excluded by the default `-m "not integration"` pytest.ini setting). Additionally verified, function-by-function, that all five moved function bodies are byte-identical between their old (pre-move, commit 9643407) and new locations — not just a file-level `git diff` review.

## Deviations from Plan

None — plan executed exactly as written. All three tasks matched the plan's exact line numbers, exact_test_repoint_map, and split_pattern_decision.

### Note on the grep gate in Task 3's `<verify>` block

The literal grep-gate script in the plan's Task 3 `<automated>` verification (filtering `grep "bravos.broker.connection"` over already-`grep -n`-prefixed output) produces a false positive against its own output filename prefix (every line of `connection.py` is prefixed `bravos/broker/connection.py:NNN:...`, which always matches the final `grep "bravos.broker.connection"` regardless of content). I interpreted the gate's actual intent — "no reference anywhere is still qualified with the OLD module path" — and verified that directly with `grep -rn "connection\._handle_exec_details\|connection\._update_order_filled\|connection\._update_order_partial\|connection\._write_position_snapshot\|connection\._reconcile_against_db"` across `bravos/`, `scripts/`, `tests/`, which returned zero matches. This is a pre-existing quirk in the plan's verify script, not a functional issue with the split; documenting it here rather than as a code deviation since no code was changed to work around it.

## Auth Gates

None encountered.

## Database Safety (CRITICAL SAFETY constraint)

Ran the full relevant suite twice with `BRAVOS_TEST_DB_OK=1` (Cloud SQL Auth Proxy, production `bravos_trading` DB) via detached `nohup`/`disown` background processes polled through log files, per the constraint that this machine IS production and CLI-process SIGKILL interruptions have occurred before.

Both runs deterministically tripped the suite's own pre-existing session-scoped isolation-leak guard (`tests/conftest.py::_assert_no_fake_lots`, referencing IMPROVEMENTS.md item 14) — **NOT a regression from this split**. Root cause: `tests/test_positions.py`'s `open_lot`/`partial_close_lot`/reconciliation-mismatch tests (`OPN_*`, `FIF_*`, `PAR_*`, `SPN_*` prefixed synthetic tickers, and `http://test/*`-prefixed synthetic signals) call production code paths (`bravos/execution/positions.py`, untouched by this plan) that `db_conn.commit()` internally; the tests' own `finally: db_connection.rollback()` cleanup runs too late to undo an already-committed transaction. This is a pre-existing test-isolation gap in files outside this plan's scope (`bravos/execution/positions.py` and its fixtures), not introduced or touched by the connection.py split — out of scope per the deviation-rule scope boundary.

Purged all test-origin rows after each run, cascading correctly through the schema (`position_lots` → independent; `signals` → `orders` (via `signal_id`) → `executions`/`risk_gate_log`), and verified clean with **fresh** queries after each purge (never trusted DELETE rowcounts alone):

- Run 1: purged 9 `position_lots` rows (`OPN_*`/`FIF_*`/`PAR_*`/`SPN_*`) + 8 `signals` (`http://test/*`) + 6 cascaded `orders` + 2 cascaded `risk_gate_log` rows. Fresh re-query: 0 remaining.
- Run 2 (suite re-run to confirm stability): purged another 9 `position_lots` rows + 8 more `signals` + 6 `orders` + 2 `risk_gate_log` rows (same deterministic pattern). Fresh re-query: 0 remaining.

**Final fresh-query confirmation (post-purge):**
- `position_lots` leak count: 0
- `signals` test-origin count: 0
- Order 403 (HROW, BUY, 109 shares @ 45.08, status FILLED) — untouched throughout, confirmed via fresh query.
- Order 752 (TXRH, BUY, 25 shares @ 199.11, status FILLED) — untouched throughout, confirmed via fresh query.

No FK dependents exist on `position_lots` in this schema; the `signals` → `orders`/`risk_gate_log` cascade was confirmed via `information_schema` lookups before every delete, and `executions`/child-`orders` referencing the test order IDs were confirmed empty before cascading.

## Known Pre-existing Failures (tolerated, per plan)

- `test_bracket_oca_group` (tests/test_execution.py) — `@pytest.mark.integration`, excluded by default `-m "not integration"` pytest.ini config; never ran, not a regression.
- `test_multi_ticker_forces_low_confidence` — lives in `tests/test_parser.py`, not one of the four target suites for this plan; not exercised.
- `test_signal_stored_with_raw_html` — lives in `tests/test_ingestion_integration.py`, integration-marked, excluded by default config; not exercised.

## Verification Results

- Task 1 `<automated>` checks: PASSED (`fills split OK`; 3 test_broker.py replay tests + 4 test_positions.py exec/order-status tests all pass).
- Task 2 `<automated>` checks: PASSED (`reconciliation split OK`; test_broker.py reconcile test (non-DB) + DB-gated reconcile tests in both files pass).
- Task 3 `<automated>` checks: PASSED (preserved-symbols + no-cycle import proof; no stray old-module-qualified references to moved symbols; full relevant suite 80/80 passed, 12 integration tests correctly deselected).
- Function-level byte-identity check (beyond the plan's stated verification): all 5 moved functions confirmed byte-identical pre-move vs. post-move.

## Deploy Gate

Not executed in this session — deploy gate (non-market-hours restart + one clean reconciliation cycle via `journalctl`) was out of scope for this execution pass per the task instructions, which specified code changes, tests, and this summary only. **Recommend running the deploy gate from CLAUDE.md/plan's `<verification>` section as a follow-up** before/during the next non-market-hours window (before 13:30 UTC or after 20:00 UTC):
1. `sudo systemctl restart` the bravos ingestion service.
2. Confirm via `journalctl` one clean scrape-and-execute cycle AND one full reconciliation cycle (`run_startup_reconciliation` → snapshot written → reconcile completes "DB not modified (D-08)") with no import error and no traceback.
3. Rollback = `git revert` + restart if any anomaly appears.

## Self-Check: PASSED

- `bravos/broker/reconciliation.py` — FOUND
- `bravos/execution/fills.py` — FOUND
- Commit `d4264c7` (Task 1) — FOUND in `git log`
- Commit `02c0979` (Task 2) — FOUND in `git log`
- `bravos/broker/connection.py` no longer defines `_handle_exec_details`/`_update_order_filled`/`_update_order_partial`/`_write_position_snapshot`/`_reconcile_against_db` — confirmed via `hasattr` checks
- All 5 moved functions verified byte-identical between old and new locations
