#!/usr/bin/env python3
"""
Bravos Trading System — Ingestion Daemon Entry Point.

Long-running daemon that serves as the process host for the BravosScraper.
The primary ingestion path is Gmail-triggered: the Gmail poller detects new
Trade Alert notification emails and calls scraper.process_alert(url) directly.

This daemon also runs a session-health check cycle on a schedule to detect
and recover from Chrome driver or session failures without restarting the process.

Usage:
    python scripts/run_ingestion.py

Process management:
    - SIGTERM / SIGINT: graceful shutdown (quits Chrome driver)
    - schedule library fires run_cycle every SCRAPE_INTERVAL_SECONDS
    - Exceptions inside run_cycle are caught by @catch_cycle_exceptions — daemon keeps running

Architecture note (per 02-03 selector discovery):
    Post URLs arrive via Bravos notification emails (not category-page polling).
    The Gmail poller (separate process) extracts URLs from email bodies and calls
    scraper.process_alert(url). The schedule loop here runs a session health check
    to ensure the Chrome driver stays alive between alert events.

Per decisions: D-01, D-13, D-16.
"""
import signal
import sys
import time
import logging
from pathlib import Path

# Ensure the repo root is on sys.path when running as `python scripts/run_ingestion.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schedule

import bravos.broker.connection as broker_module
from bravos.broker.connection import IBApp
from bravos.config import settings
from bravos.config.settings import SCRAPE_INTERVAL_SECONDS
from bravos.ingestion.scraper import BravosScraper

# Configure logging (stdlib per research — not structlog)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.ingestion.daemon")

_shutdown = False
_scraper: BravosScraper | None = None


def _get_db_connection():
    """Open a psycopg2 connection for reconciliation. Closed after use."""
    import psycopg2
    import os
    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=password,
    )


def handle_shutdown(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown
    logger.info("Received signal %s — initiating graceful shutdown", signum)
    _shutdown = True


def run_cycle():
    """Periodic health-check cycle: verify session is alive and re-authenticate if needed.

    In the Gmail-triggered architecture, alerts arrive via email — process_alert(url)
    is called directly by the Gmail poller. This cycle keeps the Chrome driver session
    warm so the scraper is ready to process the next alert without a cold login.
    """
    global _scraper
    if _scraper is None:
        logger.error("run_cycle called before scraper is initialized")
        return

    session_ok = _scraper._check_session()
    if session_ok:
        logger.info("Session health check: OK — driver active and authenticated")
    else:
        logger.warning("Session health check: FAILED — re-authenticating")
        if not _scraper._login():
            logger.error("Re-authentication failed in health check cycle")
        else:
            logger.info("Re-authentication succeeded")


def main():
    global _scraper

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # ── IBKR startup (Phase 3) ────────────────────────────────────────────────
    logger.info(
        "Starting IBKR connection — mode=%s host=%s port=%d client_id=%d",
        settings.TRADING_MODE,
        settings.IBKR_HOST,
        settings.get_ibkr_port(),
        settings.IBKR_CLIENT_ID,
    )
    _ibapp = IBApp(
        host=settings.IBKR_HOST,
        port=settings.get_ibkr_port(),
        client_id=settings.IBKR_CLIENT_ID,
    )
    broker_module.ibapp = _ibapp

    ibkr_ok = _ibapp.connect_and_run(timeout=30)
    if ibkr_ok:
        logger.info("IBKR connected — running startup reconciliation")
        try:
            _db_conn = _get_db_connection()
            _ibapp.run_startup_reconciliation(_db_conn, timeout=30)
            _db_conn.close()
        except Exception:
            logger.exception("Startup reconciliation failed — continuing without reconciliation")
        _ibapp.start_heartbeat_monitor()
        logger.info("IBKR ready — heartbeat monitor started")
    else:
        logger.critical(
            "IBKR initial connect failed (mode=%s port=%d) — "
            "starting ingestion without IBKR (D-14). "
            "Orders will not be placed until connection is established.",
            settings.TRADING_MODE,
            settings.get_ibkr_port(),
        )
        _ibapp.start_background_reconnect()
    # ── End IBKR startup ──────────────────────────────────────────────────────

    logger.info("Starting ingestion daemon...")
    _scraper = BravosScraper()
    _scraper.startup()

    # Schedule the scrape cycle (session health check at same interval as alert polling)
    schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(run_cycle)

    # Run first cycle immediately to confirm session is healthy after startup
    logger.info("Running initial scrape cycle...")
    run_cycle()

    logger.info("Ingestion daemon started — polling every %ds", SCRAPE_INTERVAL_SECONDS)
    while not _shutdown:
        schedule.run_pending()
        time.sleep(1)

    # Graceful cleanup
    logger.info("Shutting down — closing Chrome driver")
    if broker_module.ibapp is not None:
        logger.info("Stopping IBKR connection...")
        broker_module.ibapp.stop()
    _scraper.shutdown()
    logger.info("Ingestion daemon stopped")


if __name__ == "__main__":
    main()
