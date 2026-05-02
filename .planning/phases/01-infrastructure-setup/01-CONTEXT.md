# Phase 1: Infrastructure Setup - Context

**Gathered:** 2026-05-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Provision bravos_vm1 on GCP to be a fully operational execution surface: IB Gateway running via IBC, Python 3.11 venv ready, Chromium in headless mode, PostgreSQL installed with the Bravos trading schema, and all secrets loaded from GCP Secret Manager. This phase delivers the infrastructure that every subsequent phase depends on. No application code is written here.

</domain>

<decisions>
## Implementation Decisions

### VM Specification
- **D-01:** Machine type: `e2-standard-2` (2 vCPU, 8GB RAM) — stepped down from opt-trade-vm4's e2-standard-4 to reduce cost; sufficient for Chrome + Python + PostgreSQL + IB Gateway
- **D-02:** Boot disk: 50GB SSD (standard for this workload)
- **D-03:** Region: US East (us-east1 or us-east4) — lowest latency to NYSE/IBKR servers
- **D-04:** OS: Ubuntu LTS (mirror opt-trade-vm4's OS)

### IB Gateway Management
- **D-05:** Use IBC to manage IB Gateway startup and lifecycle — same tool as opt-trade-vm4
- **D-06:** 2FA handling: mirror opt-trade-vm4's exact approach (mobile notification approval); investigate and document the exact mechanism during implementation. The goal is maximum automation with minimum operator intervention — operator approves mobile 2FA once at Gateway startup; IBC handles everything else
- **D-07:** IB Gateway must accept connections on its configured port (paper: 4002, live: 4001) after startup — verified as a success criterion

### PostgreSQL
- **D-08:** PostgreSQL runs on the VM itself (same pattern as opt-trade-vm4) — no Cloud SQL
- **D-09:** Separate database and schema from opt-trade-vm4's database — bravos_vm1 has its own isolated DB
- **D-10:** Trading schema tables: `signals`, `orders`, `position_lots`, `executions`, `broker_positions_snapshot` (plus any audit/logging tables)
- **D-11:** Backup strategy: Claude's discretion (automated pg_dump to GCS bucket is the standard approach)

### Python Environment
- **D-12:** Python 3.11 (not 3.12) — stable, well-tested ibapi and Selenium compatibility
- **D-13:** Virtual environment (`venv`) for isolation
- **D-14:** Dependencies managed via `requirements.txt` with pinned versions
- **D-15:** ibapi installed from IB's official developer portal zip (not PyPI) — version must match the IB Gateway version installed

### Secrets Management
- **D-16:** Secrets stored in GCP Secret Manager — Bravos Research credentials (username/password), IBKR account config (port, account ID, client ID)
- **D-17:** VM accesses secrets via service account — no secrets on disk, no secrets in version control
- **D-18:** Startup validation: system checks all required secrets are readable before marking setup complete

### Chromium / Selenium
- **D-19:** Chromium installed system-wide (apt), not Chrome — better for headless Linux VMs
- **D-20:** Anti-detection flags applied at Chrome startup (from selenium-scraper skill patterns)
- **D-21:** Success criterion: a test script can open a headless browser session without errors

### Claude's Discretion
- Exact Ubuntu LTS version (20.04 or 22.04 — match opt-trade-vm4)
- pg_dump backup schedule and GCS bucket naming
- Firewall/VPC configuration details
- ChromeDriver version management approach (webdriver-manager vs pinned)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### IBKR Connection Patterns
- `.claude/skills/ibkr-connection/SKILL.md` — ibapi architecture, IB Gateway setup, port configuration, connection best practices

### Selenium / Chrome Setup
- `.claude/skills/selenium-scraper/SKILL.md` — Chrome anti-detection flags, WebDriver setup patterns, headless configuration

### Database Schema
- `.claude/skills/postgres-patterns/SKILL.md` — PostgreSQL schema design, indexing, security patterns
- `.claude/skills/trade-database-review/` — Trading data schema design (signals, orders, positions, executions)

### Project Requirements
- `.planning/REQUIREMENTS.md` — DEPL-01, DEPL-03, DEPL-04, DEPL-05 (the four requirements this phase delivers)
- `.planning/research/STACK.md` — Recommended stack versions and rationale
- `.planning/research/ARCHITECTURE.md` — Component boundaries and deployment architecture
- `.planning/research/PITFALLS.md` — P2 (ChromeDriver mismatch), P13 (Gateway nightly restart), P14 (Chrome memory), P15 (no alerting)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield project, no existing application code

### Established Patterns
- opt-trade-vm4 is the reference implementation: same GCP project, same IBC setup, same IB Gateway version. Implementation should investigate and mirror it before diverging.

### Integration Points
- Phase 2 (Signal Ingestion) requires: PostgreSQL running with schema applied, Chromium headless working, secrets readable from VM
- Phase 3 (IBKR Connection) requires: IB Gateway reachable on configured port, Python venv with ibapi installed

</code_context>

<specifics>
## Specific Ideas

- opt-trade-vm4 (e2-standard-4, same GCP project) is the reference — investigate its exact setup (OS version, IBC config, PostgreSQL install method, Gateway version) before building bravos_vm1
- bravos_vm1 must be a separate, independent VM — shared nothing with opt-trade-vm4 except the GCP project and general setup pattern

</specifics>

<deferred>
## Deferred Ideas

- None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-infrastructure-setup*
*Context gathered: 2026-05-02*
