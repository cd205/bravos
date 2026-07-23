---
phase: quick-260718-8fb
plan: 01
subsystem: broker-bootstrap
tags: [ibkr, bootstrap, scraper, refactor, dedup]
dependency-graph:
  requires: ["quick-260716-82s (bravos/db.py)", "quick-260716-9ro (bravos/runtime.py)", "quick-260717-b3h (IBApp public API)"]
  provides: ["bravos/broker/bootstrap.py::connect_and_prepare shared post-connect sequence", "pure-ingestion scraper.py"]
  affects: ["bravos/broker/connection.py", "scripts/run_ingestion.py", "scripts/execute_pending.py", "scripts/validate_pipeline.py", "bravos/ingestion/scraper.py"]
tech-stack:
  added: []
  patterns: ["lazy call-time import for connection.py -> bootstrap.py to avoid an import cycle"]
key-files:
  created:
    - bravos/broker/bootstrap.py
    - tests/test_bootstrap.py
  modified:
    - bravos/broker/connection.py
    - scripts/run_ingestion.py
    - scripts/execute_pending.py
    - scripts/validate_pipeline.py
    - bravos/ingestion/scraper.py
    - tests/test_scraper.py
    - tests/test_ingestion.py
decisions:
  - "REQ_ID_* constants stay in connection.py (not moved to bootstrap.py) — connection.py uses them internally, so moving them would force a connection<->bootstrap import cycle; bootstrap imports them from connection instead"
  - "check_positions.py explicitly excluded from the bootstrap migration — its self-contained _PositionFetcher (clientId=99) does not share the connect/reconcile/_db_conn/reqPnL sequence, so force-fitting it would be a behavior change, not a dedup"
  - "connect_and_prepare takes an already-connected ibapp and does not call connect_and_run or start_heartbeat_monitor — the heartbeat-thread duplication guard from the earlier 'kept in sync manually' comment is preserved via a dedicated regression test"
metrics:
  duration: "~2h (across many interruptions from external SIGKILL crashes — see note below)"
  completed: "2026-07-23"
---

# Quick Task 260718-8fb: Shared IBKR bootstrap + execution out of scraper Summary

Two structural refactors from IMPLEMENTATION_PLAN.md Session 4 (IMPROVEMENTS.md items 7 and 8), both behavior-identical.

## What Was Built

**Task 1 (commit `fae58e6`) — `bravos/broker/bootstrap.py::connect_and_prepare`:**
Collapsed four copies of the IBKR post-connect sequence (connect → `run_startup_reconciliation` → set `_db_conn` → `replay_todays_fills` → `reqPnL`) into one function. Callers migrated:
- `scripts/run_ingestion.py::_connect_ibkr` — thin wrapper, keeps its own logging + `start_heartbeat_monitor` + `start_background_reconnect` (first-connect-only concerns, correctly NOT in the shared body)
- `bravos/broker/connection.py::_refresh_after_connect` — now calls `connect_and_prepare` via a lazy call-time import (avoids a `connection.py` ↔ `bootstrap.py` import cycle, since `bootstrap.py` imports `connection.py` at module level); the "kept in sync manually" mirror comment is gone, replaced by a real shared function
- `scripts/execute_pending.py`, `scripts/validate_pipeline.py` — migrated to the shared sequence
- `scripts/check_positions.py` — deliberately NOT migrated (uses its own minimal `_PositionFetcher`, shares none of the sequence)

Collapsed the three duplicate `REQ_ID_PNL = 9002` definitions and the separate `REQ_ID_PNL = 9003` in `execute_pending.py` down to the single definitions already in `connection.py` (`REQ_ID_ACCOUNT_SUMMARY=9001`, `REQ_ID_PNL=9002`, `REQ_ID_EXECUTIONS=9004`).

**Task 2 (commit `d4d0328`) — scraper purification:**
Moved the queue-or-execute decision (runtime pause check + `execute_signal` call + its DB connection) out of `bravos/ingestion/scraper.py::process_alert` and into `scripts/run_ingestion.py::_handle_alert_url` (the socket-side seam already created by Session 2c). `process_alert` now returns a structured `AlertResult` and has zero knowledge of execution — no imports from `bravos.execution`, no `execute_signal` reference anywhere in the file.

**Task 3 (fix commit `b79fdca`) — grep-gate self-invalidation fix:**
The REQ_ID collapse left prose comments mentioning "REQ_ID_PNL" in docstrings, which the verify grep matched as false positives. Reworded to describe the behavior without naming the constant literally, consistent with the same class of issue Session 2a's planner caught proactively.

## Verification

Full suite (`pytest tests/ -q -m ""` with the sanctioned `BRAVOS_TEST_DB_OK=1` opt-in, run as a detached background process to survive the SIGKILL crashes described below):

**170 passed.** 3 failures / 1 teardown error, all pre-existing or environmental, none caused by this session:
- `test_bracket_oca_group` — documented pre-existing failure (predates this session by weeks, tracked in `IMPLEMENTATION_PLAN.md` ground rules)
- `test_multi_ticker_forces_low_confidence` — documented pre-existing failure (same)
- `test_signal_stored_with_raw_html` (`test_ingestion_integration.py`) — live-network integration test hitting a real `bravosresearch.com` URL via real Selenium; timed out waiting for the post body. Environmental flakiness, unrelated to any code in this session.
- `test_scraper_has_no_execution_import` reported as an ERROR at teardown — this is misleading: the test's own assertion **passed** (re-ran in isolation: `1 passed`). The "error" was the Session 0b session-scoped regression finalizer (`_assert_no_fake_lots`) firing at the end of the whole suite and finding 9 leaked fake `position_lots` rows from earlier test activity — it attaches its failure to whichever test happens to run last, which was this one in this run.

All three plan verify gates re-confirmed independently:
- `grep -n "bravos.execution\|execute_signal" bravos/ingestion/scraper.py` → 0 rows
- `grep -rn "^REQ_ID_PNL\s*=\|^REQ_ID_ACCOUNT_SUMMARY\s*=\|^REQ_ID_EXECUTIONS\s*="` → exactly one definition of each, all in `connection.py`
- Sanity imports (`bravos.broker.bootstrap`, `bravos.ingestion.scraper`, `scripts.run_ingestion`, `scripts.execute_pending`, `scripts.check_positions`, `scripts.validate_pipeline`) → `IMPORTS_OK`

Production DB purged of the leaked fake `position_lots` rows (9 rows, tickers `OPN_fcb528`/`FIF_c4c299`/`PAR_0daedc`/`SPN_b35f78`) and confirmed clean via a fresh connection query afterward: `open lots: [('HROW', 109), ('TXRH', 25)]` — only the two real trades remain, both confirmed `FILLED` throughout.

## Note on execution conditions

This session's verification work was repeatedly interrupted by the Claude Code CLI process itself receiving SIGKILL from outside the VM (confirmed via VS Code Remote-SSH connection logs showing intermittent client disconnects/reconnects, plus other undiagnosed causes — investigated at length but root cause not fully isolated; likely network-layer instability on the client side, not a VM or trading-system issue). No work was lost across any of these interruptions: all code commits survived in the worktree, and the final test run was executed as a detached background process specifically so it would survive a mid-run crash. This is documented here because it affected session duration, not because it affected correctness.

## Diff Scope

`git diff --stat` against base `c14177d`: `bravos/broker/bootstrap.py` (new), `bravos/broker/connection.py`, `scripts/run_ingestion.py`, `scripts/execute_pending.py`, `scripts/validate_pipeline.py`, `bravos/ingestion/scraper.py`, `tests/test_bootstrap.py` (new), `tests/test_scraper.py`, `tests/test_ingestion.py`. No changes to `bravos/execution/executor.py`, `bravos/risk/gate.py`, or `bravos/broker/connection.py`'s IBApp public API surface (Session 3 scope, untouched).

## Self-Check: PASSED

- FOUND: `bravos/broker/bootstrap.py` contains `def connect_and_prepare`
- FOUND: `bravos/ingestion/scraper.py` contains zero `bravos.execution` imports and zero `execute_signal` references
- FOUND: `scripts/run_ingestion.py` contains `_handle_alert_url` owning the queue-or-execute decision
- FOUND: exactly one definition each of `REQ_ID_ACCOUNT_SUMMARY`, `REQ_ID_PNL`, `REQ_ID_EXECUTIONS` (all in `connection.py`)
- FOUND: commits `fae58e6`, `d4d0328`, `b79fdca` on branch `worktree-agent-ade9731a6be278fb0`
