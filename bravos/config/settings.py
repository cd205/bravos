"""Bravos Trading System — Configuration Settings"""
import os

# Database
DB_HOST = os.environ.get("BRAVOS_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("BRAVOS_DB_PORT", "5432"))
DB_NAME = os.environ.get("BRAVOS_DB_NAME", "bravos_trading")
DB_USER = os.environ.get("BRAVOS_DB_USER", "bravos")

# IBKR
IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PAPER_PORT = 4002
IBKR_LIVE_PORT = 4001
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "1"))

# Scraping
SCRAPE_INTERVAL_SECONDS = 300  # 5 minutes per project constraint
BRAVOS_BASE_URL = "https://bravosresearch.com"

# Trading
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")  # "paper" or "live"

# Scraping — URLs and selectors (confirmed via selector discovery 2026-05-08)
LOGIN_URL = f"{BRAVOS_BASE_URL}/my-account/"
# Post body selector confirmed against live post page
POST_BODY_SELECTOR = ".entry-content"
# WooCommerce login: use ID selectors (page has duplicate name= fields; first is hidden)
LOGIN_USERNAME_ID = "username"
LOGIN_PASSWORD_ID = "password"
LOGIN_SUBMIT_XPATH = "//button[@type='submit'] | //input[@type='submit']"
# Gmail poller filter
GMAIL_SENDER_FILTER = "from:bravosresearch.com"
GMAIL_SUBJECT_KEYWORD = "New Trade Alert"
# Scraping limits
POSTS_PER_CYCLE = 10
MAX_REAUTH_ATTEMPTS = 3


def get_ibkr_port() -> int:
    return IBKR_LIVE_PORT if TRADING_MODE == "live" else IBKR_PAPER_PORT
