"""
Wave 0 test stubs for ingestion DB operations.

Tests use the db_connection fixture from conftest.py (live Cloud SQL).
Stubs are @pytest.mark.skip until plan 02-04 implements the DB write layer.

Requirement coverage:
  INGST-03 -> test_dedup_on_conflict
  INGST-06 -> test_raw_html_stored
  AUDIT-01 -> test_audit_fields_populated

run_cycle crash-safety / self-healing (quick-260708-7lk):
  Pure-mock tests — no DB, no live Chrome. See the "run_cycle dead-driver
  recovery" section at the bottom of this file.
"""
import pytest
from unittest.mock import MagicMock


def test_dedup_on_conflict(db_connection):
    """INGST-03: Duplicate post_url is silently ignored."""
    test_url = "https://test.bravosresearch.com/phase2-dedup-fixture"
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html) VALUES (%s, 'Test', '<p>test</p>') ON CONFLICT (post_url) DO NOTHING",
            (test_url,)
        )
        db_connection.commit()
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html) VALUES (%s, 'Dup', '<p>dup</p>') ON CONFLICT (post_url) DO NOTHING",
            (test_url,)
        )
        db_connection.commit()
        cur.execute("SELECT COUNT(*) FROM signals WHERE post_url = %s", (test_url,))
        count = cur.fetchone()[0]
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()
    assert count == 1


def test_raw_html_stored(db_connection):
    """INGST-06: raw_html column is populated for every signal."""
    test_url = "https://test.bravosresearch.com/phase2-raw-html-fixture"
    raw = "<div class='entry-content'><p>Trade alert body</p></div>"
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html, ticker, action_type, confidence, parse_method, scraped_at) VALUES (%s, 'Test Title', %s, 'EME', 'open', 'high', 'regex', NOW()) ON CONFLICT (post_url) DO NOTHING",
            (test_url, raw)
        )
        db_connection.commit()
        cur.execute("SELECT raw_html FROM signals WHERE post_url = %s", (test_url,))
        row = cur.fetchone()
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()
    assert row is not None
    assert row[0] == raw


def test_audit_fields_populated(db_connection):
    """AUDIT-01: Every signal has scraped_at and parse_method populated."""
    test_url = "https://test.bravosresearch.com/phase2-audit-fixture"
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, raw_html, parse_method, scraped_at) VALUES (%s, 'Audit Test', '<p>audit</p>', 'regex', NOW()) ON CONFLICT (post_url) DO NOTHING",
            (test_url,)
        )
        db_connection.commit()
        cur.execute("SELECT parse_method, scraped_at FROM signals WHERE post_url = %s", (test_url,))
        row = cur.fetchone()
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()
    assert row is not None
    assert row[0] == "regex"
    assert row[1] is not None  # scraped_at timestamp


# ── run_cycle dead-driver recovery (quick-260708-7lk) ───────────────────────
# Pure-mock tests: no db_connection fixture, no live Chrome. run_cycle is
# decorated with @catch_cycle_exceptions, so an unexpected raise is swallowed
# and logged — to prove "triggers recovery" we assert on the recovery mock
# being called, not just on the absence of exceptions.

import scripts.run_ingestion as daemon


def _make_dead_scraper(login_ok: bool = False) -> MagicMock:
    """Fake scraper whose session check fails (expired session or dead driver)."""
    fake = MagicMock()
    fake._check_session.return_value = False
    fake._login.return_value = login_ok
    return fake


def test_run_cycle_survives_dead_driver(monkeypatch):
    """A dead driver must never let an exception escape run_cycle."""
    fake = _make_dead_scraper(login_ok=False)
    monkeypatch.setattr(daemon, "_scraper", fake)
    monkeypatch.setattr(daemon, "_recover_scraper", MagicMock(return_value=True))
    monkeypatch.setattr(daemon, "_consecutive_recovery_failures", 0)
    monkeypatch.setattr("bravos.runtime.ibapp", None)

    daemon.run_cycle()  # must NOT raise (schedule bookkeeping stays safe)


def test_run_cycle_triggers_recovery_on_dead_driver(monkeypatch):
    """Dead driver (session bad, re-login fails) triggers full driver recreation."""
    fake = _make_dead_scraper(login_ok=False)
    recover_mock = MagicMock(return_value=True)
    monkeypatch.setattr(daemon, "_scraper", fake)
    monkeypatch.setattr(daemon, "_recover_scraper", recover_mock)
    monkeypatch.setattr(daemon, "_consecutive_recovery_failures", 0)
    monkeypatch.setattr("bravos.runtime.ibapp", None)

    daemon.run_cycle()

    assert recover_mock.called is True
    reason = recover_mock.call_args[0][0]
    assert "recovery" in reason


def test_run_cycle_login_recovers_expired_session_without_recreation(monkeypatch):
    """Expired session with a healthy driver takes the cheap re-login path only."""
    fake = _make_dead_scraper(login_ok=True)
    recover_mock = MagicMock(return_value=True)
    monkeypatch.setattr(daemon, "_scraper", fake)
    monkeypatch.setattr(daemon, "_recover_scraper", recover_mock)
    monkeypatch.setattr(daemon, "_consecutive_recovery_failures", 0)
    monkeypatch.setattr("bravos.runtime.ibapp", None)

    daemon.run_cycle()

    assert recover_mock.called is False


def test_run_cycle_alerts_after_threshold_failures(monkeypatch):
    """After RECOVERY_ALERT_THRESHOLD consecutive failed recoveries, send_alert fires
    — and run_cycle keeps retrying (never raises) across all iterations."""
    fake = _make_dead_scraper(login_ok=False)
    alert_mock = MagicMock()
    monkeypatch.setattr(daemon, "_scraper", fake)
    monkeypatch.setattr(daemon, "_recover_scraper", MagicMock(return_value=False))
    monkeypatch.setattr(daemon, "send_alert", alert_mock)
    monkeypatch.setattr(daemon, "_consecutive_recovery_failures", 0)
    monkeypatch.setattr("bravos.runtime.ibapp", None)

    for _ in range(daemon.RECOVERY_ALERT_THRESHOLD):
        daemon.run_cycle()  # must never raise

    assert alert_mock.called is True


# ── _handle_alert_url socket seam (Session 2c, IMPROVEMENTS.md item 17) ─────
# Pure-mock: monkeypatch daemon._scraper directly, no real Chrome/DB.

def test_handle_alert_url_ok_on_stored(monkeypatch):
    fake = MagicMock()
    fake.process_alert.return_value = "stored"
    monkeypatch.setattr(daemon, "_scraper", fake)
    assert daemon._handle_alert_url("https://x.com/a/") == "OK"


def test_handle_alert_url_ok_on_duplicate(monkeypatch):
    fake = MagicMock()
    fake.process_alert.return_value = "duplicate"
    monkeypatch.setattr(daemon, "_scraper", fake)
    assert daemon._handle_alert_url("https://x.com/a/") == "OK"


def test_handle_alert_url_err_on_failed(monkeypatch):
    fake = MagicMock()
    fake.process_alert.return_value = "failed"
    monkeypatch.setattr(daemon, "_scraper", fake)
    result = daemon._handle_alert_url("https://x.com/a/")
    assert result.startswith("ERR")


def test_handle_alert_url_err_on_none(monkeypatch):
    """Defensive: process_alert returning None (should not happen post-2c) is still ERR."""
    fake = MagicMock()
    fake.process_alert.return_value = None
    monkeypatch.setattr(daemon, "_scraper", fake)
    result = daemon._handle_alert_url("https://x.com/a/")
    assert result.startswith("ERR")


def test_handle_alert_url_err_on_raise(monkeypatch):
    fake = MagicMock()
    fake.process_alert.side_effect = RuntimeError("boom")
    monkeypatch.setattr(daemon, "_scraper", fake)
    result = daemon._handle_alert_url("https://x.com/a/")
    assert result.startswith("ERR")


def test_handle_alert_url_err_when_scraper_not_ready(monkeypatch):
    monkeypatch.setattr(daemon, "_scraper", None)
    result = daemon._handle_alert_url("https://x.com/a/")
    assert result == "ERR scraper not ready"
