# Phase 6 Validation Report

**Status:** PENDING — populated by the Plan 03 live validation run
**Environment:** bravos-vm1, paper account (TRADING_MODE=paper, port 4002) — D-09

## Success Criteria

| SC | Criterion | Result | Evidence |
|----|-----------|--------|----------|
| SC-1 | At least 10 real Bravos Trade Alert posts processed end-to-end | PENDING | |
| SC-2 | No order reaches IBKR with wrong ticker, action type, or quantity (all 4 action types represented — D-02) | PENDING | |
| SC-3 | All parser edge cases discovered during validation fixed and re-tested | PENDING | |
| SC-4 | No critical system failure during a full trading day (INGST-07 session recovery, IBKR-02 heartbeat recovery, IBKR-04 periodic reconciliation) | PENDING | |

## Per-Scenario Results

| URL | Expected Ticker | Expected Action | Result | Detail |
|-----|-----------------|-----------------|--------|--------|
| _populated by scripts/validate_pipeline.py output during Plan 03_ | | | | |

## Live Observation Period (D-03)

Daemon left running post-seeded-batch to validate timing-sensitive behaviors:
- INGST-07 session expiry auto-recovery: PENDING
- IBKR-02 heartbeat failure auto-recovery: PENDING
- IBKR-04 periodic reconciliation runs cleanly each cycle: PENDING
