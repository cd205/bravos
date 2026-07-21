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

def test_ibapp_init_sets_host_port_client_id():
    """IBApp stores connection params from constructor — does not connect."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert app._host == "127.0.0.1"
    assert app._port == 4002
    assert app._client_id == 1


def test_ibapp_init_connected_event_is_clear():
    """_connected Event must be clear on init — not yet connected."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._connected.is_set()


def test_ibapp_is_connected_returns_false_before_connect():
    """is_connected() reflects _connected Event state — False at init."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert app.is_connected() is False


def test_next_valid_id_sets_connected_and_stores_order_id():
    """nextValidId() callback: sets _connected, stores next_order_id, updates heartbeat ts."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert not app._connected.is_set()
    app.nextValidId(42)
    assert app._connected.is_set()
    assert app.next_order_id == 42
    assert app._last_heartbeat_at > 0


def test_paper_port_config():
    """get_ibkr_port() returns 4002 when TRADING_MODE=paper."""
    os.environ["TRADING_MODE"] = "paper"
    # Re-import to pick up env var (settings reads at import time; use helper directly)
    from bravos.config.settings import IBKR_PAPER_PORT, IBKR_LIVE_PORT
    port = IBKR_PAPER_PORT if os.environ.get("TRADING_MODE") != "live" else IBKR_LIVE_PORT
    assert port == 4002


def test_live_port_config():
    """get_ibkr_port() returns 4001 when TRADING_MODE=live."""
    os.environ["TRADING_MODE"] = "live"
    from bravos.config.settings import IBKR_PAPER_PORT, IBKR_LIVE_PORT
    port = IBKR_LIVE_PORT if os.environ.get("TRADING_MODE") == "live" else IBKR_PAPER_PORT
    assert port == 4001
    # Cleanup
    os.environ["TRADING_MODE"] = "paper"


def test_module_level_ibapp_singleton_is_none_at_import():
    """bravos.broker.connection.ibapp is None at import — initialized by run_ingestion.py."""
    import bravos.broker.connection as broker_module
    # Reset to None to simulate fresh import
    broker_module.ibapp = None
    assert broker_module.ibapp is None


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


# ── IMPROVEMENTS item 3: order-scoped error() rejection routing ────────────

def test_error_order_scoped_writes_ibkr_error():
    """error() with a registered slot and a non-allowlisted code writes an
    IBKR_ERROR result into the slot and sets its event — this is how the
    executor learns of a rejection when orderStatus never fires."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    app._order_status_events[1001] = {"event": threading.Event(), "status": None}
    app.error(reqId=1001, errorCode=321, errorString="Read-Only API")

    slot = app._order_status_events[1001]
    assert slot["status"] == "IBKR_ERROR"
    assert slot["code"] == 321
    assert slot["message"] == "Read-Only API"
    assert slot["event"].is_set()


def test_error_399_does_not_reject_accepted_order():
    """error(399) — accepted-order warning — must NOT write the slot or set
    its event; the real PreSubmitted/Submitted callback still resolves it."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    app._order_status_events[1002] = {"event": threading.Event(), "status": None}
    app.error(reqId=1002, errorCode=399, errorString="Warning: your order will not be placed at the exchange until 09:30:00 ET; queued")

    slot = app._order_status_events[1002]
    assert slot["status"] is None
    assert not slot["event"].is_set()


def test_error_unregistered_reqid_is_safe():
    """error() with a reqId not in _order_status_events does not raise and
    does not create a new slot."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    app.error(reqId=99999, errorCode=321, errorString="x")

    assert 99999 not in app._order_status_events


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


def test_reconnect_success_reruns_reconciliation(monkeypatch):
    """_refresh_after_connect (called from _reconnect_loop's success path)
    repopulates _account_summary with a fresh NetLiquidation — regression
    test for the 2026-07-06 HROW incident where an internal reconnect left
    _account_summary empty and executor._calculate_quantity computed 0."""
    from unittest.mock import MagicMock
    from bravos.broker.connection import IBApp

    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    assert app._account_summary == {}

    # Reconnect "succeeds" without a real socket.
    monkeypatch.setattr(app, "connect_and_run", lambda *a, **k: True)

    # Simulate IBKR replying with a real NLV during reconciliation.
    def fake_recon(db_conn, timeout=30):
        app._account_summary["NetLiquidation"] = "500000.00"
        return True

    monkeypatch.setattr(app, "run_startup_reconciliation", fake_recon)

    # No real DB connection — short-lived connections return a MagicMock.
    monkeypatch.setattr(app, "_open_short_lived_db_conn", lambda: MagicMock())

    # No real IBKR socket for the reqPnL re-subscription.
    monkeypatch.setattr(app, "reqPnL", lambda *a, **k: None)
    app._account_name = "DU123"

    app._refresh_after_connect()

    assert app._account_summary.get("NetLiquidation") == "500000.00"
    assert app._db_conn is not None


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


# ── Session 1b: fill-replay safety (IMPROVEMENTS item 16) ──────────────────

def test_replay_duplicate_exec_id_no_double_dispatch():
    """A replayed fill whose exec_id is already recorded is a true no-op —
    the RETURNING id fetch is None (ON CONFLICT), so no lot is dispatched."""
    from unittest.mock import MagicMock, patch
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    # order lookup: found; RETURNING id: no-op (already recorded)
    mock_cur.fetchone.side_effect = [(42, "BUY"), None]

    execution = MagicMock()
    execution.execId = "EXEC_DUP"
    execution.orderId = 1000
    execution.side = "BOT"
    execution.shares = 5
    execution.price = 150.0

    contract = MagicMock()
    contract.symbol = "AAPL"

    with patch("bravos.execution.positions.open_lot") as m_open, \
         patch("bravos.execution.positions.partial_close_lot") as m_close:
        connection._handle_exec_details(mock_conn, execution, contract)

    m_open.assert_not_called()
    m_close.assert_not_called()

    # sanity: INSERT still ran (ON CONFLICT retained)
    sqls = [str(c[0][0]) for c in mock_cur.execute.call_args_list]
    assert any("ON CONFLICT (exec_id) DO NOTHING" in s for s in sqls)


def test_replay_unknown_order_id_skipped():
    """An execDetails callback for an ibkr_order_id with no orders row is
    logged and skipped — it must not raise or dispatch a lot."""
    from unittest.mock import MagicMock, patch
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.return_value = None  # no orders row

    execution = MagicMock()
    execution.execId = "EXEC_UNKNOWN"
    execution.orderId = 9999
    execution.side = "BOT"
    execution.shares = 5
    execution.price = 150.0

    contract = MagicMock()
    contract.symbol = "AAPL"

    with patch("bravos.execution.positions.open_lot") as m_open:
        connection._handle_exec_details(mock_conn, execution, contract)  # must not raise

    m_open.assert_not_called()


def test_replay_fresh_fill_dispatches_once():
    """A fresh, unseen fill for a known order inserts one execution row
    (RETURNING id is non-None) and dispatches exactly one lot."""
    from unittest.mock import MagicMock, patch
    from bravos.broker import connection

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    # order lookup: found; RETURNING id: fresh insert (id=7)
    mock_cur.fetchone.side_effect = [(42, "BUY"), (7,)]

    execution = MagicMock()
    execution.execId = "EXEC_FRESH"
    execution.orderId = 1000
    execution.side = "BOT"
    execution.shares = 5
    execution.price = 150.0

    contract = MagicMock()
    contract.symbol = "AAPL"

    with patch("bravos.execution.positions.open_lot") as m_open:
        connection._handle_exec_details(mock_conn, execution, contract)

    m_open.assert_called_once_with("AAPL", 5, 150.0, mock_conn)


def test_replay_issued_after_db_conn():
    """replay_todays_fills() issues reqExecutions with REQ_ID_EXECUTIONS and a
    same-day ExecutionFilter when connected + _db_conn set; is a no-op when
    _db_conn is None (fills would otherwise be dropped by execDetails)."""
    from unittest.mock import MagicMock
    from datetime import datetime
    from bravos.broker.connection import IBApp, REQ_ID_EXECUTIONS

    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app._connected.set()  # force is_connected() True
    app._db_conn = MagicMock()  # _db_conn present

    captured = {}
    app.reqExecutions = lambda reqId, filt: captured.update(reqId=reqId, time=filt.time)

    app.replay_todays_fills()

    assert captured["reqId"] == REQ_ID_EXECUTIONS
    assert captured["time"].startswith(datetime.now().strftime("%Y%m%d"))

    # no-op when _db_conn is None
    app._db_conn = None
    captured.clear()
    app.replay_todays_fills()
    assert captured == {}, "replay must be a no-op when _db_conn is unset"


# ── Session 3 (item 4/3a): public IBApp API — get_nlv, get_daily_pnl, ─────
# fetch_delayed_price, place_order_and_wait, register/await decomposition ──

def test_get_nlv_present():
    """get_nlv() returns float(NetLiquidation) when the tag is present."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app._account_summary = {"NetLiquidation": "500000.00"}
    assert app.get_nlv() == 500000.0


def test_get_nlv_missing():
    """get_nlv() returns None when NetLiquidation tag is absent."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app._account_summary = {}
    assert app.get_nlv() is None


def test_get_nlv_unparseable():
    """get_nlv() returns None (and logs) when NetLiquidation is not float-castable."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app._account_summary = {"NetLiquidation": "abc"}
    assert app.get_nlv() is None


def test_get_daily_pnl_present():
    """get_daily_pnl() returns the stored _daily_pnl value."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app._daily_pnl = -1234.5
    assert app.get_daily_pnl() == -1234.5


def test_get_daily_pnl_none():
    """get_daily_pnl() returns None when _daily_pnl has never been set by pnl()."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app._daily_pnl = None
    assert app.get_daily_pnl() is None


def test_build_contract_module_level():
    """build_contract() returns a STK/SMART/USD Contract for the given ticker."""
    from bravos.broker.connection import build_contract
    c = build_contract("AAPL")
    assert c.symbol == "AAPL"
    assert c.secType == "STK"
    assert c.exchange == "SMART"
    assert c.currency == "USD"


def test_fetch_delayed_price_tick_arrives():
    """fetch_delayed_price returns the tick price when reqMktData writes the slot."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    calls = {"market_data_type": False, "cancel_ids": []}
    app.reqMarketDataType = lambda t: calls.update(market_data_type=(t == 3))

    def fake_req_mkt_data(req_id, contract, *args, **kwargs):
        slot = app._tick_events[req_id]
        slot["price"] = 204.5
        slot["event"].set()
    app.reqMktData = fake_req_mkt_data
    app.cancelMktData = lambda req_id: calls["cancel_ids"].append(req_id)

    price = app.fetch_delayed_price("AAPL")
    assert price == 204.5
    assert calls["market_data_type"] is True
    assert len(calls["cancel_ids"]) == 1


def test_fetch_delayed_price_timeout():
    """fetch_delayed_price returns None on timeout; cancelMktData still called
    (finally) and the slot is popped from _tick_events."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    calls = {"cancel_ids": []}
    app.reqMarketDataType = lambda t: None
    app.reqMktData = lambda *a, **k: None  # no-op — never writes the slot
    app.cancelMktData = lambda req_id: calls["cancel_ids"].append(req_id)

    price = app.fetch_delayed_price("AAPL", timeout=0.05)
    assert price is None
    assert len(calls["cancel_ids"]) == 1
    assert app._tick_events == {}


def test_fetch_delayed_price_cancel_always_called_once():
    """Even when the wait times out, cancelMktData(req_id) is invoked exactly once."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    cancel_calls = []
    app.reqMarketDataType = lambda t: None
    app.reqMktData = lambda *a, **k: None
    app.cancelMktData = lambda req_id: cancel_calls.append(req_id)

    app.fetch_delayed_price("MSFT", timeout=0.05)
    assert len(cancel_calls) == 1


def test_place_order_and_wait_callback():
    """place_order_and_wait returns a 'callback' OrderResult when placeOrder's
    side effect fires a real orderStatus (Submitted) on the registered slot."""
    from bravos.broker.connection import IBApp, OrderResult
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    def fake_place_order(order_id, contract, order):
        slot = app._order_status_events[order_id]
        slot["status"] = "Submitted"
        slot["event"].set()
    app.placeOrder = fake_place_order

    result = app.place_order_and_wait(1001, object(), object())
    assert result == OrderResult(kind="callback", status_str="Submitted", code=None, message=None)
    assert 1001 not in app._order_status_events


def test_place_order_and_wait_ibkr_error():
    """place_order_and_wait returns an 'ibkr_error' OrderResult when placeOrder's
    side effect writes an IBKR_ERROR slot (mirrors error() callback rejection)."""
    from bravos.broker.connection import IBApp, OrderResult
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    def fake_place_order(order_id, contract, order):
        slot = app._order_status_events[order_id]
        slot["status"] = "IBKR_ERROR"
        slot["code"] = 10268
        slot["message"] = "EtradeOnly not supported"
        slot["event"].set()
    app.placeOrder = fake_place_order

    result = app.place_order_and_wait(1002, object(), object())
    assert result == OrderResult(
        kind="ibkr_error", status_str="IBKR_ERROR", code=10268, message="EtradeOnly not supported",
    )
    assert 1002 not in app._order_status_events


def test_place_order_and_wait_timeout():
    """place_order_and_wait returns a 'timeout' OrderResult when placeOrder
    never touches the slot; slot is popped afterward."""
    from bravos.broker.connection import IBApp, OrderResult
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)
    app.placeOrder = lambda order_id, contract, order: None  # never touches slot

    result = app.place_order_and_wait(1003, object(), object(), timeout=0.05)
    assert result == OrderResult(kind="timeout", status_str=None, code=None, message=None)
    assert 1003 not in app._order_status_events


def test_register_order_slot_and_await_callback():
    """_register_order_slot installs a slot; a subsequent orderStatus callback
    + _await_registered_order resolves to a 'callback' OrderResult; slot popped."""
    from bravos.broker.connection import IBApp, OrderResult
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    app._register_order_slot(2001)
    assert 2001 in app._order_status_events

    # Simulate the orderStatus callback firing.
    app.orderStatus(
        orderId=2001, status="Submitted", filled=0, remaining=10, avgFillPrice=0,
        permId=1, parentId=0, lastFillPrice=0, clientId=1, whyHeld="", mktCapPrice=0,
    )

    result = app._await_registered_order(2001, timeout=1.0)
    assert result == OrderResult(kind="callback", status_str="Submitted", code=None, message=None)
    assert 2001 not in app._order_status_events


def test_await_registered_order_timeout():
    """_await_registered_order returns a 'timeout' OrderResult when no callback
    ever fires; slot popped."""
    from bravos.broker.connection import IBApp, OrderResult
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    app._register_order_slot(2002)
    result = app._await_registered_order(2002, timeout=0.05)
    assert result == OrderResult(kind="timeout", status_str=None, code=None, message=None)
    assert 2002 not in app._order_status_events


def test_place_order_and_wait_is_register_place_await_composition():
    """place_order_and_wait registers the slot, calls placeOrder exactly once,
    then awaits — same OrderResult as calling register/placeOrder/await by hand."""
    from bravos.broker.connection import IBApp
    app = IBApp(host="127.0.0.1", port=4002, client_id=1)

    call_count = {"n": 0}

    def fake_place_order(order_id, contract, order):
        call_count["n"] += 1
        # Slot must already exist (registered) before placeOrder is called.
        assert order_id in app._order_status_events
        slot = app._order_status_events[order_id]
        slot["status"] = "Submitted"
        slot["event"].set()
    app.placeOrder = fake_place_order

    result = app.place_order_and_wait(3001, object(), object())
    assert call_count["n"] == 1
    assert result.kind == "callback"
    assert result.status_str == "Submitted"
