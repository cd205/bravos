"""
tests/test_monitor_query.py — queries/monitor.sql stop_loss_check CTE tests.

Issue #1: dashboard needs a per-signal stop_loss_ordered column so the
verification table can flag alerts that specified a stop-loss but never got a
matching STP order placed. Covers the three states the CTE must distinguish:
ordered (STP order present), not ordered (stop_loss set, no STP order), and
not applicable (signal has no stop_loss).

Runs against a real Postgres instance via the db_connection fixture (rollback
after each test, no commits) — see conftest.py for the production-DB guard.
"""
import os

MONITOR_SQL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "queries", "monitor.sql"
)


def _load_monitor_sql():
    with open(MONITOR_SQL_PATH) as f:
        return f.read()


def _insert_signal(cur, ticker, *, stop_loss=None):
    cur.execute(
        "INSERT INTO signals (post_url, post_title, ticker, action_type, stop_loss) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (f"https://example.com/{ticker}", f"{ticker} alert", ticker, "open", stop_loss),
    )
    return cur.fetchone()[0]


def _insert_order(cur, signal_id, ticker, *, order_type="MKT", parent_order_id=None):
    cur.execute(
        "INSERT INTO orders (signal_id, ticker, action, quantity, order_type, "
        "status, parent_order_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (signal_id, ticker, "BUY", 10, order_type, "SUBMITTED", parent_order_id),
    )
    return cur.fetchone()[0]


def _stop_loss_ordered_for(cur, signal_id):
    sql = _load_monitor_sql().rstrip().rstrip(";")
    cur.execute(f"SELECT signal_id, stop_loss_ordered FROM ({sql}) monitor "
                "WHERE signal_id = %s", (signal_id,))
    row = cur.fetchone()
    assert row is not None, f"Expected a monitor.sql row for signal_id={signal_id}"
    return row[1]


def test_stop_loss_ordered_true_when_stp_order_present(db_connection):
    ticker = f"SLY_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            signal_id = _insert_signal(cur, ticker, stop_loss=95.00)
            parent_id = _insert_order(cur, signal_id, ticker, order_type="MKT")
            _insert_order(cur, signal_id, ticker, order_type="STP", parent_order_id=parent_id)

            result = _stop_loss_ordered_for(cur, signal_id)

        assert result is True, f"Expected stop_loss_ordered=True, got {result}"
    finally:
        db_connection.rollback()


def test_stop_loss_ordered_false_when_stp_order_missing(db_connection):
    ticker = f"SLN_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            signal_id = _insert_signal(cur, ticker, stop_loss=95.00)
            _insert_order(cur, signal_id, ticker, order_type="MKT")

            result = _stop_loss_ordered_for(cur, signal_id)

        assert result is False, f"Expected stop_loss_ordered=False, got {result}"
    finally:
        db_connection.rollback()


def test_stop_loss_ordered_null_when_signal_has_no_stop_loss(db_connection):
    ticker = f"SLU_{os.urandom(3).hex()}"
    try:
        with db_connection.cursor() as cur:
            signal_id = _insert_signal(cur, ticker, stop_loss=None)
            _insert_order(cur, signal_id, ticker, order_type="MKT")

            result = _stop_loss_ordered_for(cur, signal_id)

        assert result is None, f"Expected stop_loss_ordered=NULL (not-applicable), got {result}"
    finally:
        db_connection.rollback()
