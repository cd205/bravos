# Phase 2: Signal Ingestion - Context

**Gathered:** 2026-05-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Selenium-based scraper logs into bravosresearch.com, polls every 5 minutes for new Trade Alert posts, parses each post into structured signal fields with a confidence score, and stores the full audit trail in PostgreSQL. No IBKR connection, no order execution, no dashboard — signal ingestion only.

</domain>

<decisions>
## Implementation Decisions

### Session Management
- **D-01:** Single persistent Chrome driver for the full trading day — one instance stays open across all 5-minute cycles. New driver created at daemon startup; restarted only on crash or confirmed session expiry.
- **D-02:** Session expiry detection: after each scrape cycle, check if the login form is present on the current page. If found → session has expired.
- **D-03:** On session expiry: re-authenticate, then continue the current scrape cycle (do not skip). New posts since last cycle should still be picked up.
- **D-04:** Re-auth attempts: 3 attempts before logging a CRITICAL error and skipping the current cycle. System keeps running — it does not crash or exit.

### Post Detection
- **D-05:** Deduplication via URL only — use the `UNIQUE` constraint on `signals.post_url`. Each cycle attempts INSERT; `ON CONFLICT DO NOTHING` handles already-seen posts. No date cursor.
- **D-06:** Scrape the 10 most recent posts per cycle from the Trade Alert category page. Page 1 only — no pagination across cycles.
- **D-07:** Filter by Trade Alert category at the URL level (navigate to category-filtered URL), not by scraping all posts and filtering after.
- **D-08:** When site is unreachable or returns 0 posts: log a WARNING, skip the cycle, continue. No crash, no DB error record. Phase 7 dashboard will surface consecutive scrape failures via last_scrape_at staleness check.

### Parser
- **D-09:** Regex-first parsing (as specified in CLAUDE.md). spaCy NLP as fallback when regex extraction is incomplete. Regex patterns per CLAUDE.md: `\$([A-Z]{1,5})` (ticker), `at \$(\d+\.\d{2})` (price), `weight of (\d+) to (\d+)` (weight change), title suffix keywords for action type.
- **D-10:** Confidence scoring based on field completeness: all 4 required fields (ticker, action_type, weight_from, weight_to) extracted = `'high'`. 3 of 4 = `'medium'`. Fewer than 3 = `'low'`. Reference price is optional and does not affect confidence.
- **D-11:** Low-confidence signals: stored verbatim (raw HTML + extracted fields + `confidence='low'`), never forwarded to order execution. Operator reviews via Phase 7 dashboard.
- **D-12:** Parser is a standalone module `bravos/ingestion/parser.py` with unit tests in `tests/test_parser.py`. Tests feed known-format strings and assert field extraction. Testable independently of the scraper.

### Scraper Process Structure
- **D-13:** Long-running daemon using the `schedule` library (already in CLAUDE.md tech stack). Fires the scrape function every 5 minutes. Single persistent Chrome driver held in the daemon.
- **D-14:** Module structure: `bravos/ingestion/` package. Files: `__init__.py`, `scraper.py` (Selenium driver + login + post detection), `parser.py` (field extraction + confidence scoring).
- **D-15:** Database connection: `psycopg2` connection opened per 5-minute cycle, committed, and closed. Cloud SQL proxy (already running as systemd service) handles infra-level connection management.
- **D-16:** Entry point: `scripts/run_ingestion.py` — minimal script that imports and starts the daemon. Daemon logic lives in `bravos/ingestion/`; entry point is the run target for manual invocation and future systemd wrapping.

### Claude's Discretion
- Exact HTML element selectors for the bravos site (post list, post body) — researcher should investigate
- Logging format and log levels (structlog vs stdlib logging)
- Graceful shutdown handling (SIGTERM → quit Chrome driver cleanly)
- Exact schedule library invocation pattern and main loop structure

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project constraints and stack
- `.planning/REQUIREMENTS.md` — INGST-01 through INGST-07, AUDIT-01 through AUDIT-06 (all Phase 2 requirements)
- `CLAUDE.md` — tech stack decisions: Selenium 4.x, webdriver-manager, schedule 1.x, regex patterns, spaCy fallback, `en_core_web_sm`

### Existing code and patterns
- `.claude/skills/selenium-scraper/SKILL.md` — production Selenium patterns: driver setup, login automation (3-tier click), tab extraction, DB write pattern
- `bravos/config/settings.py` — `SCRAPE_INTERVAL_SECONDS`, `BRAVOS_BASE_URL`, `TRADING_MODE`
- `bravos/config/secrets_config.py` — `get_secret()` for loading `bravos-site-username` and `bravos-site-password` from GCP Secret Manager
- `infra/schema.sql` — `signals` table schema: `post_url UNIQUE`, `ticker`, `action_type`, `weight_from`, `weight_to`, `reference_price`, `confidence`, `raw_html`, timestamps

### Phase 1 infrastructure decisions
- `.planning/phases/01-infrastructure-setup/01-DECISIONS.md` — Chrome headless setup, Python 3.13 + miniconda, secrets loading pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bravos/config/secrets_config.py`: `get_secret(secret_id)` — use to load site username and password at daemon startup
- `bravos/config/settings.py`: `SCRAPE_INTERVAL_SECONDS = 300` — use as the schedule interval; `BRAVOS_BASE_URL` for constructing target URLs
- `scripts/verify_chrome.py`: Chrome headless setup flags — replicate anti-detection options in scraper's `setup_chrome_driver()`
- `.claude/skills/selenium-scraper/SKILL.md`: `setup_chrome_driver()`, `automated_login()`, DB write pattern — adapt directly

### Established Patterns
- Secrets loaded from GCP Secret Manager via `get_secret()`, never from env vars or files
- psycopg2 for DB access; connection string uses `BRAVOS_DB_PASSWORD` env var (see `tests/conftest.py`)
- Python package structure: `bravos/<module>/` pattern established by `bravos/config/`

### Integration Points
- New code connects to `signals` table (already defined in `infra/schema.sql`)
- Cloud SQL Auth Proxy already running on `127.0.0.1:5432` (via `infra/cloud-sql-proxy.service`)
- Daemon entry point at `scripts/run_ingestion.py` — will become the systemd `ExecStart` in Phase 8

</code_context>

<specifics>
## Specific Ideas

- Bravos site is WordPress/WooCommerce membership-gated; Trade Alert category URL likely `/research/?category=trade-alerts` or similar — researcher should confirm exact category slug
- Action type inference from title suffix: "Profit Booking" → `partial_close`, "Breakdown" → `close`, "Technical Strength" / agriculture-related keywords → `open` or `add` (per CLAUDE.md)
- Weight pattern `weight of (\d+) to (\d+)` — if weight_to > weight_from → open/add; if weight_to < weight_from → partial_close; if weight_to = 0 → close (cross-check with title suffix)

</specifics>

<deferred>
## Deferred Ideas

- Email parsing as secondary signal channel — INGST-V2-01 (v2 requirement, separate phase)
- Stale signal detection (>2 hours old) — INGST-V2-02 (v2 requirement)
- Scrape error records written to DB — mentioned during discussion, deferred; Phase 7 dashboard covers staleness detection

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-signal-ingestion*
*Context gathered: 2026-05-08*
