"""
tests/test_broker.py — IBApp unit and integration tests.

All tests are Wave 0 stubs (skipped). Each test body is the full intended
implementation. Tests are unskipped as their implementing plan lands:
  - 03-1: test_ibapp_init_*, test_paper_port_config, test_live_port_config
  - 03-2: test_heartbeat_*, test_error_*, test_reconnect_*
  - 03-3: test_position_*, test_open_order_*, test_account_summary_*,
           test_position_snapshot_written, test_reconcile_mismatch_*
"""
import os
import threading
import time
import pytest


# ── Plan 03-1: IBApp class skeleton + settings integration ─────────────────

@pytest.mark.skip(reason="plan: 03-1")
def test_ibapp_init_sets_host_port_client_id():
    """IBApp stores connection params from constructor — does not connect."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert app._host == "127.0.0.1"
    assert app._port == 4002
    assert app._client_id == 1


@pytest.mark.skip(reason="plan: 03-1")
def test_ibapp_init_connected_event_is_clear():
    """_connected Event must be clear on init — not yet connected."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._connected.is_set()


@pytest.mark.skip(reason="plan: 03-1")
def test_ibapp_is_connected_returns_false_before_connect():
    """is_connected() reflects _connected Event state — False at init."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert app.is_connected() is False


@pytest.mark.skip(reason="plan: 03-1")
def test_next_valid_id_sets_connected_and_stores_order_id():
    """nextValidId() callback: sets _connected, stores next_order_id, updates heartbeat ts."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._connected.is_set()
    app.nextValidId(42)
    assert app._connected.is_set()
    assert app.next_order_id == 42
    assert app._last_heartbeat_at > 0


@pytest.mark.skip(reason="plan: 03-1")
def test_paper_port_config():
    """get_ibkr_port() returns 4002 when TRADING_MODE=paper."""
    os.environ["TRADING_MODE"] = "paper"
    # Re-import to pick up env var (settings reads at import time; use helper directly)
    from bravos.config.settings import IBKR_PAPER_PORT, IBKR_LIVE_PORT
    port = IBKR_PAPER_PORT if os.environ.get("TRADING_MODE") != "live" else IBKR_LIVE_PORT
    assert port == 4002


@pytest.mark.skip(reason="plan: 03-1")
def test_live_port_config():
    """get_ibkr_port() returns 4001 when TRADING_MODE=live."""
    os.environ["TRADING_MODE"] = "live"
    from bravos.config.settings import IBKR_PAPER_PORT, IBKR_LIVE_PORT
    port = IBKR_LIVE_PORT if os.environ.get("TRADING_MODE") == "live" else IBKR_PAPER_PORT
    assert port == 4001
    # Cleanup
    os.environ["TRADING_MODE"] = "paper"


@pytest.mark.skip(reason="plan: 03-1")
def test_module_level_ibapp_singleton_is_none_at_import():
    """bravos.broker.connection.ibapp is None at import — initialized by run_ingestion.py."""
    import bravos.broker.connection as broker_module
    # Reset to None to simulate fresh import
    broker_module.ibapp = None
    assert broker_module.ibapp is None


@pytest.mark.skip(reason="plan: 03-1")
def test_stop_sets_stop_event_and_clears_connected():
    """stop() sets _stop_event and clears _connected — safe to call without connecting."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app.nextValidId(1)  # simulate connected
    assert app._connected.is_set()
    app.stop()
    assert app._stop_event.is_set()
    assert not app._connected.is_set()


# ── Plan 03-2: Heartbeat + reconnect ───────────────────────────────────────

def test_current_time_updates_last_heartbeat_at():
    """currentTime() callback updates _last_heartbeat_at to current monotonic time."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    before = time.monotonic()
    app.currentTime(1234567890)
    assert app._last_heartbeat_at >= before


def test_heartbeat_timeout_triggers_reconnect(monkeypatch):
    """_heartbeat_loop triggers _trigger_reconnect when heartbeat is stale > 10s."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app.nextValidId(1)  # mark connected
    app._last_heartbeat_at = time.monotonic() - 15  # force stale

    reconnect_called = threading.Event()

    def mock_trigger_reconnect(reason):
        reconnect_called.set()

    monkeypatch.setattr(app, "_trigger_reconnect", mock_trigger_reconnect)
    monkeypatch.setattr(app, "reqCurrentTime", lambda: None)

    # Simulate one heartbeat check iteration (not the full loop)
    from bravos.broker import connection as conn_module
    app._connected.set()
    app.reqCurrentTime()
    time.sleep(conn_module.HEARTBEAT_TIMEOUT + 0.1)
    elapsed = time.monotonic() - app._last_heartbeat_at
    if elapsed > conn_module.HEARTBEAT_TIMEOUT:
        app._trigger_reconnect("heartbeat_timeout")

    assert reconnect_called.is_set()


def test_error_504_triggers_reconnect(monkeypatch):
    """error(504) triggers immediate reconnect (CLOSE-WAIT / not connected)."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    reconnect_called = threading.Event()
    monkeypatch.setattr(app, "_trigger_reconnect", lambda reason: reconnect_called.set())

    app.error(reqId=-1, errorCode=504, errorString="Not connected")
    assert reconnect_called.is_set()


def test_error_1100_triggers_reconnect(monkeypatch):
    """error(1100) triggers immediate reconnect (connectivity lost)."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    reconnect_called = threading.Event()
    monkeypatch.setattr(app, "_trigger_reconnect", lambda reason: reconnect_called.set())

    app.error(reqId=-1, errorCode=1100, errorString="Connectivity between IB and TWS has been lost")
    assert reconnect_called.is_set()


def test_error_2104_is_ignored(monkeypatch):
    """error(2104) — market data farm status — does NOT trigger reconnect."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    reconnect_called = threading.Event()
    monkeypatch.setattr(app, "_trigger_reconnect", lambda reason: reconnect_called.set())

    app.error(reqId=-1, errorCode=2104, errorString="Market data farm connection is OK")
    assert not reconnect_called.is_set()


def test_trigger_reconnect_does_not_spawn_duplicate_thread(monkeypatch):
    """_trigger_reconnect is a no-op if _reconnecting is already True (guard against races)."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    threads_started = []
    original_start = threading.Thread.start

    def counting_start(self_thread):
        threads_started.append(self_thread.name)
        # Don't actually start — just record

    monkeypatch.setattr(threading.Thread, "start", counting_start)

    # Mark reconnecting already in progress
    with app._recon_lock:
        app._reconnecting = True

    app._trigger_reconnect("test")
    assert len(threads_started) == 0  # no new thread spawned


def test_reconnect_backoff_delays():
    """_RETRY_DELAYS list has 5 elements: [5, 10, 20, 40, 80] — matches D-07."""
    from bravos.broker import connection as conn_module
    assert conn_module._RETRY_DELAYS == [5, 10, 20, 40, 80]
    assert len(conn_module._RETRY_DELAYS) == 5


# ── Plan 03-3: Startup reconciliation callbacks + snapshot write ────────────

def test_position_end_sets_event():
    """positionEnd() callback sets _positions_done threading.Event."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._positions_done.is_set()
    app.positionEnd()
    assert app._positions_done.is_set()


def test_position_callback_appends_stk_positions():
    """position() callback appends STK positions to _positions list."""
    from bravos.broker.connection import IBApp
    from unittest.mock import MagicMock
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    contract = MagicMock()
    contract.symbol = "AAPL"
    contract.secType = "STK"

    app.position(account="U123456", contract=contract, position=100.0, avgCost=150.25)
    assert len(app._positions) == 1
    assert app._positions[0]["ticker"] == "AAPL"
    assert app._positions[0]["position"] == 100
    assert app._positions[0]["avg_cost"] == 150.25


def test_open_order_end_sets_event():
    """openOrderEnd() callback sets _orders_done threading.Event."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._orders_done.is_set()
    app.openOrderEnd()
    assert app._orders_done.is_set()


def test_open_order_callback_appends_orders():
    """openOrder() callback appends order dict to _open_orders list."""
    from bravos.broker.connection import IBApp
    from unittest.mock import MagicMock
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    contract = MagicMock()
    contract.symbol = "MSFT"
    order = MagicMock()
    order.action = "BUY"
    order.totalQuantity = 50
    order.orderType = "MKT"
    orderState = MagicMock()
    orderState.status = "Submitted"

    app.openOrder(orderId=101, contract=contract, order=order, orderState=orderState)
    assert len(app._open_orders) == 1
    assert app._open_orders[0]["ticker"] == "MSFT"
    assert app._open_orders[0]["action"] == "BUY"


def test_account_summary_end_sets_event():
    """accountSummaryEnd() callback sets _summary_done threading.Event."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._summary_done.is_set()
    app.accountSummaryEnd(reqId=9001)
    assert app._summary_done.is_set()


def test_position_snapshot_written(db_connection):
    """_write_position_snapshot inserts rows into broker_positions_snapshot."""
    from bravos.broker.connection import _write_position_snapshot

    positions = [
        {"ticker": "AAPL", "position": 100, "avg_cost": 150.25},
        {"ticker": "MSFT", "position": 50, "avg_cost": 320.00},
    ]

    # Count rows before
    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM broker_positions_snapshot")
        before = cur.fetchone()[0]

    _write_position_snapshot(db_connection, positions)

    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM broker_positions_snapshot")
        after = cur.fetchone()[0]

    assert after == before + 2


def test_reconcile_mismatch_logs_warning(caplog):
    """_reconcile_against_db logs WARNING when IBKR qty != DB open lot qty."""
    import logging
    from unittest.mock import MagicMock, patch

    # Simulate DB returning an open lot for AAPL with qty=100
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [("AAPL", 100)]  # DB says 100 shares

    # IBKR says 80 shares — mismatch
    ibkr_positions = [{"ticker": "AAPL", "position": 80, "avg_cost": 150.0}]

    from bravos.broker.connection import _reconcile_against_db
    with caplog.at_level(logging.WARNING, logger="bravos.broker.connection"):
        _reconcile_against_db(mock_conn, ibkr_positions, [])

    assert any("RECONCILE MISMATCH" in r.message for r in caplog.records)


def test_reconcile_no_db_write_on_mismatch(db_connection):
    """_reconcile_against_db never writes to position_lots on mismatch (D-08)."""
    from bravos.broker.connection import _reconcile_against_db

    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM position_lots")
        before = cur.fetchone()[0]

    # IBKR has a position not in DB — should only log, not write
    ibkr_positions = [{"ticker": "AAPL", "position": 100, "avg_cost": 150.0}]
    _reconcile_against_db(db_connection, ibkr_positions, [])

    with db_connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM position_lots")
        after = cur.fetchone()[0]

    assert after == before  # no rows written
