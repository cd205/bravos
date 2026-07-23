"""
bravos/broker/reconciliation.py — reconciliation snapshot/compare module (item 5).

Module-level helper functions, moved verbatim from bravos/broker/connection.py
(Plan 03-3: Module-level helper functions). Invoked by IBApp.run_startup_reconciliation
and IBApp.run_periodic_reconciliation as thin delegates.
"""

import logging

logger = logging.getLogger(__name__)


def _write_position_snapshot(db_conn, positions: list[dict]) -> None:
    """
    Insert one row per position into broker_positions_snapshot.

    Does NOT populate market_value — no live prices at reconciliation time.
    Commits after all inserts. Safe to call with an empty positions list.
    """
    with db_conn.cursor() as cur:
        for pos in positions:
            cur.execute(
                "INSERT INTO broker_positions_snapshot (ticker, position, avg_cost, snapshot_at)"
                " VALUES (%s, %s, %s, NOW())",
                (pos["ticker"], pos["position"], pos["avg_cost"]),
            )
    db_conn.commit()
    logger.info("Wrote %d position rows to broker_positions_snapshot", len(positions))


def _reconcile_against_db(db_conn, ibkr_positions: list[dict], ibkr_orders: list[dict]) -> None:
    """
    Compare IBKR positions against open lots in position_lots.

    Logs WARNING for any quantity mismatch. NEVER writes to position_lots (D-08).
    """
    ibkr_pos_map = {p["ticker"]: p["position"] for p in ibkr_positions}

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, SUM(quantity) FROM position_lots"
            " WHERE lot_closed_at IS NULL GROUP BY ticker"
        )
        db_open = {row[0]: row[1] for row in cur.fetchall()}

    # Check DB lots against IBKR
    for ticker, db_qty in db_open.items():
        ibkr_qty = ibkr_pos_map.get(ticker, 0)
        if db_qty != ibkr_qty:
            logger.warning(
                "RECONCILE MISMATCH ticker=%s db_open_qty=%s ibkr_qty=%s"
                " — operator review required",
                ticker,
                db_qty,
                ibkr_qty,
            )

    # Check IBKR positions not in DB
    for ticker, qty in ibkr_pos_map.items():
        if ticker not in db_open and qty != 0:
            logger.warning(
                "RECONCILE IBKR HAS POSITION NOT IN DB ticker=%s ibkr_qty=%s"
                " — operator review required",
                ticker,
                qty,
            )

    # D-08: never write to position_lots
    logger.info("Reconciliation complete — DB not modified (D-08)")
