# Phase 6: Paper Trading Validation — Research

**Researched:** 2026-05-15
**Domain:** End-to-end pipeline validation on paper account; test-suite triage; schema migration
**Confidence:** HIGH (all findings verified against live codebase and database)

---

## Summary

Phase 6 is a validation-and-bug-fix phase, not a feature phase. No new persistent architecture is added. The work falls into three streams: (1) fix three known test failures to achieve a green suite before validation begins, (2) build a scripted validation harness that exercises the full pipeline against real Bravos post URLs during NYSE market hours, and (3) run a live observation period to confirm timing-sensitive behaviors (session recovery, heartbeat recovery, periodic reconciliation).

The three known test failures each have a clear, bounded root cause. Two (`test_gate_log_pass`, `test_gate_log_block`) require applying an unapplied schema migration (`infra/migrate_phase4.sql` creates `risk_gate_log`, which exists in the file but not in the live DB). One (`test_order_db_write_pending`) is a test-isolation bug: `ibkr_order_id=1000` is hardcoded and a prior test run leaves a `SUBMITTED` row in the `orders` table; the `capture_db_state` side-effect reads the stale row instead of the freshly inserted `PENDING_SUBMISSION` row. The fix is adding a cleanup step or making the order id unique.

The 10 previously-skipped `test_positions.py` stubs are fully passing as of this research session — they were unskipped and implemented during Phase 5 and all pass against the live DB. The 8 skipped tests in `test_broker.py` are Wave 0 stubs for plan 03-1 (IBApp skeleton) that were never unskipped because their plan preceded the full implementation; their test bodies are correct but the decorator was never removed.

**Primary recommendation:** Fix 3 failing tests (Wave 0), apply `migrate_phase4.sql` to the live DB, then build the validation script. The test fixes and migration are Wave 1; the validation script and live observation are Wave 2.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Signal Sourcing**
- D-01: Primary validation: call `scraper.process_alert(url)` directly on real historical Bravos post URLs — exercises full scrape+parse path with real HTML from a live session. The user will provide a list of 10+ URLs; no discovery script needed.
- D-02: URLs must cover all 4 action types: `open`, `add`, `partial_close`, `close`.
- D-03: After the seeded batch, leave the daemon running to observe live incoming alerts as secondary validation (session expiry, reconnect recovery).

**Bug Fix Policy**
- D-04: Bugs are fixed in-place within Phase 6 plans. Phase does not close until all order-path failures are resolved.
- D-05: Blocking failure threshold: any bug that prevents an order from being placed or causes an incorrect order (wrong ticker, wrong action type, wrong quantity). Parser edge cases and log noise are not blocking unless they corrupt the order path.
- D-06: Two output documents: `BUG-LOG.md` (bugs as they surface) and `VALIDATION-REPORT.md` (overall pass/fail per success criterion).

**Validation Run Structure**
- D-07: Scripted sequence: `scripts/validate_pipeline.py` (or similar) calls `process_alert(url)` for each URL, checks DB state after each, prints PASS/FAIL per scenario.
- D-08: Pass/fail verification: Claude's discretion on which DB state checks and IBKR position queries to use.
- D-09: Validation runs on bravos-vm1 with real IB Gateway (paper account, port 4002). No mocked environment.

**Out-of-Hours Order Path**
- D-10: Live paper orders only during NYSE market hours (09:30–16:00 ET) — market hours gate NOT bypassed. Seeded validation run must execute during market hours.
- D-11: Order→fill path additionally covered by unit tests (Phases 4/5 stubs). Claude's discretion on unskip strategy.

### Claude's Discretion
- Exact DB state assertions in validation script (tables, fields, values)
- Whether `VALIDATION-REPORT.md` and `BUG-LOG.md` go in `scripts/`, `docs/`, or a new `validation/` directory
- Whether validation script tears down test state between scenarios or accumulates state
- Unskip strategy for Phase 4/5 test stubs — fix as needed to make suite green

### Deferred Ideas (OUT OF SCOPE)
- Live account activation — Phase 8 scope
- Automated daily validation run via cron — Phase 8 hardening concern
- Gmail poller validation (INGST-V2-01 secondary channel) — v2 requirement
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| IBKR-05 | Paper/live toggle via TRADING_MODE=paper | Verified: `settings.py` uses `TRADING_MODE` env var; `get_ibkr_port()` returns 4002 for paper. No code changes needed — already delivered Phase 3. |
| EXEC-01 | Share quantity formula: abs(delta) × WEIGHT_PCT_PER_UNIT × NLV / price | Verified: `executor._calculate_quantity()` implements formula correctly. `test_quantity_formula` passes. |
| EXEC-02 | MKT DAY orders for all action types | Verified: `_build_order()` always uses orderType='MKT', tif='DAY'. `test_build_order_buy/sell` pass. |
| EXEC-03 | Market hours gate (09:30–16:00 ET) | Verified: `gate._is_market_hours()` + Gate 1 in `RiskGate.check()`. Tests pass. |
| EXEC-04 | Order lifecycle tracking via callbacks | Verified: PENDING_SUBMISSION→SUBMITTED/REJECTED via `orderStatus` callback. Tests pass (except D-08 test design issue — see Pitfall 2). |
| EXEC-05 | Fill price + quantity from execDetails | Verified: `connection._handle_exec_details()` writes `executions` row + calls `open_lot`/`partial_close_lot`. Tests pass. |
| EXEC-06 | Partial fill handling | Verified: `orderStatus Filled/PartiallyFilled` → `_update_order_filled/_partial`. Tests pass. |
| RISK-01 | Max open positions gate | Verified: Gate 2 in `RiskGate.check()`. Tests pass. |
| RISK-02 | Max allocation per trade gate | Verified: Gate 3 in `RiskGate.check()`. Tests pass. |
| RISK-03 | Daily loss circuit breaker | Verified: Gate 4 in `RiskGate.check()`. Tests pass. |
| RISK-04 | Every gate decision logged with reason + values | Verified: `_log_and_return()` writes to `risk_gate_log`. CURRENTLY FAILING — table not in live DB. Migration exists in `infra/migrate_phase4.sql`. |
| IBKR-04 | Periodic reconciliation vs IBKR positions | Verified: `run_periodic_reconciliation()` in `connection.py`, wired into `run_ingestion.run_cycle()`. Test passes. |
| POS-01 | Open position lots tracking | Verified: `positions.open_lot()`. All 5 positions tests pass. |
| POS-02 | Closed positions with P&L | Verified: `partial_close_lot()` sets `lot_closed_at`, `exit_price`, `pnl`. Tests pass. |
| POS-03 | FIFO lot assignment | Verified: `partial_close_lot()` orders by `lot_opened_at ASC`. Tests pass. |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Scrape real Bravos URLs | Browser/Selenium (main thread) | DB (signal storage) | `process_alert()` owns the full scrape+parse+store path |
| Risk gate check | Backend (synchronous, in-process) | DB (risk_gate_log) | Single gate in executor, writes log row per decision |
| Order sizing + submission | Backend (main thread) + IBKR API thread | DB (orders table) | `execute_signal()` sizes and places; `orderStatus` callback confirms |
| Fill capture | IBKR API thread (callback) | DB (executions + position_lots) | `execDetails` callback wired to `_handle_exec_details` |
| Periodic reconciliation | Main thread (every 5-min cycle) | DB (broker_positions_snapshot) | `run_periodic_reconciliation()` in `run_cycle()` |
| DB schema corrections | DB (migration SQL) | — | `infra/migrate_phase4.sql` must be applied before any test touching `risk_gate_log` |
| Validation script | Backend (standalone script) | DB (assertions) | Calls `process_alert()` per URL, reads DB to confirm |
| Validation artifacts (BUG-LOG, REPORT) | File system (validation/ dir) | — | Claude's discretion per CONTEXT.md |

---

## Standard Stack

### Core (already installed — no new dependencies)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| ibapi | 9.81.1.post1 | IBKR connection, order placement, callbacks | Verified: `python -c "import ibapi"` returns OK |
| psycopg2-binary | 2.9.12 | PostgreSQL connection | Verified: installed in miniconda3 env |
| selenium | 4.x | Chrome driver for `process_alert()` scraping | Installed (Phase 2) |
| pytest | 9.0.3 | Test runner | Verified: current test session uses 9.0.3 |

**No new packages are required for Phase 6.** [VERIFIED: live environment check]

### Test runner command

```bash
# All tests except live integration (fast, no browser):
/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ \
  --ignore=tests/test_ingestion_integration.py \
  --ignore=tests/test_infrastructure.py -v

# Quick smoke during development:
/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_execution.py tests/test_positions.py -v
```

---

## Architecture Patterns

### System Architecture Diagram

```
bravos-vm1 (paper trading, TRADING_MODE=paper)

Operator                   Validation Script               Live Daemon
   |                            |                              |
   | provides URL list          |                              |
   |------------------------->  |                              |
                                |-- process_alert(url) ------->|
                                |   (Chrome + Selenium)        |-- fetch_post() --> bravosresearch.com
                                |                              |-- parse_signal()
                                |                              |-- _store_signal() --> signals table
                                |                              |-- execute_signal()
                                |                              |     |-- RiskGate.check()
                                |                              |     |     |-- _log_and_return() --> risk_gate_log
                                |                              |     |-- _fetch_price() --> IB Gateway 4002
                                |                              |     |-- _submit_order()
                                |                              |           |-- INSERT orders (PENDING_SUBMISSION)
                                |                              |           |-- placeOrder() --> IB Gateway
                                |                              |           |-- wait orderStatus callback
                                |                              |           |-- UPDATE orders (SUBMITTED)
                                |                              |
                                |   IB Gateway (paper) fires callbacks:
                                |     execDetails --> _handle_exec_details()
                                |                         |--> INSERT executions
                                |                         |--> open_lot() / partial_close_lot()
                                |     orderStatus Filled --> _update_order_filled()
                                |
                                |-- DB assertions per URL
                                |     signals: row exists, correct ticker/action
                                |     risk_gate_log: row exists, gate_passed value
                                |     orders: row exists, status=SUBMITTED (if in-hours)
                                |     executions: row exists with fill price (post-fill)
                                |     position_lots: open lot created (BUY) or closed (SELL)
                                |
                                |-- print PASS/FAIL per scenario
                                |-- write BUG-LOG.md (failures) + VALIDATION-REPORT.md (summary)
```

### Validation Script Structure

The script replicates the IBApp startup sequence from `run_ingestion.py` before calling `process_alert()`. The canonical sequence (verified against `run_ingestion.py` lines 138–195):

```python
# 1. Instantiate and set module-level singleton
_ibapp = IBApp(host=settings.IBKR_HOST, port=settings.get_ibkr_port(),
               client_id=settings.IBKR_CLIENT_ID)
broker_module.ibapp = _ibapp

# 2. Connect (30s timeout)
ibkr_ok = _ibapp.connect_and_run(timeout=30)

# 3. Startup reconciliation
_ibapp.run_startup_reconciliation(db_conn, timeout=30)

# 4. Install _db_conn for fill callbacks (execDetails/orderStatus fire on api thread)
_ibapp._db_conn = _get_db_connection()  # separate connection — api thread owns it

# 5. Subscribe reqPnL (circuit breaker needs live daily P&L)
if _ibapp._account_name:
    _ibapp.reqPnL(REQ_ID_PNL, _ibapp._account_name, "")

# 6. Start heartbeat monitor
_ibapp.start_heartbeat_monitor()

# 7. Instantiate and start scraper
scraper = BravosScraper()
scraper.startup()

# 8. Process each URL in order
for url, expected_action in url_list:
    scraper.process_alert(url)
    time.sleep(5)  # allow callbacks to fire
    result = check_db_state(url, expected_action, db_conn)
    print(f"{'PASS' if result.ok else 'FAIL'} {url} -> {result.detail}")
```

**Critical**: `_ibapp._db_conn` must be a SEPARATE psycopg2 connection from the one used for DB assertions. The api thread owns `_ibapp._db_conn`. [VERIFIED: connection.py comments, run_ingestion.py lines 156–171]

### Recommended Directory Structure

```
validation/
├── BUG-LOG.md            # bugs logged as they surface (created manually during run)
└── VALIDATION-REPORT.md  # final pass/fail per success criterion

scripts/
└── validate_pipeline.py  # validation script (new — Phase 6)
```

### Pattern: DB State Assertions

After each `process_alert(url)` call, check these tables in sequence. Minimum required:

```python
def check_db_state(url: str, expected_action: str, db_conn, wait_for_fill_sec: int = 10) -> Result:
    # 1. Signal row
    cur.execute("SELECT id, ticker, action_type, confidence FROM signals WHERE post_url=%s", (url,))
    signal_row = cur.fetchone()
    # 2. Risk gate log row
    cur.execute("SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s", (signal_id,))
    # 3. Order row (only if confidence='high' and in-hours)
    cur.execute("SELECT id, ibkr_order_id, status FROM orders WHERE signal_id=%s", (signal_id,))
    # 4. Execution row (wait up to wait_for_fill_sec)
    #    — only after confirming order row exists and IBKR is in paper mode
    # 5. Position lot row (if BUY fill received)
```

### Anti-Patterns to Avoid

- **Calling `process_alert()` before IBApp is initialized**: `execute_signal` guard returns early if `ibapp is None or not ibapp.is_connected()`. The validation script must complete the full startup sequence before processing URLs.
- **Sharing the fill-callback DB connection with the main thread**: psycopg2 connections are not thread-safe. `_ibapp._db_conn` is owned by the ibkr-api thread; the validation script's DB assertions must use a different connection.
- **Running the seeded batch outside market hours**: Risk Gate 1 blocks all orders outside 09:30–16:00 ET. All order→fill checks in the validation pass only during market hours (D-10).
- **Hardcoding `ibkr_order_id` in tests**: This caused the `test_order_db_write_pending` failure. Use unique IDs or rollback between runs.

---

## Bug Analysis: Pre-existing Failures

### Bug 1: `risk_gate_log` Table Missing

**Root cause:** `infra/migrate_phase4.sql` defines the `risk_gate_log` table and was written during Phase 4 plan 04-01. It was never applied to the live Cloud SQL database (Cloud SQL Auth Proxy was unavailable on the dev VM at that time — recorded in STATE.md). [VERIFIED: DB query shows 5 tables, no `risk_gate_log`]

**Affected tests:** `test_gate_log_pass`, `test_gate_log_block`

**Error:** `psycopg2.errors.UndefinedTable: relation "risk_gate_log" does not exist`

**Fix:**
```bash
PGPASSWORD="$BRAVOS_DB_PASSWORD" psql -h 127.0.0.1 -U bravos -d bravos_trading \
  -f /home/chris_s_dodd/bravos/infra/migrate_phase4.sql
```

The SQL is idempotent (`CREATE TABLE IF NOT EXISTS`). After applying, both tests will pass — the test bodies are complete and correct. [VERIFIED: `gate.py` `_log_and_return()` exactly matches the INSERT in `migrate_phase4.sql`]

### Bug 2: `test_order_db_write_pending` Test Isolation

**Root cause:** The test hardcodes `mock_ibapp.next_order_id = 1000`. On the first test run ever, there is no row with `ibkr_order_id=1000` in `orders`. But after any prior run that completed `_submit_order`, a row with `ibkr_order_id=1000, status='SUBMITTED'` is left in the DB (the test does not rollback or clean up). On subsequent runs, `capture_db_state` queries `WHERE ibkr_order_id=1000` and finds the stale `SUBMITTED` row rather than the freshly inserted `PENDING_SUBMISSION` row. [VERIFIED: the error `assert 'SUBMITTED' == 'PENDING_SUBMISSION'` and the log message "orderStatus callback timed out" confirm the 3-second wait ran, then DB was updated to SUBMITTED, then the assertion failed]

**Affected test:** `test_order_db_write_pending`

**Two fix options (Claude's discretion):**
- Option A: Use `os.urandom(4)` to generate a unique `next_order_id` per run (same pattern used for `post_url` in all other tests in the file). Simple one-line change.
- Option B: Add rollback/cleanup after the test. The test already uses `db_connection` fixture which closes the connection but does not rollback. A `finally: db_connection.rollback()` block (like `test_positions.py` tests use) would also work.

Option A is simpler. The test body is otherwise correct — `capture_db_state` fires during `placeOrder`, at which point the INSERT has committed and should show `PENDING_SUBMISSION` for the fresh row.

### Bug 3: `test_signal_stored_with_raw_html` (integration test)

**Status:** Expected failure. The integration test hits a real URL (`https://bravosresearch.com/?p=1`) which requires a live authenticated Bravos session. Not run in the standard suite (`--ignore=tests/test_ingestion_integration.py`). This is not a blocker for Phase 6. [VERIFIED: additional_context and test file exclude]

### Skipped Tests Status

**`test_broker.py` (8 skipped):** Wave 0 stubs for plan 03-1 that were never unskipped. Their test bodies correctly test IBApp init, port config, and module singleton — behaviors that are now implemented. These can be unskipped as a minor cleanup, but they are not blocking. The 15 already-passing broker tests cover all the same code paths. [VERIFIED: test_broker.py lines 19–86]

**`test_positions.py` (0 skipped):** All 10 were unskipped and pass. The additional_context description of "10 skipped stubs" was based on the Wave 0 state; Phase 5 unskipped them. [VERIFIED: test run output — 10/10 pass]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| IBApp startup in validation script | New connection/startup code | Copy startup sequence from `run_ingestion.main()` | The sequence is known-correct; divergence causes subtle bugs (e.g. missing `_db_conn` for fill callbacks) |
| DB cleanup between validation scenarios | Custom truncation/teardown | Use unique URL slugs + rely on ON CONFLICT DO NOTHING dedup | Accumulating state is fine for the validation run; teardown risks corrupting state mid-run |
| Signal URL discovery | Web scraping for post list | Operator-provided URL list (D-01) | Already decided; scraper.process_alert takes a URL directly |
| Connection pool for validation assertions | Thread-safe pool | Two fixed connections (main thread assertion conn + `_ibapp._db_conn` for api thread) | This is exactly how `run_ingestion.py` does it; the 2-connection pattern is established |

---

## Common Pitfalls

### Pitfall 1: Validation Script Starts Without `_ibapp._db_conn`
**What goes wrong:** `execDetails` and `orderStatus Filled/PartiallyFilled` callbacks fire on the ibkr-api thread. If `_ibapp._db_conn is None`, the fill capture is silently skipped (logged as WARNING). Validation checks for `executions` rows will always fail.
**Why it happens:** Easy to omit the `_db_conn` install step when copying from `run_ingestion.py`.
**How to avoid:** Explicitly install `_ibapp._db_conn = _get_db_connection()` in the startup sequence, BEFORE processing any URL. [VERIFIED: `connection.py` line 549, `run_ingestion.py` lines 156–171]
**Warning signs:** `executions` table empty after processing a URL that should have generated fills; WARNING log "execDetails: _db_conn not set"

### Pitfall 2: Asserting DB State Too Quickly After `process_alert()`
**What goes wrong:** `process_alert()` returns after `execute_signal()` which returns after the `orderStatus` callback (3-second wait). But `execDetails` (fill capture) fires asynchronously after the order reaches the exchange. If the assertion queries `executions` immediately, the row may not exist yet.
**Why it happens:** IB Gateway paper account fills take 0.5–2 seconds after order confirmation.
**How to avoid:** Add a `time.sleep(5)` or a short polling loop (`for _ in range(10): check; if found: break; sleep(1)`) after `process_alert()` before asserting `executions` rows.
**Warning signs:** `executions` rows are intermittently present/absent for the same URL

### Pitfall 3: Risk Gate Always Blocks (Outside Market Hours)
**What goes wrong:** `RiskGate.check()` Gate 1 blocks all orders if run outside 09:30–16:00 ET. The validation script processes the URLs but no `orders` rows are created — `risk_gate_log` shows `gate_passed=False, reason='market_hours'`.
**Why it happens:** D-10 explicitly preserves the market hours gate for paper validation. Expected behavior, not a bug.
**How to avoid:** Schedule the seeded validation run during NYSE hours. For the test suite, both `test_gate_log_pass` and `test_gate_log_block` correctly mock `_is_market_hours` — no issue in unit tests.
**Warning signs:** All `risk_gate_log` rows show `gate_passed=False, reason containing 'market_hours'`

### Pitfall 4: `mock_ibapp._order_status_events` Interaction
**What goes wrong:** In unit tests, `mock_ibapp._order_status_events = {}` sets the dict on the mock. When `_submit_order` assigns `ibapp._order_status_events[order_id] = status_slot`, this modifies the dict on the mock object — this works correctly. The issue in `test_order_db_write_pending` is not with the mock mechanics but with stale DB state from a prior run (see Bug 2 above).
**How to avoid:** Use unique `ibkr_order_id` values per test run.

### Pitfall 5: 8 Skipped broker Tests — Not a Phase 6 Concern
**What goes wrong:** `test_broker.py` has 8 tests with `@pytest.mark.skip(reason="plan: 03-1")`. These tests are correct and their implementation exists. They were simply never unskipped.
**Status:** Not blocking. Can be unskipped as Phase 6 housekeeping if desired, but are NOT required for the pipeline validation.

### Pitfall 6: `reqPnL` Subscription Required for Circuit Breaker
**What goes wrong:** Without `reqPnL`, `ibapp._daily_pnl` stays `None`. Gate 4 (circuit breaker) in `RiskGate.check()` uses `if daily_pnl is not None and daily_pnl < threshold`. When `None`, the check is skipped (fail-open). This is the intended behavior for v1 (gate passes when no P&L data). But the validation must confirm this does not cause unexpected blocks.
**How to avoid:** Include `reqPnL` subscription in the validation startup sequence (same as `run_ingestion.py`). Verify `ibapp._account_name` is populated after `run_startup_reconciliation()` before calling `reqPnL`.

---

## Code Examples

### Apply Missing Migration
```bash
# Source: infra/migrate_phase4.sql [VERIFIED]
PGPASSWORD="$BRAVOS_DB_PASSWORD" psql \
  -h 127.0.0.1 -U bravos -d bravos_trading \
  -f /home/chris_s_dodd/bravos/infra/migrate_phase4.sql
```

### Fix `test_order_db_write_pending` (Option A — unique order_id)
```python
# Source: tests/test_execution.py line 244 [VERIFIED]
# Change:
mock_ibapp.next_order_id = 1000
# To:
order_id = int.from_bytes(os.urandom(3), "big") + 10000  # unique per run
mock_ibapp.next_order_id = order_id
# Also update any hardcoded references to 1000 in capture_db_state and the assertion
```

### Unskip 8 broker tests
```python
# Source: tests/test_broker.py lines 19–86 [VERIFIED]
# Remove @pytest.mark.skip decorator from:
# test_ibapp_init_sets_host_port_client_id
# test_ibapp_init_connected_event_is_clear
# test_ibapp_is_connected_returns_false_before_connect
# test_next_valid_id_sets_connected_and_stores_order_id
# test_paper_port_config
# test_live_port_config
# test_module_level_ibapp_singleton_is_none_at_import
# test_stop_sets_stop_event_and_clears_connected
```

### Minimal DB assertion pattern for validation script
```python
# Source: test patterns from test_execution.py + test_positions.py [VERIFIED]
import time

def assert_signal_processed(url: str, expected_ticker: str, expected_action: str,
                              db_conn, expect_order: bool = True) -> dict:
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, action_type, confidence FROM signals WHERE post_url=%s",
            (url,)
        )
        sig = cur.fetchone()
    if sig is None:
        return {"ok": False, "detail": "no signal row"}
    signal_id, ticker, action_type, confidence = sig
    if ticker != expected_ticker or action_type != expected_action:
        return {"ok": False, "detail": f"expected {expected_ticker}/{expected_action}, got {ticker}/{action_type}"}
    if not expect_order:
        return {"ok": True, "detail": "signal only (low confidence or out of hours)"}
    # Check risk_gate_log
    with db_conn.cursor() as cur:
        cur.execute("SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s", (signal_id,))
        gate = cur.fetchone()
    if gate is None:
        return {"ok": False, "detail": "no risk_gate_log row"}
    # Check orders
    with db_conn.cursor() as cur:
        cur.execute("SELECT ibkr_order_id, status FROM orders WHERE signal_id=%s", (signal_id,))
        order = cur.fetchone()
    if order is None:
        return {"ok": False, "detail": f"no order row (gate: {gate})"}
    return {"ok": True, "detail": f"order={order[0]} status={order[1]} gate={gate[1]}"}
```

---

## Runtime State Inventory

This phase does not rename or refactor any strings. However, there is one live DB state gap that must be addressed before the validation script can run:

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | `risk_gate_log` table does not exist in live DB | Apply `infra/migrate_phase4.sql` — Wave 1 task |
| Live service config | IB Gateway paper account (port 4002) not running on dev VM | Must be running on bravos-vm1 for validation run (D-09) |
| OS-registered state | None — no OS-level registrations affected | None |
| Secrets/env vars | `BRAVOS_DB_PASSWORD` needed by validation script (same as daemon) | None — already set on bravos-vm1 |
| Build artifacts | None — no compiled artifacts | None |

---

## Environment Availability

| Dependency | Required By | Available (dev VM) | Available (bravos-vm1) | Version | Fallback |
|------------|------------|-------------------|------------------------|---------|----------|
| Python (miniconda3) | All tests + validation script | Yes | Yes | 3.13.13 | — |
| psycopg2 | DB assertions, tests | Yes | Yes | 2.9.12 | — |
| ibapi | IBApp, broker tests | Yes | Yes | 9.81.1.post1 | — |
| PostgreSQL (Cloud SQL Auth Proxy) | DB tests, validation | Yes (port 5432) | Yes | 16.13 | — |
| selenium + Chrome | `process_alert()` scraping | Yes | Yes | 4.x | — |
| IB Gateway (paper, port 4002) | Live validation run | NOT running | Required | — | Cannot mock for D-09 |
| pytest | Test suite | Yes | Yes | 9.0.3 | — |

**Missing dependencies with no fallback:**
- IB Gateway (paper) on port 4002: required for the live validation run (D-09). Unit test fixes in Wave 1 do NOT require Gateway. Only the validation script in Wave 2 requires it. Operator must start Gateway on bravos-vm1 before running `validate_pipeline.py`.

**Missing dependencies with fallback:**
- None — all other dependencies are present in both environments.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | `pytest.ini` (project root) |
| Quick run command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_execution.py tests/test_positions.py -v` |
| Full suite command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ --ignore=tests/test_ingestion_integration.py --ignore=tests/test_infrastructure.py -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | Tests Exist? |
|--------|----------|-----------|-------------------|-------------|
| RISK-04 | risk_gate_log row written on gate decision | Integration (DB) | `pytest tests/test_execution.py::test_gate_log_pass tests/test_execution.py::test_gate_log_block` | Yes, FAILING (migration not applied) |
| EXEC-04 | PENDING_SUBMISSION before placeOrder | Integration (DB) | `pytest tests/test_execution.py::test_order_db_write_pending` | Yes, FAILING (test isolation bug) |
| EXEC-01 | Quantity formula | Unit | `pytest tests/test_execution.py::test_quantity_formula` | Yes, passing |
| EXEC-02 | MKT DAY order built | Unit | `pytest tests/test_execution.py::test_build_order_buy tests/test_execution.py::test_build_order_sell` | Yes, passing |
| EXEC-03 | Market hours gate | Unit | `pytest tests/test_execution.py::test_market_hours_gate_blocks tests/test_execution.py::test_market_hours_gate_passes` | Yes, passing |
| EXEC-05 | execDetails writes executions row | Unit (mock) | `pytest tests/test_positions.py::test_exec_details_writes_execution_row` | Yes, passing |
| EXEC-06 | Partial fill handling | Unit (mock) | `pytest tests/test_positions.py::test_order_status_filled tests/test_positions.py::test_order_status_partial` | Yes, passing |
| POS-01 | open_lot inserts row | Integration (DB) | `pytest tests/test_positions.py::test_open_lot_writes_row` | Yes, passing |
| POS-02 | close_lot sets P&L fields | Integration (DB) | `pytest tests/test_positions.py::test_close_lot_sets_fields` | Yes, passing |
| POS-03 | FIFO lot assignment | Integration (DB) | `pytest tests/test_positions.py::test_fifo_closes_oldest_lot_first` | Yes, passing |
| IBKR-04 | Reconciliation mismatch logged | Unit (mock + DB) | `pytest tests/test_positions.py::test_periodic_reconciliation_mismatch` | Yes, passing |
| IBKR-05 | Paper/live port toggle | Unit | unskipping `test_paper_port_config`, `test_live_port_config` in test_broker.py | Skipped, bodies correct |

### Current Test Counts (verified this session)

| Category | Count |
|----------|-------|
| PASSING | 58 (excluding integration tests) |
| FAILING (known pre-existing bugs) | 3 |
| SKIPPED (Wave 0 stubs) | 8 (test_broker.py plan 03-1 stubs) |
| Integration/infra (excluded from standard run) | ~8 |

**Target for Phase 6 gate:** 0 failures, 0 skipped in the standard run. The 8 broker stubs should also be unskipped (bodies are correct, implementation exists).

### Sampling Rate

- **Per task commit:** `pytest tests/test_execution.py tests/test_positions.py -v`
- **Per wave merge:** `pytest tests/ --ignore=tests/test_ingestion_integration.py --ignore=tests/test_infrastructure.py -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

None — existing test infrastructure covers all phase requirements. The three failing tests have complete bodies; they only need their pre-conditions fixed (schema migration, test isolation fix). No new test files are needed.

---

## Success Criteria Mapping

Phase 6 ROADMAP success criteria, with research notes:

| SC | Criterion | Research Note |
|----|-----------|---------------|
| SC-1 | At least 10 real Bravos Trade Alert posts processed end-to-end | Requires user-provided URL list (D-01). Validation script asserts signal+order+fill rows per URL. |
| SC-2 | No order reaches IBKR with wrong ticker, action type, or quantity | Covered by validation script DB assertions on `orders.ticker`, `orders.action`, `orders.quantity`. All action types must be represented (D-02). |
| SC-3 | All parser edge cases discovered during validation have been fixed | Covered by BUG-LOG.md + re-run. Not automatable in advance — requires observing real parses. |
| SC-4 | No critical system failure during a full trading day | Covered by live observation period (D-03): daemon running post-seeded-batch for ≥1 full trading day. Tests: INGST-07 (session expiry), IBKR-02 (heartbeat recovery), IBKR-04 (periodic reconciliation). These are timing-dependent and cannot be unit-tested. |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The 8 skipped broker tests in test_broker.py will pass after decorator removal | Bug Analysis | Low — the implementation is correct and the test bodies were written as Wave 0 stubs for the existing code. The risk is minimal. |
| A2 | `test_order_db_write_pending` fails due to stale `ibkr_order_id=1000` row from prior run | Bug Analysis | Medium — if the root cause is something else (e.g. MagicMock interaction), a different fix may be needed. The fix (unique order_id) should work regardless. |

All other claims in this research were verified against live code, the live database, and actual test run output.

---

## Open Questions

1. **URL list for seeded validation batch**
   - What we know: D-01 says "user will provide 10+ URLs covering all 4 action types"
   - What's unclear: The user has not yet provided the URL list
   - Recommendation: The validation script Wave 1 can hardcode a placeholder list (empty or with dummy URLs). Wave 2 replaces it with the real list when the user provides it. Do not block plan creation on URL list.

2. **State accumulation vs. teardown between scenarios**
   - What we know: CONTEXT.md leaves this to Claude's discretion
   - What's unclear: Accumulated state from 10+ URL runs may cause confusing failures (e.g. duplicate position lots if a ticker is processed twice)
   - Recommendation: Accumulate state (no teardown). Use unique Bravos post URLs — they are naturally unique, so ON CONFLICT DO NOTHING on signals table prevents re-processing. Position lot accumulation is fine for validation purposes and matches production behavior.

3. **Paper account fill latency**
   - What we know: IB Gateway paper account fills typically arrive within 1–2 seconds of order submission for liquid equities
   - What's unclear: Exact latency for each Bravos alert ticker — some may be less liquid
   - Recommendation: Use a 5-second wait after `process_alert()` before asserting `executions` rows. Log a note if no fill arrives within 10 seconds.

---

## Sources

### Primary (HIGH confidence)
- Live codebase inspection: `bravos/risk/gate.py`, `bravos/execution/executor.py`, `bravos/broker/connection.py`, `bravos/execution/positions.py`, `bravos/ingestion/scraper.py` — all Phase 4/5 implementation verified
- Live test run output: `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ ...` — confirmed 3 failed, 58 passed, 8 skipped
- Live DB schema: `psql ... -c "\dt"` — confirmed `risk_gate_log` absent, 5 other tables present
- `infra/migrate_phase4.sql` — verified DDL creates `risk_gate_log` matching `gate.py`'s INSERT
- `infra/schema.sql` — verified base schema (no `risk_gate_log`)
- `.planning/phases/06-paper-trading-validation/06-CONTEXT.md` — all decisions locked/discretion verbatim
- `bravos/config/settings.py` — verified TRADING_MODE, `get_ibkr_port()`, risk params

### Secondary (MEDIUM confidence)
- STATE.md: confirms migration not applied to Cloud SQL ("Cloud SQL Auth Proxy not running on dev VM; 2 tests unskipped and will run on VM" — Phase 02/03 decisions)

### Tertiary (LOW confidence)
- None in this research — all claims verified against live artifacts

---

## Metadata

**Confidence breakdown:**
- Bug root causes: HIGH — verified against live test output and code
- Migration gap: HIGH — verified against live DB
- Standard stack: HIGH — all packages confirmed installed
- Architecture patterns: HIGH — directly derived from existing `run_ingestion.py`
- Validation script design: MEDIUM — the DB assertion timing (sleep duration) is an estimate

**Research date:** 2026-05-15
**Valid until:** 2026-06-15 (stable phase — no external dependencies change)
