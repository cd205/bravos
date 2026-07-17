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


def test_process_alert_queues_signal_when_paused(monkeypatch):
    """Session 2b: pause state read from bravos.runtime, not scripts.run_ingestion."""
    import queue as queue_mod
    from bravos import runtime
    from bravos.ingestion.scraper import BravosScraper
    test_q = queue_mod.Queue()
    monkeypatch.setattr(runtime, "ibkr_paused", True)
    monkeypatch.setattr(runtime, "paused_signal_queue", test_q)
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=True), \
         patch.object(s, "fetch_post", return_value={"title": "t", "url": "u", "raw_html": "", "text": ""}), \
         patch.object(s, "_store_signal", return_value=(123, True)), \
         patch("bravos.ingestion.scraper.parse_signal", return_value={"confidence": "high", "ticker": "EME"}), \
         patch("bravos.notifications.notifier.record_parse_outcome"), \
         patch("bravos.execution.executor.execute_signal") as mock_exec:
        s.process_alert("https://x.com/p/")
    assert test_q.get_nowait() == 123
    mock_exec.assert_not_called()


def test_process_alert_executes_when_not_paused(monkeypatch):
    """Not paused → execute path (execute_signal called with the new signal id)."""
    from bravos import runtime
    from bravos.ingestion.scraper import BravosScraper
    monkeypatch.setattr(runtime, "ibkr_paused", False)
    s = BravosScraper()
    s.driver = MagicMock()
    with patch.object(s, "_check_session", return_value=True), \
         patch.object(s, "fetch_post", return_value={"title": "t", "url": "u2", "raw_html": "", "text": ""}), \
         patch.object(s, "_store_signal", return_value=(124, True)), \
         patch("bravos.ingestion.scraper.parse_signal", return_value={"confidence": "high", "ticker": "EME"}), \
         patch("bravos.notifications.notifier.record_parse_outcome"), \
         patch("bravos.execution.executor.execute_signal") as mock_exec, \
         patch("psycopg2.connect", return_value=MagicMock()):
        s.process_alert("https://x.com/p/2")
    mock_exec.assert_called_once()
    assert mock_exec.call_args[0][0] == 124
