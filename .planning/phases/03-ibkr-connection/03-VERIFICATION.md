---
phase: 03-ibkr-connection
verified: 2026-05-14T00:00:00Z
status: human_needed
score: 21/21 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Start daemon with IB Gateway running in paper mode: TRADING_MODE=paper python scripts/run_ingestion.py"
    expected: "Log sequence: 'Starting IBKR connection', 'IBKR connected', 'reqPositions complete', 'reqOpenOrders complete', 'reqAccountSummary complete', 'Wrote N position rows to broker_positions_snapshot', 'Reconciliation complete — DB not modified (D-08)', 'IBKR ready — heartbeat monitor started', 'Ingestion daemon started'"
    why_human: "Requires IB Gateway running on bravos-vm1 with paper account active on port 4002 and Cloud SQL Auth Proxy active"
  - test: "After daemon starts, wait 60 seconds and confirm no heartbeat timeout errors appear in logs"
    expected: "Daemon remains running with no ERROR or WARNING about heartbeat timeout. currentTime() is silently called — no log line expected."
    why_human: "Requires live IB Gateway connection and 60s observation window"
  - test: "Send SIGTERM: kill -TERM <pid>"
    expected: "Log sequence: 'Received signal 15 — initiating graceful shutdown', 'Stopping IBKR connection...', 'IBApp stopped', 'Ingestion daemon stopped'. Process exits with code 0."
    why_human: "Requires running daemon process and real IBKR connection on bravos-vm1"
  - test: "Start daemon with Gateway NOT running (D-14 path)"
    expected: "CRITICAL log: 'IBKR initial connect failed (mode=paper port=4002) — starting ingestion without IBKR (D-14)'. Daemon continues running into schedule loop without crashing."
    why_human: "Requires controlled environment where Gateway is down, or kill Gateway before starting daemon"
  - test: "After successful startup with Gateway, query broker_positions_snapshot: SELECT ticker, position, avg_cost, snapshot_at FROM broker_positions_snapshot ORDER BY snapshot_at DESC LIMIT 5;"
    expected: "Rows present (or empty table if paper account has no open positions — which is valid)"
    why_human: "Requires live DB connection and completed startup reconciliation on bravos-vm1"
---

# Phase 3: IBKR Connection Verification Report

**Phase Goal:** A persistent, self-healing IBKR connection thread is running that survives CLOSE-WAIT stalls and Gateway restarts, reconciles open positions and orders on startup, and supports both paper and live account configuration.
**Verified:** 2026-05-14
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | IBApp(EWrapper, EClient) class exists, importable, stores connection params without connecting | VERIFIED | `bravos/broker/connection.py` L49-97; `IBApp.__init__` stores `_host`, `_port`, `_client_id`, initializes `_connected = threading.Event()` (clear), does not call `connect()` |
| 2 | Module-level `ibapp` singleton is None at import | VERIFIED | `connection.py` L538: `ibapp: "IBApp | None" = None` |
| 3 | Heartbeat monitor fires reqCurrentTime every 60s via daemon thread named ibkr-heartbeat | VERIFIED | `start_heartbeat_monitor()` L236-249 starts daemon thread; `_heartbeat_loop()` L251-272 uses `_stop_event.wait(timeout=HEARTBEAT_INTERVAL)` (HEARTBEAT_INTERVAL=60) |
| 4 | Heartbeat timeout (10s no response) triggers reconnect | VERIFIED | `_heartbeat_loop()` L265-272: after `reqCurrentTime()` + `time.sleep(10)`, if `elapsed > HEARTBEAT_TIMEOUT` calls `_trigger_reconnect("heartbeat_timeout")` |
| 5 | CLOSE-WAIT detection: error codes 504 and 1100 trigger immediate reconnect | VERIFIED | `error()` L196-208: `_IMMEDIATE_RECONNECT_CODES = {504, 1100}` routes to `_trigger_reconnect()`; test_error_504_triggers_reconnect and test_error_1100_triggers_reconnect both PASS |
| 6 | Reconnect uses exponential backoff [5, 10, 20, 40, 80] then 60s forever with 5s CLOSE-WAIT drain | VERIFIED | `_RETRY_DELAYS = [5, 10, 20, 40, 80]` at L26; `_reconnect_loop()` L297-349: `time.sleep(5)` drain + `time.sleep(max(0, delay-5))` remaining; after 5 attempts logs CRITICAL and falls to 60s |
| 7 | Duplicate reconnect thread guard (no-op if _reconnecting=True) | VERIFIED | `_trigger_reconnect()` L276-295: acquires `_recon_lock`, checks `_reconnecting`, returns if True; test_trigger_reconnect_does_not_spawn_duplicate_thread PASS |
| 8 | On startup, reqPositions / reqAllOpenOrders / reqAccountSummary issued and results accumulated | VERIFIED | `run_startup_reconciliation()` L428-468: clears accumulators, issues all 3 requests, waits on 3 threading.Events |
| 9 | position() callback appends STK positions only; positionEnd() sets _positions_done | VERIFIED | L361-384; non-STK returns early; test_position_callback_appends_stk_positions and test_position_end_sets_event PASS |
| 10 | openOrder() appends to _open_orders; openOrderEnd() sets _orders_done | VERIFIED | L386-407; test_open_order_callback_appends_orders and test_open_order_end_sets_event PASS |
| 11 | accountSummary() stores tags; accountSummaryEnd() sets _summary_done | VERIFIED | L409-424; test_account_summary_end_sets_event PASS |
| 12 | _write_position_snapshot inserts into broker_positions_snapshot, never writes market_value, commits | VERIFIED | L474-489; test_position_snapshot_written PASS (with DB via conftest fixture) |
| 13 | _reconcile_against_db logs RECONCILE MISMATCH WARNING, never writes to position_lots (D-08) | VERIFIED | L492-530; test_reconcile_mismatch_logs_warning PASS; test_reconcile_no_db_write_on_mismatch PASS |
| 14 | IBApp started before schedule loop in run_ingestion.py | VERIFIED | `run_ingestion.py` L107-142: full IBKR startup block before `_scraper = BravosScraper()` at L145 |
| 15 | run_startup_reconciliation called when connection succeeds | VERIFIED | `run_ingestion.py` L127: `_ibapp.run_startup_reconciliation(_db_conn, timeout=30)` inside `if ibkr_ok:` block |
| 16 | start_heartbeat_monitor called after successful reconciliation | VERIFIED | `run_ingestion.py` L131: `_ibapp.start_heartbeat_monitor()` called after reconciliation block |
| 17 | start_background_reconnect called when initial connection fails (D-14) | VERIFIED | `run_ingestion.py` L141: `_ibapp.start_background_reconnect()` in else branch |
| 18 | broker_module.ibapp.stop() called on SIGTERM shutdown | VERIFIED | `run_ingestion.py` L162-164: `if broker_module.ibapp is not None: broker_module.ibapp.stop()` before `_scraper.shutdown()` |
| 19 | TRADING_MODE=live produces port 4001; TRADING_MODE=paper produces port 4002 | VERIFIED | `settings.py` L39-40: `get_ibkr_port()` returns `IBKR_LIVE_PORT (4001)` if TRADING_MODE=="live" else `IBKR_PAPER_PORT (4002)`; run_ingestion.py calls `settings.get_ibkr_port()` — no hardcoded port |
| 20 | Informational error codes (2104, 2106, etc.) do not trigger reconnect | VERIFIED | `_IGNORE_CODES = {2104, 2106, 2119, 2158, 2110}`; test_error_2104_is_ignored PASS |
| 21 | Tests: 15 pass, 8 skipped (03-1 Wave 0 stubs intentionally retained), 0 failures | VERIFIED | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_broker.py`: 15 passed, 8 skipped, 0 failed |

**Score:** 21/21 truths verified

### Note on Skipped 03-1 Tests

The 8 skipped tests (marked `reason="plan: 03-1"`) are Wave 0 stubs intentionally left skipped per the plan's explicit language: "unskip them first to verify, then re-skip — or leave skip and verify manually against the test body logic." All 8 were manually verified against the implementation and pass. They were left skipped while 03-2 and 03-3 tests were permanently unskipped (per plan decisions). This is intentional — not a gap.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|---------|--------|---------|
| `bravos/broker/__init__.py` | Package init | VERIFIED | Exists, importable |
| `bravos/broker/connection.py` | IBApp class + helpers | VERIFIED | 538 lines; IBApp(EWrapper, EClient) with all required methods; module-level helpers `_write_position_snapshot`, `_reconcile_against_db`; singleton `ibapp = None` |
| `tests/test_broker.py` | 23 tests (15 pass, 8 skip) | VERIFIED | 23 tests collected; 15 pass, 8 skipped (03-1 stubs retained intentionally) |
| `scripts/run_ingestion.py` | IBApp wired into daemon | VERIFIED | Full IBKR startup block at L107-142; imports at L39-41; `_get_db_connection()` helper at L57-68; shutdown at L162-164 |
| `bravos/config/settings.py` | `get_ibkr_port()`, port constants | VERIFIED | `IBKR_PAPER_PORT=4002`, `IBKR_LIVE_PORT=4001`, `get_ibkr_port()` returns correct value based on TRADING_MODE |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `run_ingestion.py` | `IBApp.connect_and_run()` | Direct call at L122 | WIRED | `ibkr_ok = _ibapp.connect_and_run(timeout=30)` |
| `run_ingestion.py` | `IBApp.run_startup_reconciliation()` | Call at L127 inside `if ibkr_ok:` | WIRED | Receives fresh psycopg2 connection from `_get_db_connection()` |
| `run_ingestion.py` | `IBApp.start_heartbeat_monitor()` | Call at L131 after reconciliation | WIRED | Called after `run_startup_reconciliation()` completes |
| `run_ingestion.py` | `IBApp.start_background_reconnect()` | Call at L141 in else branch | WIRED | D-14 path: initial connect failure |
| `run_ingestion.py` | `broker_module.ibapp.stop()` | Call at L164 in cleanup block | WIRED | Guard: `if broker_module.ibapp is not None:` |
| `IBApp.error()` | `IBApp._trigger_reconnect()` | Code set lookup at L200-208 | WIRED | `_IMMEDIATE_RECONNECT_CODES` routes 504/1100 to reconnect |
| `IBApp._heartbeat_loop()` | `IBApp._trigger_reconnect()` | Call at L272 | WIRED | After timeout check: `if elapsed > HEARTBEAT_TIMEOUT:` |
| `IBApp._trigger_reconnect()` | `IBApp._reconnect_loop()` | daemon thread at L289-295 | WIRED | Lock-guarded; thread named `ibkr-reconnect` |
| `IBApp.run_startup_reconciliation()` | `_write_position_snapshot()` | Call at L463 | WIRED | Module-level helper receives `db_conn` and `self._positions` |
| `IBApp.run_startup_reconciliation()` | `_reconcile_against_db()` | Call at L466 | WIRED | Receives `db_conn`, `self._positions`, `self._open_orders` |
| `settings.get_ibkr_port()` | `TRADING_MODE` env var | L39-40 in settings.py | WIRED | `run_ingestion.py` calls `settings.get_ibkr_port()` — no hardcoded port |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|-------------------|--------|
| `IBApp._positions` | Populated by `position()` callback | IBKR `reqPositions()` API callback | Yes — IBKR delivers position data | FLOWING |
| `IBApp._open_orders` | Populated by `openOrder()` callback | IBKR `reqAllOpenOrders()` API callback | Yes — IBKR delivers open order data | FLOWING |
| `IBApp._account_summary` | Populated by `accountSummary()` callback | IBKR `reqAccountSummary()` API callback | Yes — IBKR delivers account tags | FLOWING |
| `broker_positions_snapshot` table | Written by `_write_position_snapshot()` | `self._positions` list from IBKR | Real INSERT from real positions list | FLOWING |
| `_connected` Event | Set by `nextValidId()` callback | IBKR Gateway handshake | Real — fires only on successful TCP connect + auth | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| IBApp instantiation returns False from is_connected() | `python -c "from bravos.broker.connection import IBApp; app = IBApp('127.0.0.1', 4002, 1); print(app.is_connected())"` | `False` | PASS |
| nextValidId sets connected + stores order_id | Direct call: `app.nextValidId(42)` | connected=True, next_order_id=42, heartbeat>0 | PASS |
| stop() sets _stop_event, clears _connected | Direct call after nextValidId | _stop_event=True, connected=False | PASS |
| error(504) triggers reconnect | `app.error(reqId=-1, errorCode=504, ...)` with patched `_trigger_reconnect` | reconnect_calls=[error_504] | PASS |
| error(1100) triggers reconnect | `app.error(reqId=-1, errorCode=1100, ...)` | reconnect_calls=[error_1100] | PASS |
| error(2104) does NOT trigger reconnect | `app.error(reqId=-1, errorCode=2104, ...)` | reconnect_calls=[] | PASS |
| Duplicate reconnect guard | Set `_reconnecting=True`, call `_trigger_reconnect()` with patched `Thread.start` | 0 threads started | PASS |
| Position callbacks: STK added, OPT ignored | `app.position()` with secType=STK then OPT | 1 position in list | PASS |
| positionEnd() sets _positions_done | Direct call | event.is_set()=True | PASS |
| openOrderEnd() sets _orders_done | Direct call | event.is_set()=True | PASS |
| accountSummaryEnd() sets _summary_done | Direct call | event.is_set()=True | PASS |
| TRADING_MODE=live port | `get_ibkr_port()` with TRADING_MODE=live | 4001 | PASS |
| TRADING_MODE=paper port | `get_ibkr_port()` with TRADING_MODE=paper | 4002 | PASS |
| Full test suite | `python -m pytest tests/test_broker.py` | 15 passed, 8 skipped, 0 failed | PASS |
| Daemon startup with live Gateway | Requires bravos-vm1 + IB Gateway | Not runnable here | SKIP (human needed) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| IBKR-01 | 03-02, 03-04 | Persistent connection with 60s heartbeat; no operator intervention on reconnect | SATISFIED | `HEARTBEAT_INTERVAL=60`; `_heartbeat_loop()` sends `reqCurrentTime` every 60s; `_reconnect_loop()` auto-reconnects without human action |
| IBKR-02 | 03-02, 03-04 | CLOSE-WAIT detection + force reconnect without operator intervention | SATISFIED | `_IMMEDIATE_RECONNECT_CODES={504,1100}`; 5s CLOSE-WAIT drain in `_reconnect_loop()`; exponential backoff then 60s forever |
| IBKR-03 | 03-03, 03-04 | Startup reconciliation before scrape/execute loop | SATISFIED | `run_startup_reconciliation()` fully implemented; called in `run_ingestion.py` before `BravosScraper()` instantiation |
| IBKR-05 | 03-01, 03-04 | Paper (4002) / live (4001) switching via config toggle | SATISFIED | `get_ibkr_port()` in settings.py; `run_ingestion.py` calls `settings.get_ibkr_port()` — no hardcoded port anywhere |

All 4 required IDs (IBKR-01, IBKR-02, IBKR-03, IBKR-05) accounted for and satisfied.

### Anti-Patterns Found

No blockers or warnings found.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | No TODOs, NOTIMPLEMENTEDERRORs, placeholders, or hollow stubs found | — | — |

All `NotImplementedError` stubs from Plan 03-1 were replaced by Plans 03-2 and 03-3 as designed. Grep confirms zero remaining occurrences.

### Human Verification Required

#### 1. Live Gateway Startup Sequence (bravos-vm1)

**Test:** Start daemon with IB Gateway running in paper mode on bravos-vm1:
```bash
TRADING_MODE=paper python scripts/run_ingestion.py
```
**Expected:** Log sequence in order:
```
Starting IBKR connection — mode=paper host=127.0.0.1 port=4002 client_id=1
IBKR connected — ...
IBKR connected — running startup reconciliation
reqPositions complete — N positions received
reqOpenOrders complete — N open orders received
reqAccountSummary complete — N tags received
Wrote N position rows to broker_positions_snapshot
Reconciliation complete — DB not modified (D-08)
IBKR ready — heartbeat monitor started
Ingestion daemon started — polling every 300s
```
**Why human:** Requires IB Gateway running on bravos-vm1 paper account (port 4002) + Cloud SQL Auth Proxy active.

#### 2. Heartbeat Stays Alive (60s observation)

**Test:** After successful startup, observe logs for 60+ seconds.
**Expected:** No ERROR or WARNING about heartbeat timeout. Daemon remains running.
**Why human:** Requires live IB Gateway connection and timed observation window.

#### 3. SIGTERM Clean Shutdown

**Test:** In a second terminal: `kill -TERM <pid>`
**Expected:**
```
Received signal 15 — initiating graceful shutdown
Stopping IBKR connection...
IBApp stopped (clientId=1)
Ingestion daemon stopped
```
Process exits with code 0.
**Why human:** Requires running daemon process on bravos-vm1.

#### 4. D-14 Path — Initial Connect Failure

**Test:** Start daemon with Gateway NOT running.
**Expected:**
```
IBKR initial connect failed (mode=paper port=4002) — starting ingestion without IBKR (D-14). Orders will not be placed until connection is established.
```
Daemon continues into schedule loop — does NOT crash.
**Why human:** Requires controlled environment with Gateway intentionally down.

#### 5. broker_positions_snapshot Table Populated

**Test:** After successful startup with Gateway:
```sql
SELECT ticker, position, avg_cost, snapshot_at FROM broker_positions_snapshot ORDER BY snapshot_at DESC LIMIT 5;
```
**Expected:** Rows present (empty is valid if paper account has no open positions — reconciliation must have run regardless).
**Why human:** Requires live DB connection and completed startup reconciliation on bravos-vm1.

### Gaps Summary

No gaps found. All 21 must-haves are verified against the actual codebase. Human verification items are integration tests that require IB Gateway and are explicitly noted in Plan 03-04 Wave 2 as deferred to bravos-vm1.

---

_Verified: 2026-05-14_
_Verifier: Claude (gsd-verifier)_
