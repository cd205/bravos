# Roadmap: Bravos Trading System

## Overview

Eight phases build the system bottom-up along its critical dependency chain: first the GCP VM is provisioned and configured to mirror opt-trade-vm4 (IB Gateway, Python, Chrome, PostgreSQL, secrets), then signal ingestion, then IBKR connection reliability, then the risk gate and order engine, then fill capture and position reconciliation, then end-to-end paper validation, then the dashboard and alerting layer, and finally live deployment hardening. No phase connects to execution before its upstream dependencies are solid.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Infrastructure Setup** - GCP VM provisioning (bravos_vm1), IB Gateway install, Python env, Chrome headless, PostgreSQL install + schema, secrets management
- [ ] **Phase 2: Signal Ingestion** - Selenium scraper, alert parser, and full audit trail stored in PostgreSQL
- [ ] **Phase 3: IBKR Connection** - Persistent IB Gateway connection with heartbeat, reconnect, and startup reconciliation
- [ ] **Phase 4: Risk Controls and Order Execution** - Single synchronous risk gate, order size calculator, and market-order submission
- [ ] **Phase 5: Fill Capture and Position Reconciliation** - Execution callbacks, partial-fill handling, FIFO lots, and periodic broker reconciliation
- [ ] **Phase 6: Paper Trading Validation** - End-to-end pipeline validation on paper account before any live orders
- [ ] **Phase 7: Dashboard and Notifications** - FastAPI dashboard (signals, positions, health) and email alerts on critical events
- [ ] **Phase 8: Live Deployment** - Systemd service hardening, production secrets, and live account activation

## Phase Details

### Phase 1: Infrastructure Setup
**Goal**: bravos_vm1 is a fully configured GCP VM mirroring opt-trade-vm4 — IB Gateway installed and startable, Python environment ready, Chromium running headless, PostgreSQL installed with the trading schema, and all secrets loaded from GCP Secret Manager — so that every subsequent phase has a working execution surface
**Depends on**: Nothing (first phase)
**Requirements**: DEPL-01, DEPL-03, DEPL-04, DEPL-05
**Success Criteria** (what must be TRUE):
  1. bravos_vm1 is running on GCP (Linux), SSH-accessible, and IB Gateway can be started and accepts a connection on its configured port
  2. A Python virtual environment exists on the VM with all required packages installable, matching the setup pattern of opt-trade-vm4
  3. Chromium runs in headless mode on the VM with the anti-detection flags required for Selenium scraping — a test script can open a browser session without errors
  4. PostgreSQL is installed and running; the trading schema (signals, orders, position_lots, executions, broker_positions_snapshot) is applied and all tables are queryable
  5. All secrets (Bravos credentials, IBKR configuration) are stored in GCP Secret Manager and readable from the VM via service account — no secrets exist in any file on disk or in version control
**Plans**: TBD

### Phase 2: Signal Ingestion
**Goal**: The system reliably scrapes bravosresearch.com, parses every Trade Alert into a structured signal with a confidence score, and stores the full audit trail in PostgreSQL — all without connecting to IBKR or executing any orders
**Depends on**: Phase 1
**Requirements**: INGST-01, INGST-02, INGST-03, INGST-04, INGST-05, INGST-06, INGST-07, AUDIT-01, AUDIT-02, AUDIT-03, AUDIT-04, AUDIT-05, AUDIT-06
**Success Criteria** (what must be TRUE):
  1. The scraper logs into bravosresearch.com, polls every 5 minutes, and stores only new Trade Alert posts — a post seen once is never stored again regardless of site edits
  2. Each stored signal record contains raw HTML, parsed fields (ticker, action type, weight_from, weight_to, reference price), and a confidence score
  3. Low-confidence parses are marked with a flag and are not forwarded to any execution path
  4. If the session expires mid-day, the system detects it and re-authenticates rather than silently returning zero new posts
  5. Every signal can be traced end-to-end through the database: raw post → parsed fields → parse status, with all records immutable (append-only)
**Plans**: 5 plans
Plans:
- [x] 02-01-PLAN.md — Schema migration + package scaffold + Wave 0 test stubs
- [x] 02-02-PLAN.md — Parser module (TDD: regex extraction, action keywords, confidence scoring)
- [x] 02-03-PLAN.md — Scraper module (BravosScraper class + selector discovery)
- [x] 02-04-PLAN.md — DB integration tests (dedup, raw_html, audit fields)
- [ ] 02-05-PLAN.md — Daemon entry point + end-to-end integration validation

### Phase 3: IBKR Connection
**Goal**: A persistent, self-healing IBKR connection thread is running that survives CLOSE-WAIT stalls and Gateway restarts, reconciles open positions and orders on startup, and supports both paper and live account configuration
**Depends on**: Phase 1
**Requirements**: IBKR-01, IBKR-02, IBKR-03, IBKR-05
**Success Criteria** (what must be TRUE):
  1. The IBKR daemon thread connects to IB Gateway on startup, issues a heartbeat every 60 seconds, and automatically force-reconnects when the heartbeat times out — without operator intervention
  2. On startup, the system fetches open positions and open orders from IBKR and reconciles them with the database before entering the main loop
  3. Switching between paper account (port 4002) and live account (port 4001) requires only a configuration change — no code changes
  4. All credentials (Bravos login, IBKR configuration) are loaded from environment variables or GCP Secret Manager — no secrets appear in code or committed files
**Plans**: TBD

### Phase 4: Risk Controls and Order Execution
**Goal**: The complete signal-to-order path is working: a parsed signal passes through a single synchronous risk gate (all controls enforced), order size is calculated from live account value, and a market order is submitted to IBKR with its state written to the database before submission
**Depends on**: Phase 2, Phase 3
**Requirements**: EXEC-01, EXEC-02, EXEC-03, EXEC-04, RISK-01, RISK-02, RISK-03, RISK-04
**Success Criteria** (what must be TRUE):
  1. Every order path — regardless of signal type — passes through a single RiskGate.check() call; no bypass exists; every gate decision is logged with the signal ID, computed values, and pass/block reason
  2. Orders are blocked when any risk limit is breached: max open positions exceeded, trade allocation cap exceeded, or daily loss circuit breaker triggered
  3. No order is submitted outside NYSE regular trading hours (09:30–16:00 ET)
  4. Order share quantity is calculated as abs(new_weight - old_weight) × weight_pct × account_net_liquidation / current_price, using account value fetched from IBKR at execution time
  5. Order records are written to the database with status PENDING_SUBMISSION before placeOrder() is called; order status transitions are tracked through ibapi callbacks
**Plans**: TBD

### Phase 5: Fill Capture and Position Reconciliation
**Goal**: The system correctly captures every fill (including partial fills), maintains accurate per-lot position state with FIFO assignment, and periodically reconciles internal state against IBKR's authoritative position data
**Depends on**: Phase 4
**Requirements**: EXEC-05, EXEC-06, IBKR-04, POS-01, POS-02, POS-03
**Success Criteria** (what must be TRUE):
  1. Fill price and fill quantity are captured from ibapi execution callbacks and stored as per-execution records; an order is only marked FILLED when total filled quantity matches order quantity
  2. Partial fills accumulate correctly — position state updates incrementally and reflects actual filled quantity at all times
  3. When a position with multiple open lots (from scale-ins) is partially or fully closed, FIFO lot assignment is applied and the remaining open quantity is preserved correctly
  4. The system runs reqPositions() on a periodic schedule; any discrepancy between internal position state and IBKR's authoritative data is logged and flagged for review
**Plans**: TBD

### Phase 6: Paper Trading Validation
**Goal**: The full pipeline (scrape → parse → risk → order → fill → reconcile) has been exercised end-to-end on the paper account with real Bravos signals, and no critical failures remain unresolved
**Depends on**: Phase 5
**Requirements**: (validation phase — no new requirements; covers IBKR-05 paper mode toggle already delivered in Phase 3)
**Success Criteria** (what must be TRUE):
  1. At least 10 real Bravos Trade Alert posts have been processed end-to-end: scraped, parsed, risk-evaluated, and (where in-hours) submitted as paper orders with fills captured
  2. No order reaches IBKR with an incorrect ticker, wrong action type, or miscalculated share quantity
  3. All parser edge cases discovered during the validation cycle have been fixed and re-tested
  4. No critical system failure (scraper session expiry not auto-recovered, IBKR heartbeat failure not auto-recovered, risk bypass) occurs during a full trading day
**Plans**: TBD

### Phase 7: Dashboard and Notifications
**Goal**: A web dashboard shows current signals, open positions, closed trade history, and system health; the system sends email alerts when the circuit breaker trips or a critical system error occurs
**Depends on**: Phase 5
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, NOTF-01, NOTF-02
**Success Criteria** (what must be TRUE):
  1. The dashboard displays all recent trade signals with parse status, action type, ticker, and whether an order was placed — updated without a page reload
  2. The dashboard displays all open positions with ticker, entry price, current weight, quantity, and unrealized P&L
  3. The dashboard displays closed trade history with entry price, exit price, and realized P&L per trade
  4. The dashboard displays a system health panel showing last scrape timestamp and IBKR connection status; a staleness warning appears if the last scrape was more than 15 minutes ago
  5. An email is sent when the daily loss circuit breaker triggers, and when a critical system error occurs (scraper failure, IBKR disconnect not auto-recovered, parse failure rate spike)
**Plans**: TBD

### Phase 8: Live Deployment
**Goal**: The system is running as hardened systemd services with production secrets, the live IBKR account is connected, and the deployment is resilient to IB Gateway nightly restarts and Chrome memory growth
**Depends on**: Phase 6, Phase 7
**Requirements**: DEPL-02
**Success Criteria** (what must be TRUE):
  1. The trading process and dashboard run as separate systemd services that auto-restart on failure
  2. The live IBKR account (port 4001) is connected and processing real orders
  3. The system handles IB Gateway's nightly restart window without operator intervention and resumes normal operation when Gateway comes back
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
Note: Phase 3 depends on Phase 1 only (not Phase 2), and can be developed in parallel with Phase 2. Phase 7 depends on Phase 5 (not Phase 6) and can be developed in parallel with Phase 6.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Infrastructure Setup | 7/7 | Done | 2026-05-07 |
| 2. Signal Ingestion | 4/5 | In Progress|  |
| 3. IBKR Connection | 0/TBD | Not started | - |
| 4. Risk Controls and Order Execution | 0/TBD | Not started | - |
| 5. Fill Capture and Position Reconciliation | 0/TBD | Not started | - |
| 6. Paper Trading Validation | 0/TBD | Not started | - |
| 7. Dashboard and Notifications | 0/TBD | Not started | - |
| 8. Live Deployment | 0/TBD | Not started | - |
