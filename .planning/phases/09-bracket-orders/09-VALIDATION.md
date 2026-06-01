---
phase: 9
slug: bracket-orders
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-01
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `pytest.ini` |
| **Quick run command** | `cd /home/chris_s_dodd/bravos && python -m pytest tests/test_parser.py tests/test_execution.py -x -m "not integration"` |
| **Full suite command** | `cd /home/chris_s_dodd/bravos && python -m pytest tests/ -m "not integration"` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_parser.py tests/test_execution.py -x -m "not integration"`
- **After every plan wave:** Run full suite `python -m pytest tests/ -m "not integration"`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 9-01-01 | 01 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_parser.py::test_extract_stop_loss_structured -x` | ❌ W0 | ⬜ pending |
| 9-01-02 | 01 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_parser.py::test_extract_stop_loss_prose -x` | ❌ W0 | ⬜ pending |
| 9-01-03 | 01 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_parser.py::test_extract_take_profits_structured -x` | ❌ W0 | ⬜ pending |
| 9-01-04 | 01 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_parser.py::test_extract_take_profits_prose -x` | ❌ W0 | ⬜ pending |
| 9-01-05 | 01 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_parser.py::test_parse_signal_returns_tp_sl -x` | ❌ W0 | ⬜ pending |
| 9-01-06 | 01 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_parser.py::test_parse_signal_no_tp_sl -x` | ❌ W0 | ⬜ pending |
| 9-02-01 | 02 | 0 | EXEC-07 | — | N/A | unit+mock | `pytest tests/test_execution.py::test_bracket_db_writes -x` | ❌ W0 | ⬜ pending |
| 9-02-02 | 02 | 0 | EXEC-07 | — | N/A | unit+mock | `pytest tests/test_execution.py::test_bracket_transmit_sequence -x` | ❌ W0 | ⬜ pending |
| 9-02-03 | 02 | 0 | EXEC-07 | — | N/A | unit+mock | `pytest tests/test_execution.py::test_bracket_parent_id -x` | ❌ W0 | ⬜ pending |
| 9-02-04 | 02 | 0 | EXEC-07 | — | N/A | unit+mock | `pytest tests/test_execution.py::test_bracket_oca_group -x` | ❌ W0 | ⬜ pending |
| 9-02-05 | 02 | 0 | EXEC-07 | — | N/A | unit | `pytest tests/test_execution.py::test_bracket_quantity_split -x` | ❌ W0 | ⬜ pending |
| 9-02-06 | 02 | 0 | EXEC-07 | — | N/A | unit+mock | `pytest tests/test_execution.py::test_bracket_fallback_no_tpsl -x` | ❌ W0 | ⬜ pending |
| 9-02-07 | 02 | 0 | EXEC-07 | — | N/A | unit+mock | `pytest tests/test_execution.py::test_bracket_fallback_qty_too_small -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_parser.py` — add stubs: `test_extract_stop_loss_structured`, `test_extract_stop_loss_prose`, `test_extract_take_profits_structured`, `test_extract_take_profits_prose`, `test_parse_signal_returns_tp_sl`, `test_parse_signal_no_tp_sl`
- [ ] `tests/test_execution.py` — add stubs: `test_bracket_db_writes`, `test_bracket_transmit_sequence`, `test_bracket_parent_id`, `test_bracket_oca_group`, `test_bracket_quantity_split`, `test_bracket_fallback_no_tpsl`, `test_bracket_fallback_qty_too_small`

No new test infrastructure (conftest.py, framework install) required. All tests follow existing Wave 0 skip-stub pattern.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `migrate_phase9.sql` is idempotent | EXEC-07 | DDL execution requires live DB connection | Run `psql $DATABASE_URL -f infra/migrate_phase9.sql` twice; verify no error on second run |
| Bracket order visible in TWS with STP+LMT children | EXEC-07 | Requires paper trading IBKR session | Run against paper account with BYPASS_MARKET_HOURS=true; verify TWS shows parent MKT + STP + LMT chain |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
