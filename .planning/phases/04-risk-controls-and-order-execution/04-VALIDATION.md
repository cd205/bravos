# Phase 04: Risk Controls and Order Execution — Validation Strategy

**Phase:** 04
**Phase slug:** risk-controls-and-order-execution
**Date:** 2026-05-14
**Source:** Extracted from 04-RESEARCH.md § Validation Architecture

---

## Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | `pytest.ini` (exists at repo root) |
| Quick run command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/test_execution.py -x -q` |
| Full suite command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -q` |

---

## Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | Wave |
|--------|----------|-----------|-------------------|------|
| EXEC-01 | Quantity formula with known inputs | unit | `pytest tests/test_execution.py::test_quantity_formula -x` | Wave 0 stub |
| EXEC-01 | Quantity=0 blocked | unit | `pytest tests/test_execution.py::test_quantity_zero_skipped -x` | Wave 0 stub |
| EXEC-02 | BUY order built for open action | unit | `pytest tests/test_execution.py::test_build_order_buy -x` | Wave 0 stub |
| EXEC-02 | SELL order built for close action | unit | `pytest tests/test_execution.py::test_build_order_sell -x` | Wave 0 stub |
| EXEC-03 | Market hours gate blocks outside hours | unit | `pytest tests/test_execution.py::test_market_hours_gate_blocks -x` | Wave 0 stub |
| EXEC-03 | Market hours gate passes during hours | unit | `pytest tests/test_execution.py::test_market_hours_gate_passes -x` | Wave 0 stub |
| EXEC-04 | DB row written PENDING_SUBMISSION before placeOrder | unit (mock) | `pytest tests/test_execution.py::test_order_db_write_pending -x` | Wave 0 stub |
| EXEC-04 | DB status updated to SUBMITTED after callback | unit (mock) | `pytest tests/test_execution.py::test_order_status_submitted -x` | Wave 0 stub |
| EXEC-04 | DB status updated to REJECTED after Inactive callback | unit (mock) | `pytest tests/test_execution.py::test_order_status_rejected -x` | Wave 0 stub |
| RISK-01 | Gate blocks when open positions ≥ max | unit (mock DB) | `pytest tests/test_execution.py::test_gate_max_positions -x` | Wave 0 stub |
| RISK-02 | Gate blocks when allocation exceeds cap | unit | `pytest tests/test_execution.py::test_gate_max_allocation -x` | Wave 0 stub |
| RISK-03 | Gate blocks when daily_pnl < threshold | unit | `pytest tests/test_execution.py::test_gate_circuit_breaker -x` | Wave 0 stub |
| RISK-03 | Gate passes when daily_pnl is None (not yet received) | unit | `pytest tests/test_execution.py::test_gate_circuit_none_pnl -x` | Wave 0 stub |
| RISK-04 | risk_gate_log row written for pass decision | integration | `pytest tests/test_execution.py::test_gate_log_pass -x` | Wave 0 stub |
| RISK-04 | risk_gate_log row written for block decision | integration | `pytest tests/test_execution.py::test_gate_log_block -x` | Wave 0 stub |

---

## Sampling Rate

- **Per task commit:** `pytest tests/test_execution.py -x -q`
- **Per wave merge:** `pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

---

## Wave 0 Gaps (must exist before integration tests can run)

- [ ] `tests/test_execution.py` — all 15 tests above (unit + integration stubs with `@pytest.mark.skip`)
- [ ] `bravos/execution/__init__.py` — empty package init
- [ ] `bravos/risk/__init__.py` — empty package init
- [ ] `infra/migrate_phase4.sql` — risk_gate_log DDL

---

## Assumptions and Known Gaps

| # | Assumption | Risk if Wrong |
|---|-----------|---------------|
| A1 | Default WEIGHT_PCT_PER_UNIT=0.05, MAX_OPEN_POSITIONS=20 are reasonable starting values | Wrong sizing; operator must override via env var before live trading |
| A2 | orderStatus fires within 3s for paper account MKT order | DB stuck at PENDING_SUBMISSION |
| A3 | RiskGate._circuit_tripped latches for the process lifetime; daily reset requires daemon restart or explicit `gate.reset()` call at market open | Circuit breaker stays tripped across trading days — documented operational limitation |
| A4 | DB status strings are UPPERCASE (PENDING_SUBMISSION, SUBMITTED, REJECTED) | Inconsistency with schema default 'pending' (lowercase) — codified in plans |
| A5 | close action with 0 open lots is blocked before placeOrder | IBKR rejects SELL 0 shares with error 201 |
