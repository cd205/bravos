# Project Research Summary

**Project:** Bravos Trading System
**Domain:** Automated signal-to-order trading system (web-scraped alerts → IBKR execution)
**Researched:** 2026-05-01
**Confidence:** HIGH

## Executive Summary

The Bravos Trading System is a purpose-built automation pipeline that monitors bravosresearch.com for trade alert posts, parses them into structured signals, and executes corresponding equity orders through Interactive Brokers. The system is not a general-purpose trading platform — it is a single-account, single-source signal follower, and the architecture should reflect that tight scope. Three existing project skills (ibkr-connection, selenium-scraper, trade-database-review) provide directly reusable, battle-tested patterns for the core technical layers, significantly de-risking implementation.

The recommended approach is a synchronous Python monolith with a dedicated IBKR daemon thread: one persistent process handles scraping, parsing, risk evaluation, and order dispatch; a second systemd service runs the read-only FastAPI dashboard. The two services share state only through PostgreSQL. This design avoids the complexity of async frameworks and message queues while remaining robust enough for the expected signal volume (~1–5 alerts/day). The parser is the highest-risk component because every downstream feature — order sizing, risk decisions, execution — depends on it producing correct structured output from inconsistent prose.

The most critical mitigations are: (1) validate the parser against a corpus of real Bravos alert samples before connecting it to the execution path, (2) implement IBKR heartbeat/reconnection logic from day one rather than retrofitting it, and (3) ensure all order paths pass through a single synchronous risk gate with no bypass. Deploying to paper trading mode first is non-negotiable — live trading should not be enabled until an end-to-end paper cycle has been validated.

---

## Key Findings

### Recommended Stack

The stack is built on existing project skills with minimal new dependencies. Selenium 4 with a persistent headless Chrome session handles scraping (reuse of selenium-scraper patterns). Signal parsing uses regex as the primary path and spaCy `en_core_web_sm` as a fallback, avoiding LLM-based extraction entirely. ibapi (official, installed from IB developer portal — not PyPI) handles broker integration, using the EWrapper/EClient threading model from the ibkr-connection skill. PostgreSQL 15+ with psycopg2-binary and Alembic migrations stores all state; no ORM is used because the 6-table schema (signals, position_lots, lot_actions, orders, executions, broker_positions_snapshot) benefits from explicit JOIN and UPSERT logic. The dashboard is FastAPI + Jinja2 + htmx — no Node.js build toolchain required.

**Core technologies:**
- Selenium 4 + webdriver-manager: web scraping — existing skill patterns directly reusable, anti-detection built in
- ibapi (official): IBKR integration — only option; ibkr-connection skill covers full EWrapper/EClient model
- PostgreSQL 15 + psycopg2-binary + Alembic: persistence — trade-database-review skill provides the 6-table schema
- FastAPI + Jinja2 + htmx: dashboard — avoids Node.js build pipeline; htmx polling covers live updates without websockets
- schedule + systemd: process management — single-interval poll loop managed by systemd is correct Linux pattern
- GCP Secret Manager + python-dotenv: secrets — never hardcode credentials; GCP-native in production
- structlog: structured logging — JSON output to journald; required for debugging a headless automated system

**Version critical:** ibapi must match IB Gateway version exactly. Install from the IB developer portal as a zip, not from PyPI.

### Expected Features

The feature set splits cleanly into a core pipeline (must be complete before any live orders) and observability/quality features that can follow.

**Must have (table stakes):**
- Signal scraper with 5-minute poll loop, login session reuse, and post deduplication on URL — system has no input without this
- Alert-to-signal parser (ticker, action type, price, weight_from, weight_to) with parse failure parking — bad parse must never silently reach order placement
- Parse confidence scoring — low-confidence signals routed to review, not execution
- Order size calculator: `floor((weight_delta * pct_per_weight * account_value) / price)` — re-queries account value at execution time, never cached
- Pre-order risk gate (single synchronous check): max positions, max allocation, daily loss limit, market hours
- Market hours enforcement (NYSE 09:30–16:00 ET) with paper-mode bypass
- Order submission via ibapi market orders with order-state lifecycle tracking
- Startup reconciliation: fetch open positions and open orders from IBKR before entering poll loop
- Position reconciliation via `reqPositions()` every 15 minutes with drift flagging
- IBKR heartbeat (60s `reqCurrentTime()`) with force-reconnect on timeout
- Paper trading mode (port toggle) — required before any live deployment
- Secure credential handling (GCP Secret Manager / env vars, validated at startup)
- Structured logging to journald for every scrape, parse, and order attempt

**Should have (differentiators):**
- FIFO lot-assignment for partial close and full close actions
- Partial-fill awareness: accumulate `execDetails()` callbacks until `filled_qty == total_qty`
- Slippage/execution quality tracking (fill price vs signal price)
- Commission capture from `commissionReport` callback
- Dashboard: positions tab (open lots, entry price, current P&L, weight)
- Dashboard: signals tab (recent signals, parse status, action taken)
- Dashboard: system health (last scrape timestamp, IBKR connection status, staleness warning)
- Email/notification on circuit breaker trip or system failure

**Defer to v2+:**
- Email alert parsing as secondary ingestion channel
- Closed-trades P&L report dashboard tab
- Reconciliation mismatch email alerts
- Config hot-reload without process restart
- Commission and slippage reporting UI

**Do not build:**
- Manual order placement UI, limit orders, stop-loss automation, multi-account, options/futures, backtesting, real-time price streaming, automated 2FA

### Architecture Approach

The system is two systemd services sharing a PostgreSQL database. `bravos-trader.service` runs a single Python process with two threads: the main thread owns the schedule loop, Chrome session, parser, risk controller, and DB writes; the IBKR daemon thread owns `EClient.run()`, all EWrapper callbacks, and order/execution DB writes. Cross-thread communication uses a `queue.Queue`. Each thread holds its own psycopg2 connection (connections are not thread-safe). `bravos-dashboard.service` runs uvicorn/FastAPI with a read-only DB connection and htmx polling — it is completely isolated from the trader process and can be deployed or restarted independently. IB Gateway runs as a separate OS process (ideally its own systemd unit) and is a hard prerequisite for the trader service.

**Major components:**
1. Scraper Module — headless Chrome, login session management, post URL diff against DB, raw HTML extraction
2. Parser Module — regex primary path, spaCy NER fallback, action-type keyword mapping, confidence scoring
3. Signal Store (PostgreSQL) — idempotent signal inserts, status lifecycle, audit trail of all raw signal text
4. Risk Controller — synchronous gate that all order paths must pass; enforces all 6 risk controls
5. Size Calculator — stateless arithmetic module; inputs from signal + live account value
6. IBKR Thread (EWrapper + EClient) — connection lifecycle, heartbeat, order submission, fill callbacks, position sync
7. Position Tracker — broker_positions_snapshot UPSERT, internal vs broker reconciliation, drift flagging
8. FastAPI Dashboard — read-only queries, Jinja2 templates, htmx fragment polling

### Critical Pitfalls

1. **Silent zero-result scrapes on session expiry (P1, CRITICAL)** — WordPress membership sites return 200 OK with a login-redirect page when the session expires. Always assert a post-login DOM element is present after each scrape; never trust "no new posts" without confirming authentication state.

2. **Ambiguous action type classification (P4, CRITICAL)** — Bravos title suffixes ("Profit Booking", "Booking Partial Profits", "Reducing Exposure") do not map 1:1 to action types. Maintain an explicit keyword-to-action-type table. For the first 20–30 real signals, route low-confidence parses to manual review rather than direct execution.

3. **Wrong weight extraction = wrong order size (P5, CRITICAL)** — Always extract both old and new weight values and compute delta explicitly (`abs(old_weight - new_weight)`). A naive regex that captures only one value will produce catastrophically wrong order sizes.

4. **CLOSE-WAIT stale socket / silent IBKR failure (P7, CRITICAL)** — IB Gateway connection enters CLOSE-WAIT state; `isConnected()` returns True but API calls go nowhere. Implement 60-second heartbeat (`reqCurrentTime()`) with 5-second callback timeout and force-reconnect. Build this from the start, not as a retrofit.

5. **Risk check bypass (P12, CRITICAL)** — Risk controls implemented ad-hoc per code path will have gaps. Implement a single `RiskGate.check()` that every order path invokes — no exceptions. Log every gate decision with reason.

6. **Order state orphaning on crash (P9, HIGH)** — Write the order to the DB with status `PENDING_SUBMISSION` before calling `placeOrder()`. On startup, `reqOpenOrders()` and reconcile against any `PENDING_SUBMISSION` rows.

---

## Implications for Roadmap

The feature dependency chain and architecture build order align on the same phase structure. The parser is the critical path bottleneck: nothing in the execution pipeline can be validated until it produces correct structured data from real Bravos alerts.

### Phase 1: Infrastructure and Signal Ingestion Pipeline

**Rationale:** PostgreSQL schema and the scraper-to-parser-to-DB pipeline are prerequisites for every other component. Building this first allows the system to start accumulating real signal data immediately, which is needed to validate the parser against actual Bravos post formats.
**Delivers:** Working scraper that logs, deduplicates, and stores raw signals; parser with validated regex patterns; signal audit trail in DB
**Addresses:** Signal ingestion, deduplication, alert parsing, parse failure handling, signal audit trail
**Avoids:** P1 (session expiry), P3 (bot detection), P4 (ambiguous action type), P5 (weight extraction), P6 (duplicate signals)
**Pitfall note:** Parser must be validated against a real signal corpus with confidence scoring before Phase 3 connects it to execution.

### Phase 2: IBKR Connection and Position Baseline

**Rationale:** The IBKR thread, heartbeat logic, and startup reconciliation must be solid before any order submission logic is built on top. Getting connection reliability right early avoids the worst pitfalls (P7, P8, P9) propagating into the execution layer.
**Delivers:** Stable IBKRApp thread with heartbeat/reconnect, startup reconciliation (open positions + open orders fetched on start), `nextValidId` management with Lock
**Addresses:** IBKR connection + startup reconciliation, graceful disconnect/reconnect
**Avoids:** P7 (CLOSE-WAIT), P8 (nextOrderId race), P9 (order state orphaning)
**Research flag:** Standard patterns well-documented in ibkr-connection skill — no additional research phase needed.

### Phase 3: Risk Controls and Order Execution

**Rationale:** Risk gate must be complete and tested before any order submission code is written. This enforces the architectural constraint that all order paths must pass through a single synchronous risk check. Only after risk controls are validated does the execution path get connected to real signal processing.
**Delivers:** RiskController (all 6 controls), SizeCalculator, OrderEngine orchestrating signal→risk→size→submit, market hours gate, order status tracking, order state written to DB before submission
**Addresses:** All risk management controls, order size calculation, order submission, market hours enforcement, order status tracking
**Avoids:** P12 (risk bypass), P8 (order ID race — revisited in order submission), P9 (pre-write before placeOrder)

### Phase 4: Position Reconciliation and Execution Capture

**Rationale:** After orders can be submitted, the system needs to correctly track fills, handle partial fills, and reconcile broker state. This phase closes the loop from order submission to position state.
**Delivers:** Execution capture from `execDetails()` callbacks, partial-fill accumulation, position_lots updates, periodic `reqPositions()` reconciliation, broker_positions_snapshot UPSERT, drift flagging
**Addresses:** Order status tracking (completion), position reconciliation, partial-fill awareness
**Avoids:** P10 (internal state drift), P11 (partial fill mishandling)

### Phase 5: Paper Trading Validation

**Rationale:** End-to-end validation in paper mode is required before live account credentials are used. This phase is not code development — it is structured testing of the full pipeline with real Bravos signals and the paper Gateway.
**Delivers:** Confidence that the full pipeline (scrape → parse → risk → order → fill → reconcile) behaves correctly under real conditions; any parser edge cases surfaced and fixed
**Addresses:** Paper trading mode toggle, parse confidence threshold validation
**Gate:** No live trading until this phase passes without critical failures.

### Phase 6: Dashboard and Alerting

**Rationale:** Dashboard can be built in parallel with Phases 1–5 against the DB schema (read-only), but should be delivered before live trading to enable monitoring. Alerting on system failure (P15) is required for operating the live system unattended.
**Delivers:** FastAPI dashboard (positions, signals, system health tabs), htmx auto-refresh, staleness warning if scrape >15 minutes stale, email/notification on circuit breaker trip or system failure
**Addresses:** Dashboard (positions tab, signals tab, system health), email notifications
**Avoids:** P15 (no alerting on system failure)

### Phase 7: Live Trading Deployment

**Rationale:** Final phase switches from paper to live after Phase 5 validation gate is passed. Deployment hardening (IB Gateway systemd unit, Chrome memory management, IB Gateway nightly restart window handling) belongs here.
**Delivers:** Live account connected, systemd service hardening, Chrome restart schedule, IB Gateway nightly restart handling, secrets via GCP Secret Manager confirmed
**Addresses:** Secure credential storage, paper→live port toggle, deployment configuration
**Avoids:** P2 (ChromeDriver version), P13 (Gateway daily restart), P14 (Chrome memory leak)

### Phase Ordering Rationale

- Schema first: every module writes to PostgreSQL; no module can be tested without tables existing
- Parser before execution: the parser is the hardest component and the linchpin of the entire pipeline; connecting it to execution before it is validated is the single highest risk to live account safety
- IBKR connection before order engine: connection reliability (heartbeat, reconnect, nextValidId) must be solid before order submission logic is layered on
- Risk gate before order submission: enforced by architecture — risk controller must exist and be complete before OrderEngine is built to call it
- Dashboard independent: can be developed in parallel with Phases 2–5 since it is read-only against the DB

### Research Flags

Phases with well-documented patterns — skip `/gsd:research-phase`:
- **Phase 2 (IBKR Connection):** ibkr-connection skill covers all patterns authoritatively
- **Phase 3 (Risk + Execution):** patterns well-understood; ibapi placeOrder model is documented in skill
- **Phase 6 (Dashboard):** FastAPI + htmx + Jinja2 patterns are standard and well-documented
- **Phase 7 (Deployment):** systemd service patterns are standard Linux operations

Phases that may warrant targeted research during planning:
- **Phase 1 (Parser):** Real Bravos post samples needed before regex patterns can be finalized. Parser should be treated as a mini-research problem during implementation — collect 20+ real alert samples and iterate on regex coverage before connecting to execution. Consider a manual-review holding queue for the first 2 weeks of live operation.
- **Phase 5 (Paper Validation):** Not a code research problem but a testing protocol that should be planned explicitly — what constitutes a passing paper trading cycle, how many signals, what failure conditions trigger a return to Phase 3.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Three existing project skills cover the hardest components; choices are well-justified and technology-locked (ibapi is the only IBKR option) |
| Features | HIGH | Clear domain with well-understood requirements; MVP ordering aligns with technical dependency chain; anti-features are explicitly scoped out |
| Architecture | HIGH | Threading model, process boundaries, and build order are fully specified in ARCHITECTURE.md, grounded in ibapi's hard constraints |
| Pitfalls | HIGH | 15 identified pitfalls with specific prevention strategies; critical pitfalls align with known ibapi and Selenium failure modes documented in project skills |

**Overall confidence:** HIGH

### Gaps to Address

- **Bravos post format variation:** Regex patterns are designed from expected format but have not been validated against a corpus of real posts. During Phase 1, collect and sanitize 20+ real alert samples and iterate on parser coverage before connecting to execution. The spaCy fallback is a safety net, not a replacement for validated regex.
- **GCP VM resource sizing:** Chrome (~300MB) + Python process + PostgreSQL on the same VM — confirm the VM has sufficient memory. The architecture is frugal, but should be verified before deployment.
- **IB Gateway nightly restart window exact timing:** Documented as ~11:45pm–12:15am ET but may shift. Validate the exact window before configuring the systemd timer that pauses the main loop.
- **Bravos site anti-bot posture:** Rate limiting and bot detection behavior is assumed to be manageable at 5-minute polling intervals with proper user-agent and session reuse. If the site adds Cloudflare challenge pages, the scraper will need additional handling. Monitor for P3 in Phase 1 before committing to the schedule interval.

---

## Sources

### Primary (HIGH confidence)
- `ibkr-connection` skill (project-local) — EWrapper/EClient threading model, heartbeat, nextValidId, connection lifecycle, error handling
- `trade-database-review` skill (project-local) — 6-table schema (signals, position_lots, lot_actions, orders, executions, broker_positions_snapshot), UPSERT patterns
- `selenium-scraper` skill (project-local) — anti-detection patterns, Chrome startup retry, 3-tier click fallback, session management
- IB TWS API official documentation — ibapi threading requirements, placeOrder, reqPositions, reqOpenOrders callbacks
- PostgreSQL 15 documentation — UNIQUE constraint behavior, ON CONFLICT DO NOTHING semantics

### Secondary (MEDIUM confidence)
- spaCy 3.x documentation — en_core_web_sm NER capabilities and CPU performance characteristics
- FastAPI + htmx community patterns — server-side rendering with htmx polling for live updates
- systemd service configuration — service dependency ordering (After=), restart policies, journald logging

### Tertiary (LOW confidence / needs validation)
- Bravos post format and title suffix keywords — inferred from PROJECT.md description; must be validated against real samples during Phase 1 implementation
- IB Gateway nightly restart window timing — documented as approximately 11:45pm–12:15am ET; validate against actual Gateway behavior in deployment environment

---

*Research completed: 2026-05-01*
*Ready for roadmap: yes*
