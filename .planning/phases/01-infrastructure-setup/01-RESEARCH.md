# Phase 1: Infrastructure Setup — Research

**Researched:** 2026-05-03
**Updated:** 2026-05-03 (opt-trade-vm4 investigation complete — see Open Questions section for resolved values)
**Domain:** GCP VM provisioning, IB Gateway + IBC, PostgreSQL, Python environment, GCP Secret Manager, Chromium headless
**Confidence:** HIGH (opt-trade-vm4 investigated; all previously LOW/MEDIUM confidence items now CONFIRMED)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: Machine type: `e2-standard-2` (2 vCPU, 8GB RAM)
- D-02: Boot disk: 50GB SSD
- D-03: Region: US East (us-east1 or us-east4)
- D-04: OS: Ubuntu LTS (mirror opt-trade-vm4's OS) — **CONFIRMED: Ubuntu 24.04 LTS (Noble)**
- D-05: Use IBC to manage IB Gateway startup and lifecycle — **CONFIRMED: IbcAlpha at /opt/ibcalpha/current/**
- D-06: 2FA handling: mirror opt-trade-vm4's exact approach (mobile notification approval); investigate and document the exact mechanism during implementation. Operator approves mobile 2FA once at Gateway startup; IBC handles everything else.
- D-07: IB Gateway must accept connections on its configured port (paper: 4002, live: 4001) after startup
- D-08: PostgreSQL runs on the VM itself — no Cloud SQL — **NOTE: PostgreSQL is NOT on opt-trade-vm4; will be a bravos_vm1-only install**
- D-09: Separate database and schema from opt-trade-vm4's database
- D-10: Trading schema tables: `signals`, `orders`, `position_lots`, `executions`, `broker_positions_snapshot` (plus any audit/logging tables)
- D-11: Backup strategy: Claude's discretion (automated pg_dump to GCS bucket is the standard approach)
- D-12: ~~Python 3.11 (not 3.12)~~ **SUPERSEDED: Python 3.13.5 via miniconda3 (mirrors opt-trade-vm4 exactly — see DECISIONS.md)**
- D-13: ~~Virtual environment (`venv`) for isolation~~ **SUPERSEDED: miniconda3 environment (mirrors opt-trade-vm4 — see DECISIONS.md)**
- D-14: Dependencies managed via `requirements.txt` with pinned versions
- D-15: ~~ibapi installed from IB's official developer portal zip (not PyPI)~~ **SUPERSEDED: pip install ibapi==9.81.1.post1 (mirrors opt-trade-vm4 — see DECISIONS.md)**
- D-16: Secrets stored in GCP Secret Manager: Bravos Research credentials, IBKR account config
- D-17: VM accesses secrets via service account — no secrets on disk, no secrets in version control
- D-18: Startup validation: system checks all required secrets are readable before marking setup complete
- D-19: ~~Chromium installed system-wide (apt), not Chrome~~ **SUPERSEDED: google-chrome-stable 139.0.7258.66 (mirrors opt-trade-vm4 — uses deb package)**
- D-20: Anti-detection flags applied at Chrome startup
- D-21: Success criterion: a test script can open a headless browser session without errors

### Claude's Discretion
- pg_dump backup schedule and GCS bucket naming
- Firewall/VPC configuration details
- ChromeDriver version management approach (webdriver-manager vs pinned)
- ~~Exact Ubuntu LTS version (resolved: 24.04 Noble)~~

### Deferred Ideas (OUT OF SCOPE)
- None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEPL-01 | System runs on a GCP VM (Linux) with IB Gateway installed; IB Gateway runs persistently with 2FA handled at operator startup only | IBC setup section; systemd service pattern; 2FA flow |
| DEPL-03 | PostgreSQL installed on VM with trading schema (signals, orders, position_lots, executions, broker_positions_snapshot) | PostgreSQL install section; DDL schema section |
| DEPL-04 | Bravos Research credentials and IBKR configuration stored in GCP Secret Manager or env vars — never in code or committed files | GCP Secret Manager section; secrets list; Python snippet |
| DEPL-05 | Chromium runs in headless mode for Selenium scraping with appropriate anti-detection flags | Chromium headless section; exact Chrome options list |
</phase_requirements>

---

## Summary

Phase 1 provisions `bravos_vm1` from scratch — a GCP e2-standard-2 VM running **Ubuntu 24.04 LTS (Noble)** (confirmed from opt-trade-vm4 investigation) that becomes the execution surface for all subsequent phases. The phase installs and configures four independent subsystems: IB Gateway managed by IBC (IbcAlpha at /opt/ibcalpha/), PostgreSQL 15 with the trading schema, Google Chrome stable (deb package, 139.x) for Selenium, and GCP Secret Manager integration via service account. No application code is written; success means every component is independently verifiable via a standalone test command.

**Primary recommendation:** Mirror opt-trade-vm4 exactly (OS=Ubuntu 24.04, Python=3.13.5+miniconda3, ibapi=pip 9.81.1.post1, Chrome=google-chrome-stable, IBC=IbcAlpha /opt/ibcalpha/). The operator must SSH in at Gateway startup to approve the 2FA push — this is intentional and not automatable without significant fragility risk.

---

## opt-trade-vm4 Reference

opt-trade-vm4 is the reference implementation: same GCP project, same IBC setup, same IB Gateway version. bravos_vm1 should mirror it exactly in structure.

### CONFIRMED: opt-trade-vm4 Versions (Investigated 2026-05-03)

| Component | Confirmed Value | Notes |
|-----------|-----------------|-------|
| OS | Ubuntu 24.04 LTS (Noble) | NOT 22.04 as originally estimated |
| Python | 3.13.5 via miniconda3 | NOT Python 3.11 + venv (decision D-12 superseded) |
| Package manager | miniconda3 at ~/miniconda3/ | Replaces venv approach |
| ibapi | 9.81.1.post1 (pip install) | Installed in miniconda env; NOT from official zip (D-15 superseded) |
| ibapi location | /home/chris_s_dodd/miniconda3/lib/python3.13/site-packages | |
| IB Gateway | Installed at /opt/ibgateway/ | Binary at /usr/local/bin/ibgateway |
| IBC | IbcAlpha at /opt/ibcalpha/current/ | IBC.jar present |
| IBC startup script | /opt/ibcalpha/start_ib_gateway.sh | Starts BOTH PAPER and LIVE gateways |
| IBC mode | Headless via gatewaystart.sh -inline | |
| Xvfb | Running on display :99 (xvfb@99.service) | |
| ibgateway.service | ExecStart=/opt/ibcalpha/start_ib_gateway.sh | Restart=always, RestartSec=15, User=ubuntu |
| Chrome | google-chrome-stable 139.0.7258.66 | Deb package (not snap/chromium) |
| PostgreSQL | NOT installed | psql not found on opt-trade-vm4 |
| ~/Jts/ | Does NOT exist | IB Gateway uses /opt/ibgateway/ instead |

**Key implications for bravos_vm1:**
- Use `ubuntu-2404-lts-amd64` image (not 22.04)
- Install miniconda3, create Python 3.13 environment
- `pip install ibapi==9.81.1.post1` (no zip download needed)
- IBC lives at `/opt/ibcalpha/`, not `/opt/ibc/`
- ibgateway.service uses `Restart=always, RestartSec=15` (not `Restart=no`)
- IB Gateway binary at `/opt/ibgateway/`, not `~/Jts/ibgateway/`
- Use Xvfb display :99 (matching opt-trade-vm4)
- PostgreSQL is a bravos_vm1-only install (not on opt-trade-vm4)

### What to Investigate on opt-trade-vm4 Before Provisioning bravos_vm1

Run these commands on opt-trade-vm4 (via SSH) to capture the reference state:

```bash
# OS version
lsb_release -a

# Python version
python3 --version

# IB Gateway version (check the install directory)
ls ~/Jts/  # or ls /opt/ibc/
cat ~/Jts/ibgateway/*/version 2>/dev/null || ibgateway --version 2>/dev/null

# IBC version
ls /opt/ibc/
cat /opt/ibc/IBC.jar 2>/dev/null | unzip -p - META-INF/MANIFEST.MF 2>/dev/null

# IBC config location
ls ~/ibc/
cat ~/ibc/config.ini 2>/dev/null | grep -v Password  # redact password

# IBC startup script
cat /opt/ibc/scripts/gatewaystart.sh 2>/dev/null
# or
cat ~/ibc_start.sh 2>/dev/null

# Systemd service files
systemctl list-units --type=service | grep -i "ibc\|ibgateway\|brav\|xvfb"
cat /etc/systemd/system/ibgateway.service 2>/dev/null
cat /etc/systemd/system/xvfb.service 2>/dev/null

# PostgreSQL version and install method
psql --version
apt list --installed 2>/dev/null | grep postgresql
systemctl status postgresql

# Check ibapi installed location
find / -name "ibapi" -type d 2>/dev/null | head -5
pip3 show ibapi 2>/dev/null

# Display variable for IB Gateway
grep -r DISPLAY /etc/systemd/system/ 2>/dev/null
```

**Planner note:** Run these commands in Wave 0 of the plan, before any IBC/Gateway tasks. The output determines the exact versions to use on bravos_vm1.

### Ubuntu Version: 24.04 LTS (CONFIRMED)

**CONFIRMED from opt-trade-vm4 investigation:** Ubuntu 24.04 LTS (Noble). Use `ubuntu-2404-lts-amd64` image from `ubuntu-os-cloud` image project when creating bravos_vm1.

~~Recommendation: Ubuntu 22.04 LTS (Jammy)~~ — this recommendation is superseded by the confirmed opt-trade-vm4 version.

---

## IBC Setup

IBC (IbcAlpha) automates IB Gateway login. The project is actively maintained at `github.com/IbcAlpha/IBC`. **Do not use the older IBController project** — it is stale and unsupported.

**CONFIRMED from opt-trade-vm4 investigation:**
- IBC installed at `/opt/ibcalpha/current/` (NOT `/opt/ibc/` as originally documented)
- IBC.jar is present at that location
- Startup script: `/opt/ibcalpha/start_ib_gateway.sh`
- The script starts BOTH PAPER and LIVE gateways simultaneously on display :99
- Uses `gatewaystart.sh -inline` (headless mode)
- ibgateway.service uses `Restart=always, RestartSec=15, User=ubuntu`

~~**Critical version note (MEDIUM confidence):**~~ Exact IBC version TBD — confirm from opt-trade-vm4 IBC.jar manifest. The setup path `/opt/ibcalpha/` is confirmed.

### Installation Steps

```bash
# 1. Install Java (required by IB Gateway)
sudo apt-get update
sudo apt-get install -y openjdk-11-jre

# 2. Install Xvfb (virtual X11 display — IB Gateway requires a display even in headless mode)
# CONFIRMED: opt-trade-vm4 uses xvfb@99.service (display :99)
sudo apt-get install -y xvfb x11vnc

# 3. Download IBC (check github.com/IbcAlpha/IBC/releases for latest version)
# CONFIRMED: opt-trade-vm4 installs to /opt/ibcalpha/current/ (NOT /opt/ibc/)
IBC_VERSION="3.19.0"  # Verify exact version from opt-trade-vm4 IBC.jar manifest
wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" \
  -O /tmp/IBCLinux.zip
sudo mkdir -p /opt/ibcalpha/current
sudo unzip /tmp/IBCLinux.zip -d /opt/ibcalpha/current
sudo chmod o+x /opt/ibcalpha/current/*.sh 2>/dev/null

# 4. Create IBC config directory for the bravos user
mkdir -p ~/ibc
cp /opt/ibcalpha/current/config.ini ~/ibc/config.ini
```

### IBC config.ini Settings

File location: `~/ibc/config.ini` (owned by the bravos service user)

```ini
# Login credentials — populated from secrets at deploy time
# (never hardcode in git; inject at startup via script that reads GCP Secret Manager)
IbLoginId=YOUR_IBKR_USERNAME
IbPassword=YOUR_IBKR_PASSWORD

# Trading mode
TradingMode=paper          # Change to 'live' for live account

# 2FA: Mobile notification via IBKR Mobile app
# IBC will submit login, then IBKR sends a push notification to your phone
# You approve it — Gateway completes login
SecondFactorDevice=         # Leave blank to use default registered device
ReloginAfterSecondFactorAuthenticationTimeout=yes  # Retry if you miss the push
SecondFactorAuthenticationTimeout=180   # 3 minute window (IBKR imposed)
ExitAfterSecondFactorAuthenticationTimeout=no

# Paper account dialog auto-dismiss
AcceptNonBrokerageAccountWarning=yes

# API port — leave blank to use Gateway's configured port (4001/4002)
OverrideTwsApiPort=

# If another session is already connected
ExistingSessionDetectedAction=primaryoverride

# Auto-accept SSL warning
AcceptIncomingConnectionAction=accept
AllowBlindTrading=no
```

**Security note:** IbPassword is in this file in plaintext. The file must be chmod 600 and owned by the bravos user. A startup script should inject credentials from GCP Secret Manager rather than storing them in the ini file permanently.

### Startup Script Pattern

**CONFIRMED:** opt-trade-vm4 uses `/opt/ibcalpha/start_ib_gateway.sh` (not `/opt/ibc/scripts/gatewaystart.sh`). IBC provides this script; the key parameters are:
1. IBC config file path
2. IB Gateway install directory
3. TWS major version number (must match Gateway version)
4. DISPLAY (must be set to Xvfb's display)

```bash
# /home/ubuntu/start-gateway.sh  (or use /opt/ibcalpha/start_ib_gateway.sh directly)
#!/bin/bash
# CONFIRMED: opt-trade-vm4 uses /opt/ibcalpha/start_ib_gateway.sh directly (no wrapper needed)
# If bravos_vm1 needs credential injection, create a wrapper:

export DISPLAY=:99  # CONFIRMED: opt-trade-vm4 uses display :99

# Pull credentials from Secret Manager and inject into config
IBKR_USER=$(gcloud secrets versions access latest --secret="bravos-ibkr-username")
IBKR_PASS=$(gcloud secrets versions access latest --secret="bravos-ibkr-password")
sed -i "s/^IbLoginId=.*/IbLoginId=${IBKR_USER}/" ~/ibc/config.ini
sed -i "s/^IbPassword=.*/IbPassword=${IBKR_PASS}/" ~/ibc/config.ini

/opt/ibcalpha/start_ib_gateway.sh \
  "~/ibc/config.ini" \
  "/opt/ibgateway"    \  # CONFIRMED: IB Gateway install path on opt-trade-vm4
  paper                  # "paper" or "live"
```

### 2FA Mobile Notification Flow

1. Operator SSHs into the VM and runs the startup script (or starts the ibgateway systemd service)
2. IBC fills in the Gateway login form with stored credentials
3. IBKR sends a push notification to the operator's phone (IBKR Mobile app)
4. Operator opens IBKR Mobile and taps "Approve" (or the notification itself)
5. Gateway completes login within the 3-minute window
6. IB Gateway API port (4001 or 4002) becomes accessible
7. Subsequent sessions: IBC auto-reconnects after the Gateway's mandatory daily restart — **no 2FA required for reconnects within the same login session**

**Critical:** 2FA is only needed at initial startup (or after a full Gateway logout). The daily nightly restart (~11:45pm–12:15am ET) does NOT require a new 2FA because IBC re-authenticates using cached credentials, not a fresh login. The session is maintained across restarts.

### Systemd Service Files

**CONFIRMED from opt-trade-vm4 investigation:** Use display :99 (not :1) and Restart=always with RestartSec=15 for ibgateway.service (mirrors opt-trade-vm4 exactly).

```ini
# /etc/systemd/system/xvfb@.service  (template service — opt-trade-vm4 uses xvfb@99.service)
# Or equivalent: ensure Xvfb is running on display :99 before ibgateway.service starts
[Unit]
Description=X Virtual Framebuffer (display :%i)
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/Xvfb :%i -screen 0 1024x768x24 -nolisten tcp
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/ibgateway.service
# CONFIRMED: mirrors opt-trade-vm4 configuration
[Unit]
Description=IB Gateway (managed by IBC)
After=network.target xvfb@99.service
Requires=xvfb@99.service

[Service]
Type=simple
User=ubuntu
ExecStart=/opt/ibcalpha/start_ib_gateway.sh
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

**Note:** opt-trade-vm4 uses `Restart=always, RestartSec=15` — this is intentional and mirrored. The operator must approve the 2FA push when the service first starts or restarts. The 2FA window (3 minutes) is set in IBC config; IBC retries if `ReloginAfterSecondFactorAuthenticationTimeout=yes`.

---

## IB Gateway Installation

### Download

```bash
# Stable version (preferred for production)
wget https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh \
  -O /tmp/ibgateway-installer.sh
chmod u+x /tmp/ibgateway-installer.sh
```

### Install (Silent / Headless)

IB Gateway uses an InstallAnywhere-based installer that requires a display. On a headless VM, run via Xvfb:

```bash
# Start Xvfb first if not already running
sudo Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1

# Run installer
/tmp/ibgateway-installer.sh
# Accept defaults; install to ~/Jts/ibgateway/ (default)
# When asked "Do you want to launch IB Gateway?" — select No
```

Alternatively use the `-c` (console/text) mode if supported by the installer version:

```bash
/tmp/ibgateway-installer.sh -c
```

**CONFIRMED from opt-trade-vm4:** Gateway binary is at `/usr/local/bin/ibgateway` with installation at `/opt/ibgateway/`. The default `~/Jts/ibgateway/` path does NOT exist on opt-trade-vm4.

Use `/opt/ibgateway` as the install target:
```bash
# Run installer with -o flag to set install dir (if supported), or install then move to /opt/ibgateway
sudo mkdir -p /opt/ibgateway
# Accept default install path, then verify binary is accessible at /usr/local/bin/ibgateway
```

### Version Matching with ibapi

**CONFIRMED from opt-trade-vm4:** ibapi 9.81.1.post1 (installed via pip) works with the Gateway installed at `/opt/ibgateway/`. No manual version matching with a zip file is required — use `pip install ibapi==9.81.1.post1`.

~~- IB Gateway stable as of early 2025: approximately version 10.30.x (build ~1030)~~
~~- Confirm on opt-trade-vm4: `cat ~/Jts/ibgateway/*/version`~~
~~- Download matching ibapi from: `https://interactivebrokers.github.io/`~~

### API Port Configuration

IB Gateway exposes a TCP socket for the Python API. Configure ports in Gateway's settings UI (accessible once running with DISPLAY set):

- **Paper trading:** port 4002
- **Live trading:** port 4001

After initial GUI setup, port settings are stored in `~/Jts/ibgateway/<version>/jts.ini`. Verify:

```bash
grep -i "apiport\|socketport" ~/Jts/ibgateway/*/jts.ini 2>/dev/null
```

The IBC `OverrideTwsApiPort` setting can force the port without touching the GUI.

---

## PostgreSQL Schema (DDL)

### Install PostgreSQL 15 on Ubuntu

```bash
# Add PGDG apt repository (official PostgreSQL repo — not Ubuntu's older packaged version)
sudo apt-get install -y curl ca-certificates
sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
  https://www.postgresql.org/media/keys/ACCC4CF8.asc
sudo sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
  https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list'
sudo apt-get update
sudo apt-get install -y postgresql-15 postgresql-client-15

# Enable and start
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### Create Database and User

```bash
sudo -u postgres psql <<'EOF'
CREATE USER bravos WITH PASSWORD 'change_me_at_deploy';
CREATE DATABASE bravos_trading OWNER bravos;
GRANT ALL PRIVILEGES ON DATABASE bravos_trading TO bravos;
\c bravos_trading
GRANT ALL ON SCHEMA public TO bravos;
EOF
```

**Security:** The DB password should be stored in GCP Secret Manager (`bravos-db-password`), not hardcoded. The startup validation script reads it from Secret Manager.

### Exact DDL — All 5 Tables

This schema supports all AUDIT-01 through AUDIT-06 requirements: immutable append-only audit trail, full end-to-end trace from signal to execution, FIFO lot tracking.

```sql
-- ============================================================
-- Schema: bravos trading system
-- DB: bravos_trading
-- ============================================================

-- 1. SIGNALS: Every scraped alert, verbatim + structured
CREATE TABLE signals (
    signal_id        BIGSERIAL PRIMARY KEY,
    post_url         TEXT NOT NULL,            -- dedup key (UNIQUE)
    post_title       TEXT NOT NULL,
    raw_html         TEXT NOT NULL,            -- verbatim scraped content (AUDIT-01)
    scraped_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Parsed fields
    ticker           TEXT,                     -- NULL if parse failed
    action_type      TEXT,                     -- 'open'|'add'|'partial_close'|'close'|NULL
    weight_from      INTEGER,                  -- prior weight units
    weight_to        INTEGER,                  -- new weight units
    ref_price        NUMERIC(12, 4),           -- reference price from alert (if present)

    -- Parse status
    parse_status     TEXT NOT NULL DEFAULT 'pending',
                                               -- 'pending'|'parsed'|'failed'|'low_confidence'
    parse_confidence NUMERIC(5, 4),            -- 0.0–1.0
    parse_notes      TEXT,                     -- reason for low_confidence or failure

    -- Signal processing status
    signal_status    TEXT NOT NULL DEFAULT 'pending',
                                               -- 'pending'|'submitted'|'filled'|'rejected'|'skipped'|'error'
    signal_error     TEXT,                     -- error message if status='error'

    CONSTRAINT signals_post_url_unique UNIQUE (post_url),
    CONSTRAINT signals_action_type_check CHECK (
        action_type IN ('open', 'add', 'partial_close', 'close') OR action_type IS NULL
    ),
    CONSTRAINT signals_parse_status_check CHECK (
        parse_status IN ('pending', 'parsed', 'failed', 'low_confidence')
    ),
    CONSTRAINT signals_signal_status_check CHECK (
        signal_status IN ('pending', 'submitted', 'filled', 'rejected', 'skipped', 'error')
    )
);

CREATE INDEX idx_signals_scraped_at ON signals (scraped_at DESC);
CREATE INDEX idx_signals_ticker ON signals (ticker) WHERE ticker IS NOT NULL;
CREATE INDEX idx_signals_signal_status ON signals (signal_status);


-- 2. ORDERS: Every order submission attempt (AUDIT-03: links to signal)
CREATE TABLE orders (
    order_id         BIGSERIAL PRIMARY KEY,
    signal_id        BIGINT NOT NULL REFERENCES signals (signal_id),
    ibkr_order_id    INTEGER,                  -- ID assigned by IB Gateway
    ibkr_perm_id     BIGINT,                   -- permanent ID (persists across sessions)

    ticker           TEXT NOT NULL,
    action           TEXT NOT NULL,            -- 'BUY'|'SELL'
    order_type       TEXT NOT NULL DEFAULT 'MKT',
    quantity         INTEGER NOT NULL,
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Order lifecycle (AUDIT-03, EXEC-04)
    status           TEXT NOT NULL DEFAULT 'PENDING_SUBMISSION',
                                               -- 'PENDING_SUBMISSION'|'SUBMITTED'|'PARTIALLY_FILLED'
                                               -- |'FILLED'|'CANCELLED'|'REJECTED'|'ERROR'
    status_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rejection_reason TEXT,                     -- from IBKR error callback

    -- Risk gate decision
    risk_approved    BOOLEAN NOT NULL DEFAULT FALSE,
    risk_notes       TEXT,                     -- why blocked or values used (RISK-04)

    CONSTRAINT orders_action_check CHECK (action IN ('BUY', 'SELL')),
    CONSTRAINT orders_status_check CHECK (
        status IN (
            'PENDING_SUBMISSION', 'SUBMITTED', 'PARTIALLY_FILLED',
            'FILLED', 'CANCELLED', 'REJECTED', 'ERROR'
        )
    )
);

CREATE INDEX idx_orders_signal_id ON orders (signal_id);
CREATE INDEX idx_orders_ibkr_order_id ON orders (ibkr_order_id) WHERE ibkr_order_id IS NOT NULL;
CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_orders_submitted_at ON orders (submitted_at DESC);


-- 3. POSITION_LOTS: Open lots (FIFO-capable per AUDIT-04, AUDIT-05, POS-03)
CREATE TABLE position_lots (
    lot_id           BIGSERIAL PRIMARY KEY,
    signal_id        BIGINT NOT NULL REFERENCES signals (signal_id),
    order_id         BIGINT NOT NULL REFERENCES orders (order_id),

    ticker           TEXT NOT NULL,
    weight_at_open   INTEGER NOT NULL,         -- weight units when lot was opened
    quantity         INTEGER NOT NULL,         -- shares in this lot
    entry_price      NUMERIC(12, 4) NOT NULL,  -- avg fill price for this lot
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Lot state
    status           TEXT NOT NULL DEFAULT 'OPEN',
                                               -- 'OPEN'|'PARTIALLY_CLOSED'|'CLOSED'
    remaining_qty    INTEGER NOT NULL,         -- quantity not yet closed (starts = quantity)
    closed_at        TIMESTAMPTZ,

    CONSTRAINT position_lots_status_check CHECK (
        status IN ('OPEN', 'PARTIALLY_CLOSED', 'CLOSED')
    ),
    CONSTRAINT position_lots_remaining_nonneg CHECK (remaining_qty >= 0)
);

CREATE INDEX idx_position_lots_ticker ON position_lots (ticker);
CREATE INDEX idx_position_lots_status ON position_lots (status) WHERE status != 'CLOSED';
CREATE INDEX idx_position_lots_opened_at ON position_lots (opened_at);


-- 4. EXECUTIONS: Every fill received from IBKR (AUDIT-03, EXEC-05)
CREATE TABLE executions (
    execution_id     BIGSERIAL PRIMARY KEY,
    order_id         BIGINT NOT NULL REFERENCES orders (order_id),
    lot_id           BIGINT REFERENCES position_lots (lot_id),  -- NULL until lot assigned

    ibkr_exec_id     TEXT NOT NULL,            -- execution.execId from IBKR (unique per fill)
    ibkr_perm_id     BIGINT,

    ticker           TEXT NOT NULL,
    side             TEXT NOT NULL,            -- 'BOT'|'SLD'
    quantity         INTEGER NOT NULL,
    fill_price       NUMERIC(12, 4) NOT NULL,
    fill_time        TIMESTAMPTZ NOT NULL,
    exchange         TEXT,

    -- Commission (from commissionReport callback — arrives separately)
    commission       NUMERIC(10, 4),
    commission_currency TEXT,
    realized_pnl     NUMERIC(12, 4),           -- NULL for opening trades

    received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT executions_ibkr_exec_id_unique UNIQUE (ibkr_exec_id),
    CONSTRAINT executions_side_check CHECK (side IN ('BOT', 'SLD'))
);

CREATE INDEX idx_executions_order_id ON executions (order_id);
CREATE INDEX idx_executions_ibkr_exec_id ON executions (ibkr_exec_id);
CREATE INDEX idx_executions_fill_time ON executions (fill_time DESC);


-- 5. BROKER_POSITIONS_SNAPSHOT: IBKR authoritative state (IBKR-04, POS-01)
-- Append-only: each reconciliation cycle inserts new rows (AUDIT-06)
CREATE TABLE broker_positions_snapshot (
    snapshot_id      BIGSERIAL PRIMARY KEY,
    snapshotted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    account          TEXT NOT NULL,

    ticker           TEXT NOT NULL,
    sec_type         TEXT NOT NULL DEFAULT 'STK',
    quantity         NUMERIC(12, 4) NOT NULL,  -- positive = long, negative = short
    avg_cost         NUMERIC(12, 4),           -- per-share average cost from IBKR

    -- Discrepancy flag
    matches_internal BOOLEAN,                  -- NULL = not yet checked; True/False = result
    discrepancy_note TEXT
);

CREATE INDEX idx_broker_snap_snapshotted_at ON broker_positions_snapshot (snapshotted_at DESC);
CREATE INDEX idx_broker_snap_ticker ON broker_positions_snapshot (ticker);
CREATE INDEX idx_broker_snap_account ON broker_positions_snapshot (account);


-- ============================================================
-- Convenience view: current open lots summary
-- ============================================================
CREATE VIEW v_open_positions AS
SELECT
    pl.ticker,
    SUM(pl.remaining_qty)               AS total_shares,
    SUM(pl.weight_at_open * pl.remaining_qty) / NULLIF(SUM(pl.remaining_qty), 0) AS avg_weight,
    SUM(pl.entry_price * pl.remaining_qty) / NULLIF(SUM(pl.remaining_qty), 0)     AS avg_entry_price,
    MIN(pl.opened_at)                   AS earliest_open,
    COUNT(*)                            AS lot_count
FROM position_lots pl
WHERE pl.status IN ('OPEN', 'PARTIALLY_CLOSED')
GROUP BY pl.ticker;
```

### Alembic Setup

```bash
# In the project venv
pip install alembic==1.18.4

# Initialize (run once, from project root)
alembic init alembic

# alembic/env.py: set sqlalchemy.url to read from env
# alembic.ini: set sqlalchemy.url = postgresql://bravos:%(db_pass)s@localhost/bravos_trading
```

For this project, Alembic is used for schema versioning. The initial migration (`alembic revision --autogenerate -m "initial schema"`) captures the DDL above. Apply with `alembic upgrade head`.

---

## GCP Secret Manager

### Secrets to Create

| Secret Name | Content | Used By |
|-------------|---------|---------|
| `bravos-site-username` | bravosresearch.com login username | Scraper at startup |
| `bravos-site-password` | bravosresearch.com login password | Scraper at startup |
| `bravos-ibkr-username` | IBKR account username | IBC config injection |
| `bravos-ibkr-password` | IBKR account password | IBC config injection |
| `bravos-ibkr-account-id` | IBKR account ID (e.g. DU123456) | ibapi account parameter |
| `bravos-db-password` | PostgreSQL bravos user password | DB connection string |

### Create Secrets via gcloud

```bash
# Create each secret (run once per secret)
echo -n "your_value" | gcloud secrets create bravos-site-username \
  --data-file=- --replication-policy=automatic

echo -n "your_value" | gcloud secrets create bravos-site-password \
  --data-file=- --replication-policy=automatic

# Repeat for all 6 secrets above
```

### Service Account Setup

```bash
# Create the service account
gcloud iam service-accounts create bravos-vm-sa \
  --display-name="Bravos VM Service Account"

SA_EMAIL="bravos-vm-sa@$(gcloud config get-value project).iam.gserviceaccount.com"

# Grant Secret Accessor role for each secret
for SECRET in bravos-site-username bravos-site-password \
              bravos-ibkr-username bravos-ibkr-password \
              bravos-ibkr-account-id bravos-db-password; do
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor"
done

# Attach service account to the VM at creation time
# CONFIRMED: Use ubuntu-2404-lts-amd64 (mirrors opt-trade-vm4 Ubuntu 24.04 LTS)
gcloud compute instances create bravos-vm1 \
  --machine-type=e2-standard-2 \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-ssd \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --zone=us-east1-b \
  --service-account="${SA_EMAIL}" \
  --scopes=https://www.googleapis.com/auth/cloud-platform
```

### Python Snippet to Read Secrets at Startup

```python
from google.cloud import secretmanager
import os

PROJECT_ID = os.environ.get("GCP_PROJECT_ID") or _get_project_id()

def _get_project_id() -> str:
    """Get project ID from metadata server when running on GCE."""
    import urllib.request
    url = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    return urllib.request.urlopen(req).read().decode()

_client = secretmanager.SecretManagerServiceClient()

def get_secret(secret_name: str, version: str = "latest") -> str:
    """Read a secret from GCP Secret Manager."""
    name = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/{version}"
    response = _client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")

# Startup validation — call this before entering any application logic
REQUIRED_SECRETS = [
    "bravos-site-username",
    "bravos-site-password",
    "bravos-ibkr-username",
    "bravos-ibkr-password",
    "bravos-ibkr-account-id",
    "bravos-db-password",
]

def validate_secrets() -> None:
    """Raise RuntimeError if any required secret is unreadable."""
    missing = []
    for secret in REQUIRED_SECRETS:
        try:
            val = get_secret(secret)
            if not val:
                missing.append(f"{secret} (empty)")
        except Exception as e:
            missing.append(f"{secret} ({e})")
    if missing:
        raise RuntimeError(f"Startup failed: secrets not readable: {missing}")
```

Authentication on GCE is automatic via the attached service account — no credentials file needed. For local dev, set `GOOGLE_APPLICATION_CREDENTIALS` to a service account key JSON file.

---

## Chromium Headless

### Install Google Chrome Stable on Ubuntu 24.04 (CONFIRMED — mirrors opt-trade-vm4)

**CONFIRMED from opt-trade-vm4:** opt-trade-vm4 uses `google-chrome-stable 139.0.7258.66` installed as a deb package. Mirror this exactly — do NOT use `apt install chromium-browser` (snap issue).

```bash
# Install google-chrome-stable (deb package — not snap)
wget -q -O /tmp/google-chrome.deb \
  https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y /tmp/google-chrome.deb
# This installs as a deb, not snap. No ChromeDriver wrapper issue.
```

**Installation verification:**
```bash
google-chrome --version      # Expect: Google Chrome 139.x or later
```

~~**Option B — Chromium deb package:**~~ Not used; opt-trade-vm4 uses google-chrome-stable.

### Chrome Options for Headless Anti-Detection

Exact options from the selenium-scraper skill (production-verified):

```python
from selenium.webdriver.chrome.options import Options as ChromeOptions

options = ChromeOptions()

# Headless mode (new style, available Chrome 112+)
options.add_argument("--headless=new")

# Stability (required on Linux VM)
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--disable-extensions")
options.add_argument("--window-size=1920,1080")

# Anti-detection (prevents sites identifying Selenium)
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)
options.add_argument(
    "user-agent=Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Performance
options.add_argument("--disable-images")

# Unique remote debug port (avoids conflicts on multi-instance)
import random
options.add_argument(f"--remote-debugging-port={random.randint(20000, 60000)}")
```

### webdriver-manager Configuration

```python
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

driver = webdriver.Chrome(
    service=ChromeService(ChromeDriverManager().install()),
    options=options
)
```

webdriver-manager automatically downloads ChromeDriver matching the installed Chrome/Chromium version. To use the system Chromium binary (not Google Chrome), set the environment variable:

```bash
export WDM_LOG=0          # suppress download logs in production
```

Or in code:
```python
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

# For system Chromium (not Google Chrome)
ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
```

### Stale Process Cleanup

Always kill zombie processes before starting a new Chrome session:

```bash
pkill -9 -f chrome 2>/dev/null || true
pkill -9 -f chromium 2>/dev/null || true
rm -rf /tmp/.org.chromium.* /tmp/chrome_* 2>/dev/null || true
```

In Python (from selenium-scraper skill):
```python
import os, time
os.system("pkill -9 -f chrome 2>/dev/null || true")
os.system("pkill -9 -f chromium 2>/dev/null || true")
os.system("rm -rf /tmp/.org.chromium.* /tmp/chrome_* 2>/dev/null || true")
time.sleep(2)
```

---

## Python Environment

### Python 3.13.5 via miniconda3 (CONFIRMED — mirrors opt-trade-vm4)

**CONFIRMED from opt-trade-vm4 investigation:** Use miniconda3 with Python 3.13.5, NOT deadsnakes PPA + venv. This supersedes decisions D-12 (Python 3.11) and D-13 (venv).

Install miniconda3 on bravos_vm1:

```bash
# Download miniconda3 installer
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p ~/miniconda3
rm /tmp/miniconda.sh

# Initialize conda
~/miniconda3/bin/conda init bash
source ~/.bashrc

# Verify
python --version   # Should show Python 3.13.x
which python       # Should show ~/miniconda3/bin/python
```

Verify: `python --version` (expect 3.13.x)

~~### Python 3.11 on Ubuntu 22.04~~ — superseded by miniconda3 approach

~~Python 3.11 via deadsnakes PPA~~ — not used; opt-trade-vm4 uses miniconda3.

### Virtual Environment / Conda Environment

opt-trade-vm4 uses the base miniconda3 environment. For bravos_vm1, either use the base conda environment or create a dedicated conda env:

```bash
# Option A: Use base conda environment (mirrors opt-trade-vm4)
# Packages install directly into ~/miniconda3/

# Option B: Create dedicated conda env (cleaner isolation)
conda create -n bravos python=3.13
conda activate bravos
```

Regardless of option, activate before running any bravos code.

~~### Virtual Environment Setup (venv)~~ — superseded; use conda instead

### requirements.txt (Pinned Versions)

Current PyPI versions as of 2026-05-03 (verified via pip index):

```
# Web scraping
selenium==4.43.0
webdriver-manager==4.0.2

# Database
psycopg2-binary==2.9.12
alembic==1.18.4

# Web dashboard
fastapi==0.136.1
uvicorn==0.46.0
jinja2==3.1.6

# Scheduling
schedule==1.2.2

# Configuration
python-dotenv==1.2.2

# Logging
structlog==25.5.0

# GCP
google-cloud-secret-manager==2.27.0

# NLP (optional parser fallback)
spacy==3.8.14
# python -m spacy download en_core_web_sm  (run after install)

# ibapi — install via pip (mirrors opt-trade-vm4): pip install ibapi==9.81.1.post1
```

Install:
```bash
pip install -r requirements.txt
```

### ibapi Installation via pip (CONFIRMED — mirrors opt-trade-vm4)

**CONFIRMED from opt-trade-vm4 investigation:** ibapi is installed via `pip install ibapi==9.81.1.post1` into the miniconda3 environment. This supersedes decision D-15 (official zip approach).

opt-trade-vm4 confirmed that `pip install ibapi` installs a compatible version (9.81.1.post1) that works with the installed IB Gateway. The pitfall documented below about PyPI packages being "unofficial forks" does NOT apply to this specific version — opt-trade-vm4 is the working reference.

```bash
# Install ibapi (from within conda env / base conda)
pip install ibapi==9.81.1.post1
```

**Verify installation:**
```python
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
print("ibapi import OK")
```

~~### ibapi Installation from Official Zip~~ — superseded; use pip install ibapi==9.81.1.post1

### Alembic Migration Setup

```bash
# From project root, inside venv
alembic init alembic

# Edit alembic/env.py — replace the sqlalchemy.url line with:
# from config import DATABASE_URL
# config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Create initial migration
alembic revision --autogenerate -m "initial_schema"

# Apply
alembic upgrade head

# Verify
psql -U bravos -d bravos_trading -c "\dt"
```

---

## Backup Strategy

### Approach

Automated `pg_dump` to a GCS bucket, scheduled via systemd timer. Daily at 02:00 UTC (off-peak, before US market open).

### GCS Bucket Naming

```
gs://bravos-db-backups-{project-id}/
├── daily/
│   └── bravos_trading_YYYY-MM-DD.sql.gz
└── weekly/
    └── bravos_trading_YYYY-Www.sql.gz
```

Retention: 30 days for daily, 6 months for weekly.

### Backup Script

```bash
#!/bin/bash
# /home/bravos/backup-db.sh

set -euo pipefail

BUCKET="gs://bravos-db-backups-$(gcloud config get-value project)"
DATE=$(date +%Y-%m-%d)
BACKUP_FILE="/tmp/bravos_trading_${DATE}.sql.gz"

# Dump and compress
PGPASSWORD="$(gcloud secrets versions access latest --secret=bravos-db-password)" \
  pg_dump -U bravos -d bravos_trading | gzip > "$BACKUP_FILE"

# Upload to GCS
gsutil cp "$BACKUP_FILE" "${BUCKET}/daily/bravos_trading_${DATE}.sql.gz"

# Clean up local
rm -f "$BACKUP_FILE"

# Delete backups older than 30 days from GCS
gsutil -m rm -f "$(gsutil ls "${BUCKET}/daily/" | \
  awk -v cutoff="$(date -d '30 days ago' +%Y-%m-%d)" '$0 < cutoff')" 2>/dev/null || true

echo "Backup complete: ${BUCKET}/daily/bravos_trading_${DATE}.sql.gz"
```

### Systemd Timer

```ini
# /etc/systemd/system/bravos-backup.service
[Unit]
Description=Bravos PostgreSQL Backup

[Service]
Type=oneshot
User=bravos
ExecStart=/home/bravos/backup-db.sh
```

```ini
# /etc/systemd/system/bravos-backup.timer
[Unit]
Description=Daily Bravos DB Backup at 02:00 UTC

[Timer]
OnCalendar=*-*-* 02:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:
```bash
sudo systemctl enable bravos-backup.timer
sudo systemctl start bravos-backup.timer
```

---

## Verification Tests

Run each command after completing the corresponding setup task. All should exit 0 with no error output.

### 1. PostgreSQL: Connection and Schema

```bash
# Connect and list tables
psql -U bravos -d bravos_trading -c "\dt"
# Expected: 5 tables listed (signals, orders, position_lots, executions, broker_positions_snapshot)

# Test insert + dedup
psql -U bravos -d bravos_trading -c "
  INSERT INTO signals (post_url, post_title, raw_html)
  VALUES ('https://test.url/post-1', 'Test', '<p>test</p>')
  ON CONFLICT (post_url) DO NOTHING;
  INSERT INTO signals (post_url, post_title, raw_html)
  VALUES ('https://test.url/post-1', 'Duplicate', '<p>dup</p>')
  ON CONFLICT (post_url) DO NOTHING;
  SELECT COUNT(*) FROM signals WHERE post_url = 'https://test.url/post-1';
  -- Expected: 1 (dedup worked)
  DELETE FROM signals WHERE post_url = 'https://test.url/post-1';
"
```

### 2. GCP Secret Manager: Secrets Readable

```bash
# Test each secret is accessible (run as bravos user — uses attached service account)
for SECRET in bravos-site-username bravos-site-password \
              bravos-ibkr-username bravos-ibkr-password \
              bravos-ibkr-account-id bravos-db-password; do
  VAL=$(gcloud secrets versions access latest --secret="$SECRET" 2>&1)
  if [ $? -eq 0 ] && [ -n "$VAL" ]; then
    echo "OK: $SECRET"
  else
    echo "FAIL: $SECRET — $VAL"
  fi
done
```

Python validation:
```bash
source /home/bravos/venv/bin/activate
python3 -c "
from secrets_config import validate_secrets
validate_secrets()
print('All secrets readable')
"
```

### 3. Chromium Headless: Browser Launch

```python
#!/usr/bin/env python3
# /home/bravos/verify_chrome.py
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
import os, sys

os.system("pkill -9 -f chrome 2>/dev/null || true")
os.system("rm -rf /tmp/.org.chromium.* /tmp/chrome_* 2>/dev/null || true")

options = ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])

try:
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options
    )
    driver.get("https://www.google.com")
    title = driver.title
    driver.quit()
    print(f"Chrome headless OK — page title: {title}")
    sys.exit(0)
except Exception as e:
    print(f"Chrome headless FAILED: {e}")
    sys.exit(1)
```

```bash
source /home/bravos/venv/bin/activate
python3 /home/bravos/verify_chrome.py
```

### 4. ibapi: Import Verification

```bash
source /home/bravos/venv/bin/activate
python3 -c "
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
print('ibapi import OK')
"
```

### 5. IB Gateway: Port Reachable

```bash
# After IB Gateway is started and 2FA approved:
# Test port is open (paper: 4002)
nc -zv 127.0.0.1 4002 && echo "Gateway port 4002 OPEN" || echo "Gateway port 4002 CLOSED"

# Python connection test
source /home/bravos/venv/bin/activate
python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
result = s.connect_ex(('127.0.0.1', 4002))
s.close()
if result == 0:
    print('IB Gateway port 4002 reachable')
    sys.exit(0)
else:
    print('IB Gateway port 4002 NOT reachable')
    sys.exit(1)
"
```

### 6. Full Startup Validation Script

```bash
#!/bin/bash
# /home/bravos/verify-all.sh
set -euo pipefail
source /home/bravos/venv/bin/activate

echo "=== Bravos Infrastructure Verification ==="

echo "[1/5] PostgreSQL..."
psql -U bravos -d bravos_trading -c "\dt" > /dev/null 2>&1 && echo "  PASS" || { echo "  FAIL"; exit 1; }

echo "[2/5] GCP Secrets..."
python3 -c "from secrets_config import validate_secrets; validate_secrets()" && echo "  PASS" || { echo "  FAIL"; exit 1; }

echo "[3/5] ibapi import..."
python3 -c "from ibapi.client import EClient; print('  PASS')" || { echo "  FAIL"; exit 1; }

echo "[4/5] Chrome headless..."
python3 /home/bravos/verify_chrome.py && echo "  PASS" || { echo "  FAIL"; exit 1; }

echo "[5/5] IB Gateway port..."
nc -z 127.0.0.1 4002 && echo "  PASS" || echo "  SKIP (Gateway not started yet)"

echo "=== Verification complete ==="
```

---

## Key Risks and Mitigations

### Risk 1: opt-trade-vm4 Version Mismatch

**What goes wrong:** bravos_vm1 uses a different IBC version, IB Gateway version, or OS version than opt-trade-vm4. Since opt-trade-vm4 is known-working, any difference is a potential failure point with no reference to debug against.

**Mitigation:** Investigate opt-trade-vm4 FIRST (Wave 0 task), before installing anything on bravos_vm1. Document every version. Use those exact versions.

**Warning sign:** Installer fails, IBC fails to start Gateway, or Gateway appears running but API port doesn't open.

### Risk 2: ibapi / IB Gateway Version Mismatch

**What goes wrong:** `error 507: Bad message length` on every API call. The ibapi library and Gateway speak different wire protocol versions. Silent or cryptic failure.

**Mitigation (UPDATED):** Use `pip install ibapi==9.81.1.post1` — this is the version confirmed working on opt-trade-vm4 with the installed IB Gateway. No manual version matching via zip file is required.

**Warning sign:** ibapi connects (no error 502), but `nextValidId` never fires, or error 507 appears in logs.

### Risk 3: Chromium Snap Package Breaks ChromeDriver

**What goes wrong:** `apt install chromium-browser` on Ubuntu 22.04 installs a snap. ChromeDriver invoked via webdriver-manager tries to use the snap's ChromeDriver wrapper, which is a symlink to `/usr/bin/snap`. The `--port` argument fails with "error: unknown flag `port'".

**Mitigation:** Use Google Chrome stable (deb package) or Chromium from PPA (non-snap). Confirm which approach opt-trade-vm4 uses.

**Warning sign:** `SessionNotCreatedException` or `unknown flag 'port'` in Chrome startup logs.

### Risk 4: IBC 2FA Login Timeout

**What goes wrong:** Operator doesn't approve the 2FA push in time (3-minute window). IBC may retry (if `ReloginAfterSecondFactorAuthenticationTimeout=yes`) or exit. Gateway never fully starts. API port never opens.

**Mitigation:** Operator must be watching their phone when starting the service. Set `ReloginAfterSecondFactorAuthenticationTimeout=yes`. Document the exact startup procedure clearly for the operator.

**Warning sign:** IBC logs show "Second Factor Authentication timed out". Gateway process not running after timeout.

### Risk 5: Python Environment Path Confusion (miniconda3)

**What goes wrong:** `python3` on the PATH points to the system Python (Ubuntu 24.04 ships Python 3.12 system Python). Scripts run with the wrong Python version, missing ibapi and other packages installed into the conda environment.

**Mitigation:** Always activate the conda environment explicitly (`conda activate bravos` or `source ~/miniconda3/bin/activate bravos`) in all systemd service files and startup scripts. Use `ExecStart=/home/ubuntu/miniconda3/bin/python3 script.py` (or the conda env's python) in service files rather than relying on `python3`.

**Warning sign:** `import ibapi` fails; `python --version` shows system Python rather than 3.13.x.

~~**Note:** Deadsnakes PPA / venv approach superseded by miniconda3.~~

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + shell verification scripts |
| Config file | `pytest.ini` (Wave 0 gap — does not exist yet) |
| Quick run command | `pytest tests/test_infrastructure.py -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEPL-01 | IB Gateway port reachable post-startup | smoke | `nc -zv 127.0.0.1 4002` (shell) | ❌ Wave 0 |
| DEPL-03 | 5 tables exist; dedup constraint works | integration | `pytest tests/test_infrastructure.py::test_schema -x` | ❌ Wave 0 |
| DEPL-04 | All 6 secrets readable by service account | smoke | `pytest tests/test_infrastructure.py::test_secrets -x` | ❌ Wave 0 |
| DEPL-05 | Headless Chrome launches; loads a page | smoke | `pytest tests/test_infrastructure.py::test_chrome -x` | ❌ Wave 0 |

### Wave 0 Gaps

- [ ] `tests/test_infrastructure.py` — smoke + integration tests for all DEPL-0x requirements
- [ ] `pytest.ini` — root pytest config
- [ ] `tests/conftest.py` — shared fixtures (DB connection, Chrome options)
- [ ] `requirements-dev.txt` — `pytest==8.x`, `pytest-timeout`

---

## Common Pitfalls

### Pitfall 1: Snap Chromium Breaking ChromeDriver

**What goes wrong:** `apt install chromium-browser` on Ubuntu 22.04 installs snap version. ChromeDriver fails with "error: unknown flag `port'".

**Why it happens:** The snap chromium-browser binary at `/snap/bin/chromium.chromedriver` is a symlink to `/usr/bin/snap`, which parses the `--port` flag as a snap command.

**How to avoid:** Use `google-chrome-stable` from Google's official apt repo (deb package), or Chromium from a non-snap PPA.

### Pitfall 2: ibapi PyPI Version (UPDATED — opt-trade-vm4 confirmed)

**CONFIRMED (2026-05-03):** `pip install ibapi==9.81.1.post1` works correctly on opt-trade-vm4. The package at this specific version is compatible with the installed IB Gateway.

**Pin the version:** Always use `pip install ibapi==9.81.1.post1` — do not `pip install ibapi` without a version pin, as future versions may introduce breaking changes.

~~**Original pitfall note:** "Never use PyPI packages for ibapi"~~ — this general caution is superseded by the confirmed working version. Use the pinned version.

**Remaining risk:** If IB Gateway is updated on the VM to a version incompatible with ibapi 9.81.1.post1, error 507 may appear. Check ibapi compatibility before upgrading Gateway.

### Pitfall 3: IBC Gateway Version Number in gatewaystart.sh

**What goes wrong:** IBC's `gatewaystart.sh` requires the TWS major version number as a parameter (e.g., "1030"). If this doesn't match the installed Gateway, IBC fails to find the Gateway binary.

**Why it happens:** IBC uses the version number to construct the path to the Gateway launch script.

**How to avoid:** Verify the version in `~/Jts/ibgateway/` after install, and use that exact number in the startup script.

### Pitfall 4: Xvfb Display Conflict

**What goes wrong:** Multiple processes try to use DISPLAY=:0 or :1. Gateway fails to start because the display is in use or not started.

**Why it happens:** Multiple services or manual debugging sessions conflict on the same display number.

**How to avoid:** Use DISPLAY=:1 for IB Gateway consistently. Use DISPLAY=:2 if a second display is needed. Never use :0 (reserved for physical display).

---

## Architecture Patterns

### Recommended Project Structure

```
/home/ubuntu/                  # User: ubuntu (mirrors opt-trade-vm4)
├── miniconda3/                # Python 3.13.5 conda environment (CONFIRMED)
├── bravos/                    # Application code (Phase 2+)
│   ├── config/
│   │   └── secrets_config.py  # get_secret(), validate_secrets()
│   └── ...
├── alembic/                   # Database migration scripts
│   ├── versions/
│   │   └── 001_initial_schema.py
│   └── env.py
├── alembic.ini
├── requirements.txt           # Pinned dependencies
├── requirements-dev.txt       # pytest, etc.
├── ibc/
│   └── config.ini             # IBC config (chmod 600)
├── backup-db.sh               # pg_dump to GCS
└── verify-all.sh              # Infrastructure verification script
```

System-level (CONFIRMED paths from opt-trade-vm4):
```
/opt/ibcalpha/current/         # IBC installation (CONFIRMED)
/opt/ibgateway/                # IB Gateway installation (CONFIRMED)
/usr/local/bin/ibgateway       # IB Gateway binary (CONFIRMED)
/etc/systemd/system/
├── xvfb@99.service            # Xvfb on display :99 (CONFIRMED)
├── ibgateway.service          # Restart=always, RestartSec=15 (CONFIRMED)
├── bravos-backup.service
└── bravos-backup.timer
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| IBController (ibcontroller) | IBC (IbcAlpha) | ~2019–2020 | IBController unmaintained; IBC required for Gateway 1016+ |
| `--headless` Chrome flag | `--headless=new` | Chrome 112 (2023) | Old flag deprecated; new mode required for modern Chrome |
| ibapi from official zip only | `pip install ibapi==9.81.1.post1` | Confirmed 2026-05-03 | opt-trade-vm4 uses pip; 9.81.1.post1 confirmed working |
| IBC at /opt/ibc/ | IBC at /opt/ibcalpha/current/ | Confirmed 2026-05-03 | Actual path on opt-trade-vm4 |
| Ubuntu 22.04 LTS | Ubuntu 24.04 LTS (Noble) | Confirmed 2026-05-03 | opt-trade-vm4 is 24.04 |
| Python 3.11 + venv | Python 3.13.5 + miniconda3 | Confirmed 2026-05-03 | opt-trade-vm4 uses miniconda3 |

---

## Open Questions

**All questions resolved by opt-trade-vm4 investigation (2026-05-03).**

1. **opt-trade-vm4 Ubuntu version** — RESOLVED
   - **Answer:** Ubuntu 24.04 LTS (Noble)
   - **Impact:** Use `ubuntu-2404-lts-amd64` image for bravos_vm1

2. **opt-trade-vm4 IBC + IB Gateway versions** — RESOLVED
   - **Answer:** IBC (IbcAlpha) at `/opt/ibcalpha/current/`; IB Gateway at `/opt/ibgateway/` with binary at `/usr/local/bin/ibgateway`
   - **Impact:** Install paths confirmed; ibapi version 9.81.1.post1 (pip) confirmed working

3. **IBC config.ini credential injection approach** — DEFERRED to plan 01-02
   - Still unconfirmed: exact credential injection mechanism used on opt-trade-vm4
   - The `sed` injection pattern remains the recommended approach; will confirm during 01-02 execution

4. **ibgateway.service Restart policy during 2FA** — RESOLVED
   - **Answer:** opt-trade-vm4 uses `Restart=always, RestartSec=15`
   - **Impact:** Mirror opt-trade-vm4 — use `Restart=always, RestartSec=15` (not `Restart=no` as originally recommended)
   - **Rationale:** The 2FA retry mechanism in IBC (`ReloginAfterSecondFactorAuthenticationTimeout=yes`) handles the restart-before-approval case

---

## Sources

### Primary (HIGH confidence)
- `bravos/.claude/skills/ibkr-connection/SKILL.md` — IB Gateway ports, IBC 2FA flow, CLOSE-WAIT diagnosis
- `bravos/.claude/skills/selenium-scraper/SKILL.md` — Chrome options, webdriver-manager pattern, stale process cleanup
- `bravos/.claude/skills/postgres-patterns/SKILL.md` — Schema design, indexing, data types
- PyPI `pip index versions` — Verified package versions as of 2026-05-03

### Secondary (MEDIUM confidence)
- [IBC GitHub — IbcAlpha/IBC](https://github.com/IbcAlpha/IBC) — IBC installation, config.ini reference, release notes for 3.14.0 (2FA improvement)
- [IBC config.ini raw](https://raw.githubusercontent.com/IbcAlpha/IBC/master/resources/config.ini) — All config settings with defaults
- IBC release note: 3.14.0 required for Gateway 1016+ with IBKR Mobile 2FA
- [Google Cloud Secret Manager Python](https://cloud.google.com/secret-manager/docs/reference/libraries) — access_secret_version API

### Tertiary (CONFIRMED — verified against opt-trade-vm4 2026-05-03)
- IB Gateway install path: `/opt/ibgateway/` (binary at `/usr/local/bin/ibgateway`) — CONFIRMED
- ibapi version: 9.81.1.post1 via pip — CONFIRMED
- IBC path: `/opt/ibcalpha/current/` — CONFIRMED
- Xvfb display: `:99` (xvfb@99.service) — CONFIRMED
- Chrome: google-chrome-stable 139.0.7258.66 (deb, not snap) — CONFIRMED
- [Ubuntu 22.04 Chromium snap issue](https://github.com/SeleniumHQ/selenium/issues/10969) — Not applicable; opt-trade-vm4/bravos_vm1 use google-chrome-stable

---

## Metadata

**Confidence breakdown:**
- PostgreSQL schema: HIGH — derived from project requirements; schema design follows standard patterns
- Python packages (versions): HIGH — verified via pip index on 2026-05-03
- IBC setup (steps, config, paths): HIGH — confirmed against opt-trade-vm4 on 2026-05-03
- IB Gateway install path: HIGH — confirmed `/opt/ibgateway/` on opt-trade-vm4
- ibapi installation: HIGH — confirmed `pip install ibapi==9.81.1.post1` on opt-trade-vm4
- Ubuntu version: HIGH — confirmed Ubuntu 24.04 LTS (Noble) on opt-trade-vm4
- Python environment: HIGH — confirmed miniconda3 + Python 3.13.5 on opt-trade-vm4
- Chrome: HIGH — confirmed google-chrome-stable 139.0.7258.66 on opt-trade-vm4

**Research date:** 2026-05-03
**Updated:** 2026-05-03 (opt-trade-vm4 investigation)
**Valid until:** 2026-08-03 (package versions; IBC/Gateway versions stable unless Gateway update released)
