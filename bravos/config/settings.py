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

# Risk controls — configurable per deployment (Phase 4)
MAX_OPEN_POSITIONS   = int(os.environ.get("MAX_OPEN_POSITIONS", "20"))           # max concurrent open positions (RISK-01)
MAX_ALLOCATION_PCT   = float(os.environ.get("MAX_ALLOCATION_PCT", "0.25"))       # 25% max per trade as fraction of NLV (RISK-02)
DAILY_LOSS_THRESHOLD = float(os.environ.get("DAILY_LOSS_THRESHOLD", "-5000.0"))  # -$5,000 circuit breaker (RISK-03)
WEIGHT_PCT_PER_UNIT  = float(os.environ.get("WEIGHT_PCT_PER_UNIT", "0.05"))      # 5% NLV per weight unit (EXEC-01)

# Notifications (Phase 7)
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")

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
