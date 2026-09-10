# Checkpoint: Issue #1 — Alert verification dashboard

**Status as of 2026-08-10: implementation complete, tests passing, NOT YET COMMITTED.**
Session has crashed repeatedly after this point — resume by reading this file,
then just running the commit steps at the bottom (everything else is done).

## What this task is

GitHub issue https://github.com/cd205/bravos/issues/1 — "Alert verification
dashboard: per-signal order/stop-loss status table". Full spec is in the
issue body (`gh issue view 1 --repo cd205/bravos`). Summary: build a FastAPI
+ Jinja2 dashboard with two routes:
- `GET /` — table of signals, most-recent-first, date-range filterable,
  showing ticker/action/stop-loss-ordered/order-status/fill-price
- `GET /alerts/{signal_id}` — detail view: raw signal fields, risk-gate log,
  full order history

The route reads `queries/monitor.sql` text at request time and wraps it
(no SQL duplicated in Python). `monitor.sql` itself needed a new
`stop_loss_check` CTE added (per-signal: does a STP order exist?).

This reverses the 2026-05-20 "no dashboard" decision recorded in
`.planning/phases/07-notifications-and-monitoring-query/07-CONTEXT.md`.

## Work completed (all done, verified working)

1. **`queries/monitor.sql`** — added `stop_loss_check` CTE + `stop_loss_ordered`
   output column (NULL when `signals.stop_loss IS NULL` = not-applicable,
   else boolean). Modified in place, diff is currently uncommitted.

2. **`tests/test_monitor_query.py`** (new file) — 3 tests against a real
   Postgres instance via the `db_connection` fixture, covering the three
   states (ordered=true, ordered=false/missing, not-applicable). **All 3
   pass** against the `bravos_test` scratch DB (see "Test DB setup" below).

3. **`bravos/dashboard/`** (new package):
   - `__init__.py` — package docstring
   - `queries.py` — `fetch_signals()`, `fetch_signal()`, `fetch_signal_detail()`.
     Reads `queries/monitor.sql` from disk each call, wraps with outer
     `SELECT * FROM (<monitor.sql text>) monitor WHERE ...`.
   - `app.py` — FastAPI app, two routes (`/` and `/alerts/{signal_id}`),
     uses `bravos.db.get_connection()`, no auth (per spec, trusted network).
     Default table window = last 14 days when no `?start=`/`?end=` given.
     Invalid date query params → 400. Missing signal_id → 404.
   - `templates/alert_table.html`, `templates/alert_detail.html` — Jinja2
     templates, plain inline CSS, no JS framework, htmx not used (kept
     simple — spec didn't require live updates).

4. **`tests/test_dashboard.py`** (new file) — 10 tests using
   `fastapi.testclient.TestClient`, mocking `bravos.dashboard.app.get_connection`
   (monkeypatched, following `tests/test_db.py`'s MagicMock pattern — first
   use of `TestClient` in this repo). **All 10 pass.** Covers: table renders
   rows, empty state, default 14-day window, date filtering + boundary
   params, invalid date → 400, stop-loss MISSING/n-a rendering, detail view
   zero-orders case, detail view full-bracket case, detail view 404.

   Note: `fetch_signal_detail` reuses **one** cursor across 3 sequential
   `execute()` calls (signals → risk_gate_log → orders), not 3 separate
   `conn.cursor()` calls. The test mock (`_SequentialCursor` class in the
   test file) had to model that correctly — an earlier MagicMock-based
   attempt was wrong and caused a real failure that caught the mismatch.
   This is already fixed and passing; if you see anyone try to "simplify"
   `_SequentialCursor` back to MagicMock chains, make sure they re-verify
   against the real single-cursor-reuse behavior in `queries.py`.

5. **Live smoke test against real `bravos_trading` data** (read-only,
   via `uvicorn` on port 8811, already killed) — confirmed:
   - Table renders real signals, correctly shows "MISSING" (red) for a
     TEVA signal (id 3571) that had `stop_loss` set but no STP order ever
     placed (it was risk-gate-blocked on max_positions).
   - Detail view for signal 3571 correctly shows the risk-gate BLOCKED
     entry with reason "max_positions: 20/20".
   - Detail view for signal 2577 (a real 5-order bracket: 1 MKT parent +
     1 STP + 3 LMT take-profit children, OCA group "TP_3") renders all
     rows correctly including parent_order_id linkage.
   - Fixed one real bug found live: `Take Profits` was rendering Python's
     raw `[Decimal('41.50'), ...]` repr instead of a clean list — template
     now does `{{ signal.take_profits|join(", ") if signal.take_profits else "—" }}`.
   - Confirmed Jinja2 autoescape is on by default for `.html` templates
     (XSS-safe) via `Jinja2Templates(...).env.autoescape`.

6. **Decision reversal recorded** — appended (not rewrote) a note under the
   existing "Dashboard (DASH-01–04) — cut by decision 2026-05-20" line in
   `.planning/phases/07-notifications-and-monitoring-query/07-CONTEXT.md`,
   marked "**Superseded 2026-08-09 (GitHub issue #1)**" with rationale.

## Test DB setup (already done, may need recreating if VM state reset)

`bravos_test` database did not exist. Created it and applied all migrations
so it mirrors `bravos_trading` schema, per the `db_connection` fixture's
own documented recommendation (`export BRAVOS_DB_NAME=bravos_test`):

```bash
export BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password --project=crafty-water-453519-d7 2>/dev/null)
PGPASSWORD="$BRAVOS_DB_PASSWORD" psql -h 127.0.0.1 -U bravos -d bravos_trading -c "CREATE DATABASE bravos_test OWNER bravos"
for f in infra/schema.sql infra/migrate_phase4.sql infra/migrate_phase5.sql infra/migrate_phase9.sql infra/migrate_gmail.sql infra/migrate_signals_v2.sql; do
  PGPASSWORD="$BRAVOS_DB_PASSWORD" psql -h 127.0.0.1 -U bravos -d bravos_test -f "$f"
done
```

If `bravos_test` already exists (check with `psql -h 127.0.0.1 -U bravos -d bravos_trading -tc "SELECT 1 FROM pg_database WHERE datname='bravos_test'"`), skip this.

**Important env quirk discovered this session**: the Bash tool does NOT
reliably inherit `.bashrc`'s `BRAVOS_DB_PASSWORD` export across separate
tool-call invocations (each call may be a fresh shell). Always fetch it
explicitly at the start of any command that needs DB access:
```bash
export BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password --project=crafty-water-453519-d7 2>/dev/null)
```
And for raw `psql` (not through Python), pass `PGPASSWORD="$BRAVOS_DB_PASSWORD"`
explicitly too — `psql` does not pick up `BRAVOS_DB_PASSWORD` on its own.

## What's NOT done yet — resume here

1. **Run the full test suite** (not just the two new test files) against
   `bravos_test`, to make sure nothing else regressed:
   ```bash
   cd /home/chris_s_dodd/bravos
   export BRAVOS_DB_PASSWORD=$(gcloud secrets versions access latest --secret=bravos-db-password --project=crafty-water-453519-d7 2>/dev/null)
   export BRAVOS_DB_NAME=bravos_test
   python3 -m pytest tests/ -v 2>&1 | tail -100
   ```
   Expect some pre-existing tests may be unrelated/skipped/require Chrome
   etc (test_scraper.py, test_infrastructure.py) — that's normal, not
   something this change should have affected. Focus on whether anything
   that touches `signals`/`orders`/`risk_gate_log` broke.

2. **No typechecker is configured in this repo** (no mypy/pyright found) —
   already confirmed. `python3 -m py_compile` on the three new dashboard
   `.py` files already passed. Skip further typecheck effort.

3. **Run `/code-review`** (or the `code-review` skill) on the diff before
   committing, per the `/mattpocock-skills:implement` workflow instructions
   that kicked off this task. Has NOT been run yet.

4. **Commit the work.** Nothing has been committed yet. Files to stage:
   - `queries/monitor.sql` (modified)
   - `bravos/dashboard/` (new, all files)
   - `tests/test_dashboard.py` (new)
   - `tests/test_monitor_query.py` (new)
   - `.planning/phases/07-notifications-and-monitoring-query/07-CONTEXT.md` (modified — decision reversal note)

   **Do NOT stage/commit** these pre-existing unrelated modifications unless
   the user separately confirms they want them committed too — they predate
   this task and their origin/intent hasn't been investigated as part of
   this issue:
   - `.planning/HANDOFF.json` (modified — GSD auto-checkpoint churn)
   - `CLAUDE.md` (modified — GSD workflow-enforcement block was removed;
     unclear if intentional, flag to user)
   - `notebooks/trade-status-viewer.ipynb` (modified — large diff, unrelated)

   Suggested commit message:
   ```
   feat(issue-1): add alert verification dashboard

   FastAPI + Jinja2 dashboard reading queries/monitor.sql directly (no SQL
   duplicated in Python) for a per-signal order/stop-loss status table, plus
   a /alerts/{id} detail view with risk-gate log and full order history.

   monitor.sql gains a stop_loss_check CTE exposing stop_loss_ordered
   (true/false/NULL-not-applicable). Reverses the 2026-05-20 no-dashboard
   decision — see 07-CONTEXT.md.

   Closes #1

   Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
   ```

5. **After committing**, consider whether to `gh issue close 1` or leave
   that to the user — not yet decided, ask before closing.

## Files changed this session (for quick diffing on resume)

```
 modified:   queries/monitor.sql
 modified:   .planning/phases/07-notifications-and-monitoring-query/07-CONTEXT.md
 new file:   bravos/dashboard/__init__.py
 new file:   bravos/dashboard/app.py
 new file:   bravos/dashboard/queries.py
 new file:   bravos/dashboard/templates/alert_table.html
 new file:   bravos/dashboard/templates/alert_detail.html
 new file:   tests/test_dashboard.py
 new file:   tests/test_monitor_query.py
```

Pre-existing unrelated dirty state (present before this task started, not
touched by it): `.planning/HANDOFF.json`, `CLAUDE.md`, `notebooks/trade-status-viewer.ipynb`.
