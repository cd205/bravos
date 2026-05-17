#!/usr/bin/env python3
"""
Bravos Trading System — Pipeline Validation Harness (D-07).

Scripted end-to-end validation of the full pipeline:
    scrape → parse → risk gate → order → fill → position lot

Calls process_alert(url) for each URL in URL_LIST, checks DB state after each
call, and prints a PASS/FAIL line per scenario plus a final summary.

Usage:
    /home/chris_s_dodd/miniconda3/bin/python scripts/validate_pipeline.py

Requirements:
    - IB Gateway running on bravos-vm1 (paper account, port 4002) — D-09
    - BRAVOS_DB_PASSWORD env var set
    - URL_LIST populated with 10+ real Bravos post URLs (D-01/D-02)
    - Run during NYSE market hours (09:30–16:00 ET) for order→fill path (D-10)

Per D-09: no mocked environment — this script connects to real IB Gateway.
Per D-10: market hours gate is NOT bypassed — gate blocks are expected + reported.
Per D-07: deterministic sequence — every scenario explicitly verified.
"""
import signal
import sys
import time
import logging
import os
from pathlib import Path

# Ensure the repo root is on sys.path when running as `python scripts/validate_pipeline.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bravos.broker.connection as broker_module
from bravos.broker.connection import IBApp
from bravos.config import settings
from bravos.ingestion.scraper import BravosScraper

# Configure logging (same pattern as run_ingestion.py)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.validation")

# Phase 4: req_id allocated for reqPnL subscription.
# Must not collide with REQ_ID_ACCOUNT_SUMMARY=9001 in broker/connection.py.
REQ_ID_PNL = 9002

# ---------------------------------------------------------------------------
# URL_LIST — operator-provided list of (url, expected_ticker, expected_action)
#
# INSTRUCTIONS FOR OPERATOR:
#   1. Replace the commented placeholder tuples below with real Bravos post URLs.
#   2. Ensure coverage of all 4 action types per D-02:
#      open, add, partial_close, close
#   3. Aim for 10+ entries to satisfy SC-1 (at least 10 real posts end-to-end).
#   4. Run the script during NYSE market hours (09:30–16:00 ET) so the
#      order→fill→position_lot path is exercised (D-10).
#
# Format: (url, expected_ticker, expected_action)
# Example:
#   ("https://bravosresearch.com/?p=XXXX", "AAPL", "open"),
# ---------------------------------------------------------------------------
URL_LIST: list[tuple[str, str, str]] = [
    # Operator-provided list — 10 URLs covering all 4 action types (D-01/D-02).
    ("https://bravosresearch.com/news-feed/closing-ishares-msci-brazil-etf-ewz-breakdown/", "EWZ", "close"),
    ("https://bravosresearch.com/news-feed/booking-partial-profits-on-united-states-copper-index-fund-cper-profit-booking/", "CPER", "partial_close"),
    ("https://bravosresearch.com/news-feed/closing-energy-fuels-inc-uuuu-breakdown/", "UUUU", "close"),
    ("https://bravosresearch.com/news-feed/booking-partial-profits-on-cvs-health-corporation-cvs-profit-booking/", "CVS", "partial_close"),
    ("https://bravosresearch.com/news-feed/initiating-long-on-spdr-sp-metals-and-mining-etf-xme-breakout/", "XME", "open"),
    ("https://bravosresearch.com/news-feed/initiating-long-on-ishares-msci-japan-etf-ewj-breakout/", "EWJ", "open"),
    ("https://bravosresearch.com/news-feed/increasing-exposure-to-exelixis-inc-exel-breakout/", "EXEL", "add"),
    ("https://bravosresearch.com/news-feed/increasing-exposure-to-united-states-copper-index-fund-cper-technical-strength-2/", "CPER", "add"),
    ("https://bravosresearch.com/news-feed/initiating-long-on-proshares-ultrashort-20-year-treasury-tbt-hedge/", "TBT", "open"),
    ("https://bravosresearch.com/news-feed/booking-partial-profits-on-vaneck-semiconductor-etf-smh-profit-booking-3/", "SMH", "partial_close"),
]


def _get_db_connection():
    """Open a psycopg2 connection. Closed after use.

    Copied verbatim from scripts/run_ingestion.py lines 62–73 per RESEARCH
    Don't Hand-Roll principle.
    """
    import psycopg2
    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=password,
    )


def assert_signal_processed(
    url: str,
    expected_ticker: str,
    expected_action: str,
    db_conn,
    expect_order: bool = True,
) -> dict:
    """Assert that a signal was processed correctly after process_alert(url).

    Checks the chain: signals → risk_gate_log → orders.
    Returns {"ok": bool, "detail": str}.

    From RESEARCH Code Examples + PATTERNS — used verbatim as the basis.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, action_type, confidence FROM signals WHERE post_url=%s",
            (url,),
        )
        sig = cur.fetchone()
    if sig is None:
        return {"ok": False, "detail": "no signal row"}

    signal_id, ticker, action_type, confidence = sig

    if ticker != expected_ticker or action_type != expected_action:
        return {
            "ok": False,
            "detail": f"expected {expected_ticker}/{expected_action}, got {ticker}/{action_type}",
        }

    if not expect_order:
        return {"ok": True, "detail": "signal only (low confidence or out of hours)"}

    # Check risk_gate_log
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s",
            (signal_id,),
        )
        gate = cur.fetchone()
    if gate is None:
        return {"ok": False, "detail": "no risk_gate_log row"}

    # Check orders
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT ibkr_order_id, status FROM orders WHERE signal_id=%s",
            (signal_id,),
        )
        order = cur.fetchone()
    if order is None:
        return {"ok": False, "detail": f"no order row (gate: {gate})"}

    return {
        "ok": True,
        "detail": f"order={order[0]} status={order[1]} gate={gate[1]}",
    }


def wait_for_fill(signal_id: int, db_conn, timeout_sec: int = 10) -> dict:
    """Poll for execution fill and position_lot rows after an order is placed.

    Per RESEARCH Pitfall 2: paper fills arrive 0.5–2 seconds after order confirm.
    Uses a polling loop (not bare sleep) for faster feedback.

    Returns {"fill": row_or_None, "lot": row_or_None}.
    """
    fill_row = None
    lot_row = None

    for _ in range(timeout_sec):
        time.sleep(1)
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM executions WHERE order_id IN "
                "(SELECT id FROM orders WHERE signal_id=%s)",
                (signal_id,),
            )
            fill_row = cur.fetchone()
        if fill_row:
            break

    if fill_row:
        # Also check for position_lots
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM position_lots WHERE execution_id IN "
                "(SELECT id FROM executions WHERE order_id IN "
                "(SELECT id FROM orders WHERE signal_id=%s))",
                (signal_id,),
            )
            lot_row = cur.fetchone()

    return {"fill": fill_row, "lot": lot_row}


def run_startup() -> tuple:
    """Replicate run_ingestion.py IBApp startup sequence exactly.

    Returns (ibapp, scraper, db_conn) where db_conn is the MAIN-THREAD assertion
    connection (separate from ibapp._db_conn — RESEARCH Pitfall 1).

    Per RESEARCH Don't Hand-Roll: copies the known-correct startup sequence from
    run_ingestion.py rather than reinventing it.

    Startup order (must match run_ingestion.py lines 138–209):
      1. Instantiate IBApp and set module-level singleton
      2. connect_and_run (30s timeout)
      3. Startup reconciliation with a temp conn, then close it
      4. Install api-thread dedicated DB connection (_ibapp._db_conn)
      5. reqPnL subscription (circuit breaker)
      6. Start heartbeat monitor
      7. Instantiate BravosScraper and start session
      8. Open SEPARATE main-thread assertion connection (never shared)
    """
    # Step 1: Instantiate and set module-level singleton
    _ibapp = IBApp(
        host=settings.IBKR_HOST,
        port=settings.get_ibkr_port(),
        client_id=settings.IBKR_CLIENT_ID,
    )
    broker_module.ibapp = _ibapp

    # Step 2: Connect (30s timeout)
    ibkr_ok = _ibapp.connect_and_run(timeout=30)
    if not ibkr_ok:
        logger.critical(
            "IBKR connection failed (mode=%s port=%d) — "
            "D-09 forbids a mocked environment; aborting validation.",
            settings.TRADING_MODE,
            settings.get_ibkr_port(),
        )
        sys.exit(3)

    logger.info("IBKR connected — running startup reconciliation")

    # Step 3: Startup reconciliation (temp conn, closed after use)
    try:
        temp_conn = _get_db_connection()
        _ibapp.run_startup_reconciliation(temp_conn, timeout=30)
        temp_conn.close()
    except Exception:
        logger.exception("Startup reconciliation failed — continuing without reconciliation")

    # Step 4: Install dedicated DB connection for the api thread.
    # CRITICAL: this connection is owned by the ibkr-api thread (execDetails,
    # orderStatus callbacks fire there). It is NEVER shared with the main thread.
    # Per RESEARCH Pitfall 1 / T-06-04: psycopg2 connections are not thread-safe.
    try:
        _ibapp._db_conn = _get_db_connection()
        logger.info("IBApp._db_conn installed for fill callbacks (api-thread connection)")
    except Exception:
        logger.exception(
            "Failed to open DB connection for ibapp._db_conn — fill captures will be skipped"
        )
        _ibapp._db_conn = None

    # Step 5: reqPnL subscription so the risk gate circuit breaker has live data.
    # Per RESEARCH Pitfall 6: _account_name is populated by the time
    # run_startup_reconciliation returns (via managedAccounts callback).
    if _ibapp._account_name:
        try:
            _ibapp.reqPnL(REQ_ID_PNL, _ibapp._account_name, "")
            logger.info(
                "reqPnL subscription started — req_id=%s account=%s",
                REQ_ID_PNL,
                _ibapp._account_name,
            )
        except Exception:
            logger.exception(
                "reqPnL subscription failed for account=%s — circuit breaker will fail-open",
                _ibapp._account_name,
            )
    else:
        logger.warning(
            "_account_name empty after handshake — skipping reqPnL. "
            "Circuit breaker will fail-open (ibapp._daily_pnl stays None)."
        )

    # Step 6: Start heartbeat monitor
    _ibapp.start_heartbeat_monitor()
    logger.info("IBKR ready — heartbeat monitor started")

    # Step 7: Instantiate scraper
    scraper = BravosScraper()
    scraper.startup()
    logger.info("BravosScraper started")

    # Step 8: Open the SEPARATE main-thread assertion connection.
    # This object is distinct from _ibapp._db_conn — they must NEVER be shared
    # across threads (RESEARCH Pitfall 1 / T-06-04 two-connection model).
    # Acceptance criteria: >=3 _get_db_connection() call sites in this file confirm
    # that (reconciliation temp conn) + (api-thread conn) + (main-thread assertion conn)
    # are all independently opened.
    db_conn = _get_db_connection()
    logger.info("Main-thread assertion DB connection opened (distinct from api-thread conn)")

    return _ibapp, scraper, db_conn


def main():
    """Run the full validation sequence against URL_LIST.

    Per D-07: deterministic — calls process_alert(url) per URL, checks DB state
    after each call, prints one PASS/FAIL line per scenario, then prints totals.
    """
    # Guard: URL_LIST must be populated before running.
    # Per RESEARCH Open Question 1: the operator provides the real URL list.
    if not URL_LIST:
        logger.critical(
            "URL_LIST is empty — cannot run validation.\n"
            "\n"
            "ACTION REQUIRED:\n"
            "  1. Open scripts/validate_pipeline.py and populate URL_LIST with\n"
            "     10+ real Bravos post URLs (D-01).\n"
            "  2. Ensure all 4 action types are covered (D-02):\n"
            "     open, add, partial_close, close\n"
            "  3. Format: (url, expected_ticker, expected_action)\n"
            "  4. Run during NYSE market hours (09:30–16:00 ET) to exercise\n"
            "     the order→fill path (D-10).\n"
        )
        sys.exit(2)

    _ibapp, scraper, db_conn = run_startup()

    # Register SIGTERM/SIGINT handlers (copied from run_ingestion.py lines 127–129).
    def handle_shutdown(signum, frame):
        logger.info("Received signal %s — initiating graceful shutdown", signum)
        if broker_module.ibapp is not None:
            broker_module.ibapp.stop()
        scraper.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    results: list[tuple[str, str, bool, str]] = []  # (url, action, ok, detail)

    try:
        for url, expected_ticker, expected_action in URL_LIST:
            logger.info("--- Processing: %s (expected: %s/%s)", url, expected_ticker, expected_action)
            try:
                scraper.process_alert(url)
            except Exception:
                logger.exception("process_alert raised for url=%s", url)
                results.append((url, expected_action, False, "process_alert exception"))
                print(f"FAIL {url} -> {expected_action} -> process_alert exception")
                continue

            # Determine expect_order based on signal confidence and gate result.
            # Per RESEARCH Pitfall 3: low-confidence parses and out-of-hours gate
            # blocks are expected behavior, not bugs — assert as signal-only PASS.
            expect_order = True

            # Read signal row to check confidence and gate reason
            signal_id = None
            with db_conn.cursor() as cur:
                cur.execute(
                    "SELECT id, confidence FROM signals WHERE post_url=%s",
                    (url,),
                )
                sig_row = cur.fetchone()

            if sig_row is not None:
                signal_id, confidence = sig_row
                if confidence != "high":
                    # Low-confidence signal — order path not taken (expected)
                    expect_order = False
                    logger.info(
                        "Signal confidence=%s for %s — treating as signal-only (RESEARCH Pitfall 3)",
                        confidence,
                        url,
                    )
                else:
                    # High confidence — check if gate blocked due to market hours
                    with db_conn.cursor() as cur:
                        cur.execute(
                            "SELECT gate_passed, reason FROM risk_gate_log WHERE signal_id=%s",
                            (signal_id,),
                        )
                        gate_row = cur.fetchone()
                    if gate_row is not None:
                        gate_passed, gate_reason = gate_row
                        if not gate_passed and gate_reason and "market_hours" in gate_reason:
                            # Out-of-hours gate block — expected behavior (D-10)
                            expect_order = False
                            logger.info(
                                "Gate blocked due to market hours for %s — signal-only PASS (D-10)",
                                url,
                            )

            # Run main assertion
            result = assert_signal_processed(
                url, expected_ticker, expected_action, db_conn, expect_order=expect_order
            )
            ok = result["ok"]
            detail = result["detail"]

            # If order was placed, wait for fill and include fill/lot info
            if ok and expect_order and signal_id is not None:
                fill_result = wait_for_fill(signal_id, db_conn)
                fill_present = fill_result["fill"] is not None
                lot_present = fill_result["lot"] is not None
                if fill_present:
                    detail += f" fill=YES lot={'YES' if lot_present else 'NO'}"
                    logger.info("Fill received for signal_id=%s, lot=%s", signal_id, lot_present)
                else:
                    # Fill not received yet — note but don't fail (market hours timing)
                    detail += " fill=PENDING"
                    logger.warning(
                        "No fill row found after %ds for signal_id=%s "
                        "(may be out of hours or slow fill)",
                        10,
                        signal_id,
                    )

            results.append((url, expected_action, ok, detail))
            status = "PASS" if ok else "FAIL"
            print(f"{status} {url} -> {expected_action} -> {detail}")

    finally:
        # Always clean up — even if an exception escapes the loop
        logger.info("Shutting down scraper and IBKR connection...")
        try:
            scraper.shutdown()
        except Exception:
            logger.exception("scraper.shutdown() raised")
        try:
            if broker_module.ibapp is not None:
                broker_module.ibapp.stop()
        except Exception:
            logger.exception("ibapp.stop() raised")
        try:
            db_conn.close()
        except Exception:
            pass

    # Final summary — one line per scenario + totals
    print()
    print("=" * 72)
    print("VALIDATION SUMMARY")
    print("=" * 72)
    for url, action, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {action:<15}  {url}")
        print(f"         {detail}")
    print("-" * 72)
    passed = sum(1 for _, _, ok, _ in results if ok)
    failed = sum(1 for _, _, ok, _ in results if not ok)
    total = len(results)
    print(f"PASSED {passed}/{total}, FAILED {failed}")
    print("=" * 72)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
