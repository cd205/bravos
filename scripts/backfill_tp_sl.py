"""
Backfill stop_loss and take_profits for existing signals that were scraped before Phase 9.

Reads raw_html from the DB, strips tags to recover body text, re-runs the Phase 9
parser regexes, and UPDATEs only rows where extraction succeeds.

Safe to re-run: skips signals that already have stop_loss populated, and only
writes when at least one of stop_loss or take_profits is non-empty.
"""

import os
import re
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bravos.db import get_connection
from bravos.ingestion.parser import STOP_LOSS_RE, TAKE_PROFIT_RE, PRICE_IN_LIST_RE

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    text = TAG_RE.sub(" ", html)
    return SPACE_RE.sub(" ", text).strip()


def extract_tp_sl(text: str):
    sl_match = STOP_LOSS_RE.search(text)
    stop_loss = None
    if sl_match:
        raw = sl_match.group(1) or sl_match.group(2)
        try:
            stop_loss = float(raw.replace(",", ""))
        except (TypeError, ValueError):
            pass

    tp_match = TAKE_PROFIT_RE.search(text)
    take_profits = []
    if tp_match:
        tp_string = tp_match.group(1) or tp_match.group(2)
        try:
            take_profits = [float(p.replace(",", "")) for p in PRICE_IN_LIST_RE.findall(tp_string)]
        except (TypeError, ValueError):
            pass

    return stop_loss, take_profits


def main():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, ticker, action_type, raw_html
        FROM signals
        WHERE action_type IN ('open', 'add')
          AND stop_loss IS NULL
          AND take_profits = '{}'
          AND raw_html IS NOT NULL AND raw_html != ''
          AND post_title != 'test'
          AND ticker NOT IN ('TEST', 'BADTKR', 'AAPL')
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    log.info("Found %d signals eligible for backfill", len(rows))

    updated = 0
    skipped = 0
    for signal_id, ticker, action_type, raw_html in rows:
        text = html_to_text(raw_html)
        stop_loss, take_profits = extract_tp_sl(text)

        if stop_loss is None and not take_profits:
            log.info("  SKIP  signal_id=%-6s %-6s — no TP/SL found in body", signal_id, ticker)
            skipped += 1
            continue

        cur.execute(
            "UPDATE signals SET stop_loss = %s, take_profits = %s WHERE id = %s",
            (stop_loss, take_profits, signal_id),
        )
        log.info("  UPDATE signal_id=%-6s %-6s — SL=%s TP=%s", signal_id, ticker, stop_loss, take_profits)
        updated += 1

    conn.commit()
    log.info("Done: %d updated, %d skipped (no TP/SL in alert body)", updated, skipped)


if __name__ == "__main__":
    main()
