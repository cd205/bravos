"""
bravos/execution/positions.py — Position lot management (Phase 5).

Exposes open_lot() and partial_close_lot() for the execDetails callback.
FIFO lot closure: oldest open lot closed first (D-05/D-06).
All functions accept a psycopg2 db_conn and commit on success.
No ibapi dependency — independently testable.

Per CONTEXT.md decisions D-03, D-05, D-06, D-07.
"""
import logging

logger = logging.getLogger(__name__)


def open_lot(ticker: str, shares: int, entry_price: float, db_conn) -> None:
    """
    Insert a new open lot (POS-01). Called from execDetails for BUY fills.

    lot_opened_at defaults to NOW() per schema.sql column default.
    lot_closed_at, exit_price, pnl remain NULL until the lot is closed.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO position_lots (ticker, quantity, entry_price) "
            "VALUES (%s, %s, %s)",
            (ticker, shares, entry_price),
        )
    db_conn.commit()
    logger.info(
        "open_lot: ticker=%s shares=%s entry_price=%s",
        ticker, shares, entry_price,
    )


def partial_close_lot(
    ticker: str, shares_to_close: int, exit_price: float, db_conn,
) -> None:
    """
    FIFO lot closure (POS-02 + POS-03 + AUDIT-04 + AUDIT-06).

    Closes oldest open lots first (ORDER BY lot_opened_at ASC) until
    shares_to_close is exhausted. A full close (action_type='close') is the
    same call with shares_to_close = total open quantity (D-06).

    Per-lot pnl = (exit_price - entry_price) * quantity_closed (D-07).

    When a lot is only partially closed:
      - The surviving open row has its `quantity` reduced.
      - A NEW row is appended with lot_closed_at=NOW(), exit_price, pnl,
        and quantity=shares_closed_from_this_lot. This satisfies AUDIT-04
        (full lot history preserved) and AUDIT-06 (append-only).

    If shares_to_close exceeds total open inventory, logs a WARNING and
    returns — the caller (execDetails callback) must not crash on this.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, quantity, entry_price FROM position_lots "
            "WHERE ticker = %s AND lot_closed_at IS NULL "
            "ORDER BY lot_opened_at ASC",
            (ticker,),
        )
        lots = cur.fetchall()

    remaining = shares_to_close
    with db_conn.cursor() as cur:
        for lot_id, lot_qty, entry_price_raw in lots:
            if remaining <= 0:
                break
            entry_price_f = float(entry_price_raw)
            if lot_qty <= remaining:
                # Close this lot entirely.
                pnl = (exit_price - entry_price_f) * lot_qty
                cur.execute(
                    "UPDATE position_lots "
                    "SET lot_closed_at=NOW(), exit_price=%s, pnl=%s "
                    "WHERE id=%s",
                    (exit_price, pnl, lot_id),
                )
                remaining -= lot_qty
                logger.info(
                    "partial_close_lot: FULL closed lot_id=%s ticker=%s "
                    "qty=%s pnl=%.2f",
                    lot_id, ticker, lot_qty, pnl,
                )
            else:
                # Partial close: shrink surviving lot AND append new closed row.
                qty_closed = remaining
                pnl = (exit_price - entry_price_f) * qty_closed
                cur.execute(
                    "UPDATE position_lots SET quantity=%s WHERE id=%s",
                    (lot_qty - qty_closed, lot_id),
                )
                # AUDIT-04: append a new closed row for the closed portion.
                # Copy ticker + lot_opened_at + entry_price from the surviving lot
                # so the close record is traceable back to the original lot.
                cur.execute(
                    "INSERT INTO position_lots "
                    "(ticker, lot_opened_at, quantity, entry_price, "
                    "lot_closed_at, exit_price, pnl) "
                    "SELECT ticker, lot_opened_at, %s, entry_price, "
                    "NOW(), %s, %s FROM position_lots WHERE id=%s",
                    (qty_closed, exit_price, pnl, lot_id),
                )
                remaining = 0
                logger.info(
                    "partial_close_lot: PARTIAL closed lot_id=%s ticker=%s "
                    "qty_closed=%s pnl=%.2f remaining_in_lot=%s",
                    lot_id, ticker, qty_closed, pnl, lot_qty - qty_closed,
                )
    db_conn.commit()

    if remaining > 0:
        logger.warning(
            "partial_close_lot: shares_to_close=%s but only closed %s for "
            "ticker=%s — open lots exhausted",
            shares_to_close, shares_to_close - remaining, ticker,
        )


def close_lot(ticker: str, exit_price: float, db_conn) -> None:
    """
    Close all open lots for ticker (D-03 public API, full-close convenience wrapper).

    Queries total open quantity then delegates to partial_close_lot().
    Equivalent to partial_close_lot(ticker, total_open_qty, exit_price, db_conn).
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM position_lots "
            "WHERE ticker = %s AND lot_closed_at IS NULL",
            (ticker,),
        )
        total_qty = cur.fetchone()[0]
    if total_qty == 0:
        logger.warning("close_lot: no open lots for ticker=%s — nothing to close", ticker)
        return
    partial_close_lot(ticker, total_qty, exit_price, db_conn)
