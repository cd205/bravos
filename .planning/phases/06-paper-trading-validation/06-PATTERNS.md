# Phase 6: Paper Trading Validation — Pattern Map

**Mapped:** 2026-05-15
**Files analyzed:** 4 new/modified files
**Analogs found:** 4 / 4

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `scripts/validate_pipeline.py` | utility / script | request-response (sequential CRUD assertions) | `scripts/run_ingestion.py` | role-match (same IBApp startup sequence, same `_get_db_connection` pattern) |
| `tests/test_execution.py` (modify) | test | CRUD (DB integration) | `tests/test_positions.py` | exact (same Wave 0 / db_connection fixture pattern) |
| `tests/test_broker.py` (modify) | test | unit | `tests/test_broker.py` itself | exact (remove `@pytest.mark.skip` from 8 stubs) |
| `infra/migrate_phase4.sql` (apply, not modified) | migration | batch (DDL) | `infra/migrate_phase4.sql` | exact (the file is already written; this is a run-only action) |

---

## Pattern Assignments

### `scripts/validate_pipeline.py` (utility, request-response)

**Analog:** `scripts/run_ingestion.py`

**Imports pattern** (`scripts/run_ingestion.py` lines 29–51):
```python
import signal
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bravos.broker.connection as broker_module
from bravos.broker.connection import IBApp
from bravos.config import settings
from bravos.ingestion.scraper import BravosScraper
from bravos.execution.executor import _gate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.ingestion.daemon")

REQ_ID_PNL = 9002
```

**DB connection pattern** (`scripts/run_ingestion.py` lines 62–73):
```python
def _get_db_connection():
    """Open a psycopg2 connection for reconciliation. Closed after use."""
    import psycopg2
    import os
    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=password,
    )
```

**IBApp startup sequence** (`scripts/run_ingestion.py` lines 138–209):
```python
# Step 1: Instantiate and set module-level singleton
_ibapp = IBApp(
    host=settings.IBKR_HOST,
    port=settings.get_ibkr_port(),
    client_id=settings.IBKR_CLIENT_ID,
)
broker_module.ibapp = _ibapp

# Step 2: Connect (30s timeout)
ibkr_ok = _ibapp.connect_and_run(timeout=30)
if ibkr_ok:
    # Step 3: Startup reconciliation
    _db_conn = _get_db_connection()
    _ibapp.run_startup_reconciliation(_db_conn, timeout=30)
    _db_conn.close()

    # Step 4: Install dedicated DB connection for fill callbacks
    # CRITICAL: api thread owns this connection — NEVER share with main thread
    try:
        _ibapp._db_conn = _get_db_connection()
    except Exception:
        logger.exception("Failed to open DB connection for ibapp._db_conn — fill captures will be skipped")
        _ibapp._db_conn = None

    # Step 5: reqPnL subscription (circuit breaker)
    if _ibapp._account_name:
        _ibapp.reqPnL(REQ_ID_PNL, _ibapp._account_name, "")

    # Step 6: Start heartbeat monitor
    _ibapp.start_heartbeat_monitor()

# Step 7: Instantiate scraper
scraper = BravosScraper()
scraper.startup()
```

**Two-connection pattern note** (`scripts/run_ingestion.py` lines 155–171):
```python
# execDetails and orderStatus fire on the ibkr-api thread. This
# connection is owned by that thread and is NEVER shared with the
# main thread or the executor (psycopg2 connections are not thread-safe).
# The connection is intentionally not closed at this scope — it lives
# for the daemon process lifetime.
_ibapp._db_conn = _get_db_connection()  # api-thread connection

# The main thread uses a SEPARATE connection for DB assertions:
db_conn = _get_db_connection()  # main-thread assertion connection
```

**DB assertion pattern** (sourced from `tests/test_execution.py` lines 122–177 and RESEARCH.md):
```python
def assert_signal_processed(url: str, expected_ticker: str, expected_action: str,
                              db_conn, expect_order: bool = True) -> dict:
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, action_type, confidence FROM signals WHERE post_url=%s",
            (url,)
        )
        sig = cur.fetchone()
    if sig is None:
        return {"ok": False, "detail": "no signal row"}
    signal_id, ticker, action_type, confidence = sig
    if ticker != expected_ticker or action_type != expected_action:
        return {"ok": False, "detail": f"expected {expected_ticker}/{expected_action}, got {ticker}/{action_type}"}
    if not expect_order:
        return {"ok": True, "detail": "signal only (low confidence or out of hours)"}
    # Check risk_gate_log
    with db_conn.cursor() as cur:
        cur.execute("SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s", (signal_id,))
        gate = cur.fetchone()
    if gate is None:
        return {"ok": False, "detail": "no risk_gate_log row"}
    # Check orders
    with db_conn.cursor() as cur:
        cur.execute("SELECT ibkr_order_id, status FROM orders WHERE signal_id=%s", (signal_id,))
        order = cur.fetchone()
    if order is None:
        return {"ok": False, "detail": f"no order row (gate: {gate})"}
    return {"ok": True, "detail": f"order={order[0]} status={order[1]} gate={gate[1]}"}
```

**Fill wait pattern** (per RESEARCH.md Pitfall 2 — IB paper fills arrive 0.5–2 seconds after order confirmation):
```python
# After process_alert(), wait before asserting executions rows.
# Use polling loop not bare sleep for faster feedback:
scraper.process_alert(url)
fill_row = None
for _ in range(10):
    time.sleep(1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM executions WHERE order_id IN "
                    "(SELECT id FROM orders WHERE signal_id=%s)", (signal_id,))
        fill_row = cur.fetchone()
    if fill_row:
        break
```

**Graceful shutdown pattern** (`scripts/run_ingestion.py` lines 127–129, 232–237):
```python
signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)
# ...on shutdown:
if broker_module.ibapp is not None:
    broker_module.ibapp.stop()
scraper.shutdown()
```

**Script entry point pattern** (`scripts/run_ingestion.py` lines 240–241, `scripts/verify_chrome.py` lines 46–47):
```python
if __name__ == "__main__":
    main()
```

---

### `tests/test_execution.py` — fix `test_order_db_write_pending` (modify)

**Analog:** `tests/test_execution.py` itself (lines 227–255), plus unique-id pattern from `tests/test_positions.py`

**Problem:** `mock_ibapp.next_order_id = 1000` is hardcoded. Stale `SUBMITTED` row from a prior run causes `capture_db_state` to read the wrong row (see RESEARCH.md Bug 2).

**Fix pattern — unique order ID** (`tests/test_execution.py` line 244; also used for `post_url` throughout the file):
```python
# BEFORE (broken):
mock_ibapp.next_order_id = 1000

# AFTER (Option A — unique per run, same approach as post_url generation):
order_id = int.from_bytes(os.urandom(3), "big") + 10000  # unique per run, > 10000 avoids low collisions
mock_ibapp.next_order_id = order_id
```

Also update the hardcoded `1000` in `capture_db_state` and the assertion to use `order_id`.

**Unique-ID pattern established in this file** (`tests/test_execution.py` lines 131, 154, 235, etc.):
```python
f"http://test/{os.urandom(4).hex()}"  # used for post_url uniqueness throughout
```

Apply the same `os.urandom` approach to order IDs.

---

### `tests/test_broker.py` — unskip 8 stubs (modify)

**Analog:** `tests/test_broker.py` lines 19–96 (the 8 stubs themselves)

**Pattern:** Remove `@pytest.mark.skip(reason="plan: 03-1")` decorator only. Test bodies are complete and correct.

**Tests to unskip** (`tests/test_broker.py` lines 19–96):
```python
# Remove decorator from each of these:
@pytest.mark.skip(reason="plan: 03-1")  # LINE 19 — remove this line
def test_ibapp_init_sets_host_port_client_id(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 29 — remove this line
def test_ibapp_init_connected_event_is_clear(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 37 — remove this line
def test_ibapp_is_connected_returns_false_before_connect(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 45 — remove this line
def test_next_valid_id_sets_connected_and_stores_order_id(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 57 — remove this line
def test_paper_port_config(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 67 — remove this line
def test_live_port_config(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 78 — remove this line
def test_module_level_ibapp_singleton_is_none_at_import(): ...

@pytest.mark.skip(reason="plan: 03-1")  # LINE 87 — remove this line
def test_stop_sets_stop_event_and_clears_connected(): ...
```

No other changes needed. The `import os`, `import threading`, `import time`, and `import pytest` are already present at lines 11–14.

---

### `infra/migrate_phase4.sql` (apply only — file already correct)

**Analog:** `infra/migrate_phase4.sql` (the file itself, lines 1–23)

**Action:** Run the migration, not modify it. The DDL is idempotent (`CREATE TABLE IF NOT EXISTS`).

**Run pattern** (`infra/migrate_phase4.sql` header comment, lines 3–4):
```bash
PGPASSWORD="$BRAVOS_DB_PASSWORD" psql \
  -h 127.0.0.1 -U bravos -d bravos_trading \
  -f /home/chris_s_dodd/bravos/infra/migrate_phase4.sql
```

**Expected result:** `risk_gate_log` table created with these columns:
- `id SERIAL PRIMARY KEY`
- `signal_id INTEGER REFERENCES signals(id)`
- `checked_at TIMESTAMPTZ DEFAULT NOW()`
- `gate_passed BOOLEAN NOT NULL`
- `reason TEXT NOT NULL`
- `open_positions INTEGER`, `max_positions INTEGER`
- `order_allocation_pct NUMERIC(6,4)`, `max_allocation_pct NUMERIC(6,4)`
- `net_liquidation NUMERIC(14,2)`, `daily_pnl NUMERIC(14,2)`, `daily_pnl_threshold NUMERIC(14,2)`

---

## Shared Patterns

### DB Connection (two-connection model)
**Source:** `scripts/run_ingestion.py` lines 62–73, 163–171
**Apply to:** `scripts/validate_pipeline.py`

The project uses a fixed two-connection pattern — never a pool:
1. `_ibapp._db_conn` — owned by the ibkr-api thread for fill callbacks (`execDetails`/`orderStatus`). Install before processing any URL.
2. Main-thread connection — used for DB assertions in the validation loop. Separate object from `_ibapp._db_conn`.

```python
# api-thread connection (install before any process_alert call)
_ibapp._db_conn = _get_db_connection()

# main-thread assertion connection (separate — psycopg2 is not thread-safe)
db_conn = _get_db_connection()
```

### Test DB Fixture
**Source:** `tests/conftest.py` lines 11–35
**Apply to:** Any new test that needs DB access

```python
@pytest.fixture
def db_connection():
    import psycopg2
    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    conn = psycopg2.connect(
        host="127.0.0.1",
        port=5432,
        dbname="bravos_trading",
        user="bravos",
        password=password,
    )
    try:
        yield conn
    finally:
        conn.close()
```

### Test Rollback Pattern
**Source:** `tests/test_positions.py` lines 49–50, 100–101, etc. (every DB test)
**Apply to:** Any new DB-writing test in the validation suite

```python
try:
    # ... test body with DB writes ...
finally:
    db_connection.rollback()
```

### Unique Test Data IDs
**Source:** `tests/test_execution.py` lines 131, 154, 235; `tests/test_positions.py` lines 33, 61, etc.
**Apply to:** Any new test that writes to signals or orders tables

```python
# For post_url uniqueness:
f"http://test/{os.urandom(4).hex()}"

# For order_id uniqueness (apply to test_order_db_write_pending fix):
int.from_bytes(os.urandom(3), "big") + 10000
```

### Script `sys.path` Setup
**Source:** `scripts/run_ingestion.py` lines 35–36
**Apply to:** `scripts/validate_pipeline.py`

```python
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

### Logging Setup
**Source:** `scripts/run_ingestion.py` lines 47–51
**Apply to:** `scripts/validate_pipeline.py`

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.validation")
```

---

## No Analog Found

All Phase 6 files have close analogs. No files require falling back to RESEARCH.md patterns exclusively.

| File | Note |
|------|------|
| `validation/BUG-LOG.md` | Free-form markdown doc; no code analog needed — created manually by operator during run |
| `validation/VALIDATION-REPORT.md` | Free-form markdown doc; structure driven by CONTEXT.md D-06 and RESEARCH.md success criteria table |

---

## Metadata

**Analog search scope:** `scripts/`, `tests/`, `infra/`
**Files scanned:** `run_ingestion.py`, `verify_chrome.py`, `test_execution.py`, `test_positions.py`, `test_broker.py`, `conftest.py`, `migrate_phase4.sql`
**Pattern extraction date:** 2026-05-15
