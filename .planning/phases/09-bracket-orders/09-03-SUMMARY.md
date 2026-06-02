---
plan: 09-03
phase: 09-bracket-orders
status: complete
completed: 2026-06-02
commits:
  - 55f446a
  - 905fb16
self_check: PARTIAL
deviation: execute_signal D-10 routing to _submit_bracket_order not yet wired
---

## Summary

Extended `_load_signal()` to return stop_loss and take_profits, and implemented `_submit_bracket_order()` with full bracket order logic. The D-10 routing condition in `execute_signal()` was not wired in this plan — tracked as a gap.

## What Was Built

### _load_signal() extension (bravos/execution/executor.py)
- SELECT query extended to fetch `stop_loss` (row[6]) and `take_profits` (row[7])
- Returned dict includes `stop_loss` (float|None) and `take_profits` (list[float]) (D-03)

### _submit_bracket_order() (bravos/execution/executor.py)
- Reserves N+2 IBKR order IDs upfront (D-17)
- Writes N+2 DB rows PENDING_SUBMISSION before any placeOrder call
- Parent MKT order: transmit=False
- STP child: auxPrice=stop_loss, GTC, transmit=False, no OCA group (D-13)
- N LMT TP children: lmtPrice=tp_price, GTC, ocaGroup=f'TP_{parent_ibkr_id}', ocaType=2 (D-12, D-15)
- Last TP child: transmit=True (fires entire bracket) (D-14)
- Child parentId = IBKR order ID, not DB row id (D-16)
- Quantity split equally across TPs with integer division, remainder to last TP (D-11)
- Input validation guard: refuses bracket if stop_loss invalid or take_profits empty

### Not implemented in this plan
- D-10: `execute_signal()` routing condition (`use_bracket`) to call `_submit_bracket_order()` instead of `_submit_order()` — requires fixing on main before Phase 9 is complete
- D-20: quantity < N fallback with warning — not implemented

## Key Files

- `bravos/execution/executor.py` — _load_signal() extended, _submit_bracket_order() added

## Test Results

7 executor stubs from 09-01 remain skipped (D-10 routing not wired, so tests can't pass yet).

## Deviations

D-10 routing and D-20 fallback not implemented. Needs a follow-up fix commit on main.
