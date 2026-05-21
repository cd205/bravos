#!/usr/bin/env python3
"""
Bravos Trading System -- Gmail Poller Entry Point (PLACEHOLDER).

This script is a placeholder for the Gmail poller process (INGST-V2-01).
bravos-gmail.service points here so the systemd unit is registered and
auto-restarts are enabled, but no Gmail polling is implemented yet.

When INGST-V2-01 is implemented, this script will:
  1. Poll Gmail via IMAP using settings.GMAIL_SENDER_FILTER and GMAIL_SUBJECT_KEYWORD
  2. Extract post URLs from email bodies
  3. Call the scraper's alert handler for each new URL

For now: log the placeholder status once at startup, then sleep forever so
systemd Restart=always does not thrash.
"""
import sys
import time
import logging
from pathlib import Path

# Ensure the repo root is on sys.path when running as `python scripts/run_gmail.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Configure logging (mirrors scripts/run_ingestion.py lines 48-53)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.gmail.daemon")


def main():
    logger.info(
        "Gmail poller started (PLACEHOLDER -- INGST-V2-01 not yet implemented). "
        "Service is registered and will auto-restart. No email polling active."
    )
    # Keep process alive so systemd Restart=always does not thrash.
    # Sleep in 60s ticks (vs sleep forever) so journald sees the process is healthy.
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
