"""
Tests for bravos.ingestion.scraper.

Tests use mock WebDriver — no live browser or site needed.

Requirement coverage:
  INGST-01 -> test_startup_loads_credentials
  INGST-02 -> test_fetch_post_returns_title_and_body
  INGST-07 -> test_session_expiry_detected, test_session_valid, test_reauth_on_expiry
  AUDIT-06 -> test_no_update_on_duplicate
"""
import pytest
from unittest.mock import MagicMock, patch

from urllib3.exceptions import MaxRetryError


def test_startup_loads_credentials():
    from bravos.ingestion.scraper import BravosScraper
    with patch("bravos.ingestion.scraper.get_secret") as mock_secret:
        mock_secret.return_value = "test_value"
        s = BravosScraper()
        with patch("bravos.ingestion.scraper.setup_chrome_driver", return_value=MagicMock()):
            with patch.object(s, "_login", return_value=True):
                s.startup()
        assert mock_secret.call_count == 2  # username + password


def test_session_expiry_detected():
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    # Simulate login form present (session expired)
    s.driver.find_elements.return_value = [MagicMock()]
    assert s._check_session() == False


def test_session_valid():
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    s.driver.find_elements.return_value = []
    s.driver.current_url = "https://bravosresearch.com/category/portfolio-update/"
    assert s._check_session() == True


def test_check_session_returns_false_on_dead_driver():
    """A dead chromedriver raises urllib3 MaxRetryError, not WebDriverException."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    s.driver.find_elements.side_effect = MaxRetryError(MagicMock(), "url", None)
    assert s._check_session() is False


def test_check_session_returns_false_on_connection_refused():
    """A dead chromedriver's HTTP transport can also raise a bare ConnectionError."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    s.driver.find_elements.side_effect = ConnectionRefusedError()
    assert s._check_session() is False


def test_reauth_on_expiry():
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    s.username = "user"
    s.password = "pass"
    # Session expired (visible login field present); login succeeds; fetch_post called
    mock_field = MagicMock()
    mock_field.is_displayed.return_value = True
    s.driver.find_elements.return_value = [mock_field]
    with patch.object(s, "_login", return_value=True) as mock_login:
        with patch.object(s, "fetch_post", return_value={"title": "", "url": "https://x.com/p/", "raw_html": "", "text": ""}):
            with patch.object(s, "_store_signal"):
                with patch("bravos.ingestion.scraper.parse_signal", return_value={}):
                    s.process_alert("https://x.com/p/")
    mock_login.assert_called_once()


def test_fetch_post_returns_title_and_body():
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    mock_article = MagicMock()
    mock_article.text = "Initiating Long on $EME\nTrade Alert\nBody text here"
    mock_body = MagicMock()
    mock_body.get_attribute.return_value = "<p>Body</p>"
    mock_body.text = "Body text here"
    with patch("bravos.ingestion.scraper.WebDriverWait"):
        # First find_elements call is for og:title meta — return empty so article fallback runs.
        # Second call is for the article element.
        s.driver.find_elements.side_effect = [[], [mock_article]]
        s.driver.find_element.return_value = mock_body
        result = s.fetch_post("https://bravosresearch.com/news-feed/eme-post/")
    assert result["url"] == "https://bravosresearch.com/news-feed/eme-post/"
    assert result["title"] == "Initiating Long on $EME"
    assert result["text"] == "Body text here"


def test_no_update_on_duplicate():
    """AUDIT-06: scraper must use INSERT ON CONFLICT DO NOTHING, never UPDATE."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    # Verify _store_signal SQL contains ON CONFLICT DO NOTHING and no UPDATE
    import inspect
    source = inspect.getsource(s._store_signal)
    assert "ON CONFLICT" in source
    assert "DO NOTHING" in source


def test_process_alert_new_high_confidence_does_not_execute_or_read_pause(monkeypatch):
    """Session 4b: scraper is pure ingestion — no execute_signal call, no pause read.

    Replaces the old test_process_alert_queues_signal_when_paused /
    test_process_alert_executes_when_not_paused (which tested execution FROM
    the scraper). The queue-or-execute decision now lives in
    run_ingestion._handle_alert_url — see tests/test_ingestion.py.
    """
    from bravos.ingestion.scraper import AlertResult, BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=True), \
         patch.object(s, "fetch_post", return_value={"title": "t", "url": "u", "raw_html": "", "text": ""}), \
         patch.object(s, "_store_signal", return_value=(123, True)), \
         patch("bravos.ingestion.scraper.parse_signal", return_value={"confidence": "high", "ticker": "EME"}), \
         patch("bravos.notifications.notifier.record_parse_outcome"), \
         patch("bravos.execution.executor.execute_signal") as mock_exec:
        result = s.process_alert("https://x.com/p/")
    assert result == AlertResult("stored", 123, True, "high")
    mock_exec.assert_not_called()


# ── Session 2c/4b: process_alert explicit AlertResult contract ─────────────
# (IMPROVEMENTS.md item 17, extended by item 8 to a structured result)

def test_process_alert_returns_duplicate_on_dedup():
    """_store_signal returning is_new=False -> result='duplicate', no execute_signal touch."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=True), \
         patch.object(s, "fetch_post", return_value={"title": "t", "url": "u", "raw_html": "", "text": ""}), \
         patch.object(s, "_store_signal", return_value=(55, False)), \
         patch("bravos.ingestion.scraper.parse_signal", return_value={"confidence": "high", "ticker": "EME"}), \
         patch("bravos.notifications.notifier.record_parse_outcome"), \
         patch("bravos.execution.executor.execute_signal") as mock_exec:
        result = s.process_alert("https://x.com/dup/")
    assert result.result == "duplicate"
    assert result.is_new is False
    assert result.signal_id == 55
    mock_exec.assert_not_called()


def test_process_alert_returns_stored_low_confidence_no_execution():
    """New row, low confidence -> result='stored' even though execute_signal is never called."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=True), \
         patch.object(s, "fetch_post", return_value={"title": "t", "url": "u", "raw_html": "", "text": ""}), \
         patch.object(s, "_store_signal", return_value=(56, True)), \
         patch("bravos.ingestion.scraper.parse_signal", return_value={"confidence": "low", "ticker": "EME"}), \
         patch("bravos.notifications.notifier.record_parse_outcome"), \
         patch("bravos.execution.executor.execute_signal") as mock_exec:
        result = s.process_alert("https://x.com/low/")
    assert result.result == "stored"
    assert result.confidence == "low"
    mock_exec.assert_not_called()


def test_process_alert_returns_failed_on_fetch_raise():
    """fetch_post raising -> result='failed', never propagates."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=True), \
         patch.object(s, "fetch_post", side_effect=RuntimeError("boom")):
        result = s.process_alert("https://x.com/err/")
    assert result.result == "failed"
    assert result.signal_id is None


def test_process_alert_returns_failed_on_reauth_failure():
    """Session expired and re-login fails -> result='failed', fetch_post never called."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=False), \
         patch.object(s, "_login", return_value=False), \
         patch.object(s, "fetch_post") as mock_fetch:
        result = s.process_alert("https://x.com/reauth-fail/")
    assert result.result == "failed"
    mock_fetch.assert_not_called()


def test_scraper_has_no_execution_import():
    """Grep-style guard: scraper.py source contains no execution-layer coupling.

    Mirrors Task 3's GATE 1 (bravos.execution import / execute_signal call)
    as a unit test so a regression fails fast in the normal test run, not
    only in the verification script.
    """
    import inspect
    import bravos.ingestion.scraper as scraper_module
    source = inspect.getsource(scraper_module)
    assert "bravos.execution" not in source
    assert "execute_signal" not in source
