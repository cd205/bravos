# Phase 2: Signal Ingestion - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-08
**Phase:** 02-signal-ingestion
**Areas discussed:** Session management, Post detection strategy, Parser approach, Scraper process structure

---

## Session Management

| Option | Description | Selected |
|--------|-------------|----------|
| Single persistent driver | One Chrome instance stays open all day. Restart only on crash or session expiry. | ✓ |
| Fresh driver each cycle | Start Chrome, login, scrape, quit — every 5 minutes | |
| Driver pool with health check | Keep driver alive, run health-check URL before each cycle | |

**User's choice:** Single persistent driver

---

| Option | Description | Selected |
|--------|-------------|----------|
| Check for login form presence | After each cycle, verify login form not present | ✓ |
| Check URL redirect to /login | After navigating to /research/, check for login redirect | |
| Check for known member-only element | Look for element only visible when logged in | |

**User's choice:** Check for login form presence

---

| Option | Description | Selected |
|--------|-------------|----------|
| Re-auth then continue the cycle | Re-login, then proceed to scrape as normal | ✓ |
| Re-auth and skip the cycle | Re-login, skip this cycle, resume next | |

**User's choice:** Re-auth then continue the cycle

---

| Option | Description | Selected |
|--------|-------------|----------|
| 3 attempts, then log critical error | Try login 3 times. If all fail, log CRITICAL, skip cycle, keep running. | ✓ |
| 1 attempt, then log critical error | Single attempt, fail fast | |
| Infinite retries with backoff | Keep trying every 60s until login succeeds | |

**User's choice:** 3 attempts, then log critical error

---

## Post Detection Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| URL dedup only — check signals table | Try INSERT; ON CONFLICT DO NOTHING skips already-seen posts | ✓ |
| Date cursor — track last_seen_at timestamp | Only fetch posts newer than last cursor | |
| URL dedup + date cursor hybrid | Both mechanisms combined | |

**User's choice:** URL dedup only (UNIQUE constraint on signals.post_url)

---

| Option | Description | Selected |
|--------|-------------|----------|
| Latest 10 posts per cycle | Check 10 most recent posts. More than enough at 5-min polling. | ✓ |
| Latest 5 posts per cycle | Smaller window, faster | |
| All posts on page 1 | Take whatever site returns | |

**User's choice:** Latest 10 posts per cycle

---

| Option | Description | Selected |
|--------|-------------|----------|
| Log warning, skip cycle, continue | Network errors logged as WARN; wait for next cycle | ✓ |
| Log warning + emit DB error record | Write scrape_errors row for each failed cycle | |
| Retry 3 times before skipping | Retry with 30s backoff x3 before giving up | |

**User's choice:** Log a warning, skip cycle, continue

---

| Option | Description | Selected |
|--------|-------------|----------|
| Filter by category at URL level | Navigate to category-filtered URL (e.g. /?category=trade-alerts) | ✓ |
| Grab all posts and filter by tag | Scrape all recent posts, then filter by category label | |

**User's choice:** Filter by category at URL level

---

## Parser Approach

| Option | Description | Selected |
|--------|-------------|----------|
| Regex-first, spaCy NLP as fallback | Regex for known patterns; spaCy if regex confidence is low | ✓ |
| Regex only — no NLP fallback | Simpler; low-confidence signals flagged if regex fails | |
| spaCy primary, regex for structured fields | NLP entity recognition first | |

**User's choice:** Regex-first, spaCy NLP as fallback

---

| Option | Description | Selected |
|--------|-------------|----------|
| Field completeness: all 4 required fields = high | ticker + action_type + weight_from + weight_to = high; 3/4 = medium; <3 = low | ✓ |
| Pattern match quality: regex = high, NLP = medium/low | Tracks HOW it was parsed | |
| Composite score: field completeness + pattern quality | 0–1 score combining both dimensions | |

**User's choice:** Field completeness: all 4 required fields extracted = high

---

| Option | Description | Selected |
|--------|-------------|----------|
| Store it, flag it, block from execution | Store with confidence='low'; never route to order execution | ✓ |
| Store it and immediately alert operator | Same as above + structured log/notification | |
| Discard it and log a warning | Don't store — simpler but loses audit trail | |

**User's choice:** Store it, flag it, block from execution

---

| Option | Description | Selected |
|--------|-------------|----------|
| Standalone parser module with unit tests | bravos/ingestion/parser.py + tests/test_parser.py | ✓ |
| Parser in same file as scraper | Keep scraper + parser together | |
| No separate tests — integration tests only | Test parser only via end-to-end tests | |

**User's choice:** Standalone parser module with unit tests

---

## Scraper Process Structure

| Option | Description | Selected |
|--------|-------------|----------|
| Long-running daemon with schedule library | Single Python process, schedule fires every 5 minutes | ✓ |
| Discrete script invoked by system cron | New Chrome driver each run; cron fires every 5 min | |
| Async event loop with asyncio | Async scheduler; Selenium is sync — requires threading | |

**User's choice:** Long-running daemon with schedule library

---

| Option | Description | Selected |
|--------|-------------|----------|
| bravos/ingestion/ package | __init__.py, scraper.py, parser.py | ✓ |
| bravos/scraper/ and bravos/parser/ as siblings | Two separate top-level packages | |
| Flat in bravos/ root | bravos/scraper.py, bravos/parser.py | |

**User's choice:** bravos/ingestion/ package

---

| Option | Description | Selected |
|--------|-------------|----------|
| psycopg2 with connection-per-cycle | Open connection per cycle, commit, close | ✓ |
| Persistent connection with keep-alive pings | Single connection all day, ping before each cycle | |
| SQLAlchemy connection pool | SQLAlchemy pooling handles reconnects | |

**User's choice:** psycopg2 with connection-per-cycle

---

| Option | Description | Selected |
|--------|-------------|----------|
| scripts/run_ingestion.py entry point | Minimal script importing and starting the daemon | ✓ |
| python -m bravos.ingestion as module entry | __main__.py in bravos/ingestion/ | |
| CLI via Click/argparse in bravos/cli.py | CLI subcommands | |

**User's choice:** scripts/run_ingestion.py entry point

---

## Claude's Discretion

- Exact HTML element selectors for the bravos site
- Logging format and log levels (structlog vs stdlib logging)
- Graceful shutdown handling (SIGTERM → quit Chrome driver cleanly)
- Exact schedule library invocation and main loop structure

## Deferred Ideas

- Email parsing as secondary channel (INGST-V2-01 — v2)
- Stale signal detection >2 hours (INGST-V2-02 — v2)
- Scrape error DB records — deferred; Phase 7 covers staleness via dashboard
