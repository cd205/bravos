---
phase: 2
slug: signal-ingestion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-08
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (~/miniconda3/bin/pytest) |
| **Config file** | pytest.ini or pyproject.toml (existing) |
| **Quick run command** | `~/miniconda3/bin/pytest tests/test_parser.py -x -q` |
| **Full suite command** | `~/miniconda3/bin/pytest tests/ -x -q` |
| **Estimated runtime** | ~10 seconds (parser unit tests); scraper tests require VM |

---

## Sampling Rate

- **After every task commit:** Run `~/miniconda3/bin/pytest tests/test_parser.py -x -q`
- **After every plan wave:** Run `~/miniconda3/bin/pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| schema-migration | 02-01 | 1 | AUDIT-01, INGST-06 | integration | `psql -c "\d signals"` verify columns | ❌ W0 | ⬜ pending |
| parser-unit | 02-02 | 1 | INGST-04, INGST-05 | unit | `pytest tests/test_parser.py -x -q` | ❌ W0 | ⬜ pending |
| scraper-module | 02-03 | 2 | INGST-01, INGST-02, INGST-07 | manual | Headed Chrome login + scrape | manual | ⬜ pending |
| db-write | 02-04 | 2 | INGST-03, INGST-06, AUDIT-06 | integration | `pytest tests/test_ingestion.py -x -q` | ❌ W0 | ⬜ pending |
| daemon | 02-05 | 3 | INGST-02, AUDIT-01 | manual | Run daemon 10 min, check DB rows | manual | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_parser.py` — stubs for INGST-04, INGST-05: test_ticker_extracted, test_action_type_extracted, test_weight_extracted, test_confidence_high, test_confidence_medium, test_confidence_low, test_spacy_fallback
- [ ] `tests/test_ingestion.py` — stubs for INGST-03, INGST-06, AUDIT-06: test_dedup_on_conflict, test_raw_html_stored, test_immutable_no_update
- [ ] `tests/conftest.py` — already exists; add `mock_db_connection` fixture for parser/ingestion tests that don't need live Cloud SQL

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Login to bravosresearch.com succeeds | INGST-01 | Requires live credentials + site | Run scraper in headed mode; verify redirect to member area |
| Session expiry detection triggers re-auth | INGST-07 | Can't force session expiry without live session | Let session expire overnight; verify re-auth log on next cycle |
| 5-minute polling fires correctly | INGST-02 | Requires live daemon + time | Run daemon 15 min; confirm 3 cycle logs in stdout |
| HTML selectors find post list and body | INGST-02, INGST-04 | Theme-specific selectors unknown | First-run selector discovery task (headless=False) |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
