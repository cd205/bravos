# Phase 3: IBKR Connection - Context

**Gathered:** 2026-05-10
**Status:** Ready for planning

<domain>
## Phase Boundary

A persistent, self-healing IBKR connection thread is running that survives CLOSE-WAIT stalls and Gateway restarts, reconciles open positions, open orders, and account summary on startup, and supports both paper and live account configuration. No order execution logic — connection management and startup reconciliation only.

</domain>

<decisions>
## Implementation Decisions

### Connection Architecture
- **D-01:** Pattern B: combined class — single `IBApp(EWrapper, EClient)` class, not separate wrapper/client/app classes. Simpler, fewer files, sufficient for this project's scale.
- **D-02:** Module location: `bravos/broker/` package (new, alongside `bravos/ingestion/`). Files: `__init__.py`, `connection.py` (IBApp class), tests in `tests/test_broker.py`.
- **D-03:** Exposure pattern: Claude's discretion — pick the cleaner pattern (singleton vs dependency injection) based on what the planner recommends for Phase 4 compatibility.

### Reconnect Strategy
- **D-04:** Detection: both mechanisms active — heartbeat (reqCurrentTime every 60s) as primary detector; ibapi error codes (504 not connected, 1100 connectivity lost, 2110 connectivity restored) as immediate triggers. Most robust.
- **D-05:** Heartbeat timeout: 10 seconds — if no currentTime() callback within 10s of reqCurrentTime(), declare connection dead.
- **D-06:** Force-reconnect sequence: `client.disconnect()` → wait 5 seconds (CLOSE-WAIT drain) → create fresh connection. Matches opt-trade-vm4 pattern.
- **D-07:** Retry policy: 5 attempts with exponential backoff (5s, 10s, 20s, 40s, 80s). After 5 failures, log CRITICAL and continue retrying every 60s indefinitely — process never exits due to IBKR unavailability.

### Startup Reconciliation
- **D-08:** Mismatch handling: IBKR is NOT automatically authoritative — flag discrepancies between DB and IBKR, log each as WARNING, don't overwrite DB state. Operator reviews via logs.
- **D-09:** Reconciliation scope: positions (reqPositions) + open orders (reqOpenOrders) + account summary (reqAccountSummary for net liquidation). All three fetched before entering main loop.
- **D-10:** Storage: write snapshot to `broker_positions_snapshot` table (already in schema). One row per position per reconciliation run. DB-queryable and auditable.
- **D-11:** Reconciliation must complete before the ingestion schedule loop starts — no scrape cycle runs until IBKR connection confirmed and reconciliation done.

### Thread / Process Model
- **D-12:** Same process as ingestion daemon — IBKR connection runs as a background thread inside `scripts/run_ingestion.py`. No separate process, no IPC. Phase 4 order execution calls the connection directly in-process.
- **D-13:** IBKR thread starts at daemon startup, before the schedule loop — connect, reconcile, then start ingestion.
- **D-14:** Startup failure mode: if IBKR connection fails all 5 initial attempts, log CRITICAL, start the ingestion loop anyway (signals are scraped, parsed, and stored), keep retrying IBKR connection in the background indefinitely. No orders placed until connection is live. System never goes dark due to IBKR unavailability.

### Claude's Discretion
- Exact exposure pattern for IBApp instance (singleton module-level vs dependency injection) — pick what's cleanest for Phase 4
- reqCurrentTime heartbeat implementation details (threading.Timer vs schedule library vs dedicated thread)
- Error code handling granularity (which codes trigger immediate reconnect vs log-only)
- Test approach for connection logic (mock EWrapper vs integration test with real Gateway)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### IBKR API Patterns
- `.claude/skills/ibkr-connection/SKILL.md` — EWrapper/EClient architecture, Pattern B (combined class), threading model, reqPositions, reqOpenOrders, reqAccountSummary, reqCurrentTime, error codes (504, 1100, 2110), connection handshake

### Project configuration
- `bravos/config/settings.py` — `IBKR_HOST`, `IBKR_PAPER_PORT` (4002), `IBKR_LIVE_PORT` (4001), `IBKR_CLIENT_ID`, `TRADING_MODE`, `get_ibkr_port()` helper already implemented
- `bravos/config/secrets_config.py` — `get_secret()` for loading IBKR credentials from GCP Secret Manager

### Database schema
- `infra/schema.sql` — `broker_positions_snapshot` table (target for reconciliation writes); `signals` table for context
- `.claude/skills/postgres-patterns/SKILL.md` — psycopg2 patterns, connection handling

### Phase requirements
- `.planning/REQUIREMENTS.md` — IBKR-01 (heartbeat/reconnect), IBKR-02 (CLOSE-WAIT detection), IBKR-03 (startup reconciliation), IBKR-05 (paper/live config toggle)

### Existing daemon (integration point)
- `scripts/run_ingestion.py` — current daemon entry point; IBKR thread is added here at startup
- `bravos/ingestion/scraper.py` — existing Phase 2 pattern for daemon threads, SIGTERM handling

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bravos/config/settings.py`: `get_ibkr_port()` already returns the correct port based on `TRADING_MODE` — use directly in IBApp constructor
- `bravos/config/settings.py`: `IBKR_HOST`, `IBKR_CLIENT_ID` — connection params already defined
- `bravos/config/secrets_config.py`: `get_secret()` — use to load any IBKR secrets from GCP Secret Manager at startup
- `scripts/run_ingestion.py`: SIGTERM handler pattern — replicate for clean IBKR shutdown (disconnect before process exit)
- `tests/conftest.py`: `db_connection` fixture — reuse for broker reconciliation tests that write to `broker_positions_snapshot`

### Established Patterns
- Package structure: `bravos/<module>/__init__.py` + module files — replicate for `bravos/broker/`
- Retry pattern: 3 attempts → CRITICAL log (Phase 2 re-auth); this phase extends to 5 attempts + exponential backoff
- Wave 0 test stubs: write full test bodies inside `@pytest.mark.skip`, named by implementing plan — established in Phase 1 and Phase 2
- Secrets never in code — `get_secret()` or env vars only

### Integration Points
- `scripts/run_ingestion.py`: IBApp thread starts here, before `schedule.run_pending()` loop
- `bravos/broker/connection.py`: IBApp class — Phase 4 (order execution) will import and use this directly
- `infra/schema.sql`: `broker_positions_snapshot` table — reconciliation writes land here
- SIGTERM handler in `run_ingestion.py`: must call `ibapp.disconnect()` cleanly on shutdown

</code_context>

<specifics>
## Specific Ideas

- opt-trade-vm4's IBKR connection code is the reference — if researcher can read it, replicate the reconnect approach before diverging
- Heartbeat: `reqCurrentTime()` → `currentTime()` callback; store last-received timestamp; background thread checks every 60s and triggers reconnect if timestamp is stale by >10s
- CLOSE-WAIT detection: error codes 1100 (connectivity lost) and 504 (not connected) should trigger immediate reconnect attempt without waiting for the next heartbeat

</specifics>

<deferred>
## Deferred Ideas

- Periodic position reconciliation during the trading day (IBKR-04) — Phase 5 requirement, not Phase 3
- Account summary streaming for real-time P&L — Phase 7 dashboard concern
- Email notification on IBKR disconnect not auto-recovered — NOTF-02, Phase 7

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-ibkr-connection*
*Context gathered: 2026-05-10*
