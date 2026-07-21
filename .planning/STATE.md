---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: milestone_complete
stopped_at: Phase 9 context gathered
last_updated: "2026-06-04T05:18:19.592Z"
progress:
  total_phases: 9
  completed_phases: 7
  total_plans: 35
  completed_plans: 33
  percent: 94
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Core value:** When a new trade alert is posted on Bravos Research, the correct order is placed in IBKR within minutes — without manual intervention.
**Current focus:** Phase 08 — live-deployment

## Current Position

Phase: 08 (live-deployment) — EXECUTING
Plan: 1 of 3

## Performance Metrics

**Velocity:**

- Total plans completed: 16
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 04 | 5 | - | - |
| 05 | 3 | - | - |
| 09 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*
| Phase 01 P01 | 2min | 1 tasks | 4 files |
| Phase 01 P01 | 45 | 2 tasks | 6 files |
| Phase 02 P01 | 4min | 2 tasks | 7 files |
| Phase 02 P02 | 12min | 2 tasks | 3 files |
| Phase 02 P03 | 32min | 1 tasks | 3 files |
| Phase 02 P04 | 15min | 1 tasks | 1 files |
| Phase 02 P05 | 5 | 1 tasks | 2 files |
| Phase 03 P01 | 3min | 3 tasks | 3 files |
| Phase 03 P02 | 3min | 2 tasks | 2 files |
| Phase 03 P03 | 3min | 2 tasks | 2 files |
| Phase 08 P01 | 3min | 3 tasks | 3 files |
| Phase 08 P02 | 8min | 3 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- No decisions logged yet. See PROJECT.md Key Decisions.
- [Phase 01]: Wave 0 test stubs written with full bodies inside @pytest.mark.skip — future plans remove decorator, not rewrite test
- [Phase 01]: DEV-01: Python 3.13.5 + miniconda3 (not 3.11+venv) — mirrors opt-trade-vm4 exactly
- [Phase 01]: DEV-02: pip install ibapi==9.81.1.post1 (not official zip) — mirrors opt-trade-vm4
- [Phase 01]: DEV-03: Ubuntu 24.04 LTS Noble — use ubuntu-2404-lts-amd64 image for bravos_vm1
- [Phase 01]: DEV-04: ibgateway.service Restart=always,RestartSec=15 — mirrors opt-trade-vm4
- [Phase 02]: Phase 2 Wave 0 follows Phase 1 skip-stub pattern: full test bodies inside @pytest.mark.skip, skip reason names implementing plan
- [Phase 02]: DB migration SQL written and ready; not applied live because Cloud SQL Auth Proxy binary unavailable on this VM state
- [Phase 02]: Confidence threshold: 4=high, >=2=medium, <2=low (test behavior overrides D-10 prose)
- [Phase 02]: parse_method='spacy' when ticker is None — marks NLP path was entered, even if spaCy unavailable
- [Phase 02]: WEIGHT_RE matches 'weight from X to Y' in addition to 'weight of X to Y'
- [Phase 02]: WordPress login fields: name='log' (username) and name='pwd' (password) — standard WP /my-account/ form
- [Phase 02]: Selector defaults are WordPress standard (article, h2 a, .entry-content) — must be confirmed against live Bravos site
- [Phase 02]: Un-skipping tests is the canonical way to activate Wave 0 stubs — no rewrite needed, the test body was complete
- [Phase 02]: v2 migration (infra/migrate_signals_v2.sql) adds parse_method + scraped_at; applied to live Cloud SQL before running tests
- [Phase 02]: Daemon schedule loop runs session health check (not category polling) — Gmail-triggered arch; alerts arrive via email, loop keeps Chrome driver warm
- [Phase 02]: Integration tests accept TEST_ALERT_URL env var to specify a real post URL on VM without hardcoding
- [Phase 03]: IBApp uses combined EWrapper+EClient Pattern B — fewer files, clean MRO since EWrapper has no __init__
- [Phase 03]: error() routing via module-level code sets (_IGNORE_CODES, _IMMEDIATE_RECONNECT_CODES, etc.) — O(1) lookup, easily extended
- [Phase 03]: Plan 03-2/03-3 stubs raise NotImplementedError — explicit contract, not silent no-op
- [Phase 03]: Run broker tests with miniconda3 python (/home/chris_s_dodd/miniconda3/bin/python) — system python3 lacks ibapi
- [Phase 03]: _trigger_reconnect uses lock-guarded _reconnecting flag to prevent duplicate reconnect thread spawns
- [Phase 03]: 03-2 tests permanently unskipped (not re-skipped) — 7 tests now pass with miniconda3 python
- [Phase 03]: reqAllOpenOrders() used in run_startup_reconciliation (not reqOpenOrders()) — captures manually-placed TWS orders for full reconciliation; Phase 4 order tracking uses scoped reqOpenOrders()
- [Phase 03]: DB integration tests deferred to bravos-vm1 — Cloud SQL Auth Proxy not running on dev VM; 2 tests unskipped and will run on VM
- [Phase 03]: Helper functions _write_position_snapshot and _reconcile_against_db are module-level not IBApp methods — DB operations logically separate from IBKR connection class
- [Phase ?]: Locked in 08-01
- [Phase 08-02]: schedule string '06:00' UTC (not '01:00') — schedule library uses VM local time; '01:00' would fire at 8pm ET (market hours)
- [Phase 08-02]: _scraper=None guard set BEFORE old_scraper.shutdown() — run_cycle null-guard at line 92 provides safe concurrent access without a lock
- [Phase 08-02]: DST limitation accepted — 06:00 UTC = 01:00 ET winter / 02:00 ET summer; both inside safe window after Gateway restart, before pre-market
- [Phase 08-02]: run_gmail.py is a pure stub (no IMAP, no process_alert) — INGST-V2-01 tracked as future phase; stub prevents bravos-gmail.service thrash

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: Parser regex patterns must be validated against 20+ real Bravos alert samples before connecting to execution path. Real post format variation is unconfirmed.
- [Phase 1]: Bravos site anti-bot posture unconfirmed at 5-minute polling interval — monitor during Phase 1 implementation.
- [Phase 7]: IB Gateway nightly restart window timing (~11:45pm–12:15am ET) needs confirmation against actual Gateway behavior before configuring the systemd pause timer.

### Quick Tasks Completed

| # | Description | Date | Commit | Status | Directory |
|---|-------------|------|--------|--------|-----------|
| 260707-75m | Fix IBKR reconnect not re-running reconciliation (stale-NLV silent trade drop) | 2026-07-07 | 71d5b25 | | [260707-75m](./quick/260707-75m-fix-ibkr-reconnect-loop-not-re-running-s/) |
| 260708-7lk | Fix Chrome driver crash death loop (self-healing run_cycle) | 2026-07-08 | 98c3391 | | [260708-7lk](./quick/260708-7lk-fix-chrome-driver-crash-death-loop-dead-/) |
| 260708-9az | Fix IBKR error 10268 (clear eTradeOnly/firmQuoteOnly on all orders) | 2026-07-08 | e2f1f74 | | [260708-9az](./quick/260708-9az-fix-ibkr-error-10268-ibapi-9-81-order-de/) |
| 260710-7bi | Session 0b: test-DB isolation guard — tests can never write to production bravos_trading | 2026-07-10 | 25b761d | | [260710-7bi](./quick/260710-7bi-session-0b-from-planning-implementation-/) |
| 260714-6gz | Session 1a: rejection routing + honest order statuses (IMPROVEMENTS.md item 3) | 2026-07-14 | 001e94e | Needs Review | [260714-6gz](./quick/260714-6gz-session-1a-from-planning-implementation-/) |
| 260716-7dx | Session 1b: fill replay on connect via reqExecutions + double-count gating (IMPROVEMENTS.md item 16) | 2026-07-16 | c0e5fc8 | Verified | [260716-7dx](./quick/260716-7dx-session-1b-from-planning-implementation-/) |
| 260716-82s | Session 2a: shared DB helper bravos/db.py, fail-loud password, 10 sites consolidated (IMPROVEMENTS.md item 1) | 2026-07-17 | 743d92e | | [260716-82s](./quick/260716-82s-session-2a-from-planning-implementation-/) |
| 260716-9ro | Session 2b: bravos/runtime.py process-state module; scraper drops scripts-import inversion (IMPROVEMENTS.md item 2) | 2026-07-17 | 7a5593d | | [260716-9ro](./quick/260716-9ro-session-2b-from-planning-implementation-/) |
| 260717-9xp | Session 2c: bounded honest Gmail retry — explicit process_alert result, 10-strike alert+mark-seen, 0-rows fallback (IMPROVEMENTS.md item 17) | 2026-07-18 | d8b0ca9 | | [260717-9xp](./quick/260717-9xp-session-2c-from-planning-implementation-/) |
| 260717-b3h | Session 3: public IBApp API (get_nlv/get_daily_pnl/fetch_delayed_price/place_order_and_wait) + deduplicated submit paths (IMPROVEMENTS.md items 4 and 6) | 2026-07-21 | f99adb3 | Verified 9/9 (2 gap-fix rounds) | [260717-b3h](./quick/260717-b3h-session-3-3a-3b-from-planning-implementa/) |

## Session Continuity

Last activity: 2026-07-21 - Completed quick task 260717-b3h: IBApp public API + submit-path dedup (IMPLEMENTATION_PLAN.md Session 3)
Last session: 2026-06-01T05:11:23.989Z
Stopped at: Phase 9 context gathered
Resume file: .planning/phases/09-bracket-orders/09-CONTEXT.md
Next action: /gsd:execute-phase 3
