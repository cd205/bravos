# Phase 6: Paper Trading Validation - Context

**Gathered:** 2026-05-15
**Status:** Ready for planning

<domain>
## Phase Boundary

The full pipeline (scrape → parse → risk → order → fill → reconcile) has been exercised end-to-end on the paper account with real Bravos signals, and no critical failures remain unresolved. Phase 6 is validation-only: no new persistent architecture is added. Any bugs found are fixed in-place within this phase before it closes.

</domain>

<decisions>
## Implementation Decisions

### Signal Sourcing
- **D-01:** Primary validation method: call `scraper.process_alert(url)` directly on real historical Bravos post URLs — exercises the full scrape+parse path with real HTML from a live session. The user will provide a list of 10+ URLs; no discovery script needed.
- **D-02:** URLs must be selected deliberately to cover all 4 action types: `open`, `add`, `partial_close`, `close`. SC #2 requires all action types to work correctly.
- **D-03:** After the seeded batch is exercised, leave the daemon running to observe live incoming alerts as a secondary validation layer (timing-sensitive issues: session expiry, reconnect recovery).

### Bug Fix Policy
- **D-04:** Bugs are fixed in-place within Phase 6 plans. The phase does not close until all order-path failures are resolved.
- **D-05:** Blocking failure threshold: any bug that prevents an order from being placed or causes an incorrect order (wrong ticker, wrong action type, wrong quantity) is blocking. Parser edge cases and log noise are not blocking unless they corrupt the order path.
- **D-06:** Two output documents: `BUG-LOG.md` (bugs logged as they surface, with root cause and fix reference) and `VALIDATION-REPORT.md` (overall pass/fail per success criterion).

### Validation Run Structure
- **D-07:** Scripted sequence: a validation script (`scripts/validate_pipeline.py` or similar) calls `process_alert(url)` for each URL in order, checks DB state after each, and prints PASS/FAIL per scenario. Deterministic — every scenario is explicitly verified.
- **D-08:** Pass/fail verification: Claude's discretion — use whatever combination of DB state checks and IBKR position queries gives sufficient confidence that the full pipeline worked end-to-end for each scenario.
- **D-09:** Validation runs on bravos-vm1 with real IB Gateway (paper account, port 4002). No mocked environment — full end-to-end coverage is the point of this phase.

### Out-of-Hours Order Path
- **D-10:** Live paper orders are only placed during NYSE market hours (09:30–16:00 ET) — the market hours gate is NOT bypassed. This means the seeded validation run must execute during market hours to exercise the order→fill→reconcile path.
- **D-11:** The order→fill path is additionally covered by the unit test suite (Phase 4/5 test stubs in `tests/test_execution.py` and `tests/test_positions.py`). Claude's discretion: assess the current unskip state of those stubs and determine whether Phase 6 needs to unskip any, or whether a new end-to-end integration test with a mock IBApp is warranted. Either approach is acceptable as long as the order/fill/lot logic is demonstrably covered before the phase closes.

### Claude's Discretion
- Exact DB state assertions in the validation script (which tables, which fields, which values)
- Whether `VALIDATION-REPORT.md` and `BUG-LOG.md` are written to `scripts/` or `docs/` or a new `validation/` directory
- Whether the validation script tears down test state (DB cleanup) between scenarios or accumulates state across the run
- Unskip strategy for Phase 4/5 test stubs — fix as needed to make the test suite green before closing the phase

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Full pipeline entrypoints
- `scripts/run_ingestion.py` — daemon entry point; `process_alert(url)` call path; periodic reconciliation wiring; IBApp startup sequence
- `bravos/ingestion/scraper.py` — `BravosScraper.process_alert(url)` — the method the validation script calls
- `bravos/execution/executor.py` — `execute_signal()` — called by scraper after signal store; risk gate + order submission
- `bravos/risk/gate.py` — `RiskGate.check()` — single synchronous gate; market hours, max positions, allocation cap, circuit breaker
- `bravos/execution/positions.py` — `open_lot()`, `partial_close_lot()`, `close_lot()` — called from execDetails callback

### IBKR connection layer
- `bravos/broker/connection.py` — IBApp class; execDetails/orderStatus callbacks (Phase 5); run_periodic_reconciliation(); heartbeat monitor

### Configuration
- `bravos/config/settings.py` — TRADING_MODE, get_ibkr_port(), MAX_OPEN_POSITIONS, MAX_ALLOCATION_PCT, DAILY_LOSS_THRESHOLD, WEIGHT_PCT_PER_UNIT

### Database schema
- `infra/schema.sql` — signals, orders, executions, position_lots, broker_positions_snapshot tables; the tables the validation script queries for pass/fail checks

### Phase requirements being validated
- `.planning/REQUIREMENTS.md` — EXEC-01 through EXEC-06 (order sizing, MKT orders, market hours, lifecycle, fills, partial fills); RISK-01 through RISK-04; IBKR-04 (periodic reconciliation); POS-01 through POS-03 (lot tracking, FIFO); IBKR-05 (paper mode toggle)

### Existing test files (for unskip assessment)
- `tests/test_execution.py` — Phase 4 stubs (Wave 0 pattern: full bodies inside @pytest.mark.skip)
- `tests/test_positions.py` — Phase 5 stubs (same pattern)
- `tests/conftest.py` — db_connection fixture; test infrastructure

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `BravosScraper.process_alert(url)` in `bravos/ingestion/scraper.py` — the exact method to call from the validation script; exercises scrape+parse+store+execute in one call
- `_get_db_connection()` in `scripts/run_ingestion.py` — reuse this pattern in the validation script for DB assertions
- `tests/conftest.py: db_connection` fixture — available for any new test-based validation
- `ibapp.run_periodic_reconciliation(db_conn)` — already wired in `run_cycle()`; verification that it runs cleanly during the live observation phase

### Established Patterns
- Wave 0 test stub pattern: full test bodies inside `@pytest.mark.skip(reason="implementing: <plan-name>")` — Phase 6 assesses current unskip state and determines what remains
- DB write-before-submit pattern (Phase 4 D-08): PENDING_SUBMISSION written before placeOrder() — validation checks for this ordering
- Module-level singleton: `broker_module.ibapp` — validation script must initialize this singleton the same way `run_ingestion.py` does before calling `process_alert()`

### Integration Points
- Validation script must replicate the IBApp startup sequence from `run_ingestion.py` (connect_and_run → run_startup_reconciliation → install _db_conn → reqPnL → start_heartbeat_monitor) before calling process_alert()
- Or, alternatively, it can call `run_ingestion.py` as a subprocess and inject URLs via a queue or direct function call — Claude's discretion on integration approach
- BUG-LOG.md and VALIDATION-REPORT.md are new artifacts; no existing location — create under a `validation/` directory or similar

</code_context>

<specifics>
## Specific Ideas

- The validation script should print a clear PASS/FAIL summary at the end, one line per scenario (URL → action type → result), so the operator can see at a glance what passed.
- For the DB state check after each process_alert() call: at minimum verify that a signal row exists, an orders row exists with the correct ticker/action, and (if market hours) an executions row and position_lots row exist.
- The live observation phase (daemon running post-seeded-batch) specifically tests: session expiry recovery (INGST-07), IBKR heartbeat recovery (IBKR-02), and periodic reconciliation (IBKR-04) — these are timing-dependent and can't be scripted.

</specifics>

<deferred>
## Deferred Ideas

- Live account activation — Phase 8 scope
- Automated daily validation run via cron — Phase 8 hardening concern
- Gmail poller validation (INGST-V2-01 secondary channel) — v2 requirement; not part of Phase 6 scope

None beyond the above — discussion stayed within phase scope.

</deferred>

---

*Phase: 06-paper-trading-validation*
*Context gathered: 2026-05-15*
