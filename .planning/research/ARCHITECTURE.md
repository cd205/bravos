# Architecture Patterns — Bravos Trading System

**Domain:** Automated signal-to-order trading system
**Researched:** 2026-05-01

---

## Component Diagram

```
  bravosresearch.com
        |
        | HTTPS (Selenium/Chrome, 5-min poll)
        v
+-------------------+
|  Scraper Module   |  (headless Chrome, login session, category filter)
|  selenium         |
+--------+----------+
         |
         | raw HTML (post title + body text)
         v
+-------------------+
|  Parser Module    |  (regex primary, spaCy NER fallback)
|  re / spaCy       |  extracts: ticker, action_type, price,
+--------+----------+           weight_from, weight_to, entry_context
         |
         | structured SignalRecord
         v
+-------------------+
|   Signal Store    |  INSERT INTO signals (idempotent on post_url)
|   PostgreSQL      |<----------------------------------------------+
|   psycopg2        |  also writes: orders, executions, position_lots|
+--------+----------+           lot_actions, broker_positions_snapshot|
         |                                                            |
         | new unprocessed signal                                     |
         v                                                            |
+-------------------+        +------------------+                    |
|   Order Engine    |        |  Risk Controller  |                   |
|   (sync module)   +------->|  pre-trade checks |                   |
+--------+----------+        |  max positions    |                   |
         |                   |  max allocation   |                   |
         | approved order     |  daily loss limit |                   |
         |                   +------------------+                    |
         v                                                            |
+-------------------+                                                 |
|   IBKR Thread     |  EClient.placeOrder()                          |
|   ibapi           |  EWrapper callbacks:                            |
|   EWrapper+EClient|    orderStatus, execDetails, openOrder         |
+--------+----------+    error, accountSummary, position             |
         |                                                            |
         | IBKR TCP socket (localhost:4001/4000)                      |
         v                                                            |
+-------------------+                                                 |
|   IB Gateway      |  separate OS process                           |
|   (existing)      |  paper: port 4001 / live: port 4000            |
+-------------------+                                                 |
         |                                                            |
         | EWrapper callbacks (fills, positions, account data)        |
         v                                                            |
+-------------------+                                                 |
|  Position Tracker |  reconcile DB state vs IBKR broker state ------+
|  PostgreSQL writes|  UPSERT broker_positions_snapshot
+-------------------+  UPDATE position_lots on fill


+-------------------+
|  FastAPI Dashboard|  read-only queries against PostgreSQL
|  uvicorn          |  Jinja2 templates + htmx polling (10-30s)
|  (separate svc)   |  shows: signals, open positions, P&L
+-------------------+
         |
         | HTTP (internal, or reverse-proxied)
         v
      Browser
```

---

## Data Flow

### Signal Ingestion Path

```
1. Schedule tick (every 5 min)
        |
2. Scraper.poll()
   - reuse Chrome session (avoid re-login on every tick)
   - navigate to /research/?category=trade-alert
   - collect all post URLs on page
   - diff against signals.post_url in PostgreSQL
   - for each NEW url: open in tab, extract title + body HTML
        |
3. Parser.parse(title, body) -> SignalRecord
   - regex pass: ticker, price, weights, action_type
   - spaCy NER pass if regex yields None fields
   - action_type resolved from title suffix keywords:
       "Profit Booking"     -> partial_close
       "Breakdown"          -> close
       "Technical Strength" -> open or add (weight_from == 0 -> open)
       "Agriculture"        -> open or add
        |
4. SignalStore.insert(signal)
   - INSERT INTO signals (...) ON CONFLICT (post_url) DO NOTHING
   - returns signal_id; duplicate posts are silently skipped
   - signal.status = 'pending'
        |
5. OrderEngine.process(signal)
   (see Order Execution Path)
```

### Order Execution Path

```
5. RiskController.check(signal, account_state) -> approved | rejected
   - max open positions exceeded? -> reject
   - this trade exceeds max allocation per position? -> reject
   - daily realized loss > limit? -> reject
   - outside market hours? -> defer (queue for next open)
        |
6. SizeCalculator.calculate(signal, account_value)
   - shares = floor((weight_units * pct_per_weight * account_value) / current_price)
        |
7. IBKRThread.submit_order(ticker, action, shares)
   - called from main thread via thread-safe queue
   - EClient.placeOrder(orderId, contract, order)
   - UPDATE signals SET status='submitted', order_id=... WHERE signal_id=...
        |
8. EWrapper callbacks (on IBKR thread)
   - orderStatus()   -> UPDATE orders SET status=...
   - execDetails()   -> INSERT INTO executions; UPDATE position_lots
   - error()         -> log + UPDATE signals SET status='error'
        |
9. PositionTracker.reconcile()
   - runs after each fill callback AND on 15-min scheduled tick
   - EClient.reqPositions() -> EWrapper.position() callbacks
   - UPSERT broker_positions_snapshot
   - flag mismatches between DB position_lots and broker state
```

### Dashboard Read Path

```
Browser -> htmx polling (every 15s)
        -> GET /signals, /positions, /pnl
        -> FastAPI route handler
        -> psycopg2 read query (read-only connection)
        -> Jinja2 template render
        -> HTML fragment returned, htmx swaps DOM
```

---

## Threading Model

ibapi mandates a specific threading architecture. This is the most critical constraint in the system.

```
Main Thread (bravos-trader.service)
  |
  |-- schedule loop (blocks on time.sleep)
  |     |
  |     +-- Scraper.poll()        (sync, runs Chrome)
  |     +-- Parser.parse()        (sync, CPU only)
  |     +-- SignalStore.insert()  (sync, psycopg2)
  |     +-- OrderEngine.process() (sync)
  |           |
  |           +-- RiskController.check()   (sync, DB read)
  |           +-- SizeCalculator.calc()    (sync)
  |           +-- command_queue.put(order) (thread-safe)
  |
  +-- IBKRApp thread (daemon=True)
        |
        |-- EClient.run()       <- blocking reader loop (MUST be on its own thread)
        |     reads all incoming IBKR messages, dispatches callbacks
        |
        |-- EWrapper callbacks  (called by EClient.run() on THIS thread)
        |     orderStatus()
        |     execDetails()
        |     position()
        |     accountSummary()
        |     error()
        |
        +-- command_queue.get() <- drain orders from main thread
              EClient.placeOrder()   (safe to call from this thread)
              EClient.reqPositions() (safe to call from this thread)
```

**Rules enforced by this model:**

- `EClient.run()` is blocking and must run on a dedicated thread — it is never called from the main thread.
- All `EClient` method calls (placeOrder, reqPositions, reqAccountSummary) should be dispatched from within the IBKR thread, not called cross-thread directly. Use a `queue.Queue` to pass commands from main thread to IBKR thread.
- `EWrapper` callbacks are invoked on the IBKR thread. PostgreSQL writes from callbacks use a separate DB connection allocated to the IBKR thread (psycopg2 connections are not thread-safe; each thread holds its own).
- `nextOrderId` is managed with a `threading.Lock` — incremented on each `nextValidId` callback, read before each placeOrder.

**Connection objects per thread:**

| Thread | psycopg2 connection | EClient instance |
|--------|--------------------|--------------------|
| Main | conn_main (scraper, signal store, risk checks) | none |
| IBKR | conn_ibkr (order writes, execution writes) | app (shared) |
| Dashboard | conn_dashboard (read-only) | none |

---

## Build Order / Dependency Chain

Components must be built in this order because each layer depends on the previous.

```
Layer 0 — Infrastructure (no code dependencies)
  [A] PostgreSQL schema + Alembic migrations
      Tables: signals, orders, executions, position_lots,
              lot_actions, broker_positions_snapshot
      Must exist before any module writes data.

Layer 1 — IBKR Connection (depends on: IB Gateway running, Layer 0)
  [B] IBKRApp (EWrapper + EClient subclass)
      - connect(), disconnect(), run() thread management
      - nextValidId tracking
      - error() logging
      Test: connect to paper Gateway, reqCurrentTime()

Layer 2 — Core Data Modules (depends on: Layer 0)
  [C] SignalStore
      - insert_signal(), mark_submitted(), mark_filled(), mark_error()
      - get_pending_signals()
      Test: insert a synthetic signal, verify idempotency

  [D] PositionTracker
      - upsert_broker_snapshot()
      - get_open_lots()
      - reconcile() (compare DB lots vs broker snapshot)
      Test: insert lots, run reconcile, assert no drift

Layer 3 — Order Logic (depends on: B, C, D)
  [E] RiskController
      - check(signal, account_state) -> Decision
      - max_positions, max_allocation, daily_loss_limit (config-driven)
      Test: unit test each rejection condition

  [F] SizeCalculator
      - calculate(weight_units, pct_per_weight, account_value, price) -> shares
      Test: arithmetic unit tests

  [G] OrderEngine
      - process(signal): orchestrates E + F + B.submit
      Test: mock IBKRApp, verify correct order dispatched

Layer 4 — Scraper + Parser (depends on: Layer 0, C)
  [H] Parser
      - parse(title, body) -> SignalRecord | None
      - regex patterns, spaCy fallback
      Test: fixture HTML from real Bravos posts (sanitized)

  [I] Scraper
      - login(), poll(), extract_post()
      - diff against DB for new URLs
      Test: integration test against live site (once, manually)

Layer 5 — Main Process Loop (depends on: all above)
  [J] main.py
      - init DB, init IBKRApp thread, init schedule
      - schedule.every(5).minutes.do(Scraper.poll)
      - schedule.every(15).minutes.do(PositionTracker.reconcile)
      - loop: schedule.run_pending(); sleep(1)

Layer 6 — Dashboard (depends on: Layer 0 only — read-only)
  [K] FastAPI app (separate process)
      - /signals, /positions, /pnl routes
      - Jinja2 templates + htmx fragments
      - read-only psycopg2 connection
      Can be built in parallel with Layers 1-5.
```

---

## Process Boundaries

Two systemd services run on the GCP VM. IB Gateway is a third independent process.

```
systemd unit: bravos-trader.service
  Process: python main.py
  Threads:
    - Main thread (schedule loop, scraper, parser, order engine)
    - IBKRApp thread (EClient.run(), EWrapper callbacks)
  Resources:
    - Chrome subprocess (spawned by Selenium, one persistent session)
    - psycopg2 connections: 2 (main + ibkr thread)
  Restart: on-failure, RestartSec=10
  Logs: journald (structlog JSON output)
  User: bravos (non-root, has access to Chrome, secrets)
  After: network.target postgresql.service

systemd unit: bravos-dashboard.service
  Process: uvicorn bravos.dashboard.app:app --host 0.0.0.0 --port 8000
  Threads: uvicorn worker(s) (default: 1 worker for simplicity)
  Resources:
    - psycopg2 connection: 1 (read-only)
  Restart: on-failure
  Logs: journald
  After: network.target postgresql.service

IB Gateway (existing, not managed by bravos systemd)
  Process: ibgateway (Java)
  Port: 4001 (paper) or 4000 (live)
  Must be running before bravos-trader.service starts.
  Recommendation: wrap in its own systemd unit on the VM
                  so it auto-starts on boot.

Chrome/Chromium (subprocess, not a service)
  Spawned by webdriver-manager inside bravos-trader.service
  Headless mode: --headless=new --no-sandbox --disable-dev-shm-usage
  One persistent Chrome session reused across poll cycles
  (re-login only when session expires or Chrome crashes)
```

### What Is In-Process vs Separate

| Concern | In-Process (bravos-trader) | Separate Process |
|---------|---------------------------|------------------|
| Scraping | Yes (Main thread) | — |
| Parsing | Yes (Main thread) | — |
| Signal DB writes | Yes (Main thread) | — |
| Risk controls | Yes (Main thread) | — |
| Order submission | Yes (IBKR thread) | — |
| Position tracking | Yes (IBKR thread) | — |
| Dashboard serving | — | bravos-dashboard.service |
| IB Gateway | — | ibgateway process |
| PostgreSQL | — | postgresql.service |
| Chrome browser | subprocess of trader | — |

---

## Key Architectural Constraints

### 1. ibapi Connection Lifecycle
- IBKRApp must call `connect()` before `EClient.run()` is started on its thread.
- If the connection drops, `EClient.run()` exits. The IBKR thread must detect this and attempt reconnection with backoff. The main schedule loop must not submit orders during reconnection.
- `nextValidId` is delivered once on connect. Cache it and increment per order, protected by a Lock.

### 2. Chrome Session Persistence
- Login once at startup, hold the session across 5-minute poll cycles.
- Do not re-instantiate `webdriver.Chrome()` on every poll — that re-incurs startup time and risks session cookies being lost.
- Implement a `is_session_valid()` check at each poll tick; re-login only if the session has expired (detected by redirect to login page).

### 3. Signal Idempotency
- `INSERT INTO signals (...) ON CONFLICT (post_url) DO NOTHING` is the primary deduplication gate.
- Parser must also be stateless — same input always produces same SignalRecord.
- The Scraper must record `last_scrape_at` in the DB (not in memory) so a restart doesn't re-process all historical posts.

### 4. No Shared State Between Services
- bravos-trader and bravos-dashboard share state only through PostgreSQL.
- The dashboard never touches the IBKRApp instance or any in-memory structures in the trader process.
- This means the dashboard is safe to deploy, restart, or update independently.

### 5. Risk Gate Is Synchronous and Blocking
- The risk controller runs synchronously before any order is queued.
- No order can enter the IBKR thread command queue without passing risk checks.
- Market-hours check is part of risk controller — defer outside hours, do not reject permanently.

---

## Scalability Considerations

This is a single-account, single-signal-source system. Scaling is not a near-term concern, but the following limits are worth knowing.

| Concern | At Current Scale | If It Becomes a Problem |
|---------|-----------------|------------------------|
| Signal volume | ~1-5 alerts/day | Regex parser is fast; not a bottleneck |
| DB connections | 3 (main, ibkr, dashboard) | Add PgBouncer if multiple dashboards or parallel workers needed |
| Chrome memory | ~300MB per session | Single session; restart weekly via systemd timer if memory grows |
| IB Gateway API rate limits | 50 messages/sec | Single account, low order frequency — not a concern |
| IBKR order throughput | 1 order per signal | Sequential submission is fine; no batch needed |

---

## Sources

- ibapi EWrapper/EClient threading model: `ibkr-connection` skill (project-local)
- PostgreSQL schema design: `trade-database-review` skill (project-local)
- Scraper session patterns: `selenium-scraper` skill (project-local)
- systemd service patterns: STACK.md (this research)
- Deployment environment: PROJECT.md constraints section
