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

# Scraping — category and selectors
TRADE_ALERT_CATEGORY_URL = f"{BRAVOS_BASE_URL}/category/portfolio-update/"
LOGIN_URL = f"{BRAVOS_BASE_URL}/my-account/"
# WordPress selectors — confirmed via selector discovery or updated at first run
ARTICLE_SELECTOR = "article"
ARTICLE_LINK_SELECTOR = "h2 a, h1 a"
POST_BODY_SELECTOR = ".entry-content, .post-content, article .content"
# WordPress login field names
LOGIN_USERNAME_FIELD = "log"
LOGIN_PASSWORD_FIELD = "pwd"
LOGIN_SUBMIT_XPATH = "//button[@type='submit'] | //input[@type='submit']"
# Scraping limits
POSTS_PER_CYCLE = 10
MAX_REAUTH_ATTEMPTS = 3


def get_ibkr_port() -> int:
    return IBKR_LIVE_PORT if TRADING_MODE == "live" else IBKR_PAPER_PORT
