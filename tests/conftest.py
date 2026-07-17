"""
Shared pytest fixtures for the Bravos Trading System test suite.

These fixtures are available to all tests in the tests/ directory.
"""

import os
import pytest

from bravos.db import get_connection


@pytest.fixture
def db_connection():
    """
    Yields a live database connection to the effective configured database
    (bravos.config.settings.DB_NAME).

    Connection string: postgresql://{DB_USER}@{DB_HOST}/{DB_NAME}
    Password: read from BRAVOS_DB_PASSWORD env var via bravos.db.get_connection();
    the password is now REQUIRED — get_connection() raises RuntimeError if it
    is unset or empty (no more change_me_at_deploy fallback).

    ISOLATION GUARD: this fixture (and therefore every test that requests it)
    refuses to run against the production database ('bravos_trading') unless
    explicitly authorized via BRAVOS_TEST_DB_OK=1 or by pointing BRAVOS_DB_NAME
    at a scratch database. This guard exists because DB-writing tests
    (bravos.execution.positions.open_lot / partial_close_lot) commit
    internally, so a test's try/finally rollback happens too late to undo the
    write — fake position_lots rows leaked into production on 2026-06-29 and
    2026-07-07/09 (IMPROVEMENTS.md item 14). Pure-mock tests never request
    this fixture and are completely unaffected.

    The connection is closed automatically after each test.
    """
    from bravos.config import settings

    effective_db = settings.DB_NAME

    if effective_db == "bravos_trading" and os.environ.get("BRAVOS_TEST_DB_OK") != "1":
        pytest.exit(
            "REFUSING to run DB-touching tests against the PRODUCTION database "
            "'bravos_trading'.\n"
            "Test writes here previously leaked fake position_lots into production "
            "(item 14).\n"
            "To run safely, either:\n"
            "  - point tests at a scratch DB:  export BRAVOS_DB_NAME=bravos_test\n"
            "  - or explicitly accept the risk: export BRAVOS_TEST_DB_OK=1\n"
            "(Pure unit tests that do not touch the DB are unaffected and need no "
            "env vars.)",
            returncode=1,
        )

    # get_connection() reads settings.DB_NAME, which IS effective_db (checked
    # by the guard above), plus DB_HOST/PORT/USER — identical connection.
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def _assert_no_fake_lots():
    """
    Session-scoped regression guard: after the full test suite runs, assert
    that no fake position_lots rows (matching the isolation-leak pattern
    ^(OPN|PAR|FIF|SPN)_) were written to the effective database.

    This is the safety net behind the db_connection production-DB guard: if a
    future test somehow writes to a real DB (authorized scratch-DB run, or a
    bug reintroducing the leak), the build fails loudly instead of silently
    polluting position_lots (and by extension the risk gate's open-position
    count) — see IMPROVEMENTS.md item 14.

    Only runs the check when a DB connection is actually authorized:
      - BRAVOS_TEST_DB_OK=1 (production DB explicitly accepted), or
      - settings.DB_NAME != "bravos_trading" (pointed at a scratch DB)
    On pure-unit-only runs (neither set), this fixture does nothing — it
    never opens a connection, so it can't itself trip the production-DB
    guard or fail on a machine with no reachable DB.
    """
    yield

    from bravos.config import settings

    authorized = (
        os.environ.get("BRAVOS_TEST_DB_OK") == "1" or settings.DB_NAME != "bravos_trading"
    )
    if not authorized:
        return

    try:
        conn = get_connection()
    except Exception as exc:
        # Regression guard, not a primary assertion — an unreachable DB here
        # should not mask real test results, so degrade to a warning.
        import warnings

        warnings.warn(f"_assert_no_fake_lots: could not connect to verify isolation ({exc})")
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM position_lots WHERE ticker ~ %s",
                ("^(OPN|PAR|FIF|SPN)_",),
            )
            (count,) = cur.fetchone()
    finally:
        conn.close()

    if count > 0:
        pytest.fail(
            f"ISOLATION LEAK: {count} fake position_lots rows matching "
            "^(OPN|PAR|FIF|SPN)_ found after test run — a test wrote to a real "
            "DB. Purge them and fix the leaking test."
        )


@pytest.fixture
def chrome_options():
    """
    Returns a ChromeOptions object pre-configured for headless, anti-detection
    operation on a Linux VM.

    Flags sourced from the selenium-scraper skill (production-verified):
    - --headless=new         : new headless mode, available Chrome 112+
    - --no-sandbox           : required on Linux VMs without a real user session
    - --disable-dev-shm-usage: prevents /dev/shm size limit crashes
    - --disable-gpu          : avoid GPU errors in headless mode
    - --window-size=1920x1080: consistent viewport for scraping
    - --disable-blink-features=AutomationControlled: anti-detection
    """
    from selenium.webdriver.chrome.options import Options as ChromeOptions

    options = ChromeOptions()

    # Headless mode (new style, Chrome 112+)
    options.add_argument("--headless=new")

    # Stability flags (required on Linux VM)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")

    # Anti-detection
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # Performance
    options.add_argument("--disable-images")

    return options
