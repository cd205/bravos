---
phase: quick-260708-9az
plan: 01
subsystem: execution
tags: [ibkr, ibapi, order-execution, bugfix]
dependency-graph:
  requires: []
  provides: ["error-10268-fix"]
  affects: ["bravos/execution/executor.py order submission path"]
tech-stack:
  added: []
  patterns: ["explicit deprecated-flag clearing on ibapi Order objects"]
key-files:
  created: []
  modified:
    - bravos/execution/executor.py
    - tests/test_execution.py
decisions:
  - "Set eTradeOnly=False and firmQuoteOnly=False explicitly at all 4 Order construction sites rather than upgrading ibapi to 10.x (which removes the attributes) — smaller blast radius, no protocol version risk"
metrics:
  duration: "~15min"
  completed: "2026-07-08"
---

# Quick Task 260708-9az: Fix IBKR error 10268 (ibapi 9.81 deprecated Order attributes) Summary

Cleared `eTradeOnly` and `firmQuoteOnly` (both default `True` in ibapi 9.81.1-1's `Order()` constructor) to `False` at every Order construction site in `bravos/execution/executor.py`, unblocking IB Gateway 1045+ from rejecting every order with error 10268.

## What Was Built

IB Gateway 1045+ rejects any `placeOrder` call carrying the deprecated `eTradeOnly`/`firmQuoteOnly` attributes when they are `True`. ibapi 9.81.1-1's `Order()` constructor sets both to `True` by default, so every order the executor built — plain MKT orders and all three bracket-order legs — was rejected outright (confirmed in production 2026-07-08 05:24 UTC: MKT BUY 107 HROW, signal_id=2553, rejected and never reached the account).

Fixed by explicitly setting `order.eTradeOnly = False` and `order.firmQuoteOnly = False` at all 4 Order construction sites in `bravos/execution/executor.py`:

1. `_build_order()` — shared MKT builder used by `_submit_order` (plain orders)
2. `_submit_bracket_order()` — `parent_order` (MKT, transmit=False)
3. `_submit_bracket_order()` — `stp_order` (STP child)
4. `_submit_bracket_order()` — `tp_order` inside the loop (every LMT take-profit child)

No other order attributes (transmit, tif, ocaGroup, parentId, ocaType) were touched.

## Tasks Completed

1. **Set eTradeOnly=False and firmQuoteOnly=False on all executor Order objects, with regression tests** — commit `e2f1f74`
   - `bravos/execution/executor.py`: added the two-line flag assignment (with explanatory comments) at all 4 construction sites
   - `tests/test_execution.py`: extended `test_build_order_buy` and `test_build_order_sell` with `eTradeOnly is False` / `firmQuoteOnly is False` assertions; added new `test_bracket_deprecated_flags_cleared` (integration) that captures all 4 bracket-leg Order objects via a `placeOrder` side effect and asserts both flags on each

## Deviations from Plan

None — plan executed exactly as written. Attribute names (`eTradeOnly`, `firmQuoteOnly`) were pre-verified in the plan against the installed ibapi 9.81.1-1 package and matched exactly.

## Verification

- `python -m pytest tests/test_execution.py -k "build_order or deprecated_flags" -m "" -x -q` → 3 passed (the new integration test requires `-m ""` to override the repo's default `addopts = -m "not integration"`; Cloud SQL Auth Proxy was up on 127.0.0.1:5432 for this run)
- `grep -v '^\s*#' bravos/execution/executor.py | grep -c "eTradeOnly = False"` → 4
- `grep -v '^\s*#' bravos/execution/executor.py | grep -c "firmQuoteOnly = False"` → 4
- Full suite: `python -m pytest tests/test_execution.py -q -m ""` → 23 passed, 1 pre-existing failure (`test_bracket_oca_group`), 0 new failures

### Pre-existing failure (out of scope, not caused by this change)

`test_bracket_oca_group` fails on both the pre-change baseline and post-change tree with an identical assertion error (`assert 3 == 2`, expects the STP order to have no OCA group, but current executor code — from a prior WR-01 fix — deliberately puts the STP into the same OCA group as the TP children). Confirmed via `git stash` that this failure exists independent of this task's changes. Logged here per the plan's constraint (touch nothing outside the 4 Order sites and the 2 new/extended tests); not fixed as part of this quick task.

## Diff Scope

Confirmed via `git diff --stat` that only `bravos/execution/executor.py` (+12) and `tests/test_execution.py` (+39) changed in this commit. No changes to `scraper.py`, `run_ingestion.py`, or any file owned by concurrent quick task 260708-7lk.

## Self-Check: PASSED

- FOUND: bravos/execution/executor.py (grep confirms 4× `eTradeOnly = False`, 4× `firmQuoteOnly = False`)
- FOUND: tests/test_execution.py (contains `test_bracket_deprecated_flags_cleared` and extended `test_build_order_buy`/`test_build_order_sell`)
- FOUND: commit e2f1f74 (`git log --oneline` confirms `fix(quick-260708-9az): clear eTradeOnly/firmQuoteOnly on all Order objects`)
