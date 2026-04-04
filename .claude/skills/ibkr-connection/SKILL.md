---
name: ibkr-connection
description: >
  General-purpose reference for the Interactive Brokers Python ibapi library:
  EWrapper/EClient architecture, market data, historical bars, positions,
  account data, contract building for any instrument type, order placement
  for all common order types, execution and commission data, error codes,
  threading model, and IB Gateway setup. Applicable to any ibapi project
  regardless of strategy or instrument type.
triggers:
  - IBKR
  - IB Gateway
  - ibapi
  - EWrapper
  - EClient
  - Interactive Brokers API
  - reqPositions
  - reqMktData
  - placeOrder
  - reqContractDetails
  - TWS API
---

# Interactive Brokers ibapi — General Reference

Patterns for any ibapi-based project. Instrument-agnostic.

---

## 1. Connection Architecture

Two patterns — choose based on complexity:

### Pattern A: Separate Classes (recommended for larger projects)

```python
import queue, threading
from ibapi.wrapper import EWrapper
from ibapi.client import EClient

class IBWrapper(EWrapper):
    def __init__(self):
        EWrapper.__init__(self)
        self.data_queue = queue.Queue()
        self.next_order_id = None

    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self.data_queue.put(("connected", orderId))

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        self.data_queue.put(("error", (reqId, errorCode, errorString)))


class IBClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)


class IBApp:
    def __init__(self, host="127.0.0.1", port=4002, client_id=1):
        self.wrapper = IBWrapper()
        self.client = IBClient(self.wrapper)
        self.host = host
        self.port = port
        self.client_id = client_id

    def connect(self, timeout=30):
        self.client.connect(self.host, self.port, self.client_id)
        thread = threading.Thread(target=self.client.run, daemon=True)
        thread.start()
        # Wait for nextValidId (confirms connection)
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = self.wrapper.data_queue.get(timeout=1)
                if msg[0] == "connected":
                    return True
            except queue.Empty:
                pass
        return False

    def disconnect(self):
        self.client.disconnect()
```

### Pattern B: Combined Class (simpler, fewer files)

```python
from ibapi.wrapper import EWrapper
from ibapi.client import EClient
import threading

class IBApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)   # pass self as the wrapper
        self.next_order_id = None
        self.connected = False

    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self.connected = True

    def connect_and_run(self, host="127.0.0.1", port=4002, client_id=1):
        self.connect(host, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        # Wait for connected flag
        import time
        for _ in range(30):
            if self.connected:
                return True
            time.sleep(1)
        return False
```

### Threading rules
- `client.run()` (or `self.run()`) **blocks** — always call it in a daemon thread
- All EWrapper callbacks fire on the API thread — use a `queue.Queue` or flags to communicate back to the main thread
- Never call `client.run()` from the main thread or you'll block all logic

### Connection handshake
```
client.connect() → TCP socket → server sends nextValidId callback
nextValidId fires → confirms API is ready → store orderId for future orders
```

---

## 2. Market Data

### Request streaming quotes
```python
from ibapi.contract import Contract

contract = Contract()
contract.symbol = "AAPL"
contract.secType = "STK"
contract.exchange = "SMART"
contract.currency = "USD"

req_id = 1001
client.reqMktData(req_id, contract, "", False, False, [])
# genericTickList: "" = standard ticks; "100,101,104,106" = specific extras
# snapshot: False = streaming; True = one-time snapshot (cancels automatically)
# regulatorySnapshot: False (True = paid regulatory snapshot)
```

### Receive ticks via callback
```python
def tickPrice(self, reqId, tickType, price, attrib):
    # tickType reference:
    # 1  = Bid
    # 2  = Ask
    # 4  = Last
    # 6  = High (daily)
    # 7  = Low (daily)
    # 9  = Close (previous close)
    # 14 = Open (daily)
    # 37 = Mark price
    # 66 = Delayed bid
    # 67 = Delayed ask
    # 68 = Delayed last
    # 75 = Delayed open
    # 76 = Delayed close
    pass

def tickSize(self, reqId, tickType, size):
    # 0 = Bid size, 3 = Ask size, 5 = Last size, 8 = Volume
    pass
```

### Set data type (call before reqMktData)
```python
client.reqMarketDataType(3)
# 1 = Live (requires subscription)
# 2 = Frozen (last available live)
# 3 = Delayed (~15 min, free)
# 4 = Delayed frozen
```

### Cancel and clean up
```python
client.cancelMktData(req_id)
```

---

## 3. Historical Data

```python
import datetime

client.reqHistoricalData(
    reqId=2001,
    contract=contract,
    endDateTime="",           # "" = now; or "20240101 16:00:00 US/Eastern"
    durationStr="5 D",        # "N S/D/W/M/Y"  (Seconds/Days/Weeks/Months/Years)
    barSizeSetting="1 hour",  # see table below
    whatToShow="TRADES",      # see table below
    useRTH=1,                 # 1 = regular trading hours only; 0 = all hours
    formatDate=1,             # 1 = string date; 2 = epoch seconds
    keepUpToDate=False,       # True = live updating bars (streaming)
    chartOptions=[]
)
```

### barSizeSetting options
`"1 secs"`, `"5 secs"`, `"15 secs"`, `"30 secs"`,
`"1 min"`, `"2 mins"`, `"3 mins"`, `"5 mins"`, `"15 mins"`, `"30 mins"`,
`"1 hour"`, `"2 hours"`, `"4 hours"`,
`"1 day"`, `"1 week"`, `"1 month"`

### whatToShow options
`TRADES`, `MIDPOINT`, `BID`, `ASK`, `BID_ASK`,
`HISTORICAL_VOLATILITY`, `OPTION_IMPLIED_VOLATILITY`,
`SCHEDULE` (trading hours), `AGGTRADES`

### Callbacks
```python
def historicalData(self, reqId, bar):
    # bar.date, bar.open, bar.high, bar.low, bar.close
    # bar.volume, bar.wap, bar.barCount
    pass

def historicalDataEnd(self, reqId, start, end):
    # All bars received
    pass

def historicalDataUpdate(self, reqId, bar):
    # Only fires when keepUpToDate=True
    pass
```

---

## 4. Positions & Account Data

### Request all positions (all accounts)
```python
client.reqPositions()
# Fires position() for each holding, then positionEnd()

def position(self, account, contract, position, avgCost):
    # account: account string (e.g. "DU123456")
    # contract: Contract object (symbol, secType, conId, etc.)
    # position: quantity (positive = long, negative = short)
    # avgCost: average cost per unit (for options: per-share, not per-contract)
    pass

def positionEnd(self):
    # All positions delivered
    pass

client.cancelPositions()   # stop position updates
```

### Request account data (single account)
```python
client.reqAccountUpdates(True, "DU123456")   # subscribe
# Fires updateAccountValue(), updatePortfolio(), accountDownloadEnd()

def updateAccountValue(self, key, val, currency, accountName):
    # key examples: "NetLiquidation", "TotalCashValue", "GrossPositionValue",
    # "MaintMarginReq", "AvailableFunds", "BuyingPower"
    pass

def updatePortfolio(self, contract, position, marketPrice, marketValue,
                    averageCost, unrealizedPNL, realizedPNL, accountName):
    pass

def accountDownloadEnd(self, accountName):
    pass

client.reqAccountUpdates(False, "DU123456")  # unsubscribe
```

### Multi-account summary
```python
client.reqAccountSummary(9001, "All", "$LEDGER")
# tags: "NetLiquidation,TotalCashValue,AvailableFunds" or "$LEDGER" for all

def accountSummary(self, reqId, account, tag, value, currency):
    pass

def accountSummaryEnd(self, reqId):
    pass
```

---

## 5. Contract Building

### Field reference by instrument type

| Field | STK | OPT | FUT | CASH (FX) | BOND | CFD |
|-------|-----|-----|-----|-----------|------|-----|
| `symbol` | ticker | underlying | ticker | base ccy | CUSIP/ISIN | ticker |
| `secType` | `"STK"` | `"OPT"` | `"FUT"` | `"CASH"` | `"BOND"` | `"CFD"` |
| `exchange` | `"SMART"` | `"SMART"` | `"CME"` | `"IDEALPRO"` | `"SMART"` | `"SMART"` |
| `currency` | `"USD"` | `"USD"` | `"USD"` | quote ccy | `"USD"` | `"USD"` |
| `lastTradeDateOrContractMonth` | — | `"YYYYMMDD"` | `"YYYYMM"` | — | maturity | — |
| `strike` | — | float | — | — | — | — |
| `right` | — | `"C"` or `"P"` | — | — | — | — |
| `multiplier` | — | `"100"` | varies | — | — | — |
| `localSymbol` | optional | optional | optional | `"EUR.USD"` | optional | — |

### Code examples
```python
# Stock
stk = Contract()
stk.symbol = "AAPL"; stk.secType = "STK"
stk.exchange = "SMART"; stk.currency = "USD"

# Option
opt = Contract()
opt.symbol = "AAPL"; opt.secType = "OPT"
opt.exchange = "SMART"; opt.currency = "USD"
opt.lastTradeDateOrContractMonth = "20240119"  # no dashes
opt.strike = 180.0; opt.right = "C"; opt.multiplier = "100"

# Futures
fut = Contract()
fut.symbol = "ES"; fut.secType = "FUT"
fut.exchange = "CME"; fut.currency = "USD"
fut.lastTradeDateOrContractMonth = "202403"

# FX (spot)
fx = Contract()
fx.symbol = "EUR"; fx.secType = "CASH"
fx.exchange = "IDEALPRO"; fx.currency = "USD"
```

### Validate a contract (get conId and confirm it exists)
```python
client.reqContractDetails(req_id, contract)

def contractDetails(self, reqId, contractDetails):
    # contractDetails.contract.conId  — use this for combo orders
    # contractDetails.contract.symbol
    # contractDetails.minTick, contractDetails.longName
    pass

def contractDetailsEnd(self, reqId):
    # If contractDetails was never called → contract not found
    pass
```

---

## 6. Order Placement

### Core Order fields
```python
from ibapi.order import Order

order = Order()
order.action = "BUY"           # "BUY" or "SELL"
order.orderType = "LMT"        # see order types below
order.totalQuantity = 10       # number of shares/contracts
order.lmtPrice = 150.00        # for LMT, STP LMT
order.auxPrice = 149.00        # stop price for STP and STP LMT; trail amt for TRAIL
order.tif = "DAY"              # "DAY", "GTC", "IOC", "GTD", "OPG", "FOK"
order.transmit = True          # False = stage but don't send
order.outsideRth = False       # True = allow pre/post market
```

### Order type recipes
```python
# Market order
order.orderType = "MKT"

# Limit order
order.orderType = "LMT"
order.lmtPrice = 150.00

# Stop order
order.orderType = "STP"
order.auxPrice = 148.00        # stop trigger price

# Stop-limit order
order.orderType = "STP LMT"
order.lmtPrice = 149.50        # limit price after trigger
order.auxPrice = 149.00        # stop trigger price

# Market on close
order.orderType = "MOC"

# Limit on close
order.orderType = "LOC"
order.lmtPrice = 150.00

# Trailing stop (amount)
order.orderType = "TRAIL"
order.auxPrice = 2.00          # trail amount in price units

# Trailing stop (percent)
order.orderType = "TRAIL"
order.trailingPercent = 1.0    # 1% trail
```

### Place and cancel
```python
order_id = wrapper.next_order_id   # from nextValidId callback
client.placeOrder(order_id, contract, order)

client.cancelOrder(order_id, "")   # second arg = manual order cancel time
```

### Refresh valid order ID
```python
client.reqIds(-1)   # fires nextValidId again with current value
```

---

## 7. Order Status & Tracking

### orderStatus callback
```python
def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
    # status values:
    # "PreSubmitted" — held locally, not yet sent
    # "Submitted"    — sent to exchange
    # "Filled"       — fully filled
    # "PartiallyFilled" — some fills, waiting for rest
    # "Cancelled"    — cancelled
    # "Inactive"     — rejected or expired
    pass

def openOrder(self, orderId, contract, order, orderState):
    # orderState.status, orderState.commission, orderState.warningText
    pass
```

### Query existing orders
```python
client.reqAllOpenOrders()       # all open orders (all clients)
client.reqOpenOrders()          # open orders for this client only
client.reqCompletedOrders(False) # today's completed orders; True = API-submitted only

# Both fire openOrder() + orderStatus() per order, then openOrderEnd()
def openOrderEnd(self):
    pass
```

---

## 8. Executions & Commissions

### Request executions
```python
from ibapi.execution import ExecutionFilter

filt = ExecutionFilter()
# Optional filters — leave blank for all:
filt.clientId = 0       # 0 = all clients
filt.acctCode = ""      # "" = all accounts
filt.time = ""          # "YYYYMMDD HH:MM:SS" = since this time
filt.symbol = ""
filt.secType = ""       # "STK", "OPT", etc.
filt.exchange = ""
filt.side = ""          # "BOT" or "SLD"

client.reqExecutions(req_id, filt)
```

### Callbacks
```python
def execDetails(self, reqId, contract, execution):
    # execution.execId     — unique execution ID
    # execution.orderId    — linked order ID
    # execution.permId     — permanent order ID (persists across sessions)
    # execution.side       — "BOT" or "SLD"
    # execution.shares     — quantity
    # execution.price      — fill price
    # execution.time       — "YYYYMMDD  HH:MM:SS"
    # execution.exchange
    # execution.liquidation — True if forced liquidation
    pass

def execDetailsEnd(self, reqId):
    pass

def commissionReport(self, commissionReport):
    # commissionReport.execId        — links back to execDetails
    # commissionReport.commission    — commission paid
    # commissionReport.currency
    # commissionReport.realizedPNL   — NaN if not a closing trade
    # commissionReport.yield_        — for bonds
    pass
```

**Pair executions to commissions** by matching `execution.execId == commissionReport.execId`.

---

## 9. Error Code Reference

### Informational — safe to ignore
| Code | Meaning |
|------|---------|
| 2104 | Market data farm connection OK |
| 2106 | HMDS data farm connection OK |
| 2119 | Market data connection inactive (outside hours) |
| 2158 | Sec-def data farm connection OK |

### Connection errors
| Code | Meaning |
|------|---------|
| 502 | Connection refused — TWS/Gateway not running |
| 501 | Already connected — duplicate clientId on this port |
| 504 | Not connected — call `connect()` first |
| 507 | Bad message length — version mismatch |

### Contract errors
| Code | Meaning |
|------|---------|
| 200 | No security definition found — wrong symbol/expiry/strike/exchange |
| 354 | Requested market data not subscribed — need data subscription |
| 321 | Error validating request — check contract fields |

### Order errors
| Code | Meaning |
|------|---------|
| 201 | Order rejected — check price, quantity, account buying power |
| 110 | Price does not conform to min tick size |
| 103 | Duplicate order ID — increment orderId |
| 399 | Order message error — read errorString for detail |

### General error callback
```python
def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
    if errorCode in (2104, 2106, 2119, 2158):
        return  # informational, ignore
    if reqId == -1:
        print(f"System error {errorCode}: {errorString}")
    else:
        print(f"Request {reqId} error {errorCode}: {errorString}")
```

---

## 10. Threading & Request ID Management

### Daemon thread lifecycle
```python
# CORRECT: run() in daemon thread
thread = threading.Thread(target=client.run, daemon=True)
thread.start()
# daemon=True means thread dies when main program exits

# WRONG: never do this
client.run()   # blocks everything — your code after this line never runs
```

### Queue-based communication (main ↔ API thread)
```python
import queue

# In wrapper callbacks (API thread):
def tickPrice(self, reqId, tickType, price, attrib):
    self.data_queue.put(("tick", reqId, tickType, price))

# In main thread:
try:
    msg = wrapper.data_queue.get(timeout=10)
except queue.Empty:
    print("Timeout waiting for data")
```

### Request ID management
```python
class IBWrapper(EWrapper):
    def __init__(self):
        self._next_req_id = 1
        self._lock = threading.Lock()

    def get_next_req_id(self) -> int:
        with self._lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            return req_id
```

### clientId uniqueness
- Each connection to the same port must use a **unique clientId**
- TWS/Gateway tracks clientIds — reusing one while another is connected gives error 501
- Use a fixed ID per application component, or randomise: `random.randint(1, 999)`

---

## 11. Gateway & TWS Setup

### Port defaults

| Mode | TWS | IB Gateway |
|------|-----|------------|
| Live | 7496 | 4001 |
| Paper | 7497 | 4002 |

**Gateway is preferred for automation** — smaller footprint, no GUI, stays logged in.

### IBC (headless automation)
IBC (Interactive Brokers Controller) automates login and 2FA for IB Gateway:
- Reads credentials from a `.ini` config file
- Handles the login dialog on startup
- Config key settings: `IbLoginId`, `IbPassword`, `TradingMode` (`paper`/`live`), `SecondFactorAuthenticationTimeout`

### 2FA flow
1. Gateway starts → IBC fills login form → sends 2FA push to IBKR Mobile app
2. User approves on phone → gateway completes login → API port opens
3. Poll for API port readiness (try connecting; error 502 = not ready yet)

### CLOSE-WAIT connections
**Symptom**: API calls hang or timeout; gateway appears running but won't accept new connections.

**Cause**: Python client disconnected uncleanly (killed process, exception, timeout). TCP socket left in CLOSE-WAIT state on the gateway side.

**Diagnose**:
```bash
ss -anp | grep :4002 | grep CLOSE-WAIT   # paper gateway
ss -anp | grep :4001 | grep CLOSE-WAIT   # live gateway
```

**Fix**: Only a gateway restart clears CLOSE-WAIT. The client side cannot fix it.
```bash
# Restart affected gateway
./start-gateway.sh restart
```

**Prevention**: Always call `client.disconnect()` before exiting. Use `try/finally`.

---

## 12. Common Failures & Fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Error 502 on connect | Gateway/TWS not running | Start gateway; check port number matches live/paper mode |
| `nextValidId` never fires | 2FA not approved, or wrong port | Check phone for 2FA prompt; verify port; check gateway logs |
| Error 501 "already connected" | Duplicate `clientId` on same port | Use a unique `clientId` per connection instance |
| `tickPrice` never fires | Market closed, no subscription, or wrong market data type | Call `reqMarketDataType(3)` for delayed; check market hours |
| Error 200 "no security found" | Wrong symbol, exchange, expiry, or strike | Use `reqContractDetails` first to validate; check IBKR TWS manually |
| Error 354 "not subscribed" | Missing market data subscription in account | Request delayed data (`reqMarketDataType(3)`) or add subscription in account management |
| `positionEnd` never fires | No positions, or account sync delay | Wait longer; retry `reqPositions()`; check account string |
| Order error 201 "rejected" | Insufficient funds, bad price, or restricted security | Check account buying power; verify `lmtPrice` is valid for min tick |
| API calls hang / timeout | CLOSE-WAIT stale connections | `ss -anp | grep :<port> | grep CLOSE-WAIT`; restart gateway |
| `historicalDataEnd` never fires | Pacing violation (too many requests) | Add `time.sleep(10)` between historical requests; IBKR limits ~60/10 min |
| Disconnect mid-session | Gateway daily restart or network drop | Reconnect with retry loop; store `nextValidId` for order continuity |
