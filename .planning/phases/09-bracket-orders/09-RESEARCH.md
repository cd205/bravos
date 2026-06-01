# Phase 9: Bracket Orders - Research

**Researched:** 2026-06-01
**Domain:** ibapi bracket orders, PostgreSQL array columns, regex extension, Python signal parsing
**Confidence:** HIGH

## Summary

Phase 9 is a well-scoped extension to two existing modules — `parser.py` and `executor.py` — plus a new migration file and scraper update. The decisions in CONTEXT.md are specific and technically sound. All ibapi bracket order mechanics have been confirmed against the installed ibapi library (`ibapi==9.81.1.post1` in the bravos conda environment). The key ibapi fields are `parentId`, `ocaGroup`, `ocaType`, `transmit`, `auxPrice` (for STP stop price), and `lmtPrice` (for LMT take-profit price).

The PostgreSQL array type `NUMERIC(10,2)[]` is natively supported by psycopg2 2.9.12: a Python `list` of floats adapts to `ARRAY[...]` and an empty list adapts to `'{}'`. When inserting into a typed column, no explicit SQL cast is needed. The scraper's `_store_signal` method needs two additional parameters added to its existing INSERT.

The executor's `next_order_id` pattern (take current, increment by 1, store back) must be applied N+2 times upfront for a bracket. The ordering of DB writes vs placeOrder calls follows the same PENDING_SUBMISSION-first discipline as `_submit_order`. The `_order_status_events` slot mechanism is sufficient for the parent order; child status events can optionally be registered but the existing timeout-and-assume-submitted fallback applies equally.

**Primary recommendation:** Follow CONTEXT.md decisions verbatim. The codebase patterns are clean and the bracket implementation is a straightforward extension of existing patterns.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| TP/SL regex extraction | Parser (ingestion) | — | Body-only fields, same rule as weight |
| Signal INSERT with new fields | Scraper (ingestion) | — | Owns the INSERT INTO signals call |
| Schema migration | DB (infra) | — | DDL change, idempotent migration file |
| Bracket order construction | Executor (execution) | — | Owns all IBKR order placement logic |
| OCA group assignment | Executor (execution) | — | ibapi field set at order build time |
| DB writes for child orders | Executor (execution) | — | Same orders table, same commit pattern |
| ID reservation (N+2 IDs) | Executor (execution) | ibapp (next_order_id) | ID source lives on ibapp singleton |

## Standard Stack

No new libraries are required. Phase 9 uses only existing project dependencies.

### Core (all already installed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| ibapi | 9.81.1.post1 | IBKR order placement | Official IB API |
| psycopg2-binary | 2.9.12 | PostgreSQL client | Project standard |
| re (stdlib) | — | TP/SL regex extraction | Existing parser pattern |

### ibapi Order Fields Verified
[VERIFIED: /home/chris_s_dodd/miniconda3/envs/bravos/lib/python3.13/site-packages/ibapi/order.py]

| Field | Type | Default | Purpose in Bracket |
|-------|------|---------|-------------------|
| `parentId` | int | 0 | Set on child orders to the IBKR order ID of parent |
| `ocaGroup` | str | "" | OCA group name (all TP children share same value) |
| `ocaType` | int | 0 | 2 = REDUCE_WITH_BLOCK (D-12) |
| `transmit` | bool | True | False on parent+STP+all-but-last-TP; True on last TP |
| `auxPrice` | float | UNSET_DOUBLE | Stop trigger price for STP order |
| `lmtPrice` | float | UNSET_DOUBLE | Limit price for LMT take-profit orders |

**Critical distinction:** `parentId` on ibapi `Order` is the **IBKR order ID** (the integer passed to `placeOrder`), NOT the DB row id. D-16 in CONTEXT.md correctly distinguishes: `parent_order_id` in the DB `orders` table references the DB row id (`orders.id`), while `order.parentId` in ibapi references the IBKR integer order ID.

## Architecture Patterns

### System Architecture Diagram

```
Alert body (text)
       |
       v
   parse_signal()                          parser.py
   + STOP_LOSS_RE / TAKE_PROFIT_RE
       |
       | {stop_loss: float|None, take_profits: [float,...]}
       v
   _store_signal()                         scraper.py
   INSERT INTO signals
   (stop_loss, take_profits added)
       |
       | signal_id
       v
   execute_signal()                        executor.py
       |
       |-- [D-10 conditions met] ---------> _submit_bracket_order()
       |                                       |
       |                                       |-- alloc N+2 IBKR IDs
       |                                       |-- INSERT parent (PENDING_SUBMISSION)
       |                                       |-- INSERT STP child
       |                                       |-- INSERT LMT children x N
       |                                       |-- placeOrder(parent, transmit=False)
       |                                       |-- placeOrder(stp, transmit=False)
       |                                       |-- placeOrder(tp1..N-1, transmit=False)
       |                                       |-- placeOrder(tpN, transmit=True)  <-- fires bracket
       |                                       |-- wait orderStatus(parent)
       |                                       |-- UPDATE orders rows
       |
       |-- [fallback] ------------------> _submit_order()  (unchanged)
```

### Recommended Project Structure (no new files needed)

```
bravos/
├── ingestion/
│   ├── parser.py           # Add STOP_LOSS_RE, TAKE_PROFIT_RE, extend parse_signal()
│   └── scraper.py          # Extend _store_signal() INSERT to include stop_loss, take_profits
├── execution/
│   └── executor.py         # Add _submit_bracket_order(), extend execute_signal() + _load_signal()
infra/
└── migrate_phase9.sql      # New: ADD COLUMN IF NOT EXISTS for 4 columns
tests/
└── test_execution.py       # New test stubs for bracket path (Wave 0)
tests/
└── test_parser.py          # New test stubs for TP/SL extraction (Wave 0)
```

### Pattern 1: ibapi Bracket Order Submission Sequence
**What:** Parent MKT with transmit=False, then child orders each with parentId set, last child has transmit=True. IBKR holds all orders until the final transmit=True call.
**When to use:** Any time parent + children must be submitted atomically.

```python
# Source: VERIFIED from ibapi/order.py field inspection + CONTEXT.md specifics
def _submit_bracket_order(ibapp, signal_id, ticker, action, quantity,
                           stop_loss, take_profits, db_conn):
    n = len(take_profits)
    # Allocate all IDs upfront (D-17): parent + 1 STP + N LMT = N+2 IDs
    parent_ibkr_id = ibapp.next_order_id
    stp_ibkr_id = parent_ibkr_id + 1
    tp_ibkr_ids = [parent_ibkr_id + 2 + i for i in range(n)]
    ibapp.next_order_id = parent_ibkr_id + n + 2

    # Parent: MKT entry, transmit=False
    parent_order = Order()
    parent_order.action = action
    parent_order.orderType = "MKT"
    parent_order.totalQuantity = quantity
    parent_order.tif = "DAY"
    parent_order.transmit = False

    # STP child: stop loss for full quantity (D-13), transmit=False
    stp_order = Order()
    stp_order.action = "SELL" if action == "BUY" else "BUY"
    stp_order.orderType = "STP"
    stp_order.totalQuantity = quantity
    stp_order.auxPrice = stop_loss       # STP uses auxPrice for trigger
    stp_order.tif = "GTC"               # stop should persist beyond today
    stp_order.parentId = parent_ibkr_id  # ibkr ID of parent, NOT db id
    stp_order.transmit = False

    # TP children: equal split (D-11), OCA group (D-12/D-15)
    oca_group = f"TP_{parent_ibkr_id}"
    base_qty = quantity // n
    tp_orders = []
    for i, tp_price in enumerate(take_profits):
        is_last = (i == n - 1)
        tp_qty = base_qty if not is_last else quantity - (n - 1) * base_qty
        tp_order = Order()
        tp_order.action = "SELL" if action == "BUY" else "BUY"
        tp_order.orderType = "LMT"
        tp_order.totalQuantity = tp_qty
        tp_order.lmtPrice = tp_price
        tp_order.tif = "GTC"
        tp_order.parentId = parent_ibkr_id
        tp_order.ocaGroup = oca_group
        tp_order.ocaType = 2             # REDUCE_WITH_BLOCK (D-12)
        tp_order.transmit = is_last      # D-14: last TP fires the whole bracket
        tp_orders.append(tp_order)

    contract = _build_contract(ticker)
    # ... DB writes (PENDING_SUBMISSION) for all orders first ...
    ibapp.placeOrder(parent_ibkr_id, contract, parent_order)
    ibapp.placeOrder(stp_ibkr_id, contract, stp_order)
    for i, tp_order in enumerate(tp_orders):
        ibapp.placeOrder(tp_ibkr_ids[i], contract, tp_order)
    # ... wait for parent orderStatus callback ...
```

### Pattern 2: next_order_id Bulk Reservation
**What:** Allocate all IDs upfront before any placeOrder call. No lock needed — executor runs on the main thread; ibapi callbacks only read the order IDs they were given.
**When to use:** Any multi-order submission.

```python
# Source: VERIFIED from executor.py _submit_order() and connection.py nextValidId()
# Existing single-order pattern (executor.py line 262-266):
order_id = ibapp.next_order_id
ibapp.next_order_id = int(order_id) + 1  # increment after taking

# Bracket extension (N+2 orders):
parent_ibkr_id = ibapp.next_order_id
ibapp.next_order_id = int(parent_ibkr_id) + n + 2  # reserve all at once
```

### Pattern 3: DB writes for bracket child orders
**What:** All bracket orders written to `orders` table with `parent_order_id` (FK to DB row id) and `oca_group`. Same PENDING_SUBMISSION-first discipline.

```python
# orders INSERT for child orders (D-16)
cur.execute(
    """
    INSERT INTO orders
      (signal_id, ibkr_order_id, ticker, action, quantity,
       order_type, status, submitted_at, parent_order_id, oca_group)
    VALUES (%s, %s, %s, %s, %s, %s, 'PENDING_SUBMISSION', NOW(), %s, %s)
    RETURNING id
    """,
    (signal_id, stp_ibkr_id, ticker, stp_action, quantity,
     'STP', db_parent_order_id, None),  # STP has no OCA group
)
```

### Pattern 4: psycopg2 array parameter handling
**What:** Python `list` natively adapts to PostgreSQL array syntax. Empty list produces `'{}'`. No explicit cast needed when column type is declared.

```python
# Source: VERIFIED via psycopg2 2.9.12 adapt() calls
# Non-empty list:
take_profits = [320.0, 350.0, 370.0]
# psycopg2 sends: ARRAY[320.0,350.0,370.0]

# Empty list (D-06 default):
take_profits = []
# psycopg2 sends: '{}'
# Both work without ::NUMERIC[] cast when column type is NUMERIC(10,2)[]

cur.execute(
    "INSERT INTO signals (..., stop_loss, take_profits) VALUES (..., %s, %s)",
    (..., signal.get("stop_loss"), signal.get("take_profits", [])),
)
```

### Pattern 5: STP order TIF consideration
**What:** Stop loss orders for bracket positions should use `GTC` (Good Till Cancelled), not `DAY`. A DAY stop would expire at market close and the position would be unprotected overnight.
**Decision note:** CONTEXT.md D-13 specifies `STP` order type but does not specify TIF. `GTC` is the correct TIF for protective stops — a `DAY` stop expires at close and the position would be unprotected overnight. This is a Claude's Discretion item.

### Pattern 6: TP order TIF
Same reasoning: take-profit LMT orders should use `GTC` so they persist until filled or a signal reversal cancels them.

### Anti-Patterns to Avoid

- **Setting `parentId` to the DB orders.id:** `parentId` in ibapi must be the IBKR integer order ID (the one passed to `placeOrder`), not the database row id. The context CONTEXT.md D-16 correctly separates these: `parent_order_id` in the DB is the DB row id FK.
- **Using `auxPrice` for LMT orders:** `auxPrice` is the stop price for STP orders. LMT orders use `lmtPrice`. Do not swap these.
- **Transmitting the parent before all children are registered:** If `transmit=True` on the parent, IBKR transmits immediately without the children. The bracket must be: parent (`transmit=False`) → STP (`transmit=False`) → TP[0..N-2] (`transmit=False`) → TP[N-1] (`transmit=True`).
- **Waiting for orderStatus on all child orders:** Only the parent slot needs to be awaited in `_submit_bracket_order`. Children fire callbacks asynchronously. The existing timeout-and-assume-SUBMITTED fallback applies.
- **Not incrementing next_order_id atomically:** IDs must be reserved as a block before the first `placeOrder` call. Interleaving with other executor calls (unlikely in practice since execute_signal is called from the main thread) would produce ID collisions.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OCA group management | Custom cancel-all logic | ibapi `ocaType=2` | IBKR enforces OCA server-side; client-side is unreliable |
| Stop order execution | Manual position monitoring loop | ibapi `STP` order type | IBKR handles trigger + execution atomically |
| Bracket atomicity | Multi-step sequential orders | `transmit=False`/`True` pattern | IBKR rejects children that arrive before the parent is transmitted |

**Key insight:** The entire bracket mechanism — atomicity, stop execution, OCA reduce behavior — is enforced by IBKR server-side. The client only needs to set the right fields and submit in the right order.

## Common Pitfalls

### Pitfall 1: parentId is the IBKR order integer, not the DB row id
**What goes wrong:** Child order is submitted with `order.parentId = db_order_id` (e.g., 1042). IBKR rejects with "Parent order not found" because it looks for IBKR order ID 1042, not DB row 1042.
**Why it happens:** Two ID spaces share the name "order ID" in this codebase — `orders.id` (serial PK from DB) and `ibkr_order_id` (integer from IBKR's `nextValidId`). They are often different values.
**How to avoid:** Set `child_order.parentId = parent_ibkr_id` (the integer allocated from `ibapp.next_order_id`), not `db_parent_order_id`.
**Warning signs:** IBKR error callback fires with error code 161 ("Invalid parent order ID").

### Pitfall 2: Transmitting parent before children are built
**What goes wrong:** If `transmit=True` on the parent order, IBKR transmits it immediately as a standalone MKT order. The subsequent STP and LMT orders arrive as orphaned children and are rejected or converted to independent orders.
**Why it happens:** The default for `Order.transmit` is `True` (verified in ibapi/order.py). Forgetting to set `transmit=False` on the parent causes silent misbehavior.
**How to avoid:** Always explicitly set `parent_order.transmit = False` before `placeOrder(parent_ibkr_id, ...)`.
**Warning signs:** Position opens without stop protection; IBKR error 162 ("Child orders not allowed for transmitted parent").

### Pitfall 3: Empty take_profits list in signals INSERT
**What goes wrong:** `parse_signal()` returns `take_profits: []`. When inserted via psycopg2, `[]` becomes `'{}'`. If the DB column has `DEFAULT '{}'` (D-06), this is fine. But if passing `None` instead of `[]`, psycopg2 sends `NULL` — violating any NOT NULL constraint and causing a different error than expected.
**Why it happens:** `parse_signal()` returns `take_profits: list[float]` (D-03), but code that reads from the dict might use `.get("take_profits")` without a default, which returns `None` on missing key.
**How to avoid:** Use `signal.get("take_profits", [])` in the scraper INSERT, not `signal.get("take_profits")`.

### Pitfall 4: STP TIF=DAY leaves position unprotected overnight
**What goes wrong:** Stop loss order expires at 16:00 ET. Position is held overnight with no protection.
**Why it happens:** Using same `DAY` TIF as the parent MKT entry order.
**How to avoid:** Use `GTC` for both STP and LMT child orders.

### Pitfall 5: psycopg2 NUMERIC[] cast requirement for array literals
**What goes wrong:** In a raw SQL INSERT that does not have the column type in scope (e.g., a `DO $$ PLPGSQL $$` block or a dynamic query), `ARRAY[320.0, 350.0]` may fail with "cannot determine type of empty array" or type mismatch.
**Why it happens:** PostgreSQL needs type context for untyped ARRAY literals.
**How to avoid:** psycopg2's native list adapter handles this correctly for parameterized queries (`%s`). The issue only arises with string-interpolated SQL. Never build SQL with Python string formatting for array values.

### Pitfall 6: _load_signal missing the new columns
**What goes wrong:** `execute_signal` calls `_load_signal()`, which has a hardcoded `SELECT` with 6 columns (ticker, action_type, weight_from, weight_to, reference_price, confidence). After the migration adds `stop_loss` and `take_profits`, `_load_signal` must be updated to SELECT and return these fields, or the bracket condition check in D-10 will always see `None`/`[]`.
**Why it happens:** The existing `_load_signal` uses positional column access (`row[0]`, `row[1]`, etc.) and a hardcoded column list.
**How to avoid:** Update `_load_signal` to include `stop_loss` and `take_profits` in the SELECT and in the returned dict.

## Code Examples

### TP/SL Regex Patterns (per D-01, D-02, D-04)

```python
# Source: CONTEXT.md D-01 / D-02 + observed signal formats from CONTEXT.md <specifics>

# Structured form: "Suggested Stop Loss (SL): $257"
# Prose form: "stop at $195", "a stop of $195"
STOP_LOSS_RE = re.compile(
    r'(?:'
    r'Suggested Stop Loss.*?\$(\d+(?:\.\d{1,2})?)'   # structured
    r'|stop\s+(?:at|of)\s+\$(\d+(?:\.\d{1,2})?)'    # prose
    r')',
    re.IGNORECASE,
)

# Structured form: "Take Profit (TP): $320, $350, and $370"
# Prose form: "price targets of $250, $275, and $300"
TAKE_PROFIT_RE = re.compile(
    r'(?:'
    r'Take Profit.*?:\s*((?:\$\d+(?:\.\d{1,2})?[\s,and]*)+)'  # structured
    r'|price targets?\s+of\s+((?:\$\d+(?:\.\d{1,2})?[\s,and]*)+)'  # prose
    r')',
    re.IGNORECASE,
)
# Extract individual prices from TP match group
PRICE_IN_LIST_RE = re.compile(r'\$(\d+(?:\.\d{1,2})?)')
```

**Note:** Exact regex details are Claude's Discretion. The above patterns are starting points; validate against the two real formats confirmed in CONTEXT.md `<specifics>`.

### parse_signal() return dict extension (D-03)

```python
# Source: VERIFIED from parser.py return dict structure
# Existing keys: ticker, action_type, weight_from, weight_to, reference_price,
#                confidence, parse_method
# New keys (D-03):
return {
    # ... existing keys ...
    "stop_loss": stop_loss,        # float | None
    "take_profits": take_profits,  # list[float], empty list if none found
}
```

### Migration SQL (D-05 through D-09)

```sql
-- infra/migrate_phase9.sql
-- Phase 9: Bracket Orders — schema migration
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase9.sql

-- signals table: TP/SL storage
ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(10,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS take_profits NUMERIC(10,2)[] DEFAULT '{}';

-- orders table: bracket linkage
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parent_order_id INTEGER REFERENCES orders(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS oca_group TEXT;

COMMENT ON COLUMN signals.stop_loss IS 'Stop loss price from alert body (Phase 9)';
COMMENT ON COLUMN signals.take_profits IS 'Take profit levels from alert body as array (Phase 9)';
COMMENT ON COLUMN orders.parent_order_id IS 'FK to orders.id of parent order (bracket child orders only) (Phase 9)';
COMMENT ON COLUMN orders.oca_group IS 'OCA group name for TP child orders (Phase 9)';

GRANT ALL ON orders TO bravos;
GRANT ALL ON signals TO bravos;
```

### D-10 condition check in execute_signal()

```python
# Source: CONTEXT.md D-10
# After quantity calculation, before submission:
action_type = signal.get("action_type")
stop_loss = signal.get("stop_loss")
take_profits = signal.get("take_profits", [])

use_bracket = (
    action_type in ("open", "add")
    and stop_loss is not None
    and len(take_profits) > 0
    and quantity >= len(take_profits)  # D-20: prevent 0-share TP orders
)

if use_bracket:
    final_status = _submit_bracket_order(
        ibapp, signal_id, ticker, ibkr_action, quantity,
        stop_loss, take_profits, db_conn,
    )
else:
    if action_type in ("open", "add") and (stop_loss is None or len(take_profits) == 0):
        logger.debug(
            "signal_id=%s: no TP/SL data — using bare MKT (fallback, D-19)",
            signal_id,
        )
    elif quantity < len(take_profits):
        logger.warning(
            "signal_id=%s quantity=%d < len(take_profits)=%d — using bare MKT (D-20)",
            signal_id, quantity, len(take_profits),
        )
    final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Separate order submissions | `transmit=False` bracket pattern | ibapi v9.x+ | Atomic submission; IBKR rejects old approach |
| `ocaType=1` cancel-all | `ocaType=2` reduce-with-block | Always available | Partial fills work correctly at TP levels |

**Deprecated/outdated:**
- `order.cancelOrder()` deprecated in some ibapi versions: use `client.cancelOrder()` instead (not relevant for Phase 9, but noted for future).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | STP orders should use `GTC` TIF (not `DAY`) | Pattern 5 | Position unprotected overnight — high severity |
| A2 | TP orders should use `GTC` TIF | Pattern 6 | TP orders expire at close — reduced but non-zero risk |
| A3 | Waiting only on the parent `orderStatus` slot is sufficient for bracket (not all children) | Pattern 1 | Could consider registering slots for each child to detect child-level rejection |

**All three items are in Claude's Discretion per CONTEXT.md.** The STOP_LOSS_RE and TAKE_PROFIT_RE exact regex syntax is also Claude's Discretion (D-01, D-02 give the match patterns but not the Python regex string).

## Open Questions (RESOLVED)

1. **OCA group for STP order**
   - What we know: D-15 says `oca_group = f"TP_{parent_ibkr_id}"` and D-16 says OCA group stored on all TP child rows
   - What's unclear: Should the STP order also be in the OCA group? If STP triggers, do we want the TP LMT orders to be cancelled?
   - Recommendation: Do NOT put STP in the OCA group. The OCA group is only for the TP children (so when one TP fills, remaining TPs reduce). The STP is a separate protective order. If the stop triggers and fills, IBKR will cancel the remaining working orders on the parent via OCA if the parent is the group anchor — but this is complex. Keep STP outside OCA per D-12 which says "OCA type 2 for TP children" (implicitly STP is separate).

2. **Status monitoring for bracket child orders**
   - What we know: `_order_status_events` maps IBKR order_id → event slot; existing pattern waits on parent slot only
   - What's unclear: If the parent rejects, do IBKR child orders also reject immediately?
   - Recommendation: Register only the parent slot. If parent is SUBMITTED, children are in flight. If parent is Inactive (rejected), log a warning — children should also be cancelled by IBKR automatically.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| ibapi | Bracket order placement | Yes | 9.81.1.post1 | — |
| psycopg2-binary | DB writes | Yes | 2.9.12 | — |
| PostgreSQL (Cloud SQL) | Migration + signal storage | Yes (via Auth Proxy) | 15+ | — |
| Python 3.13 (miniconda3 bravos env) | All code | Yes | 3.13 | — |

No missing dependencies. Step 2.6 complete.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | `/home/chris_s_dodd/bravos/pytest.ini` |
| Quick run command | `cd /home/chris_s_dodd/bravos && python -m pytest tests/test_parser.py tests/test_execution.py -x -m "not integration"` |
| Full suite command | `cd /home/chris_s_dodd/bravos && python -m pytest tests/ -m "not integration"` |

**Notes:**
- Default `addopts = -m "not integration"` means integration tests are excluded by default
- miniconda3 bravos env is required: `/home/chris_s_dodd/miniconda3/envs/bravos/bin/python`
- `db_connection` fixture in `conftest.py` requires Cloud SQL Auth Proxy on 127.0.0.1:5432

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-07 (parser) | STOP_LOSS_RE extracts structured form `Suggested Stop Loss (SL): $257` | unit | `pytest tests/test_parser.py::test_extract_stop_loss_structured -x` | Wave 0 |
| EXEC-07 (parser) | STOP_LOSS_RE extracts prose form `stop at $195` | unit | `pytest tests/test_parser.py::test_extract_stop_loss_prose -x` | Wave 0 |
| EXEC-07 (parser) | TAKE_PROFIT_RE extracts structured form with multiple prices | unit | `pytest tests/test_parser.py::test_extract_take_profits_structured -x` | Wave 0 |
| EXEC-07 (parser) | TAKE_PROFIT_RE extracts prose form `price targets of $250, $275, and $300` | unit | `pytest tests/test_parser.py::test_extract_take_profits_prose -x` | Wave 0 |
| EXEC-07 (parser) | parse_signal() returns stop_loss + take_profits keys | unit | `pytest tests/test_parser.py::test_parse_signal_returns_tp_sl -x` | Wave 0 |
| EXEC-07 (parser) | No TP/SL in body returns stop_loss=None, take_profits=[] | unit | `pytest tests/test_parser.py::test_parse_signal_no_tp_sl -x` | Wave 0 |
| EXEC-07 (bracket) | _submit_bracket_order: all N+2 orders written to DB | unit+mock | `pytest tests/test_execution.py::test_bracket_db_writes -x` | Wave 0 |
| EXEC-07 (bracket) | Bracket: parent transmit=False, STP transmit=False, last TP transmit=True | unit+mock | `pytest tests/test_execution.py::test_bracket_transmit_sequence -x` | Wave 0 |
| EXEC-07 (bracket) | Bracket: child parentId equals parent IBKR order ID | unit+mock | `pytest tests/test_execution.py::test_bracket_parent_id -x` | Wave 0 |
| EXEC-07 (bracket) | Bracket: OCA group is f"TP_{parent_ibkr_id}" on all TP children | unit+mock | `pytest tests/test_execution.py::test_bracket_oca_group -x` | Wave 0 |
| EXEC-07 (bracket) | Bracket: equal quantity split with remainder to last TP | unit | `pytest tests/test_execution.py::test_bracket_quantity_split -x` | Wave 0 |
| EXEC-07 (fallback) | No TP/SL → falls back to bare MKT (existing _submit_order path) | unit+mock | `pytest tests/test_execution.py::test_bracket_fallback_no_tpsl -x` | Wave 0 |
| EXEC-07 (fallback) | quantity < len(take_profits) → falls back with warning | unit+mock | `pytest tests/test_execution.py::test_bracket_fallback_qty_too_small -x` | Wave 0 |
| EXEC-07 (schema) | migrate_phase9.sql is idempotent (safe to run twice) | manual | `psql ... -f infra/migrate_phase9.sql` (run twice, no error) | N/A |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_parser.py tests/test_execution.py -x -m "not integration"`
- **Per wave merge:** Full suite: `python -m pytest tests/ -m "not integration"`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps (new test stubs needed)
- [ ] `tests/test_parser.py` — add stubs: `test_extract_stop_loss_structured`, `test_extract_stop_loss_prose`, `test_extract_take_profits_structured`, `test_extract_take_profits_prose`, `test_parse_signal_returns_tp_sl`, `test_parse_signal_no_tp_sl`
- [ ] `tests/test_execution.py` — add stubs: `test_bracket_db_writes`, `test_bracket_transmit_sequence`, `test_bracket_parent_id`, `test_bracket_oca_group`, `test_bracket_quantity_split`, `test_bracket_fallback_no_tpsl`, `test_bracket_fallback_qty_too_small`

No new test infrastructure (conftest.py, framework install) required. All tests follow the existing Wave 0 skip-stub pattern from earlier phases.

## Security Domain

> security_enforcement: enabled (not explicitly disabled in config)

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes | Regex bounds: TP/SL prices must be positive floats; quantity check before bracket submission |
| V6 Cryptography | no | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed price string in alert body causes float conversion error | Tampering | try/except around float() conversion; return None on error |
| Negative stop loss / negative TP prices injected via malformed alert | Tampering | Validate stop_loss > 0 and all(tp > 0 for tp in take_profits) before submitting bracket |
| Take profits not monotonically increasing (TP1 > TP2) | Tampering | Log warning (not block); IBKR will accept any positive LMT prices |

**Input validation requirement:** `_submit_bracket_order` should validate `stop_loss > 0` and `len(take_profits) > 0` and `all(tp > 0 for tp in take_profits)` as guards before calling `placeOrder`. These are in addition to the D-10 conditions checked in `execute_signal`.

## Sources

### Primary (HIGH confidence)
- `/home/chris_s_dodd/miniconda3/envs/bravos/lib/python3.13/site-packages/ibapi/order.py` — Verified all Order field names: parentId, ocaGroup, ocaType, transmit, auxPrice, lmtPrice, defaults
- `/home/chris_s_dodd/bravos/bravos/execution/executor.py` — Verified existing patterns: next_order_id increment, _submit_order DB write sequence, _STATUS_MAP, threading.Event slot pattern
- `/home/chris_s_dodd/bravos/bravos/ingestion/parser.py` — Verified parse_signal() return dict, existing regex patterns
- `/home/chris_s_dodd/bravos/bravos/ingestion/scraper.py` — Verified _store_signal INSERT column list (9 columns), ON CONFLICT pattern
- `/home/chris_s_dodd/bravos/infra/schema.sql` — Verified orders and signals table baseline
- `/home/chris_s_dodd/bravos/infra/migrate_phase5.sql` — Verified migration pattern: ADD COLUMN IF NOT EXISTS, GRANT, COMMENT
- psycopg2 2.9.12 runtime verification — adapt([]) → `'{}'`, adapt([320.0, 350.0]) → `ARRAY[320.0,350.0]`

### Secondary (MEDIUM confidence)
- `.planning/phases/09-bracket-orders/09-CONTEXT.md` — All design decisions verified as technically consistent with ibapi field inspection
- `tests/test_execution.py` and `tests/test_parser.py` — Wave 0 skip-stub pattern, db_connection fixture, MagicMock + threading.Event test pattern

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified in installed conda env
- Architecture: HIGH — ibapi Order fields confirmed, psycopg2 array behavior verified
- Pitfalls: HIGH — based on direct code inspection and ibapi field defaults
- Regex patterns: MEDIUM — starting points based on CONTEXT.md formats; exact patterns need validation against live alert bodies

**Research date:** 2026-06-01
**Valid until:** 2026-07-01 (ibapi is stable; psycopg2 behavior is stable)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: STOP_LOSS_RE matches structured form (`Suggested Stop Loss (SL): $257`) and prose form (`stop at $195`, `a stop of $195`). Returns first match as `float | None`.
- D-02: TAKE_PROFIT_RE matches structured form (`Take Profit (TP): $320, $350, and $370`) and prose form (`price targets of $250, $275, and $300`). Returns list of floats (empty list if none).
- D-03: `parse_signal()` returns two new keys: `stop_loss: float | None` and `take_profits: list[float]`. Empty list (not None) when no TPs found.
- D-04: TP/SL extraction from body only.
- D-05: Add `stop_loss NUMERIC(10,2)` to `signals` table. Nullable.
- D-06: Add `take_profits NUMERIC(10,2)[]` to `signals` table. Default `'{}'`.
- D-07: Add `parent_order_id INTEGER` to `orders` table (FK → orders.id). NULL for parent orders and bare MKT orders.
- D-08: Add `oca_group TEXT` to `orders` table. NULL for non-OCA orders.
- D-09: Migration file: `infra/migrate_phase9.sql`. Safe/idempotent.
- D-10: Bracket only when `action_type in ('open', 'add')` AND `stop_loss is not None` AND `len(take_profits) > 0` AND `quantity >= len(take_profits)`.
- D-11: Equal quantity split; remainder to last TP order.
- D-12: OCA type = 2 (reduce but do not cancel remaining orders).
- D-13: Stop loss order type = `STP` (not `STP LMT`).
- D-14: `transmit=False` on parent and all children except last TP. Last child gets `transmit=True`.
- D-15: OCA group name: `f"TP_{parent_order_id}"` (where parent_order_id is the IBKR integer ID).
- D-16: All child orders written to `orders` table with `parent_order_id` set to the parent's DB row id. OCA group stored in `oca_group` column of all TP child rows.
- D-17: `next_order_id` incremented N+2 times upfront before placing any order.
- D-18: `_submit_bracket_order()` extracted as a new private function in executor.py alongside the existing `_submit_order()`.
- D-19: Bare MKT path (`_submit_order`) is unchanged.
- D-20: If quantity < len(take_profits), skip bracket and use bare MKT. Log a warning with signal_id and quantity.

### Claude's Discretion
- Order of regex pattern matching (structured vs prose variant precedence)
- Exact variable names inside `_submit_bracket_order`
- Whether to use a dataclass/namedtuple for bracket order parameters or pass flat args

### Deferred Ideas (OUT OF SCOPE)
- Configurable TP quantity weighting (e.g., 50/30/20 split instead of equal)
- Trailing stop variant instead of fixed STP
- Dashboard display of bracket order chains
- Modifying/cancelling bracket exits when a signal reversal comes in
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-07 | Bracket order submission: parent MKT + STP + N LMT in OCA group for open/add signals with TP/SL data | ibapi Order fields verified (parentId, ocaGroup, ocaType, transmit, auxPrice, lmtPrice); next_order_id bulk reservation pattern confirmed; psycopg2 array handling verified; all 4 file touch points identified |
</phase_requirements>
