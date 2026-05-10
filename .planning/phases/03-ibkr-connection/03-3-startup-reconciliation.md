# Plan 3: Startup Reconciliation

## Goal

Add startup reconciliation to `bravos/broker/connection.py`. After this plan, calling `ibapp.run_startup_reconciliation(db_conn)` fetches positions, open orders, and account summary from IBKR, writes a snapshot to `broker_positions_snapshot`, and logs WARNING for any quantity mismatch between IBKR and the `position_lots` table. DB state is never overwritten (D-08). Reconciliation blocks until all three callbacks complete or a 30s timeout fires.

This plan unskips the `03-3` tests from Plan 1's Wave 0.

## Requirements

- **IBKR-03** — on startup, reconcile positions + open orders + account summary with DB before entering the scrape/execute loop; write snapshot; log mismatches; never overwrite DB

## Wave 0: Test Stubs

Stubs for this plan were written in Plan 1's Wave 0 (`tests/test_broker.py`, tests marked `reason="plan: 03-3"`). Confirm they exist and are skipped:

```bash
pytest tests/test_broker.py -k "03-3" -q
```

Expected: tests collected, all skipped.

---

## Wave 1: EWrapper Reconciliation Callbacks

### Task 1.1 — Add reconciliation callbacks to IBApp

**File:** `bravos/broker/connection.py`

Add the following EWrapper callback methods. These accumulate data from IBKR into instance lists/dicts; the `*End` callbacks set threading.Events so the calling code knows when each stream is complete. No blocking, no DB calls — those go in Wave 2.

**Critical rule:** Never block inside EWrapper callbacks. These fire on the `ibkr-api` thread. Only set Events and append to lists here.

**`position(self, account: str, contract, position: float, avgCost: float) -> None`**
- If `contract.secType != "STK"`: return (equities only — v1 scope).
- Append to `self._positions`:
  ```python
  {"ticker": contract.symbol, "position": int(position), "avg_cost": avgCost}
  ```

**`positionEnd(self) -> None`**
- Call `self._positions_done.set()`.
- Log INFO: "reqPositions complete — N positions received" (len of `self._positions`).

**`openOrder(self, orderId: int, contract, order, orderState) -> None`**
- Append to `self._open_orders`:
  ```python
  {
      "order_id": orderId,
      "ticker": contract.symbol,
      "action": order.action,
      "quantity": order.totalQuantity,
      "status": orderState.status,
  }
  ```

**`openOrderEnd(self) -> None`**
- Call `self._orders_done.set()`.
- Log INFO: "reqOpenOrders complete — N open orders received".

**`accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None`**
- `self._account_summary[tag] = value` — store value string by tag name. Tags requested: `NetLiquidation`, `TotalCashValue`, `AvailableFunds`.

**`accountSummaryEnd(self, reqId: int) -> None`**
- Call `self._summary_done.set()`.
- Log INFO: "reqAccountSummary complete — N tags received".

**Verify (Wave 1):**
```bash
pytest tests/test_broker.py -k "test_position_end_sets_event or test_position_callback or test_open_order_end or test_open_order_callback or test_account_summary_end" -q
```
These 5 tests must pass (unskip to verify).

---

## Wave 2: `run_startup_reconciliation` + helper functions

### Task 2.1 — Add `run_startup_reconciliation` method to IBApp

**File:** `bravos/broker/connection.py`

**`run_startup_reconciliation(self, db_conn, timeout: float = 30) -> bool`**

Implementation steps (exact order matters — clear Events BEFORE issuing requests):

1. Clear all accumulators: `self._positions.clear()`, `self._open_orders.clear()`, `self._account_summary.clear()`.
2. Clear all Events: `self._positions_done.clear()`, `self._orders_done.clear()`, `self._summary_done.clear()`.
3. Issue requests concurrently (all three in sequence, then wait):
   - `self.reqPositions()`
   - `self.reqAllOpenOrders()` — use `reqAllOpenOrders()` (not `reqOpenOrders()`) to capture manually-placed TWS orders as well. This is the reconciliation path; Phase 4 order tracking uses scoped `reqOpenOrders()`.
   - `self.reqAccountSummary(REQ_ID_ACCOUNT_SUMMARY, "All", "NetLiquidation,TotalCashValue,AvailableFunds")`
4. Wait for all three events:
   ```python
   all_done = (
       self._positions_done.wait(timeout) and
       self._orders_done.wait(timeout) and
       self._summary_done.wait(timeout)
   )
   ```
5. If `not all_done`: log ERROR "Reconciliation timed out — partial data used for snapshot".
6. Call `_write_position_snapshot(db_conn, self._positions)` — always, even on partial data.
7. Call `_reconcile_against_db(db_conn, self._positions, self._open_orders)` — always.
8. Return `all_done`.

### Task 2.2 — Add module-level helper functions

**File:** `bravos/broker/connection.py` (module-level, below the IBApp class)

**`_write_position_snapshot(db_conn, positions: list[dict]) -> None`**

- Open a cursor, INSERT one row per position into `broker_positions_snapshot`.
- Columns to populate: `ticker`, `position`, `avg_cost`, `snapshot_at` (use `NOW()`).
- Do NOT populate `market_value` — nullable, no live prices at reconciliation time.
- SQL:
  ```sql
  INSERT INTO broker_positions_snapshot (ticker, position, avg_cost, snapshot_at)
  VALUES (%s, %s, %s, NOW())
  ```
- Commit after all inserts.
- Log INFO: "Wrote N position rows to broker_positions_snapshot".
- If `positions` is empty: still commit (no-op INSERT loop), log INFO with count 0.

**`_reconcile_against_db(db_conn, ibkr_positions: list[dict], ibkr_orders: list[dict]) -> None`**

- Build IBKR position map: `ibkr_pos_map = {p["ticker"]: p["position"] for p in ibkr_positions}`.
- Query DB for open lots:
  ```sql
  SELECT ticker, SUM(quantity) FROM position_lots WHERE lot_closed_at IS NULL GROUP BY ticker
  ```
- `db_open = {row[0]: row[1] for row in cursor.fetchall()}`.
- For each `(ticker, db_qty)` in `db_open.items()`:
  - `ibkr_qty = ibkr_pos_map.get(ticker, 0)`.
  - If `db_qty != ibkr_qty`: log WARNING:
    ```
    RECONCILE MISMATCH ticker=AAPL db_open_qty=100 ibkr_qty=80 — operator review required
    ```
- For each `(ticker, qty)` in `ibkr_pos_map.items()`:
  - If `ticker not in db_open` and `qty != 0`: log WARNING:
    ```
    RECONCILE IBKR HAS POSITION NOT IN DB ticker=AAPL ibkr_qty=100 — operator review required
    ```
- Do NOT write to `position_lots` under any circumstances (D-08).
- Log INFO at end: "Reconciliation complete — DB not modified (D-08)".

**Verify (Wave 2):**
```bash
pytest tests/test_broker.py -k "03-3" -q
```
All 03-3 tests must pass:
- `test_position_end_sets_event`
- `test_position_callback_appends_stk_positions`
- `test_open_order_end_sets_event`
- `test_open_order_callback_appends_orders`
- `test_account_summary_end_sets_event`
- `test_position_snapshot_written` (requires `db_connection` fixture — needs DB running)
- `test_reconcile_mismatch_logs_warning`
- `test_reconcile_no_db_write_on_mismatch`

---

## Verification

After both waves:

```bash
# All 03-3 tests pass
pytest tests/test_broker.py -k "03-3" -q

# Full test suite green
pytest tests/ -x -q
```

**Integration check (requires DB):**
```bash
pytest tests/test_broker.py::test_position_snapshot_written -v
```
Must pass with DB running (Cloud SQL Auth Proxy on 127.0.0.1:5432).

**Manual spot-check (no Gateway required):** Instantiate IBApp, call `app.positionEnd()` directly, assert `app._positions_done.is_set()` is True. Confirms callback wiring works without a real IBKR connection.
