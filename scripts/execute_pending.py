"""
One-shot script to execute all high-confidence signals that were stored but
never routed to the order path (signal_id returned None due to ON CONFLICT).

Usage:
    python scripts/execute_pending.py [--since YYYY-MM-DD] [--dry-run]

Finds signals with confidence=high, action_type in (open,add,partial_close,close),
no matching order row, and routes each through execute_signal().

Requires bravos-trading.service to be running (ibapp must be connected).
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2
from bravos.config import settings
from bravos.execution.executor import execute_signal

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
            SELECT s.id, s.ticker, s.action_type, s.confidence, s.post_title
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-05-19", help="Process signals on or after this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print signals without executing")
    args = parser.parse_args()

    password = os.environ.get("BRAVOS_DB_PASSWORD", "")
    conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        dbname=settings.DB_NAME, user=settings.DB_USER,
        password=password,
    )

    signals = get_pending_signals(conn, args.since)
    logger.info("Found %d pending high-confidence signals since %s", len(signals), args.since)

    if args.dry_run:
        for s in signals:
            print(f"  id={s['id']} ticker={s['ticker']} action={s['action_type']} title={s['post_title'][:60]}")
        conn.close()
        return

    ok = err = skipped = 0
    for s in signals:
        logger.info("Executing signal_id=%d ticker=%s action=%s", s["id"], s["ticker"], s["action_type"])
        exec_conn = psycopg2.connect(
            host=settings.DB_HOST, port=settings.DB_PORT,
            dbname=settings.DB_NAME, user=settings.DB_USER,
            password=password,
        )
        try:
            execute_signal(s["id"], exec_conn)
            ok += 1
        except Exception:
            logger.exception("execute_signal failed for signal_id=%d", s["id"])
            err += 1
        finally:
            exec_conn.close()

    conn.close()
    logger.info("Done — executed=%d errors=%d", ok, err)


if __name__ == "__main__":
    main()
