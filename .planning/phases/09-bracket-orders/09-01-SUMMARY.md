---
phase: 09-bracket-orders
plan: "01"
subsystem: testing
tags: [wave-0, test-stubs, bracket-orders, tdd, parser, executor]
dependency_graph:
  requires: []
  provides: [09-01-test-stubs]
  affects: [tests/test_parser.py, tests/test_execution.py]
tech_stack:
  added: []
  patterns: [wave-0-skip-stub, import-inside-function, skip-reason-naming-implementing-plan]
key_files:
  created: []
  modified:
    - tests/test_parser.py
    - tests/test_execution.py
decisions:
  - "Wave 0 skip-stub pattern: full test bodies inside @pytest.mark.skip, skip reason names implementing plan (09-02-PLAN / 09-03-PLAN)"
  - "_make_signal helper is NOT skip-decorated — it is a plain helper, not a test"
  - "Pre-existing test_multi_ticker_forces_low_confidence failure is out-of-scope; existed before this plan"
metrics:
  duration: "8 minutes"
  completed: "2026-06-02T05:12:14Z"
  tasks_completed: 3
  files_modified: 2
requirements: [EXEC-07]
---

# Phase 9 Plan 01: Wave 0 Test Stubs for Bracket Orders Summary

Wave 0 skip-stub test contract for Phase 9 bracket orders: 6 TP/SL parser stubs and 7 bracket executor stubs with `_make_signal` helper added to establish the Nyquist measurement instruments before implementation begins.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add 6 TP/SL parser test stubs | a5789cf | tests/test_parser.py |
| 2 | Add 7 bracket executor stubs + _make_signal helper | 9f020dd | tests/test_execution.py |
| 3 | Confirm full suite still runs with stubs skipped | (no commit — verification gate) | — |

## What Was Built

**tests/test_parser.py** — 6 new skip-decorated stubs added (Wave 0, implementing plan 09-02):
- `test_extract_stop_loss_structured` — STOP_LOSS_RE matches "Suggested Stop Loss (SL): $N"
- `test_extract_stop_loss_prose` — STOP_LOSS_RE matches "a stop at $N" prose format
- `test_extract_take_profits_structured` — TAKE_PROFIT_RE + PRICE_IN_LIST_RE extracts structured list
- `test_extract_take_profits_prose` — TAKE_PROFIT_RE + PRICE_IN_LIST_RE extracts prose targets
- `test_parse_signal_returns_tp_sl` — parse_signal populates stop_loss and take_profits keys
- `test_parse_signal_no_tp_sl` — parse_signal returns None/[] when no TP/SL data present

**tests/test_execution.py** — `_make_signal` helper + 7 new skip-decorated stubs (Wave 0, implementing plan 09-03):
- `_make_signal` — helper inserting synthetic signal rows with Phase 9 stop_loss/take_profits fields (not skip-decorated)
- `test_bracket_db_writes` — N+2 rows (parent + STP + N TP) written to DB
- `test_bracket_transmit_sequence` — transmit=False for all except last TP (D-14 contract)
- `test_bracket_parent_id` — child parentId = IBKR order ID, not DB row id (pitfall 1)
- `test_bracket_oca_group` — TP orders share ocaGroup = `f'TP_{parent_ibkr_id}'` (D-15)
- `test_bracket_quantity_split` — equal split with remainder to last TP (e.g. 10//3=3,3,4)
- `test_bracket_fallback_no_tpsl` — no TP/SL data routes to bare MKT _submit_order (D-19)
- `test_bracket_fallback_qty_too_small` — qty < len(TPs) routes to bare MKT (D-20)

## Verification Results

Full suite run (`tests/test_parser.py tests/test_execution.py -m "not integration"`):
- 26 passed, 13 skipped, 1 failed
- 13 new skips confirmed: 6 parser + 7 executor
- Zero errors, zero new failures introduced by this plan

## Deviations from Plan

### Pre-existing Issue (Out of Scope)

**`test_multi_ticker_forces_low_confidence` was already failing** before this plan (pre-existing in commit 953d078 / main repo). This test asserts `confidence == "low"` for a multi-ticker alert but the parser returns `"medium"`. Not caused by any change in this plan. Deferred to a future fix.

### Note on Verification Command

The plan's verification commands reference `/home/chris_s_dodd/miniconda3/envs/bravos/bin/python` but that path does not have pytest installed. The correct Python with pytest is `/home/chris_s_dodd/miniconda3/bin/python`. All verification ran successfully with the correct path.

## Known Stubs

All 13 test functions are intentional stubs (`@pytest.mark.skip`) — this is the plan's objective, not incomplete work. Plans 02 and 03 will remove the skip decorators when their implementations land.

## Threat Flags

No new security-relevant surface introduced. This plan only adds skipped test stubs and a test helper. No production paths, no new network endpoints, no auth changes.

## Self-Check: PASSED

- tests/test_parser.py: FOUND
- tests/test_execution.py: FOUND
- 09-01-SUMMARY.md: FOUND
- Commit a5789cf (parser stubs): FOUND
- Commit 9f020dd (executor stubs): FOUND
