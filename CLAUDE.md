## Project

**Bravos Trading System**

An automated trading system that monitors bravosresearch.com for new trade alerts, parses the alert content to extract structured trade signals, and executes corresponding orders in Interactive Brokers (IBKR). The system tracks all signals, open positions, and closed positions in a PostgreSQL database, and surfaces a dashboard for monitoring activity and P&L.

**Core Value:** When a new trade alert is posted on Bravos Research, the correct order is placed in IBKR within minutes — without manual intervention.

### Constraints

- **Tech Stack**: Python — ibapi requires Python; Selenium already used for scraping
- **Broker**: IBKR only — ibapi is the interface
- **Instruments**: Equities only (stocks, ETFs) — v1 scope decision
- **Deployment**: GCP VM (Linux) — must run headless; Chrome/Chromium for Selenium
- **Security**: Credentials must never appear in code or unencrypted files — use environment variables or secrets manager
- **Market Hours**: Order placement during regular market hours only (risk control)
- **Polling**: 5-minute scrape interval — balance between timeliness and rate limiting

## Technology Stack

## Recommended Stack
### Web Scraping
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Browser automation | Selenium | 4.x | HIGH |
| Driver management | webdriver-manager | 4.x | HIGH |
| Browser | Chrome/Chromium (headless) | latest stable | HIGH |
### Signal Parsing
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Primary parser | Regex (re module) | stdlib | HIGH |
| NLP fallback | spaCy | 3.x | MEDIUM |
| Language model | en_core_web_sm | latest | MEDIUM |
- Ticker: `\$([A-Z]{1,5})` in title and body
- Price: `at \$(\d+\.\d{2})`
- Weight change: `weight of (\d+) to (\d+)`
- Action type: title suffix keywords ("Profit Booking" → partial_close, "Breakdown" → close, "Technical Strength" / "Agriculture" → open/add)
### IBKR Integration
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Broker API | ibapi (official) | match Gateway version | HIGH |
| Threading | Python threading module | stdlib | HIGH |
### Database
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Database | PostgreSQL | 15+ | HIGH |
| Python client | psycopg2-binary | 2.9.x | HIGH |
| Migrations | Alembic | 1.x | HIGH |
### Scheduling / Process Management
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| In-process scheduler | schedule | 1.x | HIGH |
| Process manager | systemd | OS-level | HIGH |
| Process supervision | supervisord (alternative) | 4.x | MEDIUM |
### Web Dashboard
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Web framework | FastAPI | 0.115.x | HIGH |
| Templating | Jinja2 | 3.x | HIGH |
| Frontend reactivity | htmx | 2.x (CDN) | HIGH |
| CSS | TailwindCSS | CDN | MEDIUM |
| ASGI server | uvicorn | 0.x | HIGH |
### Secrets Management
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Primary | GCP Secret Manager | GCP SDK | HIGH |
| Fallback | .env file (not committed) + python-dotenv | 1.x | HIGH |
### Notifications / Alerting
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Email | smtplib (stdlib) or sendgrid | stdlib / 6.x | MEDIUM |
| Logging | Python logging + structlog | stdlib / 23.x | HIGH |
## Full Dependency List
# Core
# NLP (optional fallback)
# GCP
# ibapi — install from IB developer portal, not PyPI
## Deployment Architecture (GCP VM)

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (cd205/bravos) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
