"""
tests/test_execution.py — Phase 4 Risk Gate + Order Executor unit and integration tests.

All tests are Wave 0 stubs (skipped). Each test body is the full intended
implementation. Tests are unskipped as their implementing plan lands:
  - 04-03: test_market_hours_*, test_gate_max_*, test_gate_circuit_*, test_gate_log_*
  - 04-04: test_quantity_formula, test_quantity_zero_skipped, test_build_order_*,
           test_order_db_write_pending, test_order_status_submitted, test_order_status_rejected
  - 04-05: integration tests unskipped after scraper wiring lands
"""
import os
import threading
import datetime
from unittest.mock import MagicMock, patch
import pytest


# ── Plan 04-03: RiskGate — market hours, max positions, allocation, circuit breaker ──

@pytest.mark.skip(reason="plan: 04-03")
def test_market_hours_gate_blocks():
    """RiskGate blocks outside 09:30–16:00 ET (e.g. Saturday or 8am ET)."""
    from bravos.risk.gate import RiskGate, _is_market_hours
    from zoneinfo import ZoneInfo
    # Saturday at noon ET — always closed
    sat = datetime.datetime(2026, 5, 16, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("bravos.risk.gate.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = sat
        mock_dt.datetime.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        assert _is_market_hours() is False


@pytest.mark.skip(reason="plan: 04-03")
def test_market_hours_gate_passes():
    """RiskGate allows orders within 09:30–16:00 ET on a weekday."""
    from bravos.risk.gate import _is_market_hours
    from zoneinfo import ZoneInfo
    wed_noon = datetime.datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    with patch("bravos.risk.gate.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = wed_noon
        mock_dt.datetime.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        assert _is_market_hours() is True


@pytest.mark.skip(reason="plan: 04-03")
def test_gate_max_positions():
    """Gate blocks new entries when open_positions >= MAX_OPEN_POSITIONS."""
    from bravos.risk.gate import RiskGate
    gate = RiskGate()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    # Signal load returns an "open" action signal
    # Open positions query returns count = 25 (above default cap of 20)
    mock_cur.fetchone.side_effect = [
        {"action_type": "open", "weight_from": 0, "weight_to": 1, "ticker": "TEST"},  # signal load
        (25,),  # open positions count
    ]
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = 0.0
    with patch("bravos.risk.gate._is_market_hours", return_value=True):
        passed, reason = gate.check(signal_id=1, db_conn=mock_conn, ibapp=mock_ibapp)
    assert passed is False
    assert "max_positions" in reason


@pytest.mark.skip(reason="plan: 04-03")
def test_gate_max_allocation():
    """Gate blocks when (delta_weight × WEIGHT_PCT_PER_UNIT) > MAX_ALLOCATION_PCT."""
    from bravos.risk.gate import RiskGate
    gate = RiskGate()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    # weight 0→10 = delta 10 × 0.05 = 0.50 (above default cap 0.25)
    mock_cur.fetchone.side_effect = [
        {"action_type": "open", "weight_from": 0, "weight_to": 10, "ticker": "TEST"},
        (0,),  # zero open positions — that check passes
    ]
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = 0.0
    with patch("bravos.risk.gate._is_market_hours", return_value=True):
        passed, reason = gate.check(signal_id=1, db_conn=mock_conn, ibapp=mock_ibapp)
    assert passed is False
    assert "max_allocation" in reason


@pytest.mark.skip(reason="plan: 04-03")
def test_gate_circuit_breaker():
    """Gate blocks when ibapp._daily_pnl < DAILY_LOSS_THRESHOLD."""
    from bravos.risk.gate import RiskGate
    gate = RiskGate()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.side_effect = [
        {"action_type": "open", "weight_from": 0, "weight_to": 1, "ticker": "TEST"},
        (0,),  # open positions
    ]
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = -6000.0  # below default -5000 threshold
    with patch("bravos.risk.gate._is_market_hours", return_value=True):
        passed, reason = gate.check(signal_id=1, db_conn=mock_conn, ibapp=mock_ibapp)
    assert passed is False
    assert "circuit_breaker" in reason


@pytest.mark.skip(reason="plan: 04-03")
def test_gate_circuit_none_pnl():
    """Gate passes circuit-breaker check when _daily_pnl is None (not yet received from reqPnL)."""
    from bravos.risk.gate import RiskGate
    gate = RiskGate()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchone.side_effect = [
        {"action_type": "open", "weight_from": 0, "weight_to": 1, "ticker": "TEST"},
        (0,),
    ]
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = None  # reqPnL hasn't fired yet
    with patch("bravos.risk.gate._is_market_hours", return_value=True):
        passed, reason = gate.check(signal_id=1, db_conn=mock_conn, ibapp=mock_ibapp)
    assert passed is True
    assert reason == "pass"


@pytest.mark.skip(reason="plan: 04-03")
def test_gate_log_pass(db_connection):
    """A passing gate decision writes a row to risk_gate_log with gate_passed=True."""
    from bravos.risk.gate import RiskGate
    # Insert a synthetic signal for FK target
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, ticker, action_type, weight_from, weight_to, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"http://test/{os.urandom(4).hex()}", "test", "TEST", "open", 0, 1, "high"),
        )
        signal_id = cur.fetchone()[0]
    db_connection.commit()

    gate = RiskGate()
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = 0.0
    with patch("bravos.risk.gate._is_market_hours", return_value=True):
        passed, reason = gate.check(signal_id=signal_id, db_conn=db_connection, ibapp=mock_ibapp)
    assert passed is True

    with db_connection.cursor() as cur:
        cur.execute("SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s", (signal_id,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] is True
    assert row[1] == "pass"


@pytest.mark.skip(reason="plan: 04-03")
def test_gate_log_block(db_connection):
    """A blocked gate decision writes a row to risk_gate_log with gate_passed=False and the block reason."""
    from bravos.risk.gate import RiskGate
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, ticker, action_type, weight_from, weight_to, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"http://test/{os.urandom(4).hex()}", "test", "TEST", "open", 0, 1, "high"),
        )
        signal_id = cur.fetchone()[0]
    db_connection.commit()

    gate = RiskGate()
    mock_ibapp = MagicMock()
    mock_ibapp._daily_pnl = 0.0
    # Force a block by patching market hours to False
    with patch("bravos.risk.gate._is_market_hours", return_value=False):
        passed, reason = gate.check(signal_id=signal_id, db_conn=db_connection, ibapp=mock_ibapp)
    assert passed is False
    assert "market_hours" in reason

    with db_connection.cursor() as cur:
        cur.execute("SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s", (signal_id,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] is False
    assert "market_hours" in row[1]


# ── Plan 04-04: Executor — quantity formula, order build, DB transitions ────

@pytest.mark.skip(reason="plan: 04-04")
def test_quantity_formula():
    """EXEC-01: quantity = abs(delta_weight) × WEIGHT_PCT_PER_UNIT × NLV / price, rounded down."""
    from bravos.execution.executor import _calculate_quantity
    signal = {"ticker": "AAPL", "weight_from": 0, "weight_to": 4, "action_type": "open"}
    mock_ibapp = MagicMock()
    mock_ibapp._account_summary = {"NetLiquidation": "100000"}
    mock_db = MagicMock()
    qty = _calculate_quantity(signal, "open", mock_db, mock_ibapp, current_price=200.0)
    # delta=4, 4 × 0.05 × 100000 / 200 = 100
    assert qty == 100


@pytest.mark.skip(reason="plan: 04-04")
def test_quantity_zero_skipped():
    """quantity=0 must be detected as invalid before placeOrder."""
    from bravos.execution.executor import _calculate_quantity
    # Tiny delta × small NLV / huge price → 0 shares
    signal = {"ticker": "BRK.A", "weight_from": 0, "weight_to": 1, "action_type": "open"}
    mock_ibapp = MagicMock()
    mock_ibapp._account_summary = {"NetLiquidation": "100"}
    mock_db = MagicMock()
    qty = _calculate_quantity(signal, "open", mock_db, mock_ibapp, current_price=500000.0)
    assert qty == 0


@pytest.mark.skip(reason="plan: 04-04")
def test_build_order_buy():
    """EXEC-02: 'open' and 'add' action types build BUY MKT DAY orders."""
    from bravos.execution.executor import _build_order
    order = _build_order("BUY", 100)
    assert order.action == "BUY"
    assert order.orderType == "MKT"
    assert order.tif == "DAY"
    assert order.totalQuantity == 100
    assert order.outsideRth is False
    assert order.transmit is True


@pytest.mark.skip(reason="plan: 04-04")
def test_build_order_sell():
    """EXEC-02: 'partial_close' and 'close' action types build SELL MKT DAY orders."""
    from bravos.execution.executor import _build_order
    order = _build_order("SELL", 50)
    assert order.action == "SELL"
    assert order.orderType == "MKT"
    assert order.tif == "DAY"
    assert order.totalQuantity == 50


@pytest.mark.skip(reason="plan: 04-04")
def test_order_db_write_pending(db_connection):
    """EXEC-04 / D-08: orders row inserted with status='PENDING_SUBMISSION' BEFORE placeOrder is called."""
    from bravos.execution.executor import _submit_order
    # Insert a synthetic signal for FK
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, ticker, action_type, weight_from, weight_to, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"http://test/{os.urandom(4).hex()}", "test", "AAPL", "open", 0, 1, "high"),
        )
        signal_id = cur.fetchone()[0]
    db_connection.commit()

    # Mock ibapp: capture placeOrder call but do NOT fire orderStatus (we want to assert pre-call DB state)
    mock_ibapp = MagicMock()
    mock_ibapp.next_order_id = 1000
    mock_ibapp._order_status_events = {}

    # Use a side-effect on placeOrder that snapshots DB state at call time
    captured_status = {}
    def capture_db_state(*args, **kwargs):
        with db_connection.cursor() as cur:
            cur.execute("SELECT status FROM orders WHERE ibkr_order_id=%s", (1000,))
            row = cur.fetchone()
            captured_status["status"] = row[0] if row else None
    mock_ibapp.placeOrder = MagicMock(side_effect=capture_db_state)

    _submit_order(mock_ibapp, signal_id, "AAPL", "BUY", 10, db_connection)
    assert captured_status["status"] == "PENDING_SUBMISSION"


@pytest.mark.skip(reason="plan: 04-04")
def test_order_status_submitted(db_connection):
    """EXEC-04: DB status transitions to SUBMITTED when orderStatus callback fires with 'Submitted'."""
    from bravos.execution.executor import _submit_order
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, ticker, action_type, weight_from, weight_to, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"http://test/{os.urandom(4).hex()}", "test", "AAPL", "open", 0, 1, "high"),
        )
        signal_id = cur.fetchone()[0]
    db_connection.commit()

    mock_ibapp = MagicMock()
    mock_ibapp.next_order_id = 1001
    mock_ibapp._order_status_events = {}

    def fire_status_callback(*args, **kwargs):
        # Simulate the orderStatus callback firing with "Submitted"
        slot = mock_ibapp._order_status_events.get(1001)
        if slot is not None:
            slot["status"] = "Submitted"
            slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=fire_status_callback)

    final_status = _submit_order(mock_ibapp, signal_id, "AAPL", "BUY", 10, db_connection)
    assert final_status == "SUBMITTED"
    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM orders WHERE ibkr_order_id=%s", (1001,))
        assert cur.fetchone()[0] == "SUBMITTED"


@pytest.mark.skip(reason="plan: 04-04")
def test_order_status_rejected(db_connection):
    """EXEC-04: DB status transitions to REJECTED when orderStatus callback fires with 'Inactive'."""
    from bravos.execution.executor import _submit_order
    with db_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (post_url, post_title, ticker, action_type, weight_from, weight_to, confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"http://test/{os.urandom(4).hex()}", "test", "BADTKR", "open", 0, 1, "high"),
        )
        signal_id = cur.fetchone()[0]
    db_connection.commit()

    mock_ibapp = MagicMock()
    mock_ibapp.next_order_id = 1002
    mock_ibapp._order_status_events = {}

    def fire_rejected_callback(*args, **kwargs):
        slot = mock_ibapp._order_status_events.get(1002)
        if slot is not None:
            slot["status"] = "Inactive"
            slot["event"].set()
    mock_ibapp.placeOrder = MagicMock(side_effect=fire_rejected_callback)

    final_status = _submit_order(mock_ibapp, signal_id, "BADTKR", "BUY", 10, db_connection)
    assert final_status == "REJECTED"
    with db_connection.cursor() as cur:
        cur.execute("SELECT status FROM orders WHERE ibkr_order_id=%s", (1002,))
        assert cur.fetchone()[0] == "REJECTED"
