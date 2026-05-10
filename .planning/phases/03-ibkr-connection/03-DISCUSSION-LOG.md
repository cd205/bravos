# Phase 3: IBKR Connection - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-10
**Phase:** 03-ibkr-connection
**Areas discussed:** Connection architecture, Reconnect strategy, Startup reconciliation, Thread/process model

---

## Connection Architecture

| Option | Description | Selected |
|--------|-------------|----------|
| Pattern B: Combined class | Single IBApp(EWrapper, EClient) — simpler, fewer files | ✓ |
| Pattern A: Separate classes | IBWrapper + IBClient + IBApp — more separation, more boilerplate | |
| Mirror opt-trade-vm4 exactly | Replicate verbatim from reference implementation | |

**User's choice:** Pattern B: Combined class
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| bravos/broker/ package | New package alongside bravos/ingestion/ | ✓ |
| bravos/ibkr/ package | More explicit name | |
| bravos/connection.py | Single module | |

**User's choice:** bravos/broker/ package
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| Singleton module-level instance | One instance in __init__.py, imported directly | |
| Dependency injection | Instance passed as argument | |
| You decide | Claude picks cleaner pattern | ✓ |

**User's choice:** Claude's discretion
**Notes:** —

---

## Reconnect Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Heartbeat timeout | reqCurrentTime every 60s, 10s timeout | |
| Error code watch | Watch 504, 1100, 2110 codes | |
| Both: heartbeat + error codes | Most robust | ✓ |

**User's choice:** Both mechanisms
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| 10 seconds | Plenty of slack, fast detection | ✓ |
| 30 seconds | More conservative | |
| You decide | Claude picks | |

**User's choice:** 10 seconds
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| Disconnect, wait 5s, reconnect | Matches opt-trade-vm4 pattern | ✓ |
| Kill socket immediately | Faster but riskier | |
| Restart entire daemon | Clean slate, slower | |

**User's choice:** Disconnect → wait 5s → reconnect
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| 3 attempts, log CRITICAL | Consistent with Phase 2 re-auth | |
| 5 attempts, exponential backoff | More persistent, 5/10/20/40/80s | ✓ |
| Unlimited retries | Never give up | |

**User's choice:** 5 attempts, exponential backoff
**Notes:** —

---

## Startup Reconciliation

| Option | Description | Selected |
|--------|-------------|----------|
| IBKR authoritative — overwrite DB | Simplest, IBKR always wins | |
| Flag discrepancies, don't overwrite | Safer, operator reviews | ✓ |
| Block startup until resolved | Maximally safe but could block operation | |

**User's choice:** Flag discrepancies, log WARNING, don't overwrite DB
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| Positions + open orders | reqPositions + reqOpenOrders | |
| Positions only | Simpler | |
| Positions + orders + account summary | Most complete | ✓ |

**User's choice:** All three — positions, open orders, account summary
**Notes:** Account net liquidation at startup useful for Phase 4 order sizing

---

| Option | Description | Selected |
|--------|-------------|----------|
| broker_positions_snapshot table | Already in schema, auditable | ✓ |
| In-memory only | No audit trail | |
| Both: table + in-memory | Audit + runtime access | |

**User's choice:** broker_positions_snapshot table
**Notes:** —

---

## Thread / Process Model

| Option | Description | Selected |
|--------|-------------|----------|
| Same process, separate threads | Shared state, no IPC | ✓ |
| Separate process, same systemd unit | Isolated, communicate via DB | |
| Separate process, different systemd unit | Fully independent | |

**User's choice:** Same process, separate threads inside run_ingestion.py
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| At daemon startup, before schedule loop | Clean ordering, no scrape until IBKR confirmed | ✓ |
| Lazily, on first alert | Saves resources, adds first-trade latency | |
| Separate startup command | Explicit control, more operational complexity | |

**User's choice:** At daemon startup, before schedule loop
**Notes:** —

---

| Option | Description | Selected |
|--------|-------------|----------|
| Log CRITICAL, continue ingestion only | System doesn't go dark | |
| Exit with error code | Hard fail, systemd restarts | |
| Keep retrying in background indefinitely | Never exit, signals still stored | ✓ |

**User's choice:** Keep retrying in background indefinitely; ingestion runs; orders blocked until connected
**Notes:** —

---

## Claude's Discretion

- IBApp exposure pattern (singleton vs DI) — pick what's cleanest for Phase 4 compatibility
- Heartbeat implementation details (threading.Timer vs dedicated thread)
- Error code handling granularity
- Test approach for connection logic

## Deferred Ideas

- Periodic reconciliation during trading day (IBKR-04) — Phase 5
- Account summary streaming for real-time P&L — Phase 7
- Email notification on unrecovered disconnect — Phase 7
