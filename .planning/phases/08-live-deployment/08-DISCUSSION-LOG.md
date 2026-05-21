# Phase 8: Live Deployment - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the discussion.

**Date:** 2026-05-21
**Phase:** 08-live-deployment
**Mode:** discuss (default)
**Areas analyzed:** Gateway nightly restart handling, Chrome memory management, Live account cutover sequence

---

## Gray Areas Presented

The following areas were identified from phase analysis. The user selected 3 of 4 for discussion.

| Area | Status |
|------|--------|
| Service layout | Not selected |
| Gateway nightly restart handling | Selected |
| Chrome memory management | Selected |
| Live account cutover sequence | Selected |

---

## Discussion: Gateway Nightly Restart Handling

**Question:** IB Gateway restarts nightly (~11:45pm–12:15am ET). What additional handling beyond the existing auto-reconnect logic?

**Options presented:**
- Rely on existing reconnect (Recommended)
- Add a systemd timer to pause/restart trading service
- Add a pause timer inside the daemon

**User selection:** Rely on existing reconnect

**Decision captured:** D-01 — No new code; Phase 3 auto-reconnect is sufficient. Phase 8 validates this in production.

---

## Discussion: Chrome Memory Management

**Question:** Chrome/Selenium accumulates memory over days. How should Phase 8 address this?

**Options presented:**
- Scheduled nightly driver restart (Recommended)
- Rely on systemd restart + memory limit
- Skip for now — monitor first

**User selection:** Scheduled nightly driver restart

**Decision captured:** D-02/D-03 — Nightly `BravosScraper` reinitialization at 1am ET via `schedule` job. Daemon stays alive.

---

## Discussion: Live Account Cutover Sequence

**Question:** What must be true before flipping TRADING_MODE=live, and how structured?

**Options presented:**
- Operator checklist in a doc + env var flip (Recommended)
- Cutover script that validates preconditions
- Just flip the env var — no formal checklist

**User selection:** Just flip the env var — no formal checklist

**Decision captured:** D-04/D-05 — Set `TRADING_MODE=live` in `/etc/bravos/env`, restart service. Phase 6 validation is the sufficient gate.

---

## Claude's Discretion Items

- Whether the nightly Chrome restart is a `schedule` job or a systemd timer with SIGUSR1
- `MemoryMax` / `MemoryHigh` optional safeguards
- Whether service files live directly in `/etc/systemd/system/` or under `infra/` symlinked

## Deferred Ideas

- Automated daily validation via cron (from Phase 6 deferred)
- Formal CUTOVER.md checklist (user chose to skip)
- v2 requirements (NOTF-V2, EXEC-V2, DASH-V2)
