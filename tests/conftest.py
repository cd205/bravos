"""
Shared pytest fixtures for the Bravos Trading System test suite.

These fixtures are available to all tests in the tests/ directory.
"""

import os
import pytest


@pytest.fixture
def db_connection():
    """
    Yields a live psycopg2 connection to the effective configured database
    (bravos.config.settings.DB_NAME).

    Connection string: postgresql://{DB_USER}@{DB_HOST}/{DB_NAME}
    Password: read from BRAVOS_DB_PASSWORD env var, falling back to
    'change_me_at_deploy' (the placeholder used before Secret Manager is wired).

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
    import psycopg2

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

    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    conn = psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,  # Cloud SQL Auth Proxy listens here
        dbname=effective_db,
        user=settings.DB_USER,
        password=password,
    )
    try:
        yield conn
    finally:
        conn.close()


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
