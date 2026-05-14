---
phase: 04-risk-controls-and-order-execution
plan: "02"
subsystem: broker
tags: [ibkr, ewrapper, callbacks, threading, risk, order-execution]
dependency_graph:
  requires: []
  provides:
    - bravos.broker.connection.IBApp._tick_events
    - bravos.broker.connection.IBApp._tick_lock
    - bravos.broker.connection.IBApp._mkt_req_counter
    - bravos.broker.connection.IBApp._order_status_events
    - bravos.broker.connection.IBApp._account_name
    - bravos.broker.connection.IBApp._daily_pnl
    - bravos.broker.connection.IBApp.managedAccounts
    - bravos.broker.connection.IBApp.tickPrice
    - bravos.broker.connection.IBApp.orderStatus
    - bravos.broker.connection.IBApp.pnl
  affects:
    - bravos/execution/executor.py (Plan 04-04)
    - bravos/risk/gate.py (Plan 04-03)
tech_stack:
  added: []
  patterns:
    - threading.Event per-request routing (executor waits on price ticks and order status)
    - PRICE_TICK_TYPES whitelist {4, 9, 68, 76} for live+delayed compatibility
    - Lock-guarded dict mutation (_tick_lock guards _tick_events)
key_files:
  created: []
  modified:
    - bravos/broker/connection.py
decisions:
  - "PRICE_TICK_TYPES includes both live (4, 9) and delayed (68, 76) tick types — reqMarketDataType(3) sends delayed variants"
  - "pnl callback has no log per call — fires every few seconds, logging would be noisy and leak P&L to log files"
  - "orderStatus does not use _tick_lock — keyed by unique order_id with single writer (api thread) and single reader (executor thread)"
  - "_mkt_req_counter=2000 chosen to avoid collision with REQ_ID_ACCOUNT_SUMMARY=9001"
metrics:
  duration: "3min"
  completed_date: "2026-05-14T10:30:15Z"
  tasks_completed: 2
  files_modified: 1
---

# Phase 04 Plan 02: IBApp EWrapper Callbacks — SUMMARY

## One-liner

Six Phase 4 instance attributes and four EWrapper callbacks (managedAccounts, tickPrice, orderStatus, pnl) added to IBApp for executor price-tick and order-status synchronization plus circuit-breaker P&L.

## What Was Built

Modified `bravos/broker/connection.py` to extend IBApp with the async-callback infrastructure required by the executor (Plan 04-04) and risk gate (Plan 04-03).

**Task 1: Six new instance attributes in `IBApp.__init__`**

Inserted after `self._summary_done = threading.Event()`, before `# Shutdown control`:
- `_tick_events: dict[int, dict] = {}` — per-request slots for price-tick routing
- `_tick_lock = threading.Lock()` — guards concurrent mutations of `_tick_events`
- `_mkt_req_counter = 2000` — rolling counter for market data request IDs (avoids REQ_ID_ACCOUNT_SUMMARY=9001)
- `_order_status_events: dict[int, dict] = {}` — per-order slots for status routing
- `_account_name: str = ""` — populated by managedAccounts for reqPnL subscription
- `_daily_pnl: float | None = None` — populated by pnl callback for circuit breaker

**Task 2: Four new EWrapper callbacks**

Inserted after `accountSummaryEnd`, before `run_startup_reconciliation`:
- `managedAccounts(accountsList)`: splits comma-separated account list, stores first ID on `_account_name`, logs at INFO
- `tickPrice(reqId, tickType, price, attrib)`: whitelists tick types {4, 9, 68, 76}, rejects price<=0, routes to `_tick_events[reqId]` under `_tick_lock`
- `orderStatus(orderId, status, ...)`: logs at INFO, routes status string to `_order_status_events[orderId]`
- `pnl(reqId, dailyPnL, ...)`: stores `dailyPnL` to `_daily_pnl`, no log (high-frequency)

## Verification

All plan verification steps passed:
- All four callback definitions present (grep count = 4)
- All six instance attributes present and correctly initialized
- Phase 3 broker tests: **15 passed, 8 skipped** (no regressions)
- Module imports without error

Threat mitigations confirmed:
- T-04-05: `if price <= 0: return` guard rejects IBKR's -1.0 sentinel
- T-04-06: `PRICE_TICK_TYPES = {4, 9, 68, 76}` whitelist enforced
- T-04-07: All `_tick_events` mutations guarded by `_tick_lock`
- T-04-08: `pnl` callback has no log (accepted, no P&L leakage to logs)
- T-04-09: Cleanup (`pop()`) deferred to executor (Plan 04-04) per documented responsibility

## Commits

| Task | Commit | Files |
|------|--------|-------|
| Task 1: Add Phase 4 instance attributes | ffa4691 | bravos/broker/connection.py |
| Task 2: Add four EWrapper callbacks | 6a7ad86 | bravos/broker/connection.py |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. The callbacks are fully implemented. Executor (Plan 04-04) is responsible for registering slots and cleaning up via `.pop()` in finally blocks — this is documented in the threat model (T-04-09) and is not a stub.

## Threat Flags

None. All new code is additive callbacks on existing trust boundary (IB Gateway → IBApp). No new network endpoints, auth paths, or schema changes introduced.

## Self-Check: PASSED

- [x] `bravos/broker/connection.py` modified with 14+82 = 96 lines added across two commits
- [x] Commit ffa4691 exists: `feat(04-02): add Phase 4 instance attributes to IBApp.__init__`
- [x] Commit 6a7ad86 exists: `feat(04-02): add four EWrapper callbacks for order execution and risk`
- [x] No files deleted
- [x] Phase 3 tests 15 passed, 8 skipped — no new failures
