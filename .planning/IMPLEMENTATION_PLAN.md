# Implementation Plan — Codebase Improvements

Companion to [IMPROVEMENTS.md](../IMPROVEMENTS.md). Created 2026-07-09.
Intended as input to the GSD planner — sessions below map naturally onto
`/gsd:quick` tasks (or a phase, if preferred).

Sequences all 17 improvement items into 7 work sessions, each sized for one
`/gsd:quick` task (or a small cluster of them), with concrete tasks, files,
verification, and exit criteria. Total: **3–4 focused days**.

**Guiding principle** (from the July retrospective): every incident this week
was a failure that reported success. Sessions are ordered so that
trade-integrity gaps close first, structure second, polish last.

**Ground rules for every session:**
- Run through `/gsd:quick` (or `/gsd:quick --validate` where marked) so commits
  stay atomic and tracked in `.planning/quick/`.
- One executor at a time (shared git staging area — see gsd-quick-workflow memory).
- After any session that touches daemon code: `sudo systemctl restart
  bravos-trading.service` and watch `journalctl` for one clean health-check cycle.
- The full test suite must be green before and after each session
  (`pytest -q` — currently two pre-existing failures to fix or explicitly skip
  so "green" is unambiguous: `test_bracket_oca_group` (fix in Session 1) and
  `test_multi_ticker_forces_low_confidence` (parser returns `medium` for
  multi-ticker input where the test expects forced `low`; fails with and
  without the parser WIP — investigate alongside the Session 0a parser commit
  or item 11's parser corpus)).

---

## Session 0 — Protect production (½ morning, do TODAY)

### 0a. Commit the running working-tree code (item 15)

The daemon executes uncommitted code. Nothing else in this plan is safe to do
until the tree is clean — any later `git stash`/checkout mishap reverts
production behaviour.

Tasks (plain git work, no code changes — bypass GSD for this one, it IS the
planning hygiene):
1. `git add -p` review each modified file; commit in logical units:
   - `feat(pause-resume): SIGUSR1/USR2 IBKR pause-resume with signal queueing`
     — `scripts/run_ingestion.py`, `bravos/ingestion/scraper.py`,
     `infra/bravos-pause`, `infra/bravos-resume`
   - `fix(parser): robust weight phrasing + comma-aware price extraction`
     — `bravos/ingestion/parser.py`
   - `feat(scripts): direct-execution execute_pending, position reconciliation
     checker, TP/SL backfill` — `scripts/execute_pending.py`,
     `scripts/check_positions.py`, `scripts/backfill_tp_sl.py`
   - `docs: improvements review + implementation plan` — `IMPROVEMENTS.md`,
     `.planning/IMPLEMENTATION_PLAN.md`, `.planning/` artifacts, notebook
2. `git status` must end clean (only intentionally-ignored files remain).
3. Restart the service and confirm one healthy cycle (proves the committed
   state == the running state).

**Exit criteria**: clean `git status`; daemon healthy on committed code.

### 0b. Test-DB isolation guard (item 14) — `/gsd:quick`

Smallest change that removes the standing hazard, not the full fixture rework:
1. `tests/conftest.py`: session-scoped autouse guard — if any DB-touching test
   is collected and `settings.DB_NAME == "bravos_trading"` without
   `BRAVOS_TEST_DB_OK=1` set, abort collection with a loud message.
2. Convert DB-writing fixtures to transaction-rollback pattern (`yield conn;
   conn.rollback()`) where straightforward; skip-mark any test that genuinely
   needs commits until a scratch DB exists.
3. Purge check: assert `position_lots` contains no `^(OPN|PAR|FIF|SPN)_` rows
   after the suite runs (regression guard for the fake-lot leak).

**Files**: `tests/conftest.py`, individual test files as needed.
**Exit criteria**: running `pytest` on the VM with no special env fails fast
with a clear message; with the scratch-DB env it passes; `position_lots` in
production stays clean after a full suite run.

---

## Session 1 — Trade-integrity gaps (1 day) — `/gsd:quick --validate` each

### 1a. Rejection routing + honest order statuses (item 3) ⚠ highest value

1. `bravos/broker/connection.py` — in `IBApp.error()`: if `reqId` matches a key
   in `_order_status_events`, write `{"status": "IBKR_ERROR", "code": errorCode,
   "message": errorString}` into the slot and set its event — EXCEPT for
   warning-class codes that accompany accepted orders (399 confirmed; keep an
   explicit allowlist constant with a comment).
2. `bravos/execution/executor.py` — `_submit_order` / `_submit_bracket_order`:
   - slot resolved with `IBKR_ERROR` → DB status `REJECTED`, log ERROR with code.
   - true timeout (no callback at all) → DB status `UNKNOWN`, log ERROR
     ("order state unverified — check TWS"), never `SUBMITTED`.
   - add `REJECTED`/`UNKNOWN` to any status handling that assumes the old set.
3. Tests: rejection via error callback → REJECTED; error 399 then
   PreSubmitted → SUBMITTED; pure timeout → UNKNOWN. Mock patterns already
   exist in `tests/test_execution.py`.
4. Backfill honesty: one-off SQL to flip historical falsely-SUBMITTED rows
   (signals 2532 EXEL, any others found by cross-checking `orders` vs
   IBKR history) to REJECTED with a note — do this manually after deploy.

**Exit criteria**: no code path can record SUBMITTED without a real
PreSubmitted/Submitted callback; suite green.

### 1b. Fill replay on connect (item 16)

1. `bravos/broker/connection.py` — add `reqExecutions(reqId,
   ExecutionFilter())` (same-day filter) to `run_startup_reconciliation`;
   `execDetails` already handles inserts idempotently
   (`ON CONFLICT (exec_id) DO NOTHING` + lot dispatch).
   `_refresh_after_connect` gets it automatically via reconciliation.
2. Verify the `execDetails` → `positions.open_lot`/`partial_close_lot` path
   tolerates replayed fills for orders whose `orders` row already exists
   (it keys on `ibkr_order_id` — confirm behaviour when the row is missing
   entirely, e.g. manual TWS trades: must log-and-skip, not crash).
3. Tests: replayed duplicate exec_id is a no-op; fill for unknown order id is
   skipped with a warning; fresh fill inserts execution + lot.
4. After deploy: reconnect once, confirm the HROW BUY 109 fill (from today's
   market open) is auto-captured into `executions` + `position_lots` — this
   retires the manual-backfill pending item. If the fill predates the filter
   window or is missed, backfill manually and note it.
5. Optional (document, don't automate): set Master Client ID = 1 in Gateway
   API settings via VNC next time the GUI is open anyway.

**Exit criteria**: killing and restarting the daemon around a paper fill loses
nothing; HROW lot present in `position_lots`.

---

## Session 2 — Shared plumbing (½–1 day) — `/gsd:quick` each

### 2a. `bravos/db.py` (item 1)

1. Create `get_connection()` — reads settings + `BRAVOS_DB_PASSWORD`; raises
   `RuntimeError` with a setup hint if the env var is unset (kill the
   `change_me_at_deploy` silent fallback).
2. Replace all 10+ call sites (list in IMPROVEMENTS item 1). Mechanical;
   keep per-site behaviour identical (short-lived connections stay short-lived).
3. Grep-gate in the task verify: `grep -rn "psycopg2.connect" bravos/ scripts/
   --include="*.py" | grep -v db.py | grep -v tests` must return zero rows.

### 2b. `bravos/runtime.py` (item 2)

1. Create module holding `ibkr_paused: bool`, `paused_signal_queue:
   queue.Queue[int]`, and the `ibapp` singleton (moved from
   `broker/connection.py`; keep a re-export there for back-compat one release).
2. `run_ingestion.py` handlers write to it; `scraper.py` drops the
   `import scripts.run_ingestion` inversion and its try/except guard.
3. Re-verify pause/resume end-to-end on the VM (`bravos-pause` → TWS login →
   `bravos-resume`) since this touches that path.

### 2c. Bounded Gmail retry (item 17)

1. `scraper.py::process_alert` returns a result (`"stored" | "duplicate" |
   "failed"`); socket handler maps `failed` → `ERR`.
2. `run_gmail.py`: per-Message-ID failure counter (in-memory dict is fine —
   restarts reset it, which only extends retries, never drops them); after 10
   consecutive `ERR`s → `_mark_message_id_seen` + `send_alert`.
3. Tests for both sides.

**Exit criteria (session)**: zero inline `psycopg2.connect` outside `db.py`;
no library→script imports; a failing alert stops retrying after 10 attempts
and emails the operator.

---

## Session 3 — Executor/IBApp boundary (1 day) — `/gsd:quick --validate`

### 3a. Public IBApp API (item 4)

Add methods; move slot mechanics inside `connection.py`:
- `get_nlv() -> float | None`, `get_daily_pnl() -> float | None`
- `fetch_delayed_price(ticker, timeout=5.0) -> float | None`
- `place_order_and_wait(order_id, contract, order, timeout=3.0) -> OrderResult`
  (carries the Session-1a status semantics)
Update `executor.py` and `risk/gate.py` to consume them; grep-gate: no
`ibapp._` private access outside `connection.py` (except tests).

### 3b. Deduplicate submit paths (item 6)

Extract `_insert_order_row`, `_await_order_result`, `_finalize_order_status`;
`_submit_order` and `_submit_bracket_order` become orchestrations. The
Session-1a semantics then live in exactly one place.

**Exit criteria**: behaviour-identical (existing tests green unmodified except
where they poked privates); one copy of the status-wait protocol.

---

## Session 4 — Entry-point orchestration (½ day) — `/gsd:quick`

### 4a. `bravos/broker/bootstrap.py` (item 7)

`connect_and_prepare(ibapp, *, pnl_req_id) -> bool` = connect →
reconciliation → `_db_conn` → `reqPnL`. Callers: `run_ingestion._connect_ibkr`
(thin wrapper), `_refresh_after_connect` (deletes the "kept in sync manually"
mirror), `execute_pending.py`, `check_positions.py`. Move all REQ_ID_*
constants here; delete the three duplicate definitions.

### 4b. Move execution out of the scraper (item 8)

`process_alert` returns `(signal_id, is_new, confidence, result)`; the
alert-socket handler in `run_ingestion.py` owns the queue-or-execute decision
(builds directly on 2b/2c). Scraper becomes pure ingestion.

**Exit criteria**: `scraper.py` imports nothing from `execution/` or scripts;
one definition per REQ_ID constant in the repo.

---

## Session 5 — Split connection.py (½ day) — `/gsd:quick --validate` (item 5)

Pure moves, after Sessions 3–4 have shrunk it:
- `broker/connection.py` — IBApp, heartbeat, reconnect
- `broker/reconciliation.py` — snapshot + reconcile + orchestration
- `execution/fills.py` — `_handle_exec_details`, `_update_order_*`
Keep IBApp callbacks as one-line delegates. Update test imports.

**Exit criteria**: no logic diffs (move-only review); suite green.

---

## Session 6 — Polish (fold in opportunistically) (items 9–13)

| Item | What | When |
|------|------|------|
| 9 | `bravos/logging_setup.py`, used by all 3 entry points | with Session 4 |
| 10 | `schedule.every().day.at("09:30", "America/New_York")` for gate reset; same for 01:00 Chrome restart — removes both DST caveats (verify installed `schedule` ≥ 1.2 supports tz) | with Session 2 |
| 11 | Parametrised parser corpus: turn the phrasing lists in `parser.py` comments + the 208-signal history into a pytest table | any session touching parser, or standalone |
| 12 | `OrderStatus` StrEnum for PENDING_SUBMISSION/SUBMITTED/REJECTED/UNKNOWN/FILLED/PARTIAL | with Session 1a (it adds two statuses anyway) |
| 13 | Trim phase-ticket comment prefixes, keep the why-text | last, mechanical |

---

## Schedule at a glance

| Day | Sessions | Headline outcome |
|-----|----------|-----------------|
| 1 (today) | 0a, 0b | Production runs committed code; tests can't pollute prod DB |
| 2 | 1a, 1b (+12) | No order failure can ever be recorded as success; fills survive disconnects |
| 3 | 2a–2c (+10) | One DB helper, one runtime-state module, bounded alert retry |
| 4 | 3a, 3b | Clean IBApp API; single submit protocol |
| 5 (½) | 4a, 4b, 5 (+9, 11, 13) | One bootstrap; pure-ingestion scraper; readable connection layer |

## Standing risks & mitigations

- **Live system**: every session deploys via service restart during non-market
  hours (before 13:30 UTC or after 20:00 UTC) so an unnoticed regression can't
  eat a live alert. Check `journalctl` for one clean cycle + one reconciliation
  after each restart.
- **Order-path changes (Sessions 1, 3)**: after deploy, fire one end-to-end
  paper test via `execute_pending.py --dry-run` plus, where market conditions
  allow, a real re-execution of a pending signal.
- **Rollback**: every session is 1–3 atomic commits on `main`; rollback =
  `git revert` + restart. No migrations are involved anywhere in this plan.
- **Scope creep**: if a session uncovers a new bug, file it in IMPROVEMENTS.md
  and finish the session — the July pattern of chasing discoveries mid-task is
  what left the tree uncommitted for a week.
