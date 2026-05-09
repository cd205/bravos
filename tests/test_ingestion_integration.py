"""
Integration tests for the full ingestion pipeline.

These tests require:
- Live bravosresearch.com access (with valid credentials in GCP Secret Manager)
- Cloud SQL Auth Proxy running on 127.0.0.1:5432
- Chrome/Chromium installed

Marked with @pytest.mark.integration — skipped by default.
Run explicitly: pytest tests/test_ingestion_integration.py -m integration -x -q

Architecture note (per 02-03 selector discovery):
    Post URLs arrive via Bravos notification emails (Gmail-triggered architecture).
    The scraper exposes fetch_post(url) and process_alert(url) as the primary API.
    These tests exercise that API against the live site.

Requirement coverage:
  INGST-01 -> test_login_succeeds
  INGST-02 -> test_fetch_post_returns_content
  INGST-03 -> test_dedup_end_to_end
  INGST-06 -> test_signal_stored_with_raw_html
  INGST-07 -> test_session_check_after_login
"""
import os
import pytest

# Custom marker for integration tests
pytestmark = pytest.mark.integration

# A known-good Trade Alert URL from bravosresearch.com for testing.
# Replace with a real post URL when running on VM (must be a live member-accessible post).
KNOWN_ALERT_URL = os.environ.get(
    "TEST_ALERT_URL",
    "https://bravosresearch.com/?p=1"  # placeholder — override via env var on VM
)


@pytest.fixture(scope="module")
def live_scraper():
    """Create a BravosScraper with real credentials and Chrome driver.
    Shared across all tests in this module for efficiency (one login).
    """
    from bravos.ingestion.scraper import BravosScraper
    scraper = BravosScraper()
    scraper.startup()
    yield scraper
    scraper.shutdown()


def test_login_succeeds(live_scraper):
    """INGST-01: Scraper logs in with GCP-stored credentials."""
    # After startup(), driver should be on a non-login page
    from bravos.config import settings
    url = live_scraper.driver.current_url
    assert "wp-login" not in url, f"Still on login page: {url}"
    # Verify no login form present (uses By.ID with visible-element filtering)
    from selenium.webdriver.common.by import By
    login_fields = [
        f for f in live_scraper.driver.find_elements(By.ID, settings.LOGIN_USERNAME_ID)
        if f.is_displayed()
    ]
    assert len(login_fields) == 0, "Login form still present after startup"


def test_session_check_after_login(live_scraper):
    """INGST-07: Session check returns True after successful login."""
    assert live_scraper._check_session() is True


def test_fetch_post_returns_content(live_scraper):
    """INGST-02: fetch_post returns a dict with title, url, raw_html, text fields."""
    result = live_scraper.fetch_post(KNOWN_ALERT_URL)
    assert isinstance(result, dict), "fetch_post should return a dict"
    assert "title" in result, "Result missing 'title' key"
    assert "url" in result, "Result missing 'url' key"
    assert "raw_html" in result, "Result missing 'raw_html' key"
    assert "text" in result, "Result missing 'text' key"
    assert result["url"] == KNOWN_ALERT_URL, "URL in result does not match requested URL"


def test_signal_stored_with_raw_html(live_scraper, db_connection):
    """INGST-06: Signal stored with non-empty raw_html after processing via process_alert."""
    # process_alert fetches the post, parses it, and stores it in the DB
    live_scraper.process_alert(KNOWN_ALERT_URL)

    # Verify in DB
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT raw_html, confidence, parse_method, scraped_at FROM signals WHERE post_url = %s",
            (KNOWN_ALERT_URL,)
        )
        row = cur.fetchone()

    assert row is not None, f"Signal not found in DB for URL: {KNOWN_ALERT_URL}"
    assert row[0] is not None and len(row[0]) > 0, "raw_html is empty"
    assert row[1] in ("high", "medium", "low"), f"Unexpected confidence: {row[1]}"
    assert row[2] in ("regex", "spacy"), f"Unexpected parse_method: {row[2]}"
    assert row[3] is not None, "scraped_at is NULL"


def test_dedup_end_to_end(live_scraper, db_connection):
    """INGST-03: Processing same alert URL twice does not create duplicate rows."""
    # process_alert twice — ON CONFLICT DO NOTHING prevents duplicates
    live_scraper.process_alert(KNOWN_ALERT_URL)
    live_scraper.process_alert(KNOWN_ALERT_URL)

    # Verify exactly 1 row
    with db_connection.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM signals WHERE post_url = %s",
            (KNOWN_ALERT_URL,)
        )
        count = cur.fetchone()[0]

    assert count == 1, f"Expected 1 row, found {count} — dedup failed"
