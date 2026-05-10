---
phase: 03-ibkr-connection
plan: 01
subsystem: broker
tags: [ibkr, ibapi, EWrapper, EClient, broker-package, connection, threading]
dependency_graph:
  requires:
    - bravos.config.settings (IBKR_HOST, IBKR_PAPER_PORT, IBKR_LIVE_PORT, IBKR_CLIENT_ID, get_ibkr_port)
    - ibapi (EWrapper, EClient)
  provides:
    - bravos.broker package (importable)
    - bravos.broker.connection.IBApp class
    - bravos.broker.connection.ibapp singleton (None at import)
  affects:
    - Plan 03-2: will add heartbeat loop and reconnect logic to IBApp
    - Plan 03-3: will add reconciliation callbacks to IBApp
    - Plan 04: order executor will import ibapp singleton
tech_stack:
  added:
    - ibapi EWrapper/EClient combined-class pattern (Pattern B)
    - threading.Event for connection state and sync primitives
    - threading.Lock for reconnect guard
  patterns:
    - Wave 0 skip-stub pattern: full test bodies inside @pytest.mark.skip
    - Module-level singleton set at startup, not at import
    - NotImplementedError stubs for future plans
key_files:
  created:
    - bravos/broker/__init__.py
    - bravos/broker/connection.py
    - tests/test_broker.py
  modified: []
decisions:
  - IBApp uses combined EWrapper+EClient pattern (Pattern B from SKILL.md) — fewer files, cleaner for single-app use
  - EWrapper listed first in inheritance — EWrapper has no __init__ so MRO is clean; EClient.__init__(self, self) called explicitly
  - _trigger_reconnect/heartbeat stubs raise NotImplementedError — explicit contract, not silent no-op
  - error() routes by code sets at module level — O(1) lookup, easy to extend
metrics:
  duration: 3min
  completed_date: "2026-05-10"
  tasks_completed: 3
  files_created: 3
  files_modified: 0
requirements_satisfied:
  - IBKR-05
---

# Phase 3 Plan 1: Broker Package + IBApp Class Skeleton Summary

**One-liner:** IBApp(EWrapper, EClient) with connection handshake, error code routing, and threading primitives — no side effects at import.

## Tasks Completed

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| Wave 0 | Test stubs (23 tests, all skipped) | d8eba83 | tests/test_broker.py |
| 1.1 | bravos/broker/__init__.py package | 7a2999c | bravos/broker/__init__.py |
| 1.2 | IBApp class + connection.py | 605a8fa | bravos/broker/connection.py |

## What Was Built

The `bravos/broker/` package with the full `IBApp(EWrapper, EClient)` class skeleton:

- **Constructor:** Stores `_host`, `_port`, `_client_id`. Initializes all threading primitives (`_connected`, `_stop_event`, `_recon_lock`, `_positions_done`, `_orders_done`, `_summary_done`). Does NOT connect.
- **connect_and_run():** Calls `self.connect()`, starts `ibkr-api` daemon thread running `self.run()`, returns `self._connected.wait(timeout)`.
- **nextValidId():** Sets `next_order_id`, updates `_last_heartbeat_at`, calls `_connected.set()`.
- **currentTime():** Updates `_last_heartbeat_at` (heartbeat response — no log).
- **error():** Routes by module-level code sets: `_IGNORE_CODES` → debug; `_IMMEDIATE_RECONNECT_CODES` → error + reconnect trigger; `_CRITICAL_NO_RETRY_CODES` → critical, no retry; others → warning/error.
- **stop():** Sets `_stop_event`, clears `_connected`, calls `disconnect()`.
- **Module constants:** `_RETRY_DELAYS`, `_IMMEDIATE_RECONNECT_CODES`, `_IGNORE_CODES`, `_CRITICAL_NO_RETRY_CODES`, `REQ_ID_ACCOUNT_SUMMARY`, `HEARTBEAT_INTERVAL`, `HEARTBEAT_TIMEOUT`.
- **Singleton:** `ibapp: IBApp | None = None` at module level — set by `run_ingestion.py` at startup.
- **Plan 03-2/03-3 stubs:** `_trigger_reconnect`, `_heartbeat_loop`, `_reconnect_loop`, `start_heartbeat_monitor`, `run_startup_reconciliation` raise `NotImplementedError`.

## Decisions Made

1. **Combined EWrapper+EClient pattern** — Pattern B from SKILL.md chosen. Simpler than separate classes, fewer files, clean for single-app use case. MRO safe because EWrapper has no `__init__`.
2. **EClient.__init__(self, self)** — passing `self` as wrapper is the ibapi contract for the combined pattern.
3. **Stubs raise NotImplementedError** — explicit failure contract rather than silent no-op. Plan 03-2 will replace with real implementations.
4. **Error routing via module-level sets** — O(1) lookup. Adding new codes requires only updating the set, not the routing logic.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

The following methods raise `NotImplementedError` by design — they are Plan 03-2 and 03-3 deliverables, not this plan's scope:

| Method | File | Implementing Plan |
|--------|------|-------------------|
| `IBApp._trigger_reconnect()` | bravos/broker/connection.py | 03-2 |
| `IBApp._heartbeat_loop()` | bravos/broker/connection.py | 03-2 |
| `IBApp._reconnect_loop()` | bravos/broker/connection.py | 03-2 |
| `IBApp.start_heartbeat_monitor()` | bravos/broker/connection.py | 03-2 |
| `IBApp.run_startup_reconciliation()` | bravos/broker/connection.py | 03-3 |

These stubs are intentional — they define the contract for subsequent plans. The plan goal (importable skeleton, connect handshake, test stubs) is fully achieved.

## Self-Check: PASSED

Files verified present:
- bravos/broker/__init__.py: FOUND
- bravos/broker/connection.py: FOUND
- tests/test_broker.py: FOUND

Commits verified:
- d8eba83: FOUND (test stubs)
- 7a2999c: FOUND (broker __init__)
- 605a8fa: FOUND (connection.py)
