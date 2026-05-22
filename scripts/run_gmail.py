#!/usr/bin/env python3
"""
Bravos Trading System — Gmail Poller (INGST-V2-01).

Polls Gmail via IMAP for Bravos Trade Alert notification emails, extracts
post URLs, and calls scraper.process_alert(url) for each new alert.

Dedup: each processed email is recorded by its Gmail Message-ID in the
signals.gmail_message_id column (UNIQUE). An email is skipped if its
Message-ID already exists in the DB — even across restarts or --since
backfill runs. The existing signals.post_url UNIQUE constraint provides
a second independent dedup guard in _store_signal().

Usage:
    python scripts/run_gmail.py                     # poll from now
    python scripts/run_gmail.py --since 2026-05-01  # backfill from date

Setup (one-time):
    1. Enable 2-Step Verification on your Google account.
    2. Generate an App Password at myaccount.google.com/apppasswords
       (select "Mail" + "Other device", label it "bravos-vm1").
    3. Store it in GCP Secret Manager:
         echo -n "your-16-char-app-password" | \
           gcloud secrets create bravos-gmail-app-password --data-file=-
       (or update if it already exists):
         echo -n "your-16-char-app-password" | \
           gcloud secrets versions add bravos-gmail-app-password --data-file=-
    4. Apply the DB migration on bravos-vm1:
         psql -h 127.0.0.1 -U bravos bravos_trading -f infra/migrate_gmail.sql

Environment variables read at runtime:
    ALERT_EMAIL        — Gmail account to poll (set in /etc/bravos/env)
    BRAVOS_DB_PASSWORD — DB password (set in /etc/bravos/env)
"""
import argparse
import email
import imaplib
import os
import re
import signal
import sys
import time
import logging
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from bravos.config import settings
from bravos.config.secrets_config import get_secret
from bravos.ingestion.scraper import BravosScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.gmail.daemon")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
POLL_INTERVAL_SECONDS = 60

# Matches any https://bravosresearch.com/... URL in an email body.
_URL_RE = re.compile(r"https://bravosresearch\.com/[^\s\"'<>]+", re.IGNORECASE)

_shutdown = False


def _handle_shutdown(signum, frame):
    global _shutdown
    logger.info("Received signal %s — initiating graceful shutdown", signum)
    _shutdown = True


def _db_connect() -> psycopg2.extensions.connection:
    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=password,
    )


def _is_message_id_seen(conn, message_id: str) -> bool:
    """Return True if this Gmail Message-ID is already in the signals table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM signals WHERE gmail_message_id = %s LIMIT 1",
            (message_id,),
        )
        return cur.fetchone() is not None


def _mark_message_id_seen(conn, message_id: str):
    """Insert a placeholder row so this Message-ID is never reprocessed.

    If process_alert() already inserted the row (with a real signal_id), the
    ON CONFLICT DO NOTHING here is a no-op. If the email body had no parseable
    URL (or the URL was already deduped via post_url), we still record the
    Message-ID so the email is not retried on the next poll cycle.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signals
                (post_url, post_title, raw_html, gmail_message_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (post_url) DO UPDATE
                SET gmail_message_id = EXCLUDED.gmail_message_id
                WHERE signals.gmail_message_id IS NULL
            """,
            (
                f"gmail:no-url:{message_id}",
                "(no URL extracted)",
                "",
                message_id,
            ),
        )
    conn.commit()


def _set_gmail_message_id(conn, message_id: str, post_url: str):
    """Back-fill gmail_message_id onto the signal row that was just inserted by process_alert()."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE signals
               SET gmail_message_id = %s
             WHERE post_url = %s
               AND gmail_message_id IS NULL
            """,
            (message_id, post_url),
        )
    conn.commit()


def _extract_urls(msg: email.message.Message) -> list[str]:
    """Extract all bravosresearch.com URLs from email body (plain text or HTML)."""
    urls = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    urls.extend(_URL_RE.findall(body))
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            urls.extend(_URL_RE.findall(body))
        except Exception:
            pass
    # deduplicate while preserving order
    seen: set[str] = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _imap_search_criteria(since_date: date | None) -> str:
    """Build IMAP SEARCH criteria string."""
    parts = [
        f'FROM "bravosresearch.com"',
        f'SUBJECT "{settings.GMAIL_SUBJECT_KEYWORD}"',
    ]
    if since_date is not None:
        # IMAP date format: DD-Mon-YYYY
        parts.append(f'SINCE "{since_date.strftime("%d-%b-%Y")}"')
    return " ".join(parts)


def _fetch_matching_message_ids(imap: imaplib.IMAP4_SSL, since_date: date | None) -> list[bytes]:
    """Search INBOX for matching messages; return list of IMAP sequence numbers."""
    criteria = _imap_search_criteria(since_date)
    status, data = imap.search(None, criteria)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _get_message_id_header(imap: imaplib.IMAP4_SSL, seq_num: bytes) -> str | None:
    """Fetch only the Message-ID header for a message (avoids downloading full body twice)."""
    status, data = imap.fetch(seq_num, "(BODY[HEADER.FIELDS (MESSAGE-ID)])")
    if status != "OK" or not data or not data[0]:
        return None
    raw = data[0][1].decode("utf-8", errors="replace")
    for line in raw.splitlines():
        if line.lower().startswith("message-id:"):
            return line.split(":", 1)[1].strip()
    return None


def _fetch_full_message(imap: imaplib.IMAP4_SSL, seq_num: bytes) -> email.message.Message | None:
    status, data = imap.fetch(seq_num, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None
    return email.message_from_bytes(data[0][1])


def _connect_imap(gmail_account: str, app_password: str) -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(gmail_account, app_password)
    imap.select("INBOX")
    return imap


def process_emails(
    imap: imaplib.IMAP4_SSL,
    scraper: BravosScraper,
    db_conn: psycopg2.extensions.connection,
    since_date: date | None,
) -> int:
    """Fetch matching emails, dedup, and call process_alert() for each new URL.

    Returns the count of new alerts dispatched.
    """
    seq_nums = _fetch_matching_message_ids(imap, since_date)
    if not seq_nums:
        return 0

    dispatched = 0
    for seq_num in seq_nums:
        if _shutdown:
            break

        message_id = _get_message_id_header(imap, seq_num)
        if not message_id:
            logger.warning("Could not read Message-ID for seq %s — skipping", seq_num)
            continue

        if _is_message_id_seen(db_conn, message_id):
            logger.debug("Already processed Message-ID %s — skipping", message_id)
            continue

        msg = _fetch_full_message(imap, seq_num)
        if msg is None:
            logger.warning("Could not fetch full message for seq %s — skipping", seq_num)
            continue

        urls = _extract_urls(msg)
        if not urls:
            logger.warning(
                "Message-ID %s matched subject/sender but contained no bravosresearch.com URL — marking seen",
                message_id,
            )
            _mark_message_id_seen(db_conn, message_id)
            continue

        for url in urls:
            logger.info("Dispatching alert: Message-ID=%s url=%s", message_id, url)
            try:
                scraper.process_alert(url)
                # Back-fill the message_id onto the signal row process_alert() just wrote.
                _set_gmail_message_id(db_conn, message_id, url)
                dispatched += 1
            except Exception:
                logger.exception("process_alert failed for url=%s — continuing", url)
                # Still mark seen so we don't retry a broken URL forever.
                _mark_message_id_seen(db_conn, message_id)

    return dispatched


def main():
    parser = argparse.ArgumentParser(description="Bravos Gmail alert poller")
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Process emails on or after this date (backfill). Default: all unprocessed emails.",
        default=None,
    )
    args = parser.parse_args()

    since_date: date | None = None
    if args.since:
        try:
            since_date = datetime.strptime(args.since, "%Y-%m-%d").date()
            logger.info("Backfill mode: processing emails since %s", since_date)
        except ValueError:
            logger.error("--since must be YYYY-MM-DD, got: %s", args.since)
            sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    gmail_account = settings.ALERT_EMAIL
    if not gmail_account:
        logger.error("ALERT_EMAIL is not set — cannot connect to Gmail. Set it in /etc/bravos/env.")
        sys.exit(1)

    logger.info("Fetching Gmail app password from Secret Manager...")
    try:
        app_password = get_secret("bravos-gmail-app-password")
    except Exception:
        logger.exception("Failed to fetch bravos-gmail-app-password from Secret Manager. "
                         "See setup instructions in this file's module docstring.")
        sys.exit(1)

    logger.info("Starting BravosScraper...")
    scraper = BravosScraper()
    try:
        scraper.startup()
    except Exception:
        logger.exception("BravosScraper startup failed")
        sys.exit(1)

    logger.info("Connecting to Gmail IMAP as %s...", gmail_account)
    try:
        imap = _connect_imap(gmail_account, app_password)
    except Exception:
        logger.exception("IMAP login failed for %s", gmail_account)
        scraper.shutdown()
        sys.exit(1)

    db_conn = _db_connect()
    logger.info(
        "Gmail poller running — account=%s since=%s poll_interval=%ds",
        gmail_account,
        since_date or "all unprocessed",
        POLL_INTERVAL_SECONDS,
    )

    try:
        while not _shutdown:
            try:
                # Re-SELECT INBOX to pick up new messages on each poll cycle.
                imap.select("INBOX")
                dispatched = process_emails(imap, scraper, db_conn, since_date)
                if dispatched:
                    logger.info("Poll cycle: dispatched %d new alert(s)", dispatched)
                else:
                    logger.debug("Poll cycle: no new alerts")

                # After the first backfill pass, only look at emails from today
                # onwards so IMAP search stays fast on large inboxes.
                if since_date is not None:
                    since_date = date.today()

            except imaplib.IMAP4.abort:
                # Connection dropped — reconnect and continue.
                logger.warning("IMAP connection aborted — reconnecting")
                try:
                    imap = _connect_imap(gmail_account, app_password)
                except Exception:
                    logger.exception("IMAP reconnect failed — will retry next cycle")
            except Exception:
                logger.exception("Poll cycle error — will retry next cycle")

            # Sleep in 1s ticks so SIGTERM is handled promptly.
            for _ in range(POLL_INTERVAL_SECONDS):
                if _shutdown:
                    break
                time.sleep(1)
    finally:
        logger.info("Shutting down Gmail poller")
        try:
            imap.logout()
        except Exception:
            pass
        try:
            db_conn.close()
        except Exception:
            pass
        scraper.shutdown()
        logger.info("Gmail poller stopped")


if __name__ == "__main__":
    main()
