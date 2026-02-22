from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import Config
from models import OrderIntent, Position, RiskCheckResult, Side, Trade

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._daily_pnl: float = 0.0
        self._daily_trade_count: int = 0

    def check(
        self,
        order: OrderIntent,
        open_positions: list[Position],
        capital: float,
    ) -> RiskCheckResult:
        if order.side == Side.SELL:
            return RiskCheckResult(passed=True, reason="Exit order — risk checks skipped")

        checks = [
            self._check_max_risk_per_trade(order, capital),
            self._check_daily_loss(capital),
            self._check_trade_count(),
            self._check_position_size(order, open_positions),
        ]

        for result in checks:
            if not result.passed:
                logger.warning("RISK REJECTED: %s | Order %s", result.reason, order.order_id)
                return result

        return RiskCheckResult(passed=True, reason="All risk checks passed")

    def record_trade(self, trade: Trade) -> None:
        if trade.pnl is not None:
            self._daily_pnl += trade.pnl
        self._daily_trade_count += 1

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0
        self._daily_trade_count = 0
        logger.info("Risk manager daily counters reset")

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_trade_count(self) -> int:
        return self._daily_trade_count

    def is_daily_loss_breached(self, capital: float) -> bool:
        max_loss = capital * (self.cfg.MAX_DAILY_LOSS_PCT / 100)
        return self._daily_pnl <= -max_loss

    def _check_max_risk_per_trade(self, order: OrderIntent, capital: float) -> RiskCheckResult:
        max_risk = capital * (self.cfg.MAX_RISK_PER_TRADE_PCT / 100)
        trade_value = order.quantity * order.price
        if trade_value > max_risk:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"Trade value Rs{trade_value:,.0f} exceeds max risk "
                    f"Rs{max_risk:,.0f} ({self.cfg.MAX_RISK_PER_TRADE_PCT}%)"
                ),
                rule_name="max_risk_per_trade",
            )
        return RiskCheckResult(passed=True, rule_name="max_risk_per_trade")

    def _check_daily_loss(self, capital: float) -> RiskCheckResult:
        max_loss = capital * (self.cfg.MAX_DAILY_LOSS_PCT / 100)
        if self._daily_pnl <= -max_loss:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"Daily loss Rs{self._daily_pnl:,.0f} breached limit "
                    f"Rs{-max_loss:,.0f} ({self.cfg.MAX_DAILY_LOSS_PCT}%)"
                ),
                rule_name="max_daily_loss",
            )
        return RiskCheckResult(passed=True, rule_name="max_daily_loss")

    def _check_trade_count(self) -> RiskCheckResult:
        if self._daily_trade_count >= self.cfg.MAX_TRADES_PER_DAY:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"Daily trade count {self._daily_trade_count} >= "
                    f"limit {self.cfg.MAX_TRADES_PER_DAY}"
                ),
                rule_name="max_trades_per_day",
            )
        return RiskCheckResult(passed=True, rule_name="max_trades_per_day")

    def _check_position_size(
        self, order: OrderIntent, open_positions: list[Position]
    ) -> RiskCheckResult:
        existing_qty = sum(
            p.quantity for p in open_positions if p.symbol == order.symbol
        )
        total_qty = existing_qty + order.quantity
        if total_qty > self.cfg.POSITION_SIZE_LIMIT:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"Position size {total_qty} exceeds limit "
                    f"{self.cfg.POSITION_SIZE_LIMIT} for {order.symbol}"
                ),
                rule_name="position_size_limit",
            )
        return RiskCheckResult(passed=True, rule_name="position_size_limit")
