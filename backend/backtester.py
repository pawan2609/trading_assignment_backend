
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from config import Config
from data_feed import BaseFeed, FyersHistoryFeed
from execution_engine import ExecutionEngine
from kill_switch import SystemController
from models import BacktestResult, Bar, Side, Trade
from risk_manager import RiskManager
from strategy import BaseStrategy
from trade_book import TradeBook

logger = logging.getLogger(__name__)


class Backtester:
    def __init__(
        self,
        strategy: BaseStrategy,
        cfg: Optional[Config] = None,
        feed: Optional[BaseFeed] = None,
    ) -> None:
        self.cfg = cfg or Config()
        self.strategy = strategy
        self.feed = feed or FyersHistoryFeed(self.cfg)
        self._trades: list[Trade] = []
        self._equity_curve: list[float] = []

    def run(
        self,
        symbol: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        bars: Optional[list[Bar]] = None,
    ) -> BacktestResult:
        symbol = symbol or self.cfg.SYMBOL
        if bars is None:
            if start is None or end is None:
                raise ValueError("Provide either bars or start/end dates")
            self.feed.connect()
            bars = self.feed.get_bars(symbol, start, end)
            self.feed.disconnect()

        if not bars:
            logger.warning("No bars to backtest")
            return BacktestResult()

        logger.info(
            "Starting backtest: %s | %d bars | %s -> %s",
            symbol, len(bars), bars[0].timestamp, bars[-1].timestamp,
        )

        trade_book = TradeBook(self.cfg.TRADE_BOOK_PATH)
        trade_book.clear()
        risk = RiskManager(self.cfg)
        controller = SystemController()

        engine = ExecutionEngine(
            cfg=self.cfg,
            risk_manager=risk,
            trade_book=trade_book,
        )

        self._trades = []
        self._equity_curve = [self.cfg.INITIAL_CAPITAL]
        current_day = None

        for bar in bars:
            if controller.is_killed:
                break
            bar_day = bar.timestamp.date()
            if current_day is not None and bar_day != current_day:
                if engine.open_positions:
                    closed = engine.close_all_positions(
                        prev_bar.close, prev_bar.timestamp,
                    )
                    self._trades.extend(closed)
                    self.strategy.set_position(False)
                    self._equity_curve.append(engine.capital)
                risk.reset_daily()
            current_day = bar_day

            engine.update_positions_price(bar)

            signal = self.strategy.on_bar(bar)

            if signal and controller.is_running:
                trade = engine.process_signal(signal)
                if trade:
                    self._trades.append(trade)
                    
                    if trade.closed:
                        self.strategy.set_position(False)
                    else:
                        self.strategy.set_position(True, trade.entry_price)

                    if risk.is_daily_loss_breached(engine.capital):
                        controller.check_auto_kill(daily_loss_breached=True)
                        closed = engine.close_all_positions(bar.close, bar.timestamp)
                        self._trades.extend(closed)
                        self.strategy.set_position(False)

            prev_bar = bar
            self._equity_curve.append(engine.capital)

        if engine.open_positions and bars:
            last_bar = bars[-1]
            closed = engine.close_all_positions(last_bar.close, last_bar.timestamp)
            self._trades.extend(closed)
            self._equity_curve.append(engine.capital)

        return self._compute_metrics(engine.capital)

    def _compute_metrics(self, final_capital: float) -> BacktestResult:
        closed_trades = [t for t in self._trades if t.closed and t.pnl is not None]
        total_pnl = sum(t.pnl for t in closed_trades)
        wins = [t for t in closed_trades if t.pnl > 0]
        losses = [t for t in closed_trades if t.pnl <= 0]
        total = len(closed_trades)

        win_rate = (len(wins) / total * 100) if total > 0 else 0.0
        avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(t.pnl for t in losses) / len(losses)) if losses else 0.0

        peak = self._equity_curve[0] if self._equity_curve else self.cfg.INITIAL_CAPITAL
        max_dd = 0.0
        for equity in self._equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        max_dd_pct = (max_dd / self.cfg.INITIAL_CAPITAL * 100) if self.cfg.INITIAL_CAPITAL > 0 else 0.0


        result = BacktestResult(
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            win_rate=round(win_rate, 2),
            total_trades=total,
            winning_trades=len(wins),
            losing_trades=len(losses),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            equity_curve=self._equity_curve,
            trades=closed_trades,
        )

        logger.info(
            "Backtest complete: PnL=Rs%.2f | MaxDD=%.2f%% | WinRate=%.1f%% | Trades=%d",
            result.total_pnl, result.max_drawdown_pct,
            result.win_rate, result.total_trades,
        )
        return result
