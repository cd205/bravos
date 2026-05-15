# Phase 5: Fill Capture and Position Reconciliation - Context

**Gathered:** 2026-05-15
**Status:** Ready for planning

<domain>
## Phase Boundary

The system correctly captures every fill (including partial fills), maintains accurate per-lot position state with FIFO assignment, and periodically reconciles internal state against IBKR's authoritative position data. Order submission, sizing, and risk controls are Phase 4 scope — this phase extends what happens after placeOrder() returns: fill callbacks, lot writes, and intra-day reconciliation.

</domain>

<decisions>
## Implementation Decisions

### Fill Capture Callbacks
- **D-01:** `execDetails` is the canonical callback for writing execution records. It fires once per fill event with `exec_id`, `shares`, `price`, and `commission` — maps directly to the `executions` table schema. Phase 4's `orderStatus` callback continues handling SUBMITTED/REJECTED routing; Phase 5 extends it to flip `orders.status` to `FILLED` or `PARTIAL` based on `filled` vs `totalQuantity`, but `execDetails` owns the per-execution row write.
- **D-02:** An order is marked `FILLED` only when `orderStatus.filled == orderStatus.totalQuantity`. Intermediate fills update `orders.status` to `PARTIAL` and update position state incrementally (EXEC-06). `execDetails` and `orderStatus` fire independently; each handles its own concern.

### Position Lot Writes
- **D-03:** New module: `bravos/execution/positions.py`. Exposes `open_lot()`, `close_lot()`, `partial_close_lot()` functions. The `execDetails` callback in IBApp calls into this module after writing the `executions` row. Keeps IBApp callbacks thin and position-management logic independently testable.
- **D-04:** `execDetails` callback is added to `IBApp` in `bravos/broker/connection.py`. It: (1) writes one row to `executions`, (2) dispatches to `positions.py` based on the `action` field of the associated order (looked up from `orders` table by `ibkr_order_id`). `execDetailsEnd` is used to signal batch completion if needed, but each individual fill is processed immediately.

### FIFO Partial-Close Logic
- **D-05:** Lot-by-lot FIFO: close the oldest open lot completely before touching the next. Example — 20 shares to close from lots [10 oldest, 20 mid, 20 newest]: close lot 1 fully (10 shares, set `lot_closed_at`, `exit_price`, `pnl`), take 10 from lot 2 (UPDATE `quantity` to 10), lot 3 untouched.
- **D-06:** FIFO logic lives in `positions.py` (`partial_close_lot()` and `close_lot()`). Ordered by `lot_opened_at ASC` when fetching open lots. The `close` action type (full close) is a special case of FIFO that closes all remaining lots.
- **D-07:** Realized P&L per lot = `(exit_price - entry_price) × quantity_closed`. Stored in `position_lots.pnl`. Satisfies AUDIT-05.

### Periodic Reconciliation
- **D-08:** Reconciliation runs every 5 minutes, piggybacked on the scrape cycle in `scripts/run_ingestion.py`. After each scrape-and-execute pass, call `ibapp.run_periodic_reconciliation(db_conn)`.
- **D-09:** Mismatch handling: log as `WARNING`, flag for operator review — identical to startup reconciliation policy (Phase 3 D-08). System never auto-corrects `position_lots`. This is conservative for v1; auto-correction is deferred to v2.
- **D-10:** `run_periodic_reconciliation()` is a new method on `IBApp` (or a module-level function in `connection.py` following the Phase 3 pattern). It calls `reqPositions()`, waits for `positionEnd()`, then compares against `position_lots WHERE lot_closed_at IS NULL`. Reuses the existing `_write_position_snapshot` and `_reconcile_against_db` helpers from Phase 3 — no new reconciliation logic needed, just a periodic call site.

### Claude's Discretion
- Whether `execDetailsEnd` is used for anything beyond an INFO log (Phase 5 fills are processed per-event, not in batch)
- Whether `partial_close_lot` and `close_lot` are separate functions or one function with a `full_close` flag
- Threading model for `execDetails` DB writes (callback fires on the api thread — use the existing db_conn pattern from Phase 4, or a dedicated connection)
- How to pass `db_conn` to the `execDetails` callback (likely: store reference on IBApp at startup, same as executor's pattern)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### IBKR Fill Callbacks
- `.claude/skills/ibkr-connection/SKILL.md` — execDetails callback signature, execDetailsEnd, orderStatus filled/remaining/avgFillPrice, exec_id uniqueness, Execution object fields

### Existing connection layer (Phase 4 state)
- `bravos/broker/connection.py` — IBApp class; `orderStatus` callback (Phase 4 scope, Phase 5 extends); `_order_status_events` dict; `_write_position_snapshot` and `_reconcile_against_db` module-level helpers; startup reconciliation pattern to replicate for periodic reconciliation
- `bravos/execution/executor.py` — `_calculate_quantity()` already reads `position_lots WHERE lot_closed_at IS NULL` for close orders — Phase 5 lot writes must keep this consistent

### Database schema
- `infra/schema.sql` — `executions` table (`order_id`, `exec_id UNIQUE`, `shares`, `price`, `commission`, `exec_time`); `position_lots` table (`ticker`, `lot_opened_at`, `quantity`, `entry_price`, `lot_closed_at`, `exit_price`, `pnl`); `orders` table (`status`, `fill_price`, `filled_at`)
- `.claude/skills/postgres-patterns/SKILL.md` — psycopg2 patterns, connection handling

### Phase requirements
- `.planning/REQUIREMENTS.md` — EXEC-05 (fill price+qty from execDetails), EXEC-06 (partial fill accumulation), IBKR-04 (periodic reconciliation), POS-01 (open lots), POS-02 (closed positions with P&L), POS-03 (FIFO lot assignment)

### Audit requirements
- `.planning/REQUIREMENTS.md` — AUDIT-03 (execution links to order links to position lot change), AUDIT-04 (partial-close records lot reduced + remaining), AUDIT-05 (lot entry/exit/P&L per lot), AUDIT-06 (append-only, no deletes)

### Daemon entry point
- `scripts/run_ingestion.py` — where periodic reconciliation call is added (after each scrape-execute pass)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bravos/broker/connection.py`: `_write_position_snapshot()` and `_reconcile_against_db()` module-level helpers — call from the new periodic reconciliation method without rewriting
- `bravos/broker/connection.py`: `_order_status_events` dict pattern — replicate as `_exec_detail_events` if executor needs to wait for fill confirmation
- `bravos/broker/connection.py`: `_positions_done` threading.Event pattern — replicate for periodic reconciliation's `reqPositions()` wait
- `bravos/execution/executor.py`: `_calculate_quantity()` queries `position_lots WHERE lot_closed_at IS NULL` — Phase 5 lot writes must keep this consistent
- `tests/conftest.py`: `db_connection` fixture — reuse for positions.py integration tests

### Established Patterns
- Package structure: `bravos/<module>/__init__.py` + module files — `positions.py` goes inside `bravos/execution/`
- Wave 0 test stubs: full test bodies inside `@pytest.mark.skip(reason="implementing: <plan-name>")` — established in Phases 1–4
- DB write pattern: write row first, then perform IBKR action — mirror for executions + position_lots writes
- Module-level helper functions for DB operations (not IBApp methods) — established in Phase 3 for `_write_position_snapshot` / `_reconcile_against_db`

### Integration Points
- `bravos/broker/connection.py`: add `execDetails` and `execDetailsEnd` callbacks; extend `orderStatus` to handle `Filled`/`PartiallyFilled` statuses
- `bravos/execution/positions.py`: new module; called from `execDetails` callback
- `scripts/run_ingestion.py`: add `ibapp.run_periodic_reconciliation(db_conn)` call after each scrape-execute cycle
- `infra/`: may need a `migrate_phase5.sql` to add any missing columns (check if `orders.fill_price`, `orders.filled_at`, `executions.exec_id` exist — they are in schema.sql but verify they weren't missed in the initial migration)

</code_context>

<specifics>
## Specific Ideas

- `executions.exec_id` is marked UNIQUE in the schema — use this as idempotency key: `INSERT ... ON CONFLICT (exec_id) DO NOTHING` to prevent duplicate fill writes if the callback fires multiple times for the same execution.
- For `partial_close_lot()`: query `SELECT id, quantity FROM position_lots WHERE ticker=%s AND lot_closed_at IS NULL ORDER BY lot_opened_at ASC` — iterate and close oldest-first until `shares_to_close` is exhausted.
- For a full `close` action: same FIFO loop but close all lots; equivalent to calling `partial_close_lot()` with `shares_to_close = total_open_quantity`.
- The `execDetails` callback receives an `Execution` object and a `Contract` object. The `Execution.orderId` field maps to `ibkr_order_id` in the `orders` table — use this to look up `order_id` (PK) and `action` before writing the `executions` row and dispatching to positions.py.

</specifics>

<deferred>
## Deferred Ideas

- Auto-correction of position_lots from IBKR data on mismatch — conservative v1 choice; deferred to v2
- Commission capture for net P&L calculation (EXEC-V2-02) — v2 requirement
- Execution quality / slippage tracking (EXEC-V2-01) — v2
- Email notification when fills arrive (NOTF-V2-01) — Phase 7 / v2
- Account summary streaming for real-time P&L dashboard — Phase 7 concern

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 05-fill-capture-and-position-reconciliation*
*Context gathered: 2026-05-15*
