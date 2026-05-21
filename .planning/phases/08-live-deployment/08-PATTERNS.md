# Phase 8: Live Deployment - Pattern Map

**Mapped:** 2026-05-21
**Files analyzed:** 5 new/modified files
**Analogs found:** 4 / 5 (1 has no codebase analog — stub script)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `infra/bravos-trading.service` | config (systemd unit) | request-response (process lifecycle) | `infra/ibgateway.service` | exact |
| `infra/bravos-gmail.service` | config (systemd unit) | request-response (process lifecycle) | `infra/ibgateway.service` | role-match |
| `infra/env.example` | config (env template) | — | none in codebase | no analog |
| `scripts/run_ingestion.py` | service (daemon entry) | event-driven + schedule | self (modify) | self |
| `scripts/run_gmail.py` | service (daemon entry) | event-driven (stub) | `scripts/run_ingestion.py` | role-match (stub) |

---

## Pattern Assignments

### `infra/bravos-trading.service` (systemd unit, process lifecycle)

**Analog:** `infra/ibgateway.service`

**Full analog** (`infra/ibgateway.service`, lines 1–18):
```ini
[Unit]
Description=IB Gateway (PAPER) via IBCAlpha
After=network.target xvfb@99.service
Requires=xvfb@99.service

[Service]
Type=simple
ExecStart=/opt/ibcalpha/start_ib_gateway.sh
WorkingDirectory=/opt/ibcalpha/current
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
User=chris_s_dodd

[Install]
WantedBy=multi-user.target
```

**Divergence notes for `bravos-trading.service`:**
- `After=` must include `ibgateway.service cloud-sql-proxy.service` (D-07); do NOT add `Requires=ibgateway.service` — IBApp handles reconnect in-process (RESEARCH anti-pattern)
- `ExecStart=` must use the absolute miniconda3 path: `/home/chris_s_dodd/miniconda3/bin/python /home/chris_s_dodd/bravos/scripts/run_ingestion.py` — systemd does not inherit PATH or conda activation
- Add `WorkingDirectory=/home/chris_s_dodd/bravos` — needed for `sys.path.insert(0, ...)` in run_ingestion.py to resolve the repo root
- Add `EnvironmentFile=/etc/bravos/env` (D-08) — ibgateway.service does not have this; it is new for bravos services
- No `Requires=` on ibgateway or cloud-sql-proxy — ordering constraint only

**Supporting analog** (`infra/cloud-sql-proxy.service`, lines 1–18) — absolute ExecStart path pattern:
```ini
[Service]
Type=simple
ExecStart=/home/chris_s_dodd/cloud-sql-proxy \
  crafty-water-453519-d7:us-central1:bravos-db \
  --port=5432
Restart=always
RestartSec=5
User=chris_s_dodd
StandardOutput=journal
StandardError=journal
```

---

### `infra/bravos-gmail.service` (systemd unit, process lifecycle)

**Analog:** `infra/ibgateway.service` (same as trading service, different ExecStart and After= set)

**Divergence notes for `bravos-gmail.service`:**
- `ExecStart=` points to `scripts/run_gmail.py` (the stub, not run_ingestion.py)
- `After=` includes `ibgateway.service cloud-sql-proxy.service` per D-07 (non-harmful over-constraint — Gmail poller has no real IBKR dependency, but D-07 is a locked decision)
- Same `Restart=always`, `RestartSec=15`, `User=chris_s_dodd`, `StandardOutput=journal`, `EnvironmentFile=/etc/bravos/env` as bravos-trading.service
- Description should note placeholder status: `Description=Bravos Gmail Poller (placeholder — INGST-V2-01)`

---

### `infra/env.example` (config template, no data flow)

**No analog exists** in the codebase. No `.env.example` or similar template file was found anywhere in the repo.

**Pattern source:** `bravos/config/settings.py` (lines 5–31) — all `os.environ.get()` calls define what belongs in the env file.

**Env vars to document** (extracted from `bravos/config/settings.py` lines 5–31):
```python
# Lines 5-8: DB connection — host/port/name/user have defaults; only PASSWORD has no default
DB_HOST = os.environ.get("BRAVOS_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("BRAVOS_DB_PORT", "5432"))
DB_NAME = os.environ.get("BRAVOS_DB_NAME", "bravos_trading")
DB_USER = os.environ.get("BRAVOS_DB_USER", "bravos")

# Line 21: Trading mode — the live cutover toggle (D-04/D-05)
TRADING_MODE = os.environ.get("TRADING_MODE", "paper")  # "paper" or "live"

# Lines 24-27: Risk controls — configurable per deployment
MAX_OPEN_POSITIONS   = int(os.environ.get("MAX_OPEN_POSITIONS", "20"))
MAX_ALLOCATION_PCT   = float(os.environ.get("MAX_ALLOCATION_PCT", "0.25"))
DAILY_LOSS_THRESHOLD = float(os.environ.get("DAILY_LOSS_THRESHOLD", "-5000.0"))
WEIGHT_PCT_PER_UNIT  = float(os.environ.get("WEIGHT_PCT_PER_UNIT", "0.05"))

# Line 30: Notification recipient
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
```

**BRAVOS_DB_PASSWORD** is read directly in `scripts/run_ingestion.py` (line 67), not in settings.py, but is the only credential that MUST be in the env file (it cannot be fetched from GCP Secret Manager at psycopg2 connect time):
```python
# run_ingestion.py line 67:
password = os.environ.get("BRAVOS_DB_PASSWORD", "change_me_at_deploy")
```

**File format convention** (derived from RESEARCH.md Pattern 3 and settings.py analysis): Plain `KEY=value` lines, comments with `#`. No shell quoting needed for non-special values. Mode 600, owned root:root on deployment.

---

### `scripts/run_ingestion.py` (MODIFY — add nightly Chrome restart schedule job)

**Analog:** self — this file is modified, not created.

**Existing schedule pattern to follow** (`scripts/run_ingestion.py`, lines 222–230):
```python
# Schedule the scrape cycle (session health check at same interval as alert polling)
schedule.every(SCRAPE_INTERVAL_SECONDS).seconds.do(run_cycle)

# Phase 4: daily circuit breaker reset at market open.
# 14:30 UTC = 09:30 ET (EST, UTC-5). During EDT (summer, UTC-4) this fires at
# 10:30 ET — 1 hour late. Known DST limitation; acceptable for v1.
# gate.reset() clears the daily loss accumulator so a new trading day is not
# blocked by the previous day's drawdown.
schedule.every().day.at("14:30").do(_gate.reset)
logger.info("Scheduled daily RiskGate reset at 14:30 UTC (09:30 ET winter)")
```

**Existing `_scraper` global and null-guard** (`scripts/run_ingestion.py`, lines 60, 92–95):
```python
_scraper: BravosScraper | None = None

def run_cycle():
    global _scraper
    if _scraper is None:
        logger.error("run_cycle called before scraper is initialized")
        return
```

**BravosScraper shutdown/reinit lifecycle** (verified from `bravos/ingestion/scraper.py`):
- `shutdown()` calls `driver.quit()` with exception swallowing
- `startup()` calls `get_secret()` (GCP API call), `setup_chrome_driver()`, `_login()`
- A new `BravosScraper()` instance has `driver=None` until `startup()` completes

**New function to add — `_restart_chrome_driver()`:**
- Must set `_scraper = None` BEFORE calling `old_scraper.shutdown()` — this activates the existing null-guard in `run_cycle()` during the restart window
- Must assign `_scraper = new_scraper` ONLY after `new_scraper.startup()` succeeds
- Must catch exceptions from both `shutdown()` and `startup()` independently
- Schedule string: `"06:00"` (UTC) — matching the UTC convention used by the existing `"14:30"` RiskGate reset job (14:30 UTC = 09:30 ET)

**Insertion point in `main()`:** After the existing `schedule.every().day.at("14:30")` call (line 229) and before `run_cycle()` is called immediately (line 234).

---

### `scripts/run_gmail.py` (NEW — Gmail poller stub)

**No exact analog exists.** This is a new daemon entry script with no Gmail polling implemented (INGST-V2-01 is out of scope). The closest analog by structure is `scripts/run_ingestion.py`.

**Analog:** `scripts/run_ingestion.py` — use its header structure, `sys.path.insert`, `logging.basicConfig`, logger naming convention.

**Header and import pattern** (`scripts/run_ingestion.py`, lines 1–53):
```python
#!/usr/bin/env python3
"""
Bravos Trading System — [Description].
[Docstring with usage and architecture note]
"""
import signal
import sys
import time
import logging
from pathlib import Path

# Ensure the repo root is on sys.path when running as `python scripts/run_*.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Configure logging (stdlib per research — not structlog)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.ingestion.daemon")
```

**Stub body pattern:** For a placeholder stub, the body does NOT need signal handlers, schedule jobs, or BravosScraper. It only needs to:
1. Log a startup message referencing the unimplemented feature and its tracking ID (INGST-V2-01)
2. Enter an infinite sleep loop to keep the process alive (so `Restart=always` does not thrash)

---

### `tests/test_deployment.py` (NEW — unit tests for nightly Chrome restart)

**Analog:** `tests/test_scraper.py` — uses `unittest.mock.patch` and `MagicMock`, no fixtures needed (pure unit tests).

**Mock/patch import pattern** (`tests/test_scraper.py`, lines 13–14):
```python
import pytest
from unittest.mock import MagicMock, patch
```

**Test structure pattern** (`tests/test_scraper.py`, lines 16–24):
```python
def test_startup_loads_credentials():
    from bravos.ingestion.scraper import BravosScraper
    with patch("bravos.ingestion.scraper.get_secret") as mock_secret:
        mock_secret.return_value = "test_value"
        s = BravosScraper()
        with patch("bravos.ingestion.scraper.setup_chrome_driver", return_value=MagicMock()):
            with patch.object(s, "_login", return_value=True):
                s.startup()
        assert mock_secret.call_count == 2
```

**Test file header pattern** (`tests/test_infrastructure.py`, lines 1–20):
```python
"""
[Description of what is tested].

Requirement coverage:
  DEPL-XX -> test_function_name
"""
```

**Three tests required for `_restart_chrome_driver()`** (from RESEARCH.md Validation Architecture):
1. `test_nightly_chrome_restart` — verifies `old_scraper.shutdown()` and `new_scraper.startup()` are called
2. `test_restart_sets_scraper_none_during_transition` — verifies `_scraper` is set to `None` before `shutdown()` is called (concurrent guard)
3. `test_restart_handles_startup_failure` — verifies that if `startup()` raises, the function does not re-raise (daemon continues with `_scraper = None`)

**Key patching target:** `scripts/run_ingestion` is a module, not a package. To test `_restart_chrome_driver`, import it via `importlib` or patch `scripts.run_ingestion._scraper` — however, since `run_ingestion.py` uses a module-level global, the cleanest approach is to import the module and call the function directly, using `patch` on `bravos.ingestion.scraper.BravosScraper`.

---

## Shared Patterns

### Logging Setup
**Source:** `scripts/run_ingestion.py`, lines 48–53
**Apply to:** `scripts/run_gmail.py`
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bravos.ingestion.daemon")
```
For `run_gmail.py`, use logger name `"bravos.gmail.daemon"`.

### sys.path Bootstrap
**Source:** `scripts/run_ingestion.py`, lines 35–36
**Apply to:** `scripts/run_gmail.py`
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```
This must appear before any `bravos.*` imports. Required because systemd `ExecStart` runs the script directly without activating the conda environment's path manipulation.

### systemd Unit Structure
**Source:** `infra/ibgateway.service` (all 18 lines) + `infra/cloud-sql-proxy.service`
**Apply to:** `infra/bravos-trading.service`, `infra/bravos-gmail.service`

Mandatory fields for any new Bravos service:
- `Type=simple` — long-running Python process
- `Restart=always` — auto-restart on any exit code
- `RestartSec=15` — 15s cooldown (matches ibgateway.service; cloud-sql-proxy uses 5s which is too fast for a Python startup that calls GCP Secret Manager)
- `User=chris_s_dodd` — non-root service user
- `StandardOutput=journal` + `StandardError=journal` — all output to journald
- `WorkingDirectory=/home/chris_s_dodd/bravos` — needed for relative path resolution
- `EnvironmentFile=/etc/bravos/env` — secrets injection (NEW: not present in ibgateway.service or cloud-sql-proxy.service)
- `WantedBy=multi-user.target` — standard for non-graphical services

### Schedule UTC Convention
**Source:** `scripts/run_ingestion.py`, line 229
**Apply to:** The new `schedule.every().day.at("06:00")` call in `run_ingestion.py`
```python
# 14:30 UTC = 09:30 ET (EST, UTC-5). During EDT (summer, UTC-4) this fires at
# 10:30 ET — 1 hour late. Known DST limitation; acceptable for v1.
schedule.every().day.at("14:30").do(_gate.reset)
```
New Chrome restart job must follow the same UTC convention: `"06:00"` = 01:00 ET winter. Include a matching DST limitation comment.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `infra/env.example` | config (env template) | — | No `.env.example` or environment template file exists anywhere in the repo; format derived from `settings.py` `os.environ.get()` calls and RESEARCH.md Pattern 3 |

---

## Metadata

**Analog search scope:** `infra/`, `scripts/`, `tests/`, `bravos/config/`
**Files read:** 10 (ibgateway.service, cloud-sql-proxy.service, xvfb@.service, run_ingestion.py, settings.py, conftest.py, test_infrastructure.py, test_scraper.py, 08-CONTEXT.md, 08-RESEARCH.md)
**Pattern extraction date:** 2026-05-21
