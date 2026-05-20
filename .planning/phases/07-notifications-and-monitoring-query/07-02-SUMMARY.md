---
plan: 07-02
phase: 07-notifications-and-monitoring-query
status: complete
completed: 2026-05-20
requirements: [NOTF-01, NOTF-02]
---

# Plan 07-02: Wire Email Hooks into Daemon

## What Was Built

Wired all four email trigger points into the running daemon using the notifier module from Plan 01. All hooks are fire-and-forget — SMTP failure never crashes the daemon.

## Key Files Modified

### key-files.modified
- `bravos/risk/gate.py` — deferred `send_alert()` inside `_circuit_tripped` latch (D-01, NOTF-01)
- `bravos/broker/connection.py` — deferred `send_alert()` at `attempt == len(_RETRY_DELAYS)` (D-02a, NOTF-02)
- `scripts/run_ingestion.py` — module-level import; `send_alert()` after re-auth failure in `run_cycle()` (D-02b, NOTF-02)
- `bravos/ingestion/scraper.py` — deferred `record_parse_outcome(parsed)` after `_store_signal()` for non-duplicate signals (D-03, NOTF-02)

## Hook Summary

| Trigger | File | Pattern | Fires |
|---------|------|---------|-------|
| Circuit breaker trip | `gate.py` | deferred import inside latch block | Once per day (latch) |
| IBKR reconnect exhausted | `connection.py` | deferred import at `attempt == len(_RETRY_DELAYS)` | Once per disconnect event |
| Scraper re-auth failure | `run_ingestion.py` | module-level import, call after `_login()` fails | Once per failed health cycle |
| Parse failure spike | `scraper.py` | deferred import, `record_parse_outcome(parsed)` | Once per spike window breach (re-arms on recovery) |

## Decisions Made

- `record_parse_outcome` placed in `scraper.process_alert()` (not `run_ingestion.run_cycle()`) because `parsed` dict is only available at the process_alert call site; the Gmail poller calls `process_alert` directly so no call site exists in `run_ingestion.py`.
- `gate.py` and `connection.py` use deferred imports (no module-level dep) consistent with existing patterns in `connection.py`.
- `run_ingestion.py` uses module-level import (only entry point script, no circular dep risk).

## Test Results

```
Full suite — 81 passed, 2 skipped (no regressions)
```

## Self-Check: PASSED

All acceptance criteria met:
- `send_alert` present in gate.py, connection.py, run_ingestion.py
- `record_parse_outcome` present in scraper.py
- No module-level notifier imports in gate.py or connection.py
- Four email subjects: "Circuit Breaker Triggered", "IBKR Disconnect — Auto-Recovery Failed", "Scraper Re-Authentication Failed", "Parse Failure Spike"
- Full test suite green
