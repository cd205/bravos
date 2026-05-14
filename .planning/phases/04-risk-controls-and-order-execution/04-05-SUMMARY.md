---
plan: 04-05
phase: 04-risk-controls-and-order-execution
status: complete
completed_at: "2026-05-14"
tasks_total: 3
tasks_complete: 3
commits:
  - 4632d6c
  - db88e2a
key-files:
  created: []
  modified:
    - bravos/ingestion/scraper.py
    - scripts/run_ingestion.py
---

# Plan 04-05 Summary: Live Runtime Wiring

## What Was Built

Three surgical changes completing the signal-to-order path.

### Task 1: scraper.py — execute_signal wiring (D-12/D-13)

**`BravosScraper._store_signal`** now returns `int | None`:
- `RETURNING id` appended to INSERT; `row = cur.fetchone()` captured inside cursor block
- Returns `row[0]` on new insert, `None` on `ON CONFLICT DO NOTHING` (duplicate URL)

**`BravosScraper.process_alert`** wired to order path:
- Captures `signal_id = self._store_signal(signal_data)`
- Double gate: `if signal_id is not None and parsed.get("confidence") == "high":`
  - Lazy import of `execute_signal` (avoids circular import with `bravos.ingestion`)
  - Opens dedicated `exec_conn`, calls `execute_signal(signal_id, exec_conn)`, closes in finally
  - Wraps call in `try/except Exception` so executor errors never crash the scraper loop

### Task 2: run_ingestion.py — reqPnL subscription (RISK-03)

- Added `REQ_ID_PNL = 9002` module constant (no collision with REQ_ID_ACCOUNT_SUMMARY=9001)
- After `run_startup_reconciliation`: if `_ibapp._account_name` is populated, calls `_ibapp.reqPnL(REQ_ID_PNL, _ibapp._account_name, "")`
- Empty `_account_name` → WARNING log, circuit breaker fails-open (RESEARCH Pitfall 3 mitigated)
- `reqPnL` subscription fires before `start_heartbeat_monitor` so "IBKR ready" log is accurate

### Task 3: run_ingestion.py — daily RiskGate.reset() (RISK-03/Q1)

- Added `from bravos.risk.gate import RiskGate` import
- `gate = RiskGate()` instantiated in `main()` at daemon startup
- `schedule.every().day.at("14:30").do(gate.reset)` — 14:30 UTC = 09:30 ET (winter/EST)
- DST limitation documented: during EDT (summer) fires at 10:30 ET, 1 hour late — acceptable for v1
- Resolves open question Q1 from RESEARCH.md: circuit breaker now auto-resets without daemon restart

## Full signal path — now end-to-end connected

```
Gmail alert URL
  → scraper.process_alert(url)
    → fetch_post + parse_signal
    → _store_signal → DB INSERT RETURNING id
    → execute_signal(signal_id, exec_conn)
      → RiskGate.check (market hours, positions, allocation, circuit breaker)
      → _fetch_price (reqMktData via threading.Event)
      → _calculate_quantity (EXEC-01 formula)
      → _submit_order → orders INSERT PENDING_SUBMISSION → placeOrder → SUBMITTED/REJECTED
```

## Deviations

None. All edits are surgical (minimal diff to existing files).

## Self-Check: PASSED

- `_store_signal` signature `-> int | None` ✓
- `RETURNING id` in SQL ✓
- Lazy import of `execute_signal` (no top-level import) ✓
- Double gate `signal_id is not None and confidence=='high'` ✓
- `exec_conn.close()` in finally ✓
- `@catch_cycle_exceptions` decorator preserved ✓
- `REQ_ID_PNL = 9002` ✓
- `reqPnL` between reconciliation and heartbeat_monitor ✓
- `RiskGate` imported; `gate.reset` scheduled daily at 14:30 UTC ✓
- Both files compile ✓
- Phase 2 regression: 9 passed ✓
- Phase 3 regression: 15 passed, 8 skipped ✓
- Phase 4 unit tests: 10 passed ✓
