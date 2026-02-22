"""
Centralized configuration — no hard-coded parameters anywhere else.
All values can be overridden via environment variables or .env file.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Fyers API ──────────────────────────────────────────────
    FYERS_APP_ID: str = os.getenv("FYERS_APP_ID", "")
    FYERS_SECRET_KEY: str = os.getenv("FYERS_SECRET_KEY", "")
    FYERS_ACCESS_TOKEN: str = os.getenv("FYERS_ACCESS_TOKEN", "")
    FYERS_REDIRECT_URI: str = os.getenv("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/diagram")

    INITIAL_CAPITAL: float = 1_000_000.0
    MAX_RISK_PER_TRADE_PCT: float = 2.0
    POSITION_SIZE_LIMIT: int = 500
    MAX_DAILY_LOSS_PCT: float = 5.0
    MAX_TRADES_PER_DAY: int = 10
    SMA_FAST_PERIOD: int = 5
    SMA_SLOW_PERIOD: int = 20
    STOP_LOSS_PCT: float = 1.0
    SLIPPAGE_BPS: float = 5.0
    COMMISSION_PER_TRADE: float = 20.0
    SYMBOL: str = "NSE:SBIN-EQ"
    TIMEFRAME: str = "5"
    TRADING_HOURS_START: str = "09:15"
    TRADING_HOURS_END: str = "15:30"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    LOG_DIR: str = os.path.join(os.path.dirname(__file__), "logs")
    TRADE_BOOK_PATH: str = os.path.join(os.path.dirname(__file__), "data", "trades.json")
