"""
bravos/risk/gate.py — Single synchronous risk gate for all order paths (Phase 4).

Per D-02: Every order path calls RiskGate.check() and only this function.
Per D-04 / RISK-04: Every decision (pass or block) is logged to risk_gate_log.

Gate sequence (D-03):
  1. Market hours (09:30–16:00 ET, Mon–Fri) — EXEC-03
  2. Max open positions count — RISK-01
  3. Max allocation per trade as % of NLV — RISK-02
  4. Daily loss circuit breaker — RISK-03

Gate failure short-circuits remaining gates (first failure wins).
"""
import datetime
import logging
from zoneinfo import ZoneInfo

from bravos.config.settings import (
    MAX_OPEN_POSITIONS,
    MAX_ALLOCATION_PCT,
    DAILY_LOSS_THRESHOLD,
    WEIGHT_PCT_PER_UNIT,
)

logger = logging.getLogger(__name__)

_EASTERN = ZoneInfo("America/New_York")


def _is_market_hours() -> bool:
    """Return True iff current ET time is within NYSE regular hours (09:30–16:00 Mon–Fri).

    Uses stdlib zoneinfo (NOT pytz — pytz is not installed; see RESEARCH Pitfall 5).
    Saturday (weekday()==5) and Sunday (weekday()==6) return False unconditionally.
    """
    now = datetime.datetime.now(tz=_EASTERN)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now < market_close


class RiskGate:
    """
    Single risk gate for the Bravos order path.

    Stateful: latches _circuit_tripped for the trading day once daily P&L
    crosses DAILY_LOSS_THRESHOLD. Reset by calling reset() at start of trading day.
    """

    def __init__(self) -> None:
        self._circuit_tripped: bool = False

    def check(self, signal_id: int, db_conn, ibapp) -> tuple[bool, str]:
        """
        Run four gates in sequence. Log decision to risk_gate_log.

        Returns:
            (True, "pass") when all gates pass.
            (False, reason) when any gate blocks. `reason` contains a substring
            identifying which gate fired: 'market_hours', 'max_positions',
            'max_allocation', or 'circuit_breaker'.
        """
        signal = self._load_signal(signal_id, db_conn)

        open_positions = self._count_open_positions(db_conn)
        delta_weight = abs((signal["weight_to"] or 0) - (signal["weight_from"] or 0))
        alloc_pct = delta_weight * WEIGHT_PCT_PER_UNIT
        nlv = self._read_nlv(ibapp)
        daily_pnl = getattr(ibapp, "_daily_pnl", None) if ibapp is not None else None

        computed = {
            "open_positions": open_positions,
            "max_positions": MAX_OPEN_POSITIONS,
            "order_allocation_pct": alloc_pct,
            "max_allocation_pct": MAX_ALLOCATION_PCT,
            "net_liquidation": nlv,
            "daily_pnl": daily_pnl,
            "daily_pnl_threshold": DAILY_LOSS_THRESHOLD,
        }

        # Gate 1: Market hours (EXEC-03)
        if not _is_market_hours():
            return self._log_and_return(False, "market_hours: outside 09:30-16:00 ET",
                                        signal_id, computed, db_conn)

        # Gate 2: Max open positions (RISK-01) — entries only
        if signal["action_type"] in ("open", "add") and open_positions >= MAX_OPEN_POSITIONS:
            return self._log_and_return(
                False,
                f"max_positions: {open_positions}/{MAX_OPEN_POSITIONS}",
                signal_id, computed, db_conn,
            )

        # Gate 3: Max allocation per trade (RISK-02)
        if alloc_pct > MAX_ALLOCATION_PCT:
            return self._log_and_return(
                False,
                f"max_allocation: {alloc_pct:.4f} > {MAX_ALLOCATION_PCT:.4f}",
                signal_id, computed, db_conn,
            )

        # Gate 4: Daily loss circuit breaker (RISK-03)
        if not self._circuit_tripped and daily_pnl is not None and daily_pnl < DAILY_LOSS_THRESHOLD:
            self._circuit_tripped = True
            logger.critical(
                "Circuit breaker TRIPPED: daily_pnl=%.2f < threshold=%.2f",
                daily_pnl, DAILY_LOSS_THRESHOLD,
            )
            # Phase 7: email alert (D-01, NOTF-01) — deferred import avoids circular dep
            try:
                from bravos.notifications.notifier import send_alert
                send_alert(
                    "Circuit Breaker Triggered",
                    f"Daily P&L circuit breaker triggered at {datetime.datetime.now().isoformat()}\n"
                    f"daily_pnl={daily_pnl:.2f}  threshold={DAILY_LOSS_THRESHOLD:.2f}\n"
                    f"No new orders will be placed for the remainder of the trading day.",
                )
            except Exception:
                logger.warning("Failed to send circuit breaker alert", exc_info=True)
        if self._circuit_tripped:
            return self._log_and_return(
                False,
                "circuit_breaker: daily loss threshold crossed",
                signal_id, computed, db_conn,
            )

        return self._log_and_return(True, "pass", signal_id, computed, db_conn)

    def reset(self) -> None:
        """Reset latched circuit breaker. Call at start of trading day."""
        self._circuit_tripped = False
        logger.info("RiskGate._circuit_tripped reset to False")

    # ── Internals ──────────────────────────────────────────────────────────

    @staticmethod
    def _load_signal(signal_id: int, db_conn) -> dict:
        """Load the signal fields needed by the gate. Returns dict."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, action_type, weight_from, weight_to "
                "FROM signals WHERE id = %s",
                (signal_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"signal_id={signal_id} not found in signals table")
        if isinstance(row, dict):
            return row
        return {
            "ticker": row[0],
            "action_type": row[1],
            "weight_from": row[2],
            "weight_to": row[3],
        }

    @staticmethod
    def _count_open_positions(db_conn) -> int:
        """Count distinct tickers with at least one open lot (RISK-01)."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT ticker) FROM position_lots "
                "WHERE lot_closed_at IS NULL"
            )
            row = cur.fetchone()
        if row is None:
            return 0
        return int(row[0]) if row[0] is not None else 0

    @staticmethod
    def _read_nlv(ibapp):
        """Read NetLiquidation from ibapp._account_summary, cast string to float."""
        if ibapp is None:
            return None
        summary = getattr(ibapp, "_account_summary", None) or {}
        raw = summary.get("NetLiquidation")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("NetLiquidation is not float-castable: %r", raw)
            return None

    @staticmethod
    def _log_and_return(passed, reason, signal_id, computed, db_conn):
        """Write one row to risk_gate_log and return (passed, reason)."""
        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk_gate_log
                  (signal_id, gate_passed, reason, open_positions, max_positions,
                   order_allocation_pct, max_allocation_pct,
                   net_liquidation, daily_pnl, daily_pnl_threshold)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    signal_id,
                    passed,
                    reason,
                    computed.get("open_positions"),
                    computed.get("max_positions"),
                    computed.get("order_allocation_pct"),
                    computed.get("max_allocation_pct"),
                    computed.get("net_liquidation"),
                    computed.get("daily_pnl"),
                    computed.get("daily_pnl_threshold"),
                ),
            )
        db_conn.commit()
        log_level = logger.info if passed else logger.warning
        log_level(
            "RiskGate signal_id=%s passed=%s reason=%s open_positions=%s alloc_pct=%s daily_pnl=%s",
            signal_id, passed, reason,
            computed.get("open_positions"),
            computed.get("order_allocation_pct"),
            computed.get("daily_pnl"),
        )
        return passed, reason
