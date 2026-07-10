#!/usr/bin/env python3
"""
Compare IBKR actual positions against the bravos_trading DB.

Usage (run on bravos-vm1 or locally with Cloud SQL Proxy active):

    python scripts/check_positions.py
    python scripts/check_positions.py --paper       # force paper port (4002)
    python scripts/check_positions.py --live        # force live port (4001)

Output:
    - Table of positions from IBKR
    - Table of open lots from position_lots
    - Table of latest broker_positions_snapshot
    - MATCH / MISMATCH / IBKR_ONLY / DB_ONLY verdict per ticker

Exit code: 0 if all positions match, 1 if any mismatch.
"""
import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

from bravos.config import settings


# ── Minimal IBKR client to fetch positions ──────────────────────────────────

class _PositionFetcher(EWrapper, EClient):
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, self)
        self.positions: list[dict] = []
        self._done = threading.Event()
        self._next_id: int | None = None
        self._id_event = threading.Event()

    def nextValidId(self, orderId: int):
        self._next_id = orderId
        self._id_event.set()

    def position(self, account: str, contract: Contract, position: float, avgCost: float):
        if position != 0:
            self.positions.append({
                "ticker": contract.symbol,
                "position": position,
                "avg_cost": avgCost,
                "account": account,
            })

    def positionEnd(self):
        self._done.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in (2104, 2106, 2107, 2108, 2158):
            print(f"  IBKR error {errorCode}: {errorString}", file=sys.stderr)


def fetch_ibkr_positions(port: int, timeout: float = 20) -> list[dict]:
    app = _PositionFetcher()
    app.connect("127.0.0.1", port, clientId=99)

    t = threading.Thread(target=app.run, daemon=True)
    t.start()

    if not app._id_event.wait(timeout):
        print("Timeout waiting for IBKR connection", file=sys.stderr)
        app.disconnect()
        return []

    app.reqPositions()

    if not app._done.wait(timeout):
        print("Timeout waiting for positionEnd — using partial data", file=sys.stderr)

    app.cancelPositions()
    time.sleep(0.2)
    app.disconnect()
    return app.positions


# ── DB queries ───────────────────────────────────────────────────────────────

def fetch_db_open_lots(conn) -> dict[str, float]:
    """Returns {ticker: total_open_quantity} from position_lots."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, SUM(quantity)
            FROM position_lots
            WHERE lot_closed_at IS NULL
            GROUP BY ticker
        """)
        return {row[0]: float(row[1]) for row in cur.fetchall()}


def fetch_latest_snapshot(conn) -> dict[str, dict]:
    """Returns latest broker_positions_snapshot row per ticker."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (ticker)
                ticker, position, avg_cost, snapshot_at
            FROM broker_positions_snapshot
            ORDER BY ticker, snapshot_at DESC
        """)
        return {
            row[0]: {"position": row[1], "avg_cost": row[2], "snapshot_at": row[3]}
            for row in cur.fetchall()
        }


# ── Comparison and display ────────────────────────────────────────────────────

def compare(ibkr: list[dict], db_lots: dict[str, float], snapshot: dict[str, dict]) -> int:
    ibkr_map = {p["ticker"]: p for p in ibkr}
    all_tickers = sorted(set(list(ibkr_map.keys()) + list(db_lots.keys())))

    print("\n── IBKR Actual Positions ─────────────────────────────────────────")
    if not ibkr:
        print("  (none)")
    else:
        print(f"  {'Ticker':<8} {'Pos':>8} {'AvgCost':>10}  Account")
        for p in sorted(ibkr, key=lambda x: x["ticker"]):
            print(f"  {p['ticker']:<8} {p['position']:>8.0f} {p['avg_cost']:>10.2f}  {p['account']}")

    print("\n── DB open lots (position_lots) ──────────────────────────────────")
    if not db_lots:
        print("  (none)")
    else:
        print(f"  {'Ticker':<8} {'Qty':>8}")
        for tk, qty in sorted(db_lots.items()):
            print(f"  {tk:<8} {qty:>8.0f}")

    print("\n── Latest broker_positions_snapshot ──────────────────────────────")
    if not snapshot:
        print("  (none)")
    else:
        print(f"  {'Ticker':<8} {'Pos':>8} {'AvgCost':>10}  Snapshot time")
        for tk, s in sorted(snapshot.items()):
            print(f"  {tk:<8} {s['position']:>8.0f} {s['avg_cost']:>10.2f}  {s['snapshot_at']}")

    print("\n── Reconciliation ────────────────────────────────────────────────")
    print(f"  {'Ticker':<8} {'IBKR':>8} {'DB lots':>8}  Status")
    mismatches = 0
    for ticker in all_tickers:
        ibkr_qty = ibkr_map[ticker]["position"] if ticker in ibkr_map else 0
        db_qty = db_lots.get(ticker, 0)
        if ibkr_qty == 0 and db_qty == 0:
            continue
        if ibkr_qty != 0 and db_qty == 0:
            status = "IBKR_ONLY (not tracked in DB)"
            mismatches += 1
        elif ibkr_qty == 0 and db_qty != 0:
            status = "DB_ONLY (not in IBKR — stale lot?)"
            mismatches += 1
        elif ibkr_qty == db_qty:
            status = "MATCH"
        else:
            status = f"MISMATCH"
            mismatches += 1
        print(f"  {ticker:<8} {ibkr_qty:>8.0f} {db_qty:>8.0f}  {status}")

    print()
    if mismatches == 0:
        print(f"Result: ALL MATCH ({len(all_tickers)} tickers)")
    else:
        print(f"Result: {mismatches} MISMATCH(ES) — manual review required")

    return mismatches


def main():
    parser = argparse.ArgumentParser(description="Compare IBKR positions against DB")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--paper", action="store_true", help="Use paper port 4002")
    group.add_argument("--live", action="store_true", help="Use live port 4001")
    args = parser.parse_args()

    if args.paper:
        port = 4002
    elif args.live:
        port = 4001
    else:
        port = settings.get_ibkr_port()

    mode = "paper" if port == 4002 else "live"
    print(f"Connecting to IBKR Gateway ({mode}) on port {port}...")
    ibkr_positions = fetch_ibkr_positions(port)
    print(f"Fetched {len(ibkr_positions)} positions from IBKR")

    pw = os.environ.get("BRAVOS_DB_PASSWORD", "")
    conn = psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=pw,
    )

    try:
        db_lots = fetch_db_open_lots(conn)
        snapshot = fetch_latest_snapshot(conn)
    finally:
        conn.close()

    mismatches = compare(ibkr_positions, db_lots, snapshot)
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
