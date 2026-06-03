---
phase: 09-bracket-orders
reviewed: 2026-06-03T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - bravos/execution/executor.py
  - bravos/ingestion/parser.py
  - bravos/ingestion/scraper.py
  - infra/migrate_phase9.sql
  - tests/test_execution.py
  - tests/test_parser.py
findings:
  critical: 3
  warning: 6
  info: 4
  total: 13
status: issues_found
---

# Phase 09: Code Review Report

**Reviewed:** 2026-06-03
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Phase 9 adds bracket order support (STP + LMT children via OCA group) and TP/SL parsing. The
mechanical implementation of the bracket submission protocol is largely sound: IDs are reserved
upfront, transmit sequence is correct, quantity splitting math is correct. However, three critical
defects exist: (1) duplicate orders can be placed for re-processed alerts because `_store_signal`
now returns the existing signal_id for duplicates while `process_alert` unconditionally routes any
`signal_id` with high confidence to `execute_signal`; (2) `STOP_LOSS_RE` silently misses the
"stop loss at $X" prose pattern, which differs from "stop at $X" by the word "loss"; and (3)
when `_submit_bracket_order` rejects malformed bracket params (zero/negative TP price), it returns
`"UNKNOWN"` without falling back to a bare MKT order, silently dropping the trade.

---

## Critical Issues

### CR-01: Duplicate orders placed when `process_alert` is called twice for the same URL

**File:** `bravos/ingestion/scraper.py:250-320`

**Issue:** `_store_signal` now returns the existing `signal_id` for duplicate URLs
(lines 252-259) rather than returning `None`. `process_alert` then gates on
`if signal_id is not None and parsed.get("confidence") == "high"` (line 305) and calls
`execute_signal`. Because the guard no longer distinguishes a new insert from a duplicate
lookup, any re-processing of an alert URL (crash-recovery, scheduler overlap, manual retry)
will call `execute_signal` a second time for the same signal. There is no uniqueness
constraint on `(signal_id)` in the `orders` table, so a second order row is written and
`placeOrder` is called again.

The risk gate will not reliably prevent this: `max_positions` only fires if the account
already holds the maximum count of tickers, and the allocation gate only fires on extreme
weight deltas. Neither is a reliable dedup guard.

**Fix:** Track whether the stored signal was a new insert or a duplicate, and only call
`execute_signal` for new inserts:

```python
# In _store_signal, return a tuple: (signal_id, is_new)
row = cur.fetchone()
if row is None:
    cur.execute("SELECT id FROM signals WHERE post_url = %s", (signal_data["post_url"],))
    existing = cur.fetchone()
    return (existing[0] if existing else None, False)   # is_new=False
conn.commit()
return (row[0] if row else None, True)                  # is_new=True

# In process_alert:
signal_id, is_new = self._store_signal(signal_data)
if is_new and signal_id is not None and parsed.get("confidence") == "high":
    execute_signal(signal_id, exec_conn)
```

Alternatively, add a guard in `execute_signal` itself:
```sql
-- Check before placing order
SELECT COUNT(*) FROM orders WHERE signal_id = %s
```
and return early if an order already exists.

---

### CR-02: `STOP_LOSS_RE` does not match "stop loss at $X" or "stop loss of $X" prose forms

**File:** `bravos/ingestion/parser.py:33-38`

**Issue:** The prose branch of `STOP_LOSS_RE` is:
```
stop\s+(?:at|of)\s+\$(\d+...)
```
This matches "stop at $195" and "stop of $195" but NOT "stop loss at $195" or
"stop loss of $195". The word "loss" between "stop" and "at/of" breaks the match.
Real-world Bravos alerts are likely to use "stop loss at $X" as a prose variant. When
this pattern is present, `stop_loss` silently returns `None`, the `use_bracket` condition
in `execute_signal` evaluates to `False`, and the bracket order is not submitted — the
trade executes as a bare MKT order with no stop protection.

Verified: `STOP_LOSS_RE.search("stop loss at $195")` returns `None`.

**Fix:**
```python
STOP_LOSS_RE = re.compile(
    r'(?:'
    r'Suggested Stop Loss.*?\$(\d+(?:\.\d{1,2})?)'
    r'|stop(?:\s+loss)?\s+(?:at|of)\s+\$(\d+(?:\.\d{1,2})?)'
    r')',
    re.IGNORECASE,
)
```
The `(?:\s+loss)?` makes "loss" optional, covering both "stop at" and "stop loss at".

---

### CR-03: `_submit_bracket_order` returns `"UNKNOWN"` on invalid TP values without falling back to bare MKT

**File:** `bravos/execution/executor.py:348-353`

**Issue:** `_submit_bracket_order` validates bracket params at line 348:
```python
if ... or not all(tp > 0 for tp in take_profits):
    logger.warning("... invalid bracket params ... — refusing bracket")
    return "UNKNOWN"
```
However, `execute_signal` does NOT re-try with `_submit_order` when `_submit_bracket_order`
returns `"UNKNOWN"`. It only logs the final status at line 161-164 and returns. If the
database delivers a `take_profits` array containing a zero or negative value (e.g. a parse
bug or manual data correction), the bracket is refused and the entire order — including the
entry position — is silently dropped. No bare MKT fallback occurs.

Note also that the upstream `use_bracket` guard in `execute_signal` (line 147) only checks
`len(take_profits) > 0`, not that all values are positive. So a list like `[0.0, 350.0]`
passes the guard and reaches `_submit_bracket_order`, which then refuses and returns
`"UNKNOWN"`.

**Fix:** In `execute_signal`, treat `"UNKNOWN"` from `_submit_bracket_order` as a signal to
fall back to bare MKT:
```python
if use_bracket:
    final_status = _submit_bracket_order(
        ibapp, signal_id, ticker, ibkr_action, quantity,
        stop_loss, take_profits, db_conn,
    )
    if final_status == "UNKNOWN":
        logger.warning(
            "signal_id=%s bracket refused — falling back to bare MKT order",
            signal_id,
        )
        final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)
```

---

## Warnings

### WR-01: STP child order quantity never adjusts as TP children fill — oversell risk

**File:** `bravos/execution/executor.py:430`

**Issue:** The STP order is created with `totalQuantity = quantity` (the full parent fill
quantity). Each TP child covers only a fraction (`base_qty` or remainder). As TP orders fill
and reduce the held position, the STP remains active for the full original quantity. When all
TPs fill (reducing the position to zero), the STP is still live with a quantity equal to the
original position. If the stop triggers after TPs have partially filled, IBKR may reject or
partially fill the STP, depending on account settings for short selling.

While IBKR will reject an oversell in a cash account, the orphaned STP order remains active
and requires manual cancellation. In a margin account the oversell could create an unintended
short position.

**Fix:** Consider placing the STP in the same OCA group as the TP orders (OCA type 2), which
causes IBKR to reduce the STP quantity proportionally as TPs fill. This is the standard IBKR
bracket configuration. If keeping STP outside OCA is intentional per D-12, document the
operational procedure for manual STP cancellation after full TP fills.

---

### WR-02: Child order DB rows permanently stuck in `PENDING_SUBMISSION`

**File:** `bravos/execution/executor.py:383-412`

**Issue:** `_submit_bracket_order` registers a status event only for the parent order (line
454). The STP and all TP child orders are written to the `orders` table with status
`PENDING_SUBMISSION` and are never updated after `placeOrder` is called. The `orders` table
will accumulate `PENDING_SUBMISSION` rows for every STP and TP child ever submitted. Any
monitoring query that counts non-terminal order states will misrepresent the actual order
lifecycle.

**Fix:** Register `_order_status_events` entries for each child `ibkr_order_id` and update
their DB rows when the callbacks fire. If full child tracking is deferred, at minimum update
all child rows to `SUBMITTED` immediately after the parent's status resolves successfully,
rather than leaving them in `PENDING_SUBMISSION`.

---

### WR-03: Non-atomic bracket DB writes; no rollback if intermediate TP INSERT fails

**File:** `bravos/execution/executor.py:383-412`

**Issue:** The parent INSERT, STP INSERT, and each TP INSERT are committed individually
inside the loop (each has its own `db_conn.commit()`). If a TP INSERT fails partway through
(e.g. DB timeout, constraint violation), the preceding rows (parent + STP + earlier TPs) are
already committed but `placeOrder` has not been called. The result is orphaned `PENDING_SUBMISSION`
rows with no corresponding IBKR orders, and the bracket submission aborts entirely. There is
no cleanup path.

**Fix:** Batch all N+2 INSERTs in a single transaction; commit once before calling
`placeOrder`. If any INSERT fails, the exception propagates before the commit and the
transaction is rolled back cleanly:
```python
with db_conn.cursor() as cur:
    cur.execute(INSERT parent ...)
    db_parent_order_id = cur.fetchone()[0]
    cur.execute(INSERT stp ...)
    for i, tp_price in enumerate(take_profits):
        cur.execute(INSERT tp[i] ...)
db_conn.commit()   # single commit after all INSERTs succeed
```

---

### WR-04: `_login()` has an unused `attempt` parameter that misleads callers

**File:** `bravos/ingestion/scraper.py:100`

**Issue:** The `_login` method signature is `def _login(self, attempt: int = 0) -> bool:`.
The parameter `attempt` is never read inside the method. The retry loop uses `for i in
range(settings.MAX_REAUTH_ATTEMPTS)`. A caller passing `attempt=2` would have no effect.
This is dead parameter surface that suggests the function does something it does not.

**Fix:** Remove the unused parameter:
```python
def _login(self) -> bool:
```
Update the one call site at `startup()` which already calls `self._login()` with no argument.

---

### WR-05: `os.system("pkill -9 -f chrome")` in `setup_chrome_driver` kills unrelated Chrome processes

**File:** `bravos/ingestion/scraper.py:33,65`

**Issue:** `os.system("pkill -9 -f 'chrome.*remote-debugging' 2>/dev/null || true")` and
`os.system("pkill -9 -f chrome 2>/dev/null || true")` send `SIGKILL` to any process whose
command line matches "chrome" system-wide, regardless of owner or purpose. On a GCP VM that
runs any Chrome-based tooling (e.g. Puppeteer tests, admin tools), this silently kills them.
The `-9` flag also prevents Chrome from writing crash dumps, making post-mortem debugging
harder. The match pattern `chrome` in the second call is especially broad.

**Fix:** Use `subprocess` with process-group tracking to kill only the driver launched by this
invocation. At minimum, scope the pkill to the current user:
```python
import subprocess
subprocess.run(
    ["pkill", "-9", "-u", str(os.getuid()), "-f", "chrome.*remote-debugging"],
    check=False,
)
```

---

### WR-06: `test_bracket_fallback_qty_too_small` tests the wrong code path — the actual D-20 fallback is never exercised

**File:** `tests/test_execution.py:536-563`

**Issue:** The test sets `NLV=100`, `price=300`, `weight_delta=1`, which yields
`quantity = int(1 * 0.05 * 100 / 300) = 0`. With `quantity=0`, `execute_signal` returns at
line 131-135 (`quantity <= 0` early-exit) — before ever reaching the `use_bracket` decision
at line 143. `_submit_bracket_order` is trivially not called because execution never reaches
that code. The test's comment acknowledges this ambiguity ("either called or not called") but
provides no assertion on `mock_bare`, leaving the D-20 fallback (quantity >= 1 but quantity <
len(take_profits)) completely untested.

**Fix:** Use parameters that produce `1 <= quantity < len(take_profits)`. For example, with
`NLV=2000`, `price=300`, `weight_delta=1`: `quantity = int(1 * 0.05 * 2000 / 300) = 0`...
try `NLV=6000`: `quantity = int(1 * 0.05 * 6000 / 300) = 1`. With 3 TPs, `1 < 3` triggers
the D-20 path:
```python
mock_ibapp._account_summary = {"NetLiquidation": "6000"}
# quantity=1, 3 TPs → falls back to bare MKT per D-20
mock_bracket.assert_not_called()
mock_bare.assert_called_once()
```

---

## Info

### IN-01: `_submit_bracket_order` DB update uses `ibkr_order_id` while `_submit_order` uses `id`

**File:** `bravos/execution/executor.py:479-482`

**Issue:** `_submit_order` updates `WHERE id = %s` (DB primary key), while
`_submit_bracket_order` updates `WHERE ibkr_order_id = %s`. Both are correct given their
local variables, but the inconsistency makes it harder to verify correctness at a glance.
`ibkr_order_id` is unique within a Gateway session, but after a Gateway restart it can
repeat, potentially matching a stale row.

**Fix:** Capture `db_parent_order_id` from the INSERT RETURNING clause (as done in
`_submit_order`) and use `WHERE id = %s` consistently:
```python
with db_conn.cursor() as cur:
    cur.execute("UPDATE orders SET status = %s WHERE id = %s", (db_status, db_parent_order_id))
```

---

### IN-02: `score_confidence` `"medium"` threshold is 2–3 fields; `"low"` is fewer than 2 — boundary is not `< 3`

**File:** `bravos/ingestion/parser.py:183-187`

**Issue:** The docstring says `< 3 fields present = 'low'` but the code returns `"medium"`
for `present >= 2` and `"low"` otherwise (i.e. 0 or 1 present). A signal with exactly 2
fields (e.g. only ticker + action_type, no weights) is rated medium. This is consistent with
the code logic but the docstring boundary description ("3/4 fields = medium") is wrong: it
should say "2–3/4 fields present = medium". Minor documentation bug.

**Fix:** Update the docstring:
```
3–4/4 fields present = 'high'
2–3/4 fields present = 'medium'
< 2 fields present   = 'low'
```

---

### IN-03: `cross_check_action` silently treats `weight_to == weight_from > 0` as `"add"`

**File:** `bravos/ingestion/parser.py:136-143`

**Issue:** If a parsed alert yields `weight_from == weight_to > 0` (e.g. a re-statement post
with "maintain weight from 5 to 5"), the `else` branch fires and returns `"add"`, which will
cause an order to be placed for a zero-delta weight change. The quantity formula
(`abs(weight_to - weight_from) × ...`) produces `0` shares, which `execute_signal` catches
at line 131 and skips. No crash results, but the path generates unnecessary logging noise and
a spurious risk-gate check.

**Fix:** Add a guard for the no-delta case:
```python
elif weight_to == weight_from:
    weight_action = None  # no change signal; no order needed
```

---

### IN-04: `_store_signal` opens a new `psycopg2` connection on every call rather than accepting `db_conn`

**File:** `bravos/ingestion/scraper.py:210-263`

**Issue:** `_store_signal` constructs a new `psycopg2.connect(...)` every time it runs and
closes it in the `finally` block. `process_alert` then opens a second connection for
`execute_signal`. This means every alert processing cycle opens and closes at least two
database connections. On a Cloud SQL Auth Proxy this adds round-trip latency and prevents
connection reuse. This pattern was present before Phase 9 but Phase 9 did not address it.

**Fix:** Refactor `_store_signal` to accept an optional `db_conn` parameter and pass a shared
connection from `process_alert`. If connection lifecycle management is complex, consider a
simple connection pool (e.g. `psycopg2.pool.SimpleConnectionPool`).

---

_Reviewed: 2026-06-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
