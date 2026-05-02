<!-- GSD:project-start source:PROJECT.md -->
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
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
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
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
