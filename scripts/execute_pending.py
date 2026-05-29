"""
One-shot script to re-dispatch stored-but-unexecuted high-confidence signals
through the trading daemon's alert socket, which has a live ibapp connection.

Usage:
    python scripts/execute_pending.py [--since YYYY-MM-DD] [--dry-run]

Requires bravos-trading.service to be running with alert socket active.
"""
import argparse
import logging
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2
from bravos.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("execute_pending")

VALID_ACTIONS = ("open", "add", "partial_close", "close")


def get_pending_signals(conn, since: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.ticker, s.action_type, s.confidence, s.post_title, s.post_url
              FROM signals s
             WHERE s.confidence = 'high'
               AND s.action_type = ANY(%s)
               AND s.created_at >= %s
               AND s.post_title != 'test'
               AND s.ticker NOT IN ('TEST', 'BADTKR')
               AND NOT EXISTS (
                   SELECT 1 FROM orders o WHERE o.signal_id = s.id
               )
             ORDER BY s.created_at ASC
            """,
            (list(VALID_ACTIONS), since),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def dispatch_url(url: str) -> bool:
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
            return response.decode("utf-8", errors="replace").strip() == "OK"
    except FileNotFoundError:
        logger.error("Alert socket not found at %s — is bravos-trading.service running?", sock_path)
        return False
    except Exception:
        logger.exception("Failed to dispatch url=%s", url)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-05-01", help="Process signals on or after this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print signals without executing")
    args = parser.parse_args()

    password = os.environ.get("BRAVOS_DB_PASSWORD", "")
    conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        dbname=settings.DB_NAME, user=settings.DB_USER,
        password=password,
    )

    signals = get_pending_signals(conn, args.since)
    conn.close()
    logger.info("Found %d pending high-confidence signals since %s", len(signals), args.since)

    if args.dry_run:
        for s in signals:
            print(f"  id={s['id']} ticker={s['ticker']} action={s['action_type']} title={s['post_title'][:60]}")
        return

    ok = err = 0
    for s in signals:
        logger.info("Dispatching signal_id=%d ticker=%s action=%s", s["id"], s["ticker"], s["action_type"])
        if dispatch_url(s["post_url"]):
            logger.info("  -> OK")
            ok += 1
        else:
            logger.error("  -> FAILED")
            err += 1

    logger.info("Done — dispatched=%d errors=%d", ok, err)


if __name__ == "__main__":
    main()
