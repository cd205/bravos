"""
Tests for bravos.ingestion.scraper.

Tests use mock WebDriver — no live browser or site needed.

Requirement coverage:
  INGST-01 -> test_startup_loads_credentials, test_login_calls_automated_login
  INGST-02 -> test_get_recent_posts_returns_list
  INGST-07 -> test_session_expiry_detected, test_session_valid, test_reauth_on_expiry
  AUDIT-06 -> test_no_update_on_duplicate
"""
import pytest
from unittest.mock import MagicMock, patch


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


def test_reauth_on_expiry():
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    s.username = "user"
    s.password = "pass"
    # First call: session expired; login succeeds
    s.driver.find_elements.return_value = [MagicMock()]
    with patch.object(s, "_login", return_value=True) as mock_login:
        with patch.object(s, "_get_recent_posts", return_value=[]):
            s.run_cycle()
    mock_login.assert_called_once()


def test_get_recent_posts_returns_list():
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    s.driver = MagicMock()
    # Mock WebDriverWait and article elements
    with patch("bravos.ingestion.scraper.WebDriverWait"):
        mock_article = MagicMock()
        mock_link = MagicMock()
        mock_link.text = "Initiating Long on $EME"
        mock_link.get_attribute.return_value = "https://bravosresearch.com/portfolio-update/eme-post/"
        mock_article.find_element.return_value = mock_link
        s.driver.find_elements.return_value = [mock_article]
        posts = s._get_recent_posts()
    assert len(posts) == 1
    assert posts[0]["url"] == "https://bravosresearch.com/portfolio-update/eme-post/"


def test_no_update_on_duplicate():
    """AUDIT-06: scraper must use INSERT ON CONFLICT DO NOTHING, never UPDATE."""
    from bravos.ingestion.scraper import BravosScraper
    s = BravosScraper()
    # Verify _store_signal SQL contains ON CONFLICT DO NOTHING and no UPDATE
    import inspect
    source = inspect.getsource(s._store_signal)
    assert "ON CONFLICT" in source
    assert "DO NOTHING" in source
    assert "UPDATE" not in source.upper().split("ON CONFLICT")[0]
