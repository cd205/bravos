"""
Unit tests for Phase 8 nightly Chrome driver restart behavior (D-02, D-03).

Requirement coverage:
  D-02 / D-03 -> test_nightly_chrome_restart
              -> test_restart_sets_scraper_none_during_transition
              -> test_restart_handles_startup_failure

The function under test is scripts.run_ingestion._restart_chrome_driver.
We import the script as a module (importlib) and patch the BravosScraper
symbol that the script imported into its own namespace at line 43.
"""
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is importable so `import scripts.run_ingestion` works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_run_ingestion():
    """Import scripts.run_ingestion as a module without executing main().

    The module sets up logging at import time but does NOT auto-run main()
    (guarded by `if __name__ == "__main__":` at the bottom). Safe to import
    in tests.
    """
    if "scripts.run_ingestion" in sys.modules:
        return importlib.reload(sys.modules["scripts.run_ingestion"])
    return importlib.import_module("scripts.run_ingestion")


def test_nightly_chrome_restart():
    """D-02: old scraper.shutdown() called, new scraper instantiated and started, _scraper points at new instance."""
    module = _load_run_ingestion()

    old_scraper = MagicMock(name="old_scraper")
    new_scraper = MagicMock(name="new_scraper")

    module._scraper = old_scraper

    with patch.object(module, "BravosScraper", return_value=new_scraper) as mock_cls:
        module._restart_chrome_driver()

    # Old scraper was shut down exactly once.
    old_scraper.shutdown.assert_called_once_with()
    # New scraper was constructed exactly once with no args.
    mock_cls.assert_called_once_with()
    # New scraper was started exactly once.
    new_scraper.startup.assert_called_once_with()
    # Module global now points at the new instance.
    assert module._scraper is new_scraper


def test_restart_sets_scraper_none_during_transition():
    """D-02: _scraper MUST be None at the moment shutdown() is called (concurrent run_cycle guard)."""
    module = _load_run_ingestion()

    observed_during_shutdown = {}

    def assert_scraper_is_none_during_shutdown():
        observed_during_shutdown["value"] = module._scraper

    old_scraper = MagicMock(name="old_scraper")
    old_scraper.shutdown.side_effect = assert_scraper_is_none_during_shutdown

    new_scraper = MagicMock(name="new_scraper")
    module._scraper = old_scraper

    with patch.object(module, "BravosScraper", return_value=new_scraper):
        module._restart_chrome_driver()

    # At the moment shutdown() fired, _scraper had already been cleared.
    assert "value" in observed_during_shutdown, "shutdown() was not invoked"
    assert observed_during_shutdown["value"] is None, (
        f"_scraper was {observed_during_shutdown['value']!r} during shutdown -- "
        "should be None per concurrent guard contract"
    )


def test_restart_handles_startup_failure():
    """D-02: startup() failure must NOT propagate; _scraper stays None so daemon continues."""
    module = _load_run_ingestion()

    old_scraper = MagicMock(name="old_scraper")
    failing_new_scraper = MagicMock(name="new_scraper")
    failing_new_scraper.startup.side_effect = RuntimeError(
        "simulated Chrome startup failure (e.g., GCP Secret Manager unreachable)"
    )

    module._scraper = old_scraper

    with patch.object(module, "BravosScraper", return_value=failing_new_scraper):
        # MUST NOT raise.
        module._restart_chrome_driver()

    # Daemon continues with _scraper = None so run_cycle() null-guard at line 92 fires.
    assert module._scraper is None
    # Old scraper was still shut down before the new one was attempted.
    old_scraper.shutdown.assert_called_once_with()
    # startup() was attempted exactly once.
    failing_new_scraper.startup.assert_called_once_with()
