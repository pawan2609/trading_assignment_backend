
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Callable, Optional

from config import Config
from models import Bar, OrderIntent, OrderType, Position, Side, Signal, Trade
from risk_manager import RiskManager
from trade_book import TradeBook

logger = logging.getLogger(__name__)


class ExecutionEngine:

    def __init__(
        self,
        cfg: Config,
        risk_manager: RiskManager,
        trade_book: TradeBook,
        on_trade: Optional[Callable[[Trade], None]] = None,
        on_position_update: Optional[Callable[[], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.risk = risk_manager
        self.book = trade_book
        self._capital = cfg.INITIAL_CAPITAL
        self._open_positions: list[Position] = []
        self._trades: list[Trade] = []
        self._on_trade = on_trade
        self._on_position_update = on_position_update

    def process_signal(self, signal: Signal) -> Optional[Trade]:
        if signal.side == Side.SELL and not self._open_positions:
            logger.debug("No position to sell, ignoring SELL signal")
            return None

        if signal.side == Side.BUY:
            qty = self._compute_quantity(signal.price)
            if qty <= 0:
                logger.warning("Quantity is 0, skipping BUY signal")
                return None
        else:
            pos = self._find_position(signal.symbol)
            if not pos:
                return None
            qty = pos.quantity
        order = OrderIntent(
            symbol=signal.symbol,
            side=signal.side,
            quantity=qty,
            price=signal.price,
            order_type=OrderType.MARKET,
            timestamp=signal.timestamp,
            stop_loss=signal.stop_loss,
        )
        self.book.log_order_intent(order)
        logger.info("Order intent: %s %s %d @ %.2f [%s]", order.side.value, order.symbol, order.quantity, order.price, order.order_id)

        risk_result = self.risk.check(order, self._open_positions, self._capital)
        if not risk_result.passed:
            self.book.log_risk_rejection(order, risk_result)
            return None
        trade = self._execute(order, signal)
        return trade

    def close_all_positions(self, current_price: float, timestamp: datetime) -> list[Trade]:
        trades: list[Trade] = []
        for pos in list(self._open_positions):
            signal = Signal(
                symbol=pos.symbol,
                side=Side.SELL,
                price=current_price,
                timestamp=timestamp,
                reason="Kill switch — closing position",
            )
            trade = self.process_signal(signal)
            if trade:
                trades.append(trade)
        return trades

    def update_positions_price(self, bar: Bar) -> None:
        for pos in self._open_positions:
            if pos.symbol == bar.symbol:
                pos.current_price = bar.close
                if pos.side == Side.BUY:
                    pos.unrealized_pnl = (bar.close - pos.entry_price) * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - bar.close) * pos.quantity
        if self._on_position_update:
            self._on_position_update()

    @property
    def open_positions(self) -> list[Position]:
        return list(self._open_positions)

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def running_pnl(self) -> float:
        return self._capital - self.cfg.INITIAL_CAPITAL

    def _compute_quantity(self, price: float) -> int:
        max_risk_value = self._capital * (self.cfg.MAX_RISK_PER_TRADE_PCT / 100)
        qty = int(math.floor(max_risk_value / price))
        qty = min(qty, self.cfg.POSITION_SIZE_LIMIT)
        return max(qty, 0)

    def _execute(self, order: OrderIntent, signal: Signal) -> Trade:
        slippage = order.price * (self.cfg.SLIPPAGE_BPS / 10_000)
        if order.side == Side.BUY:
            fill_price = order.price + slippage
        else:
            fill_price = order.price - slippage
        commission = self.cfg.COMMISSION_PER_TRADE

        if order.side == Side.BUY:
            trade = Trade(
                order_id=order.order_id,
                symbol=order.symbol,
                side=Side.BUY,
                quantity=order.quantity,
                entry_price=fill_price,
                commission=commission,
                slippage=slippage * order.quantity,
                timestamp=order.timestamp,
                entry_reason=signal.reason,
            )
            position = Position(
                symbol=order.symbol,
                side=Side.BUY,
                quantity=order.quantity,
                entry_price=fill_price,
                current_price=fill_price,
                entry_time=order.timestamp,
                stop_loss=signal.stop_loss,
                order_id=order.order_id,
                trade_id=trade.trade_id,
            )
            self._open_positions.append(position)
            self._trades.append(trade)
            self._capital -= (fill_price * order.quantity) + commission
        else:
            pos = self._find_position(order.symbol)
            pnl = (fill_price - pos.entry_price) * order.quantity if pos else 0.0
            pnl -= commission
            trade = self._find_trade(pos.trade_id) if pos else None
            if trade:
                pnl -= trade.commission
                trade.exit_price = fill_price
                trade.pnl = pnl
                trade.exit_timestamp = order.timestamp
                trade.closed = True
                trade.commission += commission
                trade.slippage += slippage * order.quantity
                trade.exit_reason = signal.reason
            else:
                trade = Trade(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=Side.SELL,
                    quantity=order.quantity,
                    entry_price=pos.entry_price if pos else fill_price,
                    exit_price=fill_price,
                    pnl=pnl,
                    commission=commission,
                    slippage=slippage * order.quantity,
                    timestamp=pos.entry_time if pos else order.timestamp,
                    exit_timestamp=order.timestamp,
                    closed=True,
                )
            if pos:
                self._capital += (fill_price * order.quantity) - commission
                self._open_positions.remove(pos)
            self.risk.record_trade(trade)

        self.book.log_trade(trade)
        if self._on_trade:
            self._on_trade(trade)
        logger.info(
            "Trade filled: %s %s %d @ %.2f | PnL=%.2f | ID=%s",
            trade.side.value, trade.symbol, trade.quantity,
            fill_price, trade.pnl or 0.0, trade.trade_id,
        )
        return trade

    def _find_trade(self, trade_id: str) -> Optional[Trade]:
        for t in self._trades:
            if t.trade_id == trade_id:
                return t
        return None

    def _find_position(self, symbol: str) -> Optional[Position]:
        for pos in self._open_positions:
            if pos.symbol == symbol:
                return pos
        return None
