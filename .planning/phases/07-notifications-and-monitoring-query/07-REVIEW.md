---
phase: 07-notifications-and-monitoring-query
reviewed: 2026-05-20T00:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - bravos/broker/connection.py
  - bravos/config/secrets_config.py
  - bravos/config/settings.py
  - bravos/ingestion/scraper.py
  - bravos/notifications/__init__.py
  - bravos/notifications/notifier.py
  - bravos/risk/gate.py
  - queries/monitor.sql
  - scripts/run_ingestion.py
  - tests/test_notifications.py
findings:
  critical: 2
  warning: 6
  info: 3
  total: 11
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-05-20
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

This phase wires up email alerting (circuit breaker, IBKR disconnect, re-auth failure, parse-spike) and adds a full signal-to-fill monitoring SQL query. The notification logic itself is sound — the send_alert/record_parse_outcome design is correct, the test suite exercises the spike window and re-arm properly. Two blockers were found: the monitoring query will fan out rows for any signal with multiple orders, and the run_ingestion daemon main loop can crash on a Chrome driver failure that escapes run_cycle. Six warnings cover thread safety, blocking I/O in the order path, missing startup validation, and DST-sensitive scheduling. Three informational items round out code quality.

---

## Critical Issues

### CR-01: `monitor.sql` — fan-out when a signal has multiple orders

**File:** `queries/monitor.sql:47`
**Issue:** `LEFT JOIN orders o ON o.signal_id = s.id` is unconstrained. The `orders` table has no `UNIQUE` constraint on `signal_id` (confirmed in `infra/schema.sql`), so any signal that produced more than one order row (e.g. a retry or a partial-close followed by a close) will appear as multiple rows in the query result. The query has `DISTINCT ON` only for `risk_gate_log`; there is no deduplication for `orders`. A signal with two orders will appear twice, making the result set misleading for any downstream consumer that assumes one row per signal.

**Fix:**
```sql
-- Replace the raw LEFT JOIN orders with a CTE that picks the latest order per signal:
WITH latest_gate AS (
    SELECT DISTINCT ON (signal_id)
        signal_id, gate_passed, checked_at
    FROM risk_gate_log
    ORDER BY signal_id, checked_at DESC
),
latest_order AS (
    SELECT DISTINCT ON (signal_id)
        signal_id, status, fill_price
    FROM orders
    ORDER BY signal_id, created_at DESC
),
open_qty AS (
    SELECT ticker, SUM(quantity) AS open_quantity
    FROM position_lots WHERE lot_closed_at IS NULL GROUP BY ticker
),
realized AS (
    SELECT ticker, SUM(pnl) AS realized_pnl
    FROM position_lots WHERE lot_closed_at IS NOT NULL GROUP BY ticker
)
SELECT
    s.id AS signal_id,
    s.parsed_at, s.ticker, s.action_type, s.confidence,
    lg.gate_passed,
    lo.status AS order_status,
    lo.fill_price,
    oq.open_quantity,
    r.realized_pnl
FROM signals s
LEFT JOIN latest_gate  lg ON lg.signal_id = s.id
LEFT JOIN latest_order lo ON lo.signal_id = s.id
LEFT JOIN open_qty     oq ON oq.ticker    = s.ticker
LEFT JOIN realized      r ON r.ticker     = s.ticker
ORDER BY s.parsed_at DESC;
```

---

### CR-02: `run_ingestion.py` — `run_cycle` has no exception guard; a crashed Chrome driver kills the daemon

**File:** `scripts/run_ingestion.py:84-131`
**Issue:** The module docstring (line 18) and run_cycle's own docstring state that "Exceptions inside run_cycle are caught by @catch_cycle_exceptions." This is false: `run_cycle` carries no decorator. The `schedule` library does not swallow exceptions from job functions (confirmed by inspection of `schedule.Job.run`). The main loop (line 237) has no `try/except` around `schedule.run_pending()`.

The concrete failure path: `_check_session()` catches `WebDriverException` and returns `False`. `run_cycle` then calls `_scraper._login()`. `_login()` calls `self.driver.get(settings.LOGIN_URL)` on a dead driver; this raises `WebDriverException`. `_login()` only catches `TimeoutException`, so the `WebDriverException` propagates out of `run_cycle`, out of `schedule.run_pending()`, and crashes the `while not _shutdown` loop, terminating the daemon process.

**Fix:**
```python
# Option A: add the decorator (mirrors BravosScraper.process_alert)
from bravos.ingestion.scraper import catch_cycle_exceptions

@catch_cycle_exceptions
def run_cycle():
    ...

# Option B: guard in the main loop
while not _shutdown:
    try:
        schedule.run_pending()
    except Exception:
        logger.exception("Unhandled exception in schedule job — daemon continues")
    time.sleep(1)
```

Either fix independently suffices. Option A is preferred because it keeps the error log close to the failed function and matches the existing pattern in `BravosScraper.process_alert`.

---

## Warnings

### WR-01: `send_alert()` is called synchronously in the order critical path

**File:** `bravos/risk/gate.py:114-123` and `bravos/broker/connection.py:368-378`
**Issue:** `gate.check()` calls `send_alert()` inline when the circuit breaker trips. `send_alert()` calls `get_secret()` (GCP Secret Manager — network I/O) and then `smtplib.SMTP` (TCP connection + TLS handshake). This blocks the order path: `execute_signal` → `gate.check()` → `send_alert()` → GCP + SMTP. Under normal conditions this adds hundreds of milliseconds; under a network hiccup it can block for seconds.

The same issue applies in `connection.py:368` where `send_alert()` is called from the ibkr-reconnect background thread — less critical because it's already in a background thread, but still subject to long delays if GCP is slow.

**Fix:**
```python
# In gate.py, fire the alert in a daemon thread so check() returns immediately:
import threading

if not self._circuit_tripped and daily_pnl is not None and daily_pnl < DAILY_LOSS_THRESHOLD:
    self._circuit_tripped = True
    logger.critical("Circuit breaker TRIPPED: daily_pnl=%.2f < threshold=%.2f",
                    daily_pnl, DAILY_LOSS_THRESHOLD)
    threading.Thread(
        target=send_alert,
        args=("Circuit Breaker Triggered",
              f"Daily P&L circuit breaker triggered ...\ndaily_pnl={daily_pnl:.2f}"),
        daemon=True,
    ).start()
```

---

### WR-02: `notifier._spike_alerted` is a mutable module-level global without a lock

**File:** `bravos/notifications/notifier.py:27,59,68-81`
**Issue:** `_spike_alerted` and `_parse_outcomes` are module-level state. `record_parse_outcome()` reads and writes both without any lock. `process_alert()` in `scraper.py` can be called from the Gmail poller thread; `run_cycle` can be scheduled and fired from the main thread; both paths call `record_parse_outcome()`. Under CPython the GIL means no torn reads/writes on individual Python objects, but the check-then-set idiom on lines 68-69 (`if failure_count >= SPIKE_THRESHOLD and not _spike_alerted: _spike_alerted = True`) is a non-atomic read-modify-write. Two concurrent callers can both pass the `not _spike_alerted` check and both send an alert.

**Fix:**
```python
import threading
_spike_lock = threading.Lock()

def record_parse_outcome(parsed: dict) -> None:
    global _spike_alerted
    ...
    with _spike_lock:
        failure_count = sum(1 for ok in _parse_outcomes if not ok)
        if failure_count >= SPIKE_THRESHOLD and not _spike_alerted:
            _spike_alerted = True
            should_alert = True
        elif failure_count < SPIKE_THRESHOLD:
            _spike_alerted = False
            should_alert = False
        else:
            should_alert = False
    if should_alert:
        send_alert(...)
```

---

### WR-03: `_store_signal` and `process_alert` each open a new DB connection per alert call

**File:** `bravos/ingestion/scraper.py:219-251` and `bravos/ingestion/scraper.py:295-309`
**Issue:** Every call to `process_alert(url)` opens two separate psycopg2 connections: one inside `_store_signal()` (line 221) and one for `execute_signal()` (line 297). Neither connection is shared or pooled. On a busy alert day this causes unnecessary churn on the Cloud SQL Auth Proxy. More importantly, `_store_signal()` does not read `BRAVOS_DB_PASSWORD` from GCP Secret Manager (contrary to the pattern established in `secrets_config.py`); it reads from the environment with a hardcoded fallback `"change_me_at_deploy"` (lines 220, 296). If `BRAVOS_DB_PASSWORD` is not set in the environment, the connection will fail with an opaque authentication error at runtime — a silent startup misconfiguration.

**Fix:**
```python
# At BravosScraper.startup(), open one shared DB connection and pass it through.
# For the password, prefer GCP Secret Manager over env var to match the established pattern:
from bravos.config.secrets_config import get_secret

def _get_db_password() -> str:
    """Prefer GCP Secret Manager; fall back to env var."""
    try:
        return get_secret("bravos-db-password")
    except Exception:
        import os
        return os.environ["BRAVOS_DB_PASSWORD"]  # raise if not set — fail loud
```

---

### WR-04: `validate_secrets()` is never called at daemon startup

**File:** `scripts/run_ingestion.py` (entire file)
**Issue:** `secrets_config.validate_secrets()` exists precisely to confirm all required secrets are readable before the system starts placing orders. It is never called in `main()`. The `REQUIRED_SECRETS` list includes `bravos-alert-smtp-password`, `bravos-alert-smtp-from`, and the seven other secrets. A missing or misspelled secret will only surface at the first alert attempt, not at process start. In a trading daemon, silent late failures are harder to diagnose than fast startup failures.

**Fix:**
```python
# Add near the top of main(), before IBKR startup:
from bravos.config.secrets_config import validate_secrets

def main():
    ...
    try:
        validate_secrets()
    except RuntimeError as exc:
        logger.critical("Startup aborted — secret validation failed: %s", exc)
        sys.exit(1)
    ...
```

---

### WR-05: `test_send_alert_no_recipient` patches the wrong module path for `smtplib.SMTP`

**File:** `tests/test_notifications.py:28`
**Issue:** The patch target is `"smtplib.SMTP"` but the correct path for patching where the name is used is `"bravos.notifications.notifier.smtplib.SMTP"` (the same path used correctly in all other tests in the same file, lines 40, 54, 73, 89, 106). Python's `unittest.mock.patch` must patch the name in the module that imports it. The test currently passes only because `ALERT_EMAIL` is empty and the SMTP branch is never reached — the mock is never exercised. If a future code change reaches the SMTP path in the tested scenario, the mock will not intercept it and a real network connection may be attempted.

**Fix:**
```python
# Line 28: change the patch target
with patch.object(notifier, "get_secret") as mock_secret, \
     patch("bravos.notifications.notifier.smtplib.SMTP") as mock_smtp:
```

---

### WR-06: Circuit breaker resets 1 hour late during EDT (summer)

**File:** `scripts/run_ingestion.py:229`
**Issue:** `schedule.every().day.at("14:30").do(_gate.reset)` relies on system local time. The GCP VM is UTC. In winter (EST = UTC-5) this fires at 09:30 ET — correct. In summer (EDT = UTC-4) it fires at 10:30 ET — one hour after market open. If the prior day's P&L tripped the circuit breaker, no new orders can be placed for the first 60 minutes of every summer trading day. This is acknowledged in the comment as a "known DST limitation; acceptable for v1" but is worth tracking: a circuit breaker that stays latched for 1 hour after market open on every summer day means zero orders placed in that window.

**Fix:**
```python
# The `schedule` library supports timezone-aware scheduling:
schedule.every().day.at("09:30", tz="America/New_York").do(_gate.reset)
```

This eliminates the DST issue entirely with a one-line change using the existing `schedule` dependency.

---

## Info

### IN-01: GCP Project ID hardcoded in `secrets_config.py`

**File:** `bravos/config/secrets_config.py:10`
**Issue:** `PROJECT_ID = "crafty-water-453519-d7"` is a hardcoded string. If the project is ever migrated (DR scenario, separate staging project) this must be updated in code. The project constraint says "Credentials must never appear in code" — a GCP project ID is not a credential but it is deployment-specific configuration.

**Fix:**
```python
import os
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "crafty-water-453519-d7")
```

---

### IN-02: Unused `attempt` parameter in `BravosScraper._login()`

**File:** `bravos/ingestion/scraper.py:100`
**Issue:** `def _login(self, attempt: int = 0) -> bool:` declares an `attempt` parameter that is never read inside the method. The retry loop uses its own internal counter `i`. This parameter is dead code and could confuse a reader into thinking external callers control the starting attempt number.

**Fix:**
```python
def _login(self) -> bool:
    """Login with 3-attempt retry (per D-04). Returns True on success."""
```

---

### IN-03: Redundant `import datetime` inside `gate.check()` try block

**File:** `bravos/risk/gate.py:115`
**Issue:** `import datetime` on line 115 is inside the `try:` block of the circuit breaker alert. `datetime` is already imported at module level (line 1: `import datetime`). The inner import is a no-op that re-binds the name to the same module object. It adds noise and could confuse a reader into thinking the module-level import was intentionally omitted.

**Fix:** Remove the redundant `import datetime` at line 115; the module-level import is sufficient.

---

_Reviewed: 2026-05-20_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
