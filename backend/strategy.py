

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from typing import Optional

from config import Config
from models import Bar, Side, Signal

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._position_open = False
        self._entry_price: Optional[float] = None

    @abstractmethod
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        ...

    def set_position(self, is_open: bool, entry_price: Optional[float] = None) -> None:
        self._position_open = is_open
        self._entry_price = entry_price


class SMACrossoverStrategy(BaseStrategy):


    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self._fast = deque(maxlen=cfg.SMA_FAST_PERIOD)
        self._slow = deque(maxlen=cfg.SMA_SLOW_PERIOD)
        self._prev_fast_above: Optional[bool] = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._fast.append(bar.close)
        self._slow.append(bar.close)

        if len(self._slow) < self._slow.maxlen:
            return None

        fast_sma = sum(self._fast) / len(self._fast)
        slow_sma = sum(self._slow) / len(self._slow)
        fast_above = fast_sma > slow_sma

        signal: Optional[Signal] = None

        if self._position_open and self._entry_price is not None:
            sl_price = self._entry_price * (1 - self.cfg.STOP_LOSS_PCT / 100)
            if bar.close <= sl_price:
                signal = Signal(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    price=bar.close,
                    timestamp=bar.timestamp,
                    reason=f"Stop-loss hit (SL={sl_price:.2f})",
                )
                logger.info("Stop-loss triggered at %.2f for %s", bar.close, bar.symbol)
                self._prev_fast_above = fast_above
                return signal

        if self._prev_fast_above is not None:
            if fast_above and not self._prev_fast_above and not self._position_open:
                stop_loss = bar.close * (1 - self.cfg.STOP_LOSS_PCT / 100)
                signal = Signal(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    price=bar.close,
                    timestamp=bar.timestamp,
                    stop_loss=stop_loss,
                    reason=f"SMA crossover BUY (fast={fast_sma:.2f} > slow={slow_sma:.2f})",
                )
            elif not fast_above and self._prev_fast_above and self._position_open:
                signal = Signal(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    price=bar.close,
                    timestamp=bar.timestamp,
                    reason=f"SMA crossover SELL (fast={fast_sma:.2f} < slow={slow_sma:.2f})",
                )

        self._prev_fast_above = fast_above
        return signal
