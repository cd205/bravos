# Stack Research — Bravos Trading System

## Recommended Stack

### Web Scraping
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Browser automation | Selenium | 4.x | HIGH |
| Driver management | webdriver-manager | 4.x | HIGH |
| Browser | Chrome/Chromium (headless) | latest stable | HIGH |

**Rationale:** Existing `selenium-scraper` skill encodes battle-tested production patterns (anti-detection, 3-tier click fallback, Chrome startup retry, tab-based extraction). No reason to switch to Playwright — Selenium 4 has async support and the skill patterns are directly reusable.

**NOT:** Playwright (different API, would discard existing skill knowledge), requests/BeautifulSoup (JS-rendered content requires a real browser).

---

### Signal Parsing
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Primary parser | Regex (re module) | stdlib | HIGH |
| NLP fallback | spaCy | 3.x | MEDIUM |
| Language model | en_core_web_sm | latest | MEDIUM |

**Rationale:** Bravos alerts come from a known, consistent source. Regex patterns for ticker ($TICKER), price ("at $XX.XX"), weight ("weight of X to Y"), and action type (title suffix keywords) are faster, cheaper, and more predictable than LLMs. spaCy `en_core_web_sm` provides NER fallback if format drifts — runs on CPU, no GPU required.

**Pattern approach:**
- Ticker: `\$([A-Z]{1,5})` in title and body
- Price: `at \$(\d+\.\d{2})`
- Weight change: `weight of (\d+) to (\d+)`
- Action type: title suffix keywords ("Profit Booking" → partial_close, "Breakdown" → close, "Technical Strength" / "Agriculture" → open/add)

**NOT:** LLM-based parsing (expensive, slow, non-deterministic for structured extraction from consistent source).

---

### IBKR Integration
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Broker API | ibapi (official) | match Gateway version | HIGH |
| Threading | Python threading module | stdlib | HIGH |

**Rationale:** ibapi is the only option for IBKR programmatic access. Existing `ibkr-connection` skill covers EWrapper/EClient architecture, order placement, account data, error handling comprehensively.

**Critical:** ibapi comes from IB's developer portal as a zip, NOT PyPI (or PyPI version must exactly match IB Gateway version). Install from official source: [TWS API](https://www.interactivebrokers.com/en/trading/api-software.php).

**NOT:** ib_insync (adds async complexity, less control, extra dependency); unofficial PyPI packages.

---

### Database
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Database | PostgreSQL | 15+ | HIGH |
| Python client | psycopg2-binary | 2.9.x | HIGH |
| Migrations | Alembic | 1.x | HIGH |

**Rationale:** psycopg2 directly — no ORM. The `trade-database-review` skill establishes a 6-table schema (signals, position_lots, lot_actions, orders, executions, broker_positions_snapshot) with explicit JOIN and UPSERT logic. An ORM would obscure that logic and fight the schema design. asyncpg is wrong because the rest of the stack is synchronous.

**Existing `postgres-patterns` skill:** Covers schema design, indexing strategy, row-level security, Supabase best practices — use throughout.

**NOT:** SQLAlchemy ORM (over-engineered for explicit schema), asyncpg (sync stack), SQLite (no concurrency, not production-grade for financial data).

---

### Scheduling / Process Management
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| In-process scheduler | schedule | 1.x | HIGH |
| Process manager | systemd | OS-level | HIGH |
| Process supervision | supervisord (alternative) | 4.x | MEDIUM |

**Rationale:** A single persistent Python process with a 5-minute polling loop managed by systemd is the correct Linux pattern for a GCP VM. `schedule` library handles the interval cleanly within the process. systemd provides auto-restart on crash, logging via journald, and boot-time startup.

**NOT:** Celery (massively over-engineered for a single interval task), APScheduler (unnecessary complexity), cron (can't manage a persistent ibapi connection).

---

### Web Dashboard
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Web framework | FastAPI | 0.115.x | HIGH |
| Templating | Jinja2 | 3.x | HIGH |
| Frontend reactivity | htmx | 2.x (CDN) | HIGH |
| CSS | TailwindCSS | CDN | MEDIUM |
| ASGI server | uvicorn | 0.x | HIGH |

**Rationale:** FastAPI + Jinja2 + htmx avoids any Node.js/React build toolchain on the VM. htmx auto-refresh (polling every 10-30s) keeps the positions/signals table live without websockets complexity. Served by uvicorn as a separate systemd service alongside the trading process.

**NOT:** Streamlit (execution model incompatible with background process state — can't share ibapi connection), Django (too heavy), React/Next.js (requires Node.js build pipeline on server).

---

### Secrets Management
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Primary | GCP Secret Manager | GCP SDK | HIGH |
| Fallback | .env file (not committed) + python-dotenv | 1.x | HIGH |

**Rationale:** GCP Secret Manager is native to the deployment environment and the right production solution. python-dotenv provides a local dev fallback. Credentials (Bravos login, IBKR account) must never appear in code or git.

**NOT:** Hardcoded credentials, plaintext config files in repo.

---

### Notifications / Alerting
| Component | Choice | Version | Confidence |
|-----------|--------|---------|------------|
| Email | smtplib (stdlib) or sendgrid | stdlib / 6.x | MEDIUM |
| Logging | Python logging + structlog | stdlib / 23.x | HIGH |

---

## Full Dependency List

```
# Core
selenium==4.x
webdriver-manager==4.x
psycopg2-binary==2.9.x
alembic==1.x
fastapi==0.115.x
uvicorn==0.x
jinja2==3.x
schedule==1.x
python-dotenv==1.x
structlog==23.x

# NLP (optional fallback)
spacy==3.x  # + python -m spacy download en_core_web_sm

# GCP
google-cloud-secret-manager==2.x

# ibapi — install from IB developer portal, not PyPI
```

## Deployment Architecture (GCP VM)

```
systemd services:
  bravos-trader.service    → main Python process (scraper + ibapi + scheduler)
  bravos-dashboard.service → FastAPI/uvicorn dashboard

IB Gateway:
  Runs as separate process (existing pattern from ibkr-connection skill)
  Connects on localhost:4001 (paper) / localhost:4000 (live)

Chrome/Chromium:
  Headless mode
  xvfb if needed (virtual display)
```
