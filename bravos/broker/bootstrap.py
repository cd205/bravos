"""
bravos/broker/bootstrap.py — shared post-connect IBKR preparation sequence
(Session 4a, IMPLEMENTATION_PLAN.md item 7).

Collapses the four independently-maintained copies of the
connect -> run_startup_reconciliation -> set _db_conn -> replay_todays_fills
-> reqPnL sequence (scripts/run_ingestion.py::_connect_ibkr,
bravos/broker/connection.py::_refresh_after_connect,
scripts/validate_pipeline.py::run_startup, scripts/execute_pending.py::main)
into a single function: connect_and_prepare().

REQ_ID home decision: REQ_ID_ACCOUNT_SUMMARY / REQ_ID_PNL / REQ_ID_EXECUTIONS
stay defined in bravos.broker.connection (single home) — connection.py uses
all three internally, so moving them here would force connection.py to import
bootstrap.py at module level, which is a cycle (bootstrap already imports
connection.py at module level). bootstrap.py imports the constants FROM
connection for convenience/re-export only; callers are expected to pass their
own pnl_req_id explicitly (see connect_and_prepare signature).
"""
import logging

from bravos.broker.connection import REQ_ID_PNL

logger = logging.getLogger(__name__)


def connect_and_prepare(ibapp, *, pnl_req_id: int = REQ_ID_PNL) -> bool:
    """
    Run the shared post-connect IBKR preparation sequence.

    Precondition: `ibapp` is ALREADY connected (ibapp.connect_and_run()
    already returned True). This function does NOT call connect_and_run and
    does NOT call start_heartbeat_monitor / start_background_reconnect —
    those are connect-lifecycle concerns owned by each caller.

    Sequence (identical to the body every full-sequence caller used to
    duplicate, minus connect + heartbeat):
      1. Open a short-lived DB conn via ibapp._open_short_lived_db_conn();
         run ibapp.run_startup_reconciliation(conn, timeout=30); close conn.
         Wrapped so a failure is logged, never raised.
      2. Set ibapp._db_conn = ibapp._open_short_lived_db_conn() (fresh conn
         owned by the api thread). On failure, log + set _db_conn = None.
      3. ibapp.replay_todays_fills() — wrapped; runs AFTER _db_conn is set
         (execDetails callbacks are dropped if _db_conn is still unset).
      4. If ibapp._account_name: ibapp.reqPnL(pnl_req_id, ibapp._account_name, "")
         (wrapped, logged on failure); else log the "empty account" warning.

    Returns True unconditionally (the sequence was attempted). Never raises.
    """
    try:
        db_conn = ibapp._open_short_lived_db_conn()
        try:
            ibapp.run_startup_reconciliation(db_conn, timeout=30)
        finally:
            db_conn.close()
    except Exception:
        logger.exception(
            "connect_and_prepare: startup reconciliation failed — "
            "account summary may be stale"
        )

    try:
        ibapp._db_conn = ibapp._open_short_lived_db_conn()
    except Exception:
        logger.exception("connect_and_prepare: failed to open DB connection for fill callbacks")
        ibapp._db_conn = None

    # Fill replay (IMPROVEMENTS item 16). MUST come after _db_conn assignment
    # above, else replayed execDetails callbacks are dropped at the
    # "_db_conn not set" guard.
    try:
        ibapp.replay_todays_fills()
    except Exception:
        logger.exception("connect_and_prepare: fill replay failed — continuing")

    if ibapp._account_name:
        try:
            ibapp.reqPnL(pnl_req_id, ibapp._account_name, "")
            logger.info(
                "connect_and_prepare: reqPnL subscription started — account=%s",
                ibapp._account_name,
            )
        except Exception:
            logger.exception(
                "connect_and_prepare: reqPnL subscription failed — "
                "circuit breaker will fail-open"
            )
    else:
        logger.warning(
            "connect_and_prepare: _account_name empty — skipping reqPnL subscription"
        )

    return True
