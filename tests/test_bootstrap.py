"""
Tests for bravos.broker.bootstrap.connect_and_prepare (Session 4a, item 7).

Pure-mock: ibapp is a MagicMock, no live IBKR connection, no DB. Verifies the
shared post-connect sequence ordering and the no-heartbeat regression guard.
"""
from unittest.mock import MagicMock, call

from bravos.broker.bootstrap import connect_and_prepare


def _make_ibapp(account_name: str = "DU12345") -> MagicMock:
    ibapp = MagicMock()
    ibapp._account_name = account_name
    ibapp._open_short_lived_db_conn.return_value = MagicMock()
    return ibapp


def test_calls_reconcile_then_sets_db_conn_then_replay_then_reqpnl():
    ibapp = _make_ibapp()
    manager = MagicMock()
    manager.attach_mock(ibapp.run_startup_reconciliation, "run_startup_reconciliation")
    manager.attach_mock(ibapp.replay_todays_fills, "replay_todays_fills")
    manager.attach_mock(ibapp.reqPnL, "reqPnL")

    result = connect_and_prepare(ibapp, pnl_req_id=9002)

    assert result is True
    call_names = [c[0] for c in manager.mock_calls]
    assert call_names.index("run_startup_reconciliation") < call_names.index("replay_todays_fills")
    assert call_names.index("replay_todays_fills") < call_names.index("reqPnL")
    # _db_conn must be set (non-None) by the time replay_todays_fills is called —
    # assert it was assigned to the return value of _open_short_lived_db_conn.
    assert ibapp._db_conn is ibapp._open_short_lived_db_conn.return_value


def test_does_not_start_heartbeat():
    """Regression guard: connect_and_prepare must never restart the heartbeat."""
    ibapp = _make_ibapp()
    connect_and_prepare(ibapp, pnl_req_id=9002)
    ibapp.start_heartbeat_monitor.assert_not_called()


def test_does_not_call_connect_and_run():
    ibapp = _make_ibapp()
    connect_and_prepare(ibapp, pnl_req_id=9002)
    ibapp.connect_and_run.assert_not_called()


def test_uses_provided_pnl_req_id():
    ibapp = _make_ibapp()
    connect_and_prepare(ibapp, pnl_req_id=4242)
    ibapp.reqPnL.assert_called_once_with(4242, ibapp._account_name, "")


def test_skips_reqpnl_when_account_name_empty():
    ibapp = _make_ibapp(account_name="")
    connect_and_prepare(ibapp, pnl_req_id=9002)
    ibapp.reqPnL.assert_not_called()


def test_reconcile_failure_does_not_raise():
    ibapp = _make_ibapp()
    ibapp.run_startup_reconciliation.side_effect = RuntimeError("boom")

    result = connect_and_prepare(ibapp, pnl_req_id=9002)

    assert result is True
    assert ibapp._db_conn is ibapp._open_short_lived_db_conn.return_value
