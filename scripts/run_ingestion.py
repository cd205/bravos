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
import os
import queue
import signal
import socket
import sys
import threading
import time
import logging
from pathlib import Path

# Ensure the repo root is on sys.path when running as `python scripts/run_ingestion.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schedule

from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import MaxRetryError

import bravos.broker.connection as broker_module
from bravos.broker.connection import IBApp
from bravos.config import settings
from bravos.config.settings import SCRAPE_INTERVAL_SECONDS
from bravos.ingestion.scraper import BravosScraper, catch_cycle_exceptions
from bravos.execution.executor import _gate
from bravos.notifications.notifier import send_alert, record_parse_outcome

# Configure logging (stdlib per research — not structlog)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.ingestion.daemon")

# Phase 4: req_id allocated for reqPnL subscription.
# Must not collide with REQ_ID_ACCOUNT_SUMMARY=9001 in broker/connection.py.
REQ_ID_PNL = 9002

_shutdown = False
_scraper: BravosScraper | None = None

# ── IBKR pause/resume state ──────────────────────────────────────────────────
# SIGUSR1 pauses the IBKR connection so TWS/desktop can connect.
# SIGUSR2 resumes it and flushes any signals that arrived while paused.
# _paused is read on the alert-socket thread, written only from signal handlers
# (main thread). queue.Queue is thread-safe; no extra lock needed.
_ibkr_paused = False
_paused_signal_queue: queue.Queue[int] = queue.Queue()


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


def _connect_ibkr(ibapp: IBApp) -> bool:
    """Connect ibapp to IB Gateway and run startup reconciliation + subscriptions.

    Extracted so both main() and handle_resume() can reuse the same sequence.
    Returns True if connected successfully.
    """
    ibkr_ok = ibapp.connect_and_run(timeout=30)
    if not ibkr_ok:
        logger.error(
            "IBKR connect failed (mode=%s port=%d) — orders will not be placed",
            settings.TRADING_MODE, settings.get_ibkr_port(),
        )
        ibapp.start_background_reconnect()
        return False

    try:
        _db_conn = _get_db_connection()
        ibapp.run_startup_reconciliation(_db_conn, timeout=30)
        _db_conn.close()
    except Exception:
        logger.exception("Startup reconciliation failed — continuing")

    try:
        ibapp._db_conn = _get_db_connection()
    except Exception:
        logger.exception("Failed to open DB connection for fill callbacks")
        ibapp._db_conn = None

    # Fill replay (IMPROVEMENTS item 16): now that _db_conn is set, request today's
    # executions so fills that landed while disconnected / under another clientId are
    # captured via execDetails. MUST come after _db_conn assignment above, else
    # execDetails drops them at its "_db_conn not set" guard.
    try:
        ibapp.replay_todays_fills()
    except Exception:
        logger.exception("Fill replay on connect failed — continuing")

    if ibapp._account_name:
        try:
            ibapp.reqPnL(REQ_ID_PNL, ibapp._account_name, "")
            logger.info("reqPnL subscription started — account=%s", ibapp._account_name)
        except Exception:
            logger.exception("reqPnL subscription failed — circuit breaker will fail-open")
    else:
        logger.warning("_account_name empty — skipping reqPnL subscription")

    ibapp.start_heartbeat_monitor()
    return True


def handle_pause(signum, frame):
    """SIGUSR1: disconnect IBKR so TWS/desktop can connect.

    Alerts that arrive while paused are queued and flushed on resume.
    Chrome driver and Gmail poller are unaffected.
    """
    global _ibkr_paused
    if _ibkr_paused:
        logger.info("SIGUSR1 received but already paused — ignoring")
        return
    _ibkr_paused = True
    logger.info("IBKR PAUSED — disconnecting ibapi so TWS can connect (run 'bravos-resume' when done)")
    if broker_module.ibapp is not None:
        try:
            broker_module.ibapp.stop()
        except Exception:
            logger.warning("Error stopping ibapp during pause", exc_info=True)


def handle_resume(signum, frame):
    """SIGUSR2: reconnect IBKR and flush any signals queued during the pause."""
    global _ibkr_paused
    if not _ibkr_paused:
        logger.info("SIGUSR2 received but not paused — ignoring")
        return
    _ibkr_paused = False
    logger.info("IBKR RESUMING — reconnecting ibapi...")

    _ibapp = IBApp(
        host=settings.IBKR_HOST,
        port=settings.get_ibkr_port(),
        client_id=settings.IBKR_CLIENT_ID,
    )
    broker_module.ibapp = _ibapp
    connected = _connect_ibkr(_ibapp)

    if connected:
        logger.info("IBKR reconnected after pause")
    else:
        logger.error("IBKR reconnect failed after pause — background reconnect started")

    # Flush signals that arrived while paused
    queued = []
    while not _paused_signal_queue.empty():
        try:
            queued.append(_paused_signal_queue.get_nowait())
        except queue.Empty:
            break

    if queued:
        logger.info("Flushing %d signal(s) queued during IBKR pause: %s", len(queued), queued)
        from bravos.execution.executor import execute_signal
        import psycopg2
        password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
        for signal_id in queued:
            try:
                exec_conn = psycopg2.connect(
                    host=settings.DB_HOST, port=settings.DB_PORT,
                    dbname=settings.DB_NAME, user=settings.DB_USER, password=password,
                )
                try:
                    execute_signal(signal_id, exec_conn)
                finally:
                    exec_conn.close()
            except Exception:
                logger.exception("Failed to execute queued signal_id=%s after resume", signal_id)
    else:
        logger.info("No signals queued during pause")


# ── Driver-dead recovery state (quick-260708-7lk) ───────────────────────────
# Counts consecutive failed full-driver recoveries in run_cycle. Reset to 0 on
# any healthy check, successful re-login, or successful recovery. When it
# reaches RECOVERY_ALERT_THRESHOLD an email alert fires — but the daemon keeps
# retrying recovery every cycle regardless.
_consecutive_recovery_failures = 0
RECOVERY_ALERT_THRESHOLD = 3


def _recover_scraper(reason: str) -> bool:
    """Fully recreate the Chrome driver + BravosScraper (shared recovery primitive).

    Used by both the nightly 06:00 UTC restart and run_cycle's dead-driver
    self-healing. _scraper is set to None BEFORE shutdown so run_cycle()
    returns early via its null-guard if it fires concurrently. _scraper is
    only reassigned AFTER BravosScraper.startup() succeeds; if startup raises,
    _scraper stays None and the daemon keeps running (retries next trigger).

    Returns True on success, False if startup failed (leaving _scraper=None).
    """
    global _scraper
    logger.info("Chrome driver recovery starting (%s) -- recycling BravosScraper", reason)

    old_scraper = _scraper
    _scraper = None  # Guard: run_cycle returns early if _scraper is None

    if old_scraper is not None:
        try:
            old_scraper.shutdown()
            logger.info("Old Chrome driver shut down")
        except Exception:
            logger.warning("Error during old scraper shutdown -- continuing", exc_info=True)

    try:
        new_scraper = BravosScraper()
        new_scraper.startup()
        _scraper = new_scraper
        logger.info("Chrome driver recovery complete (%s) -- new driver active and logged in", reason)
        return True
    except Exception:
        logger.exception(
            "Chrome driver recovery FAILED (%s) -- daemon continues without scraper. "
            "process_alert() calls will be skipped until the next recovery attempt.",
            reason,
        )
        # _scraper remains None; run_cycle() null-guard handles this safely.
        return False


def _attempt_driver_recovery(reason: str) -> bool:
    """Run _recover_scraper with consecutive-failure bookkeeping + threshold alert.

    Shared by run_cycle's dead-driver path and its scraper-missing (null-guard)
    path so a failed recovery is retried EVERY cycle — never stranded until the
    06:00 nightly restart. After RECOVERY_ALERT_THRESHOLD consecutive failures
    an email alert fires each failing cycle, but retrying always continues.
    """
    global _consecutive_recovery_failures
    recovered = _recover_scraper(reason)
    if recovered:
        logger.info("Driver recreated after dead-driver detection")
        _consecutive_recovery_failures = 0
    else:
        _consecutive_recovery_failures += 1
        logger.error(
            "Driver recovery failed (%d consecutive failure(s)) — will retry next cycle",
            _consecutive_recovery_failures,
        )
        if _consecutive_recovery_failures >= RECOVERY_ALERT_THRESHOLD:
            # Email alert (D-02b, NOTF-02 pattern) — daemon keeps retrying.
            import datetime
            send_alert(
                "Scraper Driver Recovery Failing",
                f"Bravos Research Chrome driver recovery failed at "
                f"{datetime.datetime.now().isoformat()}\n"
                f"Consecutive recovery failures: {_consecutive_recovery_failures}\n"
                f"The Chrome driver is dead and could not be recreated. "
                f"Daemon keeps retrying every cycle.\n"
                f"Manual inspection of the VM / Chrome environment may be required.",
            )
    return recovered


@catch_cycle_exceptions
def run_cycle():
    """Periodic health-check cycle: verify session is alive and re-authenticate if needed.

    In the Gmail-triggered architecture, alerts arrive via email — process_alert(url)
    is called directly by the Gmail poller. This cycle keeps the Chrome driver session
    warm so the scraper is ready to process the next alert without a cold login.

    Crash-safe (quick-260708-7lk): decorated with @catch_cycle_exceptions so NO
    exception escapes into schedule.run_pending() — schedule always records the
    run, preserving the 300s cadence and later jobs (06:00 restart, 14:30 reset).
    A dead chromedriver (MaxRetryError/ConnectionError from the transport layer)
    is detected here and self-healed via _recover_scraper on this cycle.
    """
    global _scraper, _consecutive_recovery_failures
    if _scraper is None:
        # Scraper missing — either startup recovery failed or a prior cycle's
        # recovery failed. Retry recovery now instead of waiting for 06:00 UTC.
        logger.warning("run_cycle: scraper not initialized — attempting driver recovery")
        _attempt_driver_recovery("scraper-missing recovery")
        return

    session_ok = _scraper._check_session()
    if session_ok:
        logger.info("Session health check: OK — driver active and authenticated")
        _consecutive_recovery_failures = 0
    else:
        # Session is either expired (login form visible — driver fine) or the
        # driver is dead. Try the cheap re-login path first; a dead driver's
        # _login() -> driver.get() raises the same transport errors, so guard it.
        logger.warning("Session health check: FAILED — re-authenticating")
        login_ok = False
        try:
            login_ok = _scraper._login()
        except (WebDriverException, MaxRetryError, ConnectionError) as e:
            logger.error("Re-login raised transport error — driver is dead: %s", e)

        if login_ok:
            logger.info("Re-authentication succeeded")
            _consecutive_recovery_failures = 0
        else:
            # Driver is dead (or login persistently broken) — recreate it now
            # instead of waiting for the 06:00 UTC nightly restart.
            logger.error("Re-authentication failed — treating driver as dead, recreating")
            _attempt_driver_recovery("driver-dead recovery")

    # ── Phase 5: periodic reconciliation (IBKR-04, D-08) ─────────────────
    # Piggybacks on the scrape cycle: every SCRAPE_INTERVAL_SECONDS, fetch
    # current IBKR positions, snapshot them, and log WARNING on any mismatch
    # against open lots in position_lots. Never auto-corrects (D-09).
    if broker_module.ibapp is not None and broker_module.ibapp.is_connected():
        try:
            _recon_db_conn = _get_db_connection()
            try:
                broker_module.ibapp.run_periodic_reconciliation(_recon_db_conn)
            finally:
                _recon_db_conn.close()
        except Exception:
            logger.exception("Periodic reconciliation failed — continuing")
    else:
        logger.debug(
            "Periodic reconciliation skipped — ibapp not connected"
        )


def _run_alert_socket_server():
    """Listen on a Unix domain socket for alert URLs from the Gmail poller.

    The Gmail poller sends a newline-terminated URL; this thread calls
    scraper.process_alert(url) on the trading daemon's existing Chrome session.
    This avoids the Gmail poller needing its own BravosScraper / Chrome instance.

    Protocol: client sends "<url>\n", server replies "OK\n" or "ERR <msg>\n".
    """
    sock_path = settings.ALERT_SOCKET_PATH
    # Clean up stale socket file from a previous run.
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, 0o600)
    server.listen(5)
    server.settimeout(1.0)
    logger.info("Alert socket listening at %s", sock_path)

    while not _shutdown:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            url = data.decode("utf-8", errors="replace").strip()
            if url:
                logger.info("Alert socket received url=%s", url)
                if _scraper is not None:
                    try:
                        _scraper.process_alert(url)
                        conn.sendall(b"OK\n")
                    except Exception as exc:
                        logger.exception("process_alert failed for url=%s", url)
                        conn.sendall(f"ERR {exc}\n".encode())
                else:
                    logger.warning("Alert socket: scraper not ready, dropping url=%s", url)
                    conn.sendall(b"ERR scraper not ready\n")
        except Exception:
            logger.exception("Alert socket handler error")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    server.close()
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    logger.info("Alert socket server stopped")


def _restart_chrome_driver():
    """Recycle Chrome driver nightly to prevent memory accumulation (D-02, D-03).

    Called by schedule at 06:00 UTC (~01:00 ET winter). Daemon stays alive;
    only the Chrome driver is recycled. Delegates to _recover_scraper, which
    also backs run_cycle's dead-driver self-healing (quick-260708-7lk) — one
    shared recovery primitive, no duplicated logic. If recovery fails, _scraper
    stays None and run_cycle retries recovery on its next 300s cycle (it no
    longer waits for the next nightly attempt).
    """
    logger.info("Nightly Chrome driver restart triggered (D-02)")
    _recover_scraper("nightly restart")


def main():
    global _scraper

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGUSR1, handle_pause)
    signal.signal(signal.SIGUSR2, handle_resume)

    # ── IBKR startup ─────────────────────────────────────────────────────────
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

    if _connect_ibkr(_ibapp):
        logger.info("IBKR ready — heartbeat monitor started")
    else:
        logger.critical(
            "IBKR initial connect failed (mode=%s port=%d) — "
            "starting ingestion without IBKR (D-14). "
            "Orders will not be placed until connection is established.",
            settings.TRADING_MODE,
            settings.get_ibkr_port(),
        )
    logger.info("Pause:  sudo systemctl kill -s USR1 bravos-trading.service  (or: bravos-pause)")
    logger.info("Resume: sudo systemctl kill -s USR2 bravos-trading.service  (or: bravos-resume)")
    # ── End IBKR startup ──────────────────────────────────────────────────────

    logger.info("Starting ingestion daemon...")
    _scraper = BravosScraper()
    _scraper.startup()

    # Start the Unix socket server so the Gmail poller can hand off URLs
    # without needing its own Chrome instance.
    _socket_thread = threading.Thread(
        target=_run_alert_socket_server, daemon=True, name="alert-socket"
    )
    _socket_thread.start()

    # Schedule the scrape cycle (session health check at same interval as alert polling)
    schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(run_cycle)

    # Phase 4: daily circuit breaker reset at market open.
    # 14:30 UTC = 09:30 ET (EST, UTC-5). During EDT (summer, UTC-4) this fires at
    # 10:30 ET — 1 hour late. Known DST limitation; acceptable for v1.
    # gate.reset() clears the daily loss accumulator so a new trading day is not
    # blocked by the previous day's drawdown.
    schedule.every().day.at("14:30").do(_gate.reset)
    logger.info("Scheduled daily RiskGate reset at 14:30 UTC (09:30 ET winter)")

    # Phase 8 (D-02, D-03): nightly Chrome driver restart to prevent memory accumulation.
    # 06:00 UTC = 01:00 ET (EST, UTC-5). During EDT (summer, UTC-4) this fires at
    # 02:00 ET -- 1 hour late. Known DST limitation; both windows are well within
    # the safe interval (after ~12:15am ET Gateway restart, before 4am ET pre-market).
    # Daemon stays alive; only the Chrome driver is recycled in-process.
    schedule.every().day.at("06:00").do(_restart_chrome_driver)
    logger.info("Scheduled nightly Chrome driver restart at 06:00 UTC (~01:00 ET winter, D-02/D-03)")

    # Run first cycle immediately to confirm session is healthy after startup
    logger.info("Running initial scrape cycle...")
    run_cycle()

    logger.info("Ingestion daemon started — polling every %ds", SCRAPE_INTERVAL_SECONDS)
    while not _shutdown:
        try:
            schedule.run_pending()
        except Exception:
            # CR-02: schedule does not swallow job exceptions; guard here so a crashed
            # Chrome driver or other unhandled error in run_cycle does not kill the daemon.
            logger.exception("Unhandled exception in scheduled job — daemon continues")
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
