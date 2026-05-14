# Phase 4: Risk Controls and Order Execution - Context

**Gathered:** 2026-05-14
**Status:** Ready for planning

<domain>
## Phase Boundary

The complete signal-to-order path is working: a parsed signal passes through a single synchronous risk gate (all controls enforced), order size is calculated from live account value and current market price, and a market order is submitted to IBKR with its state written to the database. Fill capture, partial-fill handling, and FIFO lot assignment are Phase 5 scope — not this phase.

</domain>

<decisions>
## Implementation Decisions

### RiskGate Structure
- **D-01:** Module location: `bravos/risk/gate.py` — new package alongside `bravos/broker/` and `bravos/ingestion/`. RiskGate is a class (not a stateless function) because it must hold intra-day state for the daily loss circuit breaker.
- **D-02:** Single entry point: `RiskGate.check(signal_id, db_conn, ibapp) -> (bool, str)` — returns (pass, reason). Every order path calls this and only this. No bypass.
- **D-03:** Gate enforces three controls in sequence: market hours check (09:30–16:00 ET), max open positions, max allocation per trade. Daily loss circuit breaker check added as fourth gate — blocks new entries if realized + unrealized P&L falls below threshold.
- **D-04:** Every gate decision (pass or block) is logged to the database with signal_id, computed values, and reason — satisfies RISK-04.

### Current Price for Order Sizing
- **D-05:** Primary: `reqMarketDataType(3)` (delayed data) then `reqMktData`. Wait up to 5 seconds for a LAST or CLOSE tick to arrive via `tickPrice` callback.
- **D-06:** Fallback: if no tick arrives within 5s, use the signal's `reference_price` from the database. Log a WARNING indicating fallback was used (price may be stale).
- **D-07:** Price fetching lives in the executor module (`bravos/execution/executor.py`), not in IBApp — IBApp already has reqMktData callbacks in the SKILL.md pattern; they'll be added here.

### Order State Transitions in DB
- **D-08:** Phase 4 responsibility: write `PENDING_SUBMISSION` before `placeOrder()` is called. Update to `SUBMITTED` (or `PreSubmitted`) when the `orderStatus` callback fires with a submitted state. Capture `REJECTED` from `orderStatus` callback.
- **D-09:** Leave `FILLED` and `PARTIAL` status transitions to Phase 5 (fill capture). Phase 4 completeness check: order reaches `SUBMITTED` in the DB.
- **D-10:** The `ibkr_order_id` field in the orders table is populated with the `next_order_id` from `ibapp.next_order_id` at submission time; IBApp increments it after each use (standard ibapi pattern).

### Signal-to-Order Routing
- **D-11:** New module: `bravos/execution/executor.py` with `execute_signal(signal_id, db_conn) -> None`. This is the single entry point for the order path.
- **D-12:** The scraper calls `execute_signal()` after storing a high-confidence signal. Scraper stays unchanged in structure — it already stores the signal; executor is called after the store.
- **D-13:** `execute_signal()` only processes signals where `confidence` is `'high'` and `action_type` is in `{'open', 'add', 'partial_close', 'close'}`. Low-confidence and unrecognized action types are skipped with an INFO log (not an error — they were already flagged at parse time).
- **D-14:** `execute_signal()` imports `ibapp` from `bravos.broker.connection` — the module-level singleton set at daemon startup (D-03 from Phase 3). If `ibapp` is None or not connected, execution is skipped with a WARNING log.

### Claude's Discretion
- Exact `tickType` values checked in `tickPrice` callback for price extraction (LAST=4, CLOSE=9 are standard)
- Threading model for the 5s price-wait (threading.Event vs polling loop)
- Whether `reqMktDataCancel` is called after price is received
- Schema migration approach for a `risk_gate_log` table if needed for RISK-04 logging (or reuse the existing orders.status column + a log comment)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### IBKR Order Placement
- `.claude/skills/ibkr-connection/SKILL.md` — placeOrder() pattern, Order object construction, reqMktData/tickPrice pattern, reqMarketDataType(3) for delayed data, next_order_id handling, orderStatus callback, error handling

### Existing connection layer
- `bravos/broker/connection.py` — IBApp class (Phase 3); `ibapp` module-level singleton; `next_order_id` field; existing EWrapper callbacks; `is_connected()`
- `bravos/config/settings.py` — `TRADING_MODE`, `get_ibkr_port()`, market hours constants (to be added)

### Database schema
- `infra/schema.sql` — `orders` table (signal_id, ibkr_order_id, ticker, action, quantity, status, submitted_at); `position_lots` table (for open position count); `signals` table (ticker, action_type, weight_from, weight_to, reference_price, confidence)
- `.claude/skills/postgres-patterns/SKILL.md` — psycopg2 patterns, connection handling

### Phase requirements
- `.planning/REQUIREMENTS.md` — EXEC-01 (order sizing formula), EXEC-02 (market orders), EXEC-03 (market hours gate), EXEC-04 (order lifecycle via callbacks), RISK-01 (max open positions), RISK-02 (max allocation cap), RISK-03 (daily loss circuit breaker), RISK-04 (gate decision logging)

### Parser (signal source)
- `bravos/ingestion/parser.py` — ParsedSignal fields; confidence scoring; action_type values (`open`, `add`, `partial_close`, `close`)
- `bravos/ingestion/scraper.py` — where execute_signal() will be called from (after signal store)

### Daemon entry point
- `scripts/run_ingestion.py` — `ibapp` singleton setup; process model; where `bravos/execution/executor.py` integrates

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bravos/broker/connection.py`: `ibapp` singleton — import and use directly in executor; `ibapp.next_order_id` for order IDs; `ibapp.is_connected()` for gate check
- `bravos/broker/connection.py`: `IBApp._account_summary` dict already populated at startup with `NetLiquidation` — reuse for order sizing (no second reqAccountSummary needed at order time, or re-fetch for freshness)
- `bravos/config/settings.py`: `TRADING_MODE` — executor must check this; `get_ibkr_port()` — already done by IBApp but useful for context
- `tests/conftest.py`: `db_connection` fixture — reuse for executor integration tests

### Established Patterns
- Package structure: `bravos/<module>/__init__.py` + module files — replicate for `bravos/execution/` and `bravos/risk/`
- Wave 0 test stubs: full test bodies inside `@pytest.mark.skip(reason="implementing: <plan-name>")` — established in Phases 1–3
- Singleton pattern: `ibapp: IBApp | None = None` set at daemon startup — replicate for any module-level state in execution/risk if needed
- DB write-before-submit pattern (D-08): write DB row with status, then call IBKR; mirror the pattern from `_write_position_snapshot`

### Integration Points
- `bravos/ingestion/scraper.py`: add `execute_signal(signal_id, db_conn)` call after signal is stored — the only change to Phase 2 code
- `bravos/broker/connection.py`: Phase 4 adds `tickPrice` and `orderStatus` callbacks to IBApp; do NOT change existing Phase 3 callbacks
- `infra/schema.sql`: may need migration to add `risk_gate_log` table for RISK-04; or use a `parse_method`-style column addition to `orders`

</code_context>

<specifics>
## Specific Ideas

- Order sizing formula (EXEC-01): `quantity = abs(new_weight - old_weight) × weight_pct_per_unit × net_liquidation / current_price`. `weight_pct_per_unit` comes from config (e.g. 0.05 for 5% per weight unit). Integer shares — round down.
- Market hours gate: use `pytz` with `US/Eastern` timezone. NYSE regular hours: 09:30–16:00 ET, Monday–Friday. This is the sole market hours check — block outside this window with no retry.
- The `ibapp._account_summary["NetLiquidation"]` value is a string — must be cast to float before use in sizing math.
- Phase 4 uses market orders (MKT, DAY) only — no limit orders per REQUIREMENTS.md Out of Scope.

</specifics>

<deferred>
## Deferred Ideas

- Partial fill handling and order FILLED/PARTIAL status transitions — Phase 5
- Periodic position reconciliation (IBKR-04) — Phase 5
- FIFO lot assignment — Phase 5
- Email alert when circuit breaker triggers (NOTF-01) — Phase 7
- Execution quality / slippage tracking (EXEC-V2-01) — v2

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-risk-controls-and-order-execution*
*Context gathered: 2026-05-14*
