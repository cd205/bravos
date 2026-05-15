"""
tests/test_positions.py — Phase 5 Fill Capture + Position Lot unit and integration tests.

All tests are Wave 0 stubs (skipped). Each test body is the full intended
implementation. Tests are unskipped as their implementing plan lands:
  - 05-02: test_open_lot_writes_row,
           test_fifo_closes_oldest_lot_first,
           test_fifo_partial_close_one_lot,
           test_fifo_close_spanning_multiple_lots,
           test_close_lot_sets_fields
  - 05-03: test_exec_details_writes_execution_row,
           test_exec_details_idempotent,
           test_order_status_filled,
           test_order_status_partial,
           test_periodic_reconciliation_mismatch

Per CONTEXT.md decisions D-01 through D-10.
"""
import os
from unittest.mock import MagicMock, patch
import pytest


# ── Plan 05-02: positions.py — open_lot, partial_close_lot (FIFO), close_lot ──

def test_open_lot_writes_row(db_connection):
    """POS-01: open_lot() inserts a new open position_lots row with correct quantity and entry_price.

    References: POS-01, D-03.
    """
    from bravos.execution.positions import open_lot

    ticker = f"OPN_{os.urandom(3).hex()}"
    try:
        open_lot(ticker, 10, 150.00, db_connection)

        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT quantity, entry_price FROM position_lots "
                "WHERE ticker=%s AND lot_closed_at IS NULL "
                "ORDER BY lot_opened_at DESC LIMIT 1",
                (ticker,),
            )
            row = cur.fetchone()

        assert row is not None, "Expected a position_lots row to be inserted"
        assert row[0] == 10, f"Expected quantity=10, got {row[0]}"
        assert float(row[1]) == 150.00, f"Expected entry_price=150.00, got {row[1]}"
    finally:
        db_connection.rollback()


def test_fifo_closes_oldest_lot_first(db_connection):
    """POS-03: partial_close_lot() uses FIFO — oldest open lot closed before newer lots.

    References: POS-03, D-05, D-06.
    """
    from bravos.execution.positions import partial_close_lot

    ticker = f"FIF_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            # Insert oldest lot: 10 shares, $100 entry
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW() - INTERVAL '1 hour', %s, %s) RETURNING id",
                (ticker, 10, 100.00),
            )
            old_lot_id = cur.fetchone()[0]
            # Insert newer lot: 10 shares, $110 entry
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW(), %s, %s) RETURNING id",
                (ticker, 10, 110.00),
            )
            new_lot_id = cur.fetchone()[0]

        partial_close_lot(ticker, 10, 120.00, db_connection)

        with db_connection.cursor() as cur:
            # Older lot should be closed
            cur.execute(
                "SELECT lot_closed_at, exit_price, pnl FROM position_lots WHERE id=%s",
                (old_lot_id,),
            )
            old_row = cur.fetchone()
            assert old_row[0] is not None, "Older lot should have lot_closed_at set"
            assert float(old_row[1]) == 120.00, f"Expected exit_price=120.00, got {old_row[1]}"
            expected_pnl = (120.00 - 100.00) * 10
            assert float(old_row[2]) == expected_pnl, f"Expected pnl={expected_pnl}, got {old_row[2]}"

            # Newer lot should be untouched
            cur.execute(
                "SELECT lot_closed_at, quantity FROM position_lots WHERE id=%s",
                (new_lot_id,),
            )
            new_row = cur.fetchone()
            assert new_row[0] is None, "Newer lot should not be closed"
            assert new_row[1] == 10, f"Newer lot quantity should still be 10, got {new_row[1]}"
    finally:
        db_connection.rollback()


def test_fifo_partial_close_one_lot(db_connection):
    """POS-03/AUDIT-04: partial_close_lot() splits a lot — surviving open row + new closed row (append-only).

    References: POS-03, AUDIT-04, D-05, D-06.
    """
    from bravos.execution.positions import partial_close_lot

    ticker = f"PAR_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            # Insert one lot: 20 shares, $50 entry
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW(), %s, %s)",
                (ticker, 20, 50.00),
            )

        partial_close_lot(ticker, 8, 60.00, db_connection)

        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT quantity, lot_closed_at, exit_price, pnl "
                "FROM position_lots WHERE ticker=%s ORDER BY id ASC",
                (ticker,),
            )
            rows = cur.fetchall()

        assert len(rows) == 2, f"Expected 2 rows (surviving open + closed split), got {len(rows)}"

        # Identify open and closed rows
        open_rows = [r for r in rows if r[1] is None]
        closed_rows = [r for r in rows if r[1] is not None]
        assert len(open_rows) == 1, "Expected exactly 1 surviving open row"
        assert len(closed_rows) == 1, "Expected exactly 1 closed row"

        assert open_rows[0][0] == 12, f"Surviving open lot quantity should be 12, got {open_rows[0][0]}"
        assert closed_rows[0][0] == 8, f"Closed lot quantity should be 8, got {closed_rows[0][0]}"
        assert float(closed_rows[0][2]) == 60.00, f"Closed lot exit_price should be 60.00, got {closed_rows[0][2]}"
        expected_pnl = (60.00 - 50.00) * 8
        assert float(closed_rows[0][3]) == expected_pnl, f"Closed lot pnl should be {expected_pnl}, got {closed_rows[0][3]}"
    finally:
        db_connection.rollback()


def test_fifo_close_spanning_multiple_lots(db_connection):
    """POS-03: partial_close_lot() spanning multiple lots closes oldest-first across lot boundaries.

    References: POS-03, D-05, D-06.
    """
    from bravos.execution.positions import partial_close_lot

    ticker = f"SPN_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            # lot_a: 10 shares, $90 entry, oldest
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW() - INTERVAL '2 hours', %s, %s) RETURNING id",
                (ticker, 10, 90.00),
            )
            lot_a_id = cur.fetchone()[0]
            # lot_b: 20 shares, $95 entry, middle
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW() - INTERVAL '1 hour', %s, %s) RETURNING id",
                (ticker, 20, 95.00),
            )
            lot_b_id = cur.fetchone()[0]
            # lot_c: 20 shares, $100 entry, newest
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW(), %s, %s) RETURNING id",
                (ticker, 20, 100.00),
            )
            lot_c_id = cur.fetchone()[0]

        # Close 20 shares total: all of lot_a (10), then 10 from lot_b
        partial_close_lot(ticker, 20, 110.00, db_connection)

        with db_connection.cursor() as cur:
            # lot_a: fully closed
            cur.execute(
                "SELECT lot_closed_at, quantity, pnl FROM position_lots WHERE id=%s",
                (lot_a_id,),
            )
            lot_a = cur.fetchone()
            assert lot_a[0] is not None, "lot_a should be closed"
            assert lot_a[1] == 10, f"lot_a quantity should be 10, got {lot_a[1]}"
            expected_a_pnl = (110.00 - 90.00) * 10
            assert float(lot_a[2]) == expected_a_pnl, f"lot_a pnl should be {expected_a_pnl}, got {lot_a[2]}"

            # lot_b: partially closed — surviving open row should have quantity=10
            cur.execute(
                "SELECT quantity, lot_closed_at FROM position_lots WHERE id=%s",
                (lot_b_id,),
            )
            lot_b_open = cur.fetchone()
            assert lot_b_open[1] is None, "lot_b original row should remain open (AUDIT-04 append-only)"
            assert lot_b_open[0] == 10, f"lot_b surviving open quantity should be 10, got {lot_b_open[0]}"

            # lot_b split: a new closed row with quantity=10 should exist
            cur.execute(
                "SELECT quantity, lot_closed_at, exit_price, pnl "
                "FROM position_lots WHERE ticker=%s AND lot_closed_at IS NOT NULL AND id != %s",
                (ticker, lot_a_id),
            )
            lot_b_closed = cur.fetchone()
            assert lot_b_closed is not None, "Expected a closed split row from lot_b"
            assert lot_b_closed[0] == 10, f"lot_b closed split quantity should be 10, got {lot_b_closed[0]}"
            expected_b_pnl = (110.00 - 95.00) * 10
            assert float(lot_b_closed[3]) == expected_b_pnl, f"lot_b closed pnl should be {expected_b_pnl}, got {lot_b_closed[3]}"

            # lot_c: untouched
            cur.execute(
                "SELECT lot_closed_at, quantity FROM position_lots WHERE id=%s",
                (lot_c_id,),
            )
            lot_c = cur.fetchone()
            assert lot_c[0] is None, "lot_c should be untouched"
            assert lot_c[1] == 20, f"lot_c quantity should still be 20, got {lot_c[1]}"
    finally:
        db_connection.rollback()


def test_close_lot_sets_fields(db_connection):
    """POS-02: A full close via partial_close_lot() sets lot_closed_at, exit_price, and pnl correctly.

    References: POS-02, D-06, D-07.
    Realized P&L = (exit_price - entry_price) * quantity_closed per D-07.
    """
    from bravos.execution.positions import partial_close_lot

    ticker = f"CLO_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            # Insert one lot: 5 shares, $200 entry
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW(), %s, %s) RETURNING id",
                (ticker, 5, 200.00),
            )
            lot_id = cur.fetchone()[0]

        # Full close: shares_to_close == total open qty (D-06)
        partial_close_lot(ticker, 5, 210.00, db_connection)

        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT lot_closed_at, exit_price, pnl FROM position_lots WHERE ticker=%s",
                (ticker,),
            )
            rows = cur.fetchall()

        # After full close: either original row updated, or a closed split row appended
        closed_rows = [r for r in rows if r[0] is not None]
        assert len(closed_rows) >= 1, "Expected at least one closed row"
        closed = closed_rows[0]
        assert closed[0] is not None, "lot_closed_at should be set"
        assert float(closed[1]) == 210.00, f"exit_price should be 210.00, got {closed[1]}"
        expected_pnl = (210.00 - 200.00) * 5
        assert float(closed[2]) == expected_pnl, f"pnl should be {expected_pnl}, got {closed[2]}"
    finally:
        db_connection.rollback()


# ── Plan 05-03: connection.py callbacks — execDetails, orderStatus, periodic reconciliation ──

def test_exec_details_writes_execution_row():
    """EXEC-05: _handle_exec_details() writes one executions row and calls open_lot() for BUY fills.

    References: EXEC-05, D-01, D-04.
    execDetails is the canonical callback for per-execution row writes (D-01).
    """
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Order lookup: returns db_order_id=42, action=BUY
    mock_cur.fetchone.return_value = (42, "BUY")

    # Build mock execution object
    execution = MagicMock()
    execution.execId = "EXEC_ABC"
    execution.orderId = 1000
    execution.side = "BOT"
    execution.shares = 5
    execution.price = 150.00

    # Build mock contract object
    contract = MagicMock()
    contract.symbol = "AAPL"

    with patch("bravos.execution.positions.open_lot") as mock_open_lot:
        connection._handle_exec_details(mock_conn, execution, contract)

    # Assert: cursor.execute called at least twice (SELECT orders + INSERT executions)
    assert mock_cur.execute.call_count >= 2, (
        f"Expected at least 2 execute calls, got {mock_cur.execute.call_count}"
    )

    # Assert: one INSERT INTO executions call with ON CONFLICT idempotency clause
    all_sql_calls = [str(call[0][0]) for call in mock_cur.execute.call_args_list]
    insert_calls = [sql for sql in all_sql_calls if "INSERT INTO executions" in sql]
    assert len(insert_calls) >= 1, "Expected an INSERT INTO executions call"
    assert any("ON CONFLICT (exec_id) DO NOTHING" in sql for sql in insert_calls), (
        "INSERT INTO executions must include ON CONFLICT (exec_id) DO NOTHING for idempotency"
    )

    # Assert: open_lot called once with correct args
    mock_open_lot.assert_called_once_with("AAPL", 5, 150.00, mock_conn)


def test_exec_details_idempotent():
    """EXEC-05: _handle_exec_details() INSERT INTO executions uses ON CONFLICT (exec_id) DO NOTHING.

    References: EXEC-05, D-01.
    Idempotency guard prevents duplicate fill rows if callback fires more than once.
    """
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = (42, "BUY")

    execution = MagicMock()
    execution.execId = "EXEC_IDEM"
    execution.orderId = 1001
    execution.side = "BOT"
    execution.shares = 3
    execution.price = 200.00

    contract = MagicMock()
    contract.symbol = "MSFT"

    with patch("bravos.execution.positions.open_lot"):
        connection._handle_exec_details(mock_conn, execution, contract)

    all_sql_calls = [str(call[0][0]) for call in mock_cur.execute.call_args_list]
    insert_calls = [sql for sql in all_sql_calls if "INSERT INTO executions" in sql]
    assert len(insert_calls) >= 1, "Expected at least one INSERT INTO executions call"
    assert any(
        "ON CONFLICT (exec_id) DO NOTHING" in sql.upper() or
        "on conflict (exec_id) do nothing" in sql.lower()
        for sql in insert_calls
    ), "INSERT INTO executions must contain ON CONFLICT (exec_id) DO NOTHING (case-insensitive)"


def test_order_status_filled():
    """EXEC-06: _update_order_filled() sets status='FILLED', fill_price, filled_at=NOW() on orders row.

    References: EXEC-06, D-02.
    Order is FILLED only when filled==totalQuantity (D-02).
    """
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    connection._update_order_filled(mock_conn, ibkr_order_id=1000, avg_fill_price=150.25)

    assert mock_cur.execute.call_count == 1, (
        f"Expected exactly 1 execute call, got {mock_cur.execute.call_count}"
    )

    call_args = mock_cur.execute.call_args
    sql = str(call_args[0][0])
    params = call_args[0][1]

    assert "UPDATE orders SET status='FILLED'" in sql or "UPDATE orders SET" in sql, (
        f"SQL should update orders to FILLED status, got: {sql}"
    )
    assert "fill_price=%s" in sql or "fill_price = %s" in sql, (
        f"SQL should set fill_price, got: {sql}"
    )
    assert "filled_at=NOW()" in sql or "filled_at = NOW()" in sql, (
        f"SQL should set filled_at=NOW(), got: {sql}"
    )
    assert "WHERE ibkr_order_id=%s" in sql or "WHERE ibkr_order_id = %s" in sql, (
        f"SQL should filter by ibkr_order_id, got: {sql}"
    )
    assert params == (150.25, 1000), f"Expected params (150.25, 1000), got {params}"


def test_order_status_partial():
    """EXEC-06: _update_order_partial() sets status='PARTIAL' and fill_price for partial fills.

    References: EXEC-06, D-02.
    Intermediate fills (filled < totalQuantity) set status=PARTIAL (D-02).
    """
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    connection._update_order_partial(mock_conn, 1001, 149.75)

    assert mock_cur.execute.call_count == 1, (
        f"Expected exactly 1 execute call, got {mock_cur.execute.call_count}"
    )

    call_args = mock_cur.execute.call_args
    sql = str(call_args[0][0])
    params = call_args[0][1]

    assert "UPDATE orders SET status='PARTIAL'" in sql or "status='PARTIAL'" in sql, (
        f"SQL should update status to PARTIAL, got: {sql}"
    )
    assert "fill_price=%s" in sql or "fill_price = %s" in sql, (
        f"SQL should set fill_price, got: {sql}"
    )
    assert "WHERE ibkr_order_id=%s" in sql or "WHERE ibkr_order_id = %s" in sql, (
        f"SQL should filter by ibkr_order_id, got: {sql}"
    )
    assert params == (149.75, 1001), f"Expected params (149.75, 1001), got {params}"


def test_periodic_reconciliation_mismatch(db_connection):
    """IBKR-04: _reconcile_against_db() logs WARNING on position mismatch and never auto-corrects.

    References: IBKR-04, D-08, D-09, D-10.
    Mismatches logged as WARNING, never auto-corrected (D-09).
    Reuses _reconcile_against_db and _write_position_snapshot helpers (D-10).
    """
    from bravos.broker.connection import _reconcile_against_db

    ticker = f"REC_{os.urandom(3).hex()}"
    try:
        # Insert one open lot: 100 shares at $50
        with db_connection.cursor() as cur:
            cur.execute(
                "INSERT INTO position_lots (ticker, lot_opened_at, quantity, entry_price) "
                "VALUES (%s, NOW(), %s, %s)",
                (ticker, 100, 50.00),
            )

        # IBKR reports only 50 shares — mismatch (D-09: 50 != 100)
        ibkr_positions = [{"ticker": ticker, "position": 50, "avg_cost": 50.0}]

        with patch("bravos.broker.connection.logger") as mock_logger:
            _reconcile_against_db(db_connection, ibkr_positions, [])

            # Assert: at least one warning call mentioning RECONCILE MISMATCH and the ticker
            warning_calls = mock_logger.warning.call_args_list
            assert len(warning_calls) >= 1, "Expected at least one logger.warning call for mismatch"
            warning_messages = [str(call) for call in warning_calls]
            assert any(
                "RECONCILE MISMATCH" in msg and ticker in msg
                for msg in warning_messages
            ), (
                f"Expected warning containing 'RECONCILE MISMATCH' and '{ticker}', "
                f"got: {warning_messages}"
            )

        # Confirm no auto-correction: position_lots row unchanged
        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT quantity FROM position_lots WHERE ticker=%s AND lot_closed_at IS NULL",
                (ticker,),
            )
            row = cur.fetchone()
        assert row is not None, "position_lots row should still exist (no auto-correction)"
        assert row[0] == 100, f"quantity should be unchanged at 100, got {row[0]}"
    finally:
        db_connection.rollback()
