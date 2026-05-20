# Phase 7: Notifications and Monitoring Query — Pattern Map

**Mapped:** 2026-05-20
**Files analyzed:** 7 new/modified files
**Analogs found:** 7 / 7

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `bravos/notifications/__init__.py` | config | — | `bravos/risk/__init__.py` | exact |
| `bravos/notifications/notifier.py` | utility | request-response | `bravos/config/secrets_config.py` | role-match |
| `bravos/config/settings.py` | config | — | `bravos/config/settings.py` (modify) | exact |
| `bravos/config/secrets_config.py` | config | — | `bravos/config/secrets_config.py` (modify) | exact |
| `bravos/risk/gate.py` | service | event-driven | `bravos/broker/connection.py` (deferred-import hook pattern) | role-match |
| `bravos/broker/connection.py` | service | event-driven | `bravos/broker/connection.py` (modify) | exact |
| `scripts/run_ingestion.py` | service | event-driven | `scripts/run_ingestion.py` (modify) | exact |
| `tests/test_notifications.py` | test | — | `tests/test_execution.py` | exact |
| `queries/monitor.sql` | utility | CRUD | `infra/schema.sql` (schema reference) | partial |

---

## Pattern Assignments

### `bravos/notifications/__init__.py` (config, new subpackage init)

**Analog:** `bravos/risk/__init__.py`

**Full file pattern** (1 line):
```python
"""Bravos Trading System — Notifications package (Phase 7)."""
```

---

### `bravos/notifications/notifier.py` (utility, request-response)

**Analog:** `bravos/config/secrets_config.py`

**Imports pattern** — copy stdlib + existing project imports style (`secrets_config.py` lines 1-8, `settings.py` lines 1-2):
```python
"""
bravos/notifications/notifier.py — Fire-and-forget email alerting (Phase 7).

Single public function: send_alert(subject, body).
Never raises — logs warning on any failure so the daemon never crashes.
"""
import smtplib
import logging
from collections import deque
from email.mime.text import MIMEText

from bravos.config.secrets_config import get_secret
from bravos.config.settings import ALERT_EMAIL

logger = logging.getLogger(__name__)
```

**Module-level constants pattern** — copy `connection.py` module-level constant style (lines 26-44):
```python
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SUBJECT_PREFIX = "[Bravos Alert]"

# Parse spike rolling window state (D-03, Option C: lives here to avoid
# reverse dependency from scraper.py back into run_ingestion.py)
_parse_outcomes: deque = deque(maxlen=10)   # True=success, False=failure
_spike_alerted: bool = False
SPIKE_THRESHOLD = 3
```

**Core function pattern** — fire-and-forget with ALERT_EMAIL guard; use `secrets_config.get_secret()` for credentials:
```python
def send_alert(subject: str, body: str) -> None:
    """Fire-and-forget email alert. Never raises — logs warning on failure."""
    if not ALERT_EMAIL:
        logger.warning("send_alert: ALERT_EMAIL not set — skipping alert: %s", subject)
        return
    try:
        smtp_password = get_secret("bravos-alert-smtp-password")
        smtp_from = get_secret("bravos-alert-smtp-from")
        msg = MIMEText(body, "plain")
        msg["Subject"] = f"{SUBJECT_PREFIX} {subject}"
        msg["From"] = smtp_from
        msg["To"] = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_from, smtp_password)
            server.sendmail(smtp_from, [ALERT_EMAIL], msg.as_string())
        logger.info("Alert sent: %s", subject)
    except Exception:
        logger.warning("send_alert failed for subject=%r — continuing", subject, exc_info=True)
```

**Parse spike tracking function** — module-level state, one alert per breach (D-03):
```python
def record_parse_outcome(parsed: dict) -> None:
    """Record parse result; emit alert once if failure spike detected (D-03)."""
    global _spike_alerted
    is_failure = (
        parsed.get("confidence") == "low"
        or parsed.get("ticker") is None
    )
    _parse_outcomes.append(not is_failure)  # True = success
    failure_count = sum(1 for ok in _parse_outcomes if not ok)

    if failure_count >= SPIKE_THRESHOLD and not _spike_alerted:
        _spike_alerted = True
        logger.error(
            "Parse failure spike: %d failures in last %d signals",
            failure_count, len(_parse_outcomes),
        )
        import datetime
        send_alert(
            "Parse Failure Spike",
            f"Parse failure spike detected at {datetime.datetime.now().isoformat()}\n"
            f"Failures: {failure_count} out of last {len(_parse_outcomes)} signals\n"
            f"Check bravosresearch.com post format for unexpected changes.",
        )
    elif failure_count < SPIKE_THRESHOLD:
        _spike_alerted = False  # window recovered — re-arm for next spike
```

**Error handling pattern** — entire `send_alert()` is wrapped in a single `try/except Exception` that logs a warning and returns (never raises). Copy from `connection.py` lines 337-342 pattern (bare `except Exception: pass` for cleanup paths), adapted to log:
```python
    except Exception:
        logger.warning("send_alert failed for subject=%r — continuing", subject, exc_info=True)
```

---

### `bravos/config/settings.py` (config, modify — add one line)

**Analog:** `bravos/config/settings.py` (existing file)

**Pattern to copy** — existing env var lines 5-8, add one new line following same pattern:
```python
# Notifications (Phase 7)
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
```

**Placement:** After the `WEIGHT_PCT_PER_UNIT` line (line 27) and before the scraping URL block. Follow the existing pattern: uppercase constant, `os.environ.get("VAR_NAME", default)`, inline comment.

---

### `bravos/config/secrets_config.py` (config, modify — extend list)

**Analog:** `bravos/config/secrets_config.py` (existing file)

**Pattern to copy** — `REQUIRED_SECRETS` list (lines 12-20). Append two new entries following the same string-list style:
```python
REQUIRED_SECRETS = [
    "bravos-site-username",
    "bravos-site-password",
    "bravos-ibkr-username",
    "bravos-ibkr-password",
    "bravos-ibkr-port",
    "bravos-ibkr-clientid",
    "bravos-db-password",
    # Phase 7: SMTP alerting credentials
    "bravos-alert-smtp-password",
    "bravos-alert-smtp-from",
]
```

No other changes to this file.

---

### `bravos/risk/gate.py` (service, modify — add circuit breaker email hook)

**Analog:** `bravos/broker/connection.py` — deferred import pattern inside `_handle_exec_details` (lines 761-762)

**Hook location:** Lines 106-111. The email call goes immediately after `self._circuit_tripped = True` (line 107) and before `logger.critical(...)`.

**Deferred import pattern** (copy from `connection.py` line 761-762 style):
```python
# Gate 4: Daily loss circuit breaker (RISK-03)
if not self._circuit_tripped and daily_pnl is not None and daily_pnl < DAILY_LOSS_THRESHOLD:
    self._circuit_tripped = True
    logger.critical(
        "Circuit breaker TRIPPED: daily_pnl=%.2f < threshold=%.2f",
        daily_pnl, DAILY_LOSS_THRESHOLD,
    )
    # Phase 7: email alert (D-01, NOTF-01) — deferred import avoids circular dep
    try:
        from bravos.notifications.notifier import send_alert
        import datetime
        send_alert(
            "Circuit Breaker Triggered",
            f"Daily P&L circuit breaker triggered at {datetime.datetime.now().isoformat()}\n"
            f"daily_pnl={daily_pnl:.2f}  threshold={DAILY_LOSS_THRESHOLD:.2f}\n"
            f"No new orders will be placed for the remainder of the trading day.",
        )
    except Exception:
        logger.warning("Failed to send circuit breaker alert", exc_info=True)
```

**Why deferred import:** `gate.py` imports from `bravos.config.settings`; `notifier.py` imports from `bravos.config.settings`. No circular dependency exists, but deferring the import inside the block keeps the pattern consistent with `connection.py`'s deferred import of `positions` (line 761). The surrounding `try/except` on the import itself is belt-and-suspenders in case of any early-startup import error.

---

### `bravos/broker/connection.py` (service, modify — add reconnect-exhausted hook)

**Analog:** `bravos/broker/connection.py` (existing file)

**Hook location:** Lines 360-365, inside `_reconnect_loop()`, the `if attempt == len(_RETRY_DELAYS):` branch.

**Pattern to copy** — existing deferred import at line 761-762 and existing `try/except Exception: pass` pattern at lines 337-342:
```python
            if attempt == len(_RETRY_DELAYS):
                logger.critical(
                    "Reconnect failed after %s attempts (reason=%s) — retrying every 60s forever",
                    len(_RETRY_DELAYS),
                    reason,
                )
                # Phase 7: email alert (D-02a, NOTF-02)
                try:
                    from bravos.notifications.notifier import send_alert
                    import datetime
                    send_alert(
                        "IBKR Disconnect — Auto-Recovery Failed",
                        f"IB Gateway disconnect not auto-recovered after {len(_RETRY_DELAYS)} attempts.\n"
                        f"Reason: {reason}\n"
                        f"Time: {datetime.datetime.now().isoformat()}\n"
                        f"System is retrying every 60s. No orders can be placed until reconnected.\n"
                        f"Manual intervention may be required.",
                    )
                except Exception:
                    logger.warning("Failed to send IBKR disconnect alert", exc_info=True)
```

This block fires exactly once — when `attempt` first reaches `len(_RETRY_DELAYS)` (5). Subsequent 60s retries run with `delay = 60` and `attempt` incrementing past 5, so the `if attempt == len(_RETRY_DELAYS)` condition is never true again.

---

### `scripts/run_ingestion.py` (service, modify — add re-auth alert + parse spike wiring)

**Analog:** `scripts/run_ingestion.py` (existing file)

**Import addition pattern** — module-level imports block (lines 39-44). Add after existing imports:
```python
from bravos.notifications.notifier import send_alert, record_parse_outcome
```

**Re-auth failure hook** — lines 100-101. Add `send_alert()` call after the existing `logger.error()`:
```python
        if not _scraper._login():
            logger.error("Re-authentication failed in health check cycle")
            # Phase 7: email alert (D-02b, NOTF-02)
            import datetime
            send_alert(
                "Scraper Re-Authentication Failed",
                f"Bravos Research session re-authentication failed at "
                f"{datetime.datetime.now().isoformat()}\n"
                f"The Chrome driver session may be broken. Daemon will retry next cycle.\n"
                f"Manual inspection of the Chrome session may be required.",
            )
```

**Parse spike wiring** — `record_parse_outcome()` lives in `notifier.py` (Option C from RESEARCH). The call site in `run_ingestion.py` is wherever a parsed dict result is available from `process_alert()`. If `process_alert()` does not currently return the parsed dict, the planner must choose Option A (modify `process_alert()` to return it) or confirm Option C via a direct import in `scraper.py`. The wiring decision is documented in RESEARCH.md §Hook 4.

---

### `tests/test_notifications.py` (test, new file)

**Analog:** `tests/test_execution.py`

**File header pattern** (lines 1-10 of `test_execution.py`):
```python
"""
tests/test_notifications.py — Phase 7 Notifier unit tests.

All tests are Wave 0 stubs (skipped). Each test body is the full intended
implementation. Tests are unskipped as their implementing plan lands:
  - 07-01: test_send_alert_*, test_circuit_breaker_*, test_ibkr_disconnect_*,
           test_reauth_failure_*, test_parse_spike_*
"""
import datetime
from unittest.mock import MagicMock, patch
import pytest
```

**Test function pattern** — copy `test_execution.py` structure: no `@pytest.mark.skip`, plain `def test_*()`, imports inside function body, `unittest.mock.patch` for external calls (lines 20-30):
```python
def test_send_alert_no_recipient(monkeypatch):
    """send_alert() with missing ALERT_EMAIL logs warning and returns without sending."""
    monkeypatch.setattr("bravos.notifications.notifier.ALERT_EMAIL", "")
    with patch("bravos.notifications.notifier.get_secret") as mock_secret, \
         patch("smtplib.SMTP") as mock_smtp:
        from bravos.notifications.notifier import send_alert
        send_alert("test subject", "test body")
        mock_secret.assert_not_called()
        mock_smtp.assert_not_called()
```

**Mock pattern for smtplib** — patch at `smtplib.SMTP` (the class), use as context manager:
```python
def test_send_alert_smtp_failure_suppressed(monkeypatch):
    """send_alert() with SMTP failure logs warning and does not raise."""
    monkeypatch.setattr("bravos.notifications.notifier.ALERT_EMAIL", "test@example.com")
    with patch("bravos.notifications.notifier.get_secret", return_value="fake"), \
         patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        from bravos.notifications.notifier import send_alert
        send_alert("test subject", "test body")  # must not raise
```

**Circuit breaker / gate mock pattern** — follow `test_execution.py` lines 43-60: use `MagicMock()` for `db_conn`, patch `_is_market_hours`, call `gate.check()`:
```python
def test_circuit_breaker_sends_alert():
    """Circuit breaker email fires once when _circuit_tripped latches."""
    from bravos.risk.gate import RiskGate
    gate = RiskGate()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.side_effect = [
        {"action_type": "open", "weight_from": 0, "weight_to": 1, "ticker": "TEST"},
        (0,),  # open positions
    ]
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = -9999.0   # below DAILY_LOSS_THRESHOLD default -5000
    with patch("bravos.risk.gate._is_market_hours", return_value=True), \
         patch("bravos.notifications.notifier.send_alert") as mock_alert:
        gate.check(signal_id=1, db_conn=mock_conn, ibapp=mock_ibapp)
    mock_alert.assert_called_once()
    assert "Circuit Breaker" in mock_alert.call_args[0][0]
```

---

### `queries/monitor.sql` (utility, CRUD read-only)

**Analog:** No existing SQL query files. Pattern from RESEARCH.md Pattern 5 and verified schema columns.

**File header comment pattern** — follow `secrets_config.py` module docstring style, adapted as SQL comment block:
```sql
-- queries/monitor.sql — Bravos Trading System monitoring query (Phase 7)
--
-- Returns one row per signal showing full trade state: signal → gate → order → lots.
-- Run: psql -h 127.0.0.1 -U bravos -d bravos_trading -f queries/monitor.sql
--
-- gate_passed IS NULL  → signal was not risk-checked (low confidence or duplicate)
-- order_status IS NULL → signal was blocked before order creation
-- open_quantity / realized_pnl are per-ticker aggregates, not per-signal
```

**Core query pattern** — CTEs + LEFT JOINs + DISTINCT ON (verified schema columns from `infra/schema.sql` and migration files):
```sql
WITH latest_gate AS (
    -- Most recent gate check per signal (D-13: DISTINCT ON is idiomatic PostgreSQL)
    SELECT DISTINCT ON (signal_id)
        signal_id,
        gate_passed,
        checked_at
    FROM risk_gate_log
    ORDER BY signal_id, checked_at DESC
),
open_qty AS (
    -- Current open quantity per ticker (open lots only)
    SELECT ticker, SUM(quantity) AS open_quantity
    FROM position_lots
    WHERE lot_closed_at IS NULL
    GROUP BY ticker
),
realized AS (
    -- Realized P&L per ticker (closed lots only)
    SELECT ticker, SUM(pnl) AS realized_pnl
    FROM position_lots
    WHERE lot_closed_at IS NOT NULL
    GROUP BY ticker
)
SELECT
    s.id               AS signal_id,
    s.parsed_at,
    s.ticker,
    s.action_type,
    s.confidence,
    lg.gate_passed,
    o.status           AS order_status,
    o.fill_price,
    oq.open_quantity,
    r.realized_pnl
FROM signals s
LEFT JOIN latest_gate lg  ON lg.signal_id = s.id
LEFT JOIN orders o        ON o.signal_id  = s.id
LEFT JOIN open_qty oq     ON oq.ticker    = s.ticker
LEFT JOIN realized r      ON r.ticker     = s.ticker
ORDER BY s.parsed_at DESC;
```

**Column verification** (from schema files):
- `signals.parsed_at` — `TIMESTAMPTZ DEFAULT NOW()` (schema.sql)
- `signals.confidence` — `VARCHAR(10)` (schema.sql)
- `orders.status` — `VARCHAR(20) DEFAULT 'pending'` (schema.sql)
- `orders.fill_price` — `NUMERIC(10,2)` (migrate_phase5.sql)
- `orders.signal_id` — FK to `signals.id` (schema.sql)
- `risk_gate_log.gate_passed` — `BOOLEAN NOT NULL` (migrate_phase4.sql)
- `risk_gate_log.signal_id` — FK to `signals.id` (migrate_phase4.sql)
- `risk_gate_log.checked_at` — `TIMESTAMPTZ DEFAULT NOW()` (migrate_phase4.sql)
- `position_lots.lot_closed_at` — `TIMESTAMPTZ` (schema.sql)
- `position_lots.quantity` — `INTEGER NOT NULL` (schema.sql)
- `position_lots.pnl` — `NUMERIC(12,2)` (schema.sql)

---

## Shared Patterns

### Deferred Import (avoid circular dependency)
**Source:** `bravos/broker/connection.py` line 761-762
**Apply to:** `gate.py` and `connection.py` hook sites
```python
# Inside the if-block where alert fires, not at module level:
from bravos.notifications.notifier import send_alert
```

### Belt-and-suspenders try/except at call site
**Source:** `bravos/broker/connection.py` lines 337-342 (disconnect cleanup) and lines 259-260 (error callback)
**Apply to:** All hook sites in `gate.py` and `connection.py`
```python
try:
    from bravos.notifications.notifier import send_alert
    import datetime
    send_alert(subject, body)
except Exception:
    logger.warning("Failed to send <event> alert", exc_info=True)
```
Note: `send_alert()` itself never raises (it catches internally), so the outer `try/except` guards only against import failures during early daemon startup.

### Secrets Access Pattern
**Source:** `bravos/config/secrets_config.py` lines 23-28
**Apply to:** `notifier.py` (SMTP password and sender address)
```python
from bravos.config.secrets_config import get_secret
# Inside send_alert():
smtp_password = get_secret("bravos-alert-smtp-password")
smtp_from = get_secret("bravos-alert-smtp-from")
```

### Env Var Settings Pattern
**Source:** `bravos/config/settings.py` lines 5-8
**Apply to:** `settings.py` addition
```python
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
```

### Logger Pattern
**Source:** Every module in the codebase (e.g., `gate.py` line 26, `connection.py` line 22)
**Apply to:** `notifier.py`
```python
logger = logging.getLogger(__name__)
```

### Module-Level Flag + Latch Pattern
**Source:** `scripts/run_ingestion.py` line 58 (`_shutdown = False`); `gate.py` line 54 (`self._circuit_tripped: bool = False`)
**Apply to:** `notifier.py` module-level `_spike_alerted: bool = False`
```python
_spike_alerted: bool = False
```

### Module docstring + inline plan reference
**Source:** `gate.py` lines 1-13, `connection.py` lines 1-11
**Apply to:** `notifier.py` file header
```python
"""
bravos/notifications/notifier.py — Fire-and-forget email alerting (Phase 7).
...
"""
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `queries/monitor.sql` | utility | CRUD read | No existing SQL query files in the repo; pattern from RESEARCH.md Pattern 5 + verified schema columns |

---

## Metadata

**Analog search scope:** `bravos/` (all subpackages), `scripts/`, `tests/`, `infra/`
**Files scanned:** 14 Python source files, 2 init files, 2 infra SQL files
**Key pattern source files:**
- `/home/chris_s_dodd/bravos/bravos/config/secrets_config.py`
- `/home/chris_s_dodd/bravos/bravos/config/settings.py`
- `/home/chris_s_dodd/bravos/bravos/risk/gate.py`
- `/home/chris_s_dodd/bravos/bravos/broker/connection.py`
- `/home/chris_s_dodd/bravos/scripts/run_ingestion.py`
- `/home/chris_s_dodd/bravos/tests/test_execution.py`
- `/home/chris_s_dodd/bravos/tests/conftest.py`
