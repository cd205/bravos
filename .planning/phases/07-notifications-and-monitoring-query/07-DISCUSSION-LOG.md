# Phase 7: Notifications and Monitoring Query - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in 07-CONTEXT.md — this log preserves the discussion.

**Date:** 2026-05-20
**Phase:** 07-notifications-and-monitoring-query
**Mode:** discuss (default)
**Areas discussed:** Email trigger points, SMTP / email setup, Monitoring query scope

---

## Area 1: Email trigger points

**Question:** Which critical errors should trigger an email for NOTF-02?
- Options: Circuit breaker + IBKR disconnect only / All three (add parse failure spike)
- **Selected:** All three — add parse failure spike too

**Question:** For parse failure spike: what threshold?
- Options: 3 failures in last 10 signals / 5 consecutive failures
- **Selected:** 3 failures in last 10 signals (30% rolling window)

---

## Area 2: SMTP / email setup

**Question:** Which email approach?
- Options: smtplib via Gmail SMTP / SendGrid API
- **Selected:** smtplib via Gmail SMTP (stdlib, no new packages)

**Question:** Where does the recipient address come from?
- Options: Environment variable ALERT_EMAIL / GCP Secret Manager
- **Selected:** Environment variable ALERT_EMAIL

---

## Area 3: Monitoring query scope

**Question:** What should the monitoring query show?
- Options: All signals with execution info / Only signals that generated orders
- **Selected:** All signals with their best execution info (LEFT JOIN approach)

**Question:** Where does the query live?
- Options: queries/monitor.sql in repo / README/docs only
- **Selected:** queries/monitor.sql committed to the repo

---

## Summary of decisions

| Area | Decision |
|------|----------|
| Email triggers | Circuit breaker + IBKR disconnect + parse failure spike (3/10 rolling window) |
| SMTP | smtplib + Gmail, app password in Secret Manager |
| Recipient | ALERT_EMAIL env var |
| Email body | Plain-text, `[Bravos Alert]` subject prefix |
| Query scope | All signals, LEFT JOIN to orders/executions/lots |
| Query location | queries/monitor.sql |

## Deferred ideas
- NOTF-V2-01/02 (fill/signal emails) — v2 scope
- Dashboard entirely cut by decision 2026-05-20
