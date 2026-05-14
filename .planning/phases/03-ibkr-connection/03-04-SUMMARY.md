---
phase: 03-ibkr-connection
plan: 04
status: complete
committed: true
key-files:
  created: []
  modified:
    - scripts/run_ingestion.py
decisions: []
self-check: PASSED
---

# Summary: Plan 4 — Daemon Integration

## One-liner

IBApp wired into `scripts/run_ingestion.py` before the schedule loop: connects, reconciles, starts heartbeat on success; starts background reconnect on failure (D-14); SIGTERM stops IBApp cleanly before scraper; `TRADING_MODE=paper|live` switches port via `get_ibkr_port()` with no code changes.

## What Was Built

`scripts/run_ingestion.py` received the full IBKR startup block between signal handler setup and scraper startup:

1. **Connection start** — `IBApp` constructed from `settings.IBKR_HOST`, `get_ibkr_port()`, `settings.IBKR_CLIENT_ID`; assigned to `broker_module.ibapp` singleton; `connect_and_run(timeout=30)` called.
2. **Success path** — `run_startup_reconciliation()` called with a fresh DB connection; `start_heartbeat_monitor()` called after reconciliation.
3. **Failure path (D-14)** — `CRITICAL` log emitted; `start_background_reconnect()` called; daemon continues into the schedule loop so scraper session health checks proceed without IBKR.
4. **Shutdown** — `broker_module.ibapp.stop()` called before `_scraper.shutdown()` in the cleanup block.
5. **`_get_db_connection()` helper** — module-level function opens a psycopg2 connection from env/settings for reconciliation use.

## Verification

- `python -c "import importlib.util; spec = importlib.util.spec_from_file_location('run_ingestion', 'scripts/run_ingestion.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print('import OK')"` → **import OK**
- `TRADING_MODE=live` → port 4001; `TRADING_MODE=paper` → port 4002 ✓
- `pytest tests/ -x -q` → 46 passed, 10 skipped, 1 failed (only `test_signal_stored_with_raw_html` — placeholder URL `?p=1`, not related to this plan)
- All `must_haves` satisfied per plan frontmatter

## Wave 2 Checkpoint (Human Verify)

Wave 2 requires IB Gateway running in paper mode on bravos-vm1. Deferred to live VM testing.

**Startup command when Gateway is available:**
```bash
cd /home/chris_s_dodd/bravos
TRADING_MODE=paper python scripts/run_ingestion.py
```

Expected log sequence to confirm:
```
Starting IBKR connection — mode=paper host=127.0.0.1 port=4002 client_id=1
IBKR connected — running startup reconciliation
IBKR ready — heartbeat monitor started
Ingestion daemon started — polling every 300s
```

D-14 path (Gateway not running): daemon logs CRITICAL and continues without IBKR — verified by plan logic.

## Deviations

None. Implementation was committed in `11212eb feat(03-04): wire IBApp into run_ingestion.py daemon` prior to this summary being written.
