"""
bravos/execution/executor.py — Single entry point for the order path (Phase 4).

Per D-11: execute_signal(signal_id, db_conn) is the only function the scraper calls
after a high-confidence signal is stored. Internally orchestrates: confidence/action
filter (D-13), ibapp singleton check (D-14), risk gate (D-02), price fetch (D-05/D-06),
quantity calculation (EXEC-01), order submission (D-08/D-10).

Sequence:
  1. Guards: ibapp connected, confidence='high', action_type ∈ {open,add,partial_close,close}
  2. Risk gate (RiskGate.check) — single bypass-free gate
  3. Price fetch via reqMarketDataType(3) + reqMktData, 5s threading.Event wait
  4. Fallback to signals.reference_price if no tick arrives
  5. Compute quantity via EXEC-01 formula (or sum-of-lots for 'close')
  6. Write orders row PENDING_SUBMISSION (D-08)
  7. placeOrder + wait for orderStatus callback (3s)
  8. Update orders row to SUBMITTED / REJECTED

Threading: ibapi callbacks (tickPrice, orderStatus) fire on the api thread and
wake this thread via threading.Event registered in ibapp._tick_events /
ibapp._order_status_events (slots installed by Plan 04-02 callbacks).
"""
import logging
import threading

from ibapi.contract import Contract
from ibapi.order import Order

from bravos.config.settings import WEIGHT_PCT_PER_UNIT
from bravos.risk.gate import RiskGate

logger = logging.getLogger(__name__)

# ── Module constants ───────────────────────────────────────────────────────

PRICE_WAIT_TIMEOUT = 5.0      # seconds — per D-05
ORDER_STATUS_TIMEOUT = 3.0    # seconds — per RESEARCH Pattern 5

# action_type → IBKR Order.action mapping (EXEC-02)
_ACTION_MAP = {
    "open": "BUY",
    "add": "BUY",
    "partial_close": "SELL",
    "close": "SELL",
}

# IBKR orderStatus string → DB orders.status value
_STATUS_MAP = {
    "PreSubmitted": "SUBMITTED",
    "Submitted": "SUBMITTED",
    "Inactive": "REJECTED",   # IBKR uses Inactive for rejected/expired
}

# Module-level singleton so _circuit_tripped latches across calls within the process.
# run_ingestion.py schedules gate.reset() daily via this same instance (D-03/RISK-03).
_gate = RiskGate()


# ── Public entry point ─────────────────────────────────────────────────────

def execute_signal(signal_id: int, db_conn) -> None:
    """
    Entry point for the order path (D-11). Called by the scraper after a
    high-confidence signal is stored.

    Guards (return without action):
      - ibapp is None or not connected (D-14)
      - signal.confidence != 'high' (D-13)
      - signal.action_type not in _ACTION_MAP (D-13)
      - Risk gate blocks
      - quantity == 0
      - No price available (reqMktData timeout + no reference_price)

    Never raises. All errors are logged.
    """
    # D-14: import singleton at call time (not module top) to avoid import cycle
    from bravos.broker.connection import ibapp

    if ibapp is None or not ibapp.is_connected():
        logger.warning("ibapp not connected — skipping execution for signal_id=%s", signal_id)
        return

    signal = _load_signal(signal_id, db_conn)
    if signal is None:
        logger.error("signal_id=%s not found in signals table", signal_id)
        return

    # D-13: confidence + action_type filter
    if signal.get("confidence") != "high":
        logger.info(
            "signal_id=%s confidence=%s — skipping (only high-confidence signals route to orders)",
            signal_id, signal.get("confidence"),
        )
        return

    action_type = signal.get("action_type")
    if action_type not in _ACTION_MAP:
        logger.info(
            "signal_id=%s action_type=%s — skipping (unsupported action)",
            signal_id, action_type,
        )
        return

    # Risk gate (D-02) — single bypass-free check via module-level singleton (_gate).
    # Singleton preserves _circuit_tripped across calls so the latch survives
    # multiple signal events in the same process lifetime (RISK-03).
    passed, reason = _gate.check(signal_id, db_conn, ibapp)
    if not passed:
        logger.info("signal_id=%s blocked by risk gate: %s", signal_id, reason)
        return

    # Price fetch (D-05/D-06)
    ticker = signal["ticker"]
    price = _fetch_price(ticker, ibapp)
    if price is None:
        ref = signal.get("reference_price")
        if ref is None:
            logger.error(
                "signal_id=%s ticker=%s — no live price and no reference_price; cannot size order",
                signal_id, ticker,
            )
            return
        logger.warning(
            "signal_id=%s ticker=%s — using reference_price=%s as price (may be stale)",
            signal_id, ticker, ref,
        )
        price = float(ref)

    # Quantity (EXEC-01 / Pattern 10)
    quantity = _calculate_quantity(signal, action_type, db_conn, ibapp, current_price=price)
    if quantity <= 0:
        logger.info(
            "signal_id=%s ticker=%s — computed quantity=%d, skipping (per A5, IBKR rejects 0-share orders)",
            signal_id, ticker, quantity,
        )
        return

    # Submission (D-08 / D-10 / EXEC-02 / EXEC-04)
    ibkr_action = _ACTION_MAP[action_type]
    final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)
    logger.info(
        "signal_id=%s ticker=%s action=%s quantity=%d final_status=%s",
        signal_id, ticker, ibkr_action, quantity, final_status,
    )


# ── Price fetch ────────────────────────────────────────────────────────────

def _fetch_price(ticker: str, ibapp) -> float | None:
    """
    Fetch a delayed market price for `ticker`. Returns None on timeout.

    Uses reqMarketDataType(3) for delayed data (no live data subscription needed).
    Waits up to PRICE_WAIT_TIMEOUT seconds for a tick (types 4, 9, 68, 76) via
    threading.Event registered in ibapp._tick_events[req_id].

    Always cancels the subscription in finally — uses ibapp.cancelMktData
    (NOT the deprecated cancel method — see RESEARCH Pitfall 1).
    """
    contract = _build_contract(ticker)

    # Allocate a req_id under the lock to avoid races with other executors
    with ibapp._tick_lock:
        req_id = ibapp._mkt_req_counter
        ibapp._mkt_req_counter += 1
        slot = {"event": threading.Event(), "price": None}
        ibapp._tick_events[req_id] = slot

    try:
        ibapp.reqMarketDataType(3)
        ibapp.reqMktData(req_id, contract, "", False, False, [])
        got = slot["event"].wait(timeout=PRICE_WAIT_TIMEOUT)
        return slot["price"] if got else None
    finally:
        try:
            ibapp.cancelMktData(req_id)
        except Exception:
            logger.warning("cancelMktData(%s) raised — continuing", req_id, exc_info=True)
        with ibapp._tick_lock:
            ibapp._tick_events.pop(req_id, None)


# ── Order build helpers ────────────────────────────────────────────────────

def _build_contract(ticker: str) -> Contract:
    """Build a STK SMART USD contract (equities, v1 scope)."""
    contract = Contract()
    contract.symbol = ticker
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


def _build_order(action: str, quantity: int) -> Order:
    """Build a MKT DAY order. `action` must be 'BUY' or 'SELL' (EXEC-02)."""
    order = Order()
    order.action = action
    order.orderType = "MKT"
    order.totalQuantity = quantity
    order.tif = "DAY"
    order.transmit = True
    order.outsideRth = False
    return order


# ── Quantity calculation ───────────────────────────────────────────────────

def _calculate_quantity(signal: dict, action_type: str, db_conn, ibapp, current_price: float) -> int:
    """
    EXEC-01 formula (open/add/partial_close):
      quantity = int(abs(weight_to - weight_from) × WEIGHT_PCT_PER_UNIT × NLV / current_price)

    For action_type == 'close', sell the entire open position:
      quantity = sum(quantity) from position_lots WHERE ticker=signal.ticker AND lot_closed_at IS NULL

    Returns 0 if math yields 0 shares (rounds down). Caller is responsible for
    refusing to place a 0-share order.
    """
    if current_price <= 0:
        logger.error("current_price=%s is non-positive; cannot size order", current_price)
        return 0

    if action_type == "close":
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM position_lots "
                "WHERE ticker = %s AND lot_closed_at IS NULL",
                (signal["ticker"],),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    nlv_raw = (ibapp._account_summary or {}).get("NetLiquidation", "0")
    try:
        nlv = float(nlv_raw)
    except (TypeError, ValueError):
        logger.error("NetLiquidation not float-castable: %r", nlv_raw)
        return 0

    weight_to = signal.get("weight_to") or 0
    weight_from = signal.get("weight_from") or 0
    delta = abs(weight_to - weight_from)
    raw_qty = delta * WEIGHT_PCT_PER_UNIT * nlv / current_price
    return int(raw_qty)  # int() rounds toward zero (floor for non-negatives)


# ── Order submission ───────────────────────────────────────────────────────

def _submit_order(ibapp, signal_id: int, ticker: str, action: str, quantity: int, db_conn) -> str:
    """
    Write the orders row, register the status event, call placeOrder, wait for the
    orderStatus callback, and update the DB row to the final status.

    Returns the final DB status string ('SUBMITTED', 'REJECTED', or 'SUBMITTED'
    when the callback times out — we assume the order is in flight).

    Per D-08: the DB row exists in PENDING_SUBMISSION state BEFORE placeOrder is
    called. Per D-10: ibkr_order_id comes from ibapp.next_order_id, incremented
    before the call so a retry does not reuse the id.
    """
    order_id = ibapp.next_order_id
    if order_id is None:
        logger.error("ibapp.next_order_id is None; cannot place order")
        return "UNKNOWN"
    ibapp.next_order_id = int(order_id) + 1  # D-10 + RESEARCH Pitfall 6

    # 1. Write PENDING_SUBMISSION FIRST (D-08)
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders
              (signal_id, ibkr_order_id, ticker, action, quantity,
               order_type, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, 'MKT', 'PENDING_SUBMISSION', NOW())
            RETURNING id
            """,
            (signal_id, order_id, ticker, action, quantity),
        )
        db_order_id = cur.fetchone()[0]
    db_conn.commit()

    # 2. Register status event BEFORE placeOrder to avoid race with fast callback
    status_slot = {"event": threading.Event(), "status": None}
    ibapp._order_status_events[order_id] = status_slot

    final_status_raw = None
    try:
        contract = _build_contract(ticker)
        order = _build_order(action, quantity)
        ibapp.placeOrder(order_id, contract, order)
        got = status_slot["event"].wait(timeout=ORDER_STATUS_TIMEOUT)
        if not got:
            logger.warning(
                "orderStatus callback timed out for order_id=%s — assuming SUBMITTED",
                order_id,
            )
        final_status_raw = status_slot["status"]
    finally:
        ibapp._order_status_events.pop(order_id, None)

    # 3. Map IBKR status → DB status and update the row
    db_status = _STATUS_MAP.get(final_status_raw, "SUBMITTED")
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = %s WHERE id = %s",
            (db_status, db_order_id),
        )
    db_conn.commit()
    return db_status


# ── DB helpers ─────────────────────────────────────────────────────────────

def _load_signal(signal_id: int, db_conn) -> dict | None:
    """Load the signal row needed by the executor."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, action_type, weight_from, weight_to, "
            "reference_price, confidence FROM signals WHERE id = %s",
            (signal_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return {
        "ticker": row[0],
        "action_type": row[1],
        "weight_from": row[2],
        "weight_to": row[3],
        "reference_price": float(row[4]) if row[4] is not None else None,
        "confidence": row[5],
    }
