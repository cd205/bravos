# Phase 1: Infrastructure Setup — Key Decisions

**Created:** 2026-05-03
**Source:** opt-trade-vm4 investigation (plan 01-01, Task 2)

This file documents decisions made during Phase 1 execution that deviate from the original CONTEXT.md decisions or resolve previously open questions. These decisions are authoritative and supersede CONTEXT.md where noted.

---

## Decision DEV-01: Python 3.13.5 + miniconda3 (supersedes D-12 + D-13)

**Original decisions:**
- D-12: Python 3.11 (not 3.12)
- D-13: Virtual environment (`venv`) for isolation

**Confirmed reality on opt-trade-vm4:**
- Python 3.13.5 via miniconda3 at ~/miniconda3/
- No venv — conda environment (base or named env)

**Decision:** Mirror opt-trade-vm4 exactly. Use miniconda3 + Python 3.13 on bravos_vm1.

**Rationale:**
1. opt-trade-vm4 is the known-working reference; deviating introduces unknown compatibility risks
2. ibapi 9.81.1.post1 is already confirmed to work under Python 3.13 on opt-trade-vm4
3. Ubuntu 24.04 ships Python 3.12 system Python — using miniconda3 avoids any system Python confusion

**Impact on plans:**
- Plan 01-06 (Python environment): install miniconda3, not deadsnakes PPA + venv
- test_python_version stub in tests/test_infrastructure.py: assertion must be updated from `(3, 11)` to `(3, 13)` when the stub is un-skipped in plan 01-06

---

## Decision DEV-02: pip install ibapi==9.81.1.post1 (supersedes D-15)

**Original decision:**
- D-15: ibapi installed from IB's official developer portal zip (not PyPI) — version must match IB Gateway version

**Confirmed reality on opt-trade-vm4:**
- ibapi 9.81.1.post1 installed via pip into miniconda3 base environment
- Location: /home/chris_s_dodd/miniconda3/lib/python3.13/site-packages/ibapi
- Works correctly with the IB Gateway installed at /opt/ibgateway/

**Decision:** Mirror opt-trade-vm4. Use `pip install ibapi==9.81.1.post1` — no zip download required.

**Rationale:**
1. opt-trade-vm4 is the reference; the pip version is confirmed working
2. Eliminates the manual zip download + version matching complexity
3. Pinning to `==9.81.1.post1` ensures reproducibility

**Impact on plans:**
- Plan 01-06 (Python environment): replace zip-based install with `pip install ibapi==9.81.1.post1`
- requirements.txt comment: update from "NOT from PyPI" to pinned pip version

---

## Decision DEV-03: Ubuntu 24.04 LTS (Noble) (resolves Open Question 1)

**Original guidance:**
- D-04: "Ubuntu LTS — mirror opt-trade-vm4's OS"
- Open Question 1: "Is it 20.04 or 22.04?"

**Confirmed reality on opt-trade-vm4:**
- Ubuntu 24.04 LTS (Noble)

**Decision:** Use `ubuntu-2404-lts-amd64` image from `ubuntu-os-cloud` project when creating bravos_vm1.

**Rationale:**
1. D-04 explicitly says to mirror opt-trade-vm4's OS version
2. 24.04 LTS (Noble) has long-term support through April 2029

**VM creation command:**
```bash
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

**Impact on plans:**
- Plan 01-01 (VM provisioning): use ubuntu-2404-lts-amd64 (not ubuntu-2204-lts)
- All apt-based install commands may differ from Ubuntu 22.04 guides; verify on 24.04

---

## Decision DEV-04: ibgateway.service Restart=always (resolves Open Question 4)

**Original guidance:**
- Open Question 4: "Should the service be `Restart=no` (manual start only) or `Restart=on-failure` with limits?"
- Research.md recommendation: `Restart=no` (safer for 2FA wait)

**Confirmed reality on opt-trade-vm4:**
- ibgateway.service uses `Restart=always, RestartSec=15`
- User=ubuntu (system user, not login user)

**Decision:** Mirror opt-trade-vm4. Use `Restart=always, RestartSec=15` for ibgateway.service on bravos_vm1.

**Rationale:**
1. opt-trade-vm4 is the reference; its Restart policy is known-working in production
2. IBC's `ReloginAfterSecondFactorAuthenticationTimeout=yes` handles the 2FA retry — if the service restarts before 2FA is approved, IBC will re-initiate the 2FA push, and the operator can approve it in the next 3-minute window
3. `Restart=always` ensures the Gateway auto-recovers from unexpected crashes without operator intervention

**Impact on plans:**
- Plan 01-02 (IB Gateway + IBC): use `Restart=always, RestartSec=15` in ibgateway.service unit file
- Operator runbook: document that 2FA approval may be needed after any unexpected service restart

---

## Decision DEV-05: IBC path is /opt/ibcalpha/ (resolves Open Question 2 partially)

**Original guidance:**
- Research.md: IBC installs to `/opt/ibc/`

**Confirmed reality on opt-trade-vm4:**
- IBC (IbcAlpha) installed at `/opt/ibcalpha/current/`
- Startup script: `/opt/ibcalpha/start_ib_gateway.sh`
- Starts BOTH PAPER and LIVE gateways simultaneously
- Headless mode via `gatewaystart.sh -inline`

**Decision:** Install IBC to `/opt/ibcalpha/` on bravos_vm1. Mirror the path exactly.

**Rationale:**
1. opt-trade-vm4 is the reference; matching paths reduces confusion when using opt-trade-vm4 as a debugging reference
2. IBC docs don't mandate `/opt/ibc/` — it's a conventional default that was overridden on opt-trade-vm4

**Impact on plans:**
- Plan 01-02: install IBC to `/opt/ibcalpha/`, not `/opt/ibc/`
- ibgateway.service: `ExecStart=/opt/ibcalpha/start_ib_gateway.sh`
- All references to `/opt/ibc/` in RESEARCH.md updated to `/opt/ibcalpha/`

---

## Decision DEV-06: IB Gateway path is /opt/ibgateway/ (not ~/Jts/)

**Original guidance:**
- Research.md: "After install, the Gateway binary is at `~/Jts/ibgateway/<version>/ibgateway`"

**Confirmed reality on opt-trade-vm4:**
- IB Gateway installed at `/opt/ibgateway/`
- Binary at `/usr/local/bin/ibgateway`
- `~/Jts/` does NOT exist

**Decision:** Install IB Gateway to `/opt/ibgateway/` on bravos_vm1. Mirror opt-trade-vm4.

**Rationale:**
1. opt-trade-vm4 is the reference; using the same path eliminates any path-related startup script differences

**Impact on plans:**
- Plan 01-02: Gateway install target is `/opt/ibgateway/`
- IBC startup script uses `/opt/ibgateway` not `~/Jts/ibgateway/<version>/`

---

## Decision DEV-07: Xvfb on display :99 (confirmed)

**Confirmed reality on opt-trade-vm4:**
- Xvfb running on display :99 via xvfb@99.service (template service instance)
- ibgateway.service depends on xvfb@99.service

**Decision:** Use display :99 on bravos_vm1. Mirror opt-trade-vm4.

**Impact on plans:**
- Plan 01-02: `Environment=DISPLAY=:99` in ibgateway.service; `Requires=xvfb@99.service`

---

## Summary Table

| Decision | Supersedes | Change |
|----------|-----------|--------|
| DEV-01 | D-12, D-13 | Python 3.13.5 + miniconda3 (not 3.11 + venv) |
| DEV-02 | D-15 | pip install ibapi==9.81.1.post1 (not official zip) |
| DEV-03 | Open Question 1 | Ubuntu 24.04 LTS (not 22.04) |
| DEV-04 | Open Question 4 | ibgateway.service Restart=always (not Restart=no) |
| DEV-05 | Research.md IBC path | IBC at /opt/ibcalpha/ (not /opt/ibc/) |
| DEV-06 | Research.md Gateway path | IB Gateway at /opt/ibgateway/ (not ~/Jts/) |
| DEV-07 | Research.md Xvfb display | Display :99 (not :1) |

All decisions made by operator based on opt-trade-vm4 SSH investigation on 2026-05-03.
