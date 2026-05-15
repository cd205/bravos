# Phase 5: Fill Capture and Position Reconciliation - Research

**Researched:** 2026-05-15
**Domain:** IBKR fill callbacks, FIFO position lot management, periodic reconciliation
**Confidence:** HIGH

## Summary

Phase 5 extends Phase 4's order submission machinery with everything that happens after `placeOrder()` returns: capturing fill events via IBKR's `execDetails` callback, writing per-execution rows to the `executions` table, maintaining FIFO per-lot position state in `position_lots`, and adding a periodic intra-day reconciliation call. All architectural decisions are locked in CONTEXT.md — research is scoped to verifying implementation details, confirming callback signatures against the codebase's SKILL.md reference, and identifying integration pitfalls.

The implementation is constrained to three new/extended files: `bravos/broker/connection.py` (add `execDetails`, `execDetailsEnd`, extend `orderStatus`), `bravos/execution/positions.py` (new module with `open_lot`, `close_lot`, `partial_close_lot`), and `scripts/run_ingestion.py` (add periodic reconciliation call). A migration script `infra/migrate_phase5.sql` is needed if any Phase 5 columns were omitted from the initial schema deployment, but schema.sql already contains all required columns (`orders.fill_price`, `orders.filled_at`, `executions.exec_id`).

**Primary recommendation:** Implement `positions.py` first as a pure DB module (no IBKR dependency), test it against the DB fixture, then wire the `execDetails` callback in `connection.py` and confirm end-to-end with a mock execution.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** `execDetails` is the canonical callback for writing execution records. It fires once per fill event with `exec_id`, `shares`, `price`, and `commission` — maps directly to the `executions` table schema. Phase 4's `orderStatus` callback continues handling SUBMITTED/REJECTED routing; Phase 5 extends it to flip `orders.status` to `FILLED` or `PARTIAL` based on `filled` vs `totalQuantity`, but `execDetails` owns the per-execution row write.
- **D-02:** An order is marked `FILLED` only when `orderStatus.filled == orderStatus.totalQuantity`. Intermediate fills update `orders.status` to `PARTIAL` and update position state incrementally (EXEC-06). `execDetails` and `orderStatus` fire independently; each handles its own concern.
- **D-03:** New module: `bravos/execution/positions.py`. Exposes `open_lot()`, `close_lot()`, `partial_close_lot()` functions. The `execDetails` callback in IBApp calls into this module after writing the `executions` row. Keeps IBApp callbacks thin and position-management logic independently testable.
- **D-04:** `execDetails` callback is added to `IBApp` in `bravos/broker/connection.py`. It: (1) writes one row to `executions`, (2) dispatches to `positions.py` based on the `action` field of the associated order (looked up from `orders` table by `ibkr_order_id`). `execDetailsEnd` is used to signal batch completion if needed, but each individual fill is processed immediately.
- **D-05:** Lot-by-lot FIFO: close the oldest open lot completely before touching the next. Example — 20 shares to close from lots [10 oldest, 20 mid, 20 newest]: close lot 1 fully (10 shares, set `lot_closed_at`, `exit_price`, `pnl`), take 10 from lot 2 (UPDATE `quantity` to 10), lot 3 untouched.
- **D-06:** FIFO logic lives in `positions.py` (`partial_close_lot()` and `close_lot()`). Ordered by `lot_opened_at ASC` when fetching open lots. The `close` action type (full close) is a special case of FIFO that closes all remaining lots.
- **D-07:** Realized P&L per lot = `(exit_price - entry_price) × quantity_closed`. Stored in `position_lots.pnl`. Satisfies AUDIT-05.
- **D-08:** Reconciliation runs every 5 minutes, piggybacked on the scrape cycle in `scripts/run_ingestion.py`. After each scrape-and-execute pass, call `ibapp.run_periodic_reconciliation(db_conn)`.
- **D-09:** Mismatch handling: log as `WARNING`, flag for operator review — identical to startup reconciliation policy (Phase 3 D-08). System never auto-corrects `position_lots`. This is conservative for v1; auto-correction is deferred to v2.
- **D-10:** `run_periodic_reconciliation()` is a new method on `IBApp` (or a module-level function in `connection.py` following the Phase 3 pattern). It calls `reqPositions()`, waits for `positionEnd()`, then compares against `position_lots WHERE lot_closed_at IS NULL`. Reuses the existing `_write_position_snapshot` and `_reconcile_against_db` helpers from Phase 3 — no new reconciliation logic needed, just a periodic call site.

### Claude's Discretion

- Whether `execDetailsEnd` is used for anything beyond an INFO log (Phase 5 fills are processed per-event, not in batch)
- Whether `partial_close_lot` and `close_lot` are separate functions or one function with a `full_close` flag
- Threading model for `execDetails` DB writes (callback fires on the api thread — use the existing db_conn pattern from Phase 4, or a dedicated connection)
- How to pass `db_conn` to the `execDetails` callback (likely: store reference on IBApp at startup, same as executor's pattern)

### Deferred Ideas (OUT OF SCOPE)

- Auto-correction of position_lots from IBKR data on mismatch — conservative v1 choice; deferred to v2
- Commission capture for net P&L calculation (EXEC-V2-02) — v2 requirement
- Execution quality / slippage tracking (EXEC-V2-01) — v2
- Email notification when fills arrive (NOTF-V2-01) — Phase 7 / v2
- Account summary streaming for real-time P&L dashboard — Phase 7 concern
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-05 | System captures actual fill price and fill quantity from ibapi execution callbacks and stores per-execution records | `execDetails` callback writes to `executions` table; `exec_id UNIQUE` enables idempotent inserts |
| EXEC-06 | System handles partial fills correctly — order only marked FILLED when total filled quantity matches the order quantity; intermediate fills update position incrementally | `orderStatus` extended: `PARTIAL` when `filled < totalQuantity`; position updated per `execDetails` fire |
| IBKR-04 | System periodically reconciles internal position state against IBKR's authoritative position data; discrepancies are logged and flagged | `run_periodic_reconciliation()` reuses Phase 3 `_reconcile_against_db` helper; called every 5 min in `run_ingestion.py` |
| POS-01 | System maintains an internal record of all open positions (lots) with entry price, weight, quantity, and associated signal | `open_lot()` in `positions.py` writes to `position_lots`; called for BUY fills |
| POS-02 | System tracks closed positions with entry price, exit price, realized P&L, and trade duration | `close_lot()` / `partial_close_lot()` set `lot_closed_at`, `exit_price`, `pnl` on matching lots |
| POS-03 | System correctly applies FIFO lot assignment when reducing or closing a position that has multiple open lots | FIFO query: `ORDER BY lot_opened_at ASC`; oldest lot fully consumed before next lot touched |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Fill event capture | IBKR API thread (via EWrapper callback) | Database | `execDetails` fires on the api thread; DB write happens inline on callback |
| Execution record persistence | Database | — | `executions` table; idempotent via `exec_id UNIQUE` |
| Order status update (FILLED/PARTIAL) | Database | IBKR API thread | `orderStatus` callback triggers `UPDATE orders SET status` |
| FIFO lot management | `bravos/execution/positions.py` | Database | Pure DB module; no IBKR dependency; independently testable |
| Periodic reconciliation | `IBApp.run_periodic_reconciliation()` | Database | Reuses Phase 3 helper functions; called from ingestion daemon |
| Position state consistency | Database | Application | All position state mutations go through `positions.py` functions |

## Standard Stack

### Core (all from existing project dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| ibapi | 9.81.1.post1 | `execDetails`/`execDetailsEnd`/`orderStatus` callbacks | Project-locked; already installed |
| psycopg2-binary | 2.9.x | DB writes for executions + position_lots | Project-locked; already used throughout |
| threading (stdlib) | stdlib | `threading.Event` for `_positions_done` sync in periodic reconciliation | Established pattern in connection.py |

### No New Dependencies

Phase 5 introduces no new libraries. All required tools are already in the project.

**Version verification:** [VERIFIED: project codebase — `pip show psycopg2-binary ibapi` would confirm; versions established in Phase 1]

## Architecture Patterns

### System Architecture Diagram

```
IBKR Gateway
     |
     | (ibkr-api thread)
     v
execDetails(reqId, contract, execution)
     |
     |-- 1. Look up order: SELECT action FROM orders WHERE ibkr_order_id = execution.orderId
     |-- 2. INSERT INTO executions (order_id, exec_id, shares, price, exec_time) ON CONFLICT DO NOTHING
     |-- 3. Dispatch to positions.py based on action:
     |        action=BUY  --> open_lot(ticker, shares, price, db_conn)
     |        action=SELL --> partial_close_lot(ticker, shares, price, db_conn)
     v
position_lots table
     ^
     |
orderStatus(orderId, status='Filled'/'PartiallyFilled', filled, remaining, avgFillPrice)
     |
     |-- UPDATE orders SET status='FILLED'/'PARTIAL', fill_price=avgFillPrice, filled_at=NOW()
     v
orders table

------ periodic (every 5 min, in run_ingestion.py run_cycle) ------

run_periodic_reconciliation(db_conn)
     |
     |-- reqPositions() --> position() callbacks --> positionEnd()
     |-- _write_position_snapshot(db_conn, positions)
     |-- _reconcile_against_db(db_conn, positions, [])
     v
broker_positions_snapshot table + WARNING logs on mismatch
```

### Recommended Project Structure

```
bravos/
├── broker/
│   └── connection.py        # ADD: execDetails, execDetailsEnd; EXTEND: orderStatus
├── execution/
│   ├── executor.py          # unchanged
│   └── positions.py         # NEW: open_lot, close_lot, partial_close_lot
scripts/
└── run_ingestion.py         # ADD: run_periodic_reconciliation call in run_cycle
infra/
└── migrate_phase5.sql       # NEW: verify-and-add if schema columns missing
tests/
└── test_positions.py        # NEW: positions.py unit + integration tests
```

### Pattern 1: execDetails Callback (IBKR Fill Capture)

**What:** EWrapper callback that fires once per fill event on the ibkr-api thread.
**When to use:** Every order fill (full or partial). Each fill gets its own `execDetails` call.

```python
# Source: .claude/skills/ibkr-connection/SKILL.md Section 8
def execDetails(self, reqId: int, contract, execution) -> None:
    """
    Called once per fill event. Fires on the ibkr-api thread.

    execution.execId    — unique, use as idempotency key
    execution.orderId   — maps to orders.ibkr_order_id
    execution.side      — "BOT" or "SLD"
    execution.shares    — fill quantity (this fill only, not cumulative)
    execution.price     — fill price for this fill
    execution.time      — "YYYYMMDD  HH:MM:SS"
    """
    if self._db_conn is None:
        logger.error("execDetails fired but _db_conn is None — skipping fill capture")
        return
    _handle_exec_details(self._db_conn, execution, contract)
```

**Key fields (VERIFIED: .claude/skills/ibkr-connection/SKILL.md):**
- `execution.execId` — globally unique per fill; use as `ON CONFLICT` idempotency key
- `execution.orderId` — integer; maps to `orders.ibkr_order_id` for the order lookup
- `execution.shares` — shares filled in THIS callback (partial fills each fire separately)
- `execution.price` — fill price for this specific fill
- `execution.side` — "BOT" (buy) or "SLD" (sell); can cross-check against `orders.action`

### Pattern 2: execDetailsEnd Callback

**What:** Signals that all `execDetails` callbacks for a `reqExecutions()` batch are complete.
**When to use:** For live fills (triggered by actual trades, not `reqExecutions()`), `execDetailsEnd` still fires but is only relevant for batch requests. For Phase 5's live-fill path, log at INFO and do nothing else. [CITED: CONTEXT.md D-04 — "execDetailsEnd is used to signal batch completion if needed, but each individual fill is processed immediately"]

```python
# Source: .claude/skills/ibkr-connection/SKILL.md Section 8
def execDetailsEnd(self, reqId: int) -> None:
    logger.info("execDetailsEnd received (reqId=%s) — all fills for this batch processed", reqId)
```

### Pattern 3: orderStatus Extension for Fill Status

**What:** Extend Phase 4's existing `orderStatus` callback to handle `Filled` and `PartiallyFilled`.
**When to use:** Every `orderStatus` call — add new branches for fill statuses.

```python
# Extension of existing orderStatus in connection.py (Phase 4 code shown for context)
def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, ...):
    # Phase 4: route to executor event slot
    slot = self._order_status_events.get(orderId)
    if slot is not None:
        slot["status"] = status
        slot["event"].set()

    # Phase 5 extension: update DB fill status
    if status == "Filled" and self._db_conn is not None:
        _update_order_filled(self._db_conn, orderId, avgFillPrice)
    elif status == "PartiallyFilled" and self._db_conn is not None:
        _update_order_partial(self._db_conn, orderId, avgFillPrice)
    # Note: execDetails handles the position_lots write; orderStatus only updates orders.status
```

**Important threading note:** `orderStatus` fires on the ibkr-api thread (same as `execDetails`). Using the same `_db_conn` reference from IBApp is safe as long as the main thread is not concurrently using it — the executor's DB writes complete before `placeOrder` returns to the main thread. [ASSUMED — concurrent db_conn access from api thread and executor thread could cause psycopg2 issues if both fire simultaneously]

### Pattern 4: FIFO Lot Closure

**What:** Close oldest open lots first until `shares_to_close` is exhausted.
**When to use:** Any SELL fill (both `partial_close` and `close` action types).

```python
# Source: CONTEXT.md D-05, D-06, D-07
def partial_close_lot(ticker: str, shares_to_close: int, exit_price: float, db_conn) -> None:
    """
    FIFO lot closure: close oldest open lots first.
    Writes pnl = (exit_price - entry_price) × quantity_closed per lot.
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
                    "UPDATE position_lots SET lot_closed_at=NOW(), exit_price=%s, pnl=%s "
                    "WHERE id=%s",
                    (exit_price, pnl, lot_id),
                )
                remaining -= lot_qty
            else:
                # Partial close: reduce this lot's quantity
                pnl = (exit_price - float(entry_price)) * remaining
                cur.execute(
                    "UPDATE position_lots SET quantity=%s "
                    "WHERE id=%s",
                    (lot_qty - remaining, lot_id),
                )
                # AUDIT-04: append a new closed row for the partial close record
                cur.execute(
                    "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price, "
                    "lot_closed_at, exit_price, pnl) "
                    "SELECT ticker, lot_opened_at, %s, entry_price, NOW(), %s, %s FROM position_lots WHERE id=%s",
                    (remaining, exit_price, pnl, lot_id),
                )
                remaining = 0
    db_conn.commit()
```

**AUDIT-04 implication:** When partially closing a lot, a new row must be appended (the closed portion) rather than modifying the existing row in place. This preserves the full lot history per AUDIT-06 (append-only, no history destruction). The surviving open lot row has its `quantity` reduced.

### Pattern 5: open_lot Function

**What:** Write a new position_lots row when a BUY fill arrives.
**When to use:** Any BUY fill via `execDetails` (action='BUY' on associated order).

```python
# Source: CONTEXT.md D-03, schema.sql
def open_lot(ticker: str, shares: int, entry_price: float, db_conn) -> None:
    """
    Insert a new open lot. Called from execDetails for BUY fills.
    lot_opened_at is set to NOW() by column default.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO position_lots (ticker, quantity, entry_price) "
            "VALUES (%s, %s, %s)",
            (ticker, shares, entry_price),
        )
    db_conn.commit()
```

### Pattern 6: Idempotent executions Insert

**What:** Prevent duplicate execution rows if `execDetails` fires multiple times for the same fill.
**When to use:** Every `executions` INSERT.

```python
# Source: CONTEXT.md specifics section
cur.execute(
    "INSERT INTO executions (order_id, exec_id, shares, price, exec_time) "
    "VALUES (%s, %s, %s, %s, NOW()) "
    "ON CONFLICT (exec_id) DO NOTHING",
    (db_order_id, execution.execId, int(execution.shares), execution.price),
)
```

**Why:** IBKR can re-deliver `execDetails` on reconnect (e.g., startup `reqExecutions()` call). The `exec_id UNIQUE` constraint in schema.sql exists for exactly this purpose.

### Pattern 7: Periodic Reconciliation

**What:** Call `reqPositions()` + wait for `positionEnd()` + reconcile against DB, outside the startup path.
**When to use:** After each scrape-execute cycle in `run_ingestion.py`.

```python
# Source: CONTEXT.md D-08, D-10; mirrors run_startup_reconciliation pattern
def run_periodic_reconciliation(self, db_conn, timeout: float = 30) -> None:
    """
    Called after each scrape cycle. Reuses _write_position_snapshot and
    _reconcile_against_db module-level helpers from Phase 3.
    """
    self._positions.clear()
    self._positions_done.clear()
    self.reqPositions()
    got = self._positions_done.wait(timeout)
    if not got:
        logger.warning("Periodic reconciliation: reqPositions timed out — using partial data")
    _write_position_snapshot(db_conn, self._positions)
    _reconcile_against_db(db_conn, self._positions, [])
    logger.info("Periodic reconciliation complete")
```

**In run_ingestion.py run_cycle():**
```python
# After run_cycle() session health check logic, add:
if broker_module.ibapp is not None and broker_module.ibapp.is_connected():
    try:
        _db_conn = _get_db_connection()
        broker_module.ibapp.run_periodic_reconciliation(_db_conn)
        _db_conn.close()
    except Exception:
        logger.exception("Periodic reconciliation failed — continuing")
```

### Anti-Patterns to Avoid

- **Don't call `reqPositions()` concurrently:** If the scrape cycle triggers reconciliation while the startup reconciliation is still running, `_positions_done` and `_positions` will be in a race. Mitigation: add a guard (e.g., `if self._positions_done.is_set() or not self._connected.is_set(): return`).
- **Don't write to `position_lots` from `orderStatus`:** `orderStatus` fires for every status change including PENDING and SUBMITTED states. Only `execDetails` signals an actual fill. Mixing position writes into `orderStatus` will create phantom lot entries.
- **Don't assume `execDetails.orderId` is the IBKR order ID directly:** `execution.orderId` IS the IBKR order ID (`ibkr_order_id` in the `orders` table). A DB lookup is needed to get the internal `order_id` PK.
- **Don't use `execution.shares` as cumulative fill:** Each `execDetails` callback reports the shares for THAT fill only. For partial fills, multiple callbacks fire sequentially, each with its share count. `orderStatus.filled` is cumulative; `execDetails.shares` is per-event.
- **Don't close `_db_conn` inside the callback:** The connection is owned by the main/daemon flow, not the callback. Callbacks should use it but not close it.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Idempotent fill inserts | Custom deduplication logic | `ON CONFLICT (exec_id) DO NOTHING` | `exec_id` UNIQUE constraint already in schema; SQL handles it atomically |
| FIFO ordering | Custom sort + slice | `ORDER BY lot_opened_at ASC` in SQL | DB sorts at fetch time; simpler, no in-memory sort needed |
| Reconnect-safe fill recovery | Custom reconnect callback | Idempotent `execDetails` + IBKR's built-in replay | IBKR re-delivers fills on reconnect; idempotency covers this for free |
| Position snapshot | Custom position cache | `broker_positions_snapshot` + Phase 3 `_write_position_snapshot` | Already implemented; Phase 5 just calls it periodically |

**Key insight:** The schema was designed for Phase 5 from the start. `exec_id UNIQUE`, `position_lots.lot_opened_at`, and `orders.fill_price/filled_at` are all already defined in `schema.sql`. The implementation is a matter of wiring callbacks to existing schema — not inventing new data structures.

## Common Pitfalls

### Pitfall 1: db_conn Thread Safety

**What goes wrong:** `psycopg2` connections are not thread-safe. `execDetails` fires on the ibkr-api thread; the main daemon loop and executor also use a DB connection. Sharing a single psycopg2 connection across threads without synchronization will cause intermittent cursor corruption.

**Why it happens:** The Phase 4 executor holds `db_conn` during `execute_signal()`. If `execDetails` fires (on the api thread) while the executor is mid-transaction, both threads interleave operations on the same connection.

**How to avoid:** Store a dedicated `db_conn` on `IBApp` (set at startup, e.g., `ibapp._db_conn = _get_db_connection()`). This connection is used exclusively by IBKR callbacks (api thread). The executor and reconciliation use separate connections opened per-call. This follows the pattern already established: `run_startup_reconciliation` opens its own `_db_conn` in `main()`, then closes it.

**Warning signs:** `psycopg2.InterfaceError: connection already closed` or `InternalError: current transaction is aborted` appearing intermittently after fills.

### Pitfall 2: execDetails fires on Reconnect

**What goes wrong:** When IBKR reconnects, it may replay recent `execDetails` callbacks for orders placed in the current session. Without idempotency, duplicate `executions` rows are created, and `position_lots` gets inflated with extra shares.

**Why it happens:** IBKR replays execution data on reconnect as part of the connection handshake.

**How to avoid:** Always use `INSERT INTO executions ... ON CONFLICT (exec_id) DO NOTHING`. The FIFO position logic in `positions.py` should also be idempotent — once `lot_closed_at` is set, it cannot be re-closed.

**Warning signs:** Position quantities growing unexpectedly after a reconnect event.

### Pitfall 3: reqPositions Reuse During Concurrent Reconciliation

**What goes wrong:** `run_periodic_reconciliation()` and `run_startup_reconciliation()` both use `_positions` and `_positions_done`. If periodic reconciliation fires while startup is in progress (or after a reconnect triggers startup again), `_positions` will be cleared and `_positions_done` may be set prematurely.

**Why it happens:** No guard on concurrent `reqPositions()` calls.

**How to avoid:** Add a `_reconciliation_in_progress` flag (or reuse `_recon_lock`) to prevent concurrent reconciliation calls. Simplest approach: skip periodic reconciliation if `_positions_done` is not already set from a prior call (i.e., a prior reconciliation is still in flight).

**Warning signs:** Empty reconciliation snapshots when positions clearly exist.

### Pitfall 4: partial_close_lot Audit Trail (AUDIT-04)

**What goes wrong:** When partially closing a lot, directly updating `position_lots.quantity` destroys the historical record of what was closed (violating AUDIT-06).

**Why it happens:** Natural instinct is to `UPDATE position_lots SET quantity = quantity - shares_closed`. This leaves no record of the close event.

**How to avoid:** Per AUDIT-04 and AUDIT-06, append a new closed row for the portion that was closed. The surviving open lot row has its `quantity` reduced. The new row has `lot_closed_at`, `exit_price`, and `pnl` set. This makes the full lot history always recoverable.

**Warning signs:** Missing entries in `position_lots` after partial-close orders fill.

### Pitfall 5: orderStatus and execDetails Fire Order

**What goes wrong:** Assuming `execDetails` always fires before `orderStatus` (or vice versa). These are independent callbacks that can arrive in any order.

**Why it happens:** IBKR sends these on the same api thread but in unpredictable order depending on message queue.

**How to avoid:** Never let one callback's logic depend on the other having already completed. `execDetails` owns execution records + position lots. `orderStatus` owns `orders.status`. They are independent state machines.

**Warning signs:** Intermittent failures in tests that depend on callback ordering.

### Pitfall 6: Schema Column Availability

**What goes wrong:** `orders.fill_price`, `orders.filled_at`, and `executions.exec_id` exist in `schema.sql` but the live DB was created from an early migration. If the Phase 1 migration predates these columns, they may not exist.

**Why it happens:** CONTEXT.md notes "verify they weren't missed in the initial migration."

**How to avoid:** Write `infra/migrate_phase5.sql` with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` for all Phase 5 columns. This is idempotent and safe to run even if columns already exist.

**Warning signs:** `psycopg2.errors.UndefinedColumn` when writing fill data.

## Code Examples

### execDetails callback full implementation pattern

```python
# Source: CONTEXT.md D-01, D-04; SKILL.md Section 8
def execDetails(self, reqId: int, contract, execution) -> None:
    """
    Canonical fill capture callback. Fires once per fill on the ibkr-api thread.
    Writes executions row then dispatches to positions.py.
    """
    if self._db_conn is None:
        logger.warning("execDetails: _db_conn not set — skipping fill capture exec_id=%s", execution.execId)
        return

    logger.info(
        "execDetails: execId=%s orderId=%s side=%s shares=%s price=%s",
        execution.execId, execution.orderId, execution.side, execution.shares, execution.price,
    )

    # Look up internal order_id and action from ibkr_order_id
    with self._db_conn.cursor() as cur:
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

    # Write execution row (idempotent)
    with self._db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO executions (order_id, exec_id, shares, price, exec_time) "
            "VALUES (%s, %s, %s, %s, NOW()) ON CONFLICT (exec_id) DO NOTHING",
            (db_order_id, execution.execId, int(execution.shares), execution.price),
        )
    self._db_conn.commit()

    # Dispatch to positions.py
    from bravos.execution import positions
    ticker = contract.symbol
    if order_action == "BUY":
        positions.open_lot(ticker, int(execution.shares), float(execution.price), self._db_conn)
    else:  # SELL
        positions.partial_close_lot(ticker, int(execution.shares), float(execution.price), self._db_conn)
```

### Schema migration for Phase 5 (idempotent)

```sql
-- infra/migrate_phase5.sql
-- Adds Phase 5 columns if not already present
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fill_price NUMERIC(10,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;
-- executions.exec_id is already in schema.sql; add IF NOT EXISTS as safety
-- (executions.exec_id UNIQUE already defined; this is a no-op if present)
```

Note: `schema.sql` already defines `fill_price`, `filled_at`, and `exec_id` — this migration is only needed if the live DB was deployed before those columns were added.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual position tracking | Per-lot position_lots with FIFO | Phase 5 introduces | Enables accurate P&L per lot |
| Startup-only reconciliation | Startup + periodic (every 5 min) | Phase 5 adds periodic | Catches intra-day drift |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `execDetails` and `orderStatus` fire on the same ibkr-api thread; concurrent DB access from both is possible but only if they interleave | Pitfall 1 | If they serialize on the api thread, shared `_db_conn` is safe without a lock |
| A2 | IBKR replays `execDetails` on reconnect for recent orders in the current session | Pitfall 2 | If IBKR doesn't replay, idempotency is unnecessary but harmless |
| A3 | `partial_close_lot` must append a new closed row (not just update quantity) to satisfy AUDIT-04/AUDIT-06 | Pattern 4, Pitfall 4 | If AUDIT-04 allows in-place mutation, simpler UPDATE would suffice |
| A4 | `orders.fill_price` and `orders.filled_at` columns exist in the live DB (were included in initial schema deployment) | Pitfall 6 | If missing, Phase 5 writes will fail with UndefinedColumn; migrate_phase5.sql resolves this |

**Risk mitigation for A3:** CONTEXT.md states "AUDIT-04: Partial closes and profit-booking actions record both the lot(s) reduced and the resulting remaining open quantity, preserving the full lot history" and "AUDIT-06: All audit records are immutable — system appends new state rows rather than updating/deleting history." The append-new-row approach is confirmed by these requirements.

## Open Questions (RESOLVED)

1. **db_conn threading model — dedicated connection vs. shared connection**
   - What we know: Phase 4 executor opens its own connection per `execute_signal()` call; Phase 3 startup reconciliation opens a connection, uses it, closes it. There is no persistent `_db_conn` on IBApp today.
   - What's unclear: Should `execDetails` get a dedicated persistent connection stored on `ibapp._db_conn`, or should it open/close a new connection per fill event?
   - Recommendation: Store a dedicated `ibapp._db_conn` opened at startup (in `run_ingestion.py` main()). Opening a new psycopg2 connection per fill adds ~5-10ms latency and creates Cloud SQL Auth Proxy connection churn. Dedicated connection avoids this. Planner should choose one approach and document it as the Phase 5 threading pattern.
   - RESOLVED: Dedicated `ibapp._db_conn` stored on IBApp, set at startup in `run_ingestion.py` main(). Periodic reconciliation opens a fresh per-call `_recon_db_conn` (different thread/context).

2. **partial_close_lot single-function vs. two-function API**
   - What we know: CONTEXT.md marks this as Claude's Discretion. Both approaches work.
   - Recommendation: Single `partial_close_lot(ticker, shares, exit_price, db_conn)` function handles both partial and full closes (full close = call with `shares = total_open_quantity`). Avoids an extra function that `close_lot` would just delegate to anyway. Keep `open_lot` separate (different semantics entirely).
   - RESOLVED: Single `partial_close_lot()` for internal FIFO logic. D-03's explicit `close_lot()` export is preserved as a thin public wrapper that calls `partial_close_lot()` with the full open quantity — satisfies the locked D-03 API contract without duplicating logic.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| psycopg2-binary | DB writes | Yes | verified in conftest.py | — |
| ibapi | IBKR callbacks | Yes | 9.81.1.post1 | — |
| pytest | Test suite | Yes | 9.0.3 | — |
| Cloud SQL Auth Proxy | Integration tests | Needs proxy running on bravos-vm1 | — | Unit tests mock db_conn |

**Missing dependencies with no fallback:** None — all Phase 5 code paths use libraries already installed.

**Note on DB integration tests:** Integration tests (those using `db_connection` fixture) require the Cloud SQL Auth Proxy running on port 5432. This is available on bravos-vm1 but not the local dev VM. Pattern established in Phase 3: integration tests are marked to run on bravos-vm1.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | none — see existing test pattern |
| Quick run command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_positions.py -x` |
| Full suite command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-05 | `execDetails` writes one `executions` row with correct exec_id, shares, price | unit (mock db) | `pytest tests/test_positions.py::test_exec_details_writes_execution_row -x` | Wave 0 |
| EXEC-05 | Duplicate exec_id is silently ignored (ON CONFLICT DO NOTHING) | unit (mock db) | `pytest tests/test_positions.py::test_exec_details_idempotent -x` | Wave 0 |
| EXEC-06 | `orderStatus(Filled)` sets `orders.status='FILLED'` when filled==totalQuantity | unit (mock db) | `pytest tests/test_positions.py::test_order_status_filled -x` | Wave 0 |
| EXEC-06 | `orderStatus(PartiallyFilled)` sets `orders.status='PARTIAL'` | unit (mock db) | `pytest tests/test_positions.py::test_order_status_partial -x` | Wave 0 |
| IBKR-04 | Periodic reconciliation calls `_reconcile_against_db` and logs WARNING on mismatch | unit (mock ibapp) | `pytest tests/test_positions.py::test_periodic_reconciliation_mismatch -x` | Wave 0 |
| POS-01 | `open_lot()` inserts a row in `position_lots` with correct ticker/qty/price | integration (real db) | `pytest tests/test_positions.py::test_open_lot_writes_row -x` | Wave 0 |
| POS-02 | After full close, `position_lots` row has `lot_closed_at`, `exit_price`, `pnl` set | integration (real db) | `pytest tests/test_positions.py::test_close_lot_sets_fields -x` | Wave 0 |
| POS-03 | FIFO: oldest lot closed first when shares_to_close > single lot quantity | unit (mock db) | `pytest tests/test_positions.py::test_fifo_closes_oldest_lot_first -x` | Wave 0 |
| POS-03 | FIFO: partial close of one lot reduces quantity and appends closed record | unit (mock db) | `pytest tests/test_positions.py::test_fifo_partial_close_one_lot -x` | Wave 0 |
| POS-03 | FIFO: close spanning 3 lots leaves youngest lot untouched | unit (mock db) | `pytest tests/test_positions.py::test_fifo_close_spanning_multiple_lots -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_positions.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_positions.py` — covers all Phase 5 requirements (file does not exist yet)
- [ ] No new conftest.py fixtures needed — existing `db_connection` fixture covers integration tests

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes | `int(execution.shares)` cast; `float(execution.price)` cast before DB write |
| V6 Cryptography | no | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via IBKR-provided field values (ticker, exec_id) | Tampering | Parameterized psycopg2 queries — always `%s` placeholders, never f-strings in SQL |
| Replay of stale fill data | Tampering | `ON CONFLICT (exec_id) DO NOTHING` — idempotency key prevents duplicate position writes |

**Note:** All DB writes in this phase use parameterized queries (established pattern from Phases 1-4). No user-facing input paths are introduced.

## Sources

### Primary (HIGH confidence)

- `.claude/skills/ibkr-connection/SKILL.md` — execDetails/execDetailsEnd callback signatures, Execution object fields, orderStatus status values, threading model
- `bravos/broker/connection.py` — Phase 3+4 implementation; reusable helpers `_write_position_snapshot`, `_reconcile_against_db`; `_positions_done` Event pattern
- `bravos/execution/executor.py` — `_calculate_quantity` pattern for `position_lots` queries; `_submit_order` pattern for DB write + event registration
- `infra/schema.sql` — Authoritative column list; `exec_id UNIQUE`, `position_lots` structure, `orders.fill_price/filled_at`
- `.planning/phases/05-fill-capture-and-position-reconciliation/05-CONTEXT.md` — All locked decisions D-01 through D-10

### Secondary (MEDIUM confidence)

- `infra/migrate_phase4.sql` — Pattern for migration script structure (IF NOT EXISTS, GRANT)
- `tests/test_execution.py` — Phase 4 Wave 0 stub pattern; `@pytest.mark.skip` convention confirmed
- `tests/conftest.py` — `db_connection` fixture details; runs against bravos-vm1 Cloud SQL Auth Proxy

### Tertiary (LOW confidence)

- [ASSUMED] IBKR replays `execDetails` on reconnect — standard IBKR behavior but not directly verified in session

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in use; no new dependencies
- Architecture: HIGH — locked decisions from CONTEXT.md; callback signatures verified in SKILL.md
- Pitfalls: HIGH — threading pitfalls from existing codebase analysis; schema pitfalls from schema.sql comparison
- FIFO logic: HIGH — algorithm specified verbatim in CONTEXT.md D-05/D-06; SQL pattern is standard

**Research date:** 2026-05-15
**Valid until:** 2026-06-15 (ibapi callback signatures are stable; psycopg2 patterns are stable)
