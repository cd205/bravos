# Phase 7: Notifications and Monitoring Query - Context

**Gathered:** 2026-05-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Add email alerting for two categories of critical event (circuit breaker trip; unrecovered system errors including IBKR disconnect and parse failure spike) and commit a SQL monitoring query that joins signals, orders, executions, and position_lots into a single readable view of trade state. No web server, no dashboard, no real-time updates — just alerts and a query file.

</domain>

<decisions>
## Implementation Decisions

### Email trigger points
- **D-01:** Email on NOTF-01: circuit breaker trips (`RiskGate._circuit_tripped` latch fires in `gate.py`).
- **D-02:** Email on NOTF-02: three event types — (a) IBKR disconnect not auto-recovered after reconnect retries exhausted, (b) scraper re-authentication fails in `run_cycle()`, (c) parse failure spike: 3 failures in the last 10 signals (30% rolling window).
- **D-03:** Parse failure spike uses a rolling window counter maintained in `run_cycle()`. Threshold: 3 failures out of last 10 signals. Counter tracks `confidence == 'low'` or `ticker IS NULL` results from the DB (or from in-process signal results). One spike alert per window breach — does not fire repeatedly on every subsequent cycle once tripped.
- **D-04:** Hook locations: circuit breaker email in `gate.py` `_log_and_return` (or in `run_ingestion.py` where `_gate.reset()` is scheduled); IBKR disconnect email in `broker/connection.py` reconnect-exhausted path; scraper failure and parse spike email in `run_cycle()` in `run_ingestion.py`.

### Email / SMTP
- **D-05:** Use `smtplib` (Python stdlib) — no new packages. Gmail SMTP (smtp.gmail.com:587, STARTTLS).
- **D-06:** Gmail app password stored in GCP Secret Manager as `bravos-alert-smtp-password`. Sender address stored as `bravos-alert-smtp-from` in Secret Manager (it's a credential, the password requires it).
- **D-07:** Recipient address comes from env var `ALERT_EMAIL` on the VM — not a secret, set at deploy time alongside other env vars.
- **D-08:** Email body is plain-text. Subject prefix: `[Bravos Alert]`. Include: event type, timestamp, relevant values (e.g. daily_pnl for circuit breaker, account name, failure count for parse spike).
- **D-09:** Notifier module lives at `bravos/notifications/notifier.py`. Single `send_alert(subject, body)` function. Called from hook points; no retry logic in v1 — fire-and-forget, log warning on send failure.

### Monitoring query
- **D-10:** Query returns all signals (not filtered to orders-only) so blocked/low-confidence signals are visible alongside executed ones.
- **D-11:** Columns: `signal_id`, `parsed_at`, `ticker`, `action_type`, `confidence`, `gate_passed` (from risk_gate_log — NULL if not risk-checked), `order_status`, `fill_price`, `open_quantity` (sum of open lots for that ticker — NULL if no open lots), `realized_pnl` (sum of closed lot pnl for that ticker — NULL if no closed lots). Unrealized P&L left as NULL — no live prices in the DB.
- **D-12:** File location: `queries/monitor.sql`. Runnable with: `psql -h 127.0.0.1 -U bravos -d bravos_trading -f queries/monitor.sql`
- **D-13:** Query uses LEFT JOINs so signals with no matching order still appear. Most recent risk_gate_log row per signal (if multiple gate checks for same signal, take latest).

### Claude's Discretion
- Exact email body wording and formatting.
- Whether to extract the notifier call into a helper that guards against missing `ALERT_EMAIL` silently (log warning, don't crash the daemon).
- How to represent `open_quantity` and `realized_pnl` in the SQL — subquery or CTE.
- Whether `queries/` directory needs a README.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing hook points (where email calls get added)
- `bravos/risk/gate.py` — `_log_and_return` and `_circuit_tripped` latch (D-01/D-04)
- `scripts/run_ingestion.py` — `run_cycle()` for scraper failure + parse spike; daemon startup for IBKR connect failure (D-04)
- `bravos/broker/connection.py` — reconnect-exhausted path (D-04)

### Credential / settings pattern
- `bravos/config/secrets_config.py` — GCP Secret Manager reader pattern; new secrets `bravos-alert-smtp-password` and `bravos-alert-smtp-from` follow same pattern
- `bravos/config/settings.py` — `ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")` goes here

### Database schema (for monitoring query)
- `infra/schema.sql` — base tables: signals, orders, executions, position_lots
- `infra/migrate_phase4.sql` — risk_gate_log table
- `infra/migrate_phase5.sql` — fill_price / filled_at columns on orders

### Requirements
- `.planning/REQUIREMENTS.md` — NOTF-01, NOTF-02 (the two notification requirements for this phase; DASH-01–04 are stale/cut)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bravos/config/secrets_config.py` `get_secret()` — call this to fetch SMTP password and sender address from GCP Secret Manager.
- `bravos/config/settings.py` — add `ALERT_EMAIL` env var here following existing pattern.
- `logging.basicConfig` in `run_ingestion.py` — structured log lines already emitted at `logger.critical()` at each trigger point; email is additive.

### Established Patterns
- All credential reads go through `get_secret()` or `os.environ.get()` — never hardcoded.
- New modules live in `bravos/<subpackage>/` with an `__init__.py`.
- Settings constants are uppercase module-level vars in `settings.py`.

### Integration Points
- `gate.py` `_log_and_return` already returns `(passed, reason)` after writing to `risk_gate_log` — circuit breaker email call goes immediately after the `_circuit_tripped = True` assignment (line ~113).
- `run_cycle()` already has `logger.error("Re-authentication failed...")` — email call goes there.
- `broker/connection.py` reconnect loop: look for the path where retries are exhausted and `_reconnecting` is cleared without re-connection — email call goes there.
- `run_ingestion.py` already imports `_gate` from `bravos.execution.executor` — can also import `send_alert` from `bravos.notifications.notifier`.

</code_context>

<specifics>
## Specific Ideas

- "I'll do this after the system has been running" — implies the monitoring query is the primary ongoing-use artifact; email alerting is the safety net.
- Keep the notifier module minimal: one function, fire-and-forget, never crashes the daemon.

</specifics>

<deferred>
## Deferred Ideas

- NOTF-V2-01 / NOTF-V2-02 (email on new signal placed, email on fill) — v2 requirements per REQUIREMENTS.md; out of scope for Phase 7.
- Dashboard (DASH-01–04) — cut by decision 2026-05-20; user does not need a web UI.
- Unrealized P&L in monitoring query — requires live price feed; not available in the DB.

</deferred>

---

*Phase: 07-notifications-and-monitoring-query*
*Context gathered: 2026-05-20*
