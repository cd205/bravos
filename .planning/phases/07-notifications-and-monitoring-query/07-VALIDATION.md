# Phase 07: notifications-and-monitoring-query — Validation Strategy

**Phase:** 7
**Phase slug:** notifications-and-monitoring-query
**Date:** 2026-05-20
**Source:** Derived from 07-RESEARCH.md § Validation Architecture

---

## Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing, pytest.ini present) |
| Config file | `pytest.ini` — `testpaths = tests`, `addopts = -m "not integration"`, `pythonpath = .` |
| Quick run command | `python -m pytest tests/test_notifications.py -x` |
| Full suite command | `python -m pytest tests/ -x` |

---

## Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | Status |
|--------|----------|-----------|-------------------|--------|
| NOTF-01 | Circuit breaker email fires once when `_circuit_tripped` latches | unit | `python -m pytest tests/test_notifications.py::test_circuit_breaker_sends_alert -x` | Wave 1 |
| NOTF-01 | Circuit breaker email does NOT fire again on subsequent gate blocks (already tripped) | unit | `python -m pytest tests/test_notifications.py::test_circuit_breaker_no_duplicate_alert -x` | Wave 1 |
| NOTF-02 | IBKR disconnect alert fires at attempt == len(_RETRY_DELAYS) | unit | `python -m pytest tests/test_notifications.py::test_ibkr_disconnect_alert -x` | Wave 2 |
| NOTF-02 | Re-auth failure sends alert | unit | `python -m pytest tests/test_notifications.py::test_reauth_failure_alert -x` | Wave 2 |
| NOTF-02 | Parse spike: 3 failures in 10 sends one alert | unit | `python -m pytest tests/test_notifications.py::test_parse_spike_alert -x` | Wave 1 |
| NOTF-02 | Parse spike: does not re-alert after first breach until window recovers | unit | `python -m pytest tests/test_notifications.py::test_parse_spike_no_duplicate -x` | Wave 1 |
| NOTF-01/02 | send_alert() with missing ALERT_EMAIL logs warning and returns without sending | unit | `python -m pytest tests/test_notifications.py::test_send_alert_no_recipient -x` | Wave 1 |
| NOTF-01/02 | send_alert() with SMTP failure logs warning and does not raise | unit | `python -m pytest tests/test_notifications.py::test_send_alert_smtp_failure_suppressed -x` | Wave 1 |
| D-12 | monitor.sql file exists and is readable | smoke | `psql -h 127.0.0.1 -U bravos -d bravos_trading -f queries/monitor.sql` | Wave 1 |

---

## Sampling Rate

- **Per task commit:** `python -m pytest tests/test_notifications.py -x`
- **Per wave merge:** `python -m pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

---

## Wave 0 (Pre-execution) Gaps

- [ ] `tests/test_notifications.py` — covers all 8 NOTF test cases above
- [ ] `queries/` directory — `queries/monitor.sql`
- [ ] `bravos/notifications/__init__.py` — empty init for new subpackage

*(All other test infrastructure exists — pytest.ini, conftest.py with db_connection fixture, existing test files)*

---

## Security Validation

| ASVS Category | Applies | Control |
|---------------|---------|---------|
| V6 Cryptography | yes | TLS via STARTTLS — never send SMTP credentials in plaintext |
| V1 Architecture / Secrets | yes | SMTP password and sender via GCP Secret Manager; recipient via env var only |

### Threat Mitigations to Verify

| Threat | Mitigation | Verification |
|--------|-----------|--------------|
| SMTP credentials in code | `get_secret()` pattern | grep for hardcoded passwords: `grep -r "smtp.*pass\|password.*@" bravos/` returns 0 matches |
| Plaintext SMTP | `server.starttls()` before login | grep `starttls` in `notifier.py` returns ≥1 match |
| Alert flooding | Structural throttling (latch / once-per-breach) | Unit tests `test_circuit_breaker_no_duplicate_alert`, `test_parse_spike_no_duplicate` verify single-fire |
