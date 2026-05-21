# Phase 8: Live Deployment - Research

**Researched:** 2026-05-21
**Domain:** systemd service hardening, nightly Chrome driver restart, live account cutover
**Confidence:** HIGH — all findings verified by direct codebase inspection; no speculative claims

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Rely on existing IBApp auto-reconnect (Phase 3) for Gateway nightly restart. No new code. Phase 8 success = observe system survive one real nightly restart.
- **D-02:** Nightly Chrome driver restart inside the daemon via `schedule` job at 1am ET. Daemon stays alive; only `BravosScraper` / `WebDriver` is recycled.
- **D-03:** Timing: 1am ET (after Gateway window ~12:15am, before market open 9:30am ET).
- **D-04:** Live cutover = set `TRADING_MODE=live` in `/etc/bravos/env`, restart service. Phase 6 validation is the sufficient gate. No checklist doc, no cutover script.
- **D-05:** Live IBKR account on port 4001. `get_ibkr_port()` already returns 4001 when `TRADING_MODE=live` — no code change needed.
- **D-06:** Two new systemd units: `bravos-trading.service` (runs `scripts/run_ingestion.py`) and `bravos-gmail.service` (runs the Gmail poller process). Both: `Type=simple`, `Restart=always`, `RestartSec=15`, `User=chris_s_dodd`, `StandardOutput=journal`.
- **D-07:** Both services declare `After=ibgateway.service` and `After=cloud-sql-proxy.service`.
- **D-08:** Env vars via `EnvironmentFile=/etc/bravos/env` (mode 600, root-readable only).

### Claude's Discretion

- Whether nightly Chrome restart is a `schedule` job in `run_ingestion.py` or a separate systemd timer sending SIGUSR1 to the process.
- Exact `MemoryMax` / `MemoryHigh` settings if added as belt-and-suspenders alongside nightly restart.
- Whether `bravos-trading.service` and `bravos-gmail.service` are placed in `/etc/systemd/system/` directly or templated under `infra/` and symlinked.

### Deferred Ideas (OUT OF SCOPE)

- Automated daily validation via cron (from Phase 6 deferred).
- Formal CUTOVER.md checklist (user chose to skip).
- `MemoryMax` systemd resource limit (Claude's discretion, not a user requirement).
- v2 requirements (NOTF-V2, EXEC-V2, DASH-V2).
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEPL-02 | Trading process and dashboard are managed as separate systemd services with auto-restart on failure | Two new unit files modeled on `infra/ibgateway.service`; dashboard was cut so the two services are trading daemon + Gmail poller |
</phase_requirements>

---

## Summary

Phase 8 is pure operational hardening — no new pipeline logic. Three deliverables: (1) a `schedule` job in `run_ingestion.py` that recycles the Chrome driver at 1am ET, (2) two new systemd unit files (`bravos-trading.service` and `bravos-gmail.service`), and (3) a one-line env var change to activate the live IBKR account. The existing IBApp auto-reconnect logic handles the Gateway nightly restart without any new code.

**Critical gap discovered:** The Gmail poller process (`bravos-gmail.service` needs to run something) does not exist in the codebase. There is no `scripts/run_gmail.py`, no `GmailPoller` class, and no Gmail API/IMAP client anywhere in `bravos/`. The architecture documentation in prior phases consistently refers to a Gmail-triggered flow where post URLs arrive via email, but only the consumer side (`BravosScraper.process_alert(url)`) was built. The producer side — the Gmail poller that reads email and calls `process_alert()` — was never implemented. Phase 8 must either create this script or clarify that `bravos-gmail.service` points to a placeholder/stub that logs "not yet implemented."

**Primary recommendation:** Implement Phase 8 as three tasks: (1) nightly Chrome restart schedule job in `run_ingestion.py`, (2) both systemd unit files + `infra/env.example`, (3) Gmail poller entry script (`scripts/run_gmail.py`) as a minimal stub that the service unit can point to, with a stub body that logs "Gmail poller not yet implemented — pending INGST-V2-01".

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Chrome memory management | Daemon process | — | Driver restart is in-process via `schedule`; no OS-level concern |
| Gateway nightly restart survival | Daemon process (IBApp thread) | — | Existing `_reconnect_loop` handles this; no systemd involvement |
| Service auto-restart on crash | OS / systemd | — | `Restart=always` in unit file |
| Env var injection | OS / systemd | — | `EnvironmentFile=/etc/bravos/env` |
| Service dependency ordering | OS / systemd | — | `After=` directives; ensures DB and Gateway are up first |
| Live account activation | Configuration (env file) | Daemon restart | `TRADING_MODE=live` in `/etc/bravos/env` → `get_ibkr_port()` returns 4001 |
| Gmail alert → scraper routing | Gmail poller process | BravosScraper | Poller calls `scraper.process_alert(url)` |

---

## Standard Stack

### Core (existing — no new packages needed)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| `schedule` | 1.x (already installed) | Periodic job scheduling in daemon | Already used in `run_ingestion.py` for health-check cycle and daily RiskGate reset |
| `selenium` + `webdriver-manager` | 4.x (installed) | Chrome driver lifecycle | `BravosScraper.startup()` / `BravosScraper.shutdown()` already implemented |
| `systemd` | OS-level (Ubuntu 24.04) | Service management | Unit files in `infra/`; installed via `systemctl enable` on bravos-vm1 |

### Supporting (new, for Gmail poller stub)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `imaplib` | stdlib | IMAP email polling (future full impl) | When INGST-V2-01 is implemented; stub does not need it |

**Installation:** No new packages required. All dependencies are already installed.

---

## Architecture Patterns

### System Architecture Diagram

```
                    bravos-vm1 (Ubuntu 24.04)
                    ──────────────────────────────────────────

systemd (multi-user.target)
    │
    ├─► xvfb@99.service ────────────────────┐
    │                                        │ Requires=
    ├─► ibgateway.service (After=xvfb@99)   │
    │        │                              (already existed)
    ├─► cloud-sql-proxy.service
    │        │
    │        ├── After= ──────────────────────────────┐
    │        └── After= ──────────────────────────────┤
    │                                                  ▼
    ├─► bravos-trading.service ────────► scripts/run_ingestion.py (daemon)
    │   EnvironmentFile=/etc/bravos/env      │
    │                                        ├─ schedule: run_cycle every 300s
    │                                        ├─ schedule: _gate.reset at 14:30 UTC
    │                                        └─ schedule: _restart_chrome at 01:00 ET  [NEW]
    │
    └─► bravos-gmail.service ──────────► scripts/run_gmail.py  [NEW - stub]
        EnvironmentFile=/etc/bravos/env      │
                                             └─ (future: poll Gmail, call scraper.process_alert(url))

/etc/bravos/env (mode 600)
    TRADING_MODE=live          ← cutover toggle
    BRAVOS_DB_PASSWORD=...
    ALERT_EMAIL=...
    MAX_OPEN_POSITIONS=...
    MAX_ALLOCATION_PCT=...
    DAILY_LOSS_THRESHOLD=...
    WEIGHT_PCT_PER_UNIT=...
```

### Recommended Project Structure

```
infra/
├── ibgateway.service       # existing — pattern source
├── cloud-sql-proxy.service # existing — pattern source
├── xvfb@.service           # existing — template pattern
├── bravos-trading.service  # NEW — Phase 8
├── bravos-gmail.service    # NEW — Phase 8
└── env.example             # NEW — documents /etc/bravos/env vars

scripts/
├── run_ingestion.py        # MODIFIED — add nightly Chrome restart schedule job
└── run_gmail.py            # NEW — Phase 8 stub
```

### Pattern 1: Systemd Unit File (based on `infra/ibgateway.service`)

**What:** Long-running Python daemon as a systemd simple service with auto-restart, journald logging, and EnvironmentFile injection.
**When to use:** Any persistent Bravos process that must survive crashes and VM reboots.

```ini
# Source: infra/ibgateway.service (verified by direct read)
[Unit]
Description=Bravos Trading Daemon
After=network.target ibgateway.service cloud-sql-proxy.service

[Service]
Type=simple
ExecStart=/home/chris_s_dodd/miniconda3/bin/python /home/chris_s_dodd/bravos/scripts/run_ingestion.py
WorkingDirectory=/home/chris_s_dodd/bravos
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
User=chris_s_dodd
EnvironmentFile=/etc/bravos/env

[Install]
WantedBy=multi-user.target
```

**Key decision (Claude's discretion):** Service files belong in `infra/` under version control and are installed via symlink (`sudo ln -sf /home/chris_s_dodd/bravos/infra/bravos-trading.service /etc/systemd/system/`). This keeps unit files in git, auditable, and deployable from the repo. The symlink approach matches the pattern for other infra/ services.

**ExecStart path note:** Must use the absolute miniconda3 Python path (`/home/chris_s_dodd/miniconda3/bin/python`) — systemd does not inherit the user's PATH or conda activation. This is documented in Phase 1 research and confirmed by the existing `cloud-sql-proxy.service` pattern which uses an absolute binary path. [VERIFIED: infra/cloud-sql-proxy.service, .planning/phases/01-infrastructure-setup/01-RESEARCH.md]

### Pattern 2: Nightly Chrome Driver Restart (schedule job)

**What:** Replace the running `BravosScraper` instance in-process at 1am ET. Daemon loop stays alive; only the Chrome driver is recycled.
**When to use:** Once daily at 1am ET. Prevents Chrome memory accumulation over multi-day runs.

```python
# Source: run_ingestion.py (verified by direct read) + decision D-02/D-03
def _restart_chrome_driver():
    """Recycle Chrome driver to prevent memory accumulation (D-02, D-03).

    Called by schedule at 01:00 ET. Shuts down existing BravosScraper,
    creates a new one, and replaces the module-level _scraper reference.
    Daemon process stays alive throughout.
    """
    global _scraper
    logger.info("Nightly Chrome driver restart starting (D-02)")
    try:
        if _scraper is not None:
            _scraper.shutdown()
    except Exception:
        logger.warning("Error shutting down old scraper during nightly restart", exc_info=True)
    try:
        _scraper = BravosScraper()
        _scraper.startup()
        logger.info("Nightly Chrome driver restart complete — new driver active")
    except Exception:
        logger.exception(
            "Nightly Chrome driver restart FAILED — old driver was shut down, "
            "daemon is running without a scraper until next restart attempt"
        )

# In main(), after existing schedule.every() calls:
# 06:00 UTC = 01:00 ET (EST, UTC-5). During EDT (summer, UTC-4) this fires at
# 02:00 ET — 1 hour late. Known DST limitation; still well within the safe window
# (after ~12:15am Gateway restart, before 4am pre-market).
schedule.every().day.at("06:00").do(_restart_chrome_driver)
logger.info("Scheduled nightly Chrome driver restart at 06:00 UTC (~01:00 ET winter)")
```

**Note on SIGUSR1 alternative (Claude's discretion resolved):** Using a `schedule` job inside `run_ingestion.py` is simpler than a separate systemd timer + SIGUSR1 signal handler. The schedule approach requires one new function and one new `schedule.every().day.at()` call — no new systemd unit, no signal handler plumbing, no inter-process communication. Recommendation: use the `schedule` job.

### Pattern 3: EnvironmentFile for Secrets Injection

**What:** `/etc/bravos/env` is a plain `KEY=VALUE` file read by systemd at service start. All env vars in `settings.py` that fall back to `os.environ.get()` are populated this way.
**When to use:** All secrets and tunables for production deployment.

```bash
# /etc/bravos/env (mode 600, owned root:root) — template at infra/env.example
TRADING_MODE=paper
BRAVOS_DB_PASSWORD=<from GCP Secret Manager>
ALERT_EMAIL=chris.s.dodd@gmail.com
MAX_OPEN_POSITIONS=20
MAX_ALLOCATION_PCT=0.25
DAILY_LOSS_THRESHOLD=-5000.0
WEIGHT_PCT_PER_UNIT=0.05
```

**Note:** `BRAVOS_DB_PASSWORD` is the only secret that cannot be fetched from GCP Secret Manager at runtime — `settings.py` reads it directly from `os.environ.get("BRAVOS_DB_PASSWORD")` in `_get_db_connection()`. All other credentials (Bravos site username/password, IBKR config, SMTP password) are fetched via `get_secret()` at first use. So `/etc/bravos/env` needs `BRAVOS_DB_PASSWORD` explicitly; the others are optional overrides. [VERIFIED: bravos/config/settings.py, scripts/run_ingestion.py]

### Pattern 4: `BravosScraper` Shutdown/Reinit Lifecycle

**What:** `BravosScraper` supports a clean quit-and-reinit cycle. `shutdown()` calls `driver.quit()`. A new `BravosScraper()` + `startup()` creates a fresh Chrome driver and logs in.
**When to use:** Nightly driver restart job.

```python
# Source: bravos/ingestion/scraper.py (verified by direct read)

# startup() — creates driver, loads credentials from GCP Secret Manager, logs in
def startup(self):
    self.username = get_secret("bravos-site-username")
    self.password = get_secret("bravos-site-password")
    self.driver = setup_chrome_driver(headless=True)
    if self.driver is None:
        raise RuntimeError("Failed to start Chrome driver after 3 attempts")
    if not self._login():
        raise RuntimeError("Initial login failed after max attempts")

# shutdown() — quits Chrome driver cleanly
def shutdown(self):
    if self.driver:
        try:
            self.driver.quit()
        except Exception:
            logger.warning("Error closing Chrome driver", exc_info=True)
```

**Key insight:** `startup()` calls `get_secret()` — this makes a GCP Secret Manager API call. On a nightly restart this is acceptable (once per 24h). If the GCP Secret Manager API is unavailable at 1am, `startup()` will raise and the nightly restart will fail; the error is caught by the schedule job wrapper and the old driver is already shut down. This is the same failure mode as daemon startup. [VERIFIED: bravos/ingestion/scraper.py]

### Pattern 5: IBApp Auto-Reconnect (D-01 dependency)

**What:** `IBApp._heartbeat_loop()` runs every 60s. If no heartbeat response within 10s, it calls `_trigger_reconnect()`. `_reconnect_loop()` uses exponential backoff (5, 10, 20, 40, 80s), then 60s forever. Error codes 504 and 1100 also trigger immediate reconnect.
**When to use:** This is the mechanism Phase 8 relies on to survive the ~11:45pm–12:15am Gateway restart window.

**Phase 8 validation:** The Phase 8 success criterion for Gateway restart is observational — confirm that IBApp reconnects without operator intervention after the nightly restart window. No new code. [VERIFIED: bravos/broker/connection.py, CONTEXT.md D-01]

### Anti-Patterns to Avoid

- **Hardcoding Python path as `/usr/bin/python3` in ExecStart:** systemd does not inherit the user's PATH. On bravos-vm1, ibapi is installed in miniconda3 at `/home/chris_s_dodd/miniconda3/bin/python`. Using system python3 will fail with ImportError for ibapi. [VERIFIED: .planning/phases/01-infrastructure-setup/01-RESEARCH.md]
- **Adding `Requires=ibgateway.service` to bravos-trading.service:** `Requires=` means if ibgateway.service stops, systemd also stops bravos-trading.service. Since IBApp handles reconnect in-process, the trading daemon should survive a Gateway restart. Use `After=` only, not `Requires=`. The existing ibgateway.service uses `Requires=xvfb@99.service` because xvfb is a hard dependency (Gateway cannot run without a display), but bravos-trading.service does not have the same relationship with ibgateway.service. [VERIFIED: infra/ibgateway.service, bravos/broker/connection.py]
- **Using `schedule.every().day.at("01:00")` for the Chrome restart:** The schedule library uses local time. On bravos-vm1 (GCP us-central1), the system timezone is likely UTC. `"01:00"` in schedule interprets as 01:00 local time which may be UTC, not ET. Use `"06:00"` (06:00 UTC = 01:00 ET in winter, 02:00 ET in summer). Verify VM timezone with `timedatectl` before deploying. [ASSUMED — UTC is the typical GCP VM default; verify on bravos-vm1]
- **Not handling `_scraper = None` between shutdown and reinit:** During the nightly restart, there is a window (~10s for `BravosScraper.startup()`) where `_scraper` is not None but `_scraper.driver` is None (old driver quit, new one not yet created). The `run_cycle` health-check job may fire during this window if the schedule library runs both jobs in the same second. The `_restart_chrome_driver` function should set `_scraper = None` after shutdown and only assign the new instance after `startup()` completes. Better: assign new scraper before starting, so even if startup raises, the global is in a defined state.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Service auto-restart | Custom process supervisor loop | `Restart=always` in systemd unit | Handles crashes, signals, and VM reboots; no code required |
| Periodic job scheduling | `threading.Timer` or `while True: sleep` | `schedule` library (already imported) | Already used in daemon; consistent with existing patterns |
| Chrome memory limit | `resource.setrlimit()` in Python | systemd `MemoryMax=` (optional) + nightly restart | OS-level enforcement; nightly restart is the primary control |
| Env var management | `python-dotenv` loading `.env` | `EnvironmentFile=` in systemd unit | systemd injects env vars before process start; no library needed |

**Key insight:** Phase 8 is operational hardening, not code architecture. The heavy lifting (reconnect, auth, scheduling) is already implemented. The new code surface is minimal.

---

## Runtime State Inventory

> This section applies because Phase 8 changes TRADING_MODE from `paper` to `live` — a configuration change that affects runtime behavior.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | TRADING_MODE is not stored in DB — it is read from env at process start. No DB records to migrate. | None |
| Live service config | No running `bravos-trading.service` or `bravos-gmail.service` exist yet — these are new. Existing `ibgateway.service` is currently configured for PAPER (port 4002 per `ibc-config.ini`). | On live cutover: update `ibc-config.ini` to point to live account (or IBC handles this via TWS login). This is operator action, not a code edit. |
| OS-registered state | No existing bravos-trading or bravos-gmail systemd units installed yet. `ibgateway.service`, `cloud-sql-proxy.service`, and `xvfb@99.service` are already installed. | `sudo systemctl enable + start` for two new units after `infra/` files are created. |
| Secrets/env vars | `BRAVOS_DB_PASSWORD` is the only env var currently needed at startup — it is set in the environment on bravos-vm1. After Phase 8, `/etc/bravos/env` becomes the canonical source. | Create `/etc/bravos/env` on bravos-vm1 with all required vars (see Pattern 3). `TRADING_MODE` defaults to `paper` until explicitly set to `live`. |
| Build artifacts | No stale build artifacts relevant to this phase. | None |

**The live cutover sequence (D-04):**
1. Edit `/etc/bravos/env`: change `TRADING_MODE=paper` to `TRADING_MODE=live`
2. `sudo systemctl restart bravos-trading.service`
3. Confirm in journal: `IBKR connected — mode=live host=127.0.0.1 port=4001`

---

## Critical Gap: Gmail Poller Entry Script Does Not Exist

**Finding:** `bravos-gmail.service` (D-06) is meant to run a Gmail poller process. No such process exists in the codebase. [VERIFIED: `find /home/chris_s_dodd/bravos -name "*.py" | grep -v worktrees` — complete file listing shows no Gmail poller script or class]

**What does exist:**
- `BravosScraper.process_alert(url)` — the consumer that processes an alert URL [VERIFIED: bravos/ingestion/scraper.py]
- `GMAIL_SENDER_FILTER` and `GMAIL_SUBJECT_KEYWORD` constants in `settings.py` [VERIFIED: bravos/config/settings.py]
- Gmail SMTP outbound (for alerts) in `notifier.py` — this is outbound email only, not polling [VERIFIED: bravos/notifications/notifier.py]

**What does not exist:**
- Gmail API / IMAP client code
- OAuth2 token/credentials for Gmail API
- `scripts/run_gmail.py` or any entry point for `bravos-gmail.service`
- `GmailPoller` class or any class with equivalent functionality

**Options for Phase 8:**

Option A (recommended): Create `scripts/run_gmail.py` as a minimal stub that starts, logs "Gmail poller not yet implemented — this service exists as a systemd unit placeholder for INGST-V2-01", then sleeps in a loop. This satisfies DEPL-02 (the service exists with auto-restart) without implementing the unscoped INGST-V2-01 feature. The `bravos-gmail.service` unit becomes a placeholder that can be activated when the Gmail poller is built.

Option B: Omit `bravos-gmail.service` entirely. The DEPL-02 requirement says "separate systemd services" — it was written before the architecture evolved to Gmail-triggered. With the Gmail poller not yet built, the two services could be interpreted as trading daemon + any other future service. However, CONTEXT.md D-06 explicitly names `bravos-gmail.service`, so this contradicts a locked decision.

Option C: Build the Gmail poller as part of Phase 8. This is out of scope per CONTEXT.md ("No new pipeline logic — this phase is operational hardening only") and INGST-V2-01 is a v2 requirement.

**Recommendation:** Option A. Create a stub script. The service unit is correctly set up, auto-restarts, and logs. No feature work, no scope creep.

---

## Common Pitfalls

### Pitfall 1: `Requires=ibgateway.service` vs `After=ibgateway.service`
**What goes wrong:** Adding `Requires=ibgateway.service` causes systemd to stop `bravos-trading.service` whenever `ibgateway.service` restarts (e.g., during nightly Gateway restart). This defeats the purpose of IBApp's in-process reconnect logic.
**Why it happens:** `Requires=` is stronger than `After=` — it creates a lifecycle dependency, not just an ordering constraint.
**How to avoid:** Use only `After=ibgateway.service` (ordering). The daemon handles Gateway disconnects internally via `_reconnect_loop`.
**Warning signs:** In journalctl, seeing `bravos-trading.service: Stop reason: dependency` around midnight.

### Pitfall 2: schedule library uses local system time, not UTC
**What goes wrong:** `schedule.every().day.at("01:00")` fires at 01:00 in the system's local timezone. GCP VMs default to UTC. If this is intended as 01:00 ET (UTC-5), it will actually fire at 06:00 ET.
**Why it happens:** `schedule` uses Python's `datetime.now()` which returns local time.
**How to avoid:** Determine bravos-vm1's timezone with `timedatectl` before setting the time string. If UTC (likely): use `"06:00"` for 01:00 ET winter / `"05:00"` for 01:00 ET summer. The existing `_gate.reset` job uses `"14:30"` UTC for 09:30 ET winter — follow the same pattern.
**Warning signs:** `crontab -l` / `timedatectl` on bravos-vm1 shows UTC; Chrome restart fires at 01:00 UTC (8pm or 9pm ET) instead of 06:00 UTC.

### Pitfall 3: Nightly restart leaves `_scraper` pointing at a half-initialized object
**What goes wrong:** `_restart_chrome_driver()` calls `_scraper.shutdown()` then `_scraper = BravosScraper()` then `_scraper.startup()`. If `startup()` raises (GCP Secret Manager unavailable, Chrome crash), `_scraper` is an uninitialized `BravosScraper` instance with `driver=None`. The next call to `run_cycle()` or `process_alert()` will crash on `_scraper.driver.find_elements(...)` with `AttributeError`.
**Why it happens:** `startup()` can fail; the assignment `_scraper = BravosScraper()` does not guarantee a usable state.
**How to avoid:** Set `_scraper = None` before calling shutdown, and restore only after `startup()` succeeds. Add a guard in `run_cycle()` and `process_alert()` for `_scraper is None` (already present in `run_cycle()`: `if _scraper is None: return`). [VERIFIED: scripts/run_ingestion.py line 92-95]

### Pitfall 4: `EnvironmentFile=` path must exist before `systemctl start`
**What goes wrong:** If `/etc/bravos/env` does not exist when `sudo systemctl start bravos-trading.service` is run, the service fails to start with `Failed to load environment files: No such file or directory`.
**Why it happens:** systemd reads `EnvironmentFile=` at process start, not at enable time. Missing file = start failure.
**How to avoid:** Create `/etc/bravos/env` (with at minimum `TRADING_MODE=paper` and `BRAVOS_DB_PASSWORD=...`) before enabling or starting the services. Include creation instructions in the deployment operator notes.
**Warning signs:** `systemctl status bravos-trading.service` shows `Active: failed` with an environment file error.

### Pitfall 5: WebDriver restart calls `pkill -9 -f chrome` — may kill other Chrome instances
**What goes wrong:** `setup_chrome_driver()` in `scraper.py` calls `os.system("pkill -9 -f 'chrome.*remote-debugging' 2>/dev/null || true")` at the top. If another Chrome process exists on the VM (unlikely on a dedicated VM, but possible), it is killed.
**Why it happens:** The driver setup function aggressively kills stale Chrome processes to prevent port conflicts.
**How to avoid:** On a dedicated bravos-vm1, this is not a problem in practice. Note it for awareness.
**Warning signs:** Unexpected Chrome process termination in journalctl from unrelated services.

---

## Code Examples

### Verified Nightly Restart Job (add to `scripts/run_ingestion.py`)

```python
# Source: pattern derived from existing schedule jobs in run_ingestion.py (verified)
# and BravosScraper lifecycle in bravos/ingestion/scraper.py (verified)

def _restart_chrome_driver():
    """Recycle Chrome driver nightly to prevent memory accumulation (D-02, D-03).

    Called at 06:00 UTC (~01:00 ET winter). Daemon stays alive; only the
    Chrome driver is recycled. _scraper is set to None during the transition
    so run_cycle() skips if it fires concurrently (guard at line 92).
    """
    global _scraper
    logger.info("Nightly Chrome driver restart starting — recycling BravosScraper (D-02)")
    old_scraper = _scraper
    _scraper = None  # Guard: run_cycle returns early if _scraper is None

    if old_scraper is not None:
        try:
            old_scraper.shutdown()
            logger.info("Old Chrome driver shut down")
        except Exception:
            logger.warning("Error during old scraper shutdown", exc_info=True)

    try:
        new_scraper = BravosScraper()
        new_scraper.startup()
        _scraper = new_scraper
        logger.info("Nightly Chrome driver restart complete — new driver active and logged in")
    except Exception:
        logger.exception(
            "Nightly Chrome driver restart FAILED — daemon continues without scraper. "
            "process_alert() calls will be skipped until next restart attempt."
        )
        # _scraper remains None; run_cycle() guard handles this safely


# In main(), after existing schedule lines:
# 06:00 UTC = ~01:00 ET (EST winter). Gateway restart window closes ~12:15am ET.
# No scrape cycle disrupted: run_cycle fires every 300s; 06:00 UTC is well clear.
schedule.every().day.at("06:00").do(_restart_chrome_driver)
logger.info("Scheduled nightly Chrome driver restart at 06:00 UTC (~01:00 ET winter)")
```

### Verified Unit File: `infra/bravos-trading.service`

```ini
# Source: modeled on infra/ibgateway.service (verified by direct read) + D-06/D-07/D-08
[Unit]
Description=Bravos Trading Daemon
After=network.target ibgateway.service cloud-sql-proxy.service

[Service]
Type=simple
ExecStart=/home/chris_s_dodd/miniconda3/bin/python /home/chris_s_dodd/bravos/scripts/run_ingestion.py
WorkingDirectory=/home/chris_s_dodd/bravos
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
User=chris_s_dodd
EnvironmentFile=/etc/bravos/env

[Install]
WantedBy=multi-user.target
```

### Verified Unit File: `infra/bravos-gmail.service`

```ini
# Source: same pattern as bravos-trading.service; ExecStart points to stub script
[Unit]
Description=Bravos Gmail Poller (placeholder — INGST-V2-01)
After=network.target cloud-sql-proxy.service

[Service]
Type=simple
ExecStart=/home/chris_s_dodd/miniconda3/bin/python /home/chris_s_dodd/bravos/scripts/run_gmail.py
WorkingDirectory=/home/chris_s_dodd/bravos
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
User=chris_s_dodd
EnvironmentFile=/etc/bravos/env

[Install]
WantedBy=multi-user.target
```

**Note:** `bravos-gmail.service` does NOT declare `After=ibgateway.service` because the Gmail poller does not connect to IBKR — it only calls `scraper.process_alert()` which is a BravosScraper operation. The CONTEXT.md D-07 says both services declare `After=ibgateway.service`, but this is likely an oversight in the discussion — the Gmail poller has no IBKR dependency. The planner should use D-07 as written (add `After=ibgateway.service` to both) to stay consistent with the locked decision, but can note this as a non-harmful over-constraint.

### Verified `infra/env.example`

```bash
# /etc/bravos/env — template for production deployment
# Copy to /etc/bravos/env, fill in values, chmod 600, chown root:root

# Trading mode: "paper" (port 4002) or "live" (port 4001)
TRADING_MODE=paper

# Database password (fetched from GCP Secret Manager at DB connect time in psycopg2 calls)
BRAVOS_DB_PASSWORD=

# Notification recipient
ALERT_EMAIL=

# Risk controls (optional — these are the defaults from settings.py)
MAX_OPEN_POSITIONS=20
MAX_ALLOCATION_PCT=0.25
DAILY_LOSS_THRESHOLD=-5000.0
WEIGHT_PCT_PER_UNIT=0.05
```

**Note on what does NOT need to be in `/etc/bravos/env`:** Bravos site credentials, IBKR credentials, SMTP credentials, and SMTP sender address are all fetched from GCP Secret Manager via `get_secret()` at first use. They do not need to be in the EnvironmentFile. [VERIFIED: bravos/config/secrets_config.py, bravos/notifications/notifier.py]

### Minimal Gmail Poller Stub (`scripts/run_gmail.py`)

```python
#!/usr/bin/env python3
"""
Bravos Trading System — Gmail Poller Entry Point (PLACEHOLDER).

This script is a placeholder for the Gmail poller process (INGST-V2-01).
bravos-gmail.service points here so the systemd unit is registered and
auto-restarts are enabled, but no Gmail polling is implemented yet.

When INGST-V2-01 is implemented, this script will:
  1. Poll Gmail via IMAP (settings.GMAIL_SENDER_FILTER, GMAIL_SUBJECT_KEYWORD)
  2. Extract post URLs from email bodies
  3. Call scraper.process_alert(url) for each new URL
"""
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.gmail.daemon")

logger.info(
    "Gmail poller started (placeholder — INGST-V2-01 not yet implemented). "
    "Service is registered and will auto-restart. No email polling active."
)

# Keep the process alive so systemd Restart=always does not thrash
while True:
    time.sleep(60)
```

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (pytest.ini at repo root) |
| Config file | `/home/chris_s_dodd/bravos/pytest.ini` |
| Quick run command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -x -q` |
| Full suite command | `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEPL-02 | bravos-trading.service unit file parses correctly | manual (systemd on bravos-vm1) | `systemd-analyze verify infra/bravos-trading.service` | ❌ Wave 0 |
| DEPL-02 | bravos-gmail.service unit file parses correctly | manual (systemd on bravos-vm1) | `systemd-analyze verify infra/bravos-gmail.service` | ❌ Wave 0 |
| D-02 | `_restart_chrome_driver()` calls `old_scraper.shutdown()` and `new_scraper.startup()` | unit | `pytest tests/test_deployment.py::test_nightly_chrome_restart -x` | ❌ Wave 0 |
| D-02 | `_restart_chrome_driver()` sets `_scraper = None` before shutdown (concurrent guard) | unit | `pytest tests/test_deployment.py::test_restart_sets_scraper_none_during_transition -x` | ❌ Wave 0 |
| D-02 | `_restart_chrome_driver()` handles `startup()` failure gracefully (no crash) | unit | `pytest tests/test_deployment.py::test_restart_handles_startup_failure -x` | ❌ Wave 0 |
| D-08 | `env.example` documents all env vars present in `settings.py` that use `os.environ.get()` | manual review | N/A | ❌ Wave 0 |
| D-04/D-05 | `get_ibkr_port()` returns 4001 when `TRADING_MODE=live` | unit (already exists) | `pytest tests/test_infrastructure.py -k ibkr_port -x` | check existing |

### Sampling Rate

- **Per task commit:** `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -x -q`
- **Per wave merge:** `/home/chris_s_dodd/miniconda3/bin/python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_deployment.py` — covers nightly Chrome restart behavior (3 tests above)
- [ ] `systemd-analyze verify` check can be added to verification script

*(Note: Most Phase 8 changes are operational infrastructure that cannot be unit tested — service files, env file creation, live account activation. The test file covers the one new code path: `_restart_chrome_driver()` in `run_ingestion.py`.)*

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| systemd | Service unit files | ✓ (Ubuntu 24.04) | 255.x | — |
| miniconda3 Python | ExecStart in unit files | ✓ | 3.13.13 | — |
| `/etc/bravos/` directory | EnvironmentFile | ✗ (not yet created) | — | Create: `sudo mkdir /etc/bravos && sudo chmod 700 /etc/bravos` |
| `schedule` library | Nightly restart job | ✓ (installed) | 1.x | — |
| GCP Secret Manager access | `BravosScraper.startup()` on nightly restart | ✓ (service account on bravos-vm1) | — | — |
| bravos-vm1 timezone | Correct UTC offset for `"06:00"` schedule | Unverified | — | Run `timedatectl` on VM before deploying |

**Missing dependencies with no fallback:**
- `/etc/bravos/` directory and `/etc/bravos/env` file — must be created manually on bravos-vm1 before `systemctl start` can succeed.

**Missing dependencies with fallback:**
- VM timezone unverified — if not UTC, the `"06:00"` schedule string must be adjusted. Low risk: GCP VMs default to UTC and the 1am ET window has hours of margin.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No new auth surfaces — credentials still via GCP Secret Manager |
| V3 Session Management | no | No session management changes |
| V4 Access Control | yes (partial) | `/etc/bravos/env` mode 600 root:root — prevents non-root reads of BRAVOS_DB_PASSWORD |
| V5 Input Validation | no | No new user input surfaces |
| V6 Cryptography | no | No new crypto; BRAVOS_DB_PASSWORD passed via env var, not code |

### Known Threat Patterns for systemd EnvironmentFile

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| `/etc/bravos/env` world-readable | Information Disclosure | `chmod 600 /etc/bravos/env && chown root:root /etc/bravos/env` |
| BRAVOS_DB_PASSWORD in process env (visible in `/proc/$PID/environ` to root) | Information Disclosure | Unavoidable with EnvironmentFile; acceptable for root-accessible VM |
| Service running as `chris_s_dodd` (non-root user) | Privilege Escalation | Service user cannot read `/etc/bravos/env` directly (mode 600 root), but systemd reads it on behalf of the service; this is the intended and safe design |

**Note on secret exposure:** The GCP Secret Manager pattern means most secrets are not in `/etc/bravos/env` — they are fetched at runtime via the GCP service account. Only `BRAVOS_DB_PASSWORD` and operational tunables are in the file. [VERIFIED: bravos/config/secrets_config.py]

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Category-page polling every 5 min | Gmail-triggered: `process_alert(url)` called by Gmail poller; health-check loop keeps driver warm | Phase 2 (2026-05-09) | The daemon's `run_cycle()` no longer polls the category page — it just checks session health. The Gmail poller (when built) is the actual signal source. |
| Dashboard service (DEPL-02 original) | Gmail poller service (dashboard cut from scope) | Phase 7 context | DEPL-02 now means trading daemon + Gmail poller, not trading daemon + web dashboard |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | GCP VM bravos-vm1 system timezone is UTC, so `schedule.every().day.at("06:00")` fires at 01:00 ET | Common Pitfalls / Code Examples | Chrome restart fires 5 hours early or late; wrong window relative to Gateway restart. Verify with `timedatectl` on bravos-vm1. |
| A2 | `ibgateway.service` runs on port 4002 (paper) and must be reconfigured for live — this is an IBC/TWS config change, not a systemd change | Runtime State Inventory | If IBC auto-selects port based on TWS login, no manual config needed. Operator must verify IBC config at cutover. |
| A3 | `systemd-analyze verify` is available on Ubuntu 24.04 to validate unit file syntax | Validation Architecture | If not available, use `systemd --user --test` or just install and enable |

**If this table is empty:** All claims in this research were verified or cited — no user confirmation needed. (Three claims remain assumed; all are low-risk and can be verified on bravos-vm1 before deployment.)

---

## Open Questions

1. **Gmail poller: stub or skip?**
   - What we know: No Gmail poller script exists; CONTEXT.md D-06 requires `bravos-gmail.service`; INGST-V2-01 is a v2 requirement explicitly out of scope.
   - What's unclear: Does the user want a stub script, or is `bravos-gmail.service` intentionally pointing to unimplemented code with the expectation it fails and restarts (effectively a placeholder)?
   - Recommendation: Create `scripts/run_gmail.py` as a minimal sleep-loop stub. This satisfies DEPL-02, does not scope-creep into INGST-V2-01, and leaves the service unit in a deployable state.

2. **bravos-vm1 system timezone**
   - What we know: GCP VMs typically use UTC; the existing `_gate.reset` job uses `"14:30"` (which implies UTC awareness).
   - What's unclear: Has the VM timezone been changed from UTC?
   - Recommendation: Run `timedatectl` on bravos-vm1 before committing the `"06:00"` schedule string. If not UTC, adjust to the correct offset.

3. **Does `bravos-gmail.service` need `After=ibgateway.service`?** (D-07 says yes)
   - What we know: The Gmail poller has no IBKR dependency — it only calls `scraper.process_alert()`.
   - What's unclear: Whether D-07 intended this as a blanket "both services depend on ibgateway" or was written without thinking about the Gmail poller's specific dependencies.
   - Recommendation: Follow D-07 as written (add `After=ibgateway.service` to `bravos-gmail.service`). It is a non-harmful over-constraint — the service will wait for ibgateway to start before launching, which is fine.

---

## Sources

### Primary (HIGH confidence — verified by direct codebase inspection)

- `infra/ibgateway.service` — unit file structure, `Restart=always`, `RestartSec=15`, `User=chris_s_dodd`, `Type=simple`, `After=` pattern
- `infra/cloud-sql-proxy.service` — EnvironmentFile pattern, absolute binary path in ExecStart
- `infra/xvfb@.service` — template service pattern
- `scripts/run_ingestion.py` — daemon entry point, existing `schedule` jobs, `_scraper` global, shutdown flow
- `bravos/ingestion/scraper.py` — `BravosScraper.startup()`, `shutdown()`, `_check_session()`, `process_alert()` lifecycle
- `bravos/broker/connection.py` — `IBApp._heartbeat_loop()`, `_reconnect_loop()`, `_RETRY_DELAYS`, error code routing
- `bravos/config/settings.py` — `TRADING_MODE`, `get_ibkr_port()`, all env-var-backed settings
- `bravos/config/secrets_config.py` — `REQUIRED_SECRETS` list, `get_secret()` pattern
- `.planning/phases/08-live-deployment/08-CONTEXT.md` — locked decisions D-01 through D-08
- `.planning/REQUIREMENTS.md` — DEPL-02 definition and traceability

### Secondary (MEDIUM confidence — planning artifacts)

- `.planning/phases/01-infrastructure-setup/01-RESEARCH.md` — miniconda3 Python path for systemd ExecStart
- `.planning/phases/01-infrastructure-setup/01-DECISIONS.md` — DEV-01 (Python 3.13.5 + miniconda3)
- `.planning/STATE.md` — accumulated project decisions

### Tertiary (LOW confidence — assumed)

- GCP VM timezone defaults to UTC (A1 in Assumptions Log)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed; direct file verification
- Architecture: HIGH — unit file patterns directly read from infra/; scraper lifecycle verified from source
- Pitfalls: HIGH (pitfalls 1-4) / MEDIUM (pitfall 5) — derived from direct code inspection
- Gmail poller gap: HIGH — verified by exhaustive file listing; no poller script exists

**Research date:** 2026-05-21
**Valid until:** 2026-06-21 (stable domain; changes only if bravos-vm1 is reprovisioned)
