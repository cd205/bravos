"""
bravos/notifications/notifier.py — Fire-and-forget email alerting (Phase 7).

Single public function: send_alert(subject, body).
Never raises — logs warning on any failure so the daemon never crashes.

record_parse_outcome(parsed) tracks a rolling window of parse results
and fires send_alert() once when 3+ of the last 10 signals fail (D-03).
"""
import smtplib
import logging
import threading
from collections import deque
from email.mime.text import MIMEText

from bravos.config.secrets_config import get_secret
from bravos.config.settings import ALERT_EMAIL

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SUBJECT_PREFIX = "[Bravos Alert]"

# Parse spike rolling window state (D-03, Option C: lives here to avoid
# reverse dependency from scraper.py back into run_ingestion.py)
_parse_outcomes: deque = deque(maxlen=10)   # True=success, False=failure
_spike_alerted: bool = False
_spike_lock = threading.Lock()
SPIKE_THRESHOLD = 3


def send_alert(subject: str, body: str) -> None:
    """Fire-and-forget email alert. Never raises — logs warning on failure."""
    if not ALERT_EMAIL:
        logger.warning("send_alert: ALERT_EMAIL not set — skipping alert: %s", subject)
        return
    try:
        smtp_password = get_secret("bravos-alert-smtp-password")
        smtp_from = get_secret("bravos-alert-smtp-from")
        msg = MIMEText(body, "plain")
        msg["Subject"] = f"{SUBJECT_PREFIX} {subject}"
        msg["From"] = smtp_from
        msg["To"] = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_from, smtp_password)
            server.sendmail(smtp_from, [ALERT_EMAIL], msg.as_string())
        logger.info("Alert sent: %s", subject)
    except Exception:
        logger.warning("send_alert failed for subject=%r — continuing", subject, exc_info=True)


def record_parse_outcome(parsed: dict) -> None:
    """Record parse result; emit alert once if failure spike detected (D-03).

    A 'failure' is confidence == 'low' OR ticker IS None.
    Fires send_alert() at most once per spike window breach.
    Re-arms when failure_count drops back below SPIKE_THRESHOLD.
    """
    global _spike_alerted
    import datetime as _dt
    is_failure = (
        parsed.get("confidence") == "low"
        or parsed.get("ticker") is None
    )
    _parse_outcomes.append(not is_failure)  # True = success

    # WR-02: lock the check-then-set so concurrent callers (Gmail poller thread +
    # main thread) cannot both pass the `not _spike_alerted` guard and double-fire.
    with _spike_lock:
        failure_count = sum(1 for ok in _parse_outcomes if not ok)
        if failure_count >= SPIKE_THRESHOLD and not _spike_alerted:
            _spike_alerted = True
            should_alert = True
            alert_count = failure_count
            alert_window = len(_parse_outcomes)
            alert_ts = _dt.datetime.now().isoformat()
        elif failure_count < SPIKE_THRESHOLD:
            _spike_alerted = False
            should_alert = False
        else:
            should_alert = False

    if should_alert:
        logger.error(
            "Parse failure spike: %d failures in last %d signals",
            alert_count, alert_window,
        )
        send_alert(
            "Parse Failure Spike",
            f"Parse failure spike detected at {alert_ts}\n"
            f"Failures: {alert_count} out of last {alert_window} signals\n"
            f"Check bravosresearch.com post format for unexpected changes.",
        )
