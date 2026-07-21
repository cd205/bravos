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
  7. placeOrder + wait for orderStatus/error callback (3s)
  8. Update orders row to SUBMITTED / REJECTED / UNKNOWN

Threading: ibapi callbacks (tickPrice, orderStatus, error) fire on the api
thread and wake this thread via threading.Event, routed through IBApp's
public seam (fetch_delayed_price / place_order_and_wait / the
register+await order-slot pair — Session 3, item 4/3a). A rejection can
arrive via error() (reqId == orderId, orderStatus never fires) as well as
via orderStatus itself — see _resolve_db_status. SUBMITTED is only ever
assigned when a real PreSubmitted/Submitted callback was observed; a
genuine timeout (no orderStatus and no error callback) records UNKNOWN,
never SUBMITTED.
"""
import logging

from ibapi.order import Order

from bravos.broker.connection import OrderResult, build_contract
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

# IBKR orderStatus string → DB orders.status value. SUBMITTED is only ever
# assigned for a real PreSubmitted/Submitted callback (see _resolve_db_status
# below) — an unrecognised or missing status is never silently mapped to
# SUBMITTED. Status vocabulary: SUBMITTED / REJECTED / UNKNOWN.
_STATUS_MAP = {
    "PreSubmitted": "SUBMITTED",
    "Submitted": "SUBMITTED",
    "Inactive": "REJECTED",   # IBKR uses Inactive for rejected/expired
}

# Module-level singleton so _circuit_tripped latches across calls within the process.
# run_ingestion.py schedules gate.reset() daily via this same instance (D-03/RISK-03).
_gate = RiskGate()


def _resolve_db_status(result: OrderResult, order_id) -> str:
    """
    Map an OrderResult (from ibapp.place_order_and_wait /
    ibapp._await_registered_order) to a DB orders.status value.

    - result.kind == "ibkr_error" -> REJECTED (IBKR rejected via error() callback)
    - result.kind == "timeout"    -> UNKNOWN (order state unverified)
    - result.kind == "callback"   -> _STATUS_MAP (PreSubmitted/Submitted -> SUBMITTED,
                                     Inactive -> REJECTED)

    Never returns SUBMITTED unless a real PreSubmitted/Submitted callback
    was observed. Logs at ERROR for REJECTED and UNKNOWN so both are alertable.
    """
    if result.kind == "ibkr_error":
        logger.error(
            "order_id=%s REJECTED by IBKR: code=%s message=%s",
            order_id, result.code, result.message,
        )
        return "REJECTED"
    if result.kind == "timeout":
        logger.error(
            "order_id=%s status UNVERIFIED — no orderStatus and no error "
            "callback within %ss; recording UNKNOWN. Check TWS manually.",
            order_id, ORDER_STATUS_TIMEOUT,
        )
        return "UNKNOWN"
    return _STATUS_MAP.get(result.status_str, "UNKNOWN")


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
    # NEW — canonical singleton location (Session 2b); read at call time so
    # reassignment by connect/resume is always seen (never from-import it).
    from bravos import runtime
    ibapp = runtime.ibapp

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
    price = ibapp.fetch_delayed_price(ticker)
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

    # Submission (D-10 / D-19 / EXEC-02 / EXEC-04)
    ibkr_action = _ACTION_MAP[action_type]
    stop_loss = signal.get("stop_loss")
    take_profits = signal.get("take_profits", [])
    use_bracket = (
        action_type in ("open", "add")     # D-10: bracket only on entry signals
        and stop_loss is not None
        and stop_loss > 0
        and len(take_profits) > 0
        and quantity >= len(take_profits)  # D-20: fall back if too few shares to split
    )
    if use_bracket:
        final_status = _submit_bracket_order(
            ibapp, signal_id, ticker, ibkr_action, quantity,
            stop_loss, take_profits, db_conn,
        )
        if final_status == "UNKNOWN":
            # CR-03: _submit_bracket_order refused malformed bracket params
            # (e.g. zero/negative TP price). Fall back to a bare MKT order so
            # the entry position is not silently dropped.
            logger.warning(
                "signal_id=%s bracket refused (UNKNOWN) — falling back to bare MKT order",
                signal_id,
            )
            final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)
    else:
        if stop_loss is not None and quantity < len(take_profits):
            logger.warning(
                "signal_id=%s quantity=%d < %d TPs — falling back to bare MKT order (D-20)",
                signal_id, quantity, len(take_profits),
            )
        final_status = _submit_order(ibapp, signal_id, ticker, ibkr_action, quantity, db_conn)
    logger.info(
        "signal_id=%s ticker=%s action=%s quantity=%d bracket=%s final_status=%s",
        signal_id, ticker, ibkr_action, quantity, use_bracket, final_status,
    )


# ── Order build helpers ────────────────────────────────────────────────────
# Price fetch (_fetch_price) and contract construction (_build_contract) moved
# to bravos.broker.connection (ibapp.fetch_delayed_price / build_contract) —
# item 4/3a. Callers below use the public API directly.

def _build_order(action: str, quantity: int) -> Order:
    """Build a MKT DAY order. `action` must be 'BUY' or 'SELL' (EXEC-02)."""
    order = Order()
    order.action = action
    order.orderType = "MKT"
    order.totalQuantity = quantity
    order.tif = "DAY"
    order.transmit = True
    order.outsideRth = False
    # ibapi 9.81 defaults eTradeOnly/firmQuoteOnly to True; IB Gateway 1045+
    # rejects any order carrying these deprecated attributes with error 10268.
    # Must be False until ibapi is upgraded to 10.x (which removes them).
    order.eTradeOnly = False
    order.firmQuoteOnly = False
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

    # NLV parse (missing/unparseable) now lives in IBApp.get_nlv (logs WARNING there);
    # preserve behaviour here: missing/unparseable NLV -> quantity 0.
    nlv = ibapp.get_nlv()
    if nlv is None:
        logger.error("get_nlv() returned None — cannot size order")
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

    Returns the final DB status string: 'SUBMITTED' only for a real
    PreSubmitted/Submitted callback, 'REJECTED' for an Inactive callback or an
    IBKR error() rejection, or 'UNKNOWN' when the callback genuinely times out
    (order state unverified — never assumed SUBMITTED).

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

    # 2. Place + wait via the IBApp order-slot seam (Task 1) — register-slot,
    # placeOrder, await-slot all live in connection.py now.
    contract = build_contract(ticker)
    order = _build_order(action, quantity)
    result = ibapp.place_order_and_wait(order_id, contract, order, timeout=ORDER_STATUS_TIMEOUT)

    # 3. Map OrderResult → DB status and update the row.
    db_status = _resolve_db_status(result, order_id)
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = %s WHERE id = %s",
            (db_status, db_order_id),
        )
    db_conn.commit()
    return db_status


def _submit_bracket_order(
    ibapp, signal_id: int, ticker: str, action: str, quantity: int,
    stop_loss: float, take_profits: list, db_conn,
) -> str:
    """
    Submit a bracket order: parent MKT (transmit=False) + STP child +
    N LMT take-profit children (OCA group, last has transmit=True).

    All N+2 IBKR order IDs are reserved upfront (D-17).
    All DB rows are written PENDING_SUBMISSION before any placeOrder call (D-08 pattern).
    Returns final DB status string for the parent order.
    """
    n = len(take_profits)

    # Input validation (T-09-06: guard against invalid bracket params)
    if stop_loss is None or stop_loss <= 0 or n == 0 or not all(tp > 0 for tp in take_profits):
        logger.warning(
            "signal_id=%s invalid bracket params (stop_loss=%s, take_profits=%s) — refusing bracket",
            signal_id, stop_loss, take_profits,
        )
        return "UNKNOWN"

    # D-17: allocate all N+2 IDs upfront — extend existing single-order pattern
    parent_ibkr_id = ibapp.next_order_id
    if parent_ibkr_id is None:
        logger.error("ibapp.next_order_id is None; cannot place bracket order")
        return "UNKNOWN"
    stp_ibkr_id = int(parent_ibkr_id) + 1
    tp_ibkr_ids = [int(parent_ibkr_id) + 2 + i for i in range(n)]
    ibapp.next_order_id = int(parent_ibkr_id) + n + 2  # reserve full block

    child_action = "SELL" if action == "BUY" else "BUY"
    oca_group = f"TP_{parent_ibkr_id}"  # D-15
    base_qty = quantity // n

    # WR-03: Batch all N+2 INSERTs in a single transaction, commit once before
    # any placeOrder call. Previously each INSERT was committed individually, so
    # a partial failure left orphaned PENDING_SUBMISSION rows with no IBKR orders.
    # Now an INSERT failure rolls back the entire transaction cleanly.
    with db_conn.cursor() as cur:
        # 1a. Parent MKT order (D-08 pattern)
        cur.execute(
            """
            INSERT INTO orders
              (signal_id, ibkr_order_id, ticker, action, quantity,
               order_type, status, submitted_at)
            VALUES (%s, %s, %s, %s, %s, 'MKT', 'PENDING_SUBMISSION', NOW())
            RETURNING id
            """,
            (signal_id, parent_ibkr_id, ticker, action, quantity),
        )
        db_parent_order_id = cur.fetchone()[0]

        # 1b. STP child — included in the same OCA group as TPs (WR-01).
        # OCA type 2 (REDUCE_WITH_BLOCK) causes IBKR to reduce the STP quantity
        # proportionally as TP orders fill, preventing an oversell if all TPs fill
        # before the stop triggers.
        cur.execute(
            """
            INSERT INTO orders
              (signal_id, ibkr_order_id, ticker, action, quantity,
               order_type, status, submitted_at, parent_order_id, oca_group)
            VALUES (%s, %s, %s, %s, %s, 'STP', 'PENDING_SUBMISSION', NOW(), %s, %s)
            """,
            (signal_id, stp_ibkr_id, ticker, child_action, quantity, db_parent_order_id,
             oca_group),
        )

        # 1c. LMT TP children — each gets oca_group (D-16)
        for i, tp_price in enumerate(take_profits):
            is_last = (i == n - 1)
            tp_qty = base_qty if not is_last else quantity - (n - 1) * base_qty  # D-11 remainder rule
            cur.execute(
                """
                INSERT INTO orders
                  (signal_id, ibkr_order_id, ticker, action, quantity,
                   order_type, status, submitted_at, parent_order_id, oca_group)
                VALUES (%s, %s, %s, %s, %s, 'LMT', 'PENDING_SUBMISSION', NOW(), %s, %s)
                """,
                (signal_id, tp_ibkr_ids[i], ticker, child_action, tp_qty,
                 db_parent_order_id, oca_group),
            )
    db_conn.commit()  # single commit after ALL N+2 INSERTs succeed

    # Build contract and order objects
    contract = build_contract(ticker)

    # Parent: MKT, transmit=False (D-14)
    # error 10268 guard — see _build_order (ibapi 9.81 eTradeOnly/firmQuoteOnly default True)
    parent_order = Order()
    parent_order.action = action
    parent_order.orderType = "MKT"
    parent_order.totalQuantity = quantity
    parent_order.tif = "DAY"
    parent_order.transmit = False        # explicitly False — ibapi default is True (pitfall 2)
    parent_order.outsideRth = False      # match _build_order pattern
    parent_order.eTradeOnly = False
    parent_order.firmQuoteOnly = False

    # STP child: stop loss, OCA type 2 so quantity reduces as TPs fill (WR-01 / D-13)
    stp_order = Order()
    stp_order.action = child_action
    stp_order.orderType = "STP"
    stp_order.totalQuantity = quantity
    stp_order.auxPrice = stop_loss       # STP trigger price uses auxPrice, NOT lmtPrice (pitfall)
    stp_order.tif = "GTC"               # GTC — stop must persist beyond today's session
    stp_order.parentId = int(parent_ibkr_id)  # IBKR order ID, NOT db_parent_order_id (pitfall 1)
    stp_order.ocaGroup = oca_group       # same OCA group as TPs (WR-01)
    stp_order.ocaType = 2                # REDUCE_WITH_BLOCK — STP qty reduces as TPs fill
    stp_order.transmit = False
    stp_order.eTradeOnly = False
    stp_order.firmQuoteOnly = False

    # TP children: LMT GTC, OCA type 2 (D-12/D-14/D-15)
    tp_orders = []
    for i, tp_price in enumerate(take_profits):
        is_last = (i == n - 1)
        tp_qty = base_qty if not is_last else quantity - (n - 1) * base_qty
        tp_order = Order()
        tp_order.action = child_action
        tp_order.orderType = "LMT"
        tp_order.totalQuantity = tp_qty
        tp_order.lmtPrice = tp_price     # LMT uses lmtPrice, NOT auxPrice (pitfall)
        tp_order.tif = "GTC"
        tp_order.parentId = int(parent_ibkr_id)  # IBKR order ID
        tp_order.ocaGroup = oca_group
        tp_order.ocaType = 2             # REDUCE_WITH_BLOCK — D-12
        tp_order.transmit = is_last      # D-14: only last TP fires the bracket
        tp_order.eTradeOnly = False
        tp_order.firmQuoteOnly = False
        tp_orders.append(tp_order)

    # 2. Register status event for parent BEFORE placeOrder — via the IBApp
    # order-slot seam (Task 1). The bracket cannot use the atomic
    # place_order_and_wait because it must place all N+2 orders BEFORE
    # waiting (last TP transmit=True is what fires the whole bracket).
    ibapp._register_order_slot(int(parent_ibkr_id))

    # Submission order: parent → STP → TP[0..N-2] → TP[N-1] (transmit=True fires all)
    ibapp.placeOrder(int(parent_ibkr_id), contract, parent_order)
    ibapp.placeOrder(stp_ibkr_id, contract, stp_order)
    for i, tp_order in enumerate(tp_orders):
        ibapp.placeOrder(tp_ibkr_ids[i], contract, tp_order)

    # Await parent status via the seam (pops the slot internally)
    result = ibapp._await_registered_order(int(parent_ibkr_id), timeout=ORDER_STATUS_TIMEOUT)

    # 3. Map OrderResult → DB status and update the parent row
    db_status = _resolve_db_status(result, parent_ibkr_id)
    with db_conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = %s WHERE ibkr_order_id = %s",
            (db_status, parent_ibkr_id),
        )
    db_conn.commit()

    # WR-02: Update child (STP + TP) rows from PENDING_SUBMISSION to SUBMITTED —
    # but ONLY when the parent itself is genuinely SUBMITTED. If the parent was
    # REJECTED or UNKNOWN, the children must NOT be claimed as submitted; leave
    # them in PENDING_SUBMISSION for operator review (T-6gz-01/02).
    if db_status == "SUBMITTED":
        child_ibkr_ids = [stp_ibkr_id] + tp_ibkr_ids
        with db_conn.cursor() as cur:
            cur.execute(
                "UPDATE orders SET status = 'SUBMITTED' WHERE ibkr_order_id = ANY(%s)"
                " AND status = 'PENDING_SUBMISSION'",
                (child_ibkr_ids,),
            )
        db_conn.commit()

    return db_status


# ── DB helpers ─────────────────────────────────────────────────────────────

def _load_signal(signal_id: int, db_conn) -> dict | None:
    """Load the signal row needed by the executor."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, action_type, weight_from, weight_to, "
            "reference_price, confidence, stop_loss, take_profits "
            "FROM signals WHERE id = %s",
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
        "stop_loss": float(row[6]) if row[6] is not None else None,   # Phase 9 D-03
        "take_profits": list(row[7]) if row[7] is not None else [],   # Phase 9 D-03; psycopg2 returns list
    }
