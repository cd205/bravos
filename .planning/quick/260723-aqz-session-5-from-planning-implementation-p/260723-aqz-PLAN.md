---
phase: quick-260723-aqz
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - bravos/broker/connection.py
  - bravos/broker/reconciliation.py
  - bravos/execution/fills.py
  - tests/test_broker.py
  - tests/test_positions.py
autonomous: true
requirements: [IMPROVEMENTS-5]
must_haves:
  truths:
    - "The full test suite passes with identical BEHAVIOR after the split (import/patch targets updated, zero logic changes)"
    - "IBApp EWrapper callbacks (execDetails, orderStatus, position*, openOrder*, accountSummary*, etc.) remain instance methods on IBApp so ibapi's message loop can still call them"
    - "bravos/execution/fills.py owns the fill-capture DB helpers (_handle_exec_details, _update_order_filled, _update_order_partial)"
    - "bravos/broker/reconciliation.py owns the snapshot + reconcile module-level helpers (_write_position_snapshot, _reconcile_against_db)"
    - "connection.py imports reconciliation.py and fills.py at module level with NO import cycle"
    - "executor.py's `from bravos.broker.connection import OrderResult, build_contract` still resolves (these stay in connection.py)"
    - "bootstrap.py's `from bravos.broker.connection import REQ_ID_PNL` still resolves (REQ_ID_* stay in connection.py)"
    - "Every test that patched/imported a moved symbol at bravos.broker.connection.* is repointed to its new module (test_broker.py AND test_positions.py)"
  artifacts:
    - path: "bravos/execution/fills.py"
      provides: "_handle_exec_details, _update_order_filled, _update_order_partial (moved verbatim)"
      contains: "def _handle_exec_details"
    - path: "bravos/broker/reconciliation.py"
      provides: "_write_position_snapshot, _reconcile_against_db (moved verbatim)"
      contains: "def _reconcile_against_db"
    - path: "bravos/broker/connection.py"
      provides: "IBApp class (core/heartbeat/reconnect/public-API/callbacks as thin delegates), OrderResult, build_contract, REQ_ID_* constants"
      contains: "class IBApp"
  key_links:
    - from: "bravos/broker/connection.py"
      to: "bravos/execution/fills.py"
      via: "module-level import; execDetails/orderStatus delegate to fills._handle_exec_details / fills._update_order_filled / fills._update_order_partial"
      pattern: "from bravos.execution import fills|import.*fills"
    - from: "bravos/broker/connection.py"
      to: "bravos/broker/reconciliation.py"
      via: "module-level import; run_startup/periodic_reconciliation delegate to reconciliation._write_position_snapshot / reconciliation._reconcile_against_db"
      pattern: "from bravos.broker import reconciliation|import.*reconciliation"
    - from: "bravos/execution/executor.py"
      to: "bravos/broker/connection.py"
      via: "from bravos.broker.connection import OrderResult, build_contract (unchanged)"
      pattern: "from bravos.broker.connection import OrderResult, build_contract"
---

<objective>
Split the most central runtime file, `bravos/broker/connection.py`, into three focused modules — a PURE MOVE with ZERO logic changes (IMPROVEMENTS.md item 5, IMPLEMENTATION_PLAN.md Session 5):

- `bravos/broker/connection.py` — IBApp class (connection core, handshake, heartbeat, reconnect state machine), the Session-3 public API (get_nlv, get_daily_pnl, fetch_delayed_price, place_order_and_wait, _register_order_slot, _await_registered_order), OrderResult, build_contract, REQ_ID_* constants, _refresh_after_connect, replay_todays_fills, _open_short_lived_db_conn, and all EWrapper callbacks as thin delegating instance methods.
- `bravos/broker/reconciliation.py` — the two snapshot/reconcile module-level helpers (`_write_position_snapshot`, `_reconcile_against_db`).
- `bravos/execution/fills.py` — the three fill-capture module-level helpers (`_handle_exec_details`, `_update_order_filled`, `_update_order_partial`).

Purpose: connection.py is the file you most need to understand and the hardest to read end-to-end; separating reconciliation and execution-domain DB writes makes each concern reviewable in isolation.

Output: three modules, updated test import/patch targets (across BOTH test_broker.py and test_positions.py), green suite, deploy via non-market-hours restart + one clean reconciliation cycle.
</objective>

<execution_context>
@~/.claude/get-shit-done/workflows/execute-plan.md
@~/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md

<split_pattern_decision>
The codebase already establishes the pattern to follow: module-level HELPER FUNCTIONS that take `db_conn` (and other data) as parameters, invoked by thin IBApp methods/callbacks. This is exactly how `_write_position_snapshot`, `_reconcile_against_db`, `_handle_exec_details`, `_update_order_*` are already written today (Phase 03 STATE decision: "Helper functions _write_position_snapshot and _reconcile_against_db are module-level not IBApp methods — DB operations logically separate from IBKR connection class").

**Chosen pattern (consistent with the existing codebase):**
- The helper FUNCTIONS move to their new home module unchanged.
- The IBApp METHODS/CALLBACKS stay on IBApp in connection.py (ibapi requires EWrapper callbacks as instance methods; run_startup_reconciliation / run_periodic_reconciliation must stay callable as `ibapp.run_*_reconciliation(...)`). Their bodies are unchanged except that the now-relocated helper names resolve via the new module import (e.g. `reconciliation._write_position_snapshot(...)`, `fills._handle_exec_details(...)`).
- Do NOT convert any callback to a standalone function. Do NOT change any function signature. Do NOT alter any SQL, log string, control flow, or docstring semantics. Comment/docstring text that names the OLD module path may be corrected to the new path, but nothing else.

**Import-cycle map (verified before planning — must be preserved):**
- `connection.py` → imports `reconciliation.py` and `fills.py` at MODULE LEVEL. Safe: neither new module imports `connection.py`.
- `reconciliation.py` → imports only stdlib `logging`. No bravos imports needed (helpers take `db_conn` as a param). NO edge back to connection.
- `fills.py` → imports only stdlib `logging`; `_handle_exec_details` keeps its EXISTING deferred (call-time) import of `bravos.execution.positions`. NO edge back to connection.
- `bootstrap.py` → still `from bravos.broker.connection import REQ_ID_PNL` — REQ_ID_* STAY in connection.py. Unaffected.
- `executor.py` → still `from bravos.broker.connection import OrderResult, build_contract` — these STAY in connection.py. Unaffected.
- `scripts/run_ingestion.py` → calls `runtime.ibapp.run_periodic_reconciliation(...)` — that METHOD stays on IBApp. Unaffected.

Because connection.py imports fills.py at module level and fills._handle_exec_details is referenced inside IBApp.orderStatus/execDetails, the import MUST be a module-level `from bravos.execution import fills` / `from bravos.broker import reconciliation` at the top of connection.py (mirrors how connection.py already does a top-level `from bravos.db import get_connection`). This is proven cycle-free above — do not use a lazy import for these.
</split_pattern_decision>

<exact_test_repoint_map>
Every test reference to a MOVING symbol, with its new target. TWO test files reference the moved symbols: `tests/test_broker.py` AND `tests/test_positions.py`. Symbols that STAY in connection.py (IBApp, OrderResult, build_contract, REQ_ID_*, the `ibapp` singleton) are listed as UNCHANGED so the executor does not accidentally touch them.

── tests/test_broker.py ──

MOVING to `bravos.broker.reconciliation`:
- line 341: `from bravos.broker.connection import _write_position_snapshot` → `from bravos.broker.reconciliation import _write_position_snapshot`
- line 380: `from bravos.broker.connection import _reconcile_against_db` → `from bravos.broker.reconciliation import _reconcile_against_db`
- line 381: `caplog.at_level(logging.WARNING, logger="bravos.broker.connection")` → `logger="bravos.broker.reconciliation"` (CRITICAL — the WARNING is now emitted by reconciliation.py's logger; without this the caplog filter drops the record and the assertion at line 384 fails)
- line 389: `from bravos.broker.connection import _reconcile_against_db` → `from bravos.broker.reconciliation import _reconcile_against_db`

MOVING to `bravos.execution.fills`:
- line 412: `from bravos.broker import connection` → `from bravos.execution import fills`; line 432 call `fills._handle_exec_details(...)`
- line 446: `from bravos.broker import connection` → `from bravos.execution import fills`; line 464 call `fills._handle_exec_details(...)`
- line 473: `from bravos.broker import connection` → `from bravos.execution import fills`; line 492 call `fills._handle_exec_details(...)`
- (Lines 430/463/491 `patch("bravos.execution.positions.open_lot")` / `partial_close_lot` — UNCHANGED.)

── tests/test_positions.py (was NOT in the original files_to_read list; discovered during planning — MUST be repointed) ──

MOVING to `bravos.execution.fills`:
- line 277: `from bravos.broker import connection` → `from bravos.execution import fills`; line 299 call `fills._handle_exec_details(...)`
- line 324: `from bravos.broker import connection` → `from bravos.execution import fills`; line 342 call `fills._handle_exec_details(...)`
- line 360: `from bravos.broker import connection` → `from bravos.execution import fills`; line 366 call `fills._update_order_filled(...)`
- line 397: `from bravos.broker import connection` → `from bravos.execution import fills`; line 403 call `fills._update_order_partial(...)`
- (Lines 298/341 `patch("bravos.execution.positions.open_lot")` — UNCHANGED.)

MOVING to `bravos.broker.reconciliation`:
- line 432: `from bravos.broker.connection import _reconcile_against_db` → `from bravos.broker.reconciliation import _reconcile_against_db`
- line 447: `patch("bravos.broker.connection.logger")` → `patch("bravos.broker.reconciliation.logger")` (CRITICAL — this patches the logger object that `_reconcile_against_db` calls `.warning` on; after the move that function's `logger` binding lives in reconciliation.py, so patching connection.logger would mock the wrong object and the `mock_logger.warning.call_args_list` assertion at line 451/452 would find zero calls and fail)

UNCHANGED test references (symbols stay in connection.py — do NOT edit these):
- All `from bravos.broker.connection import IBApp` (test_broker.py, everywhere)
- `from bravos.broker.connection import IBApp, REQ_ID_EXECUTIONS` (test_broker.py:503)
- `from bravos.broker.connection import build_contract` (test_broker.py:569)
- `from bravos.broker.connection import IBApp, OrderResult` (test_broker.py:632,649,670,682,702)
- `import bravos.broker.connection as broker_module` (test_broker.py:74) — asserts `broker_module.ibapp is None`; singleton STAYS in connection.py
- `from bravos.broker import connection as conn_module` (test_broker.py:118, 233) — used for IBApp/error routing, symbols stay
- test_bootstrap.py: NO changes — mocks `ibapp.run_startup_reconciliation` / `replay_todays_fills` (IBApp methods, stay) and imports only `connect_and_prepare`.
- test_execution.py: NO changes — every `from bravos.broker.connection import OrderResult` stays valid; executor.py imports build_contract from connection.py (unchanged).

FULL-SWEEP GUARANTEE (verified at plan time): `grep -rln "_handle_exec_details|_update_order_filled|_update_order_partial|_write_position_snapshot|_reconcile_against_db" tests/` returns EXACTLY `tests/test_broker.py` and `tests/test_positions.py` — no other test file references the moved symbols. And no production caller outside connection.py imports these five symbols.
</exact_test_repoint_map>

# Source files under edit
@bravos/broker/connection.py
@bravos/broker/bootstrap.py
@bravos/execution/executor.py
@bravos/execution/positions.py
@tests/test_broker.py
@tests/test_positions.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Extract fill-capture helpers into bravos/execution/fills.py</name>
  <files>bravos/execution/fills.py, bravos/broker/connection.py, tests/test_broker.py, tests/test_positions.py</files>
  <action>
Create `bravos/execution/fills.py` by moving `_handle_exec_details`, `_update_order_filled`, and `_update_order_partial` VERBATIM from connection.py (the "Phase 5: Module-level fill capture + order status helpers" block, currently ~lines 988–1101). Copy the functions byte-for-byte: same signatures, same SQL, same log strings, same `ON CONFLICT (exec_id) DO NOTHING RETURNING id` replay gate, same deferred `from bravos.execution import positions` import inside `_handle_exec_details`. Add a module docstring naming this as the execution-domain fill-capture module (item 5) and `import logging` + `logger = logging.getLogger(__name__)`.

In `connection.py`:
- DELETE the three moved functions from their old location.
- Add a MODULE-LEVEL import near the other bravos imports: `from bravos.execution import fills`. (Cycle-free per the import map.) Do NOT use a lazy import.
- Update the call sites so the callbacks become thin delegates (bodies otherwise UNCHANGED):
  - `IBApp.execDetails`: `_handle_exec_details(self._db_conn, execution, contract)` → `fills._handle_exec_details(self._db_conn, execution, contract)`
  - `IBApp.orderStatus`: `_update_order_filled(self._db_conn, orderId, avgFillPrice)` → `fills._update_order_filled(...)`; `_update_order_partial(...)` → `fills._update_order_partial(...)`

In `tests/test_broker.py`, apply ONLY the fills repoints (lines 412/446/473 import → `from bravos.execution import fills`; lines 432/464/492 call → `fills._handle_exec_details(...)`). Leave the positions.open_lot / partial_close_lot patch lines untouched.

In `tests/test_positions.py`, apply ONLY the fills repoints:
- lines 277/324/360/397: `from bravos.broker import connection` → `from bravos.execution import fills`
- line 299/342: `connection._handle_exec_details(...)` → `fills._handle_exec_details(...)`
- line 366: `connection._update_order_filled(...)` → `fills._update_order_filled(...)`
- line 403: `connection._update_order_partial(...)` → `fills._update_order_partial(...)`
- Leave the `patch("bravos.execution.positions.open_lot")` lines untouched.

Do NOT touch reconciliation helpers or test_positions.py line 432/447 in this task (Task 2 owns those).
  </action>
  <verify>
    <automated>/home/chris_s_dodd/miniconda3/bin/python -c "from bravos.execution import fills; from bravos.broker import connection; assert hasattr(fills,'_handle_exec_details') and hasattr(fills,'_update_order_filled') and hasattr(fills,'_update_order_partial'); assert not hasattr(connection,'_handle_exec_details'); assert connection.fills is fills; print('fills split OK')"</automated>
    <automated>cd /home/chris_s_dodd/bravos && /home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_broker.py -k "replay_duplicate or replay_unknown or replay_fresh" tests/test_positions.py -k "exec_details or order_status_filled or order_status_partial" -q</automated>
  </verify>
  <done>fills.py exists with the three helpers moved verbatim; connection.py no longer defines them and delegates via `fills.*`; both test_broker.py and test_positions.py fills call sites repointed; the fill-dispatch + order-status tests pass; no import cycle.</done>
</task>

<task type="auto">
  <name>Task 2: Extract snapshot/reconcile helpers into bravos/broker/reconciliation.py</name>
  <files>bravos/broker/reconciliation.py, bravos/broker/connection.py, tests/test_broker.py, tests/test_positions.py</files>
  <action>
Create `bravos/broker/reconciliation.py` by moving `_write_position_snapshot` and `_reconcile_against_db` VERBATIM from connection.py (the "Plan 03-3: Module-level helper functions" block, currently ~lines 926–985). Copy byte-for-byte: same signatures `(db_conn, positions)` / `(db_conn, ibkr_positions, ibkr_orders)`, same SQL, same WARNING/INFO log strings ("RECONCILE MISMATCH", "RECONCILE IBKR HAS POSITION NOT IN DB", "Reconciliation complete — DB not modified (D-08)"). Add a module docstring naming this as the reconciliation snapshot/compare module (item 5) and `import logging` + `logger = logging.getLogger(__name__)`.

In `connection.py`:
- DELETE the two moved functions from their old location.
- Add a MODULE-LEVEL import with the other bravos imports: `from bravos.broker import reconciliation`. (Cycle-free.)
- Update the call sites inside IBApp methods `run_startup_reconciliation` and `run_periodic_reconciliation` so they delegate (method bodies otherwise UNCHANGED): `_write_position_snapshot(db_conn, ...)` → `reconciliation._write_position_snapshot(...)`; `_reconcile_against_db(...)` → `reconciliation._reconcile_against_db(...)`. Both methods MUST remain IBApp methods (run_periodic_reconciliation is called as `runtime.ibapp.run_periodic_reconciliation(...)` in scripts/run_ingestion.py; run_startup_reconciliation is called by bootstrap.py).

In `tests/test_broker.py`, apply ONLY the reconciliation repoints:
- line 341: import `_write_position_snapshot` from `bravos.broker.reconciliation`
- lines 380 & 389: import `_reconcile_against_db` from `bravos.broker.reconciliation`
- line 381: `logger="bravos.broker.connection"` → `logger="bravos.broker.reconciliation"`

In `tests/test_positions.py`, apply ONLY the reconciliation repoints:
- line 432: `from bravos.broker.connection import _reconcile_against_db` → `from bravos.broker.reconciliation import _reconcile_against_db`
- line 447: `patch("bravos.broker.connection.logger")` → `patch("bravos.broker.reconciliation.logger")` (CRITICAL — patches the logger object `_reconcile_against_db` calls `.warning` on; after the move that binding is in reconciliation.py)
  </action>
  <verify>
    <automated>/home/chris_s_dodd/miniconda3/bin/python -c "from bravos.broker import reconciliation, connection; assert hasattr(reconciliation,'_write_position_snapshot') and hasattr(reconciliation,'_reconcile_against_db'); assert not hasattr(connection,'_write_position_snapshot') and not hasattr(connection,'_reconcile_against_db'); assert connection.reconciliation is reconciliation; print('reconciliation split OK')"</automated>
    <automated>cd /home/chris_s_dodd/bravos && /home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_broker.py -k "reconcile" tests/test_positions.py -k "reconciliation" -q</automated>
  </verify>
  <done>reconciliation.py exists with both helpers moved verbatim; connection.py no longer defines them and delegates via `reconciliation.*`; run_startup/periodic_reconciliation remain IBApp methods; the reconcile tests in BOTH files pass including the mismatch-WARNING assertions with repointed logger name (caplog filter) and repointed `patch(...logger)` target; no import cycle.</done>
</task>

<task type="auto">
  <name>Task 3: Verify move-only integrity + full suite green</name>
  <files>bravos/broker/connection.py, tests/test_broker.py, tests/test_positions.py</files>
  <action>
No functional edits in this task — it is the move-only integrity gate. Confirm the three constraints a "pure move" most easily breaks:

1. Preserved-symbol check: `OrderResult`, `build_contract`, `REQ_ID_ACCOUNT_SUMMARY`, `REQ_ID_PNL`, `REQ_ID_EXECUTIONS`, and the module-level `ibapp` singleton MUST still live in connection.py (executor.py and bootstrap.py import them from there). If any accidentally moved, move it back.

2. No-stray-reference grep gate over the MOVED symbols (filter comment lines so header/docstring prose can't self-trigger the gate):
   - fills symbols: no reference outside `bravos/execution/fills.py` may still be qualified with `bravos.broker.connection`.
   - reconcile symbols: no reference outside `bravos/broker/reconciliation.py` may still be qualified with `bravos.broker.connection`.

3. Import-cycle proof: importing all five modules in any order must not raise.

Then run the FULL relevant suite. Every test must pass with unmodified behavior. A failure is an import/patch-target miss (fix per the exact_test_repoint_map) OR an accidental logic change (revert to verbatim) — NOT a reason to change production behavior.
  </action>
  <verify>
    <automated>/home/chris_s_dodd/miniconda3/bin/python -c "import bravos.execution.fills, bravos.broker.reconciliation, bravos.broker.connection, bravos.broker.bootstrap, bravos.execution.executor; from bravos.broker.connection import OrderResult, build_contract, REQ_ID_PNL, REQ_ID_ACCOUNT_SUMMARY, REQ_ID_EXECUTIONS; print('preserved-symbols + no-cycle OK')"</automated>
    <automated>cd /home/chris_s_dodd/bravos && bash -c 'set -e; test -z "$(grep -rn --include=*.py "_handle_exec_details\|_update_order_filled\|_update_order_partial" bravos/ scripts/ tests/ | grep -v "^[^:]*:[0-9]*:[[:space:]]*#" | grep -v "bravos/execution/fills.py" | grep "bravos.broker.connection")" && test -z "$(grep -rn --include=*.py "_write_position_snapshot\|_reconcile_against_db" bravos/ scripts/ tests/ | grep -v "^[^:]*:[0-9]*:[[:space:]]*#" | grep -v "bravos/broker/reconciliation.py" | grep "bravos.broker.connection")" && echo "no stray connection-qualified refs to moved symbols"'</automated>
    <automated>cd /home/chris_s_dodd/bravos && /home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_broker.py tests/test_bootstrap.py tests/test_execution.py tests/test_positions.py -q</automated>
  </verify>
  <done>All preserved symbols still resolve from connection.py; no stray connection-qualified references to any of the five moved symbols remain in production or tests; all five modules import in any order without a cycle; test_broker.py + test_bootstrap.py + test_execution.py + test_positions.py all pass.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| ibkr-api thread → DB (via fills.py) | execDetails/orderStatus callbacks fire on the api thread and write fills/positions using the api-thread-owned `_db_conn`. UNCHANGED by the move — same connection-ownership rules; fills.py functions still receive `db_conn` as a parameter and never open their own. |
| module import graph | A pure move can silently introduce an import cycle or break a downstream import path (executor/bootstrap), crashing the live daemon at startup rather than at test time. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-aqz-01 | Tampering | fill-capture DB writes moved to fills.py | accept | Pure move — SQL and the `ON CONFLICT ... RETURNING id` replay-safety gate are copied verbatim and asserted unchanged by the replay + exec-details tests (Task 1). No new trust surface. |
| T-aqz-02 | Denial of Service | connection.py module import at daemon startup | mitigate | Task 3's import-cycle proof imports all five modules in one process before any test runs; a cycle or broken downstream import (executor/bootstrap) fails the gate here, not on the live VM. Deploy gate additionally requires a non-market-hours restart with one clean reconciliation cycle before the change is trusted. |
| T-aqz-03 | Repudiation | reconcile WARNING logs (RECONCILE MISMATCH) moved to reconciliation.py logger | mitigate | The caplog logger-name repoint (test_broker.py:381) AND the `patch(...reconciliation.logger)` repoint (test_positions.py:447) ensure the audit WARNING is still emitted/asserted under the new logger; operator-review log strings copied verbatim. |
</threat_model>

<verification>
1. All three tasks' `<automated>` checks pass.
2. Full suite: `tests/test_broker.py` (46) + `tests/test_bootstrap.py` (6) + `tests/test_execution.py` (30) + `tests/test_positions.py` all green under `/home/chris_s_dodd/miniconda3/bin/python`.
   - Note: DB-touching tests (test_position_snapshot_written, test_periodic_reconciliation_mismatch, exec-details tests using `db_connection`) require the Cloud SQL Auth Proxy on 127.0.0.1:5432 and `BRAVOS_DB_PASSWORD`; on the VM these run. If the proxy is down they skip/error at the fixture — an environment condition, not a split regression. Confirm the non-DB tests (import, IBApp, delegate-target, OrderResult) pass regardless.
3. Move-only review: `git diff` shows only relocation of the five functions (identical bodies), the delegate-name changes at call sites, the two new top-level imports in connection.py, and the enumerated test repoints in test_broker.py + test_positions.py. NO SQL, log-string, control-flow, or signature changes.

## Deploy gate (this file is the trading system's runtime core)
Deploy only during non-market hours (before 13:30 UTC or after 20:00 UTC):
1. Atomic / revertable commit(s) on `main`.
2. `sudo systemctl restart` the bravos ingestion service.
3. `journalctl` shows one clean scrape-and-execute cycle AND one full reconciliation cycle (run_startup_reconciliation → snapshot written → reconcile completes "DB not modified (D-08)") with no import error and no traceback.
4. Rollback = `git revert` + restart if any anomaly appears. No migrations involved.
</verification>

<success_criteria>
- `bravos/broker/reconciliation.py` and `bravos/execution/fills.py` exist, each containing the moved helpers VERBATIM.
- `bravos/broker/connection.py` retains IBApp (all EWrapper callbacks as instance methods / thin delegates), OrderResult, build_contract, REQ_ID_* constants, and the `ibapp` singleton.
- connection.py imports reconciliation.py and fills.py at module level; no import cycle in any import order.
- executor.py (`OrderResult, build_contract`) and bootstrap.py (`REQ_ID_PNL`) import paths unbroken.
- All enumerated test import/patch/logger-name targets updated in BOTH test_broker.py and test_positions.py; no other test touched.
- Full suite passes with identical behavior; `git diff` review confirms move-only (zero logic changes).
- Deploy gate satisfied: non-market-hours restart + one clean reconciliation cycle observed.
</success_criteria>

<output>
After completion, create `.planning/quick/260723-aqz-session-5-from-planning-implementation-p/260723-aqz-SUMMARY.md`
</output>
