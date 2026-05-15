# Phase 5: Fill Capture and Position Reconciliation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the discussion.

**Date:** 2026-05-15
**Phase:** 05-fill-capture-and-position-reconciliation
**Mode:** discuss (default)
**Areas discussed:** Fill Callbacks, Position Lot Writes, FIFO Logic, Periodic Reconciliation

---

## Area 1: Fill Capture Callback Choice

**Question:** execDetails vs orderStatus — which is canonical for writing execution records and marking orders FILLED?

| Option | Description |
|--------|-------------|
| execDetails canonical | Writes executions rows; orderStatus handles SUBMITTED/REJECTED and FILLED/PARTIAL status transitions |
| orderStatus canonical | Capture fills from filled/remaining/avgFillPrice in orderStatus |
| Both with separate concerns | execDetails → executions rows; orderStatus → orders.status |

**User selection:** execDetails is canonical (Recommended)

**Notes:** The `executions` table schema has `exec_id UNIQUE` which maps naturally to execDetails. Phase 4's orderStatus already handles SUBMITTED/REJECTED — Phase 5 extends it to recognize Filled/PartiallyFilled statuses while execDetails owns the authoritative per-fill record.

---

## Area 2: Position Lot Writes — Who and When

**Question:** Where does the logic that writes to position_lots live when a fill arrives?

| Option | Description |
|--------|-------------|
| New module: positions.py | `bravos/execution/positions.py` with `open_lot()`, `close_lot()`, `partial_close_lot()` |
| Directly in execDetails callback | IBApp connection.py handles both executions and position_lots writes |
| Back in executor.py | Executor owns the full trade lifecycle including post-fill lot writes |

**User selection:** New module: bravos/execution/positions.py (Recommended)

**Notes:** Keeps IBApp callbacks thin. Follows Phase 3's pattern of module-level helper functions for DB operations. Independently testable.

---

## Area 3: FIFO Partial-Close Logic

**Question:** When selling a partial position across multiple lots, which FIFO approach?

| Option | Description |
|--------|-------------|
| Lot-by-lot FIFO | Oldest lot closed fully before touching the next |
| Proportional FIFO | Each lot shrinks proportionally to shares_to_close / total_open |

**User selection:** Lot-by-lot FIFO: oldest lot closed fully before touching the next (Recommended)

**Notes:** Standard FIFO, matches most brokerage implementations. Example: 20 shares to close from [10 oldest, 20 mid, 20 newest] → close lot 1 fully (10), reduce lot 2 by 10 (from 20 to 10), lot 3 untouched. Ordered by `lot_opened_at ASC`.

---

## Area 4: Periodic Reconciliation Timing and Response

**Question:** How often does the system call reqPositions() for drift detection, and what happens on mismatch?

| Option | Description |
|--------|-------------|
| Every 5 min, log+flag only | Piggyback on scrape cycle; same log-only policy as startup reconciliation |
| Once per hour, log+flag only | Less frequent, still conservative |
| Every 5 min, auto-correct | Auto-update position_lots from IBKR data when mismatch found |

**User selection:** Every 5 minutes (piggyback on scrape interval) — log+flag only (Recommended)

**Notes:** Conservative v1 choice. Reuses existing `_reconcile_against_db()` helper from Phase 3. No auto-correction — operator reviews discrepancies via logs. Auto-correction deferred to v2.

---

## Claude's Discretion Items

The following were noted as implementation details left to the planner:
- Whether `execDetailsEnd` is used beyond an INFO log
- Whether `partial_close_lot` and `close_lot` are separate functions or unified
- Threading model for `execDetails` DB writes (api thread → db_conn access pattern)
- How `db_conn` is passed to the `execDetails` callback (likely stored on IBApp at startup)

---

## Deferred Ideas

- Auto-correction of position_lots on mismatch (v2)
- Commission capture for net P&L (EXEC-V2-02, v2)
- Slippage tracking (EXEC-V2-01, v2)
- Fill notification emails (NOTF-V2-01, Phase 7 / v2)
