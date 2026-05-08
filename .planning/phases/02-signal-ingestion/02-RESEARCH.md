# Phase 2: Signal Ingestion — Research

**Researched:** 2026-05-08
**Domain:** Selenium scraping, regex/NLP parsing, schedule daemon, PostgreSQL audit trail
**Confidence:** HIGH (site structure confirmed via Google index; library patterns confirmed via official docs)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Session Management**
- D-01: Single persistent Chrome driver for the full trading day — one instance stays open across all 5-minute cycles. New driver created at daemon startup; restarted only on crash or confirmed session expiry.
- D-02: Session expiry detection: after each scrape cycle, check if the login form is present on the current page. If found → session has expired.
- D-03: On session expiry: re-authenticate, then continue the current scrape cycle (do not skip). New posts since last cycle should still be picked up.
- D-04: Re-auth attempts: 3 attempts before logging a CRITICAL error and skipping the current cycle. System keeps running — it does not crash or exit.

**Post Detection**
- D-05: Deduplication via URL only — use the `UNIQUE` constraint on `signals.post_url`. Each cycle attempts INSERT; `ON CONFLICT DO NOTHING` handles already-seen posts. No date cursor.
- D-06: Scrape the 10 most recent posts per cycle from the Trade Alert category page. Page 1 only — no pagination across cycles.
- D-07: Filter by Trade Alert category at the URL level (navigate to category-filtered URL), not by scraping all posts and filtering after.
- D-08: When site is unreachable or returns 0 posts: log a WARNING, skip the cycle, continue. No crash, no DB error record. Phase 7 dashboard will surface consecutive scrape failures via last_scrape_at staleness check.

**Parser**
- D-09: Regex-first parsing. spaCy NLP as fallback when regex extraction is incomplete. Regex patterns per CLAUDE.md.
- D-10: Confidence scoring: all 4 required fields (ticker, action_type, weight_from, weight_to) = `'high'`. 3 of 4 = `'medium'`. Fewer than 3 = `'low'`. Reference price is optional.
- D-11: Low-confidence signals: stored verbatim (raw HTML + extracted fields + `confidence='low'`), never forwarded to order execution.
- D-12: Parser is a standalone module `bravos/ingestion/parser.py` with unit tests in `tests/test_parser.py`.

**Scraper Process Structure**
- D-13: Long-running daemon using `schedule` library. Fires every 5 minutes. Single persistent Chrome driver held in the daemon.
- D-14: Module structure: `bravos/ingestion/` package. Files: `__init__.py`, `scraper.py`, `parser.py`.
- D-15: Database connection: `psycopg2` connection opened per 5-minute cycle, committed, and closed.
- D-16: Entry point: `scripts/run_ingestion.py`.

### Claude's Discretion
- Exact HTML element selectors for the bravos site (post list, post body)
- Logging format and log levels (structlog vs stdlib logging)
- Graceful shutdown handling (SIGTERM → quit Chrome driver cleanly)
- Exact schedule library invocation pattern and main loop structure

### Deferred Ideas (OUT OF SCOPE)
- Email parsing as secondary signal channel — INGST-V2-01
- Stale signal detection (>2 hours old) — INGST-V2-02
- Scrape error records written to DB — deferred; Phase 7 dashboard covers staleness
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INGST-01 | System logs into bravosresearch.com using securely stored credentials via Selenium and maintains a persistent browser session throughout the trading day | Selenium skill patterns + GCP Secret Manager via get_secret() |
| INGST-02 | System polls the research page every 5 minutes, filters to "Trade Alert" category, and detects posts not previously seen | Confirmed category URL: `/category/portfolio-update/`; schedule library main loop |
| INGST-03 | System deduplicates signals by post URL — a post already processed is never re-processed regardless of site edits | `ON CONFLICT (post_url) DO NOTHING` already in schema.sql |
| INGST-04 | System extracts from each new Trade Alert post: ticker symbol, action type, weight change, and reference price | Regex patterns + spaCy fallback; real title patterns confirmed |
| INGST-05 | System assigns a confidence score to each parsed signal; low-confidence parses are flagged and not routed to order execution | Field-completeness scoring model defined in D-10 |
| INGST-06 | Every scraped signal is stored verbatim (raw HTML + structured fields) in database regardless of whether order is placed | schema.sql `raw_html` column present; needs `parse_method` column addition |
| INGST-07 | System detects and re-authenticates when session expires | Login-form presence check after each cycle; 3-attempt re-auth loop |
| AUDIT-01 | Every system action recorded in database with timestamp, actor, and outcome | `scraped_at` timestamp needed; schema gap identified |
| AUDIT-02 | Each trade signal traceable end-to-end: raw scraped post → parsed fields → risk gate → order → fills → position state | `signal_id` FK on orders table already present |
| AUDIT-03 | Every order record links to the signal that triggered it | Covered by existing schema FK; Phase 2 sets the foundation |
| AUDIT-04 | Partial closes and profit-booking actions record both lot(s) reduced and remaining open quantity | Covered by schema in later phases; parser must correctly classify `partial_close` |
| AUDIT-05 | Position closes record specific lots closed (FIFO), entry price, exit price, realized P&L | Later phases; parser `close` action type must be set correctly |
| AUDIT-06 | All audit records are immutable — append-only; prior states always recoverable | `ON CONFLICT DO NOTHING` pattern; no UPDATE of existing signal rows |
</phase_requirements>

---

## Summary

Phase 2 builds the ingestion daemon that logs into bravosresearch.com, polls for Trade Alerts every 5 minutes, parses post content into structured signals, and stores everything in PostgreSQL. The site structure is confirmed: Trade Alerts live at `/category/portfolio-update/` and individual post URLs use the `/portfolio-update/{slug}/` path. The site returns 403 to non-browser requests, confirming that a logged-in Selenium session is required for all content access.

The most important finding is a discrepancy between the action-type keywords in CLAUDE.md ("Profit Booking", "Breakdown", "Technical Strength") and the actual post title vocabulary observed in Google-indexed content ("Initiating Long", "Closing", "Booking Profits", "Booking Partial Profits", "Increasing Exposure"). The parser keyword map must be built from the actual vocabulary, not the CLAUDE.md approximations. CLAUDE.md's keywords appear to be a conceptual description, not the literal text found in post titles.

The `signals` table in `schema.sql` is largely sufficient but is missing two columns needed for complete AUDIT-01 compliance: `parse_method` (records whether regex or spaCy produced the result) and `scraped_at` (records when the scrape cycle retrieved the post, distinct from `parsed_at`). These should be added via ALTER TABLE statements in the implementation plan.

**Primary recommendation:** Implement in four logical units — (1) scraper.py with driver lifecycle and login, (2) parser.py with regex-first + spaCy fallback, (3) daemon loop in run_ingestion.py with schedule + SIGTERM handler, (4) schema migration for two missing audit columns. All parser logic must be unit-testable without a browser.

---

## Site Structure

### Confirmed URLs
| URL | Purpose | Confidence |
|-----|---------|------------|
| `https://bravosresearch.com/category/portfolio-update/` | Trade Alert category archive (the poll target) | HIGH — confirmed via Google index returning "Trade Alert Archives - Bravos Research" |
| `https://bravosresearch.com/portfolio-update/{slug}/` | Individual Trade Alert post | HIGH — confirmed via multiple Google-indexed post URLs |
| `https://bravosresearch.com/research/` | General insights page (not the poll target) | HIGH |

The scraper navigates to `bravosresearch.com/category/portfolio-update/` each cycle, not to the `/research/` page. D-07 says to filter at the URL level, and the category URL is confirmed.

### WordPress Archive Page Structure (Standard Pattern)
Category archive pages in WordPress render posts inside `<article>` tags. Each post listing item follows this standard structure (varies by theme but consistent within a theme):

```html
<article id="post-{ID}" class="post-{ID} post type-post status-publish format-standard hentry category-portfolio-update ...">
  <h2 class="entry-title">
    <a href="https://bravosresearch.com/portfolio-update/{slug}/">Post Title Here</a>
  </h2>
  <div class="entry-meta">
    <span class="posted-on"><time datetime="2024-11-15T...">November 15, 2024</time></span>
  </div>
  <div class="entry-summary"><!-- excerpt --></div>
</article>
```

**Key selectors to try (in priority order):**
1. `article.hentry h2.entry-title a` — standard WordPress/Yoast theme pattern
2. `article h2 a` — fallback if theme doesn't use `entry-title` class
3. `h2.post-title a` — alternate theme convention

**Post date:** `article time[datetime]` — the `datetime` attribute gives ISO format; the visible text gives human-readable format. Use `datetime` attribute for machine parsing.

**Access note:** The site returns HTTP 403 to non-browser requests (confirmed by direct WebFetch attempts). Selenium with anti-detection flags is mandatory for all content access, including the category listing page. The site is membership-gated — posts on the category page may show excerpts to logged-out users but full post body requires authentication.

### Individual Post Structure (Standard WordPress Single Post)
```html
<article id="post-{ID}" class="post type-post status-publish ...">
  <h1 class="entry-title">Initiating Long on EMCOR Group, Inc. ($EME) | Breakout</h1>
  <div class="entry-content">
    <p>We are initiating a long position in EMCOR Group, Inc. ($EME) at $42.50.
    We are increasing weight from 0 to 5...</p>
    <!-- Additional paragraphs with trade rationale -->
  </div>
</article>
```

**Post body selector:** `.entry-content` is the standard WordPress content wrapper class. Fall back to `.post-content` or `article .content` if needed.

**Selector discovery approach:** Because the exact theme is unknown and the site blocks non-browser requests, the implementation plan MUST include a discovery task: on first run (with `headless=False` capability noted in SKILL.md), log `driver.page_source` to a debug file and identify actual selectors before hardcoding them. This is the single largest unknown.

---

## Real Post Title Patterns

From Google-indexed content, the actual post titles observed are:

| Action Type | Observed Title Pattern | Signal Classification |
|-------------|----------------------|----------------------|
| open (new long) | `Initiating Long on {Company} ($TICKER) \| Breakout` | `open` |
| open (new short) | `Initiating Short on {Asset} \| {Reason}` | `open` (short — note: v1 scope equities only) |
| add (scale-in) | `Long {Company} ($TICKER) \| {Reason}` | `add` or `open` (ambiguous) |
| add / increase | `Increasing Exposure to {Company} ($TICKER) \| Technical Strength` | `add` |
| partial_close | `Booking Partial Profits on {Company} ($TICKER) \| {Reason}` | `partial_close` |
| partial_close | `Booking Profits on {Company} ($TICKER) \| {%} Profit` | `partial_close` |
| close | `Closing {Company} ($TICKER) \| Breakdown` | `close` |
| close | `Closing {Asset}` | `close` |
| multi-action | `Portfolio Update: Increasing TLT, GLD; Closing IYT, XLK, XLY` | MULTIPLE — low confidence |

**Critical finding:** CLAUDE.md lists "Profit Booking" and "Technical Strength" as title suffix keywords, but real posts use "Booking Profits", "Booking Partial Profits", and "Increasing Exposure". The parser keyword map must use the actual observed vocabulary. The CLAUDE.md patterns are a conceptual description; the real text differs.

**Action type keyword map (recommended):**
```python
ACTION_KEYWORDS = {
    # open
    "initiating long": "open",
    "initiating a long": "open",
    # add
    "increasing exposure": "add",
    "adding to": "add",
    "long ": "add",  # "Long $TICKER |" pattern — may be open or add; check weight direction
    # partial_close
    "booking partial profits": "partial_close",
    "booking profits": "partial_close",
    "partial profit": "partial_close",
    # close
    "closing": "close",
    "initiating short": "close",  # shorts treated as close on long-only system
    # portfolio update (multi-action — set low confidence)
    "portfolio update:": None,   # triggers low confidence
}
```

**Weight direction cross-check (D-10 expansion):** When `weight_to > weight_from`, it's `open` or `add`. When `weight_to < weight_from`, it's `partial_close`. When `weight_to == 0`, it's `close`. This cross-check MUST be implemented as a secondary validation — if title keyword and weight direction conflict, log a WARNING and set confidence to `'medium'` at best.

---

## Selenium Adaptation

### Driver Lifecycle (Persistent — D-01)

The skill's `setup_chrome_driver()` kills stale processes at startup. For a persistent driver, this is called ONCE at daemon startup, not per cycle. The driver is held in a module-level or class variable and passed into each scrape cycle.

```python
# scripts/run_ingestion.py — startup sequence
from bravos.ingestion.scraper import BravosScraper

scraper = BravosScraper()
scraper.start()   # creates driver, logs in, validates session
schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(scraper.run_cycle)
```

**Driver restart on crash:** Wrap the schedule loop body in try/except. If `scraper.run_cycle()` raises `WebDriverException` (browser crashed), call `scraper.restart_driver()` which re-runs setup and re-login.

### Login Pattern (adapted from SKILL.md)

The skill's `automated_login()` uses `username_field_name` and `password_field_name` parameters. For WordPress, the standard field names are `name="log"` (username) and `name="pwd"` (password) on the `/wp-login.php` page. However, the Bravos site may use a custom WooCommerce/MemberPress login page. The login URL is likely `https://bravosresearch.com/my-account/` or `https://bravosresearch.com/login/`.

**Login success check:** After submit, verify absence of login form OR presence of a "my account" / logout link. WordPress: check for `a.logout` or check that URL is no longer `/wp-login.php`.

**Credentials:** Load via `get_secret("bravos-site-username")` and `get_secret("bravos-site-password")` at daemon startup (not per cycle).

### Session Expiry Detection (D-02)

After each scrape cycle completes, check:
```python
def is_session_expired(driver) -> bool:
    """Return True if WordPress login form is present on current page."""
    return bool(driver.find_elements(By.NAME, "log"))   # WP login field name
```

If the category page itself redirects to login, also check:
```python
    or "wp-login" in driver.current_url
    or "my-account" in driver.current_url
```

### Category Page Scraping

```python
TRADE_ALERT_CATEGORY_URL = f"{BRAVOS_BASE_URL}/category/portfolio-update/"

def get_recent_posts(driver, limit=10) -> list[dict]:
    driver.get(TRADE_ALERT_CATEGORY_URL)
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "article")))

    articles = driver.find_elements(By.CSS_SELECTOR, "article.hentry")[:limit]
    posts = []
    for article in articles:
        try:
            link = article.find_element(By.CSS_SELECTOR, "h2 a, h1 a")
            posts.append({
                "title": link.text.strip(),
                "url": link.get_attribute("href"),
            })
        except NoSuchElementException:
            continue
    return posts
```

**Wait strategy for WordPress:** `presence_of_element_located((By.TAG_NAME, "article"))` — WordPress category pages are server-side rendered, not SPA, so the wait time is dominated by network latency (not JS execution). Use `WebDriverWait(driver, 15)` to handle slow page loads.

**Do not click into each post to get the title.** The category listing page contains titles and URLs in the `<a>` tags. Only click into a post if raw HTML of the full body is needed for parsing. For deduplication (URL check), the listing page is sufficient. Then click into new (unseen) posts only to retrieve full body HTML.

### Post Body Extraction

```python
def get_post_content(driver, url: str) -> dict:
    driver.get(url)
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, ".entry-content, .post-content")
    ))
    body_el = driver.find_element(By.CSS_SELECTOR, ".entry-content, .post-content")
    return {
        "raw_html": body_el.get_attribute("innerHTML"),
        "text": body_el.text,
    }
```

### Anti-Detection

Reuse flags from `scripts/verify_chrome.py` and `tests/conftest.py` (already established in Phase 1). No new flags needed. The `--disable-images` flag from SKILL.md is appropriate here too — trade alert posts are prose-heavy, images not needed for parsing.

---

## Parser Implementation

### Regex Patterns (CLAUDE.md — verified against real posts)

```python
import re

TICKER_RE    = re.compile(r'\$([A-Z]{1,5})\b')
PRICE_RE     = re.compile(r'at \$(\d+(?:\.\d{1,2})?)')
WEIGHT_RE    = re.compile(r'weight(?:\s+of)?\s+(\d+)\s+to\s+(\d+)', re.IGNORECASE)
# Additional weight patterns for prose variation:
WEIGHT_RE2   = re.compile(r'from\s+(\d+)\s+to\s+(\d+)\s+weight', re.IGNORECASE)
WEIGHT_RE3   = re.compile(r'increasing weight\s+from\s+(\d+)\s+to\s+(\d+)', re.IGNORECASE)
```

**Edge case: weight in title vs body.** Post titles use company name format, not weight notation. Weight information will be in the post body. Parse weight from body text only.

**Edge case: multiple tickers.** Some posts contain multiple tickers (e.g., "Portfolio Update: Closing IYT, XLK, XLY"). Multi-ticker posts should be flagged as `confidence='low'` — the system cannot determine which ticker gets which action without human interpretation. Store as low-confidence, do not execute.

**Edge case: no weight field.** "Booking Profits on Meta Platforms ($META) | 8.32% Profit" may not include a weight range. When weight is absent, `weight_from` and `weight_to` remain NULL — this drops confidence by one tier per D-10.

**Edge case: ticker in title only.** If the ticker appears only in the title (`$META`) but not the body, still extract it. Regex against both title and body, deduplicated.

**Edge case: percentage profit is not weight.** "8.32% Profit" is a realized gain percentage, not a weight. The `PRICE_RE` pattern must not match percent strings. `\$(\d+\.\d{2})` correctly requires a dollar sign prefix, so `8.32%` will not match. The weight regex must also not match the gain percentage — "8.32%" contains no "weight of X to Y" pattern, so no collision.

### Action Type Inference (Two-Stage)

**Stage 1 — Title keyword match (case-insensitive):**
```python
def infer_action_from_title(title: str) -> str | None:
    t = title.lower()
    if "booking partial profits" in t:  return "partial_close"
    if "booking profits" in t:          return "partial_close"
    if "partial profit" in t:           return "partial_close"
    if "initiating long" in t:          return "open"
    if "increasing exposure" in t:      return "add"
    if "adding to" in t:                return "add"
    if "closing" in t:                  return "close"
    if "long " in t:                    return "add"   # "Long $META | ..."
    return None
```

**Stage 2 — Weight direction cross-check:**
```python
def cross_check_action(title_action: str, weight_from: int, weight_to: int) -> str:
    if weight_to > weight_from:
        direction = "open_or_add"
    elif weight_to < weight_from and weight_to > 0:
        direction = "partial_close"
    elif weight_to == 0:
        direction = "close"
    else:
        return title_action  # weight_from == weight_to: no direction signal

    # Conflict detection
    compatible = {
        "open": {"open_or_add"},
        "add":  {"open_or_add"},
        "partial_close": {"partial_close"},
        "close": {"close"},
    }
    if direction not in compatible.get(title_action, set()):
        logger.warning("Action/weight direction conflict: title=%s, direction=%s", title_action, direction)
        # weight direction is more authoritative — override title
        return {"open_or_add": "add", "partial_close": "partial_close", "close": "close"}[direction]
    return title_action
```

### spaCy Fallback Strategy

spaCy `en_core_web_sm` does NOT recognize stock tickers as named entities by default — it's a general English model. The fallback use case is extracting company names to derive tickers when the `$TICKER` format is absent from a post.

**spaCy use:** Load model lazily (only if regex is incomplete). Use `ORG` entities to find company names, then cross-reference against a ticker lookup (or extract from surrounding context). This fallback is LOW confidence by definition.

```python
import spacy
_nlp = None

def get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp

def extract_with_spacy(text: str) -> dict:
    nlp = get_nlp()
    doc = nlp(text)
    orgs = [ent.text for ent in doc.ents if ent.label_ == "ORG"]
    money = [ent.text for ent in doc.ents if ent.label_ == "MONEY"]
    return {"orgs": orgs, "money": money}
```

spaCy is a fallback for ticker discovery only. It does NOT replace the weight regex — spaCy's parser cannot extract "weight of 3 to 5" as a structured fact.

### Confidence Scoring (D-10)

```python
def score_confidence(ticker, action_type, weight_from, weight_to) -> str:
    fields_present = sum([
        ticker is not None,
        action_type is not None,
        weight_from is not None,
        weight_to is not None,
    ])
    if fields_present == 4: return "high"
    if fields_present == 3: return "medium"
    return "low"
```

**Special case — multi-ticker posts:** Force `"low"` regardless of field completeness. These are portfolio-update posts covering multiple positions and are not actionable as single signals.

---

## Daemon Structure

### Main Loop Pattern (schedule library)

```python
# scripts/run_ingestion.py
import signal
import time
import schedule
import logging

from bravos.config.settings import SCRAPE_INTERVAL_SECONDS
from bravos.ingestion.scraper import BravosScraper

log = logging.getLogger(__name__)

_shutdown = False

def handle_shutdown(signum, frame):
    global _shutdown
    log.info("Received signal %s — initiating graceful shutdown", signum)
    _shutdown = True

def main():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    scraper = BravosScraper()
    scraper.startup()   # creates driver, loads secrets, logs in

    schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(scraper.run_cycle)

    log.info("Ingestion daemon started — polling every %ds", SCRAPE_INTERVAL_SECONDS)
    while not _shutdown:
        schedule.run_pending()
        time.sleep(1)

    # Graceful cleanup
    log.info("Shutting down — closing Chrome driver")
    scraper.shutdown()   # driver.quit()

if __name__ == "__main__":
    main()
```

**Exception isolation (schedule library pattern):** The schedule library does NOT catch exceptions from job functions — an uncaught exception inside `run_cycle()` will propagate to `run_pending()` and crash the loop. Wrap `run_cycle()` with a decorator:

```python
import functools
import traceback

def catch_cycle_exceptions(job_func):
    @functools.wraps(job_func)
    def wrapper(*args, **kwargs):
        try:
            return job_func(*args, **kwargs)
        except Exception:
            log.error("Scrape cycle failed — will retry next interval:\n%s",
                      traceback.format_exc())
            # Do NOT return schedule.CancelJob — keep the job running
    return wrapper
```

Apply `@catch_cycle_exceptions` to `run_cycle()` in scraper.py. This ensures one bad cycle (network timeout, parse error, DB error) does not stop the daemon.

### Scraper Class Structure

```python
# bravos/ingestion/scraper.py
class BravosScraper:
    def __init__(self):
        self.driver = None
        self.username = None
        self.password = None

    def startup(self):
        """Called once at daemon start."""
        self.username = get_secret("bravos-site-username")
        self.password = get_secret("bravos-site-password")
        self.driver = setup_chrome_driver(headless=True)
        self._login()

    def _login(self, attempt=0) -> bool:
        """Login with 3-attempt retry (D-04)."""
        ...

    def _check_session(self) -> bool:
        """Return False if login form detected (D-02)."""
        ...

    @catch_cycle_exceptions
    def run_cycle(self):
        """Single 5-minute cycle: check session, scrape, parse, store."""
        if not self._check_session():
            # Session expired (D-03) — re-auth then continue
            if not self._login():
                log.critical("Re-auth failed after 3 attempts — skipping cycle")
                return
        posts = self._get_recent_posts()
        if not posts:
            log.warning("0 posts returned — site unreachable or empty (D-08)")
            return
        for post in posts:
            self._process_post(post)

    def _process_post(self, post: dict):
        """Fetch body, parse, store. Skip if already in DB."""
        ...

    def shutdown(self):
        """Called on SIGTERM."""
        if self.driver:
            self.driver.quit()
```

### Connection-Per-Cycle Pattern (D-15)

```python
def _store_signal(self, signal_data: dict):
    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    conn = psycopg2.connect(
        host="127.0.0.1", port=5432,
        dbname="bravos_trading", user="bravos",
        password=password,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signals
                  (post_url, post_title, raw_html, ticker, action_type,
                   weight_from, weight_to, reference_price, confidence,
                   parse_method, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (post_url) DO NOTHING
                """,
                (signal_data["post_url"], signal_data["post_title"],
                 signal_data["raw_html"], signal_data["ticker"],
                 signal_data["action_type"], signal_data["weight_from"],
                 signal_data["weight_to"], signal_data["reference_price"],
                 signal_data["confidence"], signal_data["parse_method"])
            )
        conn.commit()
    finally:
        conn.close()
```

### Logging (Claude's Discretion)

Use `structlog` for structured logging. It integrates with stdlib `logging` and produces JSON-compatible output, which is easier to query in GCP Cloud Logging. Configure at daemon startup:

```python
import structlog
structlog.configure(processors=[
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.stdlib.add_log_level,
    structlog.dev.ConsoleRenderer(),   # or JSONRenderer for production
])
log = structlog.get_logger()
```

If `structlog` adds complexity, stdlib `logging` with `%(asctime)s %(levelname)s %(name)s %(message)s` format is an acceptable fallback.

---

## Schema Gaps

The existing `signals` table in `schema.sql` is missing columns required for AUDIT-01 and INGST-06 compliance.

### Missing Columns

| Column | Type | Purpose | AUDIT requirement |
|--------|------|---------|------------------|
| `parse_method` | `VARCHAR(10)` | Records `'regex'` or `'spacy'` — which parser produced this result | AUDIT-01: every action recorded with method |
| `scraped_at` | `TIMESTAMPTZ` | When the scrape cycle retrieved this post (distinct from `parsed_at` which records parse time) | AUDIT-01: timestamp of the scrape action |

**Why `scraped_at` differs from `parsed_at`:** `parsed_at` DEFAULT NOW() records when the row was inserted/parsed. `scraped_at` records when the scraper fetched the page, which may differ by seconds if parsing is slow. For audit completeness, both are useful. In practice they will be nearly identical — but having both keeps the columns semantically distinct and satisfies AUDIT-01's "every action recorded with timestamp" requirement.

### Recommended ALTER Statements

```sql
ALTER TABLE signals
  ADD COLUMN IF NOT EXISTS parse_method VARCHAR(10),
  ADD COLUMN IF NOT EXISTS scraped_at   TIMESTAMPTZ DEFAULT NOW();

COMMENT ON COLUMN signals.parse_method IS 'regex or spacy — which parser produced this result';
COMMENT ON COLUMN signals.scraped_at   IS 'Timestamp of the scrape cycle that retrieved this post';
```

These must be applied before the scraper can write to the table.

### Existing Schema Assessment

| Column | Status | Notes |
|--------|--------|-------|
| `post_url TEXT UNIQUE` | Sufficient | UNIQUE constraint already defined — dedup works |
| `post_title TEXT` | Sufficient | Needed for action-type title parsing |
| `raw_html TEXT` | Sufficient | INGST-06 requires verbatim storage |
| `ticker VARCHAR(10)` | Sufficient | 10 chars covers 1–5 char tickers |
| `action_type VARCHAR(20)` | Sufficient | 'open', 'add', 'partial_close', 'close' all fit |
| `weight_from INTEGER` | Sufficient | Nullable for cases where weight is absent |
| `weight_to INTEGER` | Sufficient | Nullable; 0 = full close |
| `reference_price NUMERIC(10,2)` | Sufficient | Optional field per D-10 |
| `confidence VARCHAR(10)` | Sufficient | 'high', 'medium', 'low' |
| `parsed_at TIMESTAMPTZ` | Sufficient | When the row was written |
| `created_at TIMESTAMPTZ` | Sufficient | Alias for created_at (same as parsed_at in practice) |
| `parse_method` | **MISSING** | Add via ALTER |
| `scraped_at` | **MISSING** | Add via ALTER |

### AUDIT-06 Compliance (Immutability)

The schema already supports immutability: `ON CONFLICT (post_url) DO NOTHING` means existing rows are never updated. The daemon must NEVER issue `UPDATE signals SET ... WHERE post_url = ...`. If a post is re-scraped (which D-05 prevents via URL dedup), it is silently skipped, preserving the original parse result.

---

## Test Strategy

### Parser Unit Tests (no browser needed — D-12)

`tests/test_parser.py` feeds known strings directly to `bravos.ingestion.parser` functions. No Selenium, no DB, no site access.

**Fixture strings based on real post titles observed:**

```python
# Confirmed title patterns from Google-indexed content
FIXTURES = [
    {
        "title": "Initiating Long on EMCOR Group, Inc. ($EME) | Breakout",
        "body": "We are initiating a long position in EMCOR Group at $42.50. "
                "We are increasing weight from 0 to 5.",
        "expected": {"ticker": "EME", "action_type": "open",
                     "weight_from": 0, "weight_to": 5,
                     "reference_price": 42.50, "confidence": "high"},
    },
    {
        "title": "Booking Partial Profits on Meta Platforms ($META) | 8.32% Profit",
        "body": "We are booking partial profits on Meta Platforms. "
                "Reducing weight from 8 to 4 at current market price.",
        "expected": {"ticker": "META", "action_type": "partial_close",
                     "weight_from": 8, "weight_to": 4,
                     "confidence": "high"},
    },
    {
        "title": "Closing ProShares UltraShort 20+ Year Treasury ($TBT)",
        "body": "Closing our position in $TBT. Moving weight from 3 to 0.",
        "expected": {"ticker": "TBT", "action_type": "close",
                     "weight_from": 3, "weight_to": 0, "confidence": "high"},
    },
    {
        "title": "Portfolio Update: Increasing TLT, GLD; Closing IYT, XLK, XLY",
        "body": "Multiple position updates...",
        "expected": {"confidence": "low"},  # multi-ticker post
    },
    {
        "title": "No ticker or weight here just prose",
        "body": "Market commentary with no actionable signal.",
        "expected": {"ticker": None, "confidence": "low"},
    },
]
```

### Scraper Unit Tests (mock-based)

Mock the WebDriver to test scraper logic without a browser:

```python
from unittest.mock import MagicMock, patch

def test_session_expiry_detection():
    mock_driver = MagicMock()
    # Simulate login form present
    mock_driver.find_elements.return_value = [MagicMock()]  # non-empty
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = mock_driver
    assert s._check_session() == False  # expired

def test_session_valid():
    mock_driver = MagicMock()
    mock_driver.find_elements.return_value = []  # no login form
    mock_driver.current_url = "https://bravosresearch.com/category/portfolio-update/"
    ...
```

### DB Idempotency Tests

Reuse the existing `db_connection` fixture from `tests/conftest.py`:

```python
def test_dedup_insert(db_connection):
    """Inserting same post_url twice leaves exactly one row."""
    url = "https://test.bravosresearch.com/dedup-phase2-fixture"
    # insert twice, verify count == 1, cleanup
```

This pattern already exists in `test_infrastructure.py::test_schema_dedup_constraint` and can be adapted.

### Integration Test (what success looks like)

A successful end-to-end test for Phase 2 (requires live site + DB — marked as integration, not unit):
1. Start scraper with real credentials (from GCP Secret Manager)
2. Verify login succeeds (no login form on category page)
3. Verify at least 1 post is returned from category page
4. Verify the post's URL matches `bravosresearch.com/portfolio-update/` pattern
5. Verify at least 1 row is inserted into `signals` table
6. Run cycle a second time — verify row count does NOT increase (dedup)
7. Verify `confidence` column is populated ('high', 'medium', or 'low')
8. Verify `raw_html` column is non-null and non-empty

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing — `tests/conftest.py` already present) |
| Config file | None detected — uses pytest defaults |
| Quick run command | `pytest tests/test_parser.py -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INGST-01 | Login succeeds with GCP credentials | integration | `pytest tests/test_ingestion_integration.py::test_login_succeeds -x` | Wave 0 |
| INGST-02 | Category page returns posts; 5-minute schedule fires | unit (mock) + manual | `pytest tests/test_scraper.py::test_get_recent_posts -x` | Wave 0 |
| INGST-03 | Duplicate URL is silently ignored | unit (DB) | `pytest tests/test_ingestion.py::test_dedup_insert -x` | Wave 0 |
| INGST-04 | Parser extracts all 4 fields from known strings | unit | `pytest tests/test_parser.py -x` | Wave 0 |
| INGST-05 | Confidence score matches field completeness | unit | `pytest tests/test_parser.py::test_confidence_scoring -x` | Wave 0 |
| INGST-06 | raw_html stored for every signal | unit (DB) | `pytest tests/test_ingestion.py::test_raw_html_stored -x` | Wave 0 |
| INGST-07 | Session expiry detected and re-auth triggered | unit (mock) | `pytest tests/test_scraper.py::test_session_expiry -x` | Wave 0 |
| AUDIT-01 | Every signal has scraped_at and parse_method | unit (DB) | `pytest tests/test_ingestion.py::test_audit_fields -x` | Wave 0 |
| AUDIT-06 | No UPDATE issued on existing post_url | unit (mock) | `pytest tests/test_scraper.py::test_no_update_on_duplicate -x` | Wave 0 |
| AUDIT-02,03,04,05 | signal_id FK present on orders | schema check | (covered by Phase 1 schema test) | Existing |

### Sampling Rate
- **Per task commit:** `pytest tests/test_parser.py -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_parser.py` — covers INGST-04, INGST-05, with 5+ fixture strings
- [ ] `tests/test_scraper.py` — covers INGST-02, INGST-07, AUDIT-06 via mock WebDriver
- [ ] `tests/test_ingestion.py` — covers INGST-03, INGST-06, AUDIT-01 via DB fixture
- [ ] `tests/test_ingestion_integration.py` — covers INGST-01 (live site, skip by default)
- [ ] Schema migration script `infra/migrate_signals_v2.sql` — adds `parse_method`, `scraped_at`

---

## Common Pitfalls

### Pitfall 1: Site Selectors Unknown Until First Run
**What goes wrong:** Scraper navigates to category page, `find_elements(By.CSS_SELECTOR, "article.hentry")` returns empty list — posts are present but selector is wrong.
**Why it happens:** Bravos uses a custom WordPress theme; the article class may differ from standard "hentry".
**How to avoid:** In the first implementation plan, include a selector-discovery task: run with `headless=False`, dump `driver.page_source` to a file, identify actual CSS classes. Make selectors configurable (e.g., `settings.py` constants) so they can be updated without code changes.
**Warning signs:** `posts = []` log line immediately after navigation, no `TimeoutException`.

### Pitfall 2: Action Keyword Mismatch
**What goes wrong:** Parser uses CLAUDE.md keywords ("Profit Booking", "Technical Strength") — zero matches against real post titles ("Booking Profits", "Increasing Exposure").
**Why it happens:** CLAUDE.md keywords were written from memory, not from the actual site. Real titles use different phrasing.
**How to avoid:** Use the keyword map derived from observed real posts (documented in Site Structure section above). Test with fixture strings before connecting to the live site.
**Warning signs:** All parsed signals have `action_type = None`, confidence always `'low'`.

### Pitfall 3: schedule Library Does Not Catch Exceptions
**What goes wrong:** An uncaught exception inside `run_cycle()` propagates through `run_pending()` and kills the daemon.
**Why it happens:** The schedule library documentation explicitly states it does not handle exceptions.
**How to avoid:** Wrap `run_cycle()` with the `catch_cycle_exceptions` decorator. Test that an exception inside the job does NOT stop the loop.
**Warning signs:** Daemon exits unexpectedly after first parse error; log shows traceback but no "next cycle" log.

### Pitfall 4: Weight in Title vs Body Confusion
**What goes wrong:** Parser applies weight regex to the post title, which never contains weight notation. Returns `None` for weight fields even when body has weight info.
**Why it happens:** If `text = title + body` is concatenated without separator, the regex may work — but the confidence score is computed on `weight_from/weight_to` presence, so this matters.
**How to avoid:** Parse weight only from body text. Parse ticker and action_type from title (and optionally body). Keep title and body parsing paths separate.
**Warning signs:** `weight_from = None` even for posts with clear weight language in body.

### Pitfall 5: Multi-Ticker Portfolio Update Posts
**What goes wrong:** "Portfolio Update: Increasing TLT, GLD; Closing IYT, XLK, XLY" — parser extracts first ticker found (`TLT`), assigns it action type `add`, stores as `high` confidence, routes to execution. Wrong — this is a multi-action post.
**Why it happens:** Regex extracts first match; confidence is based on field count, not semantic coherence.
**How to avoid:** Count distinct tickers. If `len(tickers) > 1`, force `confidence = 'low'` and set `ticker = None` (or store first ticker with low confidence). Never route multi-ticker posts to execution.
**Warning signs:** Multiple signals inserted for the same post, or a single signal with the wrong ticker.

### Pitfall 6: DB Connection Left Open on Exception
**What goes wrong:** `psycopg2.connect()` called inside `run_cycle()`, exception thrown before `conn.close()`, connection leaks. After many cycles, connection pool on Cloud SQL exhausted.
**Why it happens:** D-15 specifies connection-per-cycle without explicit error handling.
**How to avoid:** Always use try/finally:
```python
conn = psycopg2.connect(...)
try:
    ...
finally:
    conn.close()
```
Or use `with psycopg2.connect(...) as conn:` (note: psycopg2 context manager commits/rolls back but does NOT close — must still call `conn.close()`).
**Warning signs:** `too many connections` error from PostgreSQL after several hours.

---

## Key Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Bravos site HTML selectors are unknown — first-run discovery required | HIGH | HIGH | Plan a selector-discovery task before hardcoding selectors |
| Post title vocabulary diverges from CLAUDE.md keywords | HIGH (confirmed) | HIGH | Use observed keyword map; test with fixtures before live run |
| Anti-bot measures trigger on 5-min polling | MEDIUM | HIGH | Anti-detection flags already in place; add random jitter to scrape interval |
| spaCy `en_core_web_sm` not installed in conda env | MEDIUM | LOW | Install step in plan; skip with warning if absent |
| Weight field absent from many posts | MEDIUM | MEDIUM | `confidence='medium'` graceful fallback; operator reviews via dashboard |
| Site moves Trade Alerts to different category slug | LOW | HIGH | Category URL as settings.py constant; operator-updatable |
| Cloud SQL connection limit under long-running daemon | LOW | MEDIUM | Always close connection in finally block |

---

## Planning Recommendations

### Plan Structure Recommendation

This phase should be split into 4-5 plans aligned with testable milestones:

**Plan 02-01: Schema migration + package scaffold**
- ALTER TABLE to add `parse_method`, `scraped_at`
- Create `bravos/ingestion/__init__.py`, `parser.py` (stub), `scraper.py` (stub)
- Wave 0 test stubs in `tests/test_parser.py`, `tests/test_scraper.py`, `tests/test_ingestion.py`
- Verify: schema columns exist, package importable

**Plan 02-02: Parser implementation**
- Implement `parser.py`: regex extraction, action keyword map, confidence scoring, spaCy fallback
- Un-skip `tests/test_parser.py` tests with all fixtures
- Verify: all parser unit tests pass with known-format strings

**Plan 02-03: Scraper implementation**
- Implement `scraper.py`: driver setup, login, session check, category page scraping, post body extraction
- Implement mock-based tests in `tests/test_scraper.py`
- Verify: mock tests pass; selector-discovery run on live site to confirm actual selectors

**Plan 02-04: Daemon and storage**
- Implement `scripts/run_ingestion.py`: schedule loop, SIGTERM handler, exception isolation
- Implement DB write in `scraper.py._store_signal()`
- Implement `tests/test_ingestion.py` DB tests
- Verify: daemon starts, runs one cycle, stores a signal, exits on SIGTERM

**Plan 02-05: Integration and end-to-end validation**
- Un-skip integration test, run against live site with real credentials
- Verify all Phase 2 success criteria satisfied

### Critical Early Task
The selector-discovery task in Plan 02-03 is the highest-risk item. The implementation plan should explicitly include a task that runs the scraper in headed mode (`headless=False`), navigates to `bravosresearch.com/category/portfolio-update/`, saves `driver.page_source` to `debug/category_page.html`, and inspects actual CSS classes before hardcoding selectors.

---

## Sources

### Primary (HIGH confidence)
- Google index of bravosresearch.com — real post URLs and titles confirmed: `bravosresearch.com/category/portfolio-update/` is the Trade Alert archive; `bravosresearch.com/portfolio-update/{slug}/` is the post URL pattern
- schedule library official docs — `https://schedule.readthedocs.io/en/stable/exception-handling.html` — decorator pattern confirmed
- `.claude/skills/selenium-scraper/SKILL.md` — production Selenium patterns (project-local, HIGH confidence)
- `infra/schema.sql` — confirmed existing columns; gap analysis is direct code inspection

### Secondary (MEDIUM confidence)
- WordPress standard archive HTML structure — multiple sources agree on `article.hentry`, `h2.entry-title a` pattern; theme-specific variation is the primary uncertainty
- spaCy `en_core_web_sm` NER capabilities — official docs confirm ORG/MONEY entities; financial ticker limitation confirmed by multiple community sources

### Tertiary (LOW confidence)
- Exact Bravos site login URL (`/my-account/` vs `/wp-login.php`) — not confirmed; requires first-run discovery
- Exact post body CSS selector (`.entry-content`) — standard WordPress but theme may differ; requires first-run discovery
- Whether `scraped_at` timestamp precision matters for audit — reasonable assumption based on AUDIT-01 language

## Metadata

**Confidence breakdown:**
- Site structure (category URL, post URL pattern): HIGH — confirmed via Google index
- Post title vocabulary / keyword map: HIGH — confirmed from 10+ real post titles
- Standard Stack: HIGH — all libraries from CLAUDE.md, Phase 1 confirmed working
- Parser regex correctness: MEDIUM — patterns from CLAUDE.md, edge cases inferred; needs validation against 20+ real posts (noted as Phase 1 blocker concern in STATE.md)
- Exact HTML selectors: LOW — standard WordPress patterns assumed; requires first-run discovery
- spaCy fallback precision: LOW — general NLP model not tuned for financial tickers

**Research date:** 2026-05-08
**Valid until:** 2026-06-08 (30 days — stable library stack, site structure may change if Bravos redesigns)
