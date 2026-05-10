# Phase 3: IBKR Connection - Research

**Researched:** 2026-05-10
**Domain:** Interactive Brokers ibapi — EWrapper/EClient combined class, heartbeat/reconnect, startup reconciliation, daemon thread integration
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Connection Architecture**
- D-01: Pattern B: combined class — single `IBApp(EWrapper, EClient)` class, not separate wrapper/client/app classes.
- D-02: Module location: `bravos/broker/` package (new). Files: `__init__.py`, `connection.py` (IBApp class), tests in `tests/test_broker.py`.
- D-03: Exposure pattern: Claude's discretion.

**Reconnect Strategy**
- D-04: Detection: both mechanisms active — heartbeat (reqCurrentTime every 60s) + error codes (504, 1100, 2110).
- D-05: Heartbeat timeout: 10 seconds.
- D-06: Force-reconnect sequence: `disconnect()` → wait 5s → fresh connection.
- D-07: Retry: 5 attempts exponential backoff (5,10,20,40,80s); after 5 failures, CRITICAL log, retry every 60s forever.

**Startup Reconciliation**
- D-08: Mismatch handling: flag discrepancies, log WARNING, don't overwrite DB. Operator reviews logs.
- D-09: Reconciliation scope: reqPositions + reqOpenOrders + reqAccountSummary.
- D-10: Write snapshot to `broker_positions_snapshot` table.
- D-11: Reconciliation must complete before ingestion schedule loop starts.

**Thread / Process Model**
- D-12: Same process as ingestion daemon — IBKR connection runs as background thread inside `scripts/run_ingestion.py`.
- D-13: IBKR thread starts at daemon startup, before the schedule loop.
- D-14: If initial 5 attempts fail: log CRITICAL, start ingestion loop anyway, keep retrying IBKR in background.

### Claude's Discretion
- Exact exposure pattern for IBApp instance (singleton module-level vs dependency injection)
- reqCurrentTime heartbeat implementation (threading.Timer vs schedule library vs dedicated thread)
- Error code handling granularity (which codes trigger immediate reconnect vs log-only)
- Test approach (mock EWrapper vs integration test with real Gateway)

### Deferred Ideas (OUT OF SCOPE)
- Periodic position reconciliation during trading day (IBKR-04) — Phase 5
- Account summary streaming for real-time P&L — Phase 7 dashboard
- Email notification on IBKR disconnect not auto-recovered — NOTF-02, Phase 7
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| IBKR-01 | Persistent IBKR connection with heartbeat every 60s; 2FA only at initial startup | D-04/D-05: reqCurrentTime + 10s timeout pattern; daemon thread model |
| IBKR-02 | Detect stale/dead connections (CLOSE-WAIT) and force reconnect automatically | D-06/D-07: disconnect→5s→fresh connect; error codes 504/1100 trigger immediate reconnect |
| IBKR-03 | On startup, reconcile IBKR positions and open orders with DB before entering scrape/execute loop | D-09/D-10/D-11: reqPositions+reqOpenOrders+reqAccountSummary; snapshot write; blocks schedule loop |
| IBKR-05 | Support paper (port 4002) and live (port 4001) via config toggle — no code change | settings.py `get_ibkr_port()` + `TRADING_MODE` env var — already implemented |
</phase_requirements>

---

## Summary

Phase 3 builds a single `IBApp(EWrapper, EClient)` class that runs in a daemon background thread inside the existing `scripts/run_ingestion.py` process. The class handles the full IBKR connection lifecycle: initial connect, startup reconciliation (positions + open orders + account summary), persistent heartbeat, and self-healing reconnect. All ibapi callback sequences are well-documented in the project skill; the primary research challenge is threading architecture and reconnect state management.

The heartbeat is best implemented as a **dedicated monitoring thread** (not threading.Timer, not the schedule library) that runs an infinite loop checking a `_last_heartbeat_at` timestamp. This separates heartbeat logic from the ibapi EClient.run() message loop cleanly. The `EClient.run()` loop runs in its own daemon thread. These are the two permanent background threads inside the ingestion daemon process.

The cleanest exposure pattern for Phase 4 compatibility is a **module-level singleton** in `bravos/broker/connection.py`. Phase 4 imports `from bravos.broker.connection import ibapp` and calls `ibapp.place_order(...)` directly, with no dependency injection plumbing needed. This matches the existing `bravos/ingestion/scraper.py` module-level `_scraper` pattern already established in Phase 2.

**Primary recommendation:** Dedicated heartbeat-monitor thread + module-level singleton IBApp + threading.Event for reconciliation gate.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| ibapi | Match Gateway version | EWrapper/EClient interface to IB Gateway | Only official IBKR Python API; not on PyPI — install from IB developer portal |
| threading | stdlib | Daemon threads for EClient.run() and heartbeat monitor | ibapi requires run() in separate thread; heartbeat monitor is a second daemon thread |
| queue | stdlib | Thread-safe communication from EWrapper callbacks back to main thread | Required pattern: callbacks fire on API thread, never call blocking code from them |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| psycopg2-binary | 2.9.x | Write broker_positions_snapshot and read position_lots/orders for reconciliation | Every reconciliation run |
| bravos.config.settings | project | IBKR_HOST, IBKR_CLIENT_ID, get_ibkr_port(), TRADING_MODE | Use directly; already imports env vars and get_ibkr_port() helper |
| bravos.config.secrets_config | project | get_secret() for GCP Secret Manager | Load IBKR credentials at startup if needed |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Dedicated heartbeat thread | threading.Timer | Timer fires once; requires reschedule on every call; more error-prone on reconnect cycles |
| Dedicated heartbeat thread | schedule library | schedule runs on the main thread loop; can't fire during a blocking reconnect attempt |
| Module-level singleton | Dependency injection (pass ibapp to every caller) | DI adds boilerplate; singleton is sufficient for single-process, single-account system |

**Installation note:** ibapi is NOT on PyPI. Download from IB developer portal at your Gateway version, then `pip install ibapi/` from the extracted directory. Verify version matches Gateway to avoid protocol mismatch (error 507).

---

## Architecture Patterns

### Recommended Project Structure

```
bravos/
├── broker/
│   ├── __init__.py          # empty
│   └── connection.py        # IBApp class + module-level singleton ibapp
scripts/
└── run_ingestion.py         # existing; add IBApp startup before schedule loop
tests/
└── test_broker.py           # new; Wave 0 stubs for IBKR-01/02/03/05
```

### Pattern 1: Combined IBApp Class (EWrapper + EClient)

**What:** Single class inheriting both EWrapper and EClient. EClient is initialized with `self` as the wrapper argument. EClient.run() runs in a dedicated daemon thread.

**When to use:** Always — D-01 mandates Pattern B.

```python
# Source: .claude/skills/ibkr-connection/SKILL.md § Pattern B
from ibapi.wrapper import EWrapper
from ibapi.client import EClient
import threading, queue, time, logging

logger = logging.getLogger(__name__)

class IBApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)          # pass self as wrapper
        self._next_req_id = 1
        self._lock = threading.Lock()

        # Connection state
        self.next_order_id: int | None = None
        self._connected = threading.Event()   # set when nextValidId fires
        self._last_heartbeat_at: float = 0.0

        # Reconciliation state
        self._positions: list[dict] = []
        self._open_orders: list[dict] = []
        self._account_summary: dict = {}
        self._positions_done = threading.Event()
        self._orders_done = threading.Event()
        self._summary_done = threading.Event()

        # Heartbeat monitor thread (started after connect)
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
```

### Pattern 2: Connection Handshake

**What:** `EClient.connect()` opens TCP socket; `nextValidId` callback fires to confirm API is ready. This is the only reliable "connection confirmed" signal.

**Callback sequence on startup:**

```
EClient.connect() called
  → TCP socket opens to Gateway
  → Server sends connection ACK
  → nextValidId(orderId) fires on API thread    ← confirms connection
  → error() may fire with codes 2104/2106/2158  ← informational only
```

```python
def nextValidId(self, orderId: int):
    """Connection confirmed. orderId is the starting order ID for this session."""
    self.next_order_id = orderId
    self._last_heartbeat_at = time.monotonic()
    self._connected.set()
    logger.info("IBKR connected — nextValidId=%d", orderId)

def connect_and_run(self, host: str, port: int, client_id: int, timeout: float = 30) -> bool:
    """Connect and start the EClient.run() loop in a daemon thread."""
    self.connect(host, port, client_id)
    api_thread = threading.Thread(target=self.run, daemon=True, name="ibkr-api")
    api_thread.start()
    # Block until nextValidId fires (connection confirmed) or timeout
    if not self._connected.wait(timeout):
        logger.error("IBKR connection timed out after %ds — nextValidId never fired", timeout)
        return False
    return True
```

### Pattern 3: reqCurrentTime Heartbeat

**What:** Every 60s, call `reqCurrentTime()`. The `currentTime()` callback updates `_last_heartbeat_at`. A monitor thread checks whether the last heartbeat is stale by >10s. If stale, declare connection dead and begin force-reconnect.

**Why dedicated thread (not Timer, not schedule):**
- threading.Timer requires reschedule after each firing; fails silently after reconnect if not properly restarted
- schedule library runs in the main thread loop (`time.sleep(1)` iteration); a blocking reconnect attempt would prevent schedule from running, starving the ingestion health check
- A dedicated `while not _stop_event.wait(timeout=60)` loop is self-contained and survives reconnect cycles

```python
def currentTime(self, time_val: int):
    """Heartbeat response from reqCurrentTime()."""
    self._last_heartbeat_at = time.monotonic()

def _heartbeat_monitor(self):
    """Daemon thread: send heartbeat every 60s, trigger reconnect if stale."""
    HEARTBEAT_INTERVAL = 60    # seconds between reqCurrentTime calls
    HEARTBEAT_TIMEOUT = 10     # seconds to wait for currentTime() response

    while not self._stop_event.is_set():
        if self._connected.is_set():
            self.reqCurrentTime()
            # Wait for currentTime() to update _last_heartbeat_at
            time.sleep(HEARTBEAT_TIMEOUT)
            elapsed = time.monotonic() - self._last_heartbeat_at
            if elapsed > HEARTBEAT_TIMEOUT:
                logger.warning("Heartbeat timeout (%.1fs since last response) — forcing reconnect", elapsed)
                self._trigger_reconnect("heartbeat_timeout")
        # Sleep until next check
        self._stop_event.wait(timeout=HEARTBEAT_INTERVAL)
```

### Pattern 4: Error Code Handling

**What:** EWrapper.error() fires for all error conditions. Error codes fall into three groups for this phase:

| Code | Meaning | Action |
|------|---------|--------|
| 504 | Not connected | Immediate reconnect |
| 1100 | Connectivity lost (Gateway/network drop) | Immediate reconnect |
| 2110 | Connectivity restored | Log INFO — already reconnecting or Gateway self-recovered |
| 501 | Duplicate clientId | CRITICAL log — config error, do not retry without fix |
| 502 | Connection refused — Gateway not running | Retry with backoff (Gateway may be restarting) |
| 507 | Bad message length — version mismatch | CRITICAL log — ibapi version mismatch, do not retry |
| 2104, 2106, 2119, 2158 | Market data farm status | Ignore — informational |

```python
_IMMEDIATE_RECONNECT_CODES = {504, 1100}
_IGNORE_CODES = {2104, 2106, 2119, 2158, 2110}
_CRITICAL_NO_RETRY_CODES = {501, 507}

def error(self, reqId: int, errorCode: int, errorString: str,
          advancedOrderRejectJson: str = ""):
    if errorCode in _IGNORE_CODES:
        if errorCode == 2110:
            logger.info("IBKR connectivity restored (code 2110)")
        return
    if errorCode in _CRITICAL_NO_RETRY_CODES:
        logger.critical("IBKR fatal error %d: %s — check config, not retrying", errorCode, errorString)
        return
    if errorCode in _IMMEDIATE_RECONNECT_CODES:
        logger.warning("IBKR connection error %d: %s — triggering immediate reconnect", errorCode, errorString)
        self._trigger_reconnect(f"error_{errorCode}")
        return
    if reqId == -1:
        logger.error("IBKR system error %d: %s", errorCode, errorString)
    else:
        logger.warning("IBKR req %d error %d: %s", reqId, errorCode, errorString)
```

### Pattern 5: Force-Reconnect with Exponential Backoff (D-06, D-07)

**What:** Disconnect cleanly, wait 5s (CLOSE-WAIT drain), create fresh connection. Retry 5 times with exponential backoff. After 5 failures, log CRITICAL and retry every 60s forever.

```python
_RETRY_DELAYS = [5, 10, 20, 40, 80]   # seconds; D-07

def _trigger_reconnect(self, reason: str):
    """Non-blocking — starts reconnect in a background thread so error() callback returns."""
    if self._reconnecting:
        return   # already in progress
    self._reconnecting = True
    t = threading.Thread(target=self._reconnect_loop, args=(reason,), daemon=True, name="ibkr-reconnect")
    t.start()

def _reconnect_loop(self, reason: str):
    """Exponential backoff reconnect. Runs in a background thread."""
    host = self._host
    port = self._port
    client_id = self._client_id
    attempt = 0
    while not self._stop_event.is_set():
        delay = _RETRY_DELAYS[attempt] if attempt < len(_RETRY_DELAYS) else 60
        logger.info("IBKR reconnect attempt %d/%d — reason: %s — waiting %ds",
                    attempt + 1, len(_RETRY_DELAYS), reason, delay)
        # D-06: disconnect → 5s → fresh connection
        try:
            self.disconnect()
        except Exception:
            pass
        self._connected.clear()
        time.sleep(5)   # CLOSE-WAIT drain window
        time.sleep(max(0, delay - 5))   # remaining backoff
        if self.connect_and_run(host, port, client_id):
            logger.info("IBKR reconnected successfully on attempt %d", attempt + 1)
            self._reconnecting = False
            return
        attempt += 1
        if attempt == len(_RETRY_DELAYS):
            logger.critical("IBKR reconnect failed after %d attempts — will retry every 60s", len(_RETRY_DELAYS))
    self._reconnecting = False
```

### Pattern 6: Startup Reconciliation Sequence (D-09)

**What:** After `nextValidId` fires, issue three requests. Use `threading.Event` to signal completion of each. Reconciliation runs synchronously (blocking the calling code) until all three events are set or a timeout fires.

**Callback sequences:**

```
reqPositions()        → position() per holding → positionEnd()
reqOpenOrders()       → openOrder() + orderStatus() per order → openOrderEnd()
reqAccountSummary()   → accountSummary() per tag → accountSummaryEnd()
```

```python
REQ_ID_ACCOUNT_SUMMARY = 9001   # Fixed req ID for account summary

def position(self, account: str, contract, position: float, avgCost: float):
    self._positions.append({
        "ticker": contract.symbol,
        "sec_type": contract.secType,
        "position": int(position),
        "avg_cost": avgCost,
        "account": account,
    })

def positionEnd(self):
    self._positions_done.set()
    logger.info("reqPositions complete — %d positions received", len(self._positions))

def openOrder(self, orderId: int, contract, order, orderState):
    self._open_orders.append({
        "order_id": orderId,
        "ticker": contract.symbol,
        "action": order.action,
        "quantity": order.totalQuantity,
        "order_type": order.orderType,
        "status": orderState.status,
    })

def openOrderEnd(self):
    self._orders_done.set()
    logger.info("reqOpenOrders complete — %d open orders received", len(self._open_orders))

def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
    self._account_summary[tag] = {"value": value, "currency": currency, "account": account}

def accountSummaryEnd(self, reqId: int):
    self._summary_done.set()
    logger.info("reqAccountSummary complete — %d tags received", len(self._account_summary))

def run_startup_reconciliation(self, db_conn, timeout: float = 30) -> bool:
    """Fetch positions, open orders, and account summary. Write snapshot. Log discrepancies.

    Returns True if reconciliation completed within timeout.
    Blocks until done — call this before starting the ingestion schedule loop (D-11).
    """
    self._positions.clear()
    self._open_orders.clear()
    self._account_summary.clear()
    self._positions_done.clear()
    self._orders_done.clear()
    self._summary_done.clear()

    self.reqPositions()
    self.reqOpenOrders()
    self.reqAccountSummary(REQ_ID_ACCOUNT_SUMMARY, "All", "NetLiquidation,TotalCashValue,AvailableFunds")

    done = (
        self._positions_done.wait(timeout) and
        self._orders_done.wait(timeout) and
        self._summary_done.wait(timeout)
    )
    if not done:
        logger.error("Reconciliation timed out after %ds — partial data", timeout)
        return False

    _write_position_snapshot(db_conn, self._positions)
    _reconcile_against_db(db_conn, self._positions, self._open_orders)
    return True
```

### Pattern 7: Snapshot Write to broker_positions_snapshot

```python
def _write_position_snapshot(db_conn, positions: list[dict]) -> None:
    """Write one row per IBKR position to broker_positions_snapshot (D-10)."""
    with db_conn.cursor() as cur:
        for pos in positions:
            cur.execute(
                """
                INSERT INTO broker_positions_snapshot (ticker, position, avg_cost, snapshot_at)
                VALUES (%s, %s, %s, NOW())
                """,
                (pos["ticker"], pos["position"], pos["avg_cost"])
            )
    db_conn.commit()
    logger.info("Wrote %d position rows to broker_positions_snapshot", len(positions))
```

**Schema reference (verified from infra/schema.sql):**
```sql
CREATE TABLE IF NOT EXISTS broker_positions_snapshot (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    position INTEGER,
    avg_cost NUMERIC(10,2),
    market_value NUMERIC(12,2),     -- not populated at reconciliation (no live prices)
    snapshot_at TIMESTAMPTZ DEFAULT NOW()
);
```
Note: `market_value` is nullable — do not attempt to populate it at reconciliation time (no market data subscription required).

### Pattern 8: DB Reconciliation (Mismatch Logging, D-08)

```python
def _reconcile_against_db(db_conn, ibkr_positions: list[dict], ibkr_orders: list[dict]) -> None:
    """Compare IBKR snapshot against DB state. Log discrepancies. Never overwrite DB (D-08)."""
    # Build IBKR position map: ticker → quantity
    ibkr_pos_map = {p["ticker"]: p["position"] for p in ibkr_positions}

    with db_conn.cursor() as cur:
        # Check open lots in position_lots against IBKR positions
        cur.execute(
            "SELECT ticker, SUM(quantity) FROM position_lots WHERE lot_closed_at IS NULL GROUP BY ticker"
        )
        db_open = {row[0]: row[1] for row in cur.fetchall()}

    for ticker, db_qty in db_open.items():
        ibkr_qty = ibkr_pos_map.get(ticker, 0)
        if db_qty != ibkr_qty:
            logger.warning(
                "RECONCILE MISMATCH ticker=%s db_open_qty=%d ibkr_qty=%d — operator review required",
                ticker, db_qty, ibkr_qty
            )

    # Check tickers in IBKR but not in DB at all
    for ticker, qty in ibkr_pos_map.items():
        if ticker not in db_open and qty != 0:
            logger.warning(
                "RECONCILE IBKR HAS POSITION NOT IN DB ticker=%s ibkr_qty=%d — operator review required",
                ticker, qty
            )

    logger.info("Reconciliation complete — DB not modified (D-08)")
```

### Pattern 9: Exposure Pattern — Module-Level Singleton (D-03 Recommendation)

**Recommendation:** Module-level singleton in `bravos/broker/connection.py`.

**Rationale:**
- The ingestion daemon is a single-process, single-account system. There is one IBApp instance for the lifetime of the process.
- Phase 4 (order execution) will call `ibapp.place_order(...)` directly — DI would require threading instance references through `BravosScraper`, `run_ingestion.py`, and every future caller.
- The existing Phase 2 code uses the same pattern: `_scraper: BravosScraper | None = None` is a module-level singleton in `run_ingestion.py`.
- The singleton is initialized in `run_ingestion.py`'s `main()`, not at import time, so tests can mock it cleanly.

```python
# bravos/broker/connection.py — bottom of file
# Module-level singleton — initialized by run_ingestion.py at startup
ibapp: IBApp | None = None
```

```python
# run_ingestion.py — in main() before schedule loop
import bravos.broker.connection as broker_module

ibapp = IBApp()
broker_module.ibapp = ibapp
connected = ibapp.connect_and_run(
    host=settings.IBKR_HOST,
    port=settings.get_ibkr_port(),
    client_id=settings.IBKR_CLIENT_ID,
)
```

```python
# Phase 4 caller
from bravos.broker.connection import ibapp

if ibapp and ibapp.is_connected():
    ibapp.place_order(...)
```

### Pattern 10: Integration into run_ingestion.py

**Where:** In `main()`, between signal handler setup and the schedule loop. IBKR thread must start and reconcile before `schedule.run_pending()` (D-13, D-11).

```python
# Conceptual integration — exact code in PLAN.md tasks

def main():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # --- IBKR startup (Phase 3) ---
    import bravos.broker.connection as broker_module
    from bravos.broker.connection import IBApp
    from bravos.config import settings

    ibapp = IBApp()
    broker_module.ibapp = ibapp
    ibkr_ok = ibapp.connect_and_run(
        host=settings.IBKR_HOST,
        port=settings.get_ibkr_port(),
        client_id=settings.IBKR_CLIENT_ID,
    )
    if ibkr_ok:
        # Reconcile before schedule loop starts (D-11)
        db_conn = _get_db_connection()
        ibapp.run_startup_reconciliation(db_conn)
        db_conn.close()
        ibapp.start_heartbeat_monitor()
    else:
        logger.critical("IBKR initial connection failed — starting ingestion loop without IBKR (D-14)")
        ibapp.start_background_reconnect()   # keeps retrying in background

    # --- Ingestion startup (existing) ---
    _scraper = BravosScraper()
    _scraper.startup()
    schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(run_cycle)
    ...
    # Shutdown
    ibapp.stop()          # disconnect + stop heartbeat thread
    _scraper.shutdown()
```

### Anti-Patterns to Avoid

- **Calling blocking code inside EWrapper callbacks:** Callbacks fire on the API thread (EClient.run() loop). Doing `db_conn.execute(...)` or `time.sleep(...)` inside `position()` or `error()` will stall or deadlock the message loop. Use `threading.Event.set()` in callbacks; do all blocking work in the calling thread.
- **Calling EClient.run() from the main thread:** Blocks everything permanently. Always daemon thread.
- **Reusing the same IBApp instance across reconnects:** The `EClient.connect()` / `disconnect()` cycle on the same object works, but the `EClient.run()` thread must also be restarted after disconnect. Create a fresh IBApp instance on each reconnect to avoid state pollution from previous session callbacks.
- **Not clearing reconciliation Events before re-requesting:** If `_positions_done` is already set from a prior run, `wait()` returns immediately with stale data. Always `.clear()` before calling `reqPositions()` etc.
- **Waiting for `positionEnd` when account has zero positions:** `positionEnd` still fires even with no positions — it is not conditional on having holdings. Same for `openOrderEnd` with no open orders.

---

## Threading Model (ASCII Diagram)

```
scripts/run_ingestion.py  (main thread)
│
├── startup sequence (blocking)
│   ├── IBApp.connect_and_run() ─── spawns ──► [Thread: ibkr-api]
│   │                                            EClient.run() message loop
│   │                                            callbacks fire here:
│   │                                            nextValidId, position,
│   │                                            positionEnd, openOrder,
│   │                                            openOrderEnd, accountSummary,
│   │                                            accountSummaryEnd, currentTime,
│   │                                            error
│   │
│   ├── _connected.wait(30s) ◄─────────────── nextValidId fires → _connected.set()
│   │
│   ├── run_startup_reconciliation() (blocks until Events set)
│   │   ├── reqPositions()
│   │   ├── reqOpenOrders()
│   │   ├── reqAccountSummary()
│   │   └── waits for _positions_done, _orders_done, _summary_done
│   │
│   └── start_heartbeat_monitor() ─── spawns ─► [Thread: ibkr-heartbeat]
│                                                 every 60s: reqCurrentTime()
│                                                 checks _last_heartbeat_at
│                                                 if stale > 10s: reconnect
│                                                 spawns ──► [Thread: ibkr-reconnect]
│                                                             (transient, exits on success)
│
├── scraper startup
│   └── BravosScraper.startup()
│
└── main loop (while not _shutdown)
    ├── schedule.run_pending()
    └── time.sleep(1)
        │
        every 300s: run_cycle() ── [Gmail health check]

All threads: daemon=True (die automatically when main thread exits)
SIGTERM → handle_shutdown() → _shutdown=True → main loop exits → ibapp.stop() → ibapp.disconnect()
```

---

## Reconciliation Flow

```
startup
  │
  ├─► connect_and_run() → wait nextValidId (30s timeout)
  │     FAIL? → start_background_reconnect() → start ingestion anyway (D-14)
  │
  ├─► reqPositions()        → position() × N → positionEnd()
  ├─► reqOpenOrders()       → openOrder() + orderStatus() × N → openOrderEnd()
  └─► reqAccountSummary()   → accountSummary() × N → accountSummaryEnd()
        (all three issued concurrently; wait for all three Events)
        TIMEOUT (30s)? → log ERROR, return False, start ingestion anyway
        │
        ▼
  _write_position_snapshot() → INSERT rows into broker_positions_snapshot
        │
        ▼
  _reconcile_against_db()
        ├─ for each open lot in position_lots → compare to IBKR position qty
        │     MISMATCH? → logger.warning("RECONCILE MISMATCH ...") — no DB write
        └─ for each IBKR position not in DB → logger.warning("RECONCILE IBKR HAS POSITION NOT IN DB ...")
        │
        ▼
  reconciliation done → return True
        │
        ▼
  start_heartbeat_monitor() → main loop begins
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Thread-safe flag for "connection confirmed" | bool + lock | `threading.Event` | Event.wait(timeout) is blocking poll with timeout built in; no spin loop |
| Thread-safe inter-thread notification | global bool | `threading.Event` | Avoids race conditions; built-in timeout support |
| Request ID counter | global int | `threading.Lock()` + counter | Shared mutable state accessed from API thread and main thread |
| Blocking wait for callback | time.sleep loop polling bool | `threading.Event.wait(timeout)` | Efficient, no spin; correct timeout semantics |

---

## Common Pitfalls

### Pitfall 1: CLOSE-WAIT stalls the next connect attempt

**What goes wrong:** After a crash or kill -9, the Gateway TCP socket enters CLOSE-WAIT. The next `EClient.connect()` call hangs or gets "already connected" from the Gateway side.

**Why it happens:** Python client never sent a FIN to the Gateway; Gateway TCP stack holds the socket open indefinitely.

**How to avoid:** The 5s wait in D-06 allows the OS to drain. If Gateway restarts (IBC daily restart), CLOSE-WAIT clears on the Gateway side automatically. Diagnose with `ss -anp | grep :4002 | grep CLOSE-WAIT`.

**Warning signs:** `connect()` call hangs; no `nextValidId` within 30s timeout.

### Pitfall 2: Stale Event state from prior reconciliation run

**What goes wrong:** `_positions_done.wait()` returns immediately with zero positions because the Event was set during a prior reconciliation and never cleared.

**Why it happens:** `threading.Event` stays set until explicitly cleared.

**How to avoid:** Always call `_positions_done.clear()`, `_orders_done.clear()`, `_summary_done.clear()` before issuing `reqPositions()` et al.

### Pitfall 3: Creating a fresh IBApp on reconnect vs. reusing the instance

**What goes wrong:** After `disconnect()` + fresh `connect()` on the same IBApp object, the EClient.run() thread from the prior session may still be alive. Now two threads consume from the same socket.

**Why it happens:** `disconnect()` does not stop the `run()` loop thread. The thread exits only when the socket is fully closed.

**How to avoid:** Keep a reference to the api_thread. After `disconnect()` + 5s sleep, the thread should have exited. Optionally join with a short timeout before starting a new one. Alternatively: always create a fresh IBApp object on reconnect (simpler, no stale state).

**Recommendation:** Create a fresh `IBApp` instance on each reconnect. The `_reconnect_loop` should instantiate a new IBApp, copy config, connect, and replace `broker_module.ibapp`. This avoids all stale-state bugs.

### Pitfall 4: Heartbeat response latency vs. 10s timeout

**What goes wrong:** Gateway is legitimately slow to respond to `reqCurrentTime()` during heavy load. 10s timeout triggers a spurious reconnect.

**Why it happens:** reqCurrentTime is a low-priority message on the Gateway side under load.

**How to avoid:** The 10s timeout (D-05) is the locked decision. Implement it faithfully. Log the timeout value prominently so the operator can tune it if needed. Spurious reconnects are non-fatal with the exponential backoff — they self-heal.

### Pitfall 5: Multiple error() calls triggering concurrent reconnect attempts

**What goes wrong:** Error 1100 fires; `_trigger_reconnect()` starts a reconnect thread. Before it completes, a stale heartbeat also triggers another `_trigger_reconnect()`. Two reconnect threads race.

**Why it happens:** Both detection mechanisms (error codes + heartbeat) are active simultaneously (D-04).

**How to avoid:** Guard with a `_reconnecting` flag (threading.Lock-protected). If `_reconnecting is True` in `_trigger_reconnect()`, return immediately without spawning another thread.

### Pitfall 6: ibapi version mismatch with Gateway

**What goes wrong:** `error 507 — bad message length` on connect, or callbacks fire with wrong data.

**Why it happens:** ibapi Python package version does not match installed Gateway version.

**How to avoid:** Check Gateway version in the Gateway UI. Download matching ibapi from IB developer portal. Record the version in requirements or a comment.

---

## Code Examples

### Full IBApp Skeleton

```python
# Source: .claude/skills/ibkr-connection/SKILL.md + project decisions

import threading
import time
import logging
from ibapi.wrapper import EWrapper
from ibapi.client import EClient
from bravos.config import settings

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [5, 10, 20, 40, 80]
_IMMEDIATE_RECONNECT_CODES = {504, 1100}
_IGNORE_CODES = {2104, 2106, 2119, 2158, 2110}
_CRITICAL_NO_RETRY_CODES = {501, 507}
REQ_ID_ACCOUNT_SUMMARY = 9001
HEARTBEAT_INTERVAL = 60
HEARTBEAT_TIMEOUT = 10


class IBApp(EWrapper, EClient):
    """
    IBKR connection manager.
    Combined EWrapper+EClient class (D-01).
    Thread model: EClient.run() in [ibkr-api] daemon thread;
                  heartbeat monitor in [ibkr-heartbeat] daemon thread.
    """

    def __init__(self, host: str, port: int, client_id: int):
        EClient.__init__(self, self)
        self._host = host
        self._port = port
        self._client_id = client_id

        # Connection state
        self.next_order_id: int | None = None
        self._connected = threading.Event()
        self._last_heartbeat_at: float = 0.0
        self._reconnecting = False
        self._recon_lock = threading.Lock()

        # Reconciliation state
        self._positions: list[dict] = []
        self._open_orders: list[dict] = []
        self._account_summary: dict = {}
        self._positions_done = threading.Event()
        self._orders_done = threading.Event()
        self._summary_done = threading.Event()

        # Lifecycle
        self._stop_event = threading.Event()
        self._api_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None

    # ── Connection ──────────────────────────────────────────────────────────

    def connect_and_run(self, timeout: float = 30) -> bool:
        self._connected.clear()
        self.connect(self._host, self._port, self._client_id)
        self._api_thread = threading.Thread(
            target=self.run, daemon=True, name="ibkr-api"
        )
        self._api_thread.start()
        return self._connected.wait(timeout)

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ── EWrapper: Connection callbacks ──────────────────────────────────────

    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self._last_heartbeat_at = time.monotonic()
        self._connected.set()
        logger.info("IBKR connected — nextValidId=%d port=%d", orderId, self._port)

    def currentTime(self, time_val: int):
        self._last_heartbeat_at = time.monotonic()

    def error(self, reqId: int, errorCode: int, errorString: str,
              advancedOrderRejectJson: str = ""):
        if errorCode in _IGNORE_CODES:
            return
        if errorCode in _CRITICAL_NO_RETRY_CODES:
            logger.critical("IBKR fatal error %d: %s", errorCode, errorString)
            return
        if errorCode in _IMMEDIATE_RECONNECT_CODES:
            logger.warning("IBKR connection error %d: %s — reconnecting", errorCode, errorString)
            self._trigger_reconnect(f"error_{errorCode}")
            return
        if reqId == -1:
            logger.error("IBKR system error %d: %s", errorCode, errorString)
        else:
            logger.warning("IBKR req %d error %d: %s", reqId, errorCode, errorString)

    # ── EWrapper: Reconciliation callbacks ──────────────────────────────────

    def position(self, account: str, contract, position: float, avgCost: float):
        if contract.secType == "STK":
            self._positions.append({
                "ticker": contract.symbol,
                "position": int(position),
                "avg_cost": avgCost,
            })

    def positionEnd(self):
        self._positions_done.set()

    def openOrder(self, orderId: int, contract, order, orderState):
        self._open_orders.append({
            "order_id": orderId,
            "ticker": contract.symbol,
            "action": order.action,
            "quantity": order.totalQuantity,
            "status": orderState.status,
        })

    def openOrderEnd(self):
        self._orders_done.set()

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        self._account_summary[tag] = value

    def accountSummaryEnd(self, reqId: int):
        self._summary_done.set()

    # ── Reconciliation ──────────────────────────────────────────────────────

    def run_startup_reconciliation(self, db_conn, timeout: float = 30) -> bool:
        """Fetch positions/orders/summary. Write snapshot. Log mismatches. Never overwrites DB."""
        self._positions.clear()
        self._open_orders.clear()
        self._account_summary.clear()
        self._positions_done.clear()
        self._orders_done.clear()
        self._summary_done.clear()

        self.reqPositions()
        self.reqOpenOrders()
        self.reqAccountSummary(
            REQ_ID_ACCOUNT_SUMMARY, "All",
            "NetLiquidation,TotalCashValue,AvailableFunds"
        )

        all_done = (
            self._positions_done.wait(timeout) and
            self._orders_done.wait(timeout) and
            self._summary_done.wait(timeout)
        )
        if not all_done:
            logger.error("Reconciliation timed out — partial data used")

        _write_position_snapshot(db_conn, self._positions)
        _reconcile_against_db(db_conn, self._positions, self._open_orders)
        return all_done

    # ── Heartbeat ───────────────────────────────────────────────────────────

    def start_heartbeat_monitor(self):
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="ibkr-heartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while not self._stop_event.wait(timeout=HEARTBEAT_INTERVAL):
            if not self._connected.is_set():
                continue
            self.reqCurrentTime()
            time.sleep(HEARTBEAT_TIMEOUT)
            elapsed = time.monotonic() - self._last_heartbeat_at
            if elapsed > HEARTBEAT_TIMEOUT:
                logger.warning(
                    "Heartbeat stale (%.1fs) — triggering reconnect", elapsed
                )
                self._trigger_reconnect("heartbeat_timeout")

    # ── Reconnect ───────────────────────────────────────────────────────────

    def _trigger_reconnect(self, reason: str):
        with self._recon_lock:
            if self._reconnecting:
                return
            self._reconnecting = True
        t = threading.Thread(
            target=self._reconnect_loop, args=(reason,),
            daemon=True, name="ibkr-reconnect"
        )
        t.start()

    def _reconnect_loop(self, reason: str):
        attempt = 0
        while not self._stop_event.is_set():
            delay = _RETRY_DELAYS[attempt] if attempt < len(_RETRY_DELAYS) else 60
            logger.info(
                "IBKR reconnect attempt %d reason=%s delay=%ds",
                attempt + 1, reason, delay
            )
            self._connected.clear()
            try:
                self.disconnect()
            except Exception:
                pass
            time.sleep(5)                     # CLOSE-WAIT drain (D-06)
            time.sleep(max(0, delay - 5))     # remaining backoff
            if self.connect_and_run():
                logger.info("IBKR reconnected on attempt %d", attempt + 1)
                with self._recon_lock:
                    self._reconnecting = False
                return
            attempt += 1
            if attempt == len(_RETRY_DELAYS):
                logger.critical(
                    "IBKR reconnect failed after %d attempts — retrying every 60s",
                    len(_RETRY_DELAYS)
                )
        with self._recon_lock:
            self._reconnecting = False

    def start_background_reconnect(self):
        """Start reconnect loop without blocking — used for D-14 startup failure path."""
        self._trigger_reconnect("initial_connect_failed")

    # ── Shutdown ────────────────────────────────────────────────────────────

    def stop(self):
        self._stop_event.set()
        self._connected.clear()
        try:
            self.disconnect()
        except Exception:
            pass
        logger.info("IBKR connection stopped")
```

### run_ingestion.py Integration Patch (conceptual)

```python
# In main(), after signal handler setup, before scraper init:
import bravos.broker.connection as broker_module
from bravos.broker.connection import IBApp

ibapp = IBApp(
    host=settings.IBKR_HOST,
    port=settings.get_ibkr_port(),
    client_id=settings.IBKR_CLIENT_ID,
)
broker_module.ibapp = ibapp

ibkr_ok = ibapp.connect_and_run(timeout=30)
if ibkr_ok:
    db_conn = _get_db_connection()
    ibapp.run_startup_reconciliation(db_conn, timeout=30)
    db_conn.close()
    ibapp.start_heartbeat_monitor()
    logger.info("IBKR ready — mode=%s port=%d", settings.TRADING_MODE, settings.get_ibkr_port())
else:
    logger.critical(
        "IBKR initial connect failed — starting ingestion without IBKR (D-14). "
        "Orders will not be placed until connection is established."
    )
    ibapp.start_background_reconnect()

# ... existing scraper init and schedule loop ...

# In graceful shutdown (before existing _scraper.shutdown()):
if broker_module.ibapp:
    broker_module.ibapp.stop()
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| Polling `isConnected()` flag | `threading.Event.wait(timeout)` | No spin loop; correct timeout |
| reqAllOpenOrders (all clients) | reqOpenOrders (this client only) | Avoids seeing other client's orders in reconciliation |
| Manual ibapi install from zip | Same (ibapi still not on PyPI as of 2026) | Must track ibapi version manually alongside Gateway version |

---

## Validation Architecture

nyquist_validation is enabled in .planning/config.json.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (already in use — tests/conftest.py exists) |
| Config file | none detected — pytest discovers tests/ by convention |
| Quick run command | `pytest tests/test_broker.py -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| IBKR-01 | Heartbeat thread sends reqCurrentTime every 60s; currentTime() callback updates _last_heartbeat_at | unit (mock EWrapper) | `pytest tests/test_broker.py::test_heartbeat_updates_timestamp -x` | Wave 0 |
| IBKR-01 | Heartbeat timeout triggers _trigger_reconnect | unit (mock) | `pytest tests/test_broker.py::test_heartbeat_timeout_triggers_reconnect -x` | Wave 0 |
| IBKR-02 | error(504) triggers immediate reconnect | unit (mock) | `pytest tests/test_broker.py::test_error_504_triggers_reconnect -x` | Wave 0 |
| IBKR-02 | error(1100) triggers immediate reconnect | unit (mock) | `pytest tests/test_broker.py::test_error_1100_triggers_reconnect -x` | Wave 0 |
| IBKR-02 | Reconnect loop respects exponential backoff delays | unit (mock time.sleep) | `pytest tests/test_broker.py::test_reconnect_backoff_delays -x` | Wave 0 |
| IBKR-03 | positionEnd fires → _positions_done set | unit | `pytest tests/test_broker.py::test_position_end_sets_event -x` | Wave 0 |
| IBKR-03 | openOrderEnd fires → _orders_done set | unit | `pytest tests/test_broker.py::test_open_order_end_sets_event -x` | Wave 0 |
| IBKR-03 | accountSummaryEnd fires → _summary_done set | unit | `pytest tests/test_broker.py::test_account_summary_end_sets_event -x` | Wave 0 |
| IBKR-03 | _write_position_snapshot inserts rows to broker_positions_snapshot | integration (db_connection fixture) | `pytest tests/test_broker.py::test_position_snapshot_written -x` | Wave 0 |
| IBKR-03 | Mismatch between IBKR and DB logs WARNING, does not overwrite DB | unit | `pytest tests/test_broker.py::test_reconcile_mismatch_logs_warning -x` | Wave 0 |
| IBKR-05 | get_ibkr_port() returns 4002 when TRADING_MODE=paper | unit | `pytest tests/test_broker.py::test_paper_port_config -x` | Wave 0 |
| IBKR-05 | get_ibkr_port() returns 4001 when TRADING_MODE=live | unit | `pytest tests/test_broker.py::test_live_port_config -x` | Wave 0 |

**Note on test approach:** Unit tests for IBApp mock the ibapi layer — they instantiate IBApp without calling `connect()`, then call callback methods directly (e.g., `ibapp.positionEnd()`) to test state transitions. Integration tests for snapshot writes use the `db_connection` fixture from conftest.py. No real Gateway required for any test in this phase.

### Sampling Rate

- **Per task commit:** `pytest tests/test_broker.py -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_broker.py` — all 12 tests listed above (Wave 0 stubs with `@pytest.mark.skip`)
- [ ] `bravos/broker/__init__.py` — empty file to make it a package
- [ ] `bravos/broker/connection.py` — IBApp class skeleton (Wave 0 stub)

---

## Open Questions

1. **ibapi version installed on bravos-vm1**
   - What we know: ibapi not on PyPI; must match Gateway version; error 507 if mismatch
   - What's unclear: Which Gateway version is installed on bravos-vm1; what ibapi version was used in Phase 1
   - Recommendation: Wave 0 task should verify with `python -c "import ibapi; print(ibapi.__version__)"` or check Gateway version in UI

2. **reqOpenOrders vs reqAllOpenOrders for reconciliation**
   - What we know: `reqOpenOrders()` returns orders for this clientId only; `reqAllOpenOrders()` returns all clients
   - What's unclear: Whether manually-placed TWS orders (clientId=0) should appear in reconciliation
   - Recommendation: Use `reqAllOpenOrders()` for reconciliation to see all account orders regardless of which client placed them. For Phase 4 order tracking, use `reqOpenOrders()` scoped to this client.

3. **SIGTERM handler extension in run_ingestion.py**
   - What we know: Existing `handle_shutdown()` sets `_shutdown = True`; scraper.shutdown() is called in cleanup
   - What's unclear: Whether `ibapp.stop()` needs to be wired into the same global shutdown or can be done inline in `main()`
   - Recommendation: Wire `ibapp.stop()` into `main()`'s cleanup block (after the schedule loop exits), not in `handle_shutdown()`. This matches how `_scraper.shutdown()` is currently handled.

---

## Sources

### Primary (HIGH confidence)
- `.claude/skills/ibkr-connection/SKILL.md` — EWrapper/EClient Pattern B, threading rules, reqPositions/positionEnd, reqOpenOrders/openOrderEnd, reqAccountSummary/accountSummaryEnd, reqCurrentTime/currentTime, error codes (501, 502, 504, 507, 1100, 2104, 2106, 2110, 2119, 2158), CLOSE-WAIT diagnosis
- `infra/schema.sql` — broker_positions_snapshot schema (verified column names and types)
- `bravos/config/settings.py` — IBKR_HOST, IBKR_PAPER_PORT (4002), IBKR_LIVE_PORT (4001), IBKR_CLIENT_ID, TRADING_MODE, get_ibkr_port() — all verified
- `bravos/config/secrets_config.py` — get_secret() API, REQUIRED_SECRETS list
- `scripts/run_ingestion.py` — existing daemon structure, SIGTERM pattern, schedule loop
- `bravos/ingestion/scraper.py` — Phase 2 patterns: startup/shutdown, catch_cycle_exceptions decorator
- `tests/conftest.py` — db_connection fixture signature and connection params
- `.planning/phases/03-ibkr-connection/03-CONTEXT.md` — all locked decisions D-01 through D-14

### Secondary (MEDIUM confidence)
- `tests/test_ingestion.py` — Wave 0 stub pattern (skip decorator, named by implementing plan) — established convention confirmed from actual file

### Tertiary (LOW confidence)
- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — ibapi usage verified from skill + project files; threading stdlib
- Architecture: HIGH — callback sequences verified from skill SKILL.md; patterns match confirmed Phase 2 code
- Pitfalls: HIGH — CLOSE-WAIT documented in SKILL.md with diagnosis command; stale Event and race conditions derived from threading primitives (stdlib, deterministic)
- Reconciliation flow: HIGH — callback names (positionEnd, openOrderEnd, accountSummaryEnd) verified from SKILL.md
- Exposure pattern recommendation: HIGH — singleton matches existing Phase 2 pattern; DI tradeoffs are clear

**Research date:** 2026-05-10
**Valid until:** 2026-06-10 (ibapi is stable; primary risk is Gateway version drift)
