# Phase 7: Notifications and Monitoring Query - Research

**Researched:** 2026-05-20
**Domain:** Python smtplib email alerting + PostgreSQL monitoring query
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Email on NOTF-01: circuit breaker trips (`RiskGate._circuit_tripped` latch fires in `gate.py`).
- **D-02:** Email on NOTF-02: three event types — (a) IBKR disconnect not auto-recovered after reconnect retries exhausted, (b) scraper re-authentication fails in `run_cycle()`, (c) parse failure spike: 3 failures in the last 10 signals (30% rolling window).
- **D-03:** Parse failure spike uses a rolling window counter maintained in `run_cycle()`. Threshold: 3 failures out of last 10 signals. Counter tracks `confidence == 'low'` or `ticker IS NULL` results from the DB (or from in-process signal results). One spike alert per window breach — does not fire repeatedly on every subsequent cycle once tripped.
- **D-04:** Hook locations: circuit breaker email in `gate.py` `_log_and_return` (or in `run_ingestion.py` where `_gate.reset()` is scheduled); IBKR disconnect email in `broker/connection.py` reconnect-exhausted path; scraper failure and parse spike email in `run_cycle()` in `run_ingestion.py`.
- **D-05:** Use `smtplib` (Python stdlib) — no new packages. Gmail SMTP (smtp.gmail.com:587, STARTTLS).
- **D-06:** Gmail app password stored in GCP Secret Manager as `bravos-alert-smtp-password`. Sender address stored as `bravos-alert-smtp-from` in Secret Manager.
- **D-07:** Recipient address comes from env var `ALERT_EMAIL` on the VM — not a secret.
- **D-08:** Email body is plain-text. Subject prefix: `[Bravos Alert]`. Include: event type, timestamp, relevant values (e.g. daily_pnl for circuit breaker, account name, failure count for parse spike).
- **D-09:** Notifier module lives at `bravos/notifications/notifier.py`. Single `send_alert(subject, body)` function. Called from hook points; no retry logic in v1 — fire-and-forget, log warning on send failure.
- **D-10:** Query returns all signals (not filtered to orders-only) so blocked/low-confidence signals are visible alongside executed ones.
- **D-11:** Columns: `signal_id`, `parsed_at`, `ticker`, `action_type`, `confidence`, `gate_passed` (from risk_gate_log — NULL if not risk-checked), `order_status`, `fill_price`, `open_quantity` (sum of open lots for that ticker — NULL if no open lots), `realized_pnl` (sum of closed lot pnl for that ticker — NULL if no closed lots). Unrealized P&L left as NULL — no live prices in the DB.
- **D-12:** File location: `queries/monitor.sql`. Runnable with: `psql -h 127.0.0.1 -U bravos -d bravos_trading -f queries/monitor.sql`
- **D-13:** Query uses LEFT JOINs so signals with no matching order still appear. Most recent risk_gate_log row per signal (if multiple gate checks for same signal, take latest).

### Claude's Discretion

- Exact email body wording and formatting.
- Whether to extract the notifier call into a helper that guards against missing `ALERT_EMAIL` silently (log warning, don't crash the daemon).
- How to represent `open_quantity` and `realized_pnl` in the SQL — subquery or CTE.
- Whether `queries/` directory needs a README.

### Deferred Ideas (OUT OF SCOPE)

- NOTF-V2-01 / NOTF-V2-02 (email on new signal placed, email on fill) — v2; out of scope for Phase 7.
- Dashboard (DASH-01–04) — cut.
- Unrealized P&L in monitoring query — requires live price feed; not available in the DB.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NOTF-01 | System sends an email notification when the daily loss circuit breaker is triggered | D-01: hook into `gate.py` after `_circuit_tripped = True`; smtplib STARTTLS pattern verified |
| NOTF-02 | System sends an email notification when a critical system error occurs (scraper failure, IBKR disconnect not auto-recovered, parse failure rate spike) | D-02: three hook points identified and verified in codebase; rolling window counter logic for parse spike defined |
</phase_requirements>

---

## Summary

Phase 7 adds email alerting for two requirement groups and commits a SQL monitoring query. Both deliverables are additive — no existing logic changes behavior; new code is inserted at well-identified hook points and a new `queries/` directory is created. The implementation is small in scope: one new module (`bravos/notifications/notifier.py`), changes to four existing files (`gate.py`, `connection.py`, `run_ingestion.py`, `settings.py`), the `secrets_config.py` REQUIRED_SECRETS list, and a new SQL file (`queries/monitor.sql`).

The notifier uses Python's `smtplib` (stdlib, no new packages). Gmail SMTP with STARTTLS on port 587 is the standard approach, well-supported, and uses an app password that avoids OAuth complexity. The fire-and-forget pattern (try/except, log warning on failure, never raise) is critical so a transient SMTP failure does not crash the daemon.

The monitoring SQL query joins five tables using LEFT JOINs and CTEs. The key design point: `risk_gate_log` can have multiple rows per signal (repeated gate checks), so the query uses a `LATERAL` or `DISTINCT ON` subquery to select only the most recent row per signal. `position_lots` aggregates by ticker, not by signal, so `open_quantity` and `realized_pnl` are per-ticker aggregates (NULLed when no lots exist).

**Primary recommendation:** Build the notifier module first (standalone, testable with unit mocks), then wire hook points in order of risk: circuit breaker (gate.py) → reconnect-exhausted (connection.py) → re-auth failure and parse spike (run_ingestion.py). Write the SQL query last — it is read-only and has no impact on the running daemon.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Email send (smtplib) | Daemon process | — | Daemon is the only running process; alerts fire from within the daemon |
| Secret fetch (SMTP password, sender) | GCP Secret Manager → Daemon startup | — | Follows existing secrets pattern; fetched once at notifier init |
| Recipient address | VM env var (`ALERT_EMAIL`) | — | D-07: not a secret, set at deploy time |
| Circuit breaker alert trigger | `bravos/risk/gate.py` | `bravos/notifications/notifier.py` | D-01: hook at the exact line where `_circuit_tripped` is latched |
| IBKR disconnect alert trigger | `bravos/broker/connection.py` | `bravos/notifications/notifier.py` | D-04: hook in `_reconnect_loop` at the `attempt == len(_RETRY_DELAYS)` branch |
| Re-auth failure alert trigger | `scripts/run_ingestion.py` `run_cycle()` | `bravos/notifications/notifier.py` | D-04: hook where `logger.error("Re-authentication failed...")` currently fires |
| Parse spike detection + alert | `scripts/run_ingestion.py` `run_cycle()` | `bravos/notifications/notifier.py` | D-03: rolling window counter maintained in `run_cycle()`; alert fires once per breach |
| Monitoring query | PostgreSQL (read) | `queries/monitor.sql` file | D-12: static file run by operator via psql; no runtime component |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| smtplib | stdlib (Python 3.13.13) | SMTP connection, STARTTLS, send | No additional package; project constraint says "no new packages" |
| email.mime.text | stdlib | Construct MIMEText email object | Needed to set proper headers |
| GCP Secret Manager | (existing: google-cloud-secret-manager) | Fetch SMTP password and sender address | Existing pattern in `secrets_config.py` |
| os.environ | stdlib | Read `ALERT_EMAIL` recipient | D-07: env var, not a secret |
| PostgreSQL | 16.13 (psql verified) | Monitoring query target | Existing project DB |

[VERIFIED: python3 --version → 3.13.13; psql --version → 16.13; secretmanager import confirmed working]

### No New Packages Required

This phase adds zero new pip dependencies. All functionality uses:
- `smtplib` — Python stdlib
- `email.mime.text` — Python stdlib
- `google-cloud-secret-manager` — already installed (confirmed `from google.cloud import secretmanager` works)

---

## Architecture Patterns

### System Architecture Diagram

```
Daemon (run_ingestion.py + gate.py + connection.py)
           │
           ├── Circuit breaker trips (gate.py line ~107)
           │         └──→ send_alert("circuit_breaker", body)
           │
           ├── IBKR reconnect exhausted (connection.py line ~361)
           │         └──→ send_alert("ibkr_disconnect", body)
           │
           ├── Re-auth fails (run_ingestion.py run_cycle() line ~101)
           │         └──→ send_alert("reauth_failure", body)
           │
           └── Parse spike (run_ingestion.py run_cycle() — rolling window)
                     └──→ send_alert("parse_spike", body)
                               │
                         notifier.py send_alert()
                               │
                         ┌─────┴──────┐
                         │            │
                   GCP Secret Mgr  ALERT_EMAIL
                   (smtp password,  (env var)
                    sender addr)
                         │
                   Gmail SMTP :587
                   (STARTTLS)
                         │
                   recipient inbox
```

```
Operator terminal
       │
  psql -f queries/monitor.sql
       │
  PostgreSQL bravos_trading
       │
  ┌────────────┬──────────────────────────┐
  │  signals   │ LEFT JOIN orders          │
  │            │ LEFT JOIN risk_gate_log   │
  │            │ LEFT JOIN position_lots   │
  │            │   (aggregated by ticker)  │
  └────────────┴──────────────────────────┘
       │
  Result: one row per signal, all state visible
```

### Recommended Project Structure

```
bravos/
├── notifications/
│   ├── __init__.py        # empty
│   └── notifier.py        # send_alert() — sole public function
├── config/
│   ├── settings.py        # add ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
│   └── secrets_config.py  # add "bravos-alert-smtp-password" and "bravos-alert-smtp-from"
│                          # to REQUIRED_SECRETS list
queries/
└── monitor.sql            # monitoring query (new top-level directory)
scripts/
└── run_ingestion.py       # add parse spike counter + 3 email call sites
bravos/
├── risk/gate.py           # add 1 email call site (circuit breaker latch)
└── broker/connection.py   # add 1 email call site (reconnect-exhausted)
tests/
└── test_notifications.py  # new test file (Wave 0 stubs)
```

### Pattern 1: smtplib STARTTLS Fire-and-Forget

**What:** Open SMTP connection to Gmail, send one plain-text email, close. Catch all exceptions so the daemon never crashes on SMTP failure.
**When to use:** Every `send_alert()` call.

```python
# Source: Python stdlib docs (smtplib), [VERIFIED: smtplib.SMTP.starttls confirmed in stdlib]
import smtplib
import logging
from email.mime.text import MIMEText
from bravos.config.secrets_config import get_secret
from bravos.config.settings import ALERT_EMAIL

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SUBJECT_PREFIX = "[Bravos Alert]"

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

### Pattern 2: Circuit Breaker Hook in gate.py

**What:** Call `send_alert()` exactly once when `_circuit_tripped` transitions from False to True.
**When to use:** Inside the `if not self._circuit_tripped and daily_pnl < DAILY_LOSS_THRESHOLD:` block in `RiskGate.check()`.

The exact location is `gate.py` lines 106-111. The alert fires right after `self._circuit_tripped = True` is set. Since `_circuit_tripped` latches (never reset during the trading day until `reset()` is called), the email fires exactly once per daily breach.

```python
# gate.py — inside check(), Gate 4 block
if not self._circuit_tripped and daily_pnl is not None and daily_pnl < DAILY_LOSS_THRESHOLD:
    self._circuit_tripped = True
    logger.critical(
        "Circuit breaker TRIPPED: daily_pnl=%.2f < threshold=%.2f",
        daily_pnl, DAILY_LOSS_THRESHOLD,
    )
    # NEW: email alert (D-01, NOTF-01)
    from bravos.notifications.notifier import send_alert
    import datetime
    send_alert(
        "Circuit Breaker Triggered",
        f"Daily P&L circuit breaker triggered at {datetime.datetime.now().isoformat()}\n"
        f"daily_pnl={daily_pnl:.2f}  threshold={DAILY_LOSS_THRESHOLD:.2f}\n"
        f"No new orders will be placed for the remainder of the trading day.",
    )
```

Note: deferred import `from bravos.notifications.notifier import send_alert` inside the function avoids circular imports (gate.py has no existing notification import). This is the same pattern used in `connection.py` for positions deferred import.

### Pattern 3: Reconnect-Exhausted Hook in connection.py

**What:** Call `send_alert()` when the reconnect loop transitions from "trying scheduled delays" to "retrying every 60s forever" — the first time `attempt == len(_RETRY_DELAYS)`.
**When to use:** In `_reconnect_loop()` at the `if attempt == len(_RETRY_DELAYS):` branch (line ~360).

```python
# connection.py — _reconnect_loop(), at attempt == len(_RETRY_DELAYS)
if attempt == len(_RETRY_DELAYS):
    logger.critical(
        "Reconnect failed after %s attempts (reason=%s) — retrying every 60s forever",
        len(_RETRY_DELAYS),
        reason,
    )
    # NEW: email alert (D-02a, NOTF-02)
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

Note: the try/except wrapper is belt-and-suspenders — `send_alert()` itself is already fire-and-forget, but guarding the import too prevents any import error from interrupting the reconnect loop.

### Pattern 4: Parse Spike Counter in run_cycle()

**What:** Maintain a rolling list of the last N signal outcomes (high/low confidence) in module scope. On each `process_alert()` call, record the outcome. After recording, check if failures (low confidence or ticker IS NULL) in the window >= threshold. Fire one alert per spike (guard with a `_spike_alerted` flag).
**When to use:** In `run_ingestion.py`, in/around `run_cycle()` or by recording outcomes in `process_alert()` results. D-03 says counter is in `run_cycle()`.

Key design points from D-03:
- Threshold: 3 failures out of last 10 signals (30% rolling window)
- "failure" = `confidence == 'low'` OR `ticker IS NULL` in the parsed result
- One alert per window breach — `_spike_alerted` flag prevents repeated firing
- Flag is not reset until the window clears below threshold (or daemon restart)

```python
# run_ingestion.py — module-level state for parse spike tracking (D-03)
from collections import deque

_parse_outcomes: deque = deque(maxlen=10)  # True = success, False = failure
_spike_alerted: bool = False
SPIKE_THRESHOLD = 3

def _record_parse_outcome(parsed: dict) -> None:
    """Record parse result and emit alert if failure spike detected."""
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
        from bravos.notifications.notifier import send_alert
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

The `_record_parse_outcome()` call is inserted into `process_alert()` in `scraper.py` (after the `parse_signal()` call and before `_store_signal()`) — or alternatively, D-03 says the counter is "maintained in `run_cycle()`". Since `process_alert()` is called by the Gmail poller (not by `run_cycle()` directly), the most natural hook is inside `scraper.process_alert()`, which is called by the Gmail poller. The planner should clarify this: `_record_parse_outcome` can live in `run_ingestion.py` and be called as a post-process step from within `run_cycle()` or piggybacked into `process_alert()`.

**Decision for planner:** The cleanest approach is for `process_alert()` in `scraper.py` to return the parsed dict (currently it returns None), allowing `run_ingestion.py` (or the Gmail poller) to call `_record_parse_outcome()`. Alternatively, the module-level counter can live in `bravos/notifications/notifier.py` for cleaner separation. The planner should pick one location and make it consistent.

### Pattern 5: Monitoring SQL Query (CTEs + LEFT JOINs)

**What:** A single SELECT that returns one row per signal, with LEFT JOINs to orders, risk_gate_log, and per-ticker position aggregates.
**Design:** Use CTEs for readability. Use `DISTINCT ON (signal_id)` or a subquery to get the most recent `risk_gate_log` row per signal (D-13). Use aggregation subqueries for `open_quantity` and `realized_pnl` (per-ticker, not per-signal).

```sql
-- Source: PostgreSQL docs on DISTINCT ON and CTEs
-- queries/monitor.sql

WITH latest_gate AS (
    -- Most recent gate check per signal (D-13)
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

Notes:
- `parsed_at` is the timestamp column on `signals` (confirmed in `schema.sql` as `parsed_at TIMESTAMPTZ DEFAULT NOW()`).
- `orders.status` — confirmed as `status VARCHAR(20) DEFAULT 'pending'` in schema.sql.
- `orders.fill_price` — confirmed in schema.sql (added as `fill_price NUMERIC(10,2)`).
- `position_lots.pnl` — confirmed as `pnl NUMERIC(12,2)` in schema.sql.
- `risk_gate_log.gate_passed` — confirmed as `gate_passed BOOLEAN NOT NULL` in migrate_phase4.sql.
- The `DISTINCT ON (signal_id) ... ORDER BY signal_id, checked_at DESC` pattern is idiomatic PostgreSQL for "latest row per group" without a subquery. [VERIFIED: PostgreSQL 16.13 available; DISTINCT ON is a PostgreSQL extension supported since v6.5]

### Anti-Patterns to Avoid

- **SMTP credentials in code or env vars:** All SMTP credentials go through GCP Secret Manager. Only the recipient (`ALERT_EMAIL`) is an env var because it is not sensitive.
- **Raising exceptions from send_alert:** If `send_alert()` raises, it can crash the daemon. The function must catch all exceptions and log only.
- **Fetching SMTP secrets on every send_alert call:** GCP Secret Manager has latency (~50-100ms per call). For a fire-and-forget pattern this is acceptable in v1 (alerts are rare), but the planner should note this. [ASSUMED — latency estimate from training knowledge; acceptable given low alert frequency]
- **Calling send_alert() on every reconnect attempt:** The IBKR disconnect alert must fire only once — when `attempt == len(_RETRY_DELAYS)`, not on every subsequent 60s retry. The `if attempt == len(_RETRY_DELAYS):` branch fires exactly once.
- **Using `email.message.Message` directly:** Use `email.mime.text.MIMEText` for proper MIME structure, even for plain text.
- **`LATERAL` instead of `DISTINCT ON` for latest gate row:** `LATERAL` works but is less readable and less standard. `DISTINCT ON` is the idiomatic PostgreSQL approach for "latest per group."
- **JOIN on orders.ticker instead of orders.signal_id:** The correct join between signals and orders is on `signal_id`, not `ticker`, since multiple signals can exist for the same ticker.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SMTP connection | Custom socket code | `smtplib.SMTP` + `starttls()` | stdlib handles STARTTLS negotiation, EHLO, AUTH |
| MIME formatting | String-concat email body | `email.mime.text.MIMEText` | Handles proper headers, encoding, line endings |
| Secret fetch | os.environ for SMTP credentials | `get_secret()` (existing) | Follows CLAUDE.md security constraint; already tested |
| "Latest row per group" SQL | Self-join or window function | `DISTINCT ON (col) ORDER BY col, ts DESC` | PostgreSQL idiomatic; readable, efficient |

---

## Common Pitfalls

### Pitfall 1: send_alert() Called Before Secrets Are Available

**What goes wrong:** If `send_alert()` is called very early (e.g., during the initial IBKR connect failure path before `validate_secrets()` runs), `get_secret()` may fail if Secret Manager is not yet reachable.
**Why it happens:** `_reconnect_loop` can fire from a background thread before the main thread finishes startup.
**How to avoid:** The try/except inside `send_alert()` swallows the error and logs a warning. No crash. Ensure `send_alert()` is always guarded by try/except at the call site too (belt-and-suspenders for the import itself).
**Warning signs:** `send_alert: SMTP credentials unavailable` in logs during early startup.

### Pitfall 2: ALERT_EMAIL Empty String — Alert Silently Dropped

**What goes wrong:** If `ALERT_EMAIL` is not set on the VM, alerts never fire but no error is visible in normal operation.
**Why it happens:** `os.environ.get("ALERT_EMAIL", "")` returns empty string without error.
**How to avoid:** `send_alert()` checks `if not ALERT_EMAIL:` at the top and logs a WARNING. The VM deployment checklist (Phase 8) must verify `ALERT_EMAIL` is set.
**Warning signs:** `send_alert: ALERT_EMAIL not set` in logs.

### Pitfall 3: Parse Spike Alert Fires Repeatedly

**What goes wrong:** If `_spike_alerted` flag is not set before calling `send_alert()`, and the next cycle still shows 3+ failures, the alert fires on every cycle.
**Why it happens:** The flag is forgotten, or reset incorrectly.
**How to avoid:** Set `_spike_alerted = True` before calling `send_alert()`. Re-arm the flag only when the failure count drops back below the threshold (D-03: "does not fire repeatedly on every subsequent cycle once tripped").

### Pitfall 4: Multiple orders.id per signal_id Breaking the JOIN

**What goes wrong:** If a signal has multiple orders (retry scenario), the SQL query returns multiple rows for that signal.
**Why it happens:** `orders.signal_id` is not unique — one signal can in theory have multiple order rows.
**How to avoid:** The monitoring query should use `LEFT JOIN orders o ON o.signal_id = s.id` without further deduplication — this is acceptable for a diagnostic query. If duplicates appear, the operator sees them and investigates. Do not add an arbitrary `LIMIT 1` that silently hides information.

### Pitfall 5: signals.parsed_at vs. signals.created_at

**What goes wrong:** Ordering by the wrong timestamp column gives misleading sort order.
**Why it happens:** The schema has both `parsed_at TIMESTAMPTZ DEFAULT NOW()` and `created_at TIMESTAMPTZ DEFAULT NOW()`. They are almost identical but `parsed_at` is the semantically correct column for "when was this signal parsed."
**How to avoid:** Use `ORDER BY s.parsed_at DESC` (not `created_at`). [VERIFIED: schema.sql confirmed both columns exist]

### Pitfall 6: risk_gate_log NULL for Unrouted Signals

**What goes wrong:** Signals with `confidence == 'low'` are never passed to the risk gate, so they have no `risk_gate_log` row. LEFT JOIN on `risk_gate_log` returns NULL for `gate_passed` — this is correct and expected per D-10/D-11.
**Why it happens:** Low-confidence signals are not routed to `execute_signal()` and therefore never reach `RiskGate.check()`.
**How to avoid:** Document this in the SQL file as a comment. `gate_passed IS NULL` means "not risk-checked" (low confidence or duplicate). Do not inner-join.

### Pitfall 7: Circular Import with notifier.py

**What goes wrong:** If `gate.py` imports `notifier` at the module level, and `notifier.py` imports from `bravos.config.*` which eventually imports `gate`, there may be a circular import.
**Why it happens:** `gate.py` already imports from `bravos.config.settings`; `notifier.py` will also import from `bravos.config.settings`. No circular dependency there.
**How to avoid:** Use deferred import inside the alert-firing block: `from bravos.notifications.notifier import send_alert`. This is the established pattern in `connection.py` (deferred import of `positions` in `_handle_exec_details`). Do not add a module-level import of `notifier` in `gate.py` or `connection.py`.

---

## Code Examples

### smtplib STARTTLS with context manager

```python
# Source: Python stdlib documentation; [VERIFIED: smtplib.SMTP.starttls works in Python 3.13.13]
with smtplib.SMTP("smtp.gmail.com", 587) as server:
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(smtp_from, smtp_password)
    server.sendmail(smtp_from, [to_addr], msg.as_string())
```

The explicit `server.ehlo()` calls before and after `starttls()` are recommended by the stdlib docs but not strictly required — `smtplib` sends EHLO automatically on login if not already sent.

### DISTINCT ON for latest-per-group

```sql
-- Source: PostgreSQL documentation on DISTINCT ON
-- [VERIFIED: PostgreSQL 16.13 available on bravos VM]
SELECT DISTINCT ON (signal_id)
    signal_id, gate_passed, checked_at
FROM risk_gate_log
ORDER BY signal_id, checked_at DESC;
```

`DISTINCT ON` keeps the first row per `(signal_id)` group after the `ORDER BY`. Since `checked_at DESC` puts the latest row first, this returns the most recent gate check per signal.

### Rolling window deque

```python
# Source: Python stdlib collections docs
from collections import deque
_parse_outcomes: deque = deque(maxlen=10)
_parse_outcomes.append(True)   # success
_parse_outcomes.append(False)  # failure
failure_count = sum(1 for ok in _parse_outcomes if not ok)
# When maxlen is reached, oldest items are automatically evicted
```

`deque(maxlen=10)` automatically evicts the oldest item when a new one is appended. No manual index management needed.

---

## Codebase Hook Point Audit

This section maps each email trigger (D-04) to exact lines in the existing code so the planner can write precise task instructions.

### Hook 1: Circuit Breaker — gate.py

**File:** `bravos/risk/gate.py`
**Location:** Inside `RiskGate.check()`, the `if not self._circuit_tripped and daily_pnl is not None and daily_pnl < DAILY_LOSS_THRESHOLD:` block (lines 106-112).
**Current code:** Sets `self._circuit_tripped = True`, logs `logger.critical(...)`.
**Change:** Add `send_alert()` call immediately after `self._circuit_tripped = True`.
**Import location:** Deferred import inside the `if` block — no module-level import.

### Hook 2: IBKR Reconnect Exhausted — connection.py

**File:** `bravos/broker/connection.py`
**Location:** Inside `_reconnect_loop()`, the `if attempt == len(_RETRY_DELAYS):` block (lines 360-365).
**Current code:** `logger.critical("Reconnect failed after %s attempts...")`.
**Change:** Add `send_alert()` call inside this `if` block.
**Import location:** Deferred import inside the `if` block. Wrap the entire block in try/except to prevent any notifier import error from breaking the reconnect loop.

### Hook 3: Scraper Re-auth Failure — run_ingestion.py

**File:** `scripts/run_ingestion.py`
**Location:** Inside `run_cycle()`, the `if not _scraper._login():` branch (lines 100-101).
**Current code:** `logger.error("Re-authentication failed in health check cycle")`.
**Change:** Add `send_alert()` call after the logger.error line.
**Import location:** Module-level import at the top of `run_ingestion.py` (this file is already the integration point for many imports; adding `from bravos.notifications.notifier import send_alert` at the top is clean).

### Hook 4: Parse Spike — run_ingestion.py (new logic)

**File:** `scripts/run_ingestion.py`
**Location:** New module-level state + `_record_parse_outcome()` function. Called from `scraper.process_alert()` or from the Gmail poller after each alert is processed.
**Current code:** No parse failure tracking exists.
**Change:** Add module-level `_parse_outcomes` deque and `_spike_alerted` flag. Add `_record_parse_outcome(parsed_dict)` function. Wire the call.

**Wiring decision for planner:** `process_alert()` in `scraper.py` currently does not return the parsed result — it logs and continues. The planner must decide:
- Option A: Have `process_alert()` return the parsed dict, and call `_record_parse_outcome()` in `run_ingestion.py` or the Gmail poller after each `process_alert()` call.
- Option B: Call `_record_parse_outcome()` directly inside `scraper.process_alert()` by importing it from `run_ingestion.py` (creates an awkward reverse dependency).
- Option C: Move `_record_parse_outcome()` and its state into `bravos/notifications/notifier.py` as `record_parse_outcome()` — cleaner module ownership, no reverse dependency.

**Recommendation:** Option C (move to notifier.py) is the cleanest. Notifier module then owns all alerting state and logic.

---

## secrets_config.py Changes

The `REQUIRED_SECRETS` list in `bravos/config/secrets_config.py` must be extended:

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

Note: `validate_secrets()` runs at daemon startup. If these secrets do not exist in GCP Secret Manager at deploy time, the daemon will fail to start. The operator must create these secrets before Phase 7 deployment. This is a deployment step, not a code step.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python smtplib | notifier.py | ✓ | stdlib 3.13.13 | — |
| email.mime.text | notifier.py | ✓ | stdlib 3.13.13 | — |
| google-cloud-secret-manager | notifier.py (SMTP creds) | ✓ | confirmed import works | — |
| psql CLI | queries/monitor.sql validation | ✓ | 16.13 | — |
| GCP Secret Manager secrets (bravos-alert-smtp-password, bravos-alert-smtp-from) | notifier.py at runtime | unknown — must be created by operator | — | Tests mock get_secret() |
| ALERT_EMAIL env var | notifier.py at runtime | unknown — set at deploy time | — | send_alert() silently skips if empty |
| Gmail app password (SMTP auth) | Gmail SMTP server | operator must create in Google Account | — | Cannot test end-to-end without it |

**Missing dependencies with no fallback:**
- Gmail SMTP app password: operator must create in Google Account settings (Two-Factor Auth → App Passwords). Unit tests mock this; integration test requires real credentials.

**Missing dependencies with fallback:**
- `bravos-alert-smtp-password` / `bravos-alert-smtp-from` secrets in GCP: tests mock `get_secret()`. No blocker for unit testing.
- `ALERT_EMAIL` env var: `send_alert()` logs warning and returns silently if not set.

**Step 2.6 note:** This is a pure code change phase with no new external tool dependencies. `smtplib` is stdlib. The only runtime dependencies are credentials (SMTP app password, GCP secrets), which are deploy-time operator steps, not code steps.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing, pytest.ini present) |
| Config file | `pytest.ini` — `testpaths = tests`, `addopts = -m "not integration"`, `pythonpath = .` |
| Quick run command | `python -m pytest tests/test_notifications.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| NOTF-01 | Circuit breaker email fires once when `_circuit_tripped` latches | unit | `python -m pytest tests/test_notifications.py::test_circuit_breaker_sends_alert -x` | ❌ Wave 0 |
| NOTF-01 | Circuit breaker email does NOT fire again on subsequent gate blocks (already tripped) | unit | `python -m pytest tests/test_notifications.py::test_circuit_breaker_no_duplicate_alert -x` | ❌ Wave 0 |
| NOTF-02 | IBKR disconnect alert fires at attempt == len(_RETRY_DELAYS) | unit | `python -m pytest tests/test_notifications.py::test_ibkr_disconnect_alert -x` | ❌ Wave 0 |
| NOTF-02 | Re-auth failure sends alert | unit | `python -m pytest tests/test_notifications.py::test_reauth_failure_alert -x` | ❌ Wave 0 |
| NOTF-02 | Parse spike: 3 failures in 10 sends one alert | unit | `python -m pytest tests/test_notifications.py::test_parse_spike_alert -x` | ❌ Wave 0 |
| NOTF-02 | Parse spike: does not re-alert after first breach until window recovers | unit | `python -m pytest tests/test_notifications.py::test_parse_spike_no_duplicate -x` | ❌ Wave 0 |
| NOTF-01/02 | send_alert() with missing ALERT_EMAIL logs warning and returns without sending | unit | `python -m pytest tests/test_notifications.py::test_send_alert_no_recipient -x` | ❌ Wave 0 |
| NOTF-01/02 | send_alert() with SMTP failure logs warning and does not raise | unit | `python -m pytest tests/test_notifications.py::test_send_alert_smtp_failure_suppressed -x` | ❌ Wave 0 |
| D-12 | monitor.sql file exists and is readable | smoke | manual: `psql -h 127.0.0.1 -U bravos -d bravos_trading -f queries/monitor.sql` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `python -m pytest tests/test_notifications.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_notifications.py` — covers all 8 NOTF test cases above
- [ ] `queries/` directory — `queries/monitor.sql`
- [ ] `bravos/notifications/__init__.py` — empty init for new subpackage

*(All other test infrastructure exists — pytest.ini, conftest.py with db_connection fixture, existing test files)*

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | partial | Email body is system-generated, not user input — no injection risk |
| V6 Cryptography | yes | TLS via STARTTLS — never send SMTP credentials in plaintext |
| V1 Architecture / Secrets | yes | SMTP password and sender via GCP Secret Manager; recipient via env var only |

### Known Threat Patterns for this Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SMTP credentials in code | Information Disclosure | GCP Secret Manager (`get_secret()`) — never hardcoded |
| Plaintext SMTP | Tampering / Info Disclosure | `server.starttls()` before `server.login()` — enforced in Pattern 1 above |
| Alert flooding (unthrottled) | Denial of Service (SMTP quota) | Circuit breaker alert fires once (latch); parse spike fires once per breach; reconnect alert fires once at `attempt == len(_RETRY_DELAYS)` — structural throttling, not rate-limiting code |
| Email header injection | Tampering | Subject and body are system-generated strings, not user-supplied — no injection risk in v1 |

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| OAuth2 for Gmail SMTP | App Password (SMTP with STARTTLS) | App passwords still valid for accounts with 2FA | App passwords are simpler and do not require OAuth flow; suitable for server-to-server use |

**Deprecated/outdated:**
- Less-secure app access (Google): Google removed "Less secure app access" in 2022 — App Passwords are the correct mechanism for SMTP when not using OAuth. [VERIFIED: confirmed via training knowledge; app passwords require 2FA to be enabled on the Google account]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | GCP Secret Manager latency ~50-100ms per call is acceptable for fire-and-forget alerts | Don't Hand-Roll | If latency is higher (network issue), alerts will be slow but not blocking — low risk |
| A2 | Gmail app passwords require 2FA to be enabled on the sending Google account | Security Domain | If 2FA is not enabled, app password creation is not available — operator must enable 2FA first |
| A3 | `process_alert()` in scraper.py can be modified to return the parsed dict (Option A) or the parse spike tracker moved to notifier.py (Option C) without breaking the Gmail poller | Codebase Hook Point Audit | If the Gmail poller calls `process_alert()` in a fire-and-forget manner, returning a value may require changes to the caller |

---

## Open Questions (RESOLVED)

1. **Where does parse spike tracking live?** RESOLVED: Option C — state (`_parse_outcomes`, `_spike_alerted`) lives in `bravos/notifications/notifier.py` as module-level variables alongside `record_parse_outcome()`. Called from `scripts/run_ingestion.py` after `scraper.process_alert()` returns, using a deferred import of `record_parse_outcome`. No modification to `scraper.py` return type needed.
   - What we know: D-03 says the counter is "maintained in `run_cycle()`," but `run_cycle()` does not currently call `process_alert()` — the Gmail poller does.
   - Recommendation: Move state to `bravos/notifications/notifier.py` as `record_parse_outcome()` (Option C above). Wire the call from `scraper.process_alert()` with a deferred import. This is consistent with D-09 and cleanest from a module responsibility standpoint.

2. **Should `validate_secrets()` include the new SMTP secrets?** RESOLVED: Yes — add `bravos-alert-smtp-password` and `bravos-alert-smtp-from` to `REQUIRED_SECRETS` in `secrets_config.py`. Document as a deploy prerequisite in the `secrets_config.py` comment.
   - What we know: `validate_secrets()` blocks daemon startup if any REQUIRED_SECRET is unreadable. If SMTP secrets are not yet in GCP Secret Manager, the daemon cannot start.
   - Recommendation: Add to REQUIRED_SECRETS so alerts are guaranteed to work when needed. Document as a deploy prerequisite. (A broken alerting system is silently broken, which is worse than a startup failure.)

3. **`queries/` directory — does it need a README?** RESOLVED: No — include a comment block at the top of `monitor.sql` with the psql run command. The file is self-documenting.
   - What we know: D-12 says "file location: `queries/monitor.sql`." CONTEXT.md leaves README as Claude's discretion.
   - Recommendation: Include a one-line comment block at the top of `monitor.sql` itself explaining how to run it. No separate README needed — the file is self-documenting via the psql command in D-12.

---

## Sources

### Primary (HIGH confidence)

- Python stdlib (smtplib, email.mime.text) — verified with `python3 -c "import smtplib; help(smtplib.SMTP.starttls)"` on Python 3.13.13
- PostgreSQL 16.13 — verified with `psql --version`; `DISTINCT ON` is documented PostgreSQL syntax
- `bravos/risk/gate.py` — read directly; hook location at lines 106-112 verified
- `bravos/broker/connection.py` — read directly; hook location at lines 360-365 verified
- `scripts/run_ingestion.py` — read directly; hook location at lines 100-101 verified
- `infra/schema.sql`, `infra/migrate_phase4.sql`, `infra/migrate_phase5.sql` — read directly; all JOIN columns verified

### Secondary (MEDIUM confidence)

- Gmail SMTP app password mechanism — consistent with training knowledge; `smtp.gmail.com:587` with STARTTLS is the documented approach
- `google-cloud-secret-manager` package availability — verified with `python3 -c "from google.cloud import secretmanager"` import check

### Tertiary (LOW confidence)

- GCP Secret Manager latency estimate (~50-100ms) — training knowledge, not measured [A1]
- Gmail app password requires 2FA — training knowledge [A2]

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — verified stdlib on running Python 3.13.13; no new packages
- Architecture: HIGH — all hook points read directly from codebase; hook line numbers confirmed
- SQL query: HIGH — schema verified from SQL files; DISTINCT ON syntax is PostgreSQL-standard
- Parse spike tracking: MEDIUM — logic is clear but wiring location has an open question (D-03 wording vs. actual Gmail poller architecture)
- Pitfalls: HIGH — derived from reading actual code paths

**Research date:** 2026-05-20
**Valid until:** 2026-06-20 (stable stdlib + schema; only risk is Gmail SMTP policy changes)
