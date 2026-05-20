---
plan: 07-01
phase: 07-notifications-and-monitoring-query
status: complete
completed: 2026-05-20
requirements: [NOTF-01, NOTF-02]
---

# Plan 07-01: Notifier Module + SQL Monitoring Query

## What Was Built

Created the notifications foundation for Phase 7: fire-and-forget email alerting via Gmail SMTP and a rolling-window parse failure spike detector, plus the SQL monitoring query.

## Key Files Created

### key-files.created
- `bravos/notifications/__init__.py` — subpackage init
- `bravos/notifications/notifier.py` — send_alert() and record_parse_outcome()
- `queries/monitor.sql` — full signal-to-fill monitoring query (10 columns, CTE structure)
- `tests/test_notifications.py` — 6 unit tests, all green

### key-files.modified
- `bravos/config/settings.py` — added ALERT_EMAIL env var constant
- `bravos/config/secrets_config.py` — added bravos-alert-smtp-password and bravos-alert-smtp-from to REQUIRED_SECRETS

## Decisions Made

- **D-03 parse spike**: Rolling deque(maxlen=10), SPIKE_THRESHOLD=3. State lives in notifier.py to avoid reverse dependency. Re-arms when failure_count drops below threshold.
- **D-05 SMTP**: smtplib stdlib — no new packages. smtp.gmail.com:587 + STARTTLS.
- **D-06 credentials**: get_secret() for SMTP password and sender address — never hardcoded.
- **D-13 SQL**: DISTINCT ON (signal_id) ORDER BY signal_id, checked_at DESC for latest gate row per signal (PostgreSQL-idiomatic).

## Test Results

```
tests/test_notifications.py — 6 passed
Full suite — 81 passed, 2 skipped (no regressions from prior 75)
```

## Self-Check: PASSED

All acceptance criteria met:
- send_alert() importable, never raises, guards empty ALERT_EMAIL
- record_parse_outcome() fires exactly once per spike, re-arms on recovery
- queries/monitor.sql: DISTINCT ON CTE, 4 LEFT JOINs, all 10 columns
- REQUIRED_SECRETS updated with both SMTP secrets
- ALERT_EMAIL in settings.py
