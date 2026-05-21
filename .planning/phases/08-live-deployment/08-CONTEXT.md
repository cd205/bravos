# Phase 8: Live Deployment - Context

**Gathered:** 2026-05-21
**Status:** Ready for planning

<domain>
## Phase Boundary

The full system (trading daemon + Gmail poller) runs as hardened systemd services that auto-restart on failure. The live IBKR account (port 4001) is connected and processing real orders. The deployment is resilient to IB Gateway's nightly restart and Chrome memory accumulation. No new pipeline logic — this phase is operational hardening and live activation only.

</domain>

<decisions>
## Implementation Decisions

### Gateway Nightly Restart Handling
- **D-01:** Rely on the existing IBApp auto-reconnect logic (Phase 3) to handle the IB Gateway nightly restart window (~11:45pm–12:15am ET). No new systemd timer, no in-process pause loop. Phase 8 success criterion is to observe the system survive a real nightly restart without operator intervention — confirming the Phase 3 reconnect behavior in production conditions.

### Chrome Memory Management
- **D-02:** Scheduled nightly driver restart inside the daemon. The trading daemon reinitializes `BravosScraper` / `WebDriver` once nightly (target ~1am ET, after the Gateway restart window closes). The daemon process itself stays alive — only the Chrome driver is recycled. This prevents memory accumulation without interrupting mid-trading-day sessions.
- **D-03:** Timing: 1am ET is after the Gateway window (~12:15am) and well before market open (9:30am), so no scrape cycle is disrupted.

### Live Account Cutover
- **D-04:** Cutover is a simple env var flip: set `TRADING_MODE=live` in the systemd service environment (or `.env` file) and restart the trading service. No formal checklist doc, no cutover script. Phase 6 paper validation is the sufficient precondition.
- **D-05:** The live IBKR account connects on port 4001. `get_ibkr_port()` in `settings.py` already returns 4001 when `TRADING_MODE=live` — no code change needed.

### Systemd Services
- **D-06:** Two new systemd service units required: `bravos-trading.service` (runs `scripts/run_ingestion.py`) and `bravos-gmail.service` (runs the Gmail poller process). Both follow the existing pattern: `Type=simple`, `Restart=always`, `RestartSec=15`, `User=chris_s_dodd`, `StandardOutput=journal`.
- **D-07:** Service dependencies: both `bravos-trading.service` and `bravos-gmail.service` declare `After=ibgateway.service` and `After=cloud-sql-proxy.service` so they start after their dependencies are up.
- **D-08:** Environment variables (secrets, `TRADING_MODE`, `ALERT_EMAIL`, risk params) are injected via `EnvironmentFile=/etc/bravos/env` in the service unit — not hardcoded in the unit file. The env file is root-readable only (mode 600). This mirrors the GCP Secret Manager pattern but allows systemd to manage environment injection.

### Claude's Discretion
- Whether the nightly Chrome restart is triggered by a `schedule` job in `run_ingestion.py` or by a separate systemd timer that sends SIGUSR1 to the process
- Exact `MemoryMax` / `MemoryHigh` settings if added as a belt-and-suspenders safeguard alongside the nightly restart
- Whether `bravos-trading.service` and `bravos-gmail.service` are placed in `/etc/systemd/system/` directly or templated under `infra/` and symlinked

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Existing systemd service files (patterns to follow)
- `infra/ibgateway.service` — `Restart=always`, `RestartSec=15`, `User=chris_s_dodd`, `After=network.target xvfb@99.service` — direct pattern for new trading services
- `infra/cloud-sql-proxy.service` — `Restart=always`, `RestartSec=5` — pattern for dependency ordering
- `infra/xvfb@.service` — template service pattern

### Daemon entry point
- `scripts/run_ingestion.py` — the script `bravos-trading.service` runs; where the nightly Chrome driver restart logic is added (schedule job at 1am ET)

### Configuration / secrets
- `bravos/config/settings.py` — `TRADING_MODE`, `get_ibkr_port()`, all env-var-backed settings; `TRADING_MODE=live` is the cutover toggle
- `bravos/config/secrets_config.py` — GCP Secret Manager `get_secret()` pattern; referenced for understanding how credentials are loaded at runtime

### IBKR reconnect (Phase 3)
- `bravos/broker/connection.py` — IBApp auto-reconnect logic; heartbeat monitor; this is what Phase 8 relies on to survive the Gateway nightly restart

### Phase requirements
- `.planning/REQUIREMENTS.md` — DEPL-02: "Trading process and dashboard run as separate systemd services with auto-restart on failure" (note: dashboard was cut; interpret as trading daemon + Gmail poller)

### Phase 6 validation artifacts (cutover precondition)
- `validation/VALIDATION-REPORT.md` — paper trading pass/fail record; sufficient precondition for live cutover

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `infra/ibgateway.service`: copy as base template for `bravos-trading.service` and `bravos-gmail.service` — unit structure is already correct
- `bravos/ingestion/scraper.py` `BravosScraper` class: the nightly restart reinitializes this (calls `scraper.quit()` then creates a new `BravosScraper()`); the class already supports this lifecycle
- `schedule` library already imported in `run_ingestion.py`: add a `schedule.every().day.at("01:00")` job for the driver restart alongside existing cycle jobs

### Established Patterns
- All credentials via `get_secret()` or `os.environ.get()` — `EnvironmentFile=` in systemd passes them as env vars, satisfying this constraint
- `Restart=always` + `RestartSec=15` is the established pattern for all persistent services on bravos-vm1

### Integration Points
- `run_ingestion.py`: add nightly Chrome driver restart as a `schedule` job; add graceful `scraper.quit()` + reinit sequence
- New `infra/bravos-trading.service` and `infra/bravos-gmail.service` unit files
- New `infra/env.example` documenting required env vars (`TRADING_MODE`, `ALERT_EMAIL`, `BRAVOS_DB_PASSWORD`, risk params) for the `/etc/bravos/env` EnvironmentFile

</code_context>

<specifics>
## Specific Ideas

- Live cutover: set `TRADING_MODE=live` in `/etc/bravos/env`, then `sudo systemctl restart bravos-trading.service`. No ceremony beyond that — Phase 6 validation is the gate.
- Nightly Chrome restart target time: 1am ET. After Gateway restart window (~12:15am), before pre-market (4am ET). Daemon stays alive; only `BravosScraper.__init__` is re-called (which creates a new Chrome driver and logs in).

</specifics>

<deferred>
## Deferred Ideas

- Automated daily validation run via cron — mentioned in Phase 6 deferred; still out of scope for Phase 8
- `MemoryMax` systemd resource limit as belt-and-suspenders — Claude's discretion; not a user requirement
- Formal CUTOVER.md checklist — user chose to skip; not needed
- v2 requirements (NOTF-V2, EXEC-V2, DASH-V2) — out of scope for this milestone

</deferred>

---

*Phase: 08-live-deployment*
*Context gathered: 2026-05-21*
