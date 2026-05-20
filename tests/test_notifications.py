"""
tests/test_notifications.py — Phase 7 Notifier unit tests.

Tests the send_alert() and record_parse_outcome() functions in
bravos/notifications/notifier.py. All tests use mocks — no real SMTP
or GCP Secret Manager calls are made.
"""
import importlib
from unittest.mock import MagicMock, patch, call
import pytest


def _reload_notifier():
    """Reload notifier module to reset module-level state (_parse_outcomes, _spike_alerted)."""
    import bravos.notifications.notifier as m
    importlib.reload(m)
    return m


# ── send_alert() tests ──────────────────────────────────────────────────────


def test_send_alert_no_recipient(monkeypatch, caplog):
    """send_alert() with missing ALERT_EMAIL logs warning and returns without sending."""
    import bravos.notifications.notifier as notifier
    monkeypatch.setattr(notifier, "ALERT_EMAIL", "")
    with patch.object(notifier, "get_secret") as mock_secret, \
         patch("smtplib.SMTP") as mock_smtp:
        notifier.send_alert("test subject", "test body")
    mock_secret.assert_not_called()
    mock_smtp.assert_not_called()
    assert any("ALERT_EMAIL not set" in r.message for r in caplog.records)


def test_send_alert_smtp_failure_suppressed(monkeypatch):
    """send_alert() with SMTP failure logs warning and does not raise."""
    import bravos.notifications.notifier as notifier
    monkeypatch.setattr(notifier, "ALERT_EMAIL", "test@example.com")
    with patch.object(notifier, "get_secret", return_value="fake"), \
         patch("bravos.notifications.notifier.smtplib.SMTP", side_effect=OSError("refused")):
        notifier.send_alert("test subject", "test body")  # must not raise


def test_send_alert_sends_correctly(monkeypatch):
    """send_alert() connects, STARTTLSes, logins, and sendmails to ALERT_EMAIL."""
    import bravos.notifications.notifier as notifier
    monkeypatch.setattr(notifier, "ALERT_EMAIL", "recipient@example.com")

    mock_server = MagicMock()
    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    with patch.object(notifier, "get_secret", side_effect=["smtp-pass", "sender@gmail.com"]), \
         patch("bravos.notifications.notifier.smtplib.SMTP", mock_smtp_cls):
        notifier.send_alert("Circuit Breaker Triggered", "daily_pnl=-9999")

    mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("sender@gmail.com", "smtp-pass")
    send_args = mock_server.sendmail.call_args
    assert send_args[0][1] == ["recipient@example.com"]
    assert "[Bravos Alert] Circuit Breaker Triggered" in send_args[0][2]


# ── record_parse_outcome() tests ────────────────────────────────────────────


def test_parse_spike_alert_fires_once(monkeypatch):
    """3 failures in 10 calls fires send_alert exactly once."""
    notifier = _reload_notifier()
    monkeypatch.setattr(notifier, "ALERT_EMAIL", "test@example.com")
    with patch.object(notifier, "get_secret", return_value="x"), \
         patch("bravos.notifications.notifier.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = MagicMock()
        # 7 successes + 3 failures = spike (3 of 10)
        for _ in range(7):
            notifier.record_parse_outcome({"confidence": "high", "ticker": "AAPL"})
        for _ in range(3):
            notifier.record_parse_outcome({"confidence": "low", "ticker": None})
    # send_alert called exactly once via SMTP
    assert mock_smtp.call_count == 1


def test_parse_spike_no_duplicate(monkeypatch):
    """Once spike fires, subsequent failures do not re-fire the alert."""
    notifier = _reload_notifier()
    monkeypatch.setattr(notifier, "ALERT_EMAIL", "test@example.com")
    with patch.object(notifier, "get_secret", return_value="x"), \
         patch("bravos.notifications.notifier.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = MagicMock()
        # fill with 3 failures (spike fires)
        for _ in range(3):
            notifier.record_parse_outcome({"confidence": "low", "ticker": None})
        count_after_spike = mock_smtp.call_count
        # add 3 more failures — should NOT re-fire
        for _ in range(3):
            notifier.record_parse_outcome({"confidence": "low", "ticker": None})
    assert mock_smtp.call_count == count_after_spike  # no new SMTP calls


def test_parse_spike_rearms_after_recovery(monkeypatch):
    """After window recovers (failure_count < 3), next spike fires again."""
    notifier = _reload_notifier()
    monkeypatch.setattr(notifier, "ALERT_EMAIL", "test@example.com")
    with patch.object(notifier, "get_secret", return_value="x"), \
         patch("bravos.notifications.notifier.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = MagicMock()
        # First spike: 3 failures
        for _ in range(3):
            notifier.record_parse_outcome({"confidence": "low", "ticker": None})
        # Recovery: push 10 successes (evicts all failures from deque)
        for _ in range(10):
            notifier.record_parse_outcome({"confidence": "high", "ticker": "AAPL"})
        count_after_recovery = mock_smtp.call_count
        # Second spike: 3 more failures
        for _ in range(3):
            notifier.record_parse_outcome({"confidence": "low", "ticker": None})
    assert mock_smtp.call_count == count_after_recovery + 1  # fired again
