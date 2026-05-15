---
phase: 05-fill-capture-and-position-reconciliation
reviewed: 2026-05-15T09:19:47Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - bravos/broker/connection.py
  - bravos/execution/positions.py
  - scripts/run_ingestion.py
  - tests/test_positions.py
findings:
  critical: 2
  warning: 3
  info: 2
  total: 7
status: issues_found
---

# Phase 05: Code Review Report

**Reviewed:** 2026-05-15T09:19:47Z
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

Reviewed the Phase 5 fill capture and position reconciliation implementation: the IBKR
connection callback layer (`connection.py`), the FIFO lot manager (`positions.py`), the
ingestion daemon entry point (`run_ingestion.py`), and the corresponding test suite
(`test_positions.py`).

The FIFO lot logic in `positions.py` is correct and the overall threading model is sound.
Two blockers were found: a Python short-circuit evaluation bug that causes
`run_startup_reconciliation` to skip waiting for orders/account-summary data whenever
positions time out, and a non-atomic two-phase commit in `_handle_exec_details` that
leaves the DB in an inconsistent state if the process crashes between the two commits.
Three warnings cover a test isolation failure (rollback after commit is a no-op), a
TOCTOU race in the periodic reconciliation guard, and a silently ignored reconciliation
return value in the startup path.

---

## Critical Issues

### CR-01: Short-circuit evaluation silently skips orders/summary wait on positions timeout

**File:** `bravos/broker/connection.py:594-599`

**Issue:** The `all_done` boolean is computed with Python's short-circuit `and` operator:

```python
all_done = (
    self._positions_done.wait(timeout) and
    self._orders_done.wait(timeout) and
    self._summary_done.wait(timeout)
)
```

If `_positions_done.wait(timeout)` returns `False` (i.e., positions timed out), Python
short-circuits and **never calls** `_orders_done.wait()` or `_summary_done.wait()`. The
code proceeds directly to `_write_position_snapshot` and `_reconcile_against_db` using
`self._open_orders` and `self._account_summary`, both of which are still empty (`[]` and
`{}` respectively). The docstring claims "partial data still used for snapshot/reconcile",
but in the positions-timeout case, orders and account data are empty regardless of whether
the IBKR gateway actually responded.

**Fix:** Wait for all three events independently before computing `all_done`:

```python
pos_ok     = self._positions_done.wait(timeout)
orders_ok  = self._orders_done.wait(timeout)
summary_ok = self._summary_done.wait(timeout)
all_done   = pos_ok and orders_ok and summary_ok

if not all_done:
    logger.error(
        "Reconciliation timed out — partial data used "
        "(pos_ok=%s orders_ok=%s summary_ok=%s)",
        pos_ok, orders_ok, summary_ok,
    )
```

---

### CR-02: Non-atomic commit in `_handle_exec_details` — executions row committed before position lot

**File:** `bravos/broker/connection.py:748-770`

**Issue:** `_handle_exec_details` performs two sequential, independent commits:

1. **Line 756:** `db_conn.commit()` — commits the `executions` row.
2. **Line 764-770:** `positions.open_lot(...)` or `positions.partial_close_lot(...)` — each internally calls `db_conn.commit()` on the same connection.

If the process crashes (or `open_lot`/`partial_close_lot` raises an exception) after step 1
but before step 2, the `executions` table has a fill record with no corresponding
`position_lots` row. Because the INSERT uses `ON CONFLICT (exec_id) DO NOTHING`, a retry
of `_handle_exec_details` will skip the insertion silently, and the position lot will
**never be created**. The position is permanently lost to the DB while IBKR holds the
actual shares.

The reverse (lot written, execution row missing) cannot happen due to the ordering, but the
forward direction is a real data-loss risk when the fill is for a BUY order.

**Fix:** Use a single transaction for the execution row and the lot write. The simplest
approach is to defer the commit until after both writes succeed:

```python
with db_conn.cursor() as cur:
    cur.execute(
        "INSERT INTO executions (order_id, exec_id, shares, price, exec_time) "
        "VALUES (%s, %s, %s, %s, NOW()) "
        "ON CONFLICT (exec_id) DO NOTHING",
        (db_order_id, execution.execId, int(execution.shares), float(execution.price)),
    )
    # Do NOT commit here

from bravos.execution import positions
ticker = contract.symbol
if order_action == "BUY":
    positions._open_lot_no_commit(ticker, int(execution.shares), float(execution.price), db_conn)
else:
    positions._partial_close_lot_no_commit(ticker, int(execution.shares), float(execution.price), db_conn)

db_conn.commit()  # single atomic commit covers both tables
```

This requires adding internal `_no_commit` variants in `positions.py` (or adding a
`commit=True` parameter) so the lot helpers do not call `db_conn.commit()` when invoked
from the fill callback.

---

## Warnings

### WR-01: Test `finally: db_connection.rollback()` is a no-op after `open_lot`/`partial_close_lot` commit

**File:** `tests/test_positions.py:49, 101, 144, 225, 265`

**Issue:** Every integration test follows this pattern:

```python
try:
    open_lot(ticker, 10, 150.00, db_connection)   # internally calls db_connection.commit()
    ...
finally:
    db_connection.rollback()   # no-op: rollback after commit has no effect
```

`open_lot` and `partial_close_lot` each call `db_conn.commit()` internally (per
`positions.py` lines 29 and 112). In psycopg2, once a transaction is committed, a
subsequent `rollback()` starts a new (empty) transaction and rolls it back — it does not
undo the already-committed rows. Each test run permanently inserts rows into
`position_lots` and `broker_positions_snapshot` in the live integration DB.

Tests use `os.urandom(3).hex()` tickers to avoid key conflicts across runs, but the rows
accumulate indefinitely, which will eventually bloat the table and cause test slowdown.

**Fix:** Use the psycopg2 `autocommit=False` + savepoint pattern in the fixture, and
ensure functions under test do NOT commit their own connections when used via tests. The
cleanest approach is a `conftest.py` fixture that wraps the connection in a transaction and
rolls back at teardown, combined with `positions.py` functions accepting a `_commit=True`
parameter:

```python
# conftest.py — replace the db_connection fixture teardown
@pytest.fixture
def db_connection():
    conn = psycopg2.connect(...)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()   # always rollback; test data never persists
        conn.close()
```

Functions like `open_lot` would need a `_commit=True` default so production callers still
commit while tests pass `_commit=False` (or the fixture ensures autocommit=False and lets
the test handle commit/rollback).

---

### WR-02: TOCTOU race in `run_periodic_reconciliation` guard check

**File:** `bravos/broker/connection.py:628-636`

**Issue:** The guard at line 628 and the subsequent clear/request at lines 634-636 are not
atomic:

```python
if not self._positions_done.is_set():      # line 628: read _positions_done state
    ...
    return

self._positions.clear()                    # line 634
self._positions_done.clear()               # line 635
self.reqPositions()                        # line 636
```

Between the `is_set()` check and `_positions_done.clear()`, the ibkr-api thread can fire
`position()` callbacks and then `positionEnd()` (setting `_positions_done`). The sequence
would then be:

1. Main thread: `is_set()` returns `False` (not yet done) — guard passes
2. API thread: `positionEnd()` sets `_positions_done`
3. Main thread: `_positions.clear()` — discards the freshly-populated data
4. Main thread: `_positions_done.clear()` — clears the already-set event
5. Main thread: `reqPositions()` — fires a second, redundant request
6. Main thread: `_positions_done.wait(timeout)` — waits for the second response

This is unlikely in practice (the guard is specifically for the "prior request in flight"
case), but the race window is real and can cause a spurious duplicate `reqPositions` to the
gateway followed by a 30-second wait.

**Fix:** Reverse the guard: only proceed if `_positions_done.is_set()` is `True` at check
time. Since the ibkr-api thread only ever sets (not clears) `_positions_done` during
a request cycle, a True-at-check-time means the previous request is fully complete. The
clear-then-reissue pattern is then safe:

```python
# Proceed only when the previous reqPositions is fully settled
if not self._positions_done.is_set():
    logger.debug("Periodic reconciliation skipped — prior reqPositions still in flight")
    return

# Safe to clear and reissue: positionEnd has already fired
self._positions.clear()
self._positions_done.clear()
self.reqPositions()
```

The existing code already does this — the issue is the comment says "guard", but the guard
check is correct; the real concern is that after guard passes, `positionEnd` fires and sets
the event again before `_positions_done.clear()` runs. The fix is to accept this race as
benign (extra reqPositions at worst) and add documentation, or use a lock around the
entire check-clear-reissue sequence.

---

### WR-03: `run_startup_reconciliation` return value silently ignored in `run_ingestion.py`

**File:** `scripts/run_ingestion.py:150`

**Issue:**

```python
_ibapp.run_startup_reconciliation(_db_conn, timeout=30)
```

`run_startup_reconciliation` returns `True` on success, `False` on timeout. The return
value is discarded. A timeout during startup reconciliation is only observable via the
`logger.error(...)` call inside `run_startup_reconciliation` — the startup flow continues
unconditionally, including installing `_db_conn` for fill callbacks and starting the P&L
subscription.

While proceeding on partial data is the intended policy (D-08), the caller has no way to
distinguish a successful reconciliation from a timed-out one (e.g., to increment an alert
counter, page an operator, or skip the P&L subscription). Silently ignoring a boolean
return that encodes a meaningful error state is a maintenance hazard.

**Fix:** Capture and log the return value at the call site:

```python
recon_ok = _ibapp.run_startup_reconciliation(_db_conn, timeout=30)
if not recon_ok:
    logger.warning(
        "Startup reconciliation timed out — snapshot may be incomplete. "
        "Continuing with partial data per D-08."
    )
```

---

## Info

### IN-01: Heartbeat loop `time.sleep(HEARTBEAT_TIMEOUT)` is not interruptible by `_stop_event`

**File:** `bravos/broker/connection.py:284`

**Issue:** Inside `_heartbeat_loop`, after sending `reqCurrentTime()`, the loop calls
`time.sleep(HEARTBEAT_TIMEOUT)` (10 seconds) to allow time for the response before
checking `_last_heartbeat_at`. This sleep is a plain `time.sleep` — it does not respond to
`_stop_event`. If `stop()` is called during this window, the heartbeat thread will continue
sleeping for up to 10 seconds before the outer `_stop_event.wait()` on the next iteration
detects shutdown.

This is not a correctness bug (the thread is a daemon, so it won't block process exit), but
it means shutdown logging may show a 10-second gap between `stop()` call and heartbeat
thread exit.

**Fix:** Replace `time.sleep(HEARTBEAT_TIMEOUT)` with `_stop_event.wait(HEARTBEAT_TIMEOUT)`,
which returns immediately if stop is signalled:

```python
self.reqCurrentTime()
self._stop_event.wait(HEARTBEAT_TIMEOUT)   # interruptible sleep

if self._stop_event.is_set():
    return

elapsed = time.monotonic() - self._last_heartbeat_at
...
```

---

### IN-02: `_reconnect_loop` does not re-run startup reconciliation after reconnect

**File:** `bravos/broker/connection.py:351-356`

**Issue:** When `_reconnect_loop` successfully reconnects, it only resets `_reconnecting`
and returns. It does not call `run_startup_reconciliation` or re-subscribe to `reqPnL`.
After an outage and reconnect:

- `_positions`, `_open_orders`, `_account_summary` remain stale from the last successful
  reconciliation.
- `_daily_pnl` continues updating (the pnl subscription restarts as part of the reconnect
  handshake if `reqPnL` was previously called? — no, `reqPnL` must be explicitly re-called
  after reconnect).

The periodic reconciliation in `run_cycle` will eventually refresh positions, but the gap
window can be as long as `SCRAPE_INTERVAL_SECONDS`. The circuit breaker will remain at the
stale `_daily_pnl` value (or `None` if the session was long enough that the P&L
subscription reset).

**Fix:** After a successful reconnect, call `run_startup_reconciliation` (with a fresh DB
connection) and re-issue `reqPnL` if `_account_name` is populated. This mirrors the
startup sequence in `run_ingestion.py`. A helper function extracted from `main()` would
make this reusable.

---

_Reviewed: 2026-05-15T09:19:47Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
