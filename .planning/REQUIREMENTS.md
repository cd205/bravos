# Requirements: Bravos Trading System

**Defined:** 2026-05-01
**Core Value:** When a new trade alert is posted on Bravos Research, the correct order is placed in IBKR within minutes — without manual intervention.

## v1 Requirements

### Signal Ingestion

- [ ] **INGST-01**: System logs into bravosresearch.com using securely stored credentials via Selenium and maintains a persistent browser session throughout the trading day
- [ ] **INGST-02**: System polls the research page every 5 minutes, filters to "Trade Alert" category, and detects posts not previously seen
- [ ] **INGST-03**: System deduplicates signals by post URL — a post already processed is never re-processed regardless of site edits
- [ ] **INGST-04**: System extracts from each new Trade Alert post: ticker symbol, action type (open/add/partial-close/close), weight change (old weight → new weight), and reference price
- [ ] **INGST-05**: System assigns a confidence score to each parsed signal; low-confidence parses are flagged and not routed to order execution
- [ ] **INGST-06**: Every scraped signal is stored verbatim (raw HTML + structured fields) in the database with a parse status, regardless of whether an order is placed
- [ ] **INGST-07**: System detects and re-authenticates when a session expires (i.e. confirms logged-in state after each scrape cycle, not just at startup)

### Order Execution

- [ ] **EXEC-01**: System calculates order share quantity as: `abs(new_weight - old_weight) × weight_pct_per_unit × account_net_liquidation / current_price`, using IBKR account value fetched at order time
- [ ] **EXEC-02**: System submits market orders (MKT, DAY) to IBKR via ibapi for open, add, partial-close, and close signal types
- [ ] **EXEC-03**: System enforces a market hours gate — no orders are submitted outside NYSE regular trading hours (09:30–16:00 ET)
- [ ] **EXEC-04**: System tracks order lifecycle (submitted → filled / partial / rejected / cancelled) via ibapi callbacks and stores status in the database
- [ ] **EXEC-05**: System captures actual fill price and fill quantity from ibapi execution callbacks and stores per-execution records
- [ ] **EXEC-06**: System handles partial fills correctly — order is only marked FILLED when total filled quantity matches the order quantity; intermediate fills update position incrementally

### Risk Controls

- [ ] **RISK-01**: System enforces a configurable maximum number of concurrent open positions; new entry orders are blocked if the limit is reached
- [ ] **RISK-02**: System enforces a configurable maximum allocation per trade as % of account value; orders exceeding this cap are blocked
- [ ] **RISK-03**: System enforces a configurable daily loss circuit breaker — if realized + unrealized P&L falls below the threshold, no new orders are submitted for the remainder of the trading day
- [ ] **RISK-04**: Every risk gate decision (pass or block) is logged with the reason, signal ID, and computed values used

### IBKR Integration

- [ ] **IBKR-01**: System maintains a persistent connection to IB Gateway with a heartbeat check every 60 seconds; 2FA is only required at initial startup, not on reconnect
- [ ] **IBKR-02**: System detects stale/dead connections (CLOSE-WAIT) and forces reconnect automatically without requiring operator intervention
- [ ] **IBKR-03**: On system startup, system reconciles current IBKR positions and open orders with internal database state before entering the scrape/execute loop
- [ ] **IBKR-04**: System periodically reconciles internal position state against IBKR's authoritative position data (reqPositions); discrepancies are logged and flagged
- [ ] **IBKR-05**: System supports both paper trading account (port 4002) and live account (port 4001) via configuration toggle, enabling end-to-end testing without real orders

### Audit Trail

- [ ] **AUDIT-01**: Every system action (scrape, parse, risk check, order submission, fill, position open/close/reduce) is recorded in the database with a timestamp, actor (system/automated), and outcome — no action is silent
- [ ] **AUDIT-02**: Each trade signal can be traced end-to-end: from the raw scraped post → parsed fields → risk gate decision → order submitted → fills received → position state change
- [ ] **AUDIT-03**: Every order record links to the signal that triggered it; every execution record links to the order; every position lot change links to the execution
- [ ] **AUDIT-04**: Partial closes and profit-booking actions record both the lot(s) reduced and the resulting remaining open quantity, preserving the full lot history
- [ ] **AUDIT-05**: Position closes record which specific lots were closed (FIFO), the entry price of each lot, exit price, and realized P&L per lot
- [ ] **AUDIT-06**: All audit records are immutable — system appends new state rows rather than updating/deleting history; prior states are always recoverable

### Position Tracking

- [ ] **POS-01**: System maintains an internal record of all open positions (lots) with entry price, weight, quantity, and associated signal
- [ ] **POS-02**: System tracks closed positions with entry price, exit price, realized P&L, and trade duration
- [ ] **POS-03**: System correctly applies FIFO lot assignment when reducing or closing a position that has multiple open lots (e.g. from scale-ins)

### Dashboard

- [ ] **DASH-01**: Web dashboard displays all recent trade signals with parse status, action type, ticker, and whether an order was placed
- [ ] **DASH-02**: Web dashboard displays all open positions with ticker, entry price, current weight, quantity, and unrealized P&L
- [ ] **DASH-03**: Web dashboard displays closed trade history with entry/exit prices and realized P&L
- [ ] **DASH-04**: Web dashboard displays system health: last scrape timestamp, IBKR connection status, and recent errors — with a staleness warning if last scrape >15 minutes ago

### Notifications

- [ ] **NOTF-01**: System sends an email notification when the daily loss circuit breaker is triggered
- [ ] **NOTF-02**: System sends an email notification when a critical system error occurs (scraper failure, IBKR disconnect not auto-recovered, parse failure rate spike)

### Deployment

- [ ] **DEPL-01**: System runs on a GCP VM (Linux) with IB Gateway installed; IB Gateway runs persistently with 2FA handled at operator startup only
- [ ] **DEPL-02**: Trading process and dashboard are managed as separate systemd services with auto-restart on failure
- [ ] **DEPL-03**: PostgreSQL is installed on the VM with the trading schema (signals, orders, position_lots, executions, broker_positions_snapshot)
- [ ] **DEPL-04**: Bravos Research credentials and IBKR configuration are stored in GCP Secret Manager or environment variables — never in code or committed files
- [ ] **DEPL-05**: Chromium runs in headless mode for Selenium scraping with appropriate anti-detection flags

## v2 Requirements

### Signal Ingestion

- **INGST-V2-01**: System parses Bravos Research alert emails as a secondary signal channel, sharing the same deduplication and parser pipeline as the web scraper
- **INGST-V2-02**: System detects stale signals (posted >2 hours ago but not yet processed) and skips or flags them rather than executing at an outdated price

### Notifications

- **NOTF-V2-01**: System sends email notification when a new trade signal is detected and an order is placed
- **NOTF-V2-02**: System sends email notification when an order is filled with actual fill price

### Execution

- **EXEC-V2-01**: System tracks execution quality by comparing fill price to signal reference price (slippage measurement)
- **EXEC-V2-02**: System captures IBKR commissions from commissionReport callbacks for accurate net P&L calculation

### Dashboard

- **DASH-V2-01**: Dashboard includes account summary panel showing net liquidation, buying power, and day P&L from IBKR
- **DASH-V2-02**: Dashboard supports mobile-responsive layout

## Out of Scope

| Feature | Reason |
|---------|--------|
| Options, futures, forex | Different margin/sizing/expiry model; equities only in v1 |
| Limit orders | Bravos signals not price-specific; market orders give fill certainty |
| Stop-loss / take-profit auto-placement | Conflicts with Bravos close-signal model; positions closed by explicit signal |
| Manual order placement UI | System is fully automated; manual trades handled in TWS |
| Multi-account management | Single IBKR account per deployment |
| Backtesting engine | Bravos's domain; export CSV for analysis |
| Mobile app | Web dashboard sufficient |
| Real-time price streaming | Market orders + 5-min polling makes this unnecessary |
| Multi-source signal aggregation | Bravos web + email only (same source, secondary channel) |
| Automated 2FA handling | Fragile and potentially insecure; operator handles at startup |

## Traceability

*To be populated during roadmap creation.*

| Requirement | Phase | Status |
|-------------|-------|--------|
| INGST-01 | — | Pending |
| INGST-02 | — | Pending |
| INGST-03 | — | Pending |
| INGST-04 | — | Pending |
| INGST-05 | — | Pending |
| INGST-06 | — | Pending |
| INGST-07 | — | Pending |
| EXEC-01 | — | Pending |
| EXEC-02 | — | Pending |
| EXEC-03 | — | Pending |
| EXEC-04 | — | Pending |
| EXEC-05 | — | Pending |
| EXEC-06 | — | Pending |
| RISK-01 | — | Pending |
| RISK-02 | — | Pending |
| RISK-03 | — | Pending |
| RISK-04 | — | Pending |
| AUDIT-01 | — | Pending |
| AUDIT-02 | — | Pending |
| AUDIT-03 | — | Pending |
| AUDIT-04 | — | Pending |
| AUDIT-05 | — | Pending |
| AUDIT-06 | — | Pending |
| IBKR-01 | — | Pending |
| IBKR-02 | — | Pending |
| IBKR-03 | — | Pending |
| IBKR-04 | — | Pending |
| IBKR-05 | — | Pending |
| POS-01 | — | Pending |
| POS-02 | — | Pending |
| POS-03 | — | Pending |
| DASH-01 | — | Pending |
| DASH-02 | — | Pending |
| DASH-03 | — | Pending |
| DASH-04 | — | Pending |
| NOTF-01 | — | Pending |
| NOTF-02 | — | Pending |
| DEPL-01 | — | Pending |
| DEPL-02 | — | Pending |
| DEPL-03 | — | Pending |
| DEPL-04 | — | Pending |
| DEPL-05 | — | Pending |

**Coverage:**
- v1 requirements: 42 total
- Mapped to phases: 0 (roadmap not yet created)
- Unmapped: 36 ⚠️

---
*Requirements defined: 2026-05-01*
*Last updated: 2026-05-01 after initial definition*
