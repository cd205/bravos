# Pitfalls Research — Bravos Trading System

## Domain: Web Scraping Reliability

### P1: Silent Zero-Result Scrapes on Session Expiry
**Warning signs:** Scraper runs without error but detects zero new posts consistently; no exception thrown.
**Why it happens:** WordPress membership sites return a 200 OK with a "please log in" redirect page when session expires. Selenium sees a valid page, not an error.
**Prevention:** After each scrape, assert presence of a post-login DOM element (e.g. the CATEGORIES dropdown or a Trade Alert card). If absent, re-authenticate before returning results. Never trust "no new posts" without confirming you're logged in.
**Phase:** Signal ingestion (Phase 1/2)

### P2: ChromeDriver / Chrome Version Mismatch
**Warning signs:** `SessionNotCreatedException` on startup; Chrome launches then immediately crashes.
**Why it happens:** Chrome auto-updates on the VM; webdriver-manager may cache an old ChromeDriver version.
**Prevention:** Pin Chrome to a specific version via apt-mark hold, or use `webdriver-manager` with `--no-sandbox` and set `ChromeDriverManager().install()` in a try/except that clears cache on failure. Test ChromeDriver version match in CI.
**Phase:** Deployment setup

### P3: Rate Limiting / Bot Detection
**Warning signs:** Login page loads but form submission fails silently; CAPTCHA appears.
**Why it happens:** Aggressive polling or missing user-agent/cookie headers triggers Cloudflare or WordPress bot detection.
**Prevention:** Use the anti-detection patterns from the `selenium-scraper` skill (custom user-agent, disable automation flags, human-like delays). 5-minute polling interval is safe; never poll faster. Maintain a persistent browser session across poll cycles (don't restart Chrome every poll).
**Phase:** Signal ingestion

---

## Domain: Signal Parsing

### P4: Ambiguous Action Type Classification
**Warning signs:** "Partial close" treated as "full close" or vice versa; position goes flat unexpectedly.
**Why it happens:** Title suffixes vary ("Profit Booking", "Booking Partial Profits", "Reducing Exposure") and don't map 1:1 to action types. Prose also sometimes contradicts the title.
**Prevention:** Maintain an explicit keyword → action_type mapping table. Log confidence score for every parsed signal. For first 20-30 real signals, route low-confidence parses to a manual review queue before order submission. Never infer action type from a single keyword.
**Phase:** Signal parser (Phase 2)

### P5: Wrong Weight Extraction = Wrong Order Size
**Warning signs:** Order size is significantly larger or smaller than expected.
**Why it happens:** "reducing the position from a weight of 5 to 4" — naive regex may capture "5" or "4" without understanding direction. The delta (1 unit sold) is what matters.
**Prevention:** Always extract BOTH old and new weight values. Compute delta explicitly: `units_to_trade = abs(old_weight - new_weight)`. Store both in the signal record for audit. Alert if extracted weight exceeds configured max_weight_per_ticker.
**Phase:** Signal parser

### P6: Duplicate Signal Detection Failure
**Warning signs:** Same alert processed twice; duplicate orders placed.
**Why it happens:** Post URL or date-based deduplication fails if the site re-publishes or updates a post (WordPress updates `modified_time` on minor edits).
**Prevention:** Use the post URL as the deduplication key (stable), not the modified timestamp. Store `source_url` as a UNIQUE constraint in the signals table. On duplicate key, log and skip — never re-process.
**Phase:** Signal ingestion + DB schema

---

## Domain: IBKR Connectivity

### P7: CLOSE-WAIT Stale Socket — Silent Connection Failure
**Warning signs:** No exception thrown; `isConnected()` returns True; but no responses to API calls. Orders appear to submit but never fill.
**Why it happens:** IB Gateway drops the TCP connection (daily restart, network blip) but the Python socket enters CLOSE-WAIT state. ibapi doesn't detect this until the next write attempt, which may succeed at the TCP layer but go nowhere.
**Prevention:** Implement heartbeat: call `reqCurrentTime()` every 60 seconds and expect `currentTime()` callback within 5 seconds. On timeout, force-disconnect, clear state, reconnect. This is documented in the `ibkr-connection` skill.
**Phase:** IBKR integration

### P8: nextOrderId Threading Race
**Warning signs:** Error 103 ("Duplicate order ID"); orders rejected.
**Why it happens:** ibapi requires each order to use `nextValidId` provided by the `nextValidId()` callback after connection. If orders are submitted before this callback fires, or if the ID counter isn't protected by a Lock, race conditions cause duplicate IDs.
**Prevention:** Use a `threading.Lock` around `nextOrderId` increment. Never submit an order until `nextValidId()` has been called at least once post-connection. Queue orders and drain the queue only after connection + nextValidId confirmed.
**Phase:** IBKR integration

### P9: Order State Orphaning on Crash
**Warning signs:** System restarts but doesn't know about orders placed before the crash; duplicate orders placed.
**Why it happens:** Order submitted to IBKR, system crashes before order state written to DB.
**Prevention:** Write order to DB with status `PENDING_SUBMISSION` BEFORE calling `placeOrder()`. Update to `SUBMITTED` in the `openOrder()` callback. On startup, call `reqOpenOrders()` and reconcile against DB `PENDING_SUBMISSION` rows.
**Phase:** IBKR integration + DB schema

---

## Domain: Position Reconciliation

### P10: Internal State Drift from External Events
**Warning signs:** System thinks position is open; IBKR shows it closed (or vice versa).
**Why it happens:** Any out-of-band event — manual trade in TWS, partial fill, expired order, IBKR risk liquidation — changes broker state without going through the system.
**Prevention:** Call `reqPositions()` every 5 minutes and reconcile against `broker_positions_snapshot` table. Flag any discrepancy between internal `position_lots` and broker snapshot. Never trust internal state alone for order sizing decisions — always check broker snapshot.
**Phase:** Position tracking

### P11: Partial Fill Handling
**Warning signs:** Order partially filled; system marks it complete; remaining shares never filled.
**Why it happens:** ibapi sends multiple `execDetails()` callbacks for partial fills. Naive implementation treats first callback as complete fill.
**Prevention:** Track `filled_qty` vs `total_qty` per order. Only mark order FILLED when `filled_qty == total_qty`. Sum all `execDetails()` callbacks for the same `orderId`. Store each execution record separately in the `executions` table.
**Phase:** IBKR integration

---

## Domain: Risk Controls

### P12: Signal-to-Order Without Risk Check
**Warning signs:** Order placed that exceeds max allocation; daily loss limit not enforced.
**Why it happens:** Risk checks implemented as an afterthought, bypassed in edge cases (e.g. "add to position" path skips the allocation check).
**Prevention:** Implement a single `RiskGate.check(signal) → bool` that ALL order paths must pass through — no exceptions. Check: max_open_positions, max_allocation_per_trade, daily_loss_limit, market_hours. Log every risk gate decision (pass or block) with reason.
**Phase:** Order engine

---

## Domain: GCP Deployment

### P13: IB Gateway Daily Restart Window
**Warning signs:** System goes silent every night around 11:45pm–12:15am ET; no orders processed.
**Why it happens:** IB Gateway has a mandatory daily restart. The ibapi connection drops; Python process may not reconnect automatically.
**Prevention:** Detect disconnect via `connectionClosed()` callback. Implement exponential backoff reconnection loop (30s, 60s, 120s). Log disconnect/reconnect events. Schedule systemd service to NOT restart during the Gateway maintenance window (use `systemd-timer` to pause the main loop 11:30pm–12:30am ET).
**Phase:** Deployment

### P14: Headless Chrome Memory Leak
**Warning signs:** VM memory usage grows over days; eventually OOM-killed.
**Why it happens:** Selenium Chrome instances accumulate open tabs, cached resources, or crash silently and leave zombie processes.
**Prevention:** Reuse a single Chrome instance across poll cycles (don't create/destroy per poll). Add a daily Chrome restart (outside market hours). Monitor Chrome PID and restart if it dies. Set `--disable-dev-shm-usage` and `--no-sandbox` flags. Limit Chrome memory with cgroup if needed.
**Phase:** Deployment

### P15: No Alerting on System Failure
**Warning signs:** System stops processing alerts for hours; user doesn't notice until checking dashboard.
**Why it happens:** systemd restarts the process, but silent failures (login failure, parse failure, ibapi disconnect) don't trigger any notification.
**Prevention:** Implement a "last successful scrape" timestamp in the DB. FastAPI dashboard shows staleness warning if >15 minutes since last scrape. Email/SMS alert if scraper hasn't succeeded in >30 minutes during market hours.
**Phase:** Monitoring / dashboard

---

## Summary Table

| # | Pitfall | Domain | Severity | Phase |
|---|---------|--------|----------|-------|
| P1 | Silent zero-result on session expiry | Scraping | CRITICAL | 1 |
| P2 | ChromeDriver version mismatch | Scraping | HIGH | Deploy |
| P3 | Rate limiting / bot detection | Scraping | HIGH | 1 |
| P4 | Ambiguous action type classification | Parsing | CRITICAL | 2 |
| P5 | Wrong weight extraction → wrong size | Parsing | CRITICAL | 2 |
| P6 | Duplicate signal processing | Parsing | HIGH | 1-2 |
| P7 | CLOSE-WAIT stale socket | IBKR | CRITICAL | 3 |
| P8 | nextOrderId threading race | IBKR | HIGH | 3 |
| P9 | Order state orphaning on crash | IBKR | HIGH | 3 |
| P10 | Internal state drift | Positions | HIGH | 4 |
| P11 | Partial fill handling | IBKR | MEDIUM | 3 |
| P12 | Risk check bypass | Risk | CRITICAL | 3 |
| P13 | Gateway daily restart | Deployment | HIGH | Deploy |
| P14 | Chrome memory leak | Deployment | MEDIUM | Deploy |
| P15 | No alerting on system failure | Monitoring | HIGH | 5 |
