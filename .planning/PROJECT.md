# Bravos Trading System

## What This Is

An automated trading system that monitors bravosresearch.com for new trade alerts, parses the alert content to extract structured trade signals, and executes corresponding orders in Interactive Brokers (IBKR). The system tracks all signals, open positions, and closed positions in a PostgreSQL database, and surfaces a dashboard for monitoring activity and P&L.

## Core Value

When a new trade alert is posted on Bravos Research, the correct order is placed in IBKR within minutes — without manual intervention.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Scraper logs into bravosresearch.com using secure credentials and polls for new Trade Alert posts every 5 minutes
- [ ] New alerts are parsed from article prose to extract: ticker, action type (open/add/partial close/close), price, weight change, and entry context
- [ ] Parsed signals are stored in PostgreSQL with full audit trail
- [ ] Order size is calculated as: (weight units × configured % of portfolio) × current account value
- [ ] Orders are submitted to IBKR automatically via ibapi
- [ ] Risk controls enforced before order submission: max open positions, max allocation per trade, daily loss limit
- [ ] Positions (open and closed) are tracked and reconciled against IBKR account state
- [ ] Web dashboard shows current signals, open positions, closed positions, and P&L
- [ ] System runs on GCP VM alongside IB Gateway
- [ ] Credentials stored securely (not in code or plaintext config)
- [ ] Paper trading mode supported for testing before live deployment
- [ ] Email alert parsing as a secondary signal ingestion channel

### Out of Scope

- Options, futures, forex — equities (stocks and ETFs) only in v1
- Manual order placement UI — system is fully automated
- Multi-account management — single IBKR account per deployment
- Strategy backtesting — system follows Bravos signals, not its own analysis
- Mobile app — web dashboard only

## Context

**Alert source:** bravosresearch.com — a WordPress/WooCommerce membership site. Trade alerts are published as blog posts under the "Trade Alert" category. Each post contains prose describing the trade with: ticker symbol, action type embedded in the title suffix (e.g. "Profit Booking", "Technical Strength", "Breakdown"), current price, weight change (their position sizing unit), and original entry details.

**Alert action types observed:**
- Opening a new position
- Adding to an existing position (scale-in)
- Booking partial profits (reduce weight)
- Closing entire position

**Weight system:** Bravos uses integer weights (e.g. 1–10) to represent position size. Each weight unit maps to a user-configured percentage of portfolio (e.g. 1 weight = 5% → weight 5 = 25% of portfolio).

**Scraping approach:** Selenium-based scraper logs in, navigates to /research/, filters by "Trade Alert" category, detects new posts since last scrape by comparing post URLs/dates, clicks into each new post, and extracts structured data from prose using NLP/regex patterns.

**Existing infrastructure:** User has an existing GCP VM running IBKR Gateway. This project deploys to a new GCP VM with a similar setup (IB Gateway + Python ibapi).

**Skills available:**
- `selenium-scraper` — login patterns, anti-detection, tab-based extraction, PostgreSQL writes
- `ibkr-connection` — EWrapper/EClient architecture, order placement, account data
- `postgres-patterns` — schema design, indexing, Supabase best practices
- `trade-database-review` — trading data system design

## Constraints

- **Tech Stack**: Python — ibapi requires Python; Selenium already used for scraping
- **Broker**: IBKR only — ibapi is the interface
- **Instruments**: Equities only (stocks, ETFs) — v1 scope decision
- **Deployment**: GCP VM (Linux) — must run headless; Chrome/Chromium for Selenium
- **Security**: Credentials must never appear in code or unencrypted files — use environment variables or secrets manager
- **Market Hours**: Order placement during regular market hours only (risk control)
- **Polling**: 5-minute scrape interval — balance between timeliness and rate limiting

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Selenium scraping over API | No public API from Bravos Research; site is membership-gated | — Pending |
| % of portfolio per weight unit | Scales with account size; configurable at runtime | — Pending |
| Add to position on duplicate BUY signal | User wants to scale in; aligns with Bravos weight system | — Pending |
| PostgreSQL for all state | Existing postgres-patterns skill; auditable, queryable | — Pending |
| Both paper and live IBKR accounts | Paper first for testing; promotes to live when validated | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-01 after initialization*
