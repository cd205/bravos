# Codebase Improvements — Prioritised

Reviewed 2026-07-04. **Revised 2026-07-09** after a week of live incidents that
validated several items and exposed new ones. Scope: maintainability and clarity
improvements that keep all functionality identical. Each item lists what, why,
effort, and risk so you can pick which to implement. Nothing here changes trading
behaviour except items explicitly marked ⚠ (correctness fixes you should want anyway).

**Codebase snapshot**: ~3,500 lines of app code across 12 modules + 5 scripts,
~2,500 lines of tests. Overall the code is in good shape — well-commented,
consistently styled, decision-tagged (D-xx). The issues below are structural:
duplication, circular-import workarounds, and cross-module reach-ins that make
changes riskier than they should be.

## Landed since the 2026-07-04 review

- `c858fb9`/`71d5b25` (quick-260707-75m) — internal IBKR reconnect now re-runs
  reconciliation via `IBApp._refresh_after_connect()`, fixing the stale-NLV →
  quantity=0 silent trade drop (HROW, Jul 6).
- `343972b`/`caa93e6`/`98c3391` (quick-260708-7lk) — Chrome-crash death loop fixed:
  `_check_session` is transport-crash-safe, `run_cycle` is decorated, and
  `_recover_scraper()` self-heals a dead driver on the next cycle (24-hour
  blind-spot incident, Jul 7–8).
- `e2f1f74` (quick-260708-9az) — `eTradeOnly`/`firmQuoteOnly` cleared on all Order
  objects, fixing error 10268 which rejected every order ibapi 9.81 sent to
  Gateway 1045. First accepted IBKR order followed (HROW BUY 109, Jul 9).

These fixes added a 7th+ inline DB-connect site and a third copy of the
post-connect refresh sequence — items 1 and 7 below grew accordingly.

---

## P1 — High value, low risk

### 1. Single shared DB connection helper (`bravos/db.py`)

**Problem**: `psycopg2.connect(host=settings.DB_HOST, ...)` with the
`change_me_at_deploy` fallback password is now copy-pasted in **ten+ places**
(grew from six since the last review):
- `scripts/run_ingestion.py:_get_db_connection()`
- `scripts/run_gmail.py:_db_connect()`
- `scripts/execute_pending.py:_get_db_connection()`
- `scripts/check_positions.py`
- `scripts/backfill_tp_sl.py`
- `scripts/validate_pipeline.py` (three sites)
- `bravos/ingestion/scraper.py` — twice, inline (in `_store_signal` and `process_alert`)
- `bravos/broker/connection.py:_open_short_lived_db_conn()` — added by the
  reconnect fix, deliberately consistent with the existing pattern pending this item

Any change to connection parameters (e.g. moving to a connection pool, adding
`connect_timeout`, switching auth) means editing ten files.

**Fix**: Create `bravos/db.py`:
```python
def get_connection():
    """Open a psycopg2 connection using settings + BRAVOS_DB_PASSWORD."""
```
Replace all six call sites. Also make it **fail loudly** if
`BRAVOS_DB_PASSWORD` is unset instead of silently using
`change_me_at_deploy` — today a missing env var produces a confusing
"password authentication failed" error far from the cause.

**Effort**: ~1 hour. **Risk**: minimal (mechanical replacement, existing tests cover the paths).

---

### 2. Remove the `scripts.run_ingestion` import from library code

**Problem**: `bravos/ingestion/scraper.py:325` does
`import scripts.run_ingestion as _daemon` to read the pause flag and queue.
This is an inverted dependency — a library module importing a script — and it
only works because of the `sys.path` hack in the script. It breaks any other
entry point (tests, `execute_pending.py`) that imports the scraper, silently
falling back to "not paused" via the `except (ImportError, AttributeError)` guard.

**Fix**: Create `bravos/runtime.py` holding the shared mutable state:
```python
ibkr_paused: bool = False
paused_signal_queue: queue.Queue[int] = queue.Queue()
```
`run_ingestion.py` signal handlers write to it; `scraper.py` reads from it.
No try/except import guard needed — the module always exists.

While there, move the `ibapp` singleton from `bravos/broker/connection.py`
into the same `runtime.py` so all process-level shared state lives in one
obvious place instead of two module-level globals set from outside.

**Effort**: ~1–2 hours. **Risk**: low; the pause/resume tests and a manual
`bravos-pause`/`bravos-resume` cycle verify it.

---

### 3. ⚠ Fix the orderStatus-timeout-means-SUBMITTED bug — NOW TWICE-CONFIRMED

**Problem** (observed live on July 2 AND July 8): in `bravos/execution/executor.py`,
`_submit_order` and `_submit_bracket_order` both do:
```python
db_status = _STATUS_MAP.get(final_status_raw, "SUBMITTED")
```
When the `orderStatus` callback never arrives, `final_status_raw` is `None` and
the order is recorded **SUBMITTED** even though it was rejected. This has now
masked **two real rejections**: EXEL July 2 (error 321, Read-Only) and HROW
July 8 (error 10268, EtradeOnly). Both sat in the DB as SUBMITTED and required
log forensics to discover. The pattern is structural: IBKR rejections arrive
via the `error` callback with `reqId == orderId` — `orderStatus` never fires —
so the executor's 3-second wait always times out and always assumes SUBMITTED.

**Fix**:
- Route order-scoped error callbacks (`reqId == order_id` and errorCode not in
  the informational set — note error 399 is a warning that accompanies an
  ACCEPTED order, so 399 must NOT be treated as a rejection) into the same
  `_order_status_events` slot so a rejection wakes the waiting executor with a
  real answer.
- Map `None` (true timeout, no error either) → `UNKNOWN` DB status, never
  `SUBMITTED`, and log at ERROR so it's alertable.

**Effort**: ~2-3 hours including tests. **Risk**: low — improves accuracy of
the orders table; no order placement behaviour changes. **This is now the
highest-value open item — it has hidden every order failure to date.**

---

### 4. Give `IBApp` a public API instead of private-attribute reach-ins

**Problem**: `executor.py` and `risk/gate.py` reach into IBApp internals:
`ibapp._account_summary`, `ibapp._tick_events`, `ibapp._tick_lock`,
`ibapp._mkt_req_counter`, `ibapp._order_status_events`, `ibapp._daily_pnl`,
`ibapp._db_conn`, `ibapp._account_name`. The executor even implements the
whole price-fetch slot protocol itself (`_fetch_price`). This couples three
modules to IBApp's internal data structures — renaming a field breaks
distant files with no type error.

**Fix**: Add methods to `IBApp` and update callers:
```python
def get_nlv(self) -> float | None: ...
def get_daily_pnl(self) -> float | None: ...
def fetch_delayed_price(self, ticker, timeout=5.0) -> float | None: ...
def place_order_and_wait(self, order_id, contract, order, timeout=3.0) -> str | None: ...
```
The slot/event mechanics move inside `connection.py` where they belong;
`executor.py` shrinks and reads as pure business logic.

**Effort**: ~3–4 hours. **Risk**: moderate-low — mechanical but touches the
order path; existing executor/broker tests plus one paper-mode
`execute_pending.py` run verify it.

---

## P1½ — New items from the July 7–9 incidents

### 14. ⚠ Stop tests writing to the production database

**Problem** (observed July 7–9): running the test suite re-inserted fake
position lots (OPN_*/PAR_*/FIF_*/SPN_* tickers) into the **live** `position_lots`
table — 603 rows accumulated and had to be manually purged (twice now; the first
purge was June 29). The fake lots inflate the risk gate's open-position count
(it read 4 when the true count was 0) and will eventually block real trades at
the MAX_OPEN_POSITIONS=20 limit. Root cause: integration tests connect to
whatever DB the environment points at — on the VM that IS production, and
Claude-driven or operator test runs happen on the VM routinely.

**Fix** (pick one, first is simplest):
- Make DB-touching tests require an explicit `BRAVOS_TEST_DB` env var and skip
  loudly when unset; CI/dev sets it to a scratch database.
- Or wrap each test in a transaction that rolls back (pytest fixture yielding a
  connection with `conn.rollback()` in teardown, never `commit()`).
Either way, add a conftest guard that refuses to run DB tests when
`DB_NAME == "bravos_trading"`.

**Effort**: ~2-3 hours. **Risk**: low — test-only changes.
**Priority: do this before any further test-heavy work on the VM.**

### 15. ⚠ Commit the running-but-uncommitted working-tree code

**Problem**: the systemd daemon runs directly from this working tree, and
several files it executes have **uncommitted changes**: `scraper.py` and
`run_ingestion.py` (the whole IBKR pause/resume feature), `parser.py` (weight
regex overhaul + comma-price fix), `execute_pending.py` (direct-execution
rewrite). The deployed pause/resume commands and the improved parser exist
ONLY in the working tree. Any `git checkout`/`git reset`/worktree accident
silently reverts production behaviour, and `git stash` during a future fix
would take the running code out from under the daemon.

**Fix**: review and commit the WIP as two or three logical commits
(pause/resume, parser fixes, execute_pending rewrite) plus the untracked
files that are real deliverables (`infra/bravos-pause`, `infra/bravos-resume`,
`scripts/check_positions.py`, `scripts/backfill_tp_sl.py`). 30 minutes of
review, zero code change.

**Effort**: ~30 min. **Risk**: minimal — it's already what's running.

### 16. Fill-capture gap: fills are lost when the placing client isn't connected

**Problem**: `execDetails` fills are only captured by the ibapi client that
placed the order, while it is connected. Two real gaps:
- Orders placed by `execute_pending.py` / one-off runs (clientId=2) fill later
  when only the daemon (clientId=1) is connected → no `executions` row, no
  `position_lots` lot. The HROW BUY 109 placed 2026-07-09 has exactly this gap
  — its fill needs manual backfill.
- Fills that occur while the daemon is disconnected (Gateway restart window,
  pause window) are never replayed — there is no `reqExecutions` call on
  (re)connect.

**Fix**:
- Add `reqExecutions` (with a same-day `ExecutionFilter`) to
  `run_startup_reconciliation` / `_refresh_after_connect`, inserting any
  missing rows via the existing idempotent `ON CONFLICT (exec_id) DO NOTHING`
  path — this closes both gaps with one mechanism.
- Optionally set Master Client ID = 1 in Gateway API config so clientId=1 also
  receives live callbacks for other clients' orders (Gateway GUI/VNC change,
  document in runbook).

**Effort**: ~half a day including tests. **Risk**: moderate-low — additive,
uses the already-idempotent fill path.

### 17. Gmail poller re-dispatch loop hides failures behind "OK"

**Problem** (observed July 7): when `process_alert()` fails internally, its
`@catch_cycle_exceptions` decorator swallows the exception, so the alert-socket
handler replies `OK` to the Gmail poller. The poller then calls
`_set_gmail_message_id()`, which UPDATEs zero rows (no signal was stored), so
the email is never marked seen — and gets re-dispatched every poll cycle. On
July 7 the HROW alert was re-dispatched every ~4 minutes for 14 hours. The
accidental retry loop actually saved that trade after the daemon recovered,
but it is unbounded, floods logs, and reports success where there was failure.

**Fix**:
- `process_alert()` should return an explicit success/failure result (it
  currently returns None either way); the socket handler replies `OK` only on
  success, `ERR` otherwise.
- In the poller, after N failed dispatches of the same Message-ID (suggest 10,
  spanning ~10 minutes), mark it seen and `send_alert` the operator instead of
  retrying forever. Bounded retry preserves the (genuinely useful) recovery
  behaviour without the infinite loop.

**Effort**: ~2-3 hours including tests. **Risk**: moderate-low — touches the
alert hot path; verify with a socket-dispatched test URL.

---

## P2 — Structural clarity

### 5. Split `broker/connection.py` (831 lines, 4 concerns)

**Problem**: The file mixes connection/heartbeat/reconnect logic, startup +
periodic reconciliation, fill capture (`_handle_exec_details`,
`_update_order_filled/partial` — which are really execution-domain DB writes),
and the module singleton. It's the file you most need to understand and the
hardest to read end-to-end.

**Fix**: Split into:
- `bravos/broker/connection.py` — IBApp, heartbeat, reconnect (~450 lines)
- `bravos/broker/reconciliation.py` — snapshot + reconcile helpers and the
  `run_*_reconciliation` orchestration (~150 lines)
- `bravos/execution/fills.py` — `_handle_exec_details`, `_update_order_filled`,
  `_update_order_partial` (they already deferred-import `bravos.execution.positions`,
  confirming they belong in the execution package) (~120 lines)

Keep the callback methods on IBApp as thin one-line delegates so nothing about
ibapi wiring changes.

**Effort**: ~half a day. **Risk**: low-moderate — pure moves, no logic change;
import paths in tests need updating.

---

### 6. Deduplicate `_submit_order` / `_submit_bracket_order`

**Problem**: `_submit_bracket_order` (170 lines) re-implements the
write-PENDING-row → register-slot → placeOrder → wait → map-status → update-row
sequence from `_submit_order`, with comments literally saying "copy
_submit_order pattern exactly". Two copies of the status-wait protocol means
bug #3 above had to be fixed in two places.

**Fix**: Extract shared helpers:
- `_insert_order_row(cur, ...) -> db_id`
- `_await_parent_status(ibapp, order_id, timeout) -> str | None`
- `_finalize_order_status(db_conn, ...)`

Both submit functions become short orchestrations of the same primitives.

**Effort**: ~2–3 hours. **Risk**: moderate-low; bracket tests exist
(test_execution.py covers D-20 fallback etc.).

---

### 7. Extract shared IBKR bootstrap for scripts

**Problem**: the connect → `run_startup_reconciliation` → set `_db_conn` →
`reqPnL` sequence now exists in **four copies**: `run_ingestion.py:_connect_ibkr()`,
`execute_pending.py`, `check_positions.py`, and (since the reconnect fix)
`connection.py:_refresh_after_connect()` — the last one deliberately mirrors
`_connect_ibkr` and carries a "kept in sync manually" comment, which is exactly
the kind of contract that silently rots. The ordering is non-obvious
(reconciliation must run before quantity calc or NLV is missing — that exact
bug cost a debugging session in June AND caused the missed HROW trade July 6).

**Fix**: Move the sequence into `bravos/broker/bootstrap.py` (parameterise
client_id and the PnL req_id), and have the daemon, `_refresh_after_connect`,
`execute_pending.py`, and `check_positions.py` all call it. Centralise the
req-id constants (REQ_ID_PNL=9002 is now defined identically in THREE files —
`connection.py`, `run_ingestion.py`, `validate_pipeline.py` — plus 9003 in
`execute_pending.py`).

**Effort**: ~2-3 hours. **Risk**: low.

---

### 8. Move order execution out of the scraper

**Problem**: `BravosScraper.process_alert()` fetches, parses, stores — and then
also runs the pause-check and calls `execute_signal()` with its own inline DB
connection. The scraper class does ingestion *and* order routing, which is why
it needs the runtime-state import (item 2) and why testing it requires stubbing
the executor.

**Fix**: `process_alert()` returns `(signal_id, is_new, confidence)`; the
caller (the alert-socket handler in `run_ingestion.py`) decides whether to
queue or execute. The scraper becomes pure ingestion; the daemon owns
orchestration. This also makes the flow visible in one file instead of two.

**Effort**: ~2 hours. **Risk**: moderate-low — changes the alert hot path;
verify with a socket-dispatched test URL.

---

## P3 — Smaller polish (do opportunistically)

### 9. Consolidate logging setup
`logging.basicConfig` is duplicated in `run_ingestion.py`, `run_gmail.py`,
`execute_pending.py` with the same format string. One `bravos/logging_setup.py`
helper. (~30 min)

### 10. Make DST-sensitive schedule times timezone-aware
The 14:30 UTC RiskGate reset and 06:00 UTC Chrome restart are documented as
"1 hour late in summer". `schedule` supports `.at("09:30", "America/New_York")`
in recent versions — one-line fixes that remove two standing caveats. (~30 min
+ verify library version)

### 11. Parser regex test corpus
`parser.py`'s weight regexes encode a lot of hard-won phrasing knowledge
(comments list 7+ observed variants). Turn the comment lists into a
parametrised pytest table (`@pytest.mark.parametrize` with real phrasings →
expected weights) so the next regex tweak can't silently regress a variant.
The 208-signal historical re-parse from June would have caught its bugs
instantly with this. (~1–2 hours, pure test addition)

### 12. Name the magic status strings
`'PENDING_SUBMISSION' / 'SUBMITTED' / 'REJECTED' / 'FILLED' / 'PARTIAL'` are
string literals across executor.py, connection.py, and queries. A small
`OrderStatus` constants class (or `enum.StrEnum`) makes grep and refactors
reliable. (~1 hour)

### 13. Trim phase-history comments
Comments like "Plan 03-2", "Phase 4 D-12", "WR-03", "CR-01" are valuable
history but assume the reader has `.planning/` open. Keep the *why* text,
drop or shorten the ticket-ID prefixes, and where a decision is essential
(e.g. D-08 write-before-placeOrder) keep the tag but state the rule in plain
words first. Purely editorial. (~1–2 hours across files)

---

## Suggested implementation order

| Batch | Items | Rationale |
|-------|-------|-----------|
| 0 | 15, 14 | Do first, same day: commit the running WIP (30 min, protects production), then test-DB isolation (protects the risk gate from fake data) |
| 1 | 3, 16 | The two remaining trade-integrity gaps: rejection masking and fill capture. Item 3 has hidden every order failure to date |
| 2 | 1, 2, 17 | DB helper, runtime-state module, bounded Gmail retry |
| 3 | 4, 6 | Do together — both restructure the executor/IBApp boundary |
| 4 | 7, 8 | Do together — both simplify entry-point orchestration; item 7 also kills the "kept in sync manually" mirror |
| 5 | 5 | After 4/6/7, the split falls out naturally with less churn |
| 6 | 9–13 | Fold into whichever batch touches the relevant files |

Total estimated effort: 3–4 focused days for batches 0–5.

## Retrospective note (2026-07-09)

The July incidents are a strong argument for batch 0 and 1: every failure mode
this week was invisible until log forensics — orders recorded SUBMITTED that
were rejected (item 3), a scraper blind for 24 hours while logging 19,000
identical lines (fixed), NLV silently zero after a reconnect (fixed), fake test
data steering the risk gate (item 14). The common thread is **failures that
report success**. Prioritise anything that converts a silent failure into a
loud one.

## What I deliberately did NOT flag

- The `EWrapper`/`EClient` multiple-inheritance pattern — unusual but it is
  the canonical ibapi idiom; changing it would fight the library.
- The per-alert short-lived DB connections — a pool would be "cleaner" but
  alert volume is ~4/day; the simplicity is worth more than the reuse.
- Test coverage volume — 2,500 test lines for 3,500 app lines is healthy.
- `parser.py` regex complexity — inherent to the problem; item 11 addresses
  the real risk (regression) without rewriting what works.
