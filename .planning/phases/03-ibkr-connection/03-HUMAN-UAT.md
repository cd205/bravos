---
status: partial
phase: 03-ibkr-connection
source: [03-VERIFICATION.md]
started: 2026-05-14T00:00:00Z
updated: 2026-05-14T00:00:00Z
---

## Current Test

[awaiting human testing — IB Gateway required]

## Tests

### 1. Live Gateway startup log sequence
expected: Starting IBKR connection → IBKR connected → running startup reconciliation → IBKR ready — heartbeat monitor started → Ingestion daemon started
result: [pending]

### 2. 60s heartbeat stability
expected: Daemon remains running with no ERROR or WARNING about heartbeat timeout after 60 seconds
result: [pending]

### 3. SIGTERM clean shutdown
expected: kill -TERM <pid> produces "Stopping IBKR connection..." → "IBKR connection stopped" → "Ingestion daemon stopped", exit code 0
result: [pending]

### 4. D-14 path — Gateway not running
expected: IBKR initial connect failed (mode=paper port=4002) logged as CRITICAL, daemon continues into schedule loop, no crash
result: [pending]

### 5. broker_positions_snapshot populated
expected: SELECT from broker_positions_snapshot returns rows (or empty if no open paper positions — both valid)
result: [pending]

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps
