"""
Shared pytest fixtures for the Bravos Trading System test suite.

These fixtures are available to all tests in the tests/ directory.
"""

import os
import pytest


@pytest.fixture
def db_connection():
    """
    Yields a live psycopg2 connection to the bravos_trading database.

    Connection string: postgresql://bravos@localhost/bravos_trading
    Password: read from BRAVOS_DB_PASSWORD env var, falling back to
    'change_me_at_deploy' (the placeholder used before Secret Manager is wired).

    The connection is closed automatically after each test.
    """
    import psycopg2

    password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
    conn = psycopg2.connect(
        host="localhost",
        dbname="bravos_trading",
        user="bravos",
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
