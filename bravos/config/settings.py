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


def get_ibkr_port() -> int:
    return IBKR_LIVE_PORT if TRADING_MODE == "live" else IBKR_PAPER_PORT
