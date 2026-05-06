# Plan 01-05 Summary — PostgreSQL + Schema

**Completed:** 2026-05-06
**Status:** DONE

## What Was Built

**Cloud SQL (not on-VM — architecture change):**
- Instance: bravos-db (PostgreSQL 16, db-g1-small, us-central1-a)
- Primary IP: 34.28.22.94
- Connection name: crafty-water-453519-d7:us-central1:bravos-db
- Database: bravos_trading, user: bravos
- All 5 tables applied and verified: signals, orders, position_lots, executions, broker_positions_snapshot

**On bravos-vm1:**
- Cloud SQL Auth Proxy v2.14.2 at ~/cloud-sql-proxy
- cloud-sql-proxy.service — active, listens on 127.0.0.1:5432
- postgresql-client installed (psql 16)

**In repo:**
- infra/schema.sql — full DDL for all 5 tables
- infra/cloud-sql-proxy.service — systemd service definition
- tests: schema tests un-skipped (DEPL-03)
- conftest.py: db_connection fixture updated to 127.0.0.1:5432

## Key Architecture Deviation

**Original plan:** PostgreSQL installed on-VM
**Actual:** Cloud SQL managed instance — data persists independently of VM, accessible from anywhere, automated backups enabled (03:00 UTC)

**Why:** User preference — data survives VM stop/start/deletion.

## Commits

- 8c77c64 — feat(01-05): Cloud SQL + schema — 5 tables applied, proxy configured
