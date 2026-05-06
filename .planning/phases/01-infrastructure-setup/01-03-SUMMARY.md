# Plan 01-03 Summary — IB Gateway + IBC

**Completed:** 2026-05-06
**Status:** DONE (pending 2FA checkpoint — Gateway startable but not yet started)

## What Was Built

**On bravos-vm1:**
- Xvfb running on display :99 (xvfb@99.service — active)
- IB Gateway 10.45 installed at /opt/ibgateway/
- IBC 3.19.0 installed at /opt/ibcalpha/current/
- /opt/ibcalpha/current/config.ini — configured with PLACEHOLDER credentials
- /opt/ibcalpha/start_ib_gateway.sh — startup script
- /etc/systemd/system/ibgateway.service — Restart=always, RestartSec=15, User=chris_s_dodd

**In repo:**
- infra/xvfb@.service
- infra/ibgateway.service
- infra/start-gateway.sh
- infra/ibc-config.ini (PLACEHOLDER credentials only — safe to commit)

## Deviations from Plan

- Gateway version: 10.45 (plan assumed 10.30.x — actual is newer)
- User in ibgateway.service: chris_s_dodd (opt-trade-vm4 used ubuntu system user — bravos-vm1 uses the login user)

## Human Checkpoint Pending

IB Gateway has NOT been started yet. Starting requires:
1. Credentials injected into config.ini from GCP Secret Manager (Plan 01-06)
2. Operator approves IBKR Mobile 2FA push
3. `sudo systemctl start ibgateway` or manual run of start_ib_gateway.sh

This checkpoint will be completed as part of Plan 01-07 (final verification).

## Commits

- 6a1ad43 — feat(01-03): add IB Gateway + IBC infra files
