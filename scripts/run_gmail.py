#!/usr/bin/env python3
"""
Bravos Trading System — Gmail Poller (INGST-V2-01).

Polls Gmail via IMAP for Bravos Trade Alert notification emails, extracts
post URLs, and dispatches them to the trading daemon via Unix domain socket.
The trading daemon owns the BravosScraper / Chrome session — this process
does IMAP only, keeping Chrome instances to exactly one.

Dedup: each processed email is recorded by its Gmail Message-ID in the
signals.gmail_message_id column (UNIQUE). An email is skipped if its
Message-ID already exists in the DB — even across restarts or --since
backfill runs. The existing signals.post_url UNIQUE constraint in
_store_signal() provides a second independent dedup guard.

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
    4. Apply the DB migration on bravos-vm1:
         psql -h 127.0.0.1 -U bravos bravos_trading -f infra/migrate_gmail.sql

Environment variables read at runtime:
    ALERT_EMAIL        — Gmail account to poll (set in /etc/bravos/env)
    BRAVOS_DB_PASSWORD — DB password (set in /etc/bravos/env)
    ALERT_SOCKET_PATH  — Unix socket path (default /tmp/bravos-alerts.sock)
"""
import argparse
import email
import imaplib
import os
import re
import signal
import socket
import sys
import time
import logging
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from bravos.config import settings
from bravos.config.secrets_config import get_secret

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.gmail.daemon")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
POLL_INTERVAL_SECONDS = 60

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
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM signals WHERE gmail_message_id = %s LIMIT 1",
            (message_id,),
        )
        result = cur.fetchone() is not None
    # End the implicit transaction started by the SELECT so the connection
    # stays clean for subsequent writes. Without this, any exception elsewhere
    # on this shared connection leaves it in InFailedSqlTransaction state.
    conn.rollback()
    return result


def _mark_message_id_seen(conn, message_id: str):
    """Record Message-ID even when no URL was found, so we don't retry it."""
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
    """Back-fill gmail_message_id onto the signal row written by process_alert()."""
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
    urls = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
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
    seen: set[str] = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _dispatch_url_to_daemon(url: str) -> bool:
    """Send a URL to the trading daemon via Unix socket. Returns True on success."""
    sock_path = settings.ALERT_SOCKET_PATH
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(30)
            s.connect(sock_path)
            s.sendall(f"{url}\n".encode())
            response = b""
            while b"\n" not in response:
                chunk = s.recv(256)
                if not chunk:
                    break
                response += chunk
            resp = response.decode("utf-8", errors="replace").strip()
            if resp == "OK":
                return True
            else:
                logger.error("Daemon returned error for url=%s: %s", url, resp)
                return False
    except FileNotFoundError:
        logger.error(
            "Alert socket not found at %s — is bravos-trading.service running?", sock_path
        )
        return False
    except Exception:
        logger.exception("Failed to dispatch url=%s to trading daemon", url)
        return False


def _imap_search_criteria(since_date: date | None) -> str:
    parts = [
        f'FROM "bravosresearch.com"',
        f'SUBJECT "{settings.GMAIL_SUBJECT_KEYWORD}"',
    ]
    if since_date is not None:
        parts.append(f'SINCE "{since_date.strftime("%d-%b-%Y")}"')
    return " ".join(parts)


def _fetch_matching_seq_nums(imap: imaplib.IMAP4_SSL, since_date: date | None) -> list[bytes]:
    criteria = _imap_search_criteria(since_date)
    status, data = imap.search(None, criteria)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _get_message_id_header(imap: imaplib.IMAP4_SSL, seq_num: bytes) -> str | None:
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
    db_conn: psycopg2.extensions.connection,
    since_date: date | None,
) -> int:
    """Fetch matching emails, dedup, and dispatch each new URL to the trading daemon."""
    seq_nums = _fetch_matching_seq_nums(imap, since_date)
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
                "Message-ID %s matched but contained no bravosresearch.com URL — marking seen",
                message_id,
            )
            _mark_message_id_seen(db_conn, message_id)
            continue

        for url in urls:
            logger.info("Dispatching alert: Message-ID=%s url=%s", message_id, url)
            success = _dispatch_url_to_daemon(url)
            if success:
                _set_gmail_message_id(db_conn, message_id, url)
                dispatched += 1
            else:
                # Mark seen anyway to avoid infinite retry on a broken URL
                _mark_message_id_seen(db_conn, message_id)

    return dispatched


def main():
    parser = argparse.ArgumentParser(description="Bravos Gmail alert poller")
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Process emails on or after this date (backfill).",
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
        logger.error("ALERT_EMAIL is not set — set it in /etc/bravos/env")
        sys.exit(1)

    logger.info("Fetching Gmail app password from Secret Manager...")
    try:
        app_password = get_secret("bravos-gmail-app-password")
    except Exception:
        logger.exception(
            "Failed to fetch bravos-gmail-app-password from Secret Manager. "
            "See setup instructions in this file's module docstring."
        )
        sys.exit(1)

    logger.info("Connecting to Gmail IMAP as %s...", gmail_account)
    try:
        imap = _connect_imap(gmail_account, app_password)
    except Exception:
        logger.exception("IMAP login failed for %s", gmail_account)
        sys.exit(1)

    db_conn = _db_connect()
    logger.info(
        "Gmail poller running — account=%s since=%s poll_interval=%ds socket=%s",
        gmail_account,
        since_date or "all unprocessed",
        POLL_INTERVAL_SECONDS,
        settings.ALERT_SOCKET_PATH,
    )

    try:
        while not _shutdown:
            try:
                imap.select("INBOX")
                dispatched = process_emails(imap, db_conn, since_date)
                if dispatched:
                    logger.info("Poll cycle: dispatched %d new alert(s)", dispatched)
                else:
                    logger.debug("Poll cycle: no new alerts")

                # After first backfill pass, only look at today's emails
                if since_date is not None:
                    since_date = date.today()

            except imaplib.IMAP4.abort:
                logger.warning("IMAP connection aborted — reconnecting")
                try:
                    imap = _connect_imap(gmail_account, app_password)
                except Exception:
                    logger.exception("IMAP reconnect failed — will retry next cycle")
            except Exception:
                logger.exception("Poll cycle error — will retry next cycle")
                try:
                    db_conn.rollback()
                except Exception:
                    pass

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
        logger.info("Gmail poller stopped")


if __name__ == "__main__":
    main()
