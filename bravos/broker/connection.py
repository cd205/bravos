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

        # ── Phase 4: price-tick routing (for executor._fetch_price) ──────
        self._tick_events: dict[int, dict] = {}
        self._tick_lock = threading.Lock()
        self._mkt_req_counter = 2000  # avoids collision with REQ_ID_ACCOUNT_SUMMARY=9001

        # ── Phase 4: order-status routing (for executor._submit_order) ───
        self._order_status_events: dict[int, dict] = {}

        # ── Phase 4: account name (populated by managedAccounts; used by reqPnL) ──
        self._account_name: str = ""

        # ── Phase 4: daily P&L for circuit breaker (populated by pnl callback) ──
        self._daily_pnl: float | None = None

        # ── Phase 5: dedicated DB connection for fill callbacks (api thread) ──
        # Set by run_ingestion.py main() after run_startup_reconciliation succeeds.
        # Owned by the ibkr-api thread (execDetails / orderStatus / position callbacks).
        # NEVER shared with the main thread or executor (psycopg2 connections are not
        # thread-safe; see RESEARCH Pitfall 1).
        self._db_conn = None

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

    # ── Phase 4: Order execution + risk callbacks ──────────────────────────

    def managedAccounts(self, accountsList: str) -> None:
        """
        Called by IB Gateway during the handshake with a comma-separated list
        of account IDs visible to this clientId.

        Fires BEFORE nextValidId in the connect handshake (per RESEARCH Pitfall 3).
        Stores the first account name on _account_name so reqPnL can subscribe
        with a non-empty account string.
        """
        self._account_name = accountsList.split(",")[0].strip()
        logger.info("managedAccounts: account=%s", self._account_name)

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib) -> None:
        """
        Route a tick price to the executor thread waiting on _tick_events[reqId].

        Tick types we care about (live + delayed):
          4  = Last       (live)
          9  = Close      (live)
          68 = Delayed Last
          76 = Delayed Close
        Other tick types (bid/ask/etc.) are ignored. Negative/zero prices are
        ignored (IBKR sends -1.0 as a "no data" sentinel).

        Per RESEARCH Pitfall 2: with reqMarketDataType(3), IBKR sends 68/76
        (NOT 4/9). Both sets must be handled for live+delayed compatibility.
        """
        PRICE_TICK_TYPES = {4, 9, 68, 76}
        if tickType not in PRICE_TICK_TYPES:
            return
        if price <= 0:
            return
        with self._tick_lock:
            slot = self._tick_events.get(reqId)
            if slot is not None:
                slot["price"] = price
                slot["event"].set()

    def orderStatus(
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        """
        Called by IB Gateway whenever an order's state changes.

        Phase 4 scope: route the status string to the executor thread waiting
        on _order_status_events[orderId]. The executor maps IBKR status strings
        (PreSubmitted/Submitted/Inactive) to DB values (SUBMITTED/REJECTED).

        Phase 5 will extend this for fill capture (status='Filled'/'PartiallyFilled'
        with filled/remaining/avgFillPrice).
        """
        logger.info(
            "orderStatus orderId=%s status=%s filled=%s remaining=%s",
            orderId, status, filled, remaining,
        )
        slot = self._order_status_events.get(orderId)
        if slot is not None:
            slot["status"] = status
            slot["event"].set()

        # ── Phase 5 extension: fill status → orders.status update (EXEC-06) ──
        # Phase 4 still owns PreSubmitted/Submitted/Inactive routing via the
        # slot above. Phase 5 only handles Filled / PartiallyFilled which
        # write the fill price + status flip to the DB.
        if self._db_conn is not None:
            if status == "Filled":
                _update_order_filled(self._db_conn, orderId, avgFillPrice)
            elif status == "PartiallyFilled":
                _update_order_partial(self._db_conn, orderId, avgFillPrice)

    def pnl(self, reqId: int, dailyPnL: float, unrealizedPnL: float, realizedPnL: float) -> None:
        """
        Called continuously by IB Gateway while reqPnL subscription is active.

        Stores dailyPnL on _daily_pnl for the risk gate's circuit breaker check.
        No log per call — this fires every few seconds and would be noisy.
        """
        self._daily_pnl = dailyPnL

    # ── Phase 5: execution / fill callbacks ─────────────────────────────────

    def execDetails(self, reqId: int, contract, execution) -> None:
        """
        Canonical fill capture callback (EXEC-05, D-01, D-04).

        Fires once per fill on the ibkr-api thread. Delegates immediately to
        _handle_exec_details (module-level helper) so the logic is testable
        without instantiating IBApp.
        """
        if self._db_conn is None:
            logger.warning(
                "execDetails: _db_conn not set — skipping fill capture exec_id=%s",
                execution.execId,
            )
            return
        _handle_exec_details(self._db_conn, execution, contract)

    def execDetailsEnd(self, reqId: int) -> None:
        """
        Batch completion signal (D-04).

        Phase 5 processes each fill individually in execDetails — execDetailsEnd
        is informational only.
        """
        logger.info(
            "execDetailsEnd received (reqId=%s) — all fills for this batch processed",
            reqId,
        )

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

    def run_periodic_reconciliation(self, db_conn, timeout: float = 30) -> None:
        """
        Periodic reconciliation (IBKR-04, D-08, D-10).

        Called by run_ingestion.run_cycle after each scrape-and-execute pass.
        Reuses the Phase 3 module-level helpers (_write_position_snapshot and
        _reconcile_against_db) — no new reconciliation logic.

        Guard: skip if a prior reqPositions() is still in flight. The previous
        run will eventually set _positions_done; we re-enter on the next cycle.
        (RESEARCH Pitfall 3: concurrent reqPositions corrupts _positions.)

        Mismatch policy (D-09): _reconcile_against_db logs WARNING for any
        quantity difference and NEVER writes to position_lots.
        """
        if not self._positions_done.is_set():
            logger.debug(
                "Periodic reconciliation skipped — prior reqPositions still in flight"
            )
            return

        self._positions.clear()
        self._positions_done.clear()
        self.reqPositions()
        got = self._positions_done.wait(timeout)
        if not got:
            logger.warning(
                "Periodic reconciliation: reqPositions timed out after %ss — "
                "using partial data",
                timeout,
            )
        _write_position_snapshot(db_conn, self._positions)
        _reconcile_against_db(db_conn, self._positions, [])
        logger.info("Periodic reconciliation complete")


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


# ── Phase 5: Module-level fill capture + order status helpers ──────────────


def _handle_exec_details(db_conn, execution, contract) -> None:
    """
    Module-level helper invoked by IBApp.execDetails (D-01 / D-04).

    Sequence:
      1. Look up orders.id + orders.action by ibkr_order_id (execution.orderId)
      2. INSERT a row into executions (idempotent via exec_id UNIQUE)
      3. Dispatch to positions.open_lot (BUY) or positions.partial_close_lot (SELL)

    The positions module is imported inside this function (deferred import)
    to avoid a circular dependency at module load time (mirror of
    executor.py's deferred import of ibapp).
    """
    logger.info(
        "execDetails: execId=%s orderId=%s side=%s shares=%s price=%s",
        execution.execId, execution.orderId, execution.side,
        execution.shares, execution.price,
    )

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, action FROM orders WHERE ibkr_order_id = %s",
            (execution.orderId,),
        )
        row = cur.fetchone()
    if row is None:
        logger.error(
            "execDetails: no order found for ibkr_order_id=%s — "
            "cannot write execution (exec_id=%s)",
            execution.orderId, execution.execId,
        )
        return
    db_order_id, order_action = row[0], row[1]

    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO executions (order_id, exec_id, shares, price, exec_time) "
            "VALUES (%s, %s, %s, %s, NOW()) "
            "ON CONFLICT (exec_id) DO NOTHING",
            (db_order_id, execution.execId, int(execution.shares),
             float(execution.price)),
        )
    db_conn.commit()

    # Deferred import to avoid circular dependency
    # (bravos.execution.positions has no IBKR dependency, so the import
    # is safe at call time but unnecessary at module load).
    from bravos.execution import positions
    ticker = contract.symbol
    if order_action == "BUY":
        positions.open_lot(
            ticker, int(execution.shares), float(execution.price), db_conn,
        )
    else:  # SELL
        positions.partial_close_lot(
            ticker, int(execution.shares), float(execution.price), db_conn,
        )


def _update_order_filled(db_conn, ibkr_order_id: int, avg_fill_price: float) -> None:
    """
    Set orders.status='FILLED' + fill_price + filled_at (EXEC-06, D-02).

    Called from orderStatus when status == 'Filled' (i.e. filled == totalQuantity).
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status='FILLED', fill_price=%s, filled_at=NOW() "
            "WHERE ibkr_order_id=%s",
            (avg_fill_price, ibkr_order_id),
        )
    db_conn.commit()
    logger.info(
        "orders.status -> FILLED for ibkr_order_id=%s avg_fill_price=%s",
        ibkr_order_id, avg_fill_price,
    )


def _update_order_partial(db_conn, ibkr_order_id: int, avg_fill_price: float) -> None:
    """
    Set orders.status='PARTIAL' + fill_price (EXEC-06, D-02).

    Called from orderStatus when status == 'PartiallyFilled'. Does NOT set
    filled_at — the order is not complete yet.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status='PARTIAL', fill_price=%s "
            "WHERE ibkr_order_id=%s",
            (avg_fill_price, ibkr_order_id),
        )
    db_conn.commit()
    logger.info(
        "orders.status -> PARTIAL for ibkr_order_id=%s avg_fill_price=%s",
        ibkr_order_id, avg_fill_price,
    )


# ── Module-level singleton ─────────────────────────────────────────────────

# Set by run_ingestion.py at startup — callers import this reference.
# Not set at import time to avoid side effects.
# Phase 4 callers: from bravos.broker.connection import ibapp
ibapp: "IBApp | None" = None
