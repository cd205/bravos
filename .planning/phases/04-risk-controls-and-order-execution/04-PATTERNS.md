# Phase 4: Risk Controls and Order Execution - Pattern Map

**Mapped:** 2026-05-14
**Files analyzed:** 9
**Analogs found:** 9 / 9

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bravos/risk/__init__.py` | package-init | — | `bravos/broker/__init__.py` (empty) | exact |
| `bravos/risk/gate.py` | service | request-response | `bravos/broker/connection.py` (`_write_position_snapshot`, `_reconcile_against_db`) | role-match |
| `bravos/execution/__init__.py` | package-init | — | `bravos/broker/__init__.py` (empty) | exact |
| `bravos/execution/executor.py` | service | request-response + event-driven | `bravos/broker/connection.py` (threading.Event pattern, DB write, ibapi calls) | role-match |
| `bravos/broker/connection.py` | service (modify) | event-driven | self — additive callbacks copied from existing callback style | exact |
| `bravos/config/settings.py` | config (modify) | — | self — existing env-var constant pattern | exact |
| `bravos/ingestion/scraper.py` | service (modify) | request-response | self — existing `_store_signal` + `process_alert` | exact |
| `infra/migrate_phase4.sql` | migration | — | `infra/migrate_signals_v2.sql` | exact |
| `tests/test_execution.py` | test | — | `tests/test_broker.py` (Wave 0 skip stubs) | exact |

---

## Pattern Assignments

### `bravos/risk/__init__.py` and `bravos/execution/__init__.py` (package-init)

**Analog:** `bravos/broker/__init__.py` — empty file, no content required.

These are empty package markers. No content beyond an optional module docstring is needed. The broker package `__init__.py` sets the precedent of leaving it empty (0 bytes, or at most a single-line docstring).

---

### `bravos/risk/gate.py` (service, request-response)

**Analog:** `bravos/broker/connection.py` — module-level helper functions and threading.Event coordination pattern.

**Imports pattern** (`bravos/broker/connection.py` lines 13–21):
```python
import logging
import threading
import time

from ibapi.client import EClient
from ibapi.wrapper import EWrapper

from bravos.config.settings import IBKR_CLIENT_ID, IBKR_HOST, get_ibkr_port

logger = logging.getLogger(__name__)
```
For `gate.py`, adapt imports to:
```python
import logging
import datetime
from zoneinfo import ZoneInfo

from bravos.config.settings import (
    MAX_OPEN_POSITIONS, MAX_ALLOCATION_PCT,
    DAILY_LOSS_THRESHOLD, WEIGHT_PCT_PER_UNIT,
)

logger = logging.getLogger(__name__)
```

**DB write pattern** (`bravos/broker/connection.py` lines 474–489):
```python
def _write_position_snapshot(db_conn, positions: list[dict]) -> None:
    with db_conn.cursor() as cur:
        for pos in positions:
            cur.execute(
                "INSERT INTO broker_positions_snapshot (ticker, position, avg_cost, snapshot_at)"
                " VALUES (%s, %s, %s, NOW())",
                (pos["ticker"], pos["position"], pos["avg_cost"]),
            )
    db_conn.commit()
    logger.info("Wrote %d position rows to broker_positions_snapshot", len(positions))
```
Gate log writes follow this same pattern: `with db_conn.cursor() as cur:`, parameterized `%s` placeholders, `db_conn.commit()` after each write.

**DB query pattern** (`bravos/broker/connection.py` lines 499–505):
```python
with db_conn.cursor() as cur:
    cur.execute(
        "SELECT ticker, SUM(quantity) FROM position_lots"
        " WHERE lot_closed_at IS NULL GROUP BY ticker"
    )
    db_open = {row[0]: row[1] for row in cur.fetchall()}
```
Open position count for RISK-01 uses the same `WHERE lot_closed_at IS NULL` predicate — just `COUNT(DISTINCT ticker)` instead of SUM.

**Class state pattern** (`bravos/broker/connection.py` lines 61–97):
The `IBApp.__init__` pattern shows how to initialize instance variables for threading state — `_circuit_tripped = False` follows the same flat-attribute style used for `_reconnecting`, `_positions`, `_account_summary`, etc.

---

### `bravos/execution/executor.py` (service, request-response + event-driven)

**Analog:** `bravos/broker/connection.py` — threading.Event coordination, ibapi calls, DB write-before-submit.

**Module-level constant + logger pattern** (`bravos/broker/connection.py` lines 26–38):
```python
_RETRY_DELAYS = [5, 10, 20, 40, 80]
_IMMEDIATE_RECONNECT_CODES = {504, 1100}
# ...
logger = logging.getLogger(__name__)
```
Executor follows the same pattern:
```python
PRICE_WAIT_TIMEOUT = 5.0
ORDER_STATUS_TIMEOUT = 3.0
_ACTION_MAP = {"open": "BUY", "add": "BUY", "partial_close": "SELL", "close": "SELL"}
logger = logging.getLogger(__name__)
```

**threading.Event registration + wait pattern** (`bravos/broker/connection.py` lines 110–117):
```python
self._api_thread = threading.Thread(
    target=self.run,
    name="ibkr-api",
    daemon=True,
)
self._api_thread.start()
connected = self._connected.wait(timeout)
```
The executor uses the same `.wait(timeout)` pattern for both price ticks and order status — register a slot dict with `"event": threading.Event()` before the ibapi call, then `.wait(timeout)` on that event.

**DB write then ibapi call sequence** (`bravos/broker/connection.py` lines 428–466, `run_startup_reconciliation`):
```python
# 3. Issue all three requests concurrently
self.reqPositions()
self.reqAllOpenOrders()
self.reqAccountSummary(...)

# 4. Wait for all three events
all_done = (
    self._positions_done.wait(timeout) and
    self._orders_done.wait(timeout) and
    self._summary_done.wait(timeout)
)
```
Executor mirrors this sequencing: write DB row first (D-08), then call `placeOrder`, then `.wait(timeout)` for orderStatus, then update DB.

**Singleton import guard** (`bravos/broker/connection.py` line 538 + `scripts/run_ingestion.py` lines 121):
```python
# connection.py
ibapp: "IBApp | None" = None

# run_ingestion.py
broker_module.ibapp = _ibapp
```
Executor imports the singleton at call time (not at module import) and checks before use:
```python
from bravos.broker.connection import ibapp
if ibapp is None or not ibapp.is_connected():
    logger.warning("ibapp not connected — skipping execution for signal_id=%s", signal_id)
    return
```

**DB connection pattern** (`scripts/run_ingestion.py` lines 57–68 and `bravos/ingestion/scraper.py` lines 207–216):
```python
# run_ingestion.py _get_db_connection()
import psycopg2, os
password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
return psycopg2.connect(
    host=settings.DB_HOST,
    port=settings.DB_PORT,
    dbname=settings.DB_NAME,
    user=settings.DB_USER,
    password=password,
)
```
The executor receives `db_conn` as a parameter (passed by the scraper) — it does NOT open its own connection. This matches the reconciliation pattern where `_write_position_snapshot(db_conn, ...)` accepts the connection from the caller.

---

### `bravos/broker/connection.py` (modify — additive callbacks)

**Analog:** self — existing Phase 3 callbacks in the same file.

**Existing callback signature style** (`bravos/broker/connection.py` lines 361–424):
```python
def position(self, account: str, contract, position: float, avgCost: float) -> None:
    """
    Called once per position held in the account.
    ...
    """
    if contract.secType != "STK":
        return
    self._positions.append({...})

def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:
    """
    Called once per requested tag ...
    """
    self._account_summary[tag] = value

def accountSummaryEnd(self, reqId: int) -> None:
    """..."""
    self._summary_done.set()
    logger.info("reqAccountSummary complete — %d tags received", len(self._account_summary))
```

**Four new callbacks must follow the same style:**
- One-line docstring (what fires it + phase note)
- Early return guard for irrelevant data (like `if contract.secType != "STK": return`)
- Lock acquisition before dict mutation (for `tickPrice` — see `_tick_lock` pattern in RESEARCH.md Pattern 1)
- `logger.info()` at INFO level for state transitions; no log for high-frequency callbacks (`pnl` fires continuously — no log per call)

**`__init__` additions** — insert after line 91 (after `_summary_done`, before `_stop_event`):
```python
# Phase 4: price-tick routing (for executor _fetch_price)
self._tick_events: dict[int, dict] = {}
self._tick_lock = threading.Lock()
self._mkt_req_counter = 2000          # avoids collision with REQ_ID_ACCOUNT_SUMMARY=9001

# Phase 4: order-status routing (for executor _submit_order)
self._order_status_events: dict[int, dict] = {}

# Phase 4: account name (populated by managedAccounts; used by reqPnL)
self._account_name: str = ""

# Phase 4: daily P&L for circuit breaker (populated by pnl callback)
self._daily_pnl: float | None = None
```

---

### `bravos/config/settings.py` (modify — add risk constants)

**Analog:** self — existing env-var constant pattern.

**Existing pattern** (`bravos/config/settings.py` lines 1–22):
```python
"""Bravos Trading System — Configuration Settings"""
import os

# Database
DB_HOST = os.environ.get("BRAVOS_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("BRAVOS_DB_PORT", "5432"))
# ...

# Trading
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")  # "paper" or "live"
```

**New constants follow the exact same `os.environ.get` pattern**, appended after the existing Trading block:
```python
# Risk controls — configurable per deployment
MAX_OPEN_POSITIONS    = int(os.environ.get("MAX_OPEN_POSITIONS", "20"))
MAX_ALLOCATION_PCT    = float(os.environ.get("MAX_ALLOCATION_PCT", "0.25"))
DAILY_LOSS_THRESHOLD  = float(os.environ.get("DAILY_LOSS_THRESHOLD", "-5000.0"))
WEIGHT_PCT_PER_UNIT   = float(os.environ.get("WEIGHT_PCT_PER_UNIT", "0.05"))
```
Inline comments explain defaults (same style as `SCRAPE_INTERVAL_SECONDS = 300  # 5 minutes per project constraint`).

---

### `bravos/ingestion/scraper.py` (modify — `_store_signal` return + `process_alert` call)

**Analog:** self — existing `_store_signal` and `process_alert`.

**Current `_store_signal` pattern** (`bravos/ingestion/scraper.py` lines 205–237):
```python
def _store_signal(self, signal_data: dict):
    """Insert signal into DB. ON CONFLICT DO NOTHING for dedup."""
    import psycopg2
    # ... connect ...
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signals (...)
                VALUES (...)
                ON CONFLICT (post_url) DO NOTHING
                """,
                (...)
            )
        conn.commit()
    finally:
        conn.close()
```

**Required change:** Add `RETURNING id` to the INSERT, capture `row = cur.fetchone()`, return `row[0] if row else None`. Return type changes from `None` to `int | None`. No other changes to `_store_signal`.

**Current `process_alert` pattern** (`bravos/ingestion/scraper.py` lines 239–262):
```python
@catch_cycle_exceptions
def process_alert(self, url: str):
    # ... session check + login ...
    content = self.fetch_post(url)
    parsed = parse_signal(content["title"], content["text"])
    signal_data = {
        "post_url": url,
        "post_title": content["title"],
        "raw_html": content["raw_html"],
        **parsed,
    }
    self._store_signal(signal_data)
    logger.info("Processed: %s -> ticker=%s action=%s confidence=%s", ...)
```

**Required change:** Capture `signal_id = self._store_signal(signal_data)` and then call `execute_signal` when `signal_id is not None`. Lazy import at call site (not at module top) to avoid circular import:
```python
signal_id = self._store_signal(signal_data)
if signal_id is not None:
    from bravos.execution.executor import execute_signal
    import psycopg2, os
    exec_conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        dbname=settings.DB_NAME, user=settings.DB_USER,
        password=os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy"),
    )
    try:
        execute_signal(signal_id, exec_conn)
    finally:
        exec_conn.close()
```
Note the db connection pattern is identical to `_get_db_connection()` in `scripts/run_ingestion.py` (lines 57–68) and `_store_signal` itself (lines 207–215).

---

### `infra/migrate_phase4.sql` (migration)

**Analog:** `infra/migrate_signals_v2.sql`

**Existing migration pattern** (`infra/migrate_signals_v2.sql` lines 1–11):
```sql
-- Phase 2: Signal Ingestion — schema migration
-- Adds audit columns required by AUDIT-01 and INGST-06
-- Run via: psql -h 127.0.0.1 -U bravos -d bravos_trading -f infra/migrate_signals_v2.sql

ALTER TABLE signals
  ADD COLUMN IF NOT EXISTS parse_method VARCHAR(10),
  ADD COLUMN IF NOT EXISTS scraped_at   TIMESTAMPTZ DEFAULT NOW();

COMMENT ON COLUMN signals.parse_method IS 'regex or spacy — which parser produced this result';
COMMENT ON COLUMN signals.scraped_at   IS 'Timestamp of the scrape cycle that retrieved this post';
```

**Phase 4 migration follows same header comment format**, same `-- Run via:` instruction, uses `CREATE TABLE IF NOT EXISTS` (same guard style as `schema.sql`), and includes `GRANT` statements matching `schema.sql` lines 66–67:
```sql
GRANT ALL ON ALL TABLES IN SCHEMA public TO bravos;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO bravos;
```
The `risk_gate_log` DDL from RESEARCH.md Pattern 7 is the exact content to use.

---

### `tests/test_execution.py` (test, Wave 0 stubs)

**Analog:** `tests/test_broker.py` — Wave 0 skip stub pattern.

**Wave 0 skip pattern** (`tests/test_broker.py` lines 19–84):
```python
"""
tests/test_broker.py — IBApp unit and integration tests.

All tests are Wave 0 stubs (skipped). Each test body is the full intended
implementation. Tests are unskipped as their implementing plan lands:
  - 03-1: test_ibapp_init_*, test_paper_port_config, test_live_port_config
  ...
"""
import os
import threading
import time
import pytest


# ── Plan 03-1: IBApp class skeleton + settings integration ─────────────────

@pytest.mark.skip(reason="plan: 03-1")
def test_ibapp_init_sets_host_port_client_id():
    """IBApp stores connection params from constructor — does not connect."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert app._host == "127.0.0.1"
    ...
```

**Key conventions to copy exactly:**
1. Module docstring with plan-to-test mapping table
2. `@pytest.mark.skip(reason="plan: XX-Y")` where `XX-Y` matches the plan name
3. Full test body written inside the skip — not `pass` or `...`
4. Section header comments `# ── Plan XX-Y: Description ────` with em-dash padding
5. Integration tests that need a live DB use the `db_connection` fixture from `conftest.py`
6. Unit tests that need mock objects use `from unittest.mock import MagicMock`
7. Tests that check logging use the `caplog` pytest fixture with `caplog.at_level(logging.WARNING, logger="bravos.<module>")`

**Import style for test files** (`tests/test_broker.py` lines 1–15):
```python
import os
import threading
import time
import pytest
```
Lazy imports inside test bodies (`from bravos.X import Y`) — not at module top — so skipped tests don't fail at collection if the module doesn't exist yet.

---

## Shared Patterns

### psycopg2 DB Write (parameterized queries)
**Source:** `bravos/broker/connection.py` lines 481–488 and `bravos/ingestion/scraper.py` lines 217–234
**Apply to:** `gate.py` (risk_gate_log write), `executor.py` (orders INSERT and UPDATE)
```python
with db_conn.cursor() as cur:
    cur.execute(
        "INSERT INTO table_name (col1, col2) VALUES (%s, %s)",
        (val1, val2),
    )
db_conn.commit()
```
All DB writes use `%s` placeholders — never f-strings or string concatenation. `with db_conn.cursor() as cur:` context manager always used.

### threading.Event Registration + Wait
**Source:** `bravos/broker/connection.py` lines 74–89 and `run_startup_reconciliation` lines 428–456
**Apply to:** `executor.py` (`_fetch_price` price-wait, `_submit_order` status-wait), `connection.py` new callbacks
```python
# Before the ibapi call — register the slot
event_slot = {"event": threading.Event(), "data": None}
self._some_events[key] = event_slot

# Issue the ibapi request
self.reqSomething(key, ...)

# Wait
arrived = event_slot["event"].wait(timeout=N)

# Always clean up
self._some_events.pop(key, None)
```

### Module Logger
**Source:** every existing module (`connection.py` line 22, `scraper.py` line 28, `parser.py` line 8)
**Apply to:** `gate.py`, `executor.py`
```python
logger = logging.getLogger(__name__)
```
All log calls use `%s` format args — never f-strings in log messages (`logger.info("msg %s", value)` not `logger.info(f"msg {value}")`).

### IBKR Singleton Guard
**Source:** `bravos/broker/connection.py` line 538 + `bravos/ingestion/scraper.py` line 239
**Apply to:** `executor.py` entry guard
```python
from bravos.broker.connection import ibapp
if ibapp is None or not ibapp.is_connected():
    logger.warning("ibapp not connected — skipping signal_id=%s", signal_id)
    return
```

### `os.environ.get` Config Constants
**Source:** `bravos/config/settings.py` lines 4–21
**Apply to:** new risk constants in `settings.py`
```python
SOME_SETTING = type_cast(os.environ.get("ENV_VAR_NAME", "default_value"))
```
String defaults are cast at assignment time (`int(...)`, `float(...)`). Environment variable names use `ALL_CAPS_WITH_UNDERSCORES`.

---

## No Analog Found

All files have close analogs in the codebase. No files require falling back to RESEARCH.md patterns alone — though RESEARCH.md Patterns 1–10 provide the exact ibapi-specific code that the codebase has not yet implemented (tickPrice, orderStatus, pnl, placeOrder). These supplement the structural patterns above.

| File | Note |
|------|------|
| `bravos/risk/gate.py` | No existing gate/policy class — use `_write_position_snapshot` + `_reconcile_against_db` for DB interaction style; use RESEARCH.md Pattern 6 for RiskGate class body |
| `bravos/execution/executor.py` | No existing executor — use `connection.py` threading.Event patterns for structure; use RESEARCH.md Patterns 2, 4, 5, 10 for ibapi-specific bodies |

---

## Metadata

**Analog search scope:** `bravos/`, `tests/`, `infra/`, `scripts/`
**Files scanned:** 9 source files read in full
**Pattern extraction date:** 2026-05-14
