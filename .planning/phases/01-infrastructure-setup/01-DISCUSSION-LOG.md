# Phase 1: Infrastructure Setup - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-02
**Phase:** 01-infrastructure-setup
**Areas discussed:** VM spec & GCP config, IB Gateway management, PostgreSQL location, Python environment

---

## VM Spec & GCP Config

| Option | Description | Selected |
|--------|-------------|----------|
| Match opt-trade-vm4 exactly | Same machine type, disk, region | |
| Right-size for this workload | Choose based on actual needs | ✓ |

**Follow-up — machine type:**

| Option | Description | Selected |
|--------|-------------|----------|
| e2-standard-2 (2 vCPU, 8GB) | Half cost of opt-trade-vm4 | ✓ |
| e2-standard-4 (4 vCPU, 16GB) | Match opt-trade-vm4 exactly | |
| e2-medium (1 vCPU, 4GB) | Minimum / cheapest | |

**User's choice:** e2-standard-2 — step down from opt-trade-vm4's e2-standard-4 to reduce cost
**Notes:** opt-trade-vm4 spec confirmed as e2-standard-4 (4 vCPU, 16GB RAM). User provided this after initial discussion.

**Region:**

| Option | Description | Selected |
|--------|-------------|----------|
| Same region as opt-trade-vm4 | Consistent setup | |
| US East (closest to NYSE/IBKR) | Lower latency to market | ✓ |
| Don't mind — you decide | Claude picks | |

---

## IB Gateway Management

| Option | Description | Selected |
|--------|-------------|----------|
| IBC | Automates Gateway startup and 2FA handling | ✓ |
| Manual startup by operator | SSH + start manually each day | |
| Whatever opt-trade-vm4 uses | Mirror existing approach | |

**2FA handling:**

| Option | Description | Selected |
|--------|-------------|----------|
| IBC handles automatically | Suppresses or stores credentials | |
| Manual approval on first start | Operator approves once; persists all day | |
| Mirror opt-trade-vm4 exactly | Investigate and replicate | ✓ |

**User's notes:** opt-trade-vm4 uses mobile notification for 2FA. Goal is maximum automation — operator approves mobile notification once at startup, IBC handles the rest.

---

## PostgreSQL Location

| Option | Description | Selected |
|--------|-------------|----------|
| On the VM (same as opt-trade-vm4) | Simple, no network latency, lower cost | ✓ (mirror pattern) |
| GCP Cloud SQL | Managed, HA, scalable | |
| Whatever opt-trade-vm4 uses | Mirror exactly | |

**User's notes:** Mirror how opt-trade-vm4 does it, but with a separate database and tables — bravos_vm1 must have its own isolated DB.

**Backups:**

| Option | Description | Selected |
|--------|-------------|----------|
| Automated pg_dump to GCS bucket | Daily dump, cheap, restorable | |
| VM snapshot | GCP disk snapshots | |
| You decide | Claude picks approach | ✓ |

---

## Python Environment

| Option | Description | Selected |
|--------|-------------|----------|
| Mirror opt-trade-vm4 | Same version + venv | |
| Python 3.11 + venv | Stable, good ibapi/Selenium compat | ✓ |
| Python 3.12 + venv | Latest stable | |

**Dependencies:**

| Option | Description | Selected |
|--------|-------------|----------|
| requirements.txt with pinned versions | Simple, explicit, reproducible | ✓ |
| pyproject.toml + pip | More modern | |
| Mirror opt-trade-vm4 | Match existing approach | |

---

## Claude's Discretion

- Exact Ubuntu LTS version
- pg_dump backup schedule and GCS bucket naming
- Firewall/VPC configuration details
- ChromeDriver version management approach

## Deferred Ideas

None
