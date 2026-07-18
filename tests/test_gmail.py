"""
Tests for scripts.run_gmail — bounded poller retry (Session 2c, IMPROVEMENTS.md item 17).

Pure-mock: no live IMAP, no live socket, no production DB. Every DB/IMAP/socket
touchpoint is monkeypatched at the module level. process_emails() is driven
directly with a fake IMAP object and a MagicMock db_conn cursor.

Requirement coverage: item-17
  - 10 consecutive failed dispatches of the same Message-ID -> mark-seen + one
    send_alert on the 10th; not before.
  - A 10-strike alert clears the counter (fresh count on next arrival).
  - A success after failures clears the counter.
  - A successful dispatch whose _set_gmail_message_id affects 0 rows falls back
    to _mark_message_id_seen; >0 rows does not.
"""
from unittest.mock import MagicMock, patch

import scripts.run_gmail as daemon


MESSAGE_ID = "<abc123@mail.gmail.com>"
URL = "https://bravosresearch.com/news-feed/some-post/"


def _reset_counters():
    daemon._dispatch_failures.clear()
    daemon._last_dispatch_error.clear()


def _wire_single_message(monkeypatch, *, dispatch_side_effect=None, dispatch_return=None,
                          set_gmail_message_id_return=1):
    """Wire process_emails' internals so a single seq_num/message/url flows through.

    dispatch_side_effect: list/iterable of bool (or an Exception) fed to
      _dispatch_url_to_daemon across successive calls, one per loop iteration
      the test drives. If provided, overrides dispatch_return.
    """
    monkeypatch.setattr(daemon, "_fetch_matching_seq_nums", MagicMock(return_value=[b"1"]))
    monkeypatch.setattr(daemon, "_get_message_id_header", MagicMock(return_value=MESSAGE_ID))
    monkeypatch.setattr(daemon, "_is_message_id_seen", MagicMock(return_value=False))
    monkeypatch.setattr(daemon, "_fetch_full_message", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(daemon, "_extract_urls", MagicMock(return_value=[URL]))

    dispatch_mock = MagicMock()
    if dispatch_side_effect is not None:
        dispatch_mock.side_effect = dispatch_side_effect
    else:
        dispatch_mock.return_value = dispatch_return
    monkeypatch.setattr(daemon, "_dispatch_url_to_daemon", dispatch_mock)

    set_id_mock = MagicMock(return_value=set_gmail_message_id_return)
    monkeypatch.setattr(daemon, "_set_gmail_message_id", set_id_mock)

    mark_seen_mock = MagicMock()
    monkeypatch.setattr(daemon, "_mark_message_id_seen", mark_seen_mock)

    alert_mock = MagicMock()
    monkeypatch.setattr(daemon, "send_alert", alert_mock)

    return dispatch_mock, set_id_mock, mark_seen_mock, alert_mock


def _run_once(imap=None, db_conn=None):
    imap = imap or MagicMock()
    db_conn = db_conn or MagicMock()
    return daemon.process_emails(imap, db_conn, since_date=None)


def test_retry_preserved_before_tenth_failure(monkeypatch):
    """9 consecutive failures: mark_seen and send_alert must NOT fire yet."""
    _reset_counters()
    _, _, mark_seen_mock, alert_mock = _wire_single_message(monkeypatch, dispatch_return=False)

    for _ in range(9):
        _run_once()

    mark_seen_mock.assert_not_called()
    alert_mock.assert_not_called()
    assert daemon._dispatch_failures[MESSAGE_ID] == 9


def test_tenth_consecutive_failure_marks_seen_and_alerts_once(monkeypatch):
    """10th consecutive failure: mark_seen called for the id, send_alert called
    exactly once, subject contains '10x', body contains message-id/url/last error."""
    _reset_counters()
    _, _, mark_seen_mock, alert_mock = _wire_single_message(monkeypatch, dispatch_return=False)
    # Seed the last-socket-response so the alert body has a concrete error.
    monkeypatch.setattr(daemon, "_last_socket_response", "ERR process_alert=failed")

    for _ in range(10):
        _run_once()

    assert mark_seen_mock.call_count == 1
    assert mark_seen_mock.call_args[0][1] == MESSAGE_ID
    alert_mock.assert_called_once()
    subject, body = alert_mock.call_args[0]
    assert "10x" in subject
    assert MESSAGE_ID in body
    assert URL in body
    assert "ERR process_alert=failed" in body


def test_counter_cleared_after_tenth_strike_alert(monkeypatch):
    """After the 10-strike alert fires, the counter is removed (fresh start)."""
    _reset_counters()
    _wire_single_message(monkeypatch, dispatch_return=False)

    for _ in range(10):
        _run_once()

    assert MESSAGE_ID not in daemon._dispatch_failures
    assert MESSAGE_ID not in daemon._last_dispatch_error


def test_success_after_failures_clears_counter(monkeypatch):
    """A success after some failures clears the counter; a later failure restarts at 1."""
    _reset_counters()
    dispatch_mock, _, mark_seen_mock, alert_mock = _wire_single_message(
        monkeypatch, dispatch_side_effect=[False, False, False, True, False]
    )

    for _ in range(5):
        _run_once()

    # After index 3 (success), counter cleared; index 4 (failure) restarts at 1.
    assert daemon._dispatch_failures[MESSAGE_ID] == 1
    mark_seen_mock.assert_not_called()  # never reached 10, and the one success had rowcount=1
    alert_mock.assert_not_called()


def test_success_with_zero_rows_falls_back_to_mark_seen(monkeypatch):
    """Successful dispatch where _set_gmail_message_id affects 0 rows -> mark_seen fallback.
    send_alert must NOT be called (this is a success, not a failure)."""
    _reset_counters()
    _, set_id_mock, mark_seen_mock, alert_mock = _wire_single_message(
        monkeypatch, dispatch_return=True, set_gmail_message_id_return=0
    )

    _run_once()

    set_id_mock.assert_called_once()
    mark_seen_mock.assert_called_once()
    assert mark_seen_mock.call_args[0][1] == MESSAGE_ID
    alert_mock.assert_not_called()


def test_success_with_nonzero_rows_does_not_mark_seen(monkeypatch):
    """Successful dispatch where _set_gmail_message_id affects >0 rows -> no fallback."""
    _reset_counters()
    _, set_id_mock, mark_seen_mock, alert_mock = _wire_single_message(
        monkeypatch, dispatch_return=True, set_gmail_message_id_return=1
    )

    _run_once()

    set_id_mock.assert_called_once()
    mark_seen_mock.assert_not_called()
    alert_mock.assert_not_called()


def test_success_clears_counter_and_last_error(monkeypatch):
    """A bare success (no prior failures) leaves no residual counter/error entries."""
    _reset_counters()
    daemon._dispatch_failures[MESSAGE_ID] = 3
    daemon._last_dispatch_error[MESSAGE_ID] = "some previous error"
    _wire_single_message(monkeypatch, dispatch_return=True, set_gmail_message_id_return=1)

    _run_once()

    assert MESSAGE_ID not in daemon._dispatch_failures
    assert MESSAGE_ID not in daemon._last_dispatch_error


def test_set_gmail_message_id_returns_rowcount():
    """_set_gmail_message_id reports cur.rowcount, not None."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_cur.rowcount = 1
    fake_conn.cursor.return_value.__enter__.return_value = fake_cur
    result = daemon._set_gmail_message_id(fake_conn, MESSAGE_ID, URL)
    assert result == 1
    fake_conn.commit.assert_called_once()
