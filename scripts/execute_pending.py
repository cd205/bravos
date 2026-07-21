"""
One-shot script to execute stored-but-unexecuted high-confidence signals.

Connects directly to IB Gateway, then calls execute_signal() for each
pending signal by ID. Does NOT go through the scraper or alert socket.

Usage:
    python scripts/execute_pending.py [--since YYYY-MM-DD] [--dry-run]
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bravos.broker.connection as broker_module
from bravos import runtime
from bravos.broker.bootstrap import connect_and_prepare
from bravos.broker.connection import IBApp, REQ_ID_PNL
from bravos.config import settings
from bravos.db import get_connection
from bravos.execution.executor import execute_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("execute_pending")

VALID_ACTIONS = ("open", "add", "partial_close", "close")
# reqId namespace is per-client connection (this script uses client_id=2,
# distinct from the daemon's client_id=1) — 9002 does not collide across
# distinct client connections, so the shared REQ_ID_PNL (imported above) is
# safe here (Session 4a, item 7 — was a locally-defined 9003 before).


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


def _get_db_connection():
    return get_connection()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-05-01", help="Process signals on or after this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print signals without executing")
    args = parser.parse_args()

    conn = _get_db_connection()
    signals = get_pending_signals(conn, args.since)
    conn.close()

    logger.info("Found %d pending high-confidence signals since %s", len(signals), args.since)

    if not signals:
        return

    if args.dry_run:
        for s in signals:
            print(f"  id={s['id']} ticker={s['ticker']} action={s['action_type']} title={s['post_title'][:60]}")
        return

    # Connect our own ibapi instance so execute_signal has a live broker_module.ibapp
    logger.info("Connecting to IB Gateway (%s port %d)...", settings.TRADING_MODE, settings.get_ibkr_port())
    ibapp = IBApp(
        host=settings.IBKR_HOST,
        port=settings.get_ibkr_port(),
        client_id=2,  # distinct client_id from daemon (client_id=1)
    )
    # mirror write — remove with the connection.py alias
    runtime.ibapp = ibapp
    broker_module.ibapp = ibapp
    connected = ibapp.connect_and_run(timeout=30)
    if not connected:
        logger.error("Could not connect to IB Gateway — aborting")
        sys.exit(1)

    # Shared reconcile -> _db_conn -> replay -> reqPnL sequence (Session 4a,
    # item 7 — single home in bravos.broker.bootstrap). This also fetches
    # positions, open orders, and account summary (NetLiquidation) which
    # _calculate_quantity needs to size orders.
    connect_and_prepare(ibapp, pnl_req_id=REQ_ID_PNL)
    logger.info(
        "Account summary: NLV=%s",
        (ibapp._account_summary or {}).get("NetLiquidation", "unknown"),
    )

    logger.info("IB Gateway ready")

    ok = err = 0
    for s in signals:
        logger.info("Executing signal_id=%d ticker=%s action=%s", s["id"], s["ticker"], s["action_type"])
        try:
            exec_conn = _get_db_connection()
            try:
                execute_signal(s["id"], exec_conn)
                logger.info("  -> OK")
                ok += 1
            finally:
                exec_conn.close()
        except Exception:
            logger.exception("  -> FAILED for signal_id=%d", s["id"])
            err += 1

    logger.info("Done — executed=%d errors=%d", ok, err)
    ibapp.stop()


if __name__ == "__main__":
    main()
