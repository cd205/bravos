---
phase: 1
slug: infrastructure-setup
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-03
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + shell verification scripts |
| **Config file** | `pytest.ini` (Wave 0 gap — does not exist yet) |
| **Quick run command** | `pytest tests/test_infrastructure.py -x -q` |
| **Full suite command** | `pytest tests/ -q` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_infrastructure.py -x -q`
- **After every plan wave:** Run `pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 1 | DEPL-01 | smoke | `nc -zv 127.0.0.1 4002 && echo PASS` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | DEPL-03 | integration | `pytest tests/test_infrastructure.py::test_schema -x` | ❌ W0 | ⬜ pending |
| 1-03-01 | 03 | 1 | DEPL-04 | smoke | `pytest tests/test_infrastructure.py::test_secrets -x` | ❌ W0 | ⬜ pending |
| 1-04-01 | 04 | 1 | DEPL-05 | smoke | `pytest tests/test_infrastructure.py::test_chrome -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_infrastructure.py` — smoke + integration tests for DEPL-01, DEPL-03, DEPL-04, DEPL-05
- [ ] `pytest.ini` — root pytest config with testpaths and timeout settings
- [ ] `tests/conftest.py` — shared fixtures (DB connection string, Chrome options)
- [ ] `requirements-dev.txt` — `pytest==8.x`, `pytest-timeout`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| IB Gateway starts and accepts 2FA push | DEPL-01 | Requires human to approve IBKR Mobile 2FA notification | Run `systemctl start ibgateway`; approve push on phone; wait 30s; run `nc -zv 127.0.0.1 4002` |
| SSH access to bravos_vm1 | DEPL-01 | Requires SSH key + GCP console access | `gcloud compute ssh bravos_vm1 --zone=us-east1-b` |
| GCP Secret Manager service account binding | DEPL-04 | IAM changes verified via GCP console | Check Console → IAM → Service Account has `roles/secretmanager.secretAccessor` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
