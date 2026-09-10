"""bravos/dashboard/queries.py — monitor.sql read + wrap seam (Issue #1).

Reads queries/monitor.sql text at call time and wraps it with an outer
SELECT so the file stays the single source of truth for "what happened to
this alert" — no SQL is duplicated inline in Python (per the spec's
"query reuse seam" decision).
"""
import os

MONITOR_SQL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "queries",
    "monitor.sql",
)

DEFAULT_WINDOW_DAYS = 14


def _load_monitor_sql() -> str:
    with open(MONITOR_SQL_PATH) as f:
        return f.read().rstrip().rstrip(";")


def fetch_signals(conn, *, start=None, end=None):
    """Return monitor.sql rows as dicts, most-recent-first, optionally
    restricted to signals with parsed_at BETWEEN start AND end.

    start/end are inclusive date/datetime bounds; either or both may be None
    to leave that side of the range open.
    """
    monitor_sql = _load_monitor_sql()
    where_clauses = []
    params = []
    if start is not None:
        where_clauses.append("parsed_at >= %s")
        params.append(start)
    if end is not None:
        where_clauses.append("parsed_at <= %s")
        params.append(end)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = (
        f"SELECT * FROM ({monitor_sql}) monitor {where_sql} "
        "ORDER BY parsed_at DESC"
    )

    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    return [dict(zip(columns, row)) for row in rows]


def fetch_signal(conn, signal_id: int):
    """Return the monitor.sql row for a single signal_id, or None."""
    monitor_sql = _load_monitor_sql()
    sql = f"SELECT * FROM ({monitor_sql}) monitor WHERE signal_id = %s"

    with conn.cursor() as cur:
        cur.execute(sql, (signal_id,))
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()

    return dict(zip(columns, row)) if row is not None else None


def fetch_signal_detail(conn, signal_id: int):
    """Return raw signal fields, risk-gate log entries, and full order
    history (including bracket children) for a single signal, or None if the
    signal doesn't exist.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM signals WHERE id = %s", (signal_id,))
        columns = [desc[0] for desc in cur.description]
        signal_row = cur.fetchone()
        if signal_row is None:
            return None
        signal = dict(zip(columns, signal_row))

        cur.execute(
            "SELECT * FROM risk_gate_log WHERE signal_id = %s ORDER BY checked_at DESC",
            (signal_id,),
        )
        columns = [desc[0] for desc in cur.description]
        gate_log = [dict(zip(columns, row)) for row in cur.fetchall()]

        cur.execute(
            "SELECT * FROM orders WHERE signal_id = %s ORDER BY created_at ASC",
            (signal_id,),
        )
        columns = [desc[0] for desc in cur.description]
        orders = [dict(zip(columns, row)) for row in cur.fetchall()]

    return {"signal": signal, "gate_log": gate_log, "orders": orders}
