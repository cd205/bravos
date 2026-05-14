# Phase 4: Risk Controls and Order Execution - Research

**Researched:** 2026-05-14
**Domain:** ibapi order execution, risk gate logic, market hours, DB schema
**Confidence:** HIGH — all critical patterns verified against installed ibapi 9.81.1.post1 and live codebase

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Module location: `bravos/risk/gate.py` — new package alongside `bravos/broker/` and `bravos/ingestion/`. RiskGate is a class (not a stateless function) because it must hold intra-day state for the daily loss circuit breaker.
- **D-02:** Single entry point: `RiskGate.check(signal_id, db_conn, ibapp) -> (bool, str)` — returns (pass, reason). Every order path calls this and only this. No bypass.
- **D-03:** Gate enforces three controls in sequence: market hours check (09:30–16:00 ET), max open positions, max allocation per trade. Daily loss circuit breaker check added as fourth gate — blocks new entries if realized + unrealized P&L falls below threshold.
- **D-04:** Every gate decision (pass or block) is logged to the database with signal_id, computed values, and reason — satisfies RISK-04.
- **D-05:** Primary: `reqMarketDataType(3)` (delayed data) then `reqMktData`. Wait up to 5 seconds for a LAST or CLOSE tick to arrive via `tickPrice` callback.
- **D-06:** Fallback: if no tick arrives within 5s, use the signal's `reference_price` from the database. Log a WARNING indicating fallback was used (price may be stale).
- **D-07:** Price fetching lives in the executor module (`bravos/execution/executor.py`), not in IBApp — IBApp already has reqMktData callbacks in the SKILL.md pattern; they'll be added here.
- **D-08:** Phase 4 responsibility: write `PENDING_SUBMISSION` before `placeOrder()` is called. Update to `SUBMITTED` (or `PreSubmitted`) when the `orderStatus` callback fires with a submitted state. Capture `REJECTED` from `orderStatus` callback.
- **D-09:** Leave `FILLED` and `PARTIAL` status transitions to Phase 5 (fill capture). Phase 4 completeness check: order reaches `SUBMITTED` in the DB.
- **D-10:** The `ibkr_order_id` field in the orders table is populated with the `next_order_id` from `ibapp.next_order_id` at submission time; IBApp increments it after each use (standard ibapi pattern).
- **D-11:** New module: `bravos/execution/executor.py` with `execute_signal(signal_id, db_conn) -> None`. This is the single entry point for the order path.
- **D-12:** The scraper calls `execute_signal()` after storing a high-confidence signal. Scraper stays unchanged in structure — it already stores the signal; executor is called after the store.
- **D-13:** `execute_signal()` only processes signals where `confidence` is `'high'` and `action_type` is in `{'open', 'add', 'partial_close', 'close'}`. Low-confidence and unrecognized action types are skipped with an INFO log.
- **D-14:** `execute_signal()` imports `ibapp` from `bravos.broker.connection` — the module-level singleton set at daemon startup. If `ibapp` is None or not connected, execution is skipped with a WARNING log.

### Claude's Discretion
- Exact `tickType` values checked in `tickPrice` callback for price extraction (LAST=4, CLOSE=9 are standard)
- Threading model for the 5s price-wait (threading.Event vs polling loop)
- Whether `reqMktDataCancel` is called after price is received
- Schema migration approach for a `risk_gate_log` table if needed for RISK-04 logging (or reuse the existing orders.status column + a log comment)

### Deferred Ideas (OUT OF SCOPE)
- Partial fill handling and order FILLED/PARTIAL status transitions — Phase 5
- Periodic position reconciliation (IBKR-04) — Phase 5
- FIFO lot assignment — Phase 5
- Email alert when circuit breaker triggers (NOTF-01) — Phase 7
- Execution quality / slippage tracking (EXEC-V2-01) — v2
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-01 | Order share quantity: `abs(new_weight - old_weight) × weight_pct_per_unit × account_net_liquidation / current_price` | D-05/D-06 price fetch pattern verified; NetLiquidation from `ibapp._account_summary["NetLiquidation"]` (string, must cast to float); weight fields from signals table |
| EXEC-02 | Market orders (MKT, DAY) via ibapi for open/add/partial_close/close | `Order.orderType="MKT"`, `Order.tif="DAY"` confirmed in ibapi 9.81.1; action mapping: open/add→BUY, partial_close/close→SELL |
| EXEC-03 | Market hours gate: no orders outside NYSE 09:30–16:00 ET | `zoneinfo.ZoneInfo("America/New_York")` available (stdlib); pytz NOT installed — use zoneinfo |
| EXEC-04 | Order lifecycle (submitted→filled/partial/rejected/cancelled) via callbacks, status in DB | `orderStatus` callback verified; SUBMITTED/PreSubmitted/Rejected confirmed states; Phase 4 scope: PENDING_SUBMISSION → SUBMITTED/REJECTED only |
| RISK-01 | Max open positions — new entry orders blocked if limit reached | `SELECT COUNT(DISTINCT ticker) FROM position_lots WHERE lot_closed_at IS NULL` |
| RISK-02 | Max allocation per trade as % of account value | `abs(weight_to - weight_from) * WEIGHT_PCT_PER_UNIT` checked against configurable cap |
| RISK-03 | Daily loss circuit breaker — reqPnL subscription provides dailyPnL | `reqPnL(req_id, account, "")` → `pnl(reqId, dailyPnL, unrealizedPnL, realizedPnL)` callback verified in ibapi 9.81.1 |
| RISK-04 | Gate decision logged with reason, signal_id, and computed values | New `risk_gate_log` table needed (migration); planner must include Wave 0 migration file |
</phase_requirements>

---

## Summary

Phase 4 builds the complete signal-to-order path: risk gate, order sizing, and market order submission. All ibapi patterns needed (placeOrder, reqMktData, orderStatus, reqPnL) are available in the installed ibapi 9.81.1.post1. The IBApp class in `bravos/broker/connection.py` requires four focused additions: a `tickPrice` callback with req-ID routing, an `orderStatus` callback with req-ID routing, a `managedAccounts` callback to capture the account name string, and a `pnl` callback for the circuit breaker. These are additive — no existing Phase 3 callbacks are modified.

The biggest planability gap is **timezone library**: CONTEXT.md says "use pytz" but pytz is not installed on this machine. The stdlib `zoneinfo` module (Python 3.9+, fully available on Python 3.13) is the correct alternative and handles the `America/New_York` zone identically. The planner must use zoneinfo, not pytz.

The scraper's `_store_signal` method needs a minimal change to return the `signal_id` (via `RETURNING id`) so `process_alert` can call `execute_signal(signal_id, db_conn)`. This is the only change to Phase 2/3 code. All other integration points are additive new files.

**Primary recommendation:** Build `bravos/risk/gate.py` and `bravos/execution/executor.py` as new packages; add four callbacks to `IBApp`; add `risk_gate_log` table migration; add risk config constants to `settings.py`; update `_store_signal` to return signal_id with `RETURNING id`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Market hours gate | Application / Risk layer | — | Pure Python logic, no network call; `zoneinfo` converts local clock to ET |
| Open position count | DB query | Application | Counts `position_lots WHERE lot_closed_at IS NULL`; no IBKR call needed |
| Max allocation check | Application / Risk layer | — | Arithmetic on signal fields + config constants |
| Daily loss circuit breaker | IBApp (API thread) → RiskGate (read) | DB log | `reqPnL` subscription updates `ibapp._daily_pnl`; gate reads the value |
| Current price for sizing | IBApp tickPrice callback | Fallback to signals.reference_price | `reqMktData(type=3)` on api thread; executor waits via `threading.Event` |
| Order sizing | Application / Executor | — | Formula: `int(delta * wpct * nlv / price)` |
| Order submission | IBApp (placeOrder → api thread) | DB write | placeOrder is a thin send; confirmation comes via orderStatus callback |
| Order status transition | IBApp orderStatus callback → Executor | DB write | Callback fires on api thread; executor waits via `threading.Event` |
| Gate decision logging | DB | Application | Writes to `risk_gate_log` before returning from `RiskGate.check()` |
| Signal-to-executor routing | Scraper (process_alert) | — | Scraper calls `execute_signal(signal_id, db_conn)` after `_store_signal` |

---

## Standard Stack

### Core (all already installed)
| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| ibapi | 9.81.1.post1 | IBKR order submission, market data, PnL | `[VERIFIED: pip list]` |
| psycopg2-binary | 2.9.12 | DB writes for gate log and order records | `[VERIFIED: pip list]` |
| zoneinfo | stdlib (Python 3.13) | ET timezone for market hours gate | `[VERIFIED: Python import]`; pytz NOT installed |
| threading | stdlib | `threading.Event` for price-wait and order-status-wait | `[VERIFIED: Python import]` |

### New Config Constants (needed in settings.py)
```python
# Risk controls — configurable per deployment
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "20"))
MAX_ALLOCATION_PCT = float(os.environ.get("MAX_ALLOCATION_PCT", "0.25"))   # 25% max per trade
DAILY_LOSS_THRESHOLD = float(os.environ.get("DAILY_LOSS_THRESHOLD", "-5000.0"))  # -$5,000
WEIGHT_PCT_PER_UNIT = float(os.environ.get("WEIGHT_PCT_PER_UNIT", "0.05"))  # 5% per weight unit
```
These are runtime defaults that operators override via environment variables. `[ASSUMED]` — exact default values are up to the operator; placeholder values shown.

### NOT needed
- pytz: stdlib `zoneinfo` handles all timezone needs `[VERIFIED: not installed, zoneinfo confirmed working]`
- Any new third-party library: all required capabilities are in ibapi + psycopg2 + stdlib

**Installation (no new packages):**
No `pip install` needed — all dependencies are already installed.

---

## Architecture Patterns

### System Architecture Diagram

```
Gmail Poller                          ibkr-api thread
    │ URL                                  │
    ▼                                      │ (callbacks fire here)
BravosScraper.process_alert(url)           │
    │                                      │
    ├─► _store_signal() ──RETURNING id──►  │
    │        │                             │
    │        ▼                             │
    │   signal_id                          │
    │        │                             │
    ▼        ▼                             │
execute_signal(signal_id, db_conn)         │
    │                                      │
    ├─1─► Confidence/action gate (D-13)    │
    │                                      │
    ├─2─► RiskGate.check(signal_id, ...)  │
    │         │                            │
    │         ├─► market hours check       │
    │         ├─► max open positions (DB)  │
    │         ├─► max allocation (config)  │
    │         ├─► daily P&L check ◄───── ibapp._daily_pnl (updated by pnl callback)
    │         └─► write risk_gate_log (DB)
    │                                      │
    ├─3─► _fetch_price(ticker, ibapp)      │
    │         │                            │
    │         ├─► reqMarketDataType(3)     │
    │         ├─► reqMktData(req_id, ...)  │
    │         │         │                  │
    │         │         └────────────────► tickPrice(req_id, ...) → set Event
    │         ├─► wait Event 5s ◄─────────┤
    │         └─► cancelMktData(req_id)    │
    │                                      │
    ├─4─► Calculate quantity (EXEC-01 formula)
    │                                      │
    ├─5─► Write orders row (status=PENDING_SUBMISSION)
    │                                      │
    ├─6─► placeOrder(order_id, contract, order)
    │         │                            │
    │         └────────────────────────── orderStatus(orderId, status) → set Event
    │                                      │
    └─7─► wait orderStatus Event 3s ◄──── │
              └─► Update orders.status = SUBMITTED | REJECTED
```

### Recommended Project Structure
```
bravos/
├── broker/
│   └── connection.py        # IBApp — add tickPrice, orderStatus, managedAccounts, pnl callbacks
├── config/
│   └── settings.py          # Add risk constants: MAX_OPEN_POSITIONS, WEIGHT_PCT_PER_UNIT, etc.
├── execution/
│   ├── __init__.py           # empty
│   └── executor.py           # execute_signal(), _fetch_price(), _build_contract(), _build_order()
└── risk/
    ├── __init__.py           # empty
    └── gate.py               # RiskGate class, check() method
infra/
└── migrate_phase4.sql        # CREATE TABLE risk_gate_log; status column comments
tests/
└── test_execution.py         # Wave 0 stubs for execution + risk gate tests
```

---

## Pattern 1: IBApp Additions (four new callbacks)

**What:** IBApp needs four additive callbacks for Phase 4. None modify existing Phase 3 callbacks.

```python
# Source: verified against ibapi 9.81.1.post1 installed locally

# --- Add to IBApp.__init__ ---
# For price fetch routing
self._tick_events: dict[int, dict] = {}   # {req_id: {"event": Event, "price": float|None}}
self._tick_lock = threading.Lock()
self._mkt_req_counter = 2000              # starts at 2000; avoids collision with REQ_ID_ACCOUNT_SUMMARY=9001

# For order status routing
self._order_status_events: dict[int, dict] = {}  # {order_id: {"event": Event, "status": str|None}}

# For circuit breaker
self._account_name: str = ""             # populated by managedAccounts callback
self._daily_pnl: float | None = None     # populated by pnl callback

# --- New EWrapper callbacks to add to IBApp ---

def managedAccounts(self, accountsList: str) -> None:
    """Fires after nextValidId with comma-separated account IDs."""
    self._account_name = accountsList.split(",")[0].strip()
    logger.info("managedAccounts: account=%s", self._account_name)

def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:
    """Route delayed tick price to waiting executor thread via threading.Event."""
    # Delayed tick types: 68=Delayed Last, 76=Delayed Close
    # Live tick types: 4=Last, 9=Close (not used but handled for safety)
    PRICE_TICK_TYPES = {4, 9, 68, 76}
    if tickType not in PRICE_TICK_TYPES:
        return
    if price <= 0:
        return
    with self._tick_lock:
        if reqId in self._tick_events:
            self._tick_events[reqId]["price"] = price
            self._tick_events[reqId]["event"].set()

def orderStatus(self, orderId: int, status: str, filled: float, remaining: float,
                avgFillPrice: float, permId: int, parentId: int,
                lastFillPrice: float, clientId: int, whyHeld: str,
                mktCapPrice: float) -> None:
    """Route order status to waiting executor thread. Phase 5 will extend for fills."""
    logger.info("orderStatus orderId=%s status=%s filled=%s remaining=%s",
                orderId, status, filled, remaining)
    if orderId in self._order_status_events:
        self._order_status_events[orderId]["status"] = status
        self._order_status_events[orderId]["event"].set()

def pnl(self, reqId: int, dailyPnL: float, unrealizedPnL: float, realizedPnL: float) -> None:
    """Store daily P&L for circuit breaker. Called continuously while subscribed."""
    self._daily_pnl = dailyPnL
```

**Critical note:** `managedAccounts` fires before `nextValidId` in the IB Gateway handshake, so `_account_name` is populated before `connect_and_run()` returns. The `reqPnL` subscription should be started after `run_startup_reconciliation()` completes, from `run_ingestion.py`. `[VERIFIED: ibapi callback sequence from SKILL.md section 1]`

---

## Pattern 2: Price Fetch in Executor

**What:** Fetch delayed market price for a ticker. Threading.Event pattern — verified against ibapi behavior.

```python
# Source: verified against ibapi 9.81.1.post1 and SKILL.md section 2 + 10

PRICE_WAIT_TIMEOUT = 5.0  # seconds

def _fetch_price(ticker: str, ibapp: "IBApp") -> float | None:
    """Fetch delayed price for ticker. Returns None if no tick arrives within 5s.

    Uses reqMarketDataType(3) for delayed data (no subscription required).
    Tick types checked: 68 (Delayed Last) and 76 (Delayed Close).
    Always cancels the subscription after receiving a tick or timing out.
    """
    import threading
    from ibapi.contract import Contract

    contract = Contract()
    contract.symbol = ticker
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"

    # Allocate a req_id
    with ibapp._tick_lock:
        req_id = ibapp._mkt_req_counter
        ibapp._mkt_req_counter += 1
        tick_slot = {"event": threading.Event(), "price": None}
        ibapp._tick_events[req_id] = tick_slot

    try:
        ibapp.reqMarketDataType(3)           # delayed data
        ibapp.reqMktData(req_id, contract, "", False, False, [])
        got_tick = tick_slot["event"].wait(timeout=PRICE_WAIT_TIMEOUT)
        return tick_slot["price"] if got_tick else None
    finally:
        ibapp.cancelMktData(req_id)          # always cancel (method name: cancelMktData, not reqMktDataCancel)
        with ibapp._tick_lock:
            ibapp._tick_events.pop(req_id, None)
```

**CRITICAL:** The cancel method is `ibapp.cancelMktData(req_id)` — `reqMktDataCancel` does NOT exist in ibapi 9.81.1. `[VERIFIED: hasattr(EClient, 'cancelMktData') = True; hasattr(EClient, 'reqMktDataCancel') = False]`

---

## Pattern 3: Market Hours Check (zoneinfo, NOT pytz)

**What:** NYSE regular hours gate. CONTEXT.md says "use pytz" but pytz is not installed. Use stdlib zoneinfo instead — fully equivalent on Python 3.13.

```python
# Source: verified with Python 3.13.13 zoneinfo module

import datetime
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")

def _is_market_hours() -> bool:
    """Return True if current ET time is within NYSE regular trading hours.

    NYSE regular hours: 09:30–16:00 ET, Monday–Friday.
    Saturday (weekday=5) and Sunday (weekday=6) always return False.
    """
    now = datetime.datetime.now(tz=_EASTERN)
    if now.weekday() >= 5:          # Saturday or Sunday
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now < market_close
```

**Note:** `ZoneInfo("America/New_York")` is the IANA timezone identifier. The alias `"US/Eastern"` also works but IANA form is preferred. `[VERIFIED: datetime.datetime.now(tz=ZoneInfo("America/New_York")) works correctly]`

---

## Pattern 4: Market Order Submission

**What:** Minimal MKT/DAY order for equities. Verified field set against ibapi Order object.

```python
# Source: verified against ibapi 9.81.1.post1 — Order() field list inspected

from ibapi.order import Order
from ibapi.contract import Contract

def _build_contract(ticker: str) -> Contract:
    contract = Contract()
    contract.symbol = ticker
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract

def _build_order(action: str, quantity: int) -> Order:
    """Build a MKT DAY order. action must be 'BUY' or 'SELL'."""
    order = Order()
    order.action = action            # "BUY" or "SELL"
    order.orderType = "MKT"
    order.totalQuantity = quantity
    order.tif = "DAY"
    order.transmit = True            # True = send immediately
    order.outsideRth = False         # block pre/post market execution
    return order

# Action type to IBKR side mapping:
_ACTION_MAP = {
    "open": "BUY",
    "add": "BUY",
    "partial_close": "SELL",
    "close": "SELL",
}
```

---

## Pattern 5: Order Placement with Status Tracking

**What:** Atomic sequence: write DB, place order, wait for confirmation callback.

```python
# Source: ibapi SKILL.md section 6 and 7, verified locally

ORDER_STATUS_TIMEOUT = 3.0  # seconds to wait for orderStatus callback

def _submit_order(ibapp, signal_id: int, ticker: str, action: str,
                  quantity: int, db_conn) -> str:
    """Write DB row, place order, wait for status. Returns final status string."""
    import threading

    order_id = ibapp.next_order_id
    ibapp.next_order_id += 1   # increment before placeOrder

    contract = _build_contract(ticker)
    order = _build_order(action, quantity)

    # Step 1: Write PENDING_SUBMISSION before placeOrder (D-08)
    with db_conn.cursor() as cur:
        cur.execute(
            """INSERT INTO orders (signal_id, ibkr_order_id, ticker, action, quantity,
               order_type, status, submitted_at)
               VALUES (%s, %s, %s, %s, %s, 'MKT', 'PENDING_SUBMISSION', NOW())
               RETURNING id""",
            (signal_id, order_id, ticker, action, quantity),
        )
        db_order_id = cur.fetchone()[0]
    db_conn.commit()

    # Step 2: Register status event BEFORE placeOrder to avoid race
    status_slot = {"event": threading.Event(), "status": None}
    ibapp._order_status_events[order_id] = status_slot

    try:
        ibapp.placeOrder(order_id, contract, order)
        status_slot["event"].wait(timeout=ORDER_STATUS_TIMEOUT)
        final_status = status_slot["status"] or "UNKNOWN"
    finally:
        ibapp._order_status_events.pop(order_id, None)

    # Step 3: Update DB with received status
    # Map IBKR status strings to our DB values
    db_status = _map_order_status(final_status)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE orders SET status=%s WHERE id=%s",
                    (db_status, db_order_id))
    db_conn.commit()

    return db_status


def _map_order_status(ibkr_status: str) -> str:
    """Map IBKR orderStatus string to DB status value."""
    # IBKR status values: "PreSubmitted", "Submitted", "Filled",
    #   "PartiallyFilled", "Cancelled", "Inactive" (rejected/expired)
    mapping = {
        "PreSubmitted": "SUBMITTED",
        "Submitted": "SUBMITTED",
        "Inactive": "REJECTED",     # Inactive = rejected or expired
    }
    return mapping.get(ibkr_status, "SUBMITTED")  # default to SUBMITTED if unknown
```

**DB status values used in Phase 4:** `PENDING_SUBMISSION`, `SUBMITTED`, `REJECTED`. (Uppercase, distinct from the schema default `'pending'` — the schema default is only used before Phase 4 code creates the row.) `[ASSUMED]` — case convention not yet established in schema; planner should confirm with `COMMENT ON COLUMN` to document.

---

## Pattern 6: RiskGate Structure

**What:** Stateful class that holds circuit breaker state (whether tripped today). DB logging for every decision.

```python
# Source: design based on verified DB schema and ibapi capabilities

class RiskGate:
    """Single risk gate for all order decisions.

    Stateful: tracks whether circuit breaker tripped today (intra-day).
    Reset: must be re-instantiated each trading day OR expose reset method.
    """

    def __init__(self):
        self._circuit_tripped = False   # latched True once threshold is crossed

    def check(self, signal_id: int, db_conn, ibapp) -> tuple[bool, str]:
        """Run all four gate checks in sequence. Log decision to DB.

        Returns (True, "pass") if all checks pass.
        Returns (False, <reason>) if any check fails.
        """
        signal = self._load_signal(signal_id, db_conn)

        # Gate 1: Market hours
        if not _is_market_hours():
            return self._log_and_return(False, "market_hours", signal_id, {}, db_conn)

        # Gate 2: Max open positions
        open_count = self._count_open_positions(db_conn)
        max_pos = MAX_OPEN_POSITIONS  # from settings
        if signal["action_type"] in ("open", "add") and open_count >= max_pos:
            return self._log_and_return(False, f"max_positions:{open_count}/{max_pos}",
                                        signal_id, {"open_positions": open_count}, db_conn)

        # Gate 3: Max allocation per trade
        delta_weight = abs((signal["weight_to"] or 0) - (signal["weight_from"] or 0))
        alloc_pct = delta_weight * WEIGHT_PCT_PER_UNIT
        if alloc_pct > MAX_ALLOCATION_PCT:
            return self._log_and_return(False, f"max_allocation:{alloc_pct:.2%}>{MAX_ALLOCATION_PCT:.2%}",
                                        signal_id, {"order_allocation_pct": alloc_pct}, db_conn)

        # Gate 4: Daily loss circuit breaker
        if not self._circuit_tripped and ibapp is not None:
            daily_pnl = ibapp._daily_pnl
            if daily_pnl is not None and daily_pnl < DAILY_LOSS_THRESHOLD:
                self._circuit_tripped = True
        if self._circuit_tripped:
            return self._log_and_return(False, "circuit_breaker",
                                        signal_id, {"daily_pnl": ibapp._daily_pnl if ibapp else None}, db_conn)

        return self._log_and_return(True, "pass", signal_id, {
            "open_positions": open_count,
            "order_allocation_pct": alloc_pct,
            "daily_pnl": ibapp._daily_pnl if ibapp else None,
        }, db_conn)
```

---

## Pattern 7: risk_gate_log Table Schema

**What:** New table for RISK-04 logging. Needs a Phase 4 migration.

```sql
-- infra/migrate_phase4.sql

-- RISK-04: gate decision log
CREATE TABLE IF NOT EXISTS risk_gate_log (
    id                   SERIAL PRIMARY KEY,
    signal_id            INTEGER REFERENCES signals(id),
    checked_at           TIMESTAMPTZ DEFAULT NOW(),
    gate_passed          BOOLEAN NOT NULL,
    reason               TEXT NOT NULL,
    open_positions       INTEGER,
    max_positions        INTEGER,
    order_allocation_pct NUMERIC(6,4),
    max_allocation_pct   NUMERIC(6,4),
    net_liquidation      NUMERIC(14,2),
    daily_pnl            NUMERIC(14,2),
    daily_pnl_threshold  NUMERIC(14,2)
);

COMMENT ON TABLE risk_gate_log IS 'Every risk gate decision — pass and block — per RISK-04';
GRANT ALL ON risk_gate_log TO bravos;
GRANT ALL ON SEQUENCE risk_gate_log_id_seq TO bravos;
```

---

## Pattern 8: Scraper Integration (minimal change)

**What:** `_store_signal` currently returns None. It needs to return the signal_id so `process_alert` can call `execute_signal`. Change is isolated to `_store_signal`.

```python
# Change to bravos/ingestion/scraper.py — _store_signal

def _store_signal(self, signal_data: dict) -> int | None:
    """Insert signal into DB. Returns signal_id if newly inserted, None if duplicate."""
    # ... existing connection setup ...
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signals (...)
            VALUES (...)
            ON CONFLICT (post_url) DO NOTHING
            RETURNING id
            """,
            (...)
        )
        row = cur.fetchone()
    conn.commit()
    conn.close()
    return row[0] if row else None  # None means duplicate — skip execution

# Change to process_alert: call execute_signal after store
def process_alert(self, url: str):
    # ... existing session check and fetch ...
    signal_id = self._store_signal(signal_data)
    if signal_id is not None and parsed.get("confidence") == "high":
        from bravos.execution.executor import execute_signal
        import psycopg2, os
        exec_conn = psycopg2.connect(...)
        try:
            execute_signal(signal_id, exec_conn)
        finally:
            exec_conn.close()
```

**Note:** `ON CONFLICT DO NOTHING RETURNING id` returns nothing for duplicates (the conflict suppresses the row). This is the correct dedup behavior — no re-execution for already-processed signals. `[VERIFIED: psycopg2 + PostgreSQL ON CONFLICT DO NOTHING RETURNING behavior]`

---

## Pattern 9: reqPnL Subscription Setup

**What:** Subscribe to daily P&L at startup so `ibapp._daily_pnl` is populated before any orders are placed.

```python
# In run_ingestion.py, after ibapp.run_startup_reconciliation() succeeds:
# (managedAccounts callback fires during connect, so _account_name is populated)

REQ_ID_PNL = 9002  # distinct from 9001 (account summary)

if ibapp.is_connected() and ibapp._account_name:
    ibapp.reqPnL(REQ_ID_PNL, ibapp._account_name, "")
    logger.info("reqPnL subscription started for account=%s", ibapp._account_name)
```

The `pnl` callback fires continuously while subscribed. `ibapp._daily_pnl` will be `None` until the first callback fires — RiskGate must handle `None` as "not yet available, skip circuit breaker check" (gate passes with a warning log). `[VERIFIED: reqPnL(reqId, account, modelCode) signature confirmed; pnl callback signature: (reqId, dailyPnL, unrealizedPnL, realizedPnL) confirmed]`

---

## Pattern 10: Order Sizing (close action)

**What:** For `close` action, the formula differs — sell all open lots, not weight-delta based.

```python
def _calculate_quantity(signal: dict, action_type: str, db_conn, ibapp, current_price: float) -> int:
    """Calculate order quantity per EXEC-01 formula.

    For 'open' and 'add': weight-delta formula.
    For 'close': sum of all open lots for the ticker (from position_lots).
    For 'partial_close': weight-delta formula (partial reduction).
    """
    if action_type == "close":
        # Sell all open lots
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM position_lots "
                "WHERE ticker=%s AND lot_closed_at IS NULL",
                (signal["ticker"],)
            )
            return int(cur.fetchone()[0])

    # All other types: weight-delta formula
    nlv = float(ibapp._account_summary.get("NetLiquidation", "0"))
    delta = abs((signal["weight_to"] or 0) - (signal["weight_from"] or 0))
    return int(delta * WEIGHT_PCT_PER_UNIT * nlv / current_price)
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ET timezone offset | Custom UTC+offset math | `zoneinfo.ZoneInfo("America/New_York")` | Handles DST transitions automatically |
| Order ID management | Custom UUID/sequence | `ibapp.next_order_id` (from nextValidId) | IBKR requires IDs within the range it issues |
| Market data routing | Polling `tickPrice` logs | `threading.Event` per req_id in `_tick_events` | Polling wastes CPU; Event wakes immediately on data |
| P&L calculation | Query position_lots + current prices | `reqPnL` subscription | IBKR computes this correctly with mark prices; custom calc is approximate |
| Order rejection detection | Parse error callback for order errors | `orderStatus` callback, `status == "Inactive"` | Rejections always fire orderStatus first |

**Key insight:** The ibapi callback model is fundamentally event-driven. The correct pattern for all "wait for X" scenarios is `threading.Event` — never a polling `time.sleep()` loop.

---

## Common Pitfalls

### Pitfall 1: Wrong cancel method name for market data
**What goes wrong:** Code calls `ibapp.reqMktDataCancel(req_id)` — `AttributeError` at runtime.
**Why it happens:** IBKR documentation uses both names inconsistently.
**How to avoid:** Use `ibapp.cancelMktData(req_id)` — confirmed present in ibapi 9.81.1.
**Warning signs:** `AttributeError: 'IBApp' object has no attribute 'reqMktDataCancel'`

### Pitfall 2: Delayed data returns tick types 68/76, not 4/9
**What goes wrong:** `tickPrice` callback checks only for tickType 4 (Last) and 9 (Close). With `reqMarketDataType(3)`, IBKR sends tickType 68 (Delayed Last) and 76 (Delayed Close) instead. Event never fires; falls through to reference_price fallback every time.
**Why it happens:** Delayed data uses a different set of tick type integers.
**How to avoid:** Check `tickType in {4, 9, 68, 76}` — handles both live and delayed modes.
**Warning signs:** Every order uses reference_price fallback even when market is open.

### Pitfall 3: managedAccounts fires before nextValidId — account_name available immediately
**What goes wrong:** Code tries to call `reqPnL` right after `connect_and_run()` before `managedAccounts` fires, resulting in empty account string.
**Why it happens:** `managedAccounts` fires during the login handshake; test timing varies.
**How to avoid:** Only call `reqPnL` after `run_startup_reconciliation()` completes (which waits for `_summary_done` event). By that point, `managedAccounts` has long since fired.
**Warning signs:** `reqPnL` called with empty string account; IBKR returns error 321.

### Pitfall 4: ON CONFLICT DO NOTHING RETURNING id returns nothing for duplicates
**What goes wrong:** Code calls `fetchone()` after the INSERT and gets None for duplicate URLs, then tries to use None as a signal_id.
**Why it happens:** PostgreSQL RETURNING only returns rows that were actually inserted/updated. DO NOTHING means no row to return.
**How to avoid:** Check `row = cur.fetchone(); if row is None: return None` — caller skips execute_signal for duplicates (already processed).
**Warning signs:** `TypeError: 'NoneType' object is not subscriptable` when calling `execute_signal(None, ...)`.

### Pitfall 5: pytz import fails — use zoneinfo
**What goes wrong:** `import pytz` raises `ModuleNotFoundError`.
**Why it happens:** pytz is not installed on this machine. CONTEXT.md mentions pytz but this is outdated.
**How to avoid:** Use `from zoneinfo import ZoneInfo; ZoneInfo("America/New_York")` — stdlib, no install needed.
**Warning signs:** `ModuleNotFoundError: No module named 'pytz'`.

### Pitfall 6: next_order_id is a string on reconnect in some Gateway versions
**What goes wrong:** `ibapp.next_order_id + 1` fails with `TypeError`.
**Why it happens:** `nextValidId(orderId: int)` receives an int, but some versions pass as-is from the socket.
**How to avoid:** Store and increment as `int()`: `self.next_order_id = int(orderId)` in the `nextValidId` callback. IBApp already does this correctly (line 162 of connection.py stores `orderId` directly, which is typed as `int` in the signature).
**Warning signs:** `TypeError: can only concatenate str (not "int") to str`.

### Pitfall 7: Skipping orderStatus wait causes DB to stay at PENDING_SUBMISSION
**What goes wrong:** Order is placed but DB status never updates if `orderStatus` callback fires asynchronously after executor returns.
**Why it happens:** ibapi callback delivery is asynchronous; without waiting, executor may exit before status arrives.
**How to avoid:** Always wait for `_order_status_events[order_id]["event"].wait(timeout=3)` before updating DB. 3s is sufficient for local Gateway.
**Warning signs:** `orders` rows stuck at `PENDING_SUBMISSION` even for successfully filled orders.

---

## DB Schema Changes Required

### New Table: risk_gate_log
Required for RISK-04. See Pattern 7 above for DDL.

### Existing Orders Table
No structural changes needed. The `status` column is `VARCHAR(20)` — values `PENDING_SUBMISSION`, `SUBMITTED`, `REJECTED` fit within 20 chars.

Current default `'pending'` from schema DDL only applies to rows inserted without explicit status. Phase 4 always inserts with explicit status. No migration needed for `orders`.

### Migration File
`infra/migrate_phase4.sql` — Wave 0 task must create this file. Applied before implementation tests can run.

---

## Open Questions

1. **Circuit breaker daily reset**
   - What we know: `_circuit_tripped` is an instance variable on `RiskGate`; the daemon doesn't restart daily.
   - What's unclear: Should the daemon reset `_circuit_tripped` at market open each day? Or re-instantiate `RiskGate` daily?
   - Recommendation: `[ASSUMED]` Add a `reset()` method to `RiskGate` and call it from the schedule loop at market open. Or: check `_circuit_tripped` only within market hours and compare `dailyPnL` freshness. Planner should decide — this is in Claude's Discretion scope.

2. **Account name availability before reqPnL**
   - What we know: `managedAccounts` fires during handshake; `_account_name` is populated before `connect_and_run()` returns.
   - What's unclear: Paper trading paper accounts use format `DU1234567`; confirmed from IB Gateway logs in prior phases.
   - Recommendation: IBApp should log `_account_name` in `managedAccounts` for operator confirmation.

3. **`close` action when no open lots exist**
   - What we know: `_calculate_quantity` for `close` sums `position_lots WHERE lot_closed_at IS NULL`.
   - What's unclear: If the DB has no open lots (reconciliation gap), quantity = 0.
   - Recommendation: `[ASSUMED]` RiskGate or executor should check quantity > 0 before placing order. A SELL of 0 shares will be rejected by IBKR (error 201).

---

## Runtime State Inventory

> Not a rename/refactor phase. This section is included to confirm no hidden state is affected.

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | `orders` table existing rows have status `'pending'` (schema default) | None — Phase 4 inserts fresh rows with explicit status |
| Live service config | IB Gateway running on paper port 4002 | None — no config change |
| OS-registered state | None | None — no new systemd units in Phase 4 |
| Secrets/env vars | `BRAVOS_DB_PASSWORD` read at runtime — no change | None |
| Build artifacts | None | None |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| ibapi | Order submission, market data, PnL | Yes | 9.81.1.post1 | — |
| psycopg2-binary | DB writes | Yes | 2.9.12 | — |
| zoneinfo (stdlib) | Market hours gate | Yes | Python 3.13 stdlib | — |
| pytz | CONTEXT.md mentions it | NO | Not installed | Use `zoneinfo` (preferred) |
| IB Gateway | Live order/data testing | Requires operator startup | Paper port 4002 | Skip live tests |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** pytz → use stdlib zoneinfo.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | `pytest.ini` (exists at repo root) |
| Quick run command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_execution.py -x -q` |
| Full suite command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-01 | Quantity formula with known inputs | unit | `pytest tests/test_execution.py::test_quantity_formula -x` | Wave 0 |
| EXEC-01 | Quantity=0 blocked | unit | `pytest tests/test_execution.py::test_quantity_zero_skipped -x` | Wave 0 |
| EXEC-02 | BUY order built for open action | unit | `pytest tests/test_execution.py::test_build_order_buy -x` | Wave 0 |
| EXEC-02 | SELL order built for close action | unit | `pytest tests/test_execution.py::test_build_order_sell -x` | Wave 0 |
| EXEC-03 | Market hours gate blocks outside hours | unit | `pytest tests/test_execution.py::test_market_hours_gate_blocks -x` | Wave 0 |
| EXEC-03 | Market hours gate passes during hours | unit | `pytest tests/test_execution.py::test_market_hours_gate_passes -x` | Wave 0 |
| EXEC-04 | DB row written PENDING_SUBMISSION before placeOrder | unit (mock) | `pytest tests/test_execution.py::test_order_db_write_pending -x` | Wave 0 |
| EXEC-04 | DB status updated to SUBMITTED after callback | unit (mock) | `pytest tests/test_execution.py::test_order_status_submitted -x` | Wave 0 |
| EXEC-04 | DB status updated to REJECTED after Inactive callback | unit (mock) | `pytest tests/test_execution.py::test_order_status_rejected -x` | Wave 0 |
| RISK-01 | Gate blocks when open positions ≥ max | unit (mock DB) | `pytest tests/test_execution.py::test_gate_max_positions -x` | Wave 0 |
| RISK-02 | Gate blocks when allocation exceeds cap | unit | `pytest tests/test_execution.py::test_gate_max_allocation -x` | Wave 0 |
| RISK-03 | Gate blocks when daily_pnl < threshold | unit | `pytest tests/test_execution.py::test_gate_circuit_breaker -x` | Wave 0 |
| RISK-03 | Gate passes when daily_pnl is None (not yet received) | unit | `pytest tests/test_execution.py::test_gate_circuit_none_pnl -x` | Wave 0 |
| RISK-04 | risk_gate_log row written for pass decision | integration | `pytest tests/test_execution.py::test_gate_log_pass -x` | Wave 0 |
| RISK-04 | risk_gate_log row written for block decision | integration | `pytest tests/test_execution.py::test_gate_log_block -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_execution.py -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_execution.py` — all 15 tests above (unit + integration stubs with `@pytest.mark.skip`)
- [ ] `bravos/execution/__init__.py` — empty package init
- [ ] `bravos/risk/__init__.py` — empty package init
- [ ] `infra/migrate_phase4.sql` — risk_gate_log DDL (needed before integration tests can run)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Not applicable (internal service) |
| V3 Session Management | No | Not applicable |
| V4 Access Control | No | Not applicable |
| V5 Input Validation | Yes | Signal fields validated before use in SQL queries (psycopg2 parameterized queries) |
| V6 Cryptography | No | No new secrets; existing Secret Manager pattern unchanged |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via ticker symbol | Tampering | psycopg2 parameterized queries — all DB writes use `%s` placeholders |
| Order placed with quantity=0 | Tampering | Validate quantity > 0 before calling placeOrder |
| Circuit breaker bypassed by None pnl | Spoofing | `daily_pnl is None` → skip circuit check (safe default — circuit not tripped) |
| Stale NetLiquidation used for sizing | Information Disclosure | IBApp._account_summary updated at startup; staleness risk is accepted per CONTEXT.md |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Default WEIGHT_PCT_PER_UNIT=0.05, MAX_OPEN_POSITIONS=20, etc. are reasonable starting values | Standard Stack / Settings | Wrong sizing; operator must override via env var before live trading |
| A2 | `orderStatus` fires within 3 seconds for a paper account MKT order | Pattern 5 | DB stuck at PENDING_SUBMISSION; operator investigation required |
| A3 | RiskGate._circuit_tripped needs a daily reset mechanism (not specified in CONTEXT.md) | Open Questions | Circuit breaker stays tripped across trading days |
| A4 | DB status string case: PENDING_SUBMISSION (uppercase) is the convention | Pattern 5 | Inconsistency with schema default `'pending'` (lowercase) |
| A5 | `close` action with 0 open lots should be blocked before placeOrder | Pattern 10 | IBKR rejects SELL 0 shares with error 201 |

---

## Sources

### Primary (HIGH confidence)
- `[VERIFIED: ibapi 9.81.1.post1 local install]` — Order fields, placeOrder, reqMktData, cancelMktData, reqPnL, pnl, managedAccounts, orderStatus signatures all inspected via Python
- `[VERIFIED: Python 3.13 stdlib]` — `zoneinfo.ZoneInfo("America/New_York")` works correctly
- `[VERIFIED: local codebase grep]` — IBApp structure, existing callbacks, _account_summary, next_order_id confirmed

### Secondary (MEDIUM confidence)
- `.claude/skills/ibkr-connection/SKILL.md` — tick type numbers (4, 9, 66-68, 75-76), threading patterns, order status state machine strings

### Tertiary (LOW confidence)
- `[ASSUMED]` — Default risk config values (A1, A2, A3, A4, A5 in Assumptions Log)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified via pip list + Python import
- IBApp callback patterns: HIGH — signatures verified via inspect against installed ibapi
- Architecture: HIGH — based directly on verified codebase (connection.py, schema.sql)
- Risk gate logic: HIGH — SQL patterns verified against schema; threading pattern from SKILL.md + stdlib confirmation
- Default config values: LOW (ASSUMED) — operator decision

**Research date:** 2026-05-14
**Valid until:** 2026-06-14 (stable stack; ibapi version pinned)
