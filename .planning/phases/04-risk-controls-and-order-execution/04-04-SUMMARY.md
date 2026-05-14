---
plan: 04-04
phase: 04-risk-controls-and-order-execution
status: complete
completed_at: "2026-05-14"
tasks_total: 2
tasks_complete: 2
commits:
  - a3ca602
  - fd33bb9
key-files:
  created:
    - bravos/execution/executor.py
  modified:
    - tests/test_execution.py
---

# Plan 04-04 Summary: Order Executor Implementation

## What Was Built

**`bravos/execution/executor.py`** — Single entry point for the order path (D-11).

### execute_signal(signal_id, db_conn)

Full execution sequence:
1. **Guards** (D-13/D-14): ibapp connected, confidence='high', action_type in {open, add, partial_close, close}
2. **Risk gate** (D-02): `RiskGate().check(signal_id, db_conn, ibapp)` — single bypass-free gate
3. **Price fetch** (D-05/D-06): `_fetch_price()` via reqMarketDataType(3) + reqMktData, 5s Event wait; fallback to reference_price
4. **Quantity** (EXEC-01): `_calculate_quantity()` — EXEC-01 formula for open/add/partial_close; sum-of-lots for close
5. **Submission** (D-08/D-10): `_submit_order()` — PENDING_SUBMISSION before placeOrder, 3s orderStatus wait

### Helper functions

| Function | Purpose |
|----------|---------|
| `_fetch_price(ticker, ibapp)` | Delayed price via threading.Event; always cancels with `cancelMktData` |
| `_build_contract(ticker)` | STK SMART USD Contract |
| `_build_order(action, quantity)` | MKT DAY Order, transmit=True, outsideRth=False |
| `_calculate_quantity(...)` | EXEC-01 formula; close → sum open lots |
| `_submit_order(...)` | PENDING_SUBMISSION → placeOrder → SUBMITTED/REJECTED |
| `_load_signal(...)` | Loads signal row; handles both dict (mocks) and tuple (psycopg2) |

### Key implementation decisions

- `ibapp` imported inside `execute_signal()` (lazy) to avoid import cycle (D-14)
- `_order_status_events[order_id]` registered BEFORE `placeOrder` (RESEARCH Pitfall 7)
- `ibapp.next_order_id += 1` incremented BEFORE `placeOrder` (D-10 / RESEARCH Pitfall 6)
- `cancelMktData` (not deprecated alternative) — RESEARCH Pitfall 1
- `threading.Event` for both price and order-status waits; no `time.sleep`
- `int()` truncates toward zero for quantity (floor for positive values)
- `_STATUS_MAP["Inactive"] = "REJECTED"` — IBKR uses Inactive for rejected/expired orders

### Tests (Task 2)

Removed all 7 `@pytest.mark.skip(reason="plan: 04-04")` decorators:

| Test | Type | Result |
|------|------|--------|
| test_quantity_formula | unit | PASS |
| test_quantity_zero_skipped | unit | PASS |
| test_build_order_buy | unit | PASS |
| test_build_order_sell | unit | PASS |
| test_order_db_write_pending | integration | deferred (needs live DB) |
| test_order_status_submitted | integration | deferred (needs live DB) |
| test_order_status_rejected | integration | deferred (needs live DB) |

All 15 Wave-0 stub tests are now active (combined effect of 04-03 + 04-04).
Plan 04-03 RiskGate tests still pass (6/6). No regressions.

## Deviations

None. Implementation matches plan specification.

## Self-Check: PASSED

- `bravos/execution/executor.py` exists, 330 lines, all 6 public/helper functions present
- `cancelMktData` used; no `reqMktDataCancel`; no `time.sleep`
- `ibapp` imported lazily inside `execute_signal`
- Status slot registered before `placeOrder`
- `PENDING_SUBMISSION` written before `placeOrder`
- 4 unit tests: `4 passed` ✓
- Plan 04-03 regression: `6 passed` ✓
- 0 remaining `@pytest.mark.skip` in test_execution.py ✓
