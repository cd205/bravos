# Phase 5: Fill Capture and Position Reconciliation - Pattern Map

**Mapped:** 2026-05-15
**Files analyzed:** 5 new/modified files
**Analogs found:** 5 / 5

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bravos/broker/connection.py` | service (IBKR callback layer) | event-driven | `bravos/broker/connection.py` (self — Phase 4 state) | exact — extend existing class |
| `bravos/execution/positions.py` | service | CRUD | `bravos/execution/executor.py` | role-match — same module tier, pure DB operations |
| `scripts/run_ingestion.py` | config/entrypoint | request-response | `scripts/run_ingestion.py` (self — Phase 4 state) | exact — extend existing function |
| `infra/migrate_phase5.sql` | migration | batch | `infra/migrate_phase4.sql` | exact — same migration pattern |
| `tests/test_positions.py` | test | CRUD + event-driven | `tests/test_execution.py` | exact — same Wave 0 stub pattern |

---

## Pattern Assignments

### `bravos/broker/connection.py` — add `execDetails`, `execDetailsEnd`; extend `orderStatus`

**Analog:** `bravos/broker/connection.py` (existing file; lines cited below)

**Imports pattern** (lines 1–22): No new imports needed. All existing imports (`logging`, `threading`, `time`, `ibapi.client`, `ibapi.wrapper`, `bravos.config.settings`) already cover Phase 5 additions. The `positions` module is imported inside the callback body to avoid circular imports (same pattern as executor.py line 77: `from bravos.broker.connection import ibapp`).

**Instance-variable pattern for `_db_conn`** — mirror `_order_status_events` and `_tick_events` already on `IBApp.__init__` (lines 97–98):
```python
# Add in IBApp.__init__ after existing Phase 4 slots:
# ── Phase 5: dedicated db connection for fill callbacks (api thread) ──
self._db_conn = None  # set by run_ingestion.py main() after connect
```

**Existing `orderStatus` callback** (lines 480–511) — extend the existing method body; DO NOT replace it. Add fill-status branches after the existing slot-notification code:
```python
def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, ...):
    # Phase 4 block (lines 504–510) — keep exactly as-is:
    logger.info("orderStatus orderId=%s status=%s filled=%s remaining=%s", ...)
    slot = self._order_status_events.get(orderId)
    if slot is not None:
        slot["status"] = status
        slot["event"].set()

    # Phase 5 extension — append AFTER the Phase 4 block:
    if self._db_conn is not None:
        if status == "Filled":
            _update_order_filled(self._db_conn, orderId, avgFillPrice)
        elif status == "PartiallyFilled":
            _update_order_partial(self._db_conn, orderId, avgFillPrice)
```

**New `execDetails` callback** — add as new method on IBApp, following same structure as `positionEnd` (lines 391–398) and `openOrderEnd` (lines 414–422): thin callback that delegates immediately to a module-level helper:
```python
def execDetails(self, reqId: int, contract, execution) -> None:
    """
    Canonical fill capture callback. Fires once per fill on the ibkr-api thread.
    Writes executions row then dispatches to positions.py.
    """
    if self._db_conn is None:
        logger.warning(
            "execDetails: _db_conn not set — skipping fill capture exec_id=%s",
            execution.execId,
        )
        return
    _handle_exec_details(self._db_conn, execution, contract)

def execDetailsEnd(self, reqId: int) -> None:
    """Signals batch completion. Per D-04: individual fills processed per-event."""
    logger.info(
        "execDetailsEnd received (reqId=%s) — all fills for this batch processed",
        reqId,
    )
```

**New `run_periodic_reconciliation` method** — follows `run_startup_reconciliation` (lines 524–564) exactly, simplified to positions-only:
```python
def run_periodic_reconciliation(self, db_conn, timeout: float = 30) -> None:
    """
    Called after each scrape cycle (D-08). Reuses module-level helpers from Phase 3.
    Guard: skip if a reconciliation is already in progress.
    """
    if not self._positions_done.is_set():
        logger.debug("Periodic reconciliation skipped — prior reqPositions still in flight")
        return
    self._positions.clear()
    self._positions_done.clear()
    self.reqPositions()
    got = self._positions_done.wait(timeout)
    if not got:
        logger.warning(
            "Periodic reconciliation: reqPositions timed out — using partial data"
        )
    _write_position_snapshot(db_conn, self._positions)
    _reconcile_against_db(db_conn, self._positions, [])
    logger.info("Periodic reconciliation complete")
```

**New module-level helper functions** — follow the pattern of `_write_position_snapshot` and `_reconcile_against_db` (lines 570–627): module-level functions, NOT methods on IBApp. Use `with db_conn.cursor() as cur:` + `db_conn.commit()` after writes.

```python
def _handle_exec_details(db_conn, execution, contract) -> None:
    """Module-level helper: write executions row + dispatch to positions.py."""
    logger.info(
        "execDetails: execId=%s orderId=%s side=%s shares=%s price=%s",
        execution.execId, execution.orderId, execution.side,
        execution.shares, execution.price,
    )
    # Look up internal order_id and action from ibkr_order_id
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, action FROM orders WHERE ibkr_order_id = %s",
            (execution.orderId,),
        )
        row = cur.fetchone()
    if row is None:
        logger.error(
            "execDetails: no order found for ibkr_order_id=%s — cannot write execution",
            execution.orderId,
        )
        return
    db_order_id, order_action = row

    # Write execution row (idempotent via exec_id UNIQUE)
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO executions (order_id, exec_id, shares, price, exec_time) "
            "VALUES (%s, %s, %s, %s, NOW()) ON CONFLICT (exec_id) DO NOTHING",
            (db_order_id, execution.execId, int(execution.shares), execution.price),
        )
    db_conn.commit()

    # Dispatch to positions.py
    from bravos.execution import positions
    ticker = contract.symbol
    if order_action == "BUY":
        positions.open_lot(ticker, int(execution.shares), float(execution.price), db_conn)
    else:  # SELL
        positions.partial_close_lot(
            ticker, int(execution.shares), float(execution.price), db_conn
        )


def _update_order_filled(db_conn, ibkr_order_id: int, avg_fill_price: float) -> None:
    """Update orders.status to FILLED and record fill_price + filled_at."""
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status='FILLED', fill_price=%s, filled_at=NOW() "
            "WHERE ibkr_order_id=%s",
            (avg_fill_price, ibkr_order_id),
        )
    db_conn.commit()


def _update_order_partial(db_conn, ibkr_order_id: int, avg_fill_price: float) -> None:
    """Update orders.status to PARTIAL (intermediate fill)."""
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status='PARTIAL', fill_price=%s "
            "WHERE ibkr_order_id=%s",
            (avg_fill_price, ibkr_order_id),
        )
    db_conn.commit()
```

---

### `bravos/execution/positions.py` — new module

**Analog:** `bravos/execution/executor.py` (same package tier, module-level functions, pure DB operations)

**Module header pattern** (executor.py lines 1–22): docstring, `import logging`, module-level logger, no IBApp imports at module top.

```python
"""
bravos/execution/positions.py — Position lot management (Phase 5).

Exposes open_lot(), partial_close_lot() for the execDetails callback.
FIFO lot closure: oldest open lot closed first (D-05/D-06).
All functions accept a psycopg2 db_conn and commit on success.
No ibapi dependency — independently testable.
"""
import logging

logger = logging.getLogger(__name__)
```

**`open_lot` function** — follows `_calculate_quantity` close-action DB pattern (executor.py lines 225–232): `with db_conn.cursor() as cur:` + parameterized INSERT + `db_conn.commit()`:

```python
def open_lot(ticker: str, shares: int, entry_price: float, db_conn) -> None:
    """
    Insert a new open lot. Called from execDetails for BUY fills.
    lot_opened_at defaults to NOW() per schema.sql column default.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO position_lots (ticker, quantity, entry_price) "
            "VALUES (%s, %s, %s)",
            (ticker, shares, entry_price),
        )
    db_conn.commit()
    logger.info("open_lot: ticker=%s shares=%s entry_price=%s", ticker, shares, entry_price)
```

**`partial_close_lot` function** — FIFO loop; handles both partial and full closes (D-06 discretion: single function). Pattern for `SELECT ... FOR UPDATE` ordering from `_reconcile_against_db` (connection.py lines 597–601) and FIFO logic from RESEARCH Pattern 4:

```python
def partial_close_lot(
    ticker: str, shares_to_close: int, exit_price: float, db_conn
) -> None:
    """
    FIFO lot closure: close oldest open lots first until shares_to_close exhausted.
    Full close (close action): call with shares_to_close = total open quantity.

    AUDIT-04/AUDIT-06: when partially closing a lot, appends a NEW closed row
    for the closed portion; reduces quantity on the surviving open row.
    Per-lot P&L = (exit_price - entry_price) × quantity_closed (D-07).
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, quantity, entry_price FROM position_lots "
            "WHERE ticker = %s AND lot_closed_at IS NULL "
            "ORDER BY lot_opened_at ASC",
            (ticker,),
        )
        lots = cur.fetchall()

    remaining = shares_to_close
    with db_conn.cursor() as cur:
        for lot_id, lot_qty, entry_price in lots:
            if remaining <= 0:
                break
            if lot_qty <= remaining:
                # Close this lot entirely
                pnl = (exit_price - float(entry_price)) * lot_qty
                cur.execute(
                    "UPDATE position_lots "
                    "SET lot_closed_at=NOW(), exit_price=%s, pnl=%s "
                    "WHERE id=%s",
                    (exit_price, pnl, lot_id),
                )
                remaining -= lot_qty
                logger.info(
                    "partial_close_lot: FULL closed lot_id=%s ticker=%s qty=%s pnl=%.2f",
                    lot_id, ticker, lot_qty, pnl,
                )
            else:
                # Partial close: reduce surviving lot; append closed record
                qty_closed = remaining
                pnl = (exit_price - float(entry_price)) * qty_closed
                cur.execute(
                    "UPDATE position_lots SET quantity=%s WHERE id=%s",
                    (lot_qty - qty_closed, lot_id),
                )
                # AUDIT-04: new row for the closed portion (append-only per AUDIT-06)
                cur.execute(
                    "INSERT INTO position_lots "
                    "(ticker, lot_opened_at, quantity, entry_price, "
                    "lot_closed_at, exit_price, pnl) "
                    "SELECT ticker, lot_opened_at, %s, entry_price, NOW(), %s, %s "
                    "FROM position_lots WHERE id=%s",
                    (qty_closed, exit_price, pnl, lot_id),
                )
                remaining = 0
                logger.info(
                    "partial_close_lot: PARTIAL closed lot_id=%s ticker=%s "
                    "qty_closed=%s pnl=%.2f remaining_in_lot=%s",
                    lot_id, ticker, qty_closed, pnl, lot_qty - qty_closed,
                )
    db_conn.commit()

    if remaining > 0:
        logger.warning(
            "partial_close_lot: shares_to_close=%s but only closed %s for ticker=%s "
            "— open lots exhausted",
            shares_to_close, shares_to_close - remaining, ticker,
        )
```

---

### `scripts/run_ingestion.py` — add periodic reconciliation call

**Analog:** `scripts/run_ingestion.py` (self; lines 83–104 — existing `run_cycle` function)

**Pattern:** Add periodic reconciliation call inside `run_cycle()`, after the session health check block. Follow the startup reconciliation pattern (lines 131–136): open a fresh connection, call the method, close connection, catch all exceptions:

```python
# In run_cycle(), append AFTER the existing session health-check block:
if broker_module.ibapp is not None and broker_module.ibapp.is_connected():
    try:
        _db_conn = _get_db_connection()
        broker_module.ibapp.run_periodic_reconciliation(_db_conn)
        _db_conn.close()
    except Exception:
        logger.exception("Periodic reconciliation failed — continuing")
```

**Startup: set `_db_conn` on IBApp for fill callbacks** — add in `main()` after `ibkr_ok` block, following the `_ibapp._account_name` check pattern (lines 141–158):

```python
# In main(), after startup reconciliation succeeds, add:
_ibapp._db_conn = _get_db_connection()
logger.info("IBApp._db_conn set for fill callbacks (Phase 5)")
# Note: this connection is owned by the api thread (execDetails/orderStatus callbacks)
# and must NOT be shared with the main thread or executor.
```

**`_get_db_connection` helper** is already defined at lines 62–73 and reused as-is.

---

### `infra/migrate_phase5.sql` — new migration script

**Analog:** `infra/migrate_phase4.sql` (exact match — same structure)

**Pattern** (migrate_phase4.sql lines 1–19): header comment with run command, `CREATE TABLE IF NOT EXISTS` or `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, `COMMENT ON TABLE`, `GRANT` lines.

```sql
-- Phase 5: Fill Capture and Position Reconciliation — schema migration
-- Verifies Phase 5 columns exist; safe to run even if already present (IF NOT EXISTS)
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase5.sql

-- orders table: fill capture columns (already in schema.sql; add IF NOT EXISTS as guard)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fill_price NUMERIC(10,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;

-- executions table: exec_id UNIQUE already in schema.sql
-- No new columns needed for executions in Phase 5.

-- No new tables for Phase 5 — all tables (position_lots, executions) already in schema.sql.

COMMENT ON COLUMN orders.fill_price IS 'Avg fill price from IBKR orderStatus Filled callback (Phase 5)';
COMMENT ON COLUMN orders.filled_at IS 'Timestamp when order reached FILLED state (Phase 5)';
```

---

### `tests/test_positions.py` — new Wave 0 test stubs

**Analog:** `tests/test_execution.py` (exact match — same Wave 0 stub pattern)

**Wave 0 stub pattern** (test_execution.py lines 1–10, 20–35): module docstring identifying which plan unskips each test; `import pytest`; top-level `def test_*()` with `@pytest.mark.skip` removed when implementing (NOTE: Wave 0 stubs in this project have test bodies written in full — they are NOT skipped — they just do not exist yet).

**Mock DB pattern** (test_execution.py lines 48–57): `mock_conn = MagicMock()`, `mock_cur = MagicMock()`, `mock_conn.cursor.return_value.__enter__.return_value = mock_cur`, then `mock_cur.fetchone.side_effect = [...]` to control query results.

**Integration test pattern** (test_execution.py lines 122–147): uses `db_connection` fixture from conftest.py; inserts test data with `os.urandom(4).hex()` in unique fields; asserts DB state after the call under test.

```python
# tests/test_positions.py header pattern (from test_execution.py lines 1–18):
"""
tests/test_positions.py — Phase 5 Fill Capture + Position Lot unit and integration tests.

All tests are Wave 0 stubs (skipped). Each test body is the full intended
implementation. Tests are unskipped as their implementing plan lands:
  - 05-01: test_exec_details_*, test_order_status_*
  - 05-02: test_open_lot_*, test_fifo_*, test_close_lot_*
  - 05-03: test_periodic_reconciliation_*
"""
import os
from unittest.mock import MagicMock, patch
import pytest
```

**Unit test with mock DB** (copy from test_execution.py lines 48–60):
```python
def test_exec_details_writes_execution_row():
    """EXEC-05: execDetails writes one executions row with correct exec_id, shares, price."""
    from bravos.broker import connection
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = (42, "BUY")  # (db_order_id, action)
    # ... build mock execution + contract, call _handle_exec_details, assert cur.execute calls
```

**Integration test with real DB** (copy from test_execution.py lines 122–147):
```python
def test_open_lot_writes_row(db_connection):
    """POS-01: open_lot() inserts a row in position_lots with correct ticker/qty/price."""
    from bravos.execution.positions import open_lot
    open_lot("AAPL", 10, 150.00, db_connection)
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT quantity, entry_price FROM position_lots "
            "WHERE ticker='AAPL' AND lot_closed_at IS NULL "
            "ORDER BY lot_opened_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 10
    assert float(row[1]) == 150.00
    # Cleanup
    db_connection.rollback()
```

---

## Shared Patterns

### DB Write Pattern
**Source:** `bravos/execution/executor.py` lines 269–281 (`_submit_order`) and `bravos/broker/connection.py` lines 577–584 (`_write_position_snapshot`)
**Apply to:** All new DB-writing functions in `positions.py` and `connection.py`

```python
# Standard psycopg2 pattern used throughout the project:
with db_conn.cursor() as cur:
    cur.execute(
        "SQL STATEMENT WITH %s PLACEHOLDERS",
        (param1, param2),
    )
db_conn.commit()
```

Key rules:
- Always `%s` placeholders — never f-strings in SQL (SQL injection guard)
- `with db_conn.cursor() as cur:` — cursor is a context manager; auto-closes on exit
- Commit after each logical write unit, not inside the cursor block
- `db_conn.cursor()` is NOT reused across separate SELECT/INSERT/UPDATE operations — open a new `with` block per query group

### Threading Event Sync Pattern
**Source:** `bravos/broker/connection.py` lines 86–89 (`_positions_done`, `_orders_done`, `_summary_done`) and lines 543–564 (`run_startup_reconciliation`)
**Apply to:** `run_periodic_reconciliation` on IBApp

```python
# Set at __init__:
self._positions_done = threading.Event()

# In the request method:
self._positions.clear()
self._positions_done.clear()
self.reqPositions()                    # fires callbacks on api thread
got = self._positions_done.wait(timeout)  # blocks caller until positionEnd() fires
if not got:
    logger.warning("reqPositions timed out — using partial data")
```

### Module-Level Helper Pattern
**Source:** `bravos/broker/connection.py` lines 567–627 (`_write_position_snapshot`, `_reconcile_against_db`)
**Apply to:** All new helper functions in `connection.py` (`_handle_exec_details`, `_update_order_filled`, `_update_order_partial`)

Functions are defined at module scope (not as IBApp methods) so they can be tested independently without instantiating IBApp. IBApp methods call them, passing `self._db_conn`.

### Deferred Import to Avoid Circular Dependency
**Source:** `bravos/execution/executor.py` line 77
**Apply to:** `_handle_exec_details` in `connection.py` when importing `positions`

```python
# Inside the function body, NOT at module top:
from bravos.execution import positions
```

### Logger Pattern
**Source:** `bravos/broker/connection.py` line 22; `bravos/execution/executor.py` line 32
**Apply to:** `bravos/execution/positions.py`

```python
logger = logging.getLogger(__name__)
```

Use `logger.info(...)` for normal fill events (not DEBUG — fills are significant events).
Use `logger.warning(...)` for unexpected conditions (shares_to_close > open quantity).
Use `logger.error(...)` for data integrity problems (no order found for ibkr_order_id).

### Run Ingestion: Try/Except Around IBKR Calls
**Source:** `scripts/run_ingestion.py` lines 131–136 (startup reconciliation block)
**Apply to:** Periodic reconciliation call in `run_cycle()`

```python
try:
    _db_conn = _get_db_connection()
    broker_module.ibapp.run_periodic_reconciliation(_db_conn)
    _db_conn.close()
except Exception:
    logger.exception("Periodic reconciliation failed — continuing")
```

Exceptions are caught broadly (not re-raised) — the daemon must keep running even if reconciliation fails.

---

## No Analog Found

All Phase 5 files have close analogs in the codebase. No files require falling back to RESEARCH.md patterns exclusively.

| File | Role | Data Flow | Note |
|------|------|-----------|------|
| — | — | — | All files covered |

---

## Metadata

**Analog search scope:** `bravos/broker/`, `bravos/execution/`, `scripts/`, `infra/`, `tests/`
**Files scanned:** 8 (connection.py, executor.py, conftest.py, schema.sql, test_execution.py, migrate_phase4.sql, run_ingestion.py, CLAUDE.md)
**Pattern extraction date:** 2026-05-15
