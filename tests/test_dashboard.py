"""
tests/test_dashboard.py — bravos/dashboard FastAPI route tests (Issue #1).

Follows the mocking pattern from tests/test_db.py: monkeypatch
bravos.db.get_connection to return a MagicMock cursor with canned
description/rows, so no real database is touched. Uses FastAPI's
TestClient, introduced to the repo for the first time by this feature.
"""
import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from bravos.dashboard import app as dashboard_app


def _make_conn(columns, rows):
    """Build a MagicMock connection whose cursor() context manager yields
    cur.description matching `columns` and cur.fetchall()/fetchone() from
    `rows` (list of tuples, in monitor.sql column order)."""
    cur = MagicMock()
    cur.description = [(c,) for c in columns]
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


MONITOR_COLUMNS = [
    "signal_id", "parsed_at", "ticker", "action_type", "confidence",
    "gate_passed", "order_status", "fill_price", "open_quantity",
    "realized_pnl", "stop_loss_ordered",
]


@pytest.fixture
def client(monkeypatch):
    return TestClient(dashboard_app.app)


def test_alert_table_renders_rows(monkeypatch, client):
    rows = [
        (1, datetime.datetime(2026, 8, 1, 9, 30), "AAPL", "open", "high",
         True, "SUBMITTED", None, 10, None, True),
    ]
    conn, cur = _make_conn(MONITOR_COLUMNS, rows)
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/")

    assert response.status_code == 200
    assert "AAPL" in response.text
    assert "SUBMITTED" in response.text
    conn.close.assert_called_once()


def test_alert_table_empty_shows_no_signals_message(monkeypatch, client):
    conn, cur = _make_conn(MONITOR_COLUMNS, [])
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/")

    assert response.status_code == 200
    assert "No signals in this date range" in response.text


def test_alert_table_default_window_is_last_14_days(monkeypatch, client):
    conn, cur = _make_conn(MONITOR_COLUMNS, [])
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    client.get("/")

    executed_sql, params = cur.execute.call_args[0]
    assert len(params) == 2, "Expected both start and end bound params by default"


def test_alert_table_filters_by_start_and_end_query_params(monkeypatch, client):
    conn, cur = _make_conn(MONITOR_COLUMNS, [])
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/?start=2026-08-01&end=2026-08-01")

    assert response.status_code == 200
    executed_sql, params = cur.execute.call_args[0]
    assert params[0] == datetime.datetime(2026, 8, 1, 0, 0, 0)
    assert params[1] == datetime.datetime(2026, 8, 1, 23, 59, 59, 999999)


def test_alert_table_rejects_invalid_date_param(monkeypatch, client):
    conn, cur = _make_conn(MONITOR_COLUMNS, [])
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/?start=not-a-date")

    assert response.status_code == 400


def test_stop_loss_column_shows_missing_when_false(monkeypatch, client):
    rows = [
        (2, datetime.datetime(2026, 8, 1, 9, 30), "TSLA", "open", "high",
         True, "SUBMITTED", None, 10, None, False),
    ]
    conn, cur = _make_conn(MONITOR_COLUMNS, rows)
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/")

    assert "MISSING" in response.text


def test_stop_loss_column_shows_na_when_none(monkeypatch, client):
    rows = [
        (3, datetime.datetime(2026, 8, 1, 9, 30), "MSFT", "open", "high",
         True, "SUBMITTED", None, 10, None, None),
    ]
    conn, cur = _make_conn(MONITOR_COLUMNS, rows)
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/")

    assert "n/a" in response.text


# ── Detail view ──────────────────────────────────────────────────────────

SIGNAL_COLUMNS = [
    "id", "post_url", "post_title", "raw_html", "ticker", "action_type",
    "weight_from", "weight_to", "reference_price", "confidence",
    "parsed_at", "created_at", "gmail_message_id", "stop_loss", "take_profits",
]
GATE_COLUMNS = [
    "id", "signal_id", "checked_at", "gate_passed", "reason",
    "open_positions", "max_positions", "order_allocation_pct",
    "max_allocation_pct", "net_liquidation", "daily_pnl", "daily_pnl_threshold",
]
ORDER_COLUMNS = [
    "id", "signal_id", "ibkr_order_id", "ticker", "action", "quantity",
    "order_type", "status", "submitted_at", "filled_at", "fill_price",
    "created_at", "parent_order_id", "oca_group",
]


class _SequentialCursor:
    """Stand-in for a psycopg2 cursor reused across several cur.execute()
    calls in one `with conn.cursor() as cur:` block. Each execute() call
    advances .description to the next query's columns; fetchone/fetchall
    return the canned result for whichever query just ran."""

    def __init__(self, descriptions, fetchone_results, fetchall_results):
        self._descriptions = iter(descriptions)
        self._fetchone_results = iter(fetchone_results)
        self._fetchall_results = iter(fetchall_results)
        self.description = None
        self._mode = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.description = next(self._descriptions)
        self._mode = "signals" if "FROM signals" in sql else (
            "gate" if "risk_gate_log" in sql else "orders"
        )

    def fetchone(self):
        return next(self._fetchone_results)

    def fetchall(self):
        return next(self._fetchall_results)


def _make_detail_conn(signal_row, gate_rows, order_rows):
    """fetch_signal_detail reuses a single cursor across three queries:
    signals, risk_gate_log, orders — in that order."""
    cur = _SequentialCursor(
        descriptions=[
            [(c,) for c in SIGNAL_COLUMNS],
            [(c,) for c in GATE_COLUMNS],
            [(c,) for c in ORDER_COLUMNS],
        ],
        fetchone_results=[signal_row],
        fetchall_results=[gate_rows, order_rows],
    )

    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_alert_detail_zero_orders(monkeypatch, client):
    signal_row = (
        5, "https://example.com/5", "AAPL alert", None, "AAPL", "open",
        0, 5, 190.00, "high", datetime.datetime(2026, 8, 1, 9, 30),
        datetime.datetime(2026, 8, 1, 9, 30), None, None, [],
    )
    conn = _make_detail_conn(signal_row, gate_rows=[], order_rows=[])
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/alerts/5")

    assert response.status_code == 200
    assert "No orders were placed for this signal" in response.text
    assert "No risk-gate checks recorded" in response.text


def test_alert_detail_full_bracket(monkeypatch, client):
    signal_row = (
        6, "https://example.com/6", "TSLA alert", None, "TSLA", "open",
        0, 5, 250.00, "high", datetime.datetime(2026, 8, 1, 9, 30),
        datetime.datetime(2026, 8, 1, 9, 30), None, 230.00, [270.00],
    )
    gate_rows = [
        (1, 6, datetime.datetime(2026, 8, 1, 9, 30), True, "ok", 2, 10,
         0.05, 0.1, 100000.00, 0.0, -5000.00),
    ]
    order_rows = [
        (10, 6, 111, "TSLA", "BUY", 10, "MKT", "SUBMITTED",
         datetime.datetime(2026, 8, 1, 9, 30), None, None,
         datetime.datetime(2026, 8, 1, 9, 30), None, None),
        (11, 6, 112, "TSLA", "SELL", 10, "STP", "PENDING_SUBMISSION",
         datetime.datetime(2026, 8, 1, 9, 30), None, None,
         datetime.datetime(2026, 8, 1, 9, 30), 10, "OCA_6"),
        (12, 6, 113, "TSLA", "SELL", 10, "LMT", "PENDING_SUBMISSION",
         datetime.datetime(2026, 8, 1, 9, 30), None, None,
         datetime.datetime(2026, 8, 1, 9, 30), 10, "OCA_6"),
    ]
    conn = _make_detail_conn(signal_row, gate_rows, order_rows)
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/alerts/6")

    assert response.status_code == 200
    assert "STP" in response.text
    assert "LMT" in response.text
    assert "OCA_6" in response.text


def test_alert_detail_404_when_signal_missing(monkeypatch, client):
    conn = _make_detail_conn(signal_row=None, gate_rows=[], order_rows=[])
    # fetch_signal_detail returns None before querying gate/orders, but
    # cursor.side_effect must still supply a first cursor.
    monkeypatch.setattr(dashboard_app, "get_connection", lambda: conn)

    response = client.get("/alerts/999")

    assert response.status_code == 404
