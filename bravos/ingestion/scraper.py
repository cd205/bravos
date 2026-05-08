"""
Bravos Trading System — Selenium Scraper.

Persistent Chrome driver (per D-01), session expiry detection (per D-02),
category page scraping (per D-06, D-07), and post body extraction.
"""
import logging

logger = logging.getLogger(__name__)


class BravosScraper:
    """Scrapes bravosresearch.com Trade Alert posts via Selenium."""

    def __init__(self):
        self.driver = None
        self.username = None
        self.password = None

    def startup(self):
        """Create Chrome driver, load credentials, login. Called once at daemon start."""
        raise NotImplementedError("Stub — implemented in plan 02-03")

    def run_cycle(self):
        """Single 5-minute scrape cycle: check session, scrape, parse, store."""
        raise NotImplementedError("Stub — implemented in plan 02-03")

    def shutdown(self):
        """Graceful shutdown — quit Chrome driver."""
        raise NotImplementedError("Stub — implemented in plan 02-03")
