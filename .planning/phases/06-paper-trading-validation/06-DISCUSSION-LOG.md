# Phase 6: Paper Trading Validation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the discussion.

**Date:** 2026-05-15
**Phase:** 06-paper-trading-validation
**Mode:** discuss (default, interactive)
**Areas discussed:** Signal sourcing strategy, Bug fix scope, Validation run structure, Out-of-hours order path

---

## Signal Sourcing Strategy

| Question | Options Presented | Selected |
|----------|------------------|----------|
| Where do signals come from during validation? | Real scraped alerts only / Seed historical + observe live / Mix | Mix: seed historical post URLs + observe live |
| How to inject historical URLs? | Call process_alert(url) on real URLs / Insert pre-parsed signals / Replay from saved raw_html | Call process_alert(url) directly on real Bravos URLs |
| URL availability? | User has list / Need discovery script / You decide | User has a list or can provide them |
| Action type coverage? | Deliberately select to cover all 4 types / Use whatever is available / You decide | Deliberately select URLs to cover all 4 action types: open, add, partial_close, close |

**Continue or next?** Next area

**Notes:** Calling `process_alert(url)` on real historical URLs is the right approach — it validates the full scrape+parse path with real HTML, not a synthetic shortcut. The user will provide the URL list directly, so no discovery script is needed. Coverage of all 4 action types (open/add/partial_close/close) is explicitly required to satisfy SC #2.

---

## Bug Fix Scope

| Question | Options Presented | Selected |
|----------|------------------|----------|
| Where do fixes live? | Fix in-place within Phase 6 / Document + Phase 6.5 insert / Document only fix before Phase 8 | Fix in-place within Phase 6 |
| What counts as blocking failure? | SC violations only / Any order-path failure / You decide | Any order-path failure (more conservative than SC-only) |
| Bug tracking docs? | Inline VALIDATION-REPORT.md only / Separate BUG-LOG.md + VALIDATION-REPORT.md / You decide | Separate BUG-LOG.md + VALIDATION-REPORT.md |

**Continue or next?** Next area

**Notes:** The "any order-path failure" threshold is more conservative than the ROADMAP.md success criteria, which only explicitly list SC violations. This is intentional — the user doesn't want any bug that could cause a wrong live order to slip through. Two documents keeps the bug tracking clean: BUG-LOG.md is an ongoing log during the validation run; VALIDATION-REPORT.md is the final summary.

---

## Validation Run Structure

| Question | Options Presented | Selected |
|----------|------------------|----------|
| Run structure? | Scripted sequence with pass/fail / Observation run for a real day / Both | Scripted sequence with explicit pass/fail per scenario |
| Pass/fail verification method? | DB state only / DB state + IBKR confirmation / You decide | You decide (Claude's discretion) |
| Run environment? | On bravos-vm1 with real IB Gateway / Locally with mocked IBKR / You decide | On bravos-vm1 with real IB Gateway (paper account) |

**Continue or next?** Next area

**Notes:** Scripted sequence was chosen for determinism — you know exactly what was tested. The user delegated the verification approach to Claude's discretion, which allows the planner to use DB state checks for OOH scenarios and IBKR position queries for in-hours scenarios. Full execution on bravos-vm1 is non-negotiable — partial mocked validation wouldn't satisfy the phase goal.

---

## Out-of-Hours Order Path

| Question | Options Presented | Selected |
|----------|------------------|----------|
| How to validate order→fill→reconcile OOH? | Run during market hours only / Add TEST_MODE bypass / Unit tests not live orders | Validate order path with unit tests, not live orders |
| Which unit tests need to exist? | Phase 4/5 stubs sufficient — unskip and run / Write new e2e tests with mock IBApp / You decide | You decide (Claude's discretion) |
| Should Phase 6 unskip Phase 4/5 stubs? | Yes — unskipping is Phase 6 scope / No — that's Phase 4/5 scope / You decide | You decide (Claude's discretion) |

**Ready for context?** Yes — I'm ready for context

**Notes:** The user rejected both the market-hours-only schedule and the bypass flag options, preferring the unit test route. This means the validation script's live run covers scrape→parse→risk gate only; the order→fill→lot path must be demonstrably covered by tests. The unskip strategy for Phase 4/5 stubs is left to Claude's discretion — the planner should assess the current state of `tests/test_execution.py` and `tests/test_positions.py` and decide whether unskipping existing stubs is sufficient or whether a new integration test is needed.

---

## Claude's Discretion Items

- Exact DB state assertions in the validation script
- Location of `BUG-LOG.md` and `VALIDATION-REPORT.md` (scripts/ vs docs/ vs validation/)
- Whether validation script tears down DB state between scenarios
- Unskip strategy for Phase 4/5 test stubs
- Pass/fail verification method (DB state vs DB + IBKR queries)

## Deferred Ideas

- Live account activation → Phase 8
- Automated daily validation via cron → Phase 8 hardening
- Gmail poller validation (secondary channel) → v2
