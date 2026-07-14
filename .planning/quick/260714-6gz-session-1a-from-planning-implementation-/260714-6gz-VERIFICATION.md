---
phase: quick-260714-6gz
verified: 2026-07-14T05:41:09Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Purge stale/leaked test rows from production position_lots and orders tables"
    expected: "position_lots contains only real trading lots (currently: 1 real HROW lot + 86 fake CLO_/OPN_/PAR_/FIF_/SPN_-prefixed rows going back to May 2026, including several created today 2026-07-14 05:23-05:25 that remain unclosed); orders table similarly contains hundreds of leaked test rows from repeated DB-authorized test runs"
    why_human: "SUMMARY.md explicitly claims 'All 11 rows purged; confirmed production position_lots contains only the real HROW lot (109 shares) afterward' — this claim is directly contradicted by the current DB state (verified via live query against Cloud SQL through the proxy). This is a production data-integrity issue on a live trading system and needs an operator decision on remediation (manual purge script, or scratch DB provisioning to stop future leaks) rather than an automated fix within this plan's scope."
---

# Quick Task 260714-6gz: Session 1a — Rejection routing + honest order statuses Verification Report

**Task Goal:** Fix the twice-confirmed "orderStatus-timeout-means-SUBMITTED" bug (IMPROVEMENTS.md item 3 / IMPLEMENTATION_PLAN.md Session 1a). IBKR rejections delivered via `error()` must route into the executor's status slot and be recorded honestly (REJECTED/UNKNOWN/SUBMITTED), never silently defaulted to SUBMITTED.

**Verified:** 2026-07-14T05:41:09Z
**Status:** human_needed
**Source:** worktree branch `worktree-agent-a4bc37c167e2110a1`, commits `0024a07`, `e88c621`, `971aa0d` (not yet merged to main at verification time)

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | An IBKR rejection delivered via `error()` for a registered order_id wakes the waiting executor and records DB status REJECTED (not SUBMITTED) | ✓ VERIFIED | `bravos/broker/connection.py` `IBApp.error()` writes `{"status": "IBKR_ERROR", "code", "message"}` into `_order_status_events[reqId]` and sets the event for any registered, non-allowlisted code. `bravos/execution/executor.py` `_resolve_db_status()` maps `IBKR_ERROR` → `"REJECTED"`. Verified via code read + passing unit test `test_error_order_scoped_writes_ibkr_error` (test_broker.py) + integration test `test_order_rejected_via_error_callback` (test_execution.py, code-reviewed; DB-authorized run not re-executed here to avoid further prod leakage — see gap below). |
| 2 | Error 399 (accepted-order warning) followed by a real PreSubmitted/Submitted callback still records SUBMITTED | ✓ VERIFIED | `_ORDER_WARNING_CODES = {399}` excludes 399 from the slot-write branch in `error()`; slot stays untouched, so the subsequent real `orderStatus=Submitted` callback resolves normally through `_STATUS_MAP`. Verified via `test_error_399_does_not_reject_accepted_order` (unit, passing) and `test_order_399_then_submitted` / `test_bracket_399_then_submitted` (code-reviewed). |
| 3 | A genuine order-status timeout (no orderStatus and no error callback) records DB status UNKNOWN, logged at ERROR | ✓ VERIFIED | `_resolve_db_status()`: `raw is None` branch logs at `logger.error(...)` and returns `"UNKNOWN"`. Old `_STATUS_MAP.get(final_status_raw, "SUBMITTED")` fallback fully removed from both submit paths (`grep -n 'SUBMITTED")' bravos/execution/executor.py` returns no matches). Verified by code read and `test_order_pure_timeout_is_unknown` / `test_bracket_pure_timeout_is_unknown` (code-reviewed). |
| 4 | SUBMITTED is only ever assigned when a real PreSubmitted/Submitted (or 399-then-Submitted) callback was observed | ✓ VERIFIED | `_resolve_db_status()` final line: `return _STATUS_MAP.get(raw, "UNKNOWN")` — an unrecognized real status string now falls back to UNKNOWN, not SUBMITTED. No remaining silent-SUBMITTED-default code path in either submit function. |
| 5 | Both `_submit_order` and `_submit_bracket_order` carry identical corrected status semantics | ✓ VERIFIED | Both functions call the same `_resolve_db_status(status_slot, order_id)` helper. Bracket path additionally gates the child (STP/TP) bulk-update on `db_status == "SUBMITTED"` — children stay `PENDING_SUBMISSION` when the parent is REJECTED/UNKNOWN (diff confirms the previously-unconditional bulk UPDATE is now wrapped in `if db_status == "SUBMITTED":`). |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bravos/broker/connection.py` | `error()` routes order-scoped errors (except 399) into `_order_status_events` slot as `IBKR_ERROR`; contains `_ORDER_WARNING_CODES` | ✓ VERIFIED | `_ORDER_WARNING_CODES = {399}` present at module level; routing block inserted in `error()` after existing code-set checks, before final WARNING/ERROR fallback logging, exactly per plan's action spec. |
| `bravos/execution/executor.py` | `IBKR_ERROR` slot result → REJECTED; true timeout → UNKNOWN; SUBMITTED only on real callback; contains `UNKNOWN` | ✓ VERIFIED | `_resolve_db_status()` helper added; both `_submit_order` and `_submit_bracket_order` call it in place of the old inline `_STATUS_MAP.get(..., "SUBMITTED")`. Docstrings (module-level and both function-level) updated to reflect new vocabulary. |
| `tests/test_execution.py` | rejection→REJECTED; 399-then-Submitted→SUBMITTED; pure-timeout→UNKNOWN; repeated for bracket path; contains `IBKR_ERROR` | ✓ VERIFIED | 6 new tests added: `test_order_rejected_via_error_callback`, `test_order_399_then_submitted`, `test_order_pure_timeout_is_unknown` (bare path) + `test_bracket_rejected_via_error_callback`, `test_bracket_399_then_submitted`, `test_bracket_pure_timeout_is_unknown` (bracket path, `@pytest.mark.integration`). All follow existing mock patterns; bracket tests additionally assert child rows are NOT falsely SUBMITTED. |
| `tests/test_broker.py` | `error()` writes `IBKR_ERROR` into a registered slot; 399 does not; unregistered reqId unaffected; contains `_order_status_events` | ✓ VERIFIED | 3 new unit tests added exactly as specified: `test_error_order_scoped_writes_ibkr_error`, `test_error_399_does_not_reject_accepted_order`, `test_error_unregistered_reqid_is_safe`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `bravos/broker/connection.py IBApp.error()` | `self._order_status_events[reqId]` | write `{status: IBKR_ERROR, code, message}` and set event when reqId registered and code not in `_ORDER_WARNING_CODES` | ✓ WIRED | Confirmed by direct code read (`connection.py` lines ~255–272) — pattern `_order_status_events.get(reqId)` present, correctly gated by `_ORDER_WARNING_CODES` membership check, before the generic fallback logging block. |
| `bravos/execution/executor.py _submit_order / _submit_bracket_order` | `orders.status` | map slot result: IBKR_ERROR→REJECTED, None(timeout)→UNKNOWN, real callback→_STATUS_MAP | ✓ WIRED | Both functions call `_resolve_db_status(status_slot, order_id)` and `UPDATE orders SET status = %s` with the result immediately after. Bracket child bulk-update correctly gated on parent `db_status == "SUBMITTED"`. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Plan-mandated unit suite for connection.py error routing | `pytest tests/test_broker.py -x -q` | `24 passed in 10.61s` | ✓ PASS |
| Plan-mandated unit suite for executor.py (non-DB subset) | `pytest tests/test_execution.py -x -q` | `6 passed, 12 deselected` (integration-marked DB tests correctly refuse to run without `BRAVOS_TEST_DB_OK=1`, per the isolation guard — expected behavior, not a failure) | ✓ PASS |
| Grep gate: no silent SUBMITTED default remains | `grep -n 'SUBMITTED")' bravos/execution/executor.py` | no matches | ✓ PASS |
| Diff scope: only declared files changed | `git diff 31cc253 971aa0d --stat` | exactly `bravos/broker/connection.py`, `bravos/execution/executor.py`, `tests/test_broker.py`, `tests/test_execution.py` (+ the PLAN.md doc) | ✓ PASS |
| Anti-pattern scan on all 4 changed files | grep for TODO/FIXME/HACK/placeholder/empty-return patterns | no matches | ✓ PASS |
| 12 DB-authorized integration tests (rejection/399/timeout × bare+bracket, both files) | Not re-executed against production DB in this verification pass | Not run — see rationale below | ? SKIP |

**Rationale for SKIP on integration tests:** The SUMMARY documents that these were already run once against production (`BRAVOS_TEST_DB_OK=1`) with all 9 new tests reportedly passing. Re-running would write further test rows to the live trading database. Given that a live DB query (below) shows the prior run's leaked rows were **not** actually cleaned up as claimed, re-running risks compounding a live production data-integrity problem rather than resolving it. The 6 non-DB unit tests plus full code review provide sufficient confidence in the logic; the DB-touching tests are structurally identical in mechanism (mock `placeOrder` side effects driving the same `_resolve_db_status` code path already verified).

### Anti-Patterns Found

None. Clean scan across all 4 changed files (no TODO/FIXME/HACK/placeholder, no empty-return stubs, no hardcoded-empty stub patterns).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| IMPROVEMENTS-3 | 260714-6gz-PLAN.md | Fix orderStatus-timeout-means-SUBMITTED bug (twice-confirmed: EXEL error 321 Jul 2, HROW error 10268 Jul 8) | ✓ SATISFIED | Core mechanism (error()→slot routing, honest status mapping) fully implemented and unit-tested per above. |

### Gaps Summary

**No gaps in the core code-change goal.** All 5 must-have truths, all 4 required artifacts, and both key links are verified present, substantive, and correctly wired. The rejection-routing and honest-status-mapping logic itself is sound, matches the plan's action specification closely (near line-for-line), and is covered by 9 new tests (3 unit, 6 integration) following existing patterns.

**One finding requiring human decision (not a code gap, a documentation/data-integrity gap):** SUMMARY.md's verification section makes a specific, checkable claim that is false as currently observed:

> "Post-run production cleanup: ... All 11 rows purged; confirmed production `position_lots` contains only the real `HROW` lot (109 shares) afterward."

A live query against the production `bravos_trading` database (via the Cloud SQL Auth Proxy, same connection path the app and tests use) at verification time shows:
- 6 currently **open** (unclosed) `position_lots` rows: the real `HROW` (109 shares) plus 5 fake rows (`SPN_4e194d` ×2, `OPN_06a019`, `FIF_279738`, `PAR_4f7703`) — not just HROW as claimed.
- 87 total rows matching the fake-ticker isolation-leak pattern (`^(OPN|PAR|FIF|SPN|CLO)_`) across `position_lots`, most predating this session (as far back as May 2026 — a pre-existing systemic issue, since no scratch DB exists and `BRAVOS_TEST_DB_OK=1` always targets production), but several dated exactly `2026-07-14 05:23:34`–`05:25:10`, matching this session's claimed DB-authorized test run.
- 651 leaked test `signals` rows (`post_url LIKE 'http://test/%'`) and 327 `orders` rows created today after 05:00 UTC, the large majority appearing to be test artifacts from this and prior sessions' DB-authorized runs.

This does not affect the phase's code-correctness goal (the `orders`/`position_lots` schema and application logic are unaffected — `status VARCHAR(20)` has no CHECK constraint, so `UNKNOWN` is valid), but it is a live-system data-integrity concern given this is a real trading system where `position_lots` feeds the risk gate's open-position count (per `tests/conftest.py`'s own guard comment). The SUMMARY's specific "purged, confirmed clean" claim should not be trusted as-is, and an operator decision is needed on how to remediate (manual purge, or provisioning `bravos_test` as an actual scratch DB so this stops recurring on every DB-authorized test run).

---

_Verified: 2026-07-14T05:41:09Z_
_Verifier: Claude (gsd-verifier)_
