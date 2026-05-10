"""
bravos/broker/connection.py — IBKR connection layer.

IBApp(EWrapper, EClient): combined class that handles connection, handshake,
error routing, heartbeat monitoring, reconnect logic, and startup reconciliation.

Plans:
  03-1: Class skeleton + settings integration (this plan)
  03-2: Heartbeat loop + reconnect logic
  03-3: Startup reconciliation callbacks + DB snapshot write
"""

import logging
import threading
import time

from ibapi.client import EClient
from ibapi.wrapper import EWrapper

from bravos.config.settings import IBKR_CLIENT_ID, IBKR_HOST, get_ibkr_port

logger = logging.getLogger(__name__)

# ── Module-level constants ─────────────────────────────────────────────────

_RETRY_DELAYS = [5, 10, 20, 40, 80]

# Error codes that trigger an immediate reconnect attempt.
_IMMEDIATE_RECONNECT_CODES = {504, 1100}

# Informational codes — safe to log at DEBUG and ignore.
_IGNORE_CODES = {2104, 2106, 2119, 2158, 2110}

# Fatal codes — reconnect will not help; log ERROR and do not retry.
_CRITICAL_NO_RETRY_CODES = {501, 507}

# reqId used for reqAccountSummary — must not collide with order reqIds.
REQ_ID_ACCOUNT_SUMMARY = 9001

# How often (seconds) to send reqCurrentTime to IB Gateway as a keepalive.
HEARTBEAT_INTERVAL = 60

# How long (seconds) to wait for a heartbeat response before declaring stale.
HEARTBEAT_TIMEOUT = 10


# ── IBApp ──────────────────────────────────────────────────────────────────

class IBApp(EWrapper, EClient):
    """
    Combined EWrapper + EClient class for the Bravos broker connection.

    Inheritance order: EWrapper first (it has no __init__), then EClient
    (its __init__ is called explicitly with self as the wrapper).

    Usage:
        app = IBApp(host="127.0.0.1", port=4002, client_id=1)
        connected = app.connect_and_run(timeout=30)
    """

    def __init__(self, host: str, port: int, client_id: int) -> None:
        # EWrapper has no __init__ — skip it.
        EClient.__init__(self, self)

        # Connection parameters — stored but not used until connect_and_run().
        self._host = host
        self._port = port
        self._client_id = client_id

        # Order tracking
        self.next_order_id: int | None = None

        # Connection state
        self._connected = threading.Event()
        self._last_heartbeat_at: float = 0.0

        # Reconnect state
        self._reconnecting = False
        self._recon_lock = threading.Lock()

        # Reconciliation data (populated by Plan 03-3 callbacks)
        self._positions: list[dict] = []
        self._open_orders: list[dict] = []
        self._account_summary: dict = {}

        # Reconciliation sync events
        self._positions_done = threading.Event()
        self._orders_done = threading.Event()
        self._summary_done = threading.Event()

        # Shutdown control
        self._stop_event = threading.Event()

        # Background threads (started by connect_and_run / start_heartbeat_monitor)
        self._api_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None

    # ── Connection ─────────────────────────────────────────────────────────

    def connect_and_run(self, timeout: float = 30) -> bool:
        """
        Establish TCP connection to IB Gateway and start the API message loop.

        Returns True if connection is confirmed within `timeout` seconds
        (i.e. nextValidId fires), False on timeout.
        """
        self._connected.clear()
        self.connect(self._host, self._port, self._client_id)

        self._api_thread = threading.Thread(
            target=self.run,
            name="ibkr-api",
            daemon=True,
        )
        self._api_thread.start()

        connected = self._connected.wait(timeout)
        if connected:
            logger.info(
                "IBApp connected to IB Gateway at %s:%s (clientId=%s)",
                self._host,
                self._port,
                self._client_id,
            )
        else:
            logger.error(
                "IBApp connection timeout after %ss — IB Gateway at %s:%s did not respond",
                timeout,
                self._host,
                self._port,
            )
        return connected

    def is_connected(self) -> bool:
        """Return True if the connection handshake has completed."""
        return self._connected.is_set()

    def stop(self) -> None:
        """
        Gracefully stop the IBApp: set stop event, clear connected flag,
        and disconnect from IB Gateway.

        Safe to call even if connect_and_run() was never called.
        """
        self._stop_event.set()
        self._connected.clear()
        try:
            self.disconnect()
        except Exception:
            pass
        logger.info("IBApp stopped (clientId=%s)", self._client_id)

    # ── EWrapper callbacks: connection handshake ───────────────────────────

    def nextValidId(self, orderId: int) -> None:
        """
        Called by IB Gateway immediately after a successful connection.

        Fires once per connect() call. Sets _connected so connect_and_run()
        can return. Stores next_order_id for use by the order executor.
        Updates _last_heartbeat_at to reset stale-connection detection.
        """
        self.next_order_id = orderId
        self._last_heartbeat_at = time.monotonic()
        self._connected.set()
        logger.info(
            "nextValidId received: orderId=%s port=%s — IBApp connected",
            orderId,
            self._port,
        )

    def currentTime(self, time_val: int) -> None:
        """
        Heartbeat response from IB Gateway (called by Plan 03-2's heartbeat loop).

        Updates _last_heartbeat_at. Not logged — called every 60s, would be noisy.
        """
        self._last_heartbeat_at = time.monotonic()

    def error(
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        """
        Error callback from IB Gateway — routes to appropriate action.

        Routing (in priority order):
          1. _IGNORE_CODES      → DEBUG log, return
          2. _IMMEDIATE_RECONNECT_CODES → ERROR log, call _trigger_reconnect
          3. _CRITICAL_NO_RETRY_CODES  → CRITICAL log, do not reconnect
          4. All others         → WARNING or ERROR log based on reqId
        """
        if errorCode in _IGNORE_CODES:
            logger.debug("IBKR info %s (reqId=%s): %s", errorCode, reqId, errorString)
            return

        if errorCode in _IMMEDIATE_RECONNECT_CODES:
            logger.error(
                "IBKR error %s (reqId=%s): %s — triggering reconnect",
                errorCode,
                reqId,
                errorString,
            )
            self._trigger_reconnect(f"error_{errorCode}")
            return

        if errorCode in _CRITICAL_NO_RETRY_CODES:
            logger.critical(
                "IBKR fatal error %s (reqId=%s): %s — no retry",
                errorCode,
                reqId,
                errorString,
            )
            return

        # All other errors: log at WARNING (system-level) or ERROR (request-level)
        if reqId == -1:
            logger.warning(
                "IBKR system error %s: %s",
                errorCode,
                errorString,
            )
        else:
            logger.error(
                "IBKR request error %s (reqId=%s): %s",
                errorCode,
                reqId,
                errorString,
            )

    # ── Plan 03-2: Heartbeat monitor ───────────────────────────────────────

    def start_heartbeat_monitor(self) -> None:
        """
        Start the heartbeat daemon thread.

        Must be called AFTER connect_and_run() succeeds. Called explicitly
        by run_ingestion.py (Plan 4).
        """
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="ibkr-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        logger.info("Heartbeat monitor started (interval=%ss, timeout=%ss)", HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT)

    def _heartbeat_loop(self) -> None:
        """
        Background loop that sends reqCurrentTime every HEARTBEAT_INTERVAL seconds.

        Exits cleanly when _stop_event is set. Skips the heartbeat check
        when disconnected — the reconnect thread handles recovery.
        """
        while not self._stop_event.wait(timeout=HEARTBEAT_INTERVAL):
            if not self._connected.is_set():
                continue  # disconnected — reconnect thread handles recovery

            self.reqCurrentTime()
            time.sleep(HEARTBEAT_TIMEOUT)

            elapsed = time.monotonic() - self._last_heartbeat_at
            if elapsed > HEARTBEAT_TIMEOUT:
                logger.warning(
                    "Heartbeat timeout: no response in %.1fs (threshold=%ss) — triggering reconnect",
                    elapsed,
                    HEARTBEAT_TIMEOUT,
                )
                self._trigger_reconnect("heartbeat_timeout")

    # ── Plan 03-2: Reconnect state machine ─────────────────────────────────

    def _trigger_reconnect(self, reason: str) -> None:
        """
        Trigger a background reconnect.

        Guard: if _reconnecting is already True, this is a no-op to prevent
        duplicate reconnect threads. Non-blocking — returns immediately.
        """
        with self._recon_lock:
            if self._reconnecting:
                logger.debug("_trigger_reconnect(%s) — already reconnecting, skipping", reason)
                return
            self._reconnecting = True

        recon_thread = threading.Thread(
            target=self._reconnect_loop,
            args=(reason,),
            name="ibkr-reconnect",
            daemon=True,
        )
        recon_thread.start()

    def _reconnect_loop(self, reason: str) -> None:
        """
        Background daemon thread: reconnect with exponential backoff.

        Tries _RETRY_DELAYS[0..4] (5, 10, 20, 40, 80s), then retries every
        60s forever until either connected or _stop_event is set.
        """
        attempt = 0
        while not self._stop_event.is_set():
            delay = _RETRY_DELAYS[attempt] if attempt < len(_RETRY_DELAYS) else 60

            logger.info(
                "Reconnect attempt %s (reason=%s): waiting %ss before retry",
                attempt + 1,
                reason,
                delay,
            )

            self._connected.clear()

            try:
                self.disconnect()
            except Exception:
                pass

            # CLOSE-WAIT drain window (D-06)
            time.sleep(5)
            # Remaining backoff (ensure non-negative)
            time.sleep(max(0, delay - 5))

            if self._stop_event.is_set():
                break

            success = self.connect_and_run()
            if success:
                logger.info("IBApp reconnected on attempt %s (reason=%s)", attempt + 1, reason)
                with self._recon_lock:
                    self._reconnecting = False
                return

            attempt += 1

            if attempt == len(_RETRY_DELAYS):
                logger.critical(
                    "Reconnect failed after %s attempts (reason=%s) — retrying every 60s forever",
                    len(_RETRY_DELAYS),
                    reason,
                )

        # _stop_event was set — clean up
        with self._recon_lock:
            self._reconnecting = False
        logger.info("Reconnect loop exiting: stop event set")

    def start_background_reconnect(self) -> None:
        """
        Trigger a background reconnect for the initial-connect-failed case.

        Used by Plan 4 when the first connect_and_run() call returns False.
        """
        self._trigger_reconnect("initial_connect_failed")

    # ── Plan 03-3: EWrapper reconciliation callbacks ───────────────────────

    def position(self, account: str, contract, position: float, avgCost: float) -> None:
        """
        Called once per position held in the account.

        Appends STK (equity) positions only — v1 scope. Non-STK secTypes
        (OPT, FUT, etc.) are silently ignored.
        Never blocks — only appends to list; _positions_done is set in positionEnd().
        """
        if contract.secType != "STK":
            return
        self._positions.append({
            "ticker": contract.symbol,
            "position": int(position),
            "avg_cost": avgCost,
        })

    def positionEnd(self) -> None:
        """
        Called by IB Gateway after all position() callbacks have fired.

        Sets _positions_done so run_startup_reconciliation() can proceed.
        """
        self._positions_done.set()
        logger.info("reqPositions complete — %d positions received", len(self._positions))

    def openOrder(self, orderId: int, contract, order, orderState) -> None:
        """
        Called once per open order (fired in response to reqAllOpenOrders).

        Appends a summary dict to _open_orders. Never blocks.
        """
        self._open_orders.append({
            "order_id": orderId,
            "ticker": contract.symbol,
            "action": order.action,
            "quantity": order.totalQuantity,
            "status": orderState.status,
        })

    def openOrderEnd(self) -> None:
        """
        Called by IB Gateway after all openOrder() callbacks have fired.

        Sets _orders_done so run_startup_reconciliation() can proceed.
        """
        self._orders_done.set()
        logger.info("reqOpenOrders complete — %d open orders received", len(self._open_orders))

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:
        """
        Called once per requested tag (NetLiquidation, TotalCashValue, AvailableFunds).

        Stores raw value string keyed by tag name. Never blocks.
        """
        self._account_summary[tag] = value

    def accountSummaryEnd(self, reqId: int) -> None:
        """
        Called by IB Gateway after all accountSummary() callbacks have fired.

        Sets _summary_done so run_startup_reconciliation() can proceed.
        """
        self._summary_done.set()
        logger.info("reqAccountSummary complete — %d tags received", len(self._account_summary))

    # ── Plan 03-3: run_startup_reconciliation ─────────────────────────────

    def run_startup_reconciliation(self, db_conn, timeout: float = 30) -> bool:
        """
        After connect_and_run(), fetch positions/orders/account from IBKR,
        write a snapshot, and reconcile against the DB.

        Returns True if all three callbacks completed within `timeout` seconds,
        False if a timeout occurred (partial data still used for snapshot/reconcile).
        """
        # 1. Clear accumulators
        self._positions.clear()
        self._open_orders.clear()
        self._account_summary.clear()

        # 2. Clear sync events
        self._positions_done.clear()
        self._orders_done.clear()
        self._summary_done.clear()

        # 3. Issue all three requests concurrently (callbacks fire on ibkr-api thread)
        self.reqPositions()
        self.reqAllOpenOrders()
        self.reqAccountSummary(REQ_ID_ACCOUNT_SUMMARY, "All", "NetLiquidation,TotalCashValue,AvailableFunds")

        # 4. Wait for all three events
        all_done = (
            self._positions_done.wait(timeout) and
            self._orders_done.wait(timeout) and
            self._summary_done.wait(timeout)
        )

        # 5. Log if timed out
        if not all_done:
            logger.error("Reconciliation timed out — partial data used for snapshot")

        # 6. Write snapshot (always, even on partial data)
        _write_position_snapshot(db_conn, self._positions)

        # 7. Reconcile against DB (always)
        _reconcile_against_db(db_conn, self._positions, self._open_orders)

        return all_done


# ── Plan 03-3: Module-level helper functions ───────────────────────────────


def _write_position_snapshot(db_conn, positions: list[dict]) -> None:
    """
    Insert one row per position into broker_positions_snapshot.

    Does NOT populate market_value — no live prices at reconciliation time.
    Commits after all inserts. Safe to call with an empty positions list.
    """
    with db_conn.cursor() as cur:
        for pos in positions:
            cur.execute(
                "INSERT INTO broker_positions_snapshot (ticker, position, avg_cost, snapshot_at)"
                " VALUES (%s, %s, %s, NOW())",
                (pos["ticker"], pos["position"], pos["avg_cost"]),
            )
    db_conn.commit()
    logger.info("Wrote %d position rows to broker_positions_snapshot", len(positions))


def _reconcile_against_db(db_conn, ibkr_positions: list[dict], ibkr_orders: list[dict]) -> None:
    """
    Compare IBKR positions against open lots in position_lots.

    Logs WARNING for any quantity mismatch. NEVER writes to position_lots (D-08).
    """
    ibkr_pos_map = {p["ticker"]: p["position"] for p in ibkr_positions}

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, SUM(quantity) FROM position_lots"
            " WHERE lot_closed_at IS NULL GROUP BY ticker"
        )
        db_open = {row[0]: row[1] for row in cur.fetchall()}

    # Check DB lots against IBKR
    for ticker, db_qty in db_open.items():
        ibkr_qty = ibkr_pos_map.get(ticker, 0)
        if db_qty != ibkr_qty:
            logger.warning(
                "RECONCILE MISMATCH ticker=%s db_open_qty=%s ibkr_qty=%s"
                " — operator review required",
                ticker,
                db_qty,
                ibkr_qty,
            )

    # Check IBKR positions not in DB
    for ticker, qty in ibkr_pos_map.items():
        if ticker not in db_open and qty != 0:
            logger.warning(
                "RECONCILE IBKR HAS POSITION NOT IN DB ticker=%s ibkr_qty=%s"
                " — operator review required",
                ticker,
                qty,
            )

    # D-08: never write to position_lots
    logger.info("Reconciliation complete — DB not modified (D-08)")


# ── Module-level singleton ─────────────────────────────────────────────────

# Set by run_ingestion.py at startup — callers import this reference.
# Not set at import time to avoid side effects.
# Phase 4 callers: from bravos.broker.connection import ibapp
ibapp: "IBApp | None" = None
