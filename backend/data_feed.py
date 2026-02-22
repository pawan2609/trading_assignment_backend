
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable, Optional

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

from config import Config
from models import Bar

logger = logging.getLogger(__name__)


class BaseFeed(ABC):
    

    @abstractmethod
    def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        """Return historical bars for the given window."""
        ...

    @abstractmethod
    def connect(self) -> None:
        """Establish connection (no-op for history feed)."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...


class FyersHistoryFeed(BaseFeed):
    

    RESOLUTION_MAP = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60", "D": "D"}

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.fyers = fyersModel.FyersModel(
            client_id=cfg.FYERS_APP_ID,
            token=cfg.FYERS_ACCESS_TOKEN,
            is_async=False,
            log_path=cfg.LOG_DIR,
        )

    def connect(self) -> None:
        logger.info("FyersHistoryFeed ready")

    def disconnect(self) -> None:
        pass

    def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
       
        resolution = self.RESOLUTION_MAP.get(self.cfg.TIMEFRAME, "5")
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start.strftime("%Y-%m-%d"),
            "range_to": end.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }
        resp = self.fyers.history(data=data)
        if resp.get("s") != "ok":
            logger.error("History API error: %s", resp)
            return []

        bars: list[Bar] = []
        for c in resp.get("candles", []):
            ts, o, h, l, cl, vol = c[0], c[1], c[2], c[3], c[4], int(c[5])
            bars.append(
                Bar(
                    timestamp=datetime.fromtimestamp(ts),
                    open=o,
                    high=h,
                    low=l,
                    close=cl,
                    volume=vol,
                    symbol=symbol,
                )
            )
        
        bars = self._filter_trading_hours(bars)
        bars = self._handle_missing(bars)
        logger.info("Fetched %d bars for %s", len(bars), symbol)
        return bars

    def _filter_trading_hours(self, bars: list[Bar]) -> list[Bar]:
        start_h, start_m = map(int, self.cfg.TRADING_HOURS_START.split(":"))
        end_h, end_m = map(int, self.cfg.TRADING_HOURS_END.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        filtered = []
        for bar in bars:
            bar_minutes = bar.timestamp.hour * 60 + bar.timestamp.minute
            if start_minutes <= bar_minutes < end_minutes:
                filtered.append(bar)
        if len(filtered) < len(bars):
            logger.info("Filtered %d bars outside trading hours", len(bars) - len(filtered))
        return filtered

    @staticmethod
    def _handle_missing(bars: list[Bar]) -> list[Bar]:
        if len(bars) < 2:
            return bars
        cleaned: list[Bar] = [bars[0]]
        for i in range(1, len(bars)):
            prev = cleaned[-1]
            curr = bars[i]
            gap = (curr.timestamp - prev.timestamp).total_seconds()
            expected_gap = 300 
            if gap > 6 * 3600:
                cleaned.append(curr)
                continue
            if gap > expected_gap * 1.5:
                logger.warning(
                    "Gap detected: %s -> %s (%.0fs). Forward-filling.",
                    prev.timestamp,
                    curr.timestamp,
                    gap,
                )
                fill_ts = prev.timestamp + timedelta(seconds=expected_gap)
                while fill_ts < curr.timestamp:
                    cleaned.append(
                        Bar(
                            timestamp=fill_ts,
                            open=prev.close,
                            high=prev.close,
                            low=prev.close,
                            close=prev.close,
                            volume=0,
                            symbol=prev.symbol,
                        )
                    )
                    fill_ts += timedelta(seconds=expected_gap)
            cleaned.append(curr)
        return cleaned


class FyersLiveFeed(BaseFeed):
    


    def __init__(self, cfg: Config, on_bar: Optional[Callable[[Bar], None]] = None) -> None:
        self.cfg = cfg
        self.on_bar = on_bar
        self._ws: Optional[data_ws.FyersDataSocket] = None
        self._current_bar: Optional[dict] = None
        self._bar_start: Optional[datetime] = None
        self._interval = int(cfg.TIMEFRAME) * 60  # seconds
        self._connected = False
        self._bars: list[Bar] = []

    def connect(self) -> None:

        def _on_message(msg: dict) -> None:
            self._handle_tick(msg)

        def _on_error(msg: dict) -> None:
            logger.error("WS error: %s", msg)

        def _on_close(msg: dict) -> None:
            logger.info("WS closed: %s", msg)
            self._connected = False

        def _on_open() -> None:
            logger.info("WS connected")
            data_type = "SymbolUpdate"
            symbols = [self.cfg.SYMBOL]
            self._ws.subscribe(symbols=symbols, data_type=data_type)
            self._ws.keep_running()
            self._connected = True

        self._ws = data_ws.FyersDataSocket(
            access_token=f"{self.cfg.FYERS_APP_ID}:{self.cfg.FYERS_ACCESS_TOKEN}",
            log_path=self.cfg.LOG_DIR,
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=_on_open,
            on_close=_on_close,
            on_error=_on_error,
            on_message=_on_message,
        )
        self._ws.connect()
        logger.info("FyersLiveFeed connecting to WebSocket...")

    def disconnect(self) -> None:
        if self._ws:
            self._ws.close_connection()
            self._connected = False
            logger.info("FyersLiveFeed disconnected")

    def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        
        return [b for b in self._bars if start <= b.timestamp <= end]

    def _handle_tick(self, msg: dict) -> None:
        
        try:
            # print("msg",msg)
            ltp = msg.get("ltp", 0.0)
            symbol = msg.get("symbol", self.cfg.SYMBOL)
            ts = datetime.fromtimestamp(msg.get("exch_feed_time", time.time()))
            vol = msg.get("vol_traded_today", 0)
        except Exception:
            logger.debug("Ignoring malformed tick: %s", msg)
            return

        if ltp <= 0:
            return

        bar_start = ts.replace(
            second=0, microsecond=0,
            minute=(ts.minute // int(self.cfg.TIMEFRAME)) * int(self.cfg.TIMEFRAME),
        )

        if self._bar_start is None or bar_start > self._bar_start:
            if self._current_bar is not None:
                bar = Bar(
                    timestamp=self._bar_start,
                    open=self._current_bar["open"],
                    high=self._current_bar["high"],
                    low=self._current_bar["low"],
                    close=self._current_bar["close"],
                    volume=self._current_bar["volume"],
                    symbol=symbol,
                )
                self._bars.append(bar)
                if self.on_bar:
                    self.on_bar(bar)
                logger.debug("Bar emitted: %s", bar)

            self._bar_start = bar_start
            self._current_bar = {
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": vol,
            }
        else:
            self._current_bar["high"] = max(self._current_bar["high"], ltp)
            self._current_bar["low"] = min(self._current_bar["low"], ltp)
            self._current_bar["close"] = ltp
            self._current_bar["volume"] = vol
