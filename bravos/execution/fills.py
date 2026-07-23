"""
bravos/execution/fills.py — execution-domain fill-capture module (item 5).

Module-level fill capture + order status helpers, moved verbatim from
bravos/broker/connection.py (Phase 5: Module-level fill capture + order
status helpers). Invoked by IBApp.execDetails / IBApp.orderStatus as
thin delegates.
"""

import logging

logger = logging.getLogger(__name__)


def _handle_exec_details(db_conn, execution, contract) -> None:
    """
    Module-level helper invoked by IBApp.execDetails (D-01 / D-04).

    Sequence:
      1. Look up orders.id + orders.action by ibkr_order_id (execution.orderId)
      2. INSERT a row into executions (idempotent via exec_id UNIQUE)
      3. Dispatch to positions.open_lot (BUY) or positions.partial_close_lot (SELL)

    The positions module is imported inside this function (deferred import)
    to avoid a circular dependency at module load time (mirror of
    executor.py's deferred import of ibapp).
    """
    logger.info(
        "execDetails: execId=%s orderId=%s side=%s shares=%s price=%s",
        execution.execId, execution.orderId, execution.side,
        execution.shares, execution.price,
    )

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, action FROM orders WHERE ibkr_order_id = %s",
            (execution.orderId,),
        )
        row = cur.fetchone()
    if row is None:
        logger.error(
            "execDetails: no order found for ibkr_order_id=%s — "
            "cannot write execution (exec_id=%s)",
            execution.orderId, execution.execId,
        )
        return
    db_order_id, order_action = row[0], row[1]

    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO executions (order_id, exec_id, shares, price, exec_time) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (exec_id) DO NOTHING "
            "RETURNING id",
            (db_order_id, execution.execId, int(execution.shares),
             float(execution.price)),
        )
        inserted = cur.fetchone()
    db_conn.commit()

    # Replay-safety gate (IMPROVEMENTS item 16): the INSERT above no-ops on
    # ON CONFLICT (exec_id already recorded), but without this guard the lot
    # dispatch below would still run unconditionally on every replay,
    # double-counting the position on each reconnect. Only dispatch when the
    # RETURNING id fetch proves a row was actually inserted.
    if inserted is None:
        logger.info(
            "execDetails: replay no-op — exec_id=%s already recorded, "
            "skipping lot dispatch",
            execution.execId,
        )
        return

    # Deferred import to avoid circular dependency
    # (bravos.execution.positions has no IBKR dependency, so the import
    # is safe at call time but unnecessary at module load).
    from bravos.execution import positions
    ticker = contract.symbol
    if order_action == "BUY":
        positions.open_lot(
            ticker, int(execution.shares), float(execution.price), db_conn,
        )
    else:  # SELL
        positions.partial_close_lot(
            ticker, int(execution.shares), float(execution.price), db_conn,
        )


def _update_order_filled(db_conn, ibkr_order_id: int, avg_fill_price: float) -> None:
    """
    Set orders.status='FILLED' + fill_price + filled_at (EXEC-06, D-02).

    Called from orderStatus when status == 'Filled' (i.e. filled == totalQuantity).
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status='FILLED', fill_price=%s, filled_at=NOW() "
            "WHERE ibkr_order_id=%s",
            (avg_fill_price, ibkr_order_id),
        )
    db_conn.commit()
    logger.info(
        "orders.status -> FILLED for ibkr_order_id=%s avg_fill_price=%s",
        ibkr_order_id, avg_fill_price,
    )


def _update_order_partial(db_conn, ibkr_order_id: int, avg_fill_price: float) -> None:
    """
    Set orders.status='PARTIAL' + fill_price (EXEC-06, D-02).

    Called from orderStatus when status == 'PartiallyFilled'. Does NOT set
    filled_at — the order is not complete yet.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status='PARTIAL', fill_price=%s "
            "WHERE ibkr_order_id=%s",
            (avg_fill_price, ibkr_order_id),
        )
    db_conn.commit()
    logger.info(
        "orders.status -> PARTIAL for ibkr_order_id=%s avg_fill_price=%s",
        ibkr_order_id, avg_fill_price,
    )
