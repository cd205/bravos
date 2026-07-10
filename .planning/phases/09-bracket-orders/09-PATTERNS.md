# Phase 9: Bracket Orders - Pattern Map

**Mapped:** 2026-06-01
**Files analyzed:** 6 (5 modified, 1 new)
**Analogs found:** 6 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bravos/ingestion/parser.py` | utility (parser) | transform | `bravos/ingestion/parser.py` (self) | self — extend in place |
| `bravos/ingestion/scraper.py` | service | request-response | `bravos/ingestion/scraper.py` (self) | self — extend in place |
| `bravos/execution/executor.py` | service | request-response | `bravos/execution/executor.py` (self) | self — extend in place |
| `infra/migrate_phase9.sql` | migration | batch | `infra/migrate_phase5.sql` | exact |
| `tests/test_parser.py` | test | transform | `tests/test_parser.py` (self) | self — extend in place |
| `tests/test_execution.py` | test | request-response | `tests/test_execution.py` (self) | self — extend in place |

---

## Pattern Assignments

### `bravos/ingestion/parser.py` (utility/parser, transform)

**Analog:** `bravos/ingestion/parser.py` — extend in place. New regex constants and two new return keys added. No existing keys changed.

**Imports pattern** (lines 1-10 — copy as-is, no new imports needed):
```python
import re
import logging

logger = logging.getLogger(__name__)
```

**Existing regex constant pattern** (lines 13-28 — copy structural style for new constants):
```python
# Each constant has a comment block explaining match variants and source.
PRICE_RE = re.compile(r'at \$(\d+(?:\.\d{1,2})?)')
WEIGHT_RE = re.compile(
    r'weight(?:\s+\w+){0,3}?\s+(?:from\s+)?(\d+)\s+to\s+(\d+)',
    re.IGNORECASE,
)
WEIGHT_SINGLE_RE = re.compile(
    r'weight\s+of\s+(\d+)(?!\s+to)',
    re.IGNORECASE,
)
```

**New constants to add (insert after WEIGHT_SINGLE_RE, before ACTION_KEYWORDS):**
```python
# Structured: "Suggested Stop Loss (SL): $257"
# Prose:      "stop at $195", "a stop of $195"
STOP_LOSS_RE = re.compile(
    r'(?:'
    r'Suggested Stop Loss.*?\$(\d+(?:\.\d{1,2})?)'
    r'|stop\s+(?:at|of)\s+\$(\d+(?:\.\d{1,2})?)'
    r')',
    re.IGNORECASE,
)

# Structured: "Take Profit (TP): $320, $350, and $370"
# Prose:      "price targets of $250, $275, and $300"
TAKE_PROFIT_RE = re.compile(
    r'(?:'
    r'Take Profit.*?:\s*((?:\$\d+(?:\.\d{1,2})?[\s,and]*)+)'
    r'|price targets?\s+of\s+((?:\$\d+(?:\.\d{1,2})?[\s,and]*)+)'
    r')',
    re.IGNORECASE,
)
# Helper: extract individual dollar amounts from a TP match group string
PRICE_IN_LIST_RE = re.compile(r'\$(\d+(?:\.\d{1,2})?)')
```

**Core pattern — parse_signal() docstring extension** (lines 167-192 — follow existing docstring format):
The docstring "Returns:" block currently lists 7 keys (lines 185-192). Two new keys are added in-place following the same format:
```python
# Existing return dict (lines 273-281):
return {
    "ticker": ticker,
    "action_type": action_type,
    "weight_from": weight_from,
    "weight_to": weight_to,
    "reference_price": reference_price,
    "confidence": confidence,
    "parse_method": parse_method,
    # --- Phase 9 additions (D-03) ---
    "stop_loss": stop_loss,        # float | None
    "take_profits": take_profits,  # list[float], always a list (empty if not found)
}
```

**Extraction logic to add inside parse_signal() body (insert before the cross-check block near line 268):**
Follows the same "body only" rule as WEIGHT_RE (see comment on line 226). Use the same pattern: call `.search(body)` for stop loss (first match only, D-01), then `.search(body)` for take profits, then extract list using PRICE_IN_LIST_RE.

```python
# --- Stop loss (body only, first match, D-01/D-04) ---
sl_match = STOP_LOSS_RE.search(body)
if sl_match:
    raw = sl_match.group(1) or sl_match.group(2)
    try:
        stop_loss = float(raw)
    except (TypeError, ValueError):
        stop_loss = None
else:
    stop_loss = None

# --- Take profits (body only, list of floats, D-02/D-04) ---
tp_match = TAKE_PROFIT_RE.search(body)
if tp_match:
    tp_string = tp_match.group(1) or tp_match.group(2)
    try:
        take_profits = [float(p) for p in PRICE_IN_LIST_RE.findall(tp_string)]
    except (TypeError, ValueError):
        take_profits = []
else:
    take_profits = []
```

**Error handling pattern** — follow existing price extraction pattern (line 224):
```python
# Existing safe float cast pattern to copy:
reference_price = float(price_match.group(1)) if price_match else None
# For TP/SL, wrap in try/except because the string value comes from a substring, not the whole group.
```

---

### `bravos/ingestion/scraper.py` (service, request-response)

**Analog:** `bravos/ingestion/scraper.py` — extend `_store_signal()` INSERT only. No structural changes.

**Existing INSERT pattern** (lines 229-247 — copy column list structure exactly, add two columns):
```python
# Existing INSERT (11 columns, 10 placeholders):
cur.execute(
    """
    INSERT INTO signals
      (post_url, post_title, raw_html, ticker, action_type,
       weight_from, weight_to, reference_price, confidence,
       parse_method, scraped_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT (post_url) DO NOTHING
    RETURNING id
    """,
    (
        signal_data["post_url"], signal_data["post_title"],
        signal_data["raw_html"], signal_data.get("ticker"),
        signal_data.get("action_type"), signal_data.get("weight_from"),
        signal_data.get("weight_to"), signal_data.get("reference_price"),
        signal_data.get("confidence"), signal_data.get("parse_method"),
    )
)
```

**Modified INSERT (add stop_loss and take_profits — two new columns at end of list):**
```python
cur.execute(
    """
    INSERT INTO signals
      (post_url, post_title, raw_html, ticker, action_type,
       weight_from, weight_to, reference_price, confidence,
       parse_method, scraped_at, stop_loss, take_profits)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)
    ON CONFLICT (post_url) DO NOTHING
    RETURNING id
    """,
    (
        signal_data["post_url"], signal_data["post_title"],
        signal_data["raw_html"], signal_data.get("ticker"),
        signal_data.get("action_type"), signal_data.get("weight_from"),
        signal_data.get("weight_to"), signal_data.get("reference_price"),
        signal_data.get("confidence"), signal_data.get("parse_method"),
        signal_data.get("stop_loss"),              # float | None → NUMERIC(10,2)
        signal_data.get("take_profits", []),       # list[float] → NUMERIC(10,2)[] (D-pitfall 3: use [] not None)
    )
)
```

**commit() pattern** (line 258 — unchanged): `conn.commit()` after every INSERT, same as existing.

---

### `bravos/execution/executor.py` (service, request-response)

**Analog:** `bravos/execution/executor.py` — three targeted changes: (1) extend `_load_signal()`, (2) extend `execute_signal()` branching, (3) add `_submit_bracket_order()`.

#### Change 1: `_load_signal()` — add stop_loss and take_profits columns (lines 315-335)

**Existing SELECT pattern** (lines 318-320):
```python
cur.execute(
    "SELECT ticker, action_type, weight_from, weight_to, "
    "reference_price, confidence FROM signals WHERE id = %s",
    (signal_id,),
)
```

**Modified SELECT (add two columns):**
```python
cur.execute(
    "SELECT ticker, action_type, weight_from, weight_to, "
    "reference_price, confidence, stop_loss, take_profits "
    "FROM signals WHERE id = %s",
    (signal_id,),
)
```

**Existing dict construction pattern** (lines 327-335 — extend with two new positional entries):
```python
# Existing (6 positional columns):
return {
    "ticker": row[0],
    "action_type": row[1],
    "weight_from": row[2],
    "weight_to": row[3],
    "reference_price": float(row[4]) if row[4] is not None else None,
    "confidence": row[5],
}

# Modified (8 positional columns — add rows 6 and 7):
return {
    "ticker": row[0],
    "action_type": row[1],
    "weight_from": row[2],
    "weight_to": row[3],
    "reference_price": float(row[4]) if row[4] is not None else None,
    "confidence": row[5],
    "stop_loss": float(row[6]) if row[6] is not None else None,  # Phase 9 D-03
    "take_profits": list(row[7]) if row[7] is not None else [],  # Phase 9 D-03; psycopg2 returns list
}
```

#### Change 2: `execute_signal()` — add bracket routing (insert after line 140, replacing the single `_submit_order` call)

**Existing submission call** (lines 139-144 — replace with branching):
```python
# Current single-path call (lines 139-144):
ibkr_action = _ACTION_MAP[action_type]
final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)
logger.info(
    "signal_id=%s ticker=%s action=%s quantity=%d final_status=%s",
    signal_id, ticker, ibkr_action, quantity, final_status,
)
```

**Replacement with D-10 bracket routing (copy logger.info style from lines 142-144):**
```python
ibkr_action = _ACTION_MAP[action_type]
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
    if action_type in ("open", "add") and quantity < len(take_profits):
        logger.warning(
            "signal_id=%s quantity=%d < len(take_profits)=%d — using bare MKT (D-20)",
            signal_id, quantity, len(take_profits),
        )
    elif action_type in ("open", "add") and (stop_loss is None or len(take_profits) == 0):
        logger.debug(
            "signal_id=%s: no TP/SL data — using bare MKT (fallback, D-19)",
            signal_id,
        )
    final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)

logger.info(
    "signal_id=%s ticker=%s action=%s quantity=%d final_status=%s",
    signal_id, ticker, ibkr_action, quantity, final_status,
)
```

#### Change 3: `_submit_bracket_order()` — new private function (add after `_submit_order`, before `_load_signal`)

**Analog: `_submit_order` (lines 250-310)** — copy these patterns directly:
- order_id / next_order_id increment pattern (lines 262-266)
- PENDING_SUBMISSION INSERT with `RETURNING id` (lines 269-281)
- `db_conn.commit()` after each INSERT block (line 281)
- status event slot registration before placeOrder (lines 284-285)
- `threading.Event` wait with timeout + assume-SUBMITTED fallback (lines 292-298)
- `ibapp._order_status_events.pop(order_id, None)` cleanup in finally (line 300)
- UPDATE orders SET status pattern (lines 303-309)

**New function signature and ID reservation (D-17/D-18):**
```python
def _submit_bracket_order(
    ibapp, signal_id: int, ticker: str, action: str, quantity: int,
    stop_loss: float, take_profits: list, db_conn,
) -> str:
    """
    Submit a bracket order: parent MKT (transmit=False) + STP child +
    N LMT take-profit children (OCA group, last has transmit=True).

    All N+2 IBKR order IDs are reserved upfront (D-17).
    All DB rows are written PENDING_SUBMISSION before any placeOrder call (D-08 pattern).
    Returns final DB status string for the parent order.
    """
    n = len(take_profits)

    # D-17: allocate all IDs upfront — extend existing single-order pattern (line 262-266)
    parent_ibkr_id = ibapp.next_order_id
    if parent_ibkr_id is None:
        logger.error("ibapp.next_order_id is None; cannot place bracket order")
        return "UNKNOWN"
    stp_ibkr_id = int(parent_ibkr_id) + 1
    tp_ibkr_ids = [int(parent_ibkr_id) + 2 + i for i in range(n)]
    ibapp.next_order_id = int(parent_ibkr_id) + n + 2  # reserve full block
```

**Parent order DB write — copy `_submit_order` INSERT pattern (lines 269-281):**
```python
    # 1a. Write parent PENDING_SUBMISSION FIRST (D-08 pattern from _submit_order)
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders
              (signal_id, ibkr_order_id, ticker, action, quantity,
               order_type, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, 'MKT', 'PENDING_SUBMISSION', NOW())
            RETURNING id
            """,
            (signal_id, parent_ibkr_id, ticker, action, quantity),
        )
        db_parent_order_id = cur.fetchone()[0]
    db_conn.commit()
```

**Child order DB writes — extend INSERT with parent_order_id and oca_group columns (D-16):**
```python
    child_action = "SELL" if action == "BUY" else "BUY"
    oca_group = f"TP_{parent_ibkr_id}"  # D-15

    # 1b. STP child — no OCA group (STP is outside OCA per research open question)
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders
              (signal_id, ibkr_order_id, ticker, action, quantity,
               order_type, status, submitted_at, parent_order_id, oca_group)
            VALUES (%s, %s, %s, %s, %s, 'STP', 'PENDING_SUBMISSION', NOW(), %s, NULL)
            RETURNING id
            """,
            (signal_id, stp_ibkr_id, ticker, child_action, quantity, db_parent_order_id),
        )
    db_conn.commit()

    # 1c. LMT TP children — each gets oca_group (D-16)
    base_qty = quantity // n
    for i, tp_price in enumerate(take_profits):
        is_last = (i == n - 1)
        tp_qty = base_qty if not is_last else quantity - (n - 1) * base_qty  # D-11 remainder rule
        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders
                  (signal_id, ibkr_order_id, ticker, action, quantity,
                   order_type, status, submitted_at, parent_order_id, oca_group)
                VALUES (%s, %s, %s, %s, %s, 'LMT', 'PENDING_SUBMISSION', NOW(), %s, %s)
                """,
                (signal_id, tp_ibkr_ids[i], ticker, child_action, tp_qty,
                 db_parent_order_id, oca_group),
            )
        db_conn.commit()
```

**Order object construction — extend `_build_order` pattern (lines 195-205):**
```python
    contract = _build_contract(ticker)  # reuse existing helper as-is

    # Parent: MKT, transmit=False (D-14)
    parent_order = Order()
    parent_order.action = action
    parent_order.orderType = "MKT"
    parent_order.totalQuantity = quantity
    parent_order.tif = "DAY"
    parent_order.transmit = False        # explicitly False — ibapi default is True (pitfall 2)
    parent_order.outsideRth = False      # match _build_order pattern

    # STP child: stop loss for full quantity (D-13)
    stp_order = Order()
    stp_order.action = child_action
    stp_order.orderType = "STP"
    stp_order.totalQuantity = quantity
    stp_order.auxPrice = stop_loss       # STP trigger price uses auxPrice, NOT lmtPrice (pitfall)
    stp_order.tif = "GTC"               # GTC — stop must persist beyond today's session
    stp_order.parentId = int(parent_ibkr_id)  # IBKR order ID, NOT db_parent_order_id (pitfall 1)
    stp_order.transmit = False

    # TP children: LMT GTC, OCA type 2 (D-12/D-14/D-15)
    tp_orders = []
    for i, tp_price in enumerate(take_profits):
        is_last = (i == n - 1)
        tp_qty = base_qty if not is_last else quantity - (n - 1) * base_qty
        tp_order = Order()
        tp_order.action = child_action
        tp_order.orderType = "LMT"
        tp_order.totalQuantity = tp_qty
        tp_order.lmtPrice = tp_price     # LMT uses lmtPrice, NOT auxPrice (pitfall)
        tp_order.tif = "GTC"
        tp_order.parentId = int(parent_ibkr_id)  # IBKR order ID
        tp_order.ocaGroup = oca_group
        tp_order.ocaType = 2             # REDUCE_WITH_BLOCK — D-12
        tp_order.transmit = is_last      # D-14: only last TP fires the bracket
        tp_orders.append(tp_order)
```

**Status event registration and placeOrder — copy `_submit_order` lines 283-300 for parent only:**
```python
    # 2. Register status event for parent BEFORE placeOrder (copy _submit_order lines 283-285)
    status_slot = {"event": threading.Event(), "status": None}
    ibapp._order_status_events[int(parent_ibkr_id)] = status_slot

    try:
        # Submission order: parent → STP → TP[0..N-2] → TP[N-1] (transmit=True fires all)
        ibapp.placeOrder(int(parent_ibkr_id), contract, parent_order)
        ibapp.placeOrder(stp_ibkr_id, contract, stp_order)
        for i, tp_order in enumerate(tp_orders):
            ibapp.placeOrder(tp_ibkr_ids[i], contract, tp_order)

        # Wait on parent status (copy _submit_order lines 292-298 exactly)
        got = status_slot["event"].wait(timeout=ORDER_STATUS_TIMEOUT)
        if not got:
            logger.warning(
                "orderStatus callback timed out for bracket parent order_id=%s — assuming SUBMITTED",
                parent_ibkr_id,
            )
        final_status_raw = status_slot["status"]
    finally:
        ibapp._order_status_events.pop(int(parent_ibkr_id), None)

    # 3. Map and update parent DB row (copy _submit_order lines 303-310)
    db_status = _STATUS_MAP.get(final_status_raw, "SUBMITTED")
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = %s WHERE ibkr_order_id = %s",
            (db_status, parent_ibkr_id),
        )
    db_conn.commit()
    return db_status
```

---

### `infra/migrate_phase9.sql` (migration, batch)

**Analog:** `infra/migrate_phase5.sql` (lines 1-18) — copy structure exactly.

**Pattern from migrate_phase5.sql (all 18 lines):**
```sql
-- Phase 5: Fill Capture and Position Reconciliation — schema migration
-- Verifies Phase 5 columns exist on the live DB. Safe to run even if columns
-- already exist (IF NOT EXISTS makes this idempotent).
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase5.sql

ALTER TABLE orders ADD COLUMN IF NOT EXISTS fill_price NUMERIC(10,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;

COMMENT ON COLUMN orders.fill_price IS '...';
COMMENT ON COLUMN orders.filled_at IS '...';

GRANT ALL ON orders TO bravos;
```

**New file following the same structure verbatim (per D-05 through D-09):**
```sql
-- Phase 9: Bracket Orders — schema migration
-- Verifies Phase 9 columns exist on the live DB. Safe to run even if columns
-- already exist (IF NOT EXISTS makes this idempotent).
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_phase9.sql

-- signals table: TP/SL storage (D-05, D-06)
ALTER TABLE signals ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(10,2);
ALTER TABLE signals ADD COLUMN IF NOT EXISTS take_profits NUMERIC(10,2)[] DEFAULT '{}';

-- orders table: bracket linkage (D-07, D-08)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parent_order_id INTEGER REFERENCES orders(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS oca_group TEXT;

COMMENT ON COLUMN signals.stop_loss IS 'Stop loss price from alert body (Phase 9)';
COMMENT ON COLUMN signals.take_profits IS 'Take profit levels from alert body as array (Phase 9)';
COMMENT ON COLUMN orders.parent_order_id IS 'FK to orders.id of parent order (bracket child orders only) (Phase 9)';
COMMENT ON COLUMN orders.oca_group IS 'OCA group name for TP child orders (Phase 9)';

GRANT ALL ON signals TO bravos;
GRANT ALL ON orders TO bravos;
```

---

### `tests/test_parser.py` (test, transform)

**Analog:** `tests/test_parser.py` (self) — add 6 new test functions following the existing style exactly.

**Existing test function pattern** (lines 14-19 — copy structure: import inside function, call parse_signal with fixed strings, assert single dict key):
```python
def test_extract_ticker():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. We are increasing weight from 0 to 5."
    )
    assert result["ticker"] == "EME"
```

**Existing direct import pattern for non-parse_signal tests** (lines 70-71):
```python
def test_confidence_high():
    from bravos.ingestion.parser import score_confidence
    assert score_confidence("EME", "open", 0, 5) == "high"
```

**New test stubs to add (copy structural pattern — no skip decorator needed for Wave 0 in this project):**

```python
# Wave 0 additions — Phase 9 TP/SL extraction

def test_extract_stop_loss_structured():
    from bravos.ingestion.parser import STOP_LOSS_RE
    body = "Suggested Stop Loss (SL): $257"
    m = STOP_LOSS_RE.search(body)
    assert m is not None
    raw = m.group(1) or m.group(2)
    assert float(raw) == 257.0


def test_extract_stop_loss_prose():
    from bravos.ingestion.parser import STOP_LOSS_RE
    body = "with a weight of 5, a stop at $195, and price targets of $250, $275, and $300"
    m = STOP_LOSS_RE.search(body)
    assert m is not None
    raw = m.group(1) or m.group(2)
    assert float(raw) == 195.0


def test_extract_take_profits_structured():
    from bravos.ingestion.parser import TAKE_PROFIT_RE, PRICE_IN_LIST_RE
    body = "Take Profit (TP): $320, $350, and $370"
    m = TAKE_PROFIT_RE.search(body)
    assert m is not None
    tp_string = m.group(1) or m.group(2)
    prices = [float(p) for p in PRICE_IN_LIST_RE.findall(tp_string)]
    assert prices == [320.0, 350.0, 370.0]


def test_extract_take_profits_prose():
    from bravos.ingestion.parser import TAKE_PROFIT_RE, PRICE_IN_LIST_RE
    body = "with a weight of 5, a stop at $195, and price targets of $250, $275, and $300"
    m = TAKE_PROFIT_RE.search(body)
    assert m is not None
    tp_string = m.group(1) or m.group(2)
    prices = [float(p) for p in PRICE_IN_LIST_RE.findall(tp_string)]
    assert prices == [250.0, 275.0, 300.0]


def test_parse_signal_returns_tp_sl():
    from bravos.ingestion.parser import parse_signal
    body = (
        "We are initiating a long in $AAPL at $300. "
        "Weight allocation from 0 to 5. "
        "Take Profit (TP): $320, $350, and $370\n"
        "Suggested Stop Loss (SL): $257"
    )
    result = parse_signal(
        title="Initiating Long on Apple ($AAPL) | Technical Strength",
        body=body,
    )
    assert result["stop_loss"] == 257.0
    assert result["take_profits"] == [320.0, 350.0, 370.0]


def test_parse_signal_no_tp_sl():
    from bravos.ingestion.parser import parse_signal
    result = parse_signal(
        title="Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        body="We are initiating a long position in EMCOR Group at $42.50. Weight from 0 to 5."
    )
    assert result["stop_loss"] is None
    assert result["take_profits"] == []
```

---

### `tests/test_execution.py` (test, request-response)

**Analog:** `tests/test_execution.py` (self) — add 7 new test functions following the existing style.

**Existing mock ibapp pattern** (lines 243-256 — copy MagicMock setup and placeOrder side_effect pattern):
```python
mock_ibapp = MagicMock()
order_id = int.from_bytes(os.urandom(3), "big") + 10000
mock_ibapp.next_order_id = order_id
mock_ibapp._order_status_events = {}

def capture_db_state(*args, **kwargs):
    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM orders WHERE ibkr_order_id=%s", (order_id,))
        row = cur.fetchone()
        captured_status["status"] = row[0] if row else None
mock_ibapp.placeOrder = MagicMock(side_effect=capture_db_state)
```

**Existing status callback simulation pattern** (lines 278-284):
```python
def fire_status_callback(*args, **kwargs):
    slot = mock_ibapp._order_status_events.get(1001)
    if slot is not None:
        slot["status"] = "Submitted"
        slot["event"].set()
mock_ibapp.placeOrder = MagicMock(side_effect=fire_status_callback)
```

**New test stubs to add:**

```python
# Wave 0 additions — Phase 9 bracket order tests

def _make_signal(db_conn, ticker="AAPL", action_type="open",
                  weight_from=0, weight_to=5, stop_loss=257.0,
                  take_profits=None):
    """Helper: insert a synthetic signal row with Phase 9 fields and return its id."""
    import os
    import psycopg2.extras
    if take_profits is None:
        take_profits = [320.0, 350.0, 370.0]
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signals "
            "  (post_url, post_title, ticker, action_type, weight_from, weight_to, "
            "   confidence, stop_loss, take_profits) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'high', %s, %s) RETURNING id",
            (f"http://test/{os.urandom(4).hex()}", "test", ticker, action_type,
             weight_from, weight_to, stop_loss, take_profits),
        )
        sid = cur.fetchone()[0]
    db_conn.commit()
    return sid


def test_bracket_db_writes(db_connection):
    """_submit_bracket_order writes N+2 orders rows (1 parent + 1 STP + N TP) to DB."""
    from bravos.execution.executor import _submit_bracket_order

    signal_id = _make_signal(db_connection, take_profits=[320.0, 350.0, 370.0])
    n = 3  # len(take_profits)

    mock_ibapp = MagicMock()
    start_id = int.from_bytes(os.urandom(3), "big") + 10000
    mock_ibapp.next_order_id = start_id
    mock_ibapp._order_status_events = {}

    def fire_parent_callback(order_id, *args, **kwargs):
        if order_id == start_id:
            slot = mock_ibapp._order_status_events.get(order_id)
            if slot:
                slot["status"] = "Submitted"
                slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=fire_parent_callback)

    _submit_bracket_order(
        mock_ibapp, signal_id, "AAPL", "BUY", 30,
        257.0, [320.0, 350.0, 370.0], db_connection,
    )

    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orders WHERE signal_id=%s", (signal_id,))
        count = cur.fetchone()[0]
    assert count == n + 2  # 1 parent + 1 STP + 3 TP


def test_bracket_transmit_sequence(db_connection):
    """parent transmit=False, STP transmit=False, last TP transmit=True (D-14)."""
    from bravos.execution.executor import _submit_bracket_order

    signal_id = _make_signal(db_connection, take_profits=[320.0, 350.0])
    mock_ibapp = MagicMock()
    start_id = int.from_bytes(os.urandom(3), "big") + 20000
    mock_ibapp.next_order_id = start_id
    mock_ibapp._order_status_events = {}

    captured_orders = []

    def capture_order(order_id, contract, order, *args, **kwargs):
        captured_orders.append((order_id, order.transmit))
        if order_id == start_id:
            slot = mock_ibapp._order_status_events.get(order_id)
            if slot:
                slot["status"] = "Submitted"
                slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=capture_order)

    _submit_bracket_order(
        mock_ibapp, signal_id, "AAPL", "BUY", 20,
        257.0, [320.0, 350.0], db_connection,
    )

    # parent: transmit=False; STP: transmit=False; TP[0]: transmit=False; TP[1]: transmit=True
    assert captured_orders[0] == (start_id, False)          # parent
    assert captured_orders[1] == (start_id + 1, False)      # STP
    assert captured_orders[2][1] is False                   # TP[0]
    assert captured_orders[3][1] is True                    # TP[1] — last


def test_bracket_parent_id(db_connection):
    """Child orders have parentId equal to parent IBKR order ID, NOT the DB row id (pitfall 1)."""
    from bravos.execution.executor import _submit_bracket_order

    signal_id = _make_signal(db_connection, take_profits=[320.0])
    mock_ibapp = MagicMock()
    start_id = int.from_bytes(os.urandom(3), "big") + 30000
    mock_ibapp.next_order_id = start_id
    mock_ibapp._order_status_events = {}

    child_parent_ids = []

    def capture_parent_id(order_id, contract, order, *args, **kwargs):
        if hasattr(order, "parentId") and order.parentId != 0:
            child_parent_ids.append(order.parentId)
        if order_id == start_id:
            slot = mock_ibapp._order_status_events.get(order_id)
            if slot:
                slot["status"] = "Submitted"
                slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=capture_parent_id)

    _submit_bracket_order(
        mock_ibapp, signal_id, "AAPL", "BUY", 10,
        257.0, [320.0], db_connection,
    )

    # All children must reference the IBKR parent_id, which is start_id
    assert all(pid == start_id for pid in child_parent_ids)
    assert len(child_parent_ids) == 2  # STP + 1 TP


def test_bracket_oca_group(db_connection):
    """All TP child orders have ocaGroup == f'TP_{parent_ibkr_id}' (D-15)."""
    from bravos.execution.executor import _submit_bracket_order

    signal_id = _make_signal(db_connection, take_profits=[320.0, 350.0])
    mock_ibapp = MagicMock()
    start_id = int.from_bytes(os.urandom(3), "big") + 40000
    mock_ibapp.next_order_id = start_id
    mock_ibapp._order_status_events = {}

    oca_groups_seen = []

    def capture_oca(order_id, contract, order, *args, **kwargs):
        if hasattr(order, "ocaGroup") and order.ocaGroup:
            oca_groups_seen.append(order.ocaGroup)
        if order_id == start_id:
            slot = mock_ibapp._order_status_events.get(order_id)
            if slot:
                slot["status"] = "Submitted"
                slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=capture_oca)

    _submit_bracket_order(
        mock_ibapp, signal_id, "AAPL", "BUY", 20,
        257.0, [320.0, 350.0], db_connection,
    )

    expected_group = f"TP_{start_id}"
    assert all(g == expected_group for g in oca_groups_seen)
    assert len(oca_groups_seen) == 2  # 2 TP children only; STP has no OCA group


def test_bracket_quantity_split(db_connection):
    """Equal quantity split with remainder to last TP (D-11): 10 shares, 3 TPs → 3+3+4."""
    from bravos.execution.executor import _submit_bracket_order

    signal_id = _make_signal(db_connection, take_profits=[320.0, 350.0, 370.0])
    mock_ibapp = MagicMock()
    start_id = int.from_bytes(os.urandom(3), "big") + 50000
    mock_ibapp.next_order_id = start_id
    mock_ibapp._order_status_events = {}

    tp_quantities = []

    def capture_tp_qty(order_id, contract, order, *args, **kwargs):
        # TP orders: LMT type, order_id in [start_id+2, start_id+3, start_id+4]
        if order_id in [start_id + 2, start_id + 3, start_id + 4]:
            tp_quantities.append(order.totalQuantity)
        if order_id == start_id:
            slot = mock_ibapp._order_status_events.get(order_id)
            if slot:
                slot["status"] = "Submitted"
                slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=capture_tp_qty)

    _submit_bracket_order(
        mock_ibapp, signal_id, "AAPL", "BUY", 10,
        257.0, [320.0, 350.0, 370.0], db_connection,
    )

    assert tp_quantities == [3, 3, 4]  # 10//3=3, 10//3=3, 10-2*3=4


def test_bracket_fallback_no_tpsl(db_connection):
    """No TP/SL data → execute_signal falls back to bare MKT _submit_order path (D-19)."""
    from bravos.execution.executor import execute_signal
    from unittest.mock import patch

    signal_id = _make_signal(db_connection, stop_loss=None, take_profits=[])

    mock_ibapp = MagicMock()
    mock_ibapp.is_connected.return_value = True
    mock_ibapp.next_order_id = int.from_bytes(os.urandom(3), "big") + 60000
    mock_ibapp._order_status_events = {}
    mock_ibapp._account_summary = {"NetLiquidation": "100000"}

    with patch("bravos.execution.executor._submit_bracket_order") as mock_bracket, \
         patch("bravos.execution.executor._submit_order", return_value="SUBMITTED") as mock_bare, \
         patch("bravos.execution.executor._fetch_price", return_value=300.0), \
         patch("bravos.risk.gate.RiskGate.check", return_value=(True, "pass")), \
         patch("bravos.broker.connection.ibapp", mock_ibapp):
        execute_signal(signal_id, db_connection)

    mock_bracket.assert_not_called()
    mock_bare.assert_called_once()


def test_bracket_fallback_qty_too_small(db_connection):
    """quantity < len(take_profits) → falls back to bare MKT with a warning logged (D-20)."""
    from bravos.execution.executor import execute_signal
    from unittest.mock import patch
    import logging

    # 3 TPs but NLV so tiny that quantity will be 0 or 1 (less than 3)
    signal_id = _make_signal(db_connection, weight_from=0, weight_to=1,
                              take_profits=[320.0, 350.0, 370.0])

    mock_ibapp = MagicMock()
    mock_ibapp.is_connected.return_value = True
    mock_ibapp.next_order_id = int.from_bytes(os.urandom(3), "big") + 70000
    mock_ibapp._order_status_events = {}
    # Very small NLV → quantity < 3
    mock_ibapp._account_summary = {"NetLiquidation": "100"}

    with patch("bravos.execution.executor._submit_bracket_order") as mock_bracket, \
         patch("bravos.execution.executor._submit_order", return_value="SUBMITTED") as mock_bare, \
         patch("bravos.execution.executor._fetch_price", return_value=300.0), \
         patch("bravos.risk.gate.RiskGate.check", return_value=(True, "pass")), \
         patch("bravos.broker.connection.ibapp", mock_ibapp):
        execute_signal(signal_id, db_connection)

    mock_bracket.assert_not_called()
    # _submit_order is either called (qty>=1) or not called (qty=0, skipped before submission)
    # Either way, _submit_bracket_order must NOT have been called
```

---

## Shared Patterns

### PENDING_SUBMISSION-first DB write discipline
**Source:** `bravos/execution/executor.py` lines 268-281
**Apply to:** `_submit_bracket_order()` — all N+2 order rows must be inserted before any `placeOrder` call. Copy the `INSERT ... RETURNING id` + `db_conn.commit()` pattern verbatim.

```python
# From _submit_order (lines 269-281):
with db_conn.cursor() as cur:
    cur.execute(
        """
        INSERT INTO orders
          (signal_id, ibkr_order_id, ticker, action, quantity,
           order_type, status, submitted_at)
        VALUES (%s, %s, %s, %s, %s, 'MKT', 'PENDING_SUBMISSION', NOW())
        RETURNING id
        """,
        (signal_id, order_id, ticker, action, quantity),
    )
    db_order_id = cur.fetchone()[0]
db_conn.commit()
```

### threading.Event slot + timeout pattern
**Source:** `bravos/execution/executor.py` lines 283-300
**Apply to:** `_submit_bracket_order()` — register parent slot before placeOrder; clean up in `finally`.

```python
# From _submit_order (lines 283-300):
status_slot = {"event": threading.Event(), "status": None}
ibapp._order_status_events[order_id] = status_slot
try:
    ibapp.placeOrder(order_id, contract, order)
    got = status_slot["event"].wait(timeout=ORDER_STATUS_TIMEOUT)
    if not got:
        logger.warning("orderStatus callback timed out for order_id=%s — assuming SUBMITTED", order_id)
    final_status_raw = status_slot["status"]
finally:
    ibapp._order_status_events.pop(order_id, None)
```

### _STATUS_MAP lookup
**Source:** `bravos/execution/executor.py` lines 47-52
**Apply to:** `_submit_bracket_order()` — reuse the same module-level `_STATUS_MAP` for parent order status.

```python
_STATUS_MAP = {
    "PreSubmitted": "SUBMITTED",
    "Submitted": "SUBMITTED",
    "Inactive": "REJECTED",
}
```

### Idempotent migration SQL
**Source:** `infra/migrate_phase5.sql` lines 7-8
**Apply to:** `infra/migrate_phase9.sql` — every ALTER TABLE uses `ADD COLUMN IF NOT EXISTS`.

```sql
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fill_price NUMERIC(10,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_at TIMESTAMPTZ;
```

### logger.warning with structured kwargs
**Source:** `bravos/execution/executor.py` lines 120-127
**Apply to:** All warning/error log calls in `_submit_bracket_order()` — use `%s` format args, not f-strings.

```python
logger.warning(
    "signal_id=%s ticker=%s — using reference_price=%s as price (may be stale)",
    signal_id, ticker, ref,
)
```

---

## No Analog Found

All files have close analogs in the codebase. No external references needed beyond RESEARCH.md.

---

## Metadata

**Analog search scope:** `bravos/ingestion/`, `bravos/execution/`, `infra/`, `tests/`
**Files scanned:** 7 (parser.py, executor.py, scraper.py, migrate_phase5.sql, schema.sql, test_parser.py, test_execution.py)
**Pattern extraction date:** 2026-06-01
