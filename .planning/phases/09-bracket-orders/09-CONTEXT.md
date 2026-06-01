# Phase 9: Bracket Orders - Context

**Gathered:** 2026-06-01
**Status:** Ready for planning

<domain>
## Phase Boundary

Parse stop loss and take profit levels from Bravos alert bodies and submit bracket orders to
IBKR for open/add signals that contain TP/SL data. A bracket is: parent MKT order (entry) +
STP child (stop loss for full quantity) + N LMT children (one per TP level, quantity split
equally, all in an OCA group). Signals without TP/SL data continue to submit bare MKT orders —
no regression in existing behavior. Scope is parser + schema migration + executor changes only;
no dashboard, no notification changes.

</domain>

<decisions>
## Implementation Decisions

### Parser — TP/SL extraction

- **D-01:** Add `STOP_LOSS_RE` matching both structured form (`Suggested Stop Loss (SL): $257`) and
  prose form (`stop at $195`, `a stop of $195`). Returns first match as `float | None`.
- **D-02:** Add `TAKE_PROFIT_RE` matching structured form (`Take Profit (TP): $320, $350, and $370`)
  and prose form (`price targets of $250, $275, and $300`). Returns list of floats (empty list if none).
- **D-03:** `parse_signal()` returns two new keys: `stop_loss: float | None` and
  `take_profits: list[float]`. Empty list (not None) when no TPs found — callers can check `len()`.
- **D-04:** TP/SL extraction from body only (same rule as weight). Title never contains price levels.

### Schema migration

- **D-05:** Add `stop_loss NUMERIC(10,2)` to `signals` table. Nullable — most signals won't have it
  until parser is deployed.
- **D-06:** Add `take_profits NUMERIC(10,2)[]` to `signals` table (PostgreSQL array). Default `'{}'`
  (empty array, not NULL) — simplifies executor check (`array_length(take_profits, 1) > 0`).
- **D-07:** Add `parent_order_id INTEGER` to `orders` table (FK → orders.id). NULL for parent orders
  and bare MKT orders.
- **D-08:** Add `oca_group TEXT` to `orders` table. NULL for non-OCA orders.
- **D-09:** Migration file: `infra/migrate_phase9.sql`. Safe/idempotent (`ADD COLUMN IF NOT EXISTS`).

### Executor — bracket submission

- **D-10:** Bracket submitted only when `action_type in ('open', 'add')` AND `stop_loss is not None`
  AND `len(take_profits) > 0` AND `quantity >= len(take_profits)`. All four conditions required.
  Any condition false → fall back to bare MKT (current behavior, zero code change to that path).
- **D-11:** Quantity split: equal split across TPs using integer division (`quantity // N`). Remainder
  shares go to the last TP order (i.e., last TP gets `quantity - (N-1) * (quantity // N)`).
- **D-12:** OCA type = 2 (reduce but do not cancel remaining orders). Partial TP fills are acceptable;
  we do not want all other TP orders cancelled just because one fills.
- **D-13:** Stop loss order type = `STP` (not `STP LMT`). Simpler, guaranteed execution, standard for
  protective stops.
- **D-14:** `transmit=False` on parent and all children except the last TP order. Last child gets
  `transmit=True` — IBKR transmits the whole bracket atomically on that call.
- **D-15:** OCA group name: `f"TP_{parent_order_id}"` — unique per bracket, human-readable in TWS.
- **D-16:** All child orders written to the `orders` table with `parent_order_id` set to the parent's
  DB row id. OCA group stored in `oca_group` column of all TP child rows.
- **D-17:** `next_order_id` incremented once per order submitted (parent + 1 STP + N LMT = N+2 IDs).
  Executor allocates the full ID range upfront before placing any order.
- **D-18:** `_submit_bracket_order()` extracted as a new private function in executor.py alongside the
  existing `_submit_order()`. `execute_signal()` selects which to call based on D-10 conditions.

### Fallback behavior

- **D-19:** Bare MKT path (`_submit_order`) is unchanged. No refactoring of existing order flow.
- **D-20:** If quantity < len(take_profits) (would produce 0-share TP orders), skip bracket and use
  bare MKT. Log a warning with the signal_id and quantity.

### Claude's Discretion

- Order of regex pattern matching (structured vs prose variant precedence)
- Exact variable names inside `_submit_bracket_order`
- Whether to use a dataclass/namedtuple for bracket order parameters or pass flat args

</decisions>

<specifics>
## Specific Ideas

From the memory note (confirmed real signal format):

Structured summary block (always present on open signals):
```
Take Profit (TP): $320, $350, and $370
Suggested Stop Loss (SL): $257
Weight Allocation: 3
```

Prose variant also observed:
```
with a weight of 5, a stop at $195, and price targets of $250, $275, and $300
```

ibapi bracket order mechanics (confirmed working pattern from memory):
```python
# transmit=False on parent and all children until last child
parent.transmit = False
sl.transmit = False
for i, tp in enumerate(tp_orders):
    tp.transmit = (i == len(tp_orders) - 1)  # last one fires the whole bracket
```

OCA type 2 = "reduce but do not cancel" — preferred over type 1 (cancel all).

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing parser (extend, don't replace)
- `bravos/ingestion/parser.py` — Current regex patterns, `parse_signal()` return dict, confidence scoring. New TP/SL fields added as new keys; existing keys unchanged.

### Existing executor (extend, don't replace)
- `bravos/execution/executor.py` — `execute_signal()`, `_submit_order()`, `_build_order()`, order ID management pattern (`ibapp.next_order_id`). Bracket submission follows same threading/event pattern.

### Schema baseline
- `infra/migrate_phase5.sql` — Pattern for safe idempotent migrations (`ADD COLUMN IF NOT EXISTS`). Phase 9 migration follows this exactly.
- `infra/schema.sql` — Full schema for reference on orders and signals table structure.

### ibapi reference
- `bravos/broker/connection.py` — IBApp class, `next_order_id`, `_order_status_events` pattern. Bracket uses same callback slot mechanism.

No external specs — requirements fully captured in decisions above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_build_contract(ticker)` in executor.py: reused as-is for all bracket orders (same contract object for parent + children)
- `ibapp._order_status_events` dict: bracket can register one slot per child order, or just the parent — decision left to planner
- `ibapp.next_order_id` / increment pattern: already handles multi-order ID reservation with `ibapp.next_order_id = int(order_id) + 1`

### Established Patterns
- `transmit=False` / `transmit=True` on last order: standard ibapi bracket pattern; must follow exactly or IBKR rejects
- `_STATUS_MAP` lookup for orderStatus callbacks: bracket child orders will fire the same callback; executor must handle multiple status events per signal
- `db_conn.commit()` after each DB write: keep same pattern in bracket path
- `ADD COLUMN IF NOT EXISTS` in migration SQL: established pattern (Phase 5 migration), use in Phase 9

### Integration Points
- `parse_signal()` return dict: add `stop_loss` and `take_profits` keys; scraper stores them via the existing `INSERT INTO signals` call (scraper.py must be updated to pass new fields)
- `_load_signal()` in executor.py: must SELECT the two new columns (`stop_loss`, `take_profits`) in addition to existing fields
- `execute_signal()` branching: after quantity calculation, check D-10 conditions to route to `_submit_bracket_order` vs existing `_submit_order`

</code_context>

<deferred>
## Deferred Ideas

- Configurable TP quantity weighting (e.g., 50/30/20 split instead of equal) — future enhancement
- Trailing stop variant instead of fixed STP — future phase
- Dashboard display of bracket order chains — Phase 7 or later
- Modifying/cancelling bracket exits when a signal reversal comes in — complex, future phase

</deferred>

---

*Phase: 09-bracket-orders*
*Context gathered: 2026-06-01*
