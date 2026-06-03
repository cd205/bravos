---
phase: 09-bracket-orders
fixed_at: 2026-06-03T00:00:00Z
review_path: .planning/phases/09-bracket-orders/09-REVIEW.md
iteration: 1
findings_in_scope: 9
fixed: 9
skipped: 0
status: all_fixed
---

# Phase 09: Code Review Fix Report

**Fixed at:** 2026-06-03
**Source review:** .planning/phases/09-bracket-orders/09-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 9 (3 Critical + 6 Warning)
- Fixed: 9
- Skipped: 0

## Fixed Issues

### CR-01: Duplicate orders placed when `process_alert` is called twice for the same URL

**Files modified:** `bravos/ingestion/scraper.py`
**Commit:** 69017c2
**Applied fix:** Changed `_store_signal` return type from `int | None` to `tuple[int | None, bool]` where the second element (`is_new`) is `True` only for a fresh INSERT and `False` for a conflict/duplicate. Updated `process_alert` to unpack `signal_id, is_new = self._store_signal(...)` and gate the `execute_signal` call on `is_new and signal_id is not None and confidence == "high"`. Duplicate URLs (crash-recovery, scheduler overlap, manual retry) now return `is_new=False` and skip the order path entirely.

---

### CR-02: `STOP_LOSS_RE` does not match "stop loss at $X" or "stop loss of $X" prose forms

**Files modified:** `bravos/ingestion/parser.py`
**Commit:** 9961516
**Applied fix:** Changed the prose branch of `STOP_LOSS_RE` from `stop\s+(?:at|of)\s+\$...` to `stop(?:\s+loss)?\s+(?:at|of)\s+\$...`. The `(?:\s+loss)?` group makes "loss" optional, so both "stop at $X" and "stop loss at $X" (and "stop of" / "stop loss of") now match correctly. Verified all four prose variants match and non-stop text does not match.

---

### CR-03: `_submit_bracket_order` returns `"UNKNOWN"` on invalid TP values without falling back to bare MKT

**Files modified:** `bravos/execution/executor.py`
**Commit:** 82a49df
**Applied fix:** Added a check in `execute_signal` immediately after the `_submit_bracket_order` call: if `final_status == "UNKNOWN"`, log a warning and fall back to `_submit_order(...)` with a bare MKT order. Prevents the trade's entry position from being silently dropped when bracket params are malformed (e.g. a zero or negative TP price in the database).

---

### WR-01: STP child order quantity never adjusts as TP children fill — oversell risk

**Files modified:** `bravos/execution/executor.py`
**Commit:** cd6d1f9
**Applied fix:** Added `ocaGroup = oca_group` and `ocaType = 2` (REDUCE_WITH_BLOCK) to the STP order object, and updated its DB INSERT to record `oca_group` (previously `NULL`). IBKR now automatically reduces the STP quantity proportionally as TP LMT orders fill, eliminating the oversell / orphaned-stop risk when all TPs have been filled before the stop triggers.

---

### WR-02: Child order DB rows permanently stuck in `PENDING_SUBMISSION`

**Files modified:** `bravos/execution/executor.py`
**Commit:** b12f8a5
**Applied fix:** After the parent order status resolves and its DB row is updated, a bulk `UPDATE orders SET status = 'SUBMITTED' WHERE ibkr_order_id = ANY(%s) AND status = 'PENDING_SUBMISSION'` is issued for all child ibkr_order_ids (`[stp_ibkr_id] + tp_ibkr_ids`). Full per-child callback tracking is deferred, but child rows no longer accumulate permanently in `PENDING_SUBMISSION`.

---

### WR-03: Non-atomic bracket DB writes; no rollback if intermediate TP INSERT fails

**Files modified:** `bravos/execution/executor.py`
**Commit:** 44a929f
**Applied fix:** Batched all N+2 INSERTs (parent, STP, each TP) into a single `with db_conn.cursor() as cur:` block with one `db_conn.commit()` after all succeed. Previously each INSERT had its own `commit()`, so a DB failure mid-loop left orphaned `PENDING_SUBMISSION` rows. Now any INSERT failure propagates before the commit and the transaction rolls back cleanly, leaving no stale rows.

---

### WR-04: `_login()` has an unused `attempt` parameter that misleads callers

**Files modified:** `bravos/ingestion/scraper.py`
**Commit:** 0358528
**Applied fix:** Removed the `attempt: int = 0` parameter from the `_login` method signature. Both call sites (`startup()` and `process_alert()`) already called `self._login()` with no arguments, so no call sites required updating.

---

### WR-05: `os.system("pkill -9 -f chrome")` in `setup_chrome_driver` kills unrelated Chrome processes

**Files modified:** `bravos/ingestion/scraper.py`
**Commit:** 5a80bb5
**Applied fix:** Replaced both `os.system("pkill -9 -f ...")` calls with `subprocess.run(["pkill", "-9", "-u", str(os.getuid()), "-f", "..."], check=False)`. Added `import subprocess` to the module imports. The `-u $(getuid())` flag scopes the signal to only Chrome processes owned by the current user, preventing collateral kills of unrelated Chrome-based tools on the GCP VM.

---

### WR-06: `test_bracket_fallback_qty_too_small` tests the wrong code path

**Files modified:** `tests/test_execution.py`
**Commit:** 63ef686
**Applied fix:** Changed `NetLiquidation` from `"100"` to `"6000"` so `quantity = int(1 * 0.05 * 6000 / 300) = 1`. With 3 take-profit targets, `1 < 3` triggers the D-20 fallback path in `execute_signal`: `_submit_bracket_order` is never called and `_submit_order` is called with a bare MKT order. Added `mock_bare.assert_called_once()` to assert the fallback was actually exercised. Removed the stale ambiguous comment; updated the docstring to explain the calculation. Previously `NLV=100` yielded `quantity=0`, causing an early return before the bracket decision — the D-20 code path was never reached.

---

_Fixed: 2026-06-03_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
