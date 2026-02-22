from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class SystemState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    KILLED = "KILLED"

class Bar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    symbol: str = ""

class Signal(BaseModel):
    symbol: str
    side: Side
    price: float
    timestamp: datetime
    stop_loss: Optional[float] = None
    reason: str = ""

class OrderIntent(BaseModel):
    order_id: str = Field(default_factory=lambda: f"ORD-{uuid.uuid4().hex[:8].upper()}")
    symbol: str
    side: Side
    quantity: int
    price: float
    order_type: OrderType = OrderType.MARKET
    timestamp: datetime = Field(default_factory=datetime.now)
    stop_loss: Optional[float] = None

class Trade(BaseModel):
    trade_id: str = Field(default_factory=lambda: f"TRD-{uuid.uuid4().hex[:8].upper()}")
    order_id: str
    symbol: str
    side: Side
    quantity: int
    entry_price: float
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    commission: float = 0.0
    slippage: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)
    exit_timestamp: Optional[datetime] = None
    closed: bool = False
    entry_reason: str = ""
    exit_reason: str = ""

class Position(BaseModel):
    symbol: str
    side: Side
    quantity: int
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    entry_time: datetime = Field(default_factory=datetime.now)
    stop_loss: Optional[float] = None
    order_id: str = ""
    trade_id: str = ""

class RiskCheckResult(BaseModel):
    passed: bool
    reason: str = ""
    rule_name: str = ""

class BacktestResult(BaseModel):
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    equity_curve: list[float] = Field(default_factory=list)
    trades: list[Trade] = Field(default_factory=list)

class PnLSummary(BaseModel):
    running_pnl: float = 0.0
    daily_pnl: float = 0.0
    total_commission: float = 0.0
    open_positions: int = 0

class StateChangeLog(BaseModel):
    from_state: SystemState
    to_state: SystemState
    timestamp: datetime = Field(default_factory=datetime.now)
    reason: str = ""
