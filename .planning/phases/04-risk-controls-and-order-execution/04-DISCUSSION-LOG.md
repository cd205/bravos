# Phase 4: Risk Controls and Order Execution - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the discussion.

**Date:** 2026-05-14
**Phase:** 04-risk-controls-and-order-execution
**Mode:** discuss
**Areas discussed:** Current price for order sizing, Order state transitions in DB, Signal-to-order routing

---

## Areas Selected

User was presented with 4 gray areas. Selected 3 for discussion:
- Current price for order sizing
- Order state transitions in DB
- Signal-to-order routing

(RiskGate structure was not selected — Claude captured reasonable defaults from REQUIREMENTS.md and the existing code patterns.)

---

## Discussion Log

### Current price for order sizing

**Options presented:**
- reqMktData with delayed fallback — call reqMarketDataType(3) first, wait 5s for LAST/CLOSE tick, fall back to signal reference_price
- Signal's reference_price only — use parsed price as-is, no IBKR call
- reqHistoricalData (last bar close) — heavier, more reliable on accounts without live data

**User selected:** reqMktData with delayed fallback (recommended)

**Decision captured:** D-05, D-06 — delayed market data primary, reference_price fallback after 5s timeout

---

### Order state transitions in DB

**Options presented:**
- PENDING_SUBMISSION → SUBMITTED + REJECTED — write before submit, capture orderStatus callback for submitted/rejected states; leave FILLED/PARTIAL to Phase 5
- PENDING_SUBMISSION only — leave all status tracking to Phase 5

**User selected:** PENDING_SUBMISSION → SUBMITTED + REJECTED (recommended)

**Decision captured:** D-08, D-09 — Phase 4 writes PENDING_SUBMISSION and SUBMITTED/REJECTED; FILLED/PARTIAL left for Phase 5

---

### Signal-to-order routing

**Options presented:**
- New bravos/execution/ module, called from scraper — executor.py with execute_signal(); scraper calls it after storing the signal
- Inline in run_ingestion.py — execution logic in daemon entry point
- New bravos/execution/ module, called from run_ingestion.py — executor.py called from daemon orchestrator rather than scraper

**User selected:** New bravos/execution/ module, called from scraper (recommended)

**Decision captured:** D-11, D-12 — bravos/execution/executor.py, scraper calls execute_signal() after high-confidence signal is stored

---

## Claude's Discretion Items

RiskGate structure was not discussed interactively — Claude captured the structure from REQUIREMENTS.md (RISK-01 through RISK-04) and the existing Phase 3 singleton pattern:
- RiskGate as a class in bravos/risk/gate.py (stateful for daily loss tracking)
- Single check() entry point per RISK-04 requirement
- Three controls in sequence: market hours, max positions, max allocation; daily loss as fourth gate

---

## Deferred Ideas

- Partial fill handling — Phase 5
- FIFO lot assignment — Phase 5
- Email alert on circuit breaker — Phase 7
- Slippage tracking — v2
