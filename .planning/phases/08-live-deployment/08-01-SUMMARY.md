---
phase: "08"
plan: "01"
subsystem: infra
tags:
  - systemd
  - deployment
  - infra
  - env-config
dependency_graph:
  requires:
    - "infra/ibgateway.service (pattern source)"
    - "infra/cloud-sql-proxy.service (pattern source)"
    - "bravos/config/settings.py (env var inventory)"
    - "scripts/run_ingestion.py (BRAVOS_DB_PASSWORD usage at line 67)"
  provides:
    - "infra/bravos-trading.service — systemd unit for the trading daemon"
    - "infra/bravos-gmail.service — systemd unit for the Gmail poller (placeholder)"
    - "infra/env.example — template for /etc/bravos/env on bravos-vm1"
  affects:
    - "Plan 08-03 (live cutover): installs these files on bravos-vm1"
    - "Plan 08-02 (scripts): run_gmail.py stub is ExecStart target for bravos-gmail.service"
tech_stack:
  added: []
  patterns:
    - "systemd unit: Type=simple, Restart=always, RestartSec=15, User=chris_s_dodd, StandardOutput=journal"
    - "EnvironmentFile=/etc/bravos/env for secret injection (D-08)"
    - "After= without Requires= for soft dependency ordering (D-07)"
key_files:
  created:
    - infra/bravos-trading.service
    - infra/bravos-gmail.service
    - infra/env.example
  modified: []
decisions:
  - "D-07: After=ibgateway.service not Requires= — prevents systemd stopping the daemon during IB Gateway nightly restart, preserving IBApp auto-reconnect"
  - "D-08: EnvironmentFile=/etc/bravos/env for secret injection; file must be root:root mode 600 on bravos-vm1"
  - "Absolute miniconda3 Python path in ExecStart — systemd does not inherit PATH or conda activation"
  - "bravos-db-password listed in GCP Secret Manager but BRAVOS_DB_PASSWORD goes in env file — psycopg2 connects synchronously before async GCP SDK calls are possible"
metrics:
  duration: "3 minutes"
  completed_date: "2026-05-21"
  tasks_completed: 3
  files_created: 3
  files_modified: 0
---

# Phase 8 Plan 01: Systemd Units and Env Template Summary

Three infrastructure files created that form the OS-level service surface for Phase 8 live deployment: two systemd unit files (trading daemon and Gmail poller placeholder) and an env file template documenting every runtime-configurable variable.

## What Was Built

### infra/bravos-trading.service

Systemd unit for the long-running ingestion daemon (`scripts/run_ingestion.py`). Key properties:

- `After=network.target ibgateway.service cloud-sql-proxy.service` — starts after dependencies but does NOT use `Requires=`, so IB Gateway's nightly restart does not trigger a daemon stop
- `ExecStart=/home/chris_s_dodd/miniconda3/bin/python /home/chris_s_dodd/bravos/scripts/run_ingestion.py` — absolute path required because systemd does not inherit PATH or conda activation
- `EnvironmentFile=/etc/bravos/env` — secrets and tunables injected at process start by systemd
- `Restart=always RestartSec=15` — matches `ibgateway.service` pattern; 15s cooldown allows GCP Secret Manager calls during Python startup
- `WorkingDirectory=/home/chris_s_dodd/bravos` — required so `sys.path.insert(0, ...)` in run_ingestion.py resolves the repo root

### infra/bravos-gmail.service

Systemd unit for the Gmail poller (`scripts/run_gmail.py`). Description line explicitly flags placeholder status and `INGST-V2-01` ticket so operators reading `systemctl status` immediately understand this is a stub service. All directives are identical to `bravos-trading.service` for uniform operational surface.

### infra/env.example

Template for `/etc/bravos/env` on bravos-vm1. Contains every variable read via `os.environ.get()` in `settings.py` plus `BRAVOS_DB_PASSWORD` (read directly in `run_ingestion.py` line 67). Key properties:

- `TRADING_MODE=paper` is the safe default; live cutover changes this single line
- `BRAVOS_DB_PASSWORD=` left empty — must be populated before service start
- Risk control defaults match `settings.py` exactly so the file documents both variable name and operational value
- Bravos site credentials, IBKR account credentials, and SMTP password are explicitly excluded — they flow through GCP Secret Manager via `get_secret()` in `secrets_config.py`
- Header documents `sudo chown root:root && sudo chmod 600` requirement (mitigates T-08-01)

## Decisions Implemented

| Decision | Applied Where | Rationale |
|----------|---------------|-----------|
| D-07: After= not Requires= | Both .service files | Requires=ibgateway.service would cause systemd to stop the trading daemon during the IB Gateway nightly restart, defeating IBApp auto-reconnect (D-01) |
| D-08: EnvironmentFile=/etc/bravos/env | Both .service files | Secrets and tunables injected by systemd; file created on bravos-vm1 in Plan 03 |
| Absolute miniconda3 Python path | Both ExecStart lines | systemd provides no PATH or conda activation; /usr/bin/python3 would fail with ImportError: No module named 'ibapi' |

## Operator Notes for Plan 03 (Live Deployment)

```bash
# 1. Create the env file from template
sudo mkdir -p /etc/bravos
sudo cp /home/chris_s_dodd/bravos/infra/env.example /etc/bravos/env
sudo chown root:root /etc/bravos/env
sudo chmod 600 /etc/bravos/env

# 2. Fill in BRAVOS_DB_PASSWORD and ALERT_EMAIL
sudo nano /etc/bravos/env   # or preferred editor via sudo

# 3. Symlink the unit files
sudo ln -sf /home/chris_s_dodd/bravos/infra/bravos-trading.service /etc/systemd/system/
sudo ln -sf /home/chris_s_dodd/bravos/infra/bravos-gmail.service /etc/systemd/system/

# 4. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable bravos-trading.service bravos-gmail.service
sudo systemctl start bravos-trading.service bravos-gmail.service

# 5. Verify
systemctl status bravos-trading.service bravos-gmail.service
journalctl -u bravos-trading.service -f
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

**infra/bravos-gmail.service** — `ExecStart` points to `scripts/run_gmail.py` which does not yet exist. This is intentional and flagged in the unit Description. Plan 08-02 creates the stub script; the service will fail to start until that plan is complete. This is the expected state during Phase 8 wave 1.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced beyond what is documented in the plan's threat model (T-08-01 through T-08-05). The env.example file explicitly documents the mode-600 requirement for T-08-01 mitigation.

## Self-Check: PASSED

Files confirmed:
- FOUND: infra/bravos-trading.service
- FOUND: infra/bravos-gmail.service
- FOUND: infra/env.example

Commits confirmed:
- 7d9c83d: feat(08-01): add bravos-trading.service systemd unit
- 8c229b6: feat(08-01): add bravos-gmail.service systemd unit (placeholder)
- 9c0ea39: feat(08-01): add infra/env.example environment file template
