---
plan: 04-03
phase: 04-risk-controls-and-order-execution
status: complete
completed_at: "2026-05-14"
tasks_total: 2
tasks_complete: 2
commits:
  - 2d5a38b
  - 0cfb9fd
key-files:
  created:
    - bravos/risk/gate.py
  modified:
    - tests/test_execution.py
---

# Plan 04-03 Summary: RiskGate Implementation

## What Was Built

**`bravos/risk/gate.py`** — Single synchronous risk gate for all order paths.

### RiskGate class

- `RiskGate.check(signal_id, db_conn, ibapp) → (bool, str)` — single entry point per D-02/D-03
- Four gates enforced in sequence (first failure wins):
  1. **Market hours** (EXEC-03): `_is_market_hours()` checks weekday + 09:30–16:00 ET using `stdlib zoneinfo`, NOT pytz
  2. **Max open positions** (RISK-01): counts `DISTINCT ticker FROM position_lots WHERE lot_closed_at IS NULL`, blocks open/add when `>= MAX_OPEN_POSITIONS`
  3. **Max allocation** (RISK-02): `abs(delta_weight) × WEIGHT_PCT_PER_UNIT > MAX_ALLOCATION_PCT` blocks
  4. **Circuit breaker** (RISK-03): latches `_circuit_tripped = True` when `daily_pnl < DAILY_LOSS_THRESHOLD`; stays blocked until `reset()` called
- `RiskGate.reset()` — clears latch for next trading day
- `_is_market_hours()` — module-level helper; patchable via `bravos.risk.gate.datetime`
- Every decision (pass and block) writes one row to `risk_gate_log` (RISK-04): parameterized INSERT, `db_conn.commit()` after write

### Tests (Task 2)

Removed `@pytest.mark.skip(reason="plan: 04-03")` from 8 tests in `tests/test_execution.py`:

| Test | Type | Result |
|------|------|--------|
| test_market_hours_gate_blocks | unit | PASS |
| test_market_hours_gate_passes | unit | PASS |
| test_gate_max_positions | unit | PASS |
| test_gate_max_allocation | unit | PASS |
| test_gate_circuit_breaker | unit | PASS |
| test_gate_circuit_none_pnl | unit | PASS |
| test_gate_log_pass | integration | deferred (needs live DB) |
| test_gate_log_block | integration | deferred (needs live DB) |

**6 unit tests pass. 7 plan 04-04 skip decorators untouched.**

Integration tests (log_pass, log_block) require the Cloud SQL Auth Proxy on 127.0.0.1:5432 and `risk_gate_log` table migration applied — deferred to bravos-vm1 per Phase 2/3 precedent.

## Decisions Made

- `import datetime` as module (not `from datetime import datetime`) so tests can patch `bravos.risk.gate.datetime`
- `_load_signal` handles both dict and tuple from `fetchone()` — unit tests inject dicts, live DB returns tuples
- Circuit breaker: `_daily_pnl is None` → gate passes (fail-open during startup before first reqPnL callback fires)
- No external dependencies added; `db_conn` and `ibapp` accepted as parameters

## Deviations

None. Implementation matches plan specification verbatim.

## Self-Check: PASSED

- `bravos/risk/gate.py` exists, 211 lines, `class RiskGate` present
- `from zoneinfo import ZoneInfo` — no pytz import
- All four settings constants imported from `bravos.config.settings`
- All SQL uses `%s` parameterized placeholders
- `INSERT INTO risk_gate_log` present
- `with db_conn.cursor() as cur:` used for all DB access
- 6 unit tests: `6 passed, 7 skipped` ✓
- Phase 3 broker tests: no regression
