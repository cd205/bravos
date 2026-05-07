"""
Wave 0 infrastructure test stubs for Phase 1: Infrastructure Setup.

Every test here is decorated with @pytest.mark.skip because the component
it validates has not yet been installed.  Subsequent plans remove the skip
decorator once the component is in place:

  Plan 01-01  → bravos_vm1 provisioned (SSH test)
  Plan 01-02  → IB Gateway installed       (test_gateway_port_reachable)
  Plan 01-03  → PostgreSQL + schema        (test_schema_tables_exist, test_schema_dedup_constraint)
  Plan 01-04  → GCP Secret Manager         (test_secrets_readable)
  Plan 01-05  → Chromium headless          (test_chrome_headless_launch)
  Plan 01-06  → Python 3.11 venv           (test_python_version, test_ibapi_import)

Requirement coverage:
  DEPL-01  → test_gateway_port_reachable
  DEPL-03  → test_schema_tables_exist, test_schema_dedup_constraint
  DEPL-04  → test_secrets_readable
  DEPL-05  → test_chrome_headless_launch
"""

import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# VM-level smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="VM not yet provisioned")
def test_vm_ssh_accessible():
    """
    Verify that bravos-vm1 is running and accessible via SSH.

    Uses gcloud compute ssh to run a trivial echo command on the VM.
    Passes when the command exits 0 and prints 'ok'.
    """
    result = subprocess.run(
        [
            "gcloud",
            "compute",
            "ssh",
            "bravos-vm1",
            "--zone=us-central1-a",
            "--command=echo ok",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"SSH to bravos-vm1 failed.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "ok" in result.stdout, (
        f"Unexpected SSH output: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# DEPL-01: IB Gateway port reachable
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Gateway not yet installed")
def test_gateway_port_reachable():
    """
    DEPL-01: IB Gateway must accept connections on its configured port.

    Paper trading port: 4002.  Checks TCP reachability using nc (netcat).
    This test must be run on bravos-vm1 after IB Gateway is started and
    the operator has approved the 2FA push notification.
    """
    result = subprocess.run(
        ["nc", "-zv", "127.0.0.1", "4002"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        "IB Gateway port 4002 is not reachable.\n"
        "Ensure IB Gateway is running and 2FA has been approved.\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# DEPL-03: PostgreSQL schema
# ---------------------------------------------------------------------------


def test_schema_tables_exist(db_connection):
    """
    DEPL-03: All 5 required trading tables must exist in the bravos_trading database.

    Expected tables:
      - signals
      - orders
      - position_lots
      - executions
      - broker_positions_snapshot
    """
    expected_tables = {
        "signals",
        "orders",
        "position_lots",
        "executions",
        "broker_positions_snapshot",
    }

    with db_connection.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            """
        )
        rows = cur.fetchall()

    found_tables = {row[0] for row in rows}
    missing = expected_tables - found_tables

    assert not missing, (
        f"Missing tables in bravos_trading: {sorted(missing)}\n"
        f"Tables found: {sorted(found_tables)}"
    )


def test_schema_dedup_constraint(db_connection):
    """
    DEPL-03: The signals table must enforce uniqueness on post_url.

    Inserts a test row, then attempts to insert a duplicate post_url.
    The second insert must be silently ignored (ON CONFLICT DO NOTHING),
    leaving exactly one row with that URL.

    Cleans up after itself regardless of test outcome.
    """
    import psycopg2

    test_url = "https://test.bravosresearch.com/dedup-test-fixture"

    with db_connection.cursor() as cur:
        # Insert first row
        cur.execute(
            """
            INSERT INTO signals (post_url, post_title, raw_html)
            VALUES (%s, 'Dedup Test', '<p>fixture</p>')
            ON CONFLICT (post_url) DO NOTHING
            """,
            (test_url,),
        )
        db_connection.commit()

        # Attempt duplicate insert — must silently do nothing
        cur.execute(
            """
            INSERT INTO signals (post_url, post_title, raw_html)
            VALUES (%s, 'Duplicate Title', '<p>dup</p>')
            ON CONFLICT (post_url) DO NOTHING
            """,
            (test_url,),
        )
        db_connection.commit()

        # Confirm exactly one row exists
        cur.execute(
            "SELECT COUNT(*) FROM signals WHERE post_url = %s",
            (test_url,),
        )
        count = cur.fetchone()[0]

    # Always clean up the test row
    with db_connection.cursor() as cur:
        cur.execute("DELETE FROM signals WHERE post_url = %s", (test_url,))
        db_connection.commit()

    assert count == 1, (
        f"Expected exactly 1 row after dedup insert, found {count}.\n"
        "The UNIQUE constraint on signals.post_url may be missing."
    )


# ---------------------------------------------------------------------------
# DEPL-04: GCP Secret Manager — all 6 secrets readable
# ---------------------------------------------------------------------------


def test_secrets_readable():
    """
    DEPL-04: All 6 required secrets must be accessible from GCP Secret Manager.

    Reads each secret via gcloud CLI (which uses the VM's attached service
    account when run on the VM).  Fails if any secret returns a non-zero
    exit code or an empty value.

    Secrets validated:
      - bravos-site-username
      - bravos-site-password
      - bravos-ibkr-username
      - bravos-ibkr-password
      - bravos-ibkr-account-id
      - bravos-db-password
    """
    required_secrets = [
        "bravos-site-username",
        "bravos-site-password",
        "bravos-ibkr-username",
        "bravos-ibkr-password",
        "bravos-ibkr-account-id",
        "bravos-db-password",
    ]

    failures = []
    for secret_name in required_secrets:
        result = subprocess.run(
            [
                "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                f"--secret={secret_name}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            failures.append(f"{secret_name}: gcloud returned exit code {result.returncode} — {result.stderr.strip()}")
        elif not result.stdout.strip():
            failures.append(f"{secret_name}: secret exists but value is empty")

    assert not failures, (
        "The following secrets could not be read from GCP Secret Manager:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


# ---------------------------------------------------------------------------
# DEPL-05: Chromium headless — browser can launch and load a page
# ---------------------------------------------------------------------------


def test_chrome_headless_launch(chrome_options):
    """
    DEPL-05: Headless Chrome must launch and successfully load a URL.

    Uses the chrome_options fixture (from conftest.py) with webdriver-manager
    to auto-select the matching ChromeDriver.  Loads about:blank (no network
    required) and asserts the driver is usable.
    """
    import os
    import time

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.chrome import ChromeDriverManager

    # Kill any stale Chrome processes before starting
    os.system("pkill -9 -f chrome 2>/dev/null || true")
    os.system("pkill -9 -f chromium 2>/dev/null || true")
    os.system("rm -rf /tmp/.org.chromium.* /tmp/chrome_* 2>/dev/null || true")
    time.sleep(1)

    driver = None
    try:
        driver = webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=chrome_options,
        )
        driver.get("about:blank")
        title = driver.title  # Will be empty string for about:blank — that's fine
        current_url = driver.current_url
        assert "about:blank" in current_url or current_url == "about:blank", (
            f"Unexpected current URL after navigating to about:blank: {current_url!r}"
        )
    finally:
        if driver is not None:
            driver.quit()


# ---------------------------------------------------------------------------
# Environment / dependency smoke tests
# ---------------------------------------------------------------------------


def test_ibapi_import():
    """
    Verify that ibapi (official IB TWS API) is importable from the venv.

    ibapi is installed from IB's official zip (not PyPI).  This test
    confirms the installation succeeded and the core classes are accessible.
    """
    from ibapi.client import EClient  # noqa: F401
    from ibapi.contract import Contract  # noqa: F401
    from ibapi.order import Order  # noqa: F401
    from ibapi.wrapper import EWrapper  # noqa: F401

    # If the imports above succeed, ibapi is correctly installed.
    assert True, "ibapi imports succeeded"


def test_python_version():
    """
    Verify the Python interpreter running this test is Python 3.13.

    Per DEV-01 (mirrors opt-trade-vm4), the project uses Python 3.13 + miniconda3.
    """
    major, minor = sys.version_info[:2]
    assert (major, minor) == (3, 13), (
        f"Expected Python 3.13, but running under Python {major}.{minor}.\n"
        "Ensure miniconda3 Python 3.13 is active."
    )
