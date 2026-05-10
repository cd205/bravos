# Plan 2: Heartbeat Thread + Reconnect Logic

## Goal

Add the heartbeat monitor thread and the force-reconnect state machine to `bravos/broker/connection.py`. After this plan, the IBApp can detect a dead connection via either the 60s heartbeat or error codes 504/1100, and will automatically reconnect with exponential backoff (5 attempts: 5s, 10s, 20s, 40s, 80s; then 60s forever). The guard against concurrent reconnect races is also implemented here.

This plan unskips the `03-2` tests created in Plan 1's Wave 0.

## Requirements

- **IBKR-01** — heartbeat every 60s; currentTime() response within 10s; no operator intervention
- **IBKR-02** — CLOSE-WAIT detection via error codes + heartbeat timeout; force-reconnect with 5s CLOSE-WAIT drain; exponential backoff

## Wave 0: Test Stubs

No new test file — stubs for this plan were written in Plan 1's Wave 0 (`tests/test_broker.py`, tests marked `reason="plan: 03-2"`). Confirm they exist and are skipped:

```bash
pytest tests/test_broker.py -k "03-2" -q
```

Expected: 5 tests collected, all skipped.

---

## Wave 1: Heartbeat Monitor Thread

### Task 1.1 — Add `start_heartbeat_monitor` and `_heartbeat_loop` to IBApp

**File:** `bravos/broker/connection.py`

Add the following methods to the IBApp class. These integrate with the existing `_connected`, `_stop_event`, `_last_heartbeat_at`, and `reqCurrentTime()` already in place from Plan 1.

**`start_heartbeat_monitor(self) -> None`**
- Creates and starts a daemon thread named `"ibkr-heartbeat"` running `self._heartbeat_loop`.
- Stores reference in `self._heartbeat_thread`.
- Must be called AFTER `connect_and_run()` succeeds — it is called by `run_ingestion.py` explicitly (Plan 4).

**`_heartbeat_loop(self) -> None`**
- Loop: `while not self._stop_event.wait(timeout=HEARTBEAT_INTERVAL):`
- On each iteration:
  1. If `not self._connected.is_set()`: `continue` (skip heartbeat if disconnected — reconnect thread handles recovery).
  2. Call `self.reqCurrentTime()` — fires `currentTime()` callback which updates `_last_heartbeat_at`.
  3. `time.sleep(HEARTBEAT_TIMEOUT)` — wait 10s for the response.
  4. Compute `elapsed = time.monotonic() - self._last_heartbeat_at`.
  5. If `elapsed > HEARTBEAT_TIMEOUT`: log WARNING with elapsed time, call `self._trigger_reconnect("heartbeat_timeout")`.
- Note: `_stop_event.wait(timeout=HEARTBEAT_INTERVAL)` returns True when stop is set, so the `while not` exits cleanly on shutdown.

**Verify (Wave 1):**
```bash
pytest tests/test_broker.py -k "test_current_time_updates_last_heartbeat_at or test_heartbeat_timeout_triggers_reconnect" -q
```
These two 03-2 tests must pass (unskip them for this verification step, then restore skips if not ready to permanently enable).

---

## Wave 2: Error Code Handler + Reconnect State Machine

### Task 2.1 — Add `_trigger_reconnect`, `_reconnect_loop`, `start_background_reconnect` to IBApp

**File:** `bravos/broker/connection.py`

The `error()` method in Plan 1 calls `self._trigger_reconnect(...)` — that method must now be implemented. Also implement the reconnect loop itself.

**`_trigger_reconnect(self, reason: str) -> None`**
- Acquire `self._recon_lock`.
- If `self._reconnecting is True`: return immediately (guard against concurrent triggers).
- Set `self._reconnecting = True`.
- Release lock.
- Start a daemon thread named `"ibkr-reconnect"` running `self._reconnect_loop(reason)`.
- Non-blocking — returns immediately so the calling context (error() callback or heartbeat thread) is not blocked.

**`_reconnect_loop(self, reason: str) -> None`**
- This is a background daemon thread.
- `attempt = 0`
- `while not self._stop_event.is_set():`
  1. `delay = _RETRY_DELAYS[attempt] if attempt < len(_RETRY_DELAYS) else 60`
  2. Log INFO: attempt number, reason, delay seconds.
  3. Clear `self._connected`.
  4. Try `self.disconnect()` inside bare `try/except Exception: pass`.
  5. `time.sleep(5)` — CLOSE-WAIT drain window (D-06).
  6. `time.sleep(max(0, delay - 5))` — remaining backoff.
  7. Call `self.connect_and_run()` (no timeout arg — uses default 30s).
  8. If successful: log INFO "reconnected on attempt N", set `self._reconnecting = False` (under lock), return.
  9. `attempt += 1`.
  10. If `attempt == len(_RETRY_DELAYS)` (i.e., just failed attempt 5): log CRITICAL "reconnect failed after 5 attempts — retrying every 60s forever".
  11. Loop continues — `delay` becomes 60 forever after attempt 5.
- On `_stop_event` exit: set `self._reconnecting = False` (under lock).

**`start_background_reconnect(self) -> None`**
- Calls `self._trigger_reconnect("initial_connect_failed")`.
- Used by Plan 4 when the initial connection attempt fails (D-14 startup failure mode).

**Verify (Wave 2):**
```bash
pytest tests/test_broker.py -k "03-2" -q
```
All 03-2 tests must pass:
- `test_current_time_updates_last_heartbeat_at`
- `test_heartbeat_timeout_triggers_reconnect`
- `test_error_504_triggers_reconnect`
- `test_error_1100_triggers_reconnect`
- `test_error_2104_is_ignored`
- `test_trigger_reconnect_does_not_spawn_duplicate_thread`
- `test_reconnect_backoff_delays`

---

## Verification

After both waves:

```bash
# All 03-2 tests pass
pytest tests/test_broker.py -k "03-2" -q

# No regressions in 03-1 tests (unskip manually to check or keep skipped — suite green)
pytest tests/test_broker.py -q

# Full test suite still passes
pytest tests/ -x -q
```

**Behavioral check (manual — no Gateway required):** Instantiate IBApp, call `nextValidId(1)` to simulate connected, then call `app.error(reqId=-1, errorCode=504, errorString="test")` and confirm a thread named `"ibkr-reconnect"` appears in `threading.enumerate()` within 1s (with `_RETRY_DELAYS[0]` sleep it won't connect, which is expected in test environment).
