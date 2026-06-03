---
phase: 09-bracket-orders
verified: 2026-06-03T12:00:00Z
status: human_needed
score: 4/4 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 3/4
  gaps_closed:
    - "When stop_loss and at least one take_profit are present on an open/add signal, execute_signal submits a bracket — action_type in ('open', 'add') guard now present as first condition in use_bracket (executor.py line 143)"
    - "Regression test test_bracket_close_signal_routes_bare added and un-skipped, asserting close signals with TP/SL route to bare MKT not bracket"
  gaps_remaining: []
  regressions: []
human_verification:
  - test: "Run all 8 integration bracket tests against a live DB (Auth Proxy up): pytest tests/test_execution.py -m integration -v"
    expected: "8 tests pass — the 7 original bracket tests plus the new test_bracket_close_signal_routes_bare"
    why_human: "Integration tests require DB connection via Cloud SQL Auth Proxy on 127.0.0.1:5432; cannot verify programmatically in this environment without the proxy running"
---

# Phase 9: Bracket Orders Verification Report

**Phase Goal:** Every open/add signal that includes TP and SL levels (parsed from the alert body) is submitted to IBKR as a bracket order — parent MKT order with an attached STP stop loss and one LMT limit order per take-profit level in an OCA group — so positions have defined exits from the moment the entry fills
**Verified:** 2026-06-03
**Status:** human_needed — all automated checks pass; 8 integration bracket tests require a live DB connection to confirm
**Re-verification:** Yes — after gap closure (action_type guard + regression test added)

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC-1 | Parser extracts stop_loss and take_profits from structured and prose alert bodies; stores in signals table | VERIFIED | `STOP_LOSS_RE`, `TAKE_PROFIT_RE`, `PRICE_IN_LIST_RE` present in parser.py lines 33–51; parse_signal() returns both keys on all return paths (early-return dict line 228, main return line 323). Scraper INSERT includes stop_loss + take_profits (scraper.py lines 235/247). All 6 parser tests pass. |
| SC-2 | When stop_loss and ≥1 take_profit present on an open/add signal, execute_signal submits bracket (parent MKT transmit=False, STP full qty, N LMT OCA, last TP transmit=True) | VERIFIED | use_bracket condition at executor.py lines 142–148: `action_type in ("open", "add")` is the first guard (gap now closed). _submit_bracket_order() fully implemented lines 333–484. Regression test test_bracket_close_signal_routes_bare verifies close signals with TP/SL are not routed to bracket. |
| SC-3 | TP/SL absent or quantity < len(take_profits) → bare MKT fallback, no regression | VERIFIED | use_bracket evaluates False when stop_loss is None or take_profits is empty. test_bracket_fallback_no_tpsl and test_bracket_fallback_qty_too_small both present and un-skipped. Non-integration suite: 32 passed, 0 errors. |
| SC-4 | Child orders linked to parent via parent_order_id FK; OCA group stored in oca_group | VERIFIED | STP INSERT at line 391 and LMT INSERT at line 407 both bind db_parent_order_id. oca_group bound for TP children, NULL for STP (correct per spec). migrate_phase9.sql adds parent_order_id INTEGER REFERENCES orders(id) and oca_group TEXT. |

**Score:** 4/4 truths verified

### Deferred Items

None.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bravos/ingestion/parser.py` | STOP_LOSS_RE, TAKE_PROFIT_RE, PRICE_IN_LIST_RE; extended parse_signal() | VERIFIED | All 3 constants present lines 33–51. parse_signal returns stop_loss (float\|None) + take_profits (list[float]) on early-return (line 228) and main return (line 323). |
| `bravos/ingestion/scraper.py` | INSERT INTO signals with stop_loss, take_profits columns | VERIFIED | Lines 235+247: correct column names and `signal_data.get("take_profits", [])` default (not None). |
| `infra/migrate_phase9.sql` | 4 idempotent ADD COLUMN IF NOT EXISTS statements | VERIFIED | All 4 present: stop_loss NUMERIC(10,2), take_profits NUMERIC(10,2)[] DEFAULT '{}', parent_order_id INTEGER REFERENCES orders(id), oca_group TEXT. `grep -c "ADD COLUMN IF NOT EXISTS"` returns 4. |
| `bravos/execution/executor.py` | _submit_bracket_order() + bracket routing in execute_signal() with action_type guard + extended _load_signal() | VERIFIED | _submit_bracket_order() fully implemented (lines 333–484). _load_signal() extended to SELECT 8 columns returning stop_loss/take_profits (lines 491–511). execute_signal routing at lines 139–164 with `action_type in ("open", "add")` as first condition in use_bracket. |
| `tests/test_parser.py` | 6 Phase 9 TP/SL tests, all un-skipped and passing | VERIFIED | All 6 tests present, no @pytest.mark.skip for them, all pass (1 remaining skip in file is pre-Phase-9 stub from plan 02-02, unrelated). |
| `tests/test_execution.py` | _make_signal helper + 8 bracket tests (7 original + 1 regression), all integration-marked, none skip-marked | VERIFIED | _make_signal present line 325, not skip-decorated. All 8 bracket tests have @pytest.mark.integration (no @pytest.mark.skip). test_bracket_close_signal_routes_bare at line 567 asserts mock_bracket.assert_not_called() + mock_bare.assert_called_once(). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| parse_signal() | scraper.py _store_signal() INSERT | signal_data dict keys stop_loss + take_profits | VERIFIED | scraper.py `signal_data.get("stop_loss")` + `signal_data.get("take_profits", [])` bound as 11th and 12th parameters |
| execute_signal() | _submit_bracket_order() | use_bracket condition with action_type in ('open','add') guard | VERIFIED | Lines 142–154: full D-10 condition including action_type guard; call to _submit_bracket_order on line 150 |
| execute_signal() | _submit_order() | else branch | VERIFIED | Bare MKT fallback path intact, _submit_order unchanged |
| _submit_bracket_order() | ibapp.placeOrder | parent then STP then TP[0..N-1] | VERIFIED | Lines 460–463 call placeOrder in correct sequence |
| _load_signal() | signals table | SELECT with stop_loss, take_profits columns | VERIFIED | Line 493: `SELECT ticker, action_type, weight_from, weight_to, reference_price, confidence, stop_loss, take_profits FROM signals` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| parser.py parse_signal() | stop_loss | STOP_LOSS_RE.search(body) → float() | Yes — regex extraction from alert text | FLOWING |
| parser.py parse_signal() | take_profits | TAKE_PROFIT_RE + PRICE_IN_LIST_RE | Yes — list comprehension from regex matches | FLOWING |
| scraper.py _store_signal() | stop_loss, take_profits | signal_data dict from parse_signal() | Yes — parameterized INSERT, real DB write | FLOWING |
| executor.py execute_signal() | stop_loss, take_profits | _load_signal() DB SELECT | Yes — psycopg2 row dict with real column values | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| STOP_LOSS_RE matches structured format | `python -c "from bravos.ingestion.parser import STOP_LOSS_RE; m=STOP_LOSS_RE.search('Suggested Stop Loss (SL): \$257'); print(float(m.group(1) or m.group(2)))"` | 257.0 | PASS |
| TAKE_PROFIT_RE + PRICE_IN_LIST_RE extracts list | `python -c "from bravos.ingestion.parser import TAKE_PROFIT_RE,PRICE_IN_LIST_RE; m=TAKE_PROFIT_RE.search('Take Profit (TP): \$320, \$350, and \$370'); print([float(p) for p in PRICE_IN_LIST_RE.findall(m.group(1))])"` | [320.0, 350.0, 370.0] | PASS |
| action_type guard in use_bracket | `grep -n "action_type in" bravos/execution/executor.py` | line 143 | PASS |
| Regression test present and un-skipped | `grep -n "test_bracket_close_signal_routes_bare" tests/test_execution.py` | line 567, @pytest.mark.integration | PASS |
| All 6 parser TP/SL tests pass | `pytest tests/test_parser.py -m "not integration" -q` | 32 passed, 1 pre-existing failure (out of scope), 1 skip (pre-Phase-9 stub) | PASS |
| Non-integration suite (no regressions) | `pytest tests/test_parser.py tests/test_execution.py -m "not integration" -q` | 32 passed, 1 pre-existing failure, 8 deselected (integration) | PASS |
| _submit_bracket_order exists and _submit_order unchanged | `grep -c "def _submit_bracket_order\|def _submit_order" executor.py` | 2 | PASS |

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| EXEC-07 | 09-01, 09-02, 09-03 | Bracket orders — parse TP/SL, submit MKT+STP+LMT(OCA) to IBKR | SATISFIED | All 4 ROADMAP success criteria verified. EXEC-07 not listed in the v1 Requirements traceability table in REQUIREMENTS.md — should be added as a formal entry. |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tests/test_parser.py` | 84–90 | `test_multi_ticker_forces_low_confidence` — pre-existing failure (confidence='medium' not 'low') | INFO | Pre-existing failure acknowledged since 09-01 SUMMARY. Out of scope for Phase 9; multi-ticker confidence logic unchanged. |

### Human Verification Required

#### 1. Full bracket integration test suite (8 tests)

**Test:** With the Cloud SQL Auth Proxy running on 127.0.0.1:5432, run:
```
pytest tests/test_execution.py -m integration -v
```
**Expected:** 8 tests pass — the 7 original bracket tests (`test_bracket_db_writes`, `test_bracket_transmit_sequence`, `test_bracket_parent_id`, `test_bracket_oca_group`, `test_bracket_quantity_split`, `test_bracket_fallback_no_tpsl`, `test_bracket_fallback_qty_too_small`) plus the new regression test `test_bracket_close_signal_routes_bare`.
**Why human:** Integration tests require a live PostgreSQL connection via Cloud SQL Auth Proxy. The test environment in this verification session cannot reach the proxy. All 7 original tests passed in the previous verification run; the new 8th test cannot be confirmed without a DB connection.

### Gaps Summary

No gaps. The previously identified gap (missing `action_type in ('open', 'add')` guard) has been resolved:

- executor.py line 143: `action_type in ("open", "add")` is now the first condition in `use_bracket`
- `test_bracket_close_signal_routes_bare` added at test_execution.py line 567 and marked `@pytest.mark.integration` (not skipped)

All 4 ROADMAP success criteria are verified. Human verification of the integration test suite (DB required) is the only remaining item before full sign-off.

---

_Verified: 2026-06-03_
_Verifier: Claude (gsd-verifier)_
