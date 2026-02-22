# System Architecture — Trading Infrastructure

## 1. Architecture Overview


┌───────────────────────────────── UI (React) ──────────────────────────────┐
│  Trades Table │ Open Positions │ PnL Panel │ Controls (Kill/Pause/Resume) │
└───────────────────────────────── WebSocket + REST ─────────────────────────┘
                                      │
                              ┌───────┴───────┐
                              │  FastAPI App   │
                              └───────┬───────┘
           ┌──────────────────────────┼──────────────────────────┐
           │                          │                          │
  ┌────────┴────────┐    ┌────────────┴───────────┐   ┌─────────┴──────────┐
  │  Data Feed Layer │    │  Execution Pipeline    │   │  System Controller │
  │  ──────────────  │    │  ────────────────────  │   │  ────────────────  │
  │ • FyersHistory   │    │ Signal → Risk Check    │   │ Kill / Pause /     │
  │ • FyersLive (WS) │    │      → OrderIntent     │   │ Resume             │
  │ • Gap handling   │    │      → Fill (slippage)  │   │ Auto-kill triggers │
  └──────────────────┘    │      → Trade Book       │   └────────────────────┘
                          └──────────────────────────┘
```

**Components:**

| Module              | Responsibility                                           |
|---------------------|----------------------------------------------------------|
| `config.py`         | All tuneable parameters (zero hard-coding)               |
| `models.py`         | Pydantic data models & enums                             |
| `data_feed.py`      | Fyers History API + WebSocket live feed                  |
| `strategy.py`       | SMA crossover (pluggable via `BaseStrategy`)             |
| `risk_manager.py`   | Pre-order risk gate (4 rules)                            |
| `execution_engine.py` | Signal → Risk → Order → Fill → Log pipeline           |
| `backtester.py`     | Bar-by-bar engine (no look-ahead, costs modelled)        |
| `kill_switch.py`    | Thread-safe state machine                                |
| `trade_book.py`     | Persistent JSON trade book + structured logging          |
| `app.py`            | FastAPI REST + WebSocket server                          |

---

## 2. Execution & Risk Flow

```
 Market Data (Fyers WS / History API)
        │
        ▼
   ┌─────────┐
   │ Strategy │ ── on_bar(bar) ──▶ Signal? (BUY/SELL + price + SL)
   └─────────┘
        │ Signal
        ▼
   ┌──────────────┐     REJECT                ┌──────────────┐
   │ Risk Manager  │ ─────────────────────────▶│ Trade Book   │
   │ ────────────  │     (logged)              │ (rejection)  │
   │ 1. Max risk/  │                           └──────────────┘
   │    trade      │
   │ 2. Daily loss │     PASS
   │ 3. Trade count│ ──────────┐
   │ 4. Pos. size  │           │
   └──────────────┘           ▼
                        ┌──────────────┐
                        │ Order Intent │  (symbol, side, qty, price, ts, id)
                        └──────┬───────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  Execution   │  Apply slippage + commission
                        │  Engine      │  Simulate fill
                        └──────┬───────┘
                               │
                        ┌──────┴───────┐
                        │  Trade Book  │  Persist trade → JSON
                        │  + WebSocket │  Push to UI
                        └──────────────┘
```

**Risk rules enforced before every order:**
1. **Max risk per trade** — trade value ≤ 2% of capital
2. **Max daily loss** — cumulative daily PnL ≤ −5% of capital (triggers auto-kill)
3. **Max trades per day** — ≤ 10 trades
4. **Position size limit** — ≤ 500 shares per symbol

---

## 3. Kill / Pause / Resume Behaviour

| Action   | Trigger Sources           | Effect                                                    |
|----------|---------------------------|-----------------------------------------------------------|
| **Kill** | UI button, daily loss breach, unhandled exception | State → KILLED. All open positions closed immediately. No further trading. |
| **Pause**| UI button                  | State → PAUSED. No new trades. System stays alive, data feed continues. |
| **Resume**| UI button                 | State → RUNNING (only from PAUSED). Trading resumes normally. |

**State machine:**

```
  RUNNING ──pause──▶ PAUSED
    │                  │
    │ kill             │ kill
    ▼                  ▼
  KILLED ◀── kill ── PAUSED
```

- All state transitions are **thread-safe** (mutex-locked).
- Every transition is **logged** with timestamp and reason.
- Auto-kill triggers: max daily loss breach, any unhandled exception.

---

## 4. Backtest vs Live Mode

The **same** `Strategy`, `RiskManager`, and `ExecutionEngine` objects are used in both modes. The only difference is how bars are sourced:

| Aspect       | Backtest                        | Live                              |
|--------------|---------------------------------|-----------------------------------|
| Data source  | `FyersHistoryFeed` (REST)       | `FyersLiveFeed` (WebSocket)       |
| Bar delivery | Sequential loop                 | Real-time tick → bar aggregation  |
| Fills        | Simulated (slippage + cost)     | Simulated (slippage + cost)       |
| Risk         | Same 4 rules                    | Same 4 rules                      |
| Strategy     | `SMACrossoverStrategy.on_bar()` | `SMACrossoverStrategy.on_bar()`   |


