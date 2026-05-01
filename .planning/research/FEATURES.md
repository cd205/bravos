# Feature Landscape

**Domain:** Automated trading signal-follower (web-scraped alerts → IBKR order execution)
**Project:** Bravos Trading System
**Researched:** 2026-05-01
**Confidence:** HIGH

---

## Table Stakes

Features where absence means the system is unusable, dangerous, or fundamentally broken.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Signal ingestion — scraper poll loop | System has no input without it | Med | 5-min interval; Selenium login + category filter + new-post detection |
| Signal deduplication | Without this, every poll re-processes old alerts and fires duplicate orders | Low | Fingerprint on post URL; stored in DB as UNIQUE constraint |
| Alert-to-signal parser | Raw HTML prose → structured fields (ticker, action, weight, price) | High | Regex/NLP hybrid; must handle prose variation; confidence scoring needed |
| Parse failure handling | A bad parse must not silently proceed to order placement | Low | `parse_status` field; failed signals parked for review, not executed |
| Order size calculation | Core business logic: (weight_delta × weight_pct) × account_net_liquidation | Low | Must re-query IBKR account value at time of sizing, not cache it |
| Order submission — market orders | Primary execution path; Bravos alerts are time-sensitive, not price-specific | Low | ibapi `placeOrder`; MKT order type; DAY TIF during market hours |
| Pre-order risk checks | Without these, a bug or bad parse can cause catastrophic losses | Med | Max positions, max allocation, daily loss limit, market hours |
| Market hours gate | Orders submitted outside RTH will be rejected or fill at bad prices | Low | NYSE 09:30–16:00 ET; check before every submission; paper mode bypass |
| Order status tracking | Need to know if orders filled, partial, or rejected | Med | `orderStatus` callback; store in orders table with status lifecycle |
| Position reconciliation | Internal state must match IBKR reality | High | `reqPositions` + broker_positions_snapshot; run on startup and periodically |
| Startup reconciliation | On restart, must re-sync with IBKR before processing new signals | Med | Fetch open positions + open orders before entering poll loop |
| Signal audit trail | Every scraped signal stored verbatim, regardless of action taken | Low | raw_signal_text, scraped_at, parse_status, signal_status fields |
| Paper trading mode | Required for safe testing before live deployment | Low | Toggle via config (port 4002 vs 4001) |
| Secure credential storage | Credentials for Bravos site and IBKR must never appear in plaintext | Low | Environment variables or GCP Secret Manager; validated at startup |
| System health logging | Without logs, debugging failures is impossible | Low | Structured logging to file + stdout; log every scrape, parse, and order attempt |
| Graceful IBKR disconnect + reconnect | CLOSE-WAIT socket accumulation if not handled | Med | Heartbeat every 60s; force-reconnect on timeout |

---

## Risk Management Controls (Table Stakes)

| Control | Why Required | Complexity |
|---------|--------------|------------|
| Max single-position allocation cap | Prevents oversizing from bad weight parse | Low |
| Max open positions limit | Prevents unbounded position accumulation | Low |
| Daily loss circuit breaker | Halts trading if P&L drops below threshold | Med |
| Market hours enforcement | No orders outside NYSE RTH | Low |
| Duplicate order guard | Block re-submitting for already-acted-on signal | Low |
| IBKR connection required | Hard block if not connected | Low |
| Parse confidence threshold | Reject low-confidence parses | Med |
| Dry-run / simulation mode | Execute all logic except final placeOrder | Low |

---

## Differentiators

| Feature | Value Proposition | Complexity |
|---------|-------------------|------------|
| Email alert parsing (secondary channel) | Redundancy if web scraper fails | Med |
| Stale signal detection | Skip alerts posted hours ago | Low |
| FIFO lot-assignment for reduce/close | Deterministic behavior with multiple lots | Med |
| Partial-fill awareness | Correctly track partially filled orders | Med |
| Execution quality / slippage tracking | Record fill price vs signal price | Low |
| Commission capture | Store commissions from commissionReport callback | Low |
| Reconciliation alerts | Notify when internal state diverges from IBKR | Med |
| Dashboard — positions tab | Open positions, entry price, current P&L, weight | Med |
| Dashboard — signals tab | Recent signals, parse status, action taken | Low |
| Dashboard — closed trades tab | Closed P&L history | Low |
| Dashboard — account summary | Net liquidation, buying power, day P&L | Low |
| Dashboard — system health | Last scrape time, IBKR connection status | Low |
| Email/notification on signal received | Know when alert processed and acted on | Low |
| Email/notification on order fill | Confirm execution happened | Low |
| Email/notification on circuit breaker trip | Alert when daily loss limit halted trading | Low |
| Weight-to-shares audit log | Persist exact inputs for each order size calculation | Low |

---

## Anti-Features (deliberately NOT in v1)

| Anti-Feature | Why Avoid |
|--------------|-----------|
| Manual order placement UI | System is fully automated; adds complexity |
| Limit order execution | Bravos signals not price-specific; adds fill uncertainty |
| Stop-loss / take-profit auto-placement | Conflicts with Bravos close-signal model |
| Multi-account management | Configuration complexity; single account per deployment |
| Options/futures/forex | Different risk model entirely; equities only |
| Backtesting engine | Bravos's domain, not ours |
| Mobile app | Web dashboard sufficient |
| Real-time price streaming | 5-min scrape interval; market orders make this unnecessary |
| Multi-source signal aggregation | Bravos web + email only (same source) |
| Automated 2FA handling | Fragile and potentially insecure |

---

## Feature Dependency Chain

```
Signal scraper (poll loop)
  → Signal deduplication
    → Alert parser (prose → structured fields)
      → Parse failure handling
        ├── [A] Order size calculation
        │     → Risk checks (all controls)
        │       → Market hours gate
        │         → Order submission
        │           → Order status tracking
        │             → Execution capture (fills + commissions)
        │               → Position reconciliation
        │                 → P&L calculation
        │                   → Dashboard (positions, closed trades)
        └── [B] Signal audit trail (parallel)

IBKR connection + heartbeat/reconnect
  → Startup reconciliation
    → (unblocks Branch A safely)

Paper trading mode
  → (gates live account integration)

Risk controls (all)
  → (must be complete before live trading)
```

---

## MVP Ordering

1. Signal ingestion pipeline (scraper → dedup → parser → audit)
2. IBKR connection + startup reconciliation
3. Risk controls (all — complete before any live orders)
4. Order size calculation + market hours gate + order submission
5. Order status tracking + execution capture
6. Position reconciliation
7. Paper trading mode end-to-end validation
8. Web dashboard (minimal: signals tab + positions tab)

**Defer post-MVP:** email parsing, commission capture, slippage reporting, config hot-reload, reconciliation alerts, full closed-trades P&L dashboard.

---

## Key Finding

**The parser is the hardest and most critical component.** Every downstream feature — order sizing, risk checks, execution — depends on it producing correct structured data from inconsistent prose. It warrants its own phase with explicit validation against real Bravos alert samples before the execution path is connected.
