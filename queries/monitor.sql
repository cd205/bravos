-- queries/monitor.sql — Bravos Trading System monitoring query (Phase 7)
--
-- Returns one row per signal showing full trade state: signal → gate → order → lots.
-- Run: psql -h 127.0.0.1 -U bravos -d bravos_trading -f queries/monitor.sql
--
-- gate_passed IS NULL  → signal was not risk-checked (low confidence or duplicate)
-- order_status IS NULL → signal was blocked before order creation
-- open_quantity / realized_pnl are per-ticker aggregates, not per-signal

WITH latest_gate AS (
    -- Most recent gate check per signal (D-13)
    -- DISTINCT ON keeps the first row after ORDER BY, so checked_at DESC = latest first
    SELECT DISTINCT ON (signal_id)
        signal_id,
        gate_passed,
        checked_at
    FROM risk_gate_log
    ORDER BY signal_id, checked_at DESC
),
latest_order AS (
    -- Most recent order per signal (CR-01: orders has no UNIQUE on signal_id;
    -- without this a signal with multiple orders would fan out to multiple rows)
    SELECT DISTINCT ON (signal_id)
        signal_id,
        status,
        fill_price
    FROM orders
    ORDER BY signal_id, created_at DESC
),
open_qty AS (
    -- Current open quantity per ticker (open lots only)
    SELECT ticker, SUM(quantity) AS open_quantity
    FROM position_lots
    WHERE lot_closed_at IS NULL
    GROUP BY ticker
),
realized AS (
    -- Realized P&L per ticker (closed lots only)
    SELECT ticker, SUM(pnl) AS realized_pnl
    FROM position_lots
    WHERE lot_closed_at IS NOT NULL
    GROUP BY ticker
)
SELECT
    s.id               AS signal_id,
    s.parsed_at,
    s.ticker,
    s.action_type,
    s.confidence,
    lg.gate_passed,
    lo.status          AS order_status,
    lo.fill_price,
    oq.open_quantity,
    r.realized_pnl
FROM signals s
LEFT JOIN latest_gate  lg ON lg.signal_id = s.id
LEFT JOIN latest_order lo ON lo.signal_id = s.id
LEFT JOIN open_qty     oq ON oq.ticker    = s.ticker
LEFT JOIN realized      r ON r.ticker     = s.ticker
ORDER BY s.parsed_at DESC;
