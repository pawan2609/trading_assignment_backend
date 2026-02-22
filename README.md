### Prerequisites

- Python 3.10+
- Node.js 18+
- Fyers API credentials

### 1. Backend Setup

cd backend

python -m venv venv
venv\Scripts\activate # Windows

copy .env.example .env

# run the server

uvicorn app:app --reload --port 8000

## Configuration

All parameters are in `backend/config.py` Key settings:

| Parameter              | Default    | Description                     |
| ---------------------- | ---------- | ------------------------------- |
| `INITIAL_CAPITAL`      | ₹10,00,000 | Starting capital                |
| `MAX_RISK_PER_TRADE`   | 2%         | Max trade value as % of capital |
| `MAX_DAILY_LOSS`       | 5%         | Daily loss limit (auto-kill)    |
| `MAX_TRADES_PER_DAY`   | 10         | Max orders per day              |
| `POSITION_SIZE_LIMIT`  | 500        | Max shares per symbol           |
| `STOP_LOSS_PCT`        | 1%         | Stop-loss below entry           |
| `SLIPPAGE_BPS`         | 5 bps      | Simulated slippage              |
| `COMMISSION_PER_TRADE` | ₹20        | Flat commission per order       |
| `TIMEFRAME`            | 5 min      | Candle resolution               |
