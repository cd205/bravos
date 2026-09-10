"""bravos/dashboard/app.py — Alert verification dashboard routes (Issue #1).

Two routes:
  GET /                    — table of recent signals, most-recent-first,
                              filterable by ?start=YYYY-MM-DD&end=YYYY-MM-DD
  GET /alerts/{signal_id}  — detail view: raw signal, risk-gate log, order
                              history (including bracket children)

Verification scope is presence/status only, sourced entirely from Postgres
via the existing reconciliation job's data — no live IBKR calls (see spec's
Out of Scope). No authentication — assumed to run on a trusted network/VM,
per spec.
"""
import datetime
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from bravos.dashboard.queries import DEFAULT_WINDOW_DAYS, fetch_signal_detail, fetch_signals
from bravos.db import get_connection

logger = logging.getLogger(__name__)

app = FastAPI(title="Bravos Alert Verification Dashboard")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _parse_date_param(value: str | None, param_name: str) -> datetime.date | None:
    if value is None:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid {param_name!r} date: {value!r} (expected YYYY-MM-DD)"
        )


@app.get("/", response_class=HTMLResponse)
def alert_table(request: Request, start: str | None = None, end: str | None = None):
    """Verification table: one row per signal, most-recent-first.

    Defaults to the last DEFAULT_WINDOW_DAYS days when no start/end given.
    end is treated as inclusive through end-of-day.
    """
    start_date = _parse_date_param(start, "start")
    end_date = _parse_date_param(end, "end")

    if start_date is None and end_date is None:
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=DEFAULT_WINDOW_DAYS)

    start_bound = (
        datetime.datetime.combine(start_date, datetime.time.min) if start_date else None
    )
    end_bound = (
        datetime.datetime.combine(end_date, datetime.time.max) if end_date else None
    )

    conn = get_connection()
    try:
        signals = fetch_signals(conn, start=start_bound, end=end_bound)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "alert_table.html",
        {
            "signals": signals,
            "start": start_date.isoformat() if start_date else "",
            "end": end_date.isoformat() if end_date else "",
        },
    )


@app.get("/alerts/{signal_id}", response_class=HTMLResponse)
def alert_detail(request: Request, signal_id: int):
    """Single-signal detail view: raw fields, risk-gate log, full order
    history (including bracket children)."""
    conn = get_connection()
    try:
        detail = fetch_signal_detail(conn, signal_id)
    finally:
        conn.close()

    if detail is None:
        raise HTTPException(status_code=404, detail=f"No signal with id={signal_id}")

    return templates.TemplateResponse(request, "alert_detail.html", detail)
