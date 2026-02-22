
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backtester import Backtester
from config import Config
from data_feed import FyersHistoryFeed, FyersLiveFeed
from execution_engine import ExecutionEngine
from kill_switch import SystemController
from models import BacktestResult, Bar, PnLSummary, Side, SystemState, StateChangeLog
from risk_manager import RiskManager
from strategy import SMACrossoverStrategy
from trade_book import TradeBook, setup_logging

logger = logging.getLogger(__name__)

cfg = Config()
setup_logging(cfg.LOG_DIR)

trade_book = TradeBook(cfg.TRADE_BOOK_PATH)
risk_manager = RiskManager(cfg)

ws_clients: list[WebSocket] = []


async def broadcast(event: str, data: dict) -> None:
    msg = json.dumps({"event": event, "data": data}, default=str)
    disconnected = []
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        ws_clients.remove(ws)


_loop: Optional[asyncio.AbstractEventLoop] = None


def _sync_broadcast(event: str, data: dict) -> None:
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(event, data), _loop)


def _on_state_change(log: StateChangeLog) -> None:
    trade_book.log_state_change(log)
    _sync_broadcast("state_change", log.model_dump(mode="json"))


controller = SystemController(on_state_change=_on_state_change)


def _on_trade(trade) -> None:
    _sync_broadcast("trade", trade.model_dump(mode="json"))


def _on_position_update() -> None:
    _sync_broadcast("positions", {"positions": [p.model_dump(mode="json") for p in engine.open_positions]})


engine = ExecutionEngine(
    cfg=cfg,
    risk_manager=risk_manager,
    trade_book=trade_book,
    on_trade=_on_trade,
    on_position_update=_on_position_update,
)

strategy = SMACrossoverStrategy(cfg)

def _restore_state_from_trades():
    today = datetime.now().date()
    for t in trade_book.trades:
        if not t.get("closed"):
            continue
        pnl = t.get("pnl", 0)
        engine._capital += pnl
        ts = t.get("exit_timestamp") or t.get("timestamp")
        if ts:
            try:
                trade_date = datetime.fromisoformat(str(ts)).date()
                if trade_date == today:
                    risk_manager._daily_pnl += pnl
                    risk_manager._daily_trade_count += 1
            except (ValueError, TypeError):
                pass
    if trade_book.trades:
        logger.info(
            "Restored state: capital=%.2f, running_pnl=%.2f, daily_pnl=%.2f",
            engine._capital,
            engine.running_pnl,
            risk_manager.daily_pnl,
        )

_restore_state_from_trades()

live_feed: Optional[FyersLiveFeed] = None
live_thread: Optional[threading.Thread] = None
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_event_loop()
    logger.info("Trading system started")
    yield
    if live_feed:
        live_feed.disconnect()
    logger.info("Trading system shutdown")


app = FastAPI(title="Trading System", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BacktestRequest(BaseModel):
    symbol: str = cfg.SYMBOL
    start_date: str = ""  
    end_date: str = ""    


class StartRequest(BaseModel):
    symbol: str = cfg.SYMBOL


@app.get("/api/state")
async def get_state():
    return {
        "state": controller.state.value,
        "history": [h.model_dump(mode="json") for h in controller.state_history],
    }


@app.get("/api/trades")
async def get_trades():
    return {"trades": trade_book.trades}


@app.get("/api/positions")
async def get_positions():
    return {
        "positions": [p.model_dump(mode="json") for p in engine.open_positions],
    }


@app.get("/api/pnl")
async def get_pnl():
    unrealized = sum(p.unrealized_pnl for p in engine.open_positions)
    return PnLSummary(
        running_pnl=round(engine.running_pnl + unrealized, 2),
        daily_pnl=round(risk_manager.daily_pnl, 2),
        total_commission=round(sum(t.get("commission", 0) for t in trade_book.trades), 2),
        open_positions=len(engine.open_positions),
    ).model_dump()


@app.post("/api/kill")
async def kill():
    ok = controller.kill("Manual kill from UI")
    if ok and engine.open_positions:
        last_price = engine.open_positions[0].current_price or engine.open_positions[0].entry_price
        engine.close_all_positions(last_price, datetime.now())
        strategy.set_position(False)
    return {"success": ok, "state": controller.state.value}


@app.post("/api/pause")
async def pause():
    ok = controller.pause("Manual pause from UI")
    return {"success": ok, "state": controller.state.value}


@app.post("/api/resume")
async def resume():
    ok = controller.resume("Manual resume from UI")
    return {"success": ok, "state": controller.state.value}


@app.post("/api/start")
async def start_trading(req: StartRequest):
    global live_feed, live_thread

    if not controller.is_running and not controller.is_paused:
        controller._transition(SystemState.RUNNING, "Fresh start from UI")

    if live_feed:
        return {"status": "already running"}

    def _on_bar(bar: Bar) -> None:
        try:
            if controller.is_killed:
                return
            warmup_threshold = timedelta(seconds=int(cfg.TIMEFRAME) * 60 * 2)
            is_warmup = (datetime.now() - bar.timestamp) > warmup_threshold

            engine.update_positions_price(bar)

            if not controller.is_running:
                if is_warmup:
                    strategy.on_bar(bar)
                return

            signal = strategy.on_bar(bar)

            if is_warmup:
                logger.debug("Warmup bar (skipping trade): %s @ %s", bar.symbol, bar.timestamp)
            elif signal:
                trade = engine.process_signal(signal)
                if trade:
                    if trade.side == Side.BUY:
                        strategy.set_position(True, trade.entry_price)
                    else:
                        strategy.set_position(False)

                    if risk_manager.is_daily_loss_breached(engine.capital):
                        controller.check_auto_kill(daily_loss_breached=True)
                        engine.close_all_positions(bar.close, bar.timestamp)
                        strategy.set_position(False)

            
            unrealized = sum(p.unrealized_pnl for p in engine.open_positions)
            _sync_broadcast("pnl", {
                "running_pnl": round(engine.running_pnl + unrealized, 2),
                "daily_pnl": round(risk_manager.daily_pnl, 2),
                "total_commission": round(sum(t.get("commission", 0) for t in trade_book.trades), 2),
                "open_positions": len(engine.open_positions),
            })
        except Exception as e:
            logger.exception("Error processing bar: %s", e)
            controller.handle_exception(e)

    live_feed = FyersLiveFeed(cfg, on_bar=_on_bar)

    def _run_feed():
        try:
            live_feed.connect()
        except Exception as e:
            logger.exception("Live feed error: %s", e)
            controller.handle_exception(e)

    live_thread = threading.Thread(target=_run_feed, daemon=True)
    live_thread.start()

    return {"status": "started", "symbol": req.symbol}


@app.post("/api/stop")
async def stop_trading():
    global live_feed
    if live_feed:
        live_feed.disconnect()
        live_feed = None
    return {"status": "stopped"}


@app.post("/api/backtest")
async def run_backtest(req: BacktestRequest):
    try:
        bt_strategy = SMACrossoverStrategy(cfg)
        bt = Backtester(strategy=bt_strategy, cfg=cfg)

        start = datetime.strptime(req.start_date, "%Y-%m-%d") if req.start_date else datetime.now() - timedelta(days=30)
        end = datetime.strptime(req.end_date, "%Y-%m-%d") if req.end_date else datetime.now()

        result = bt.run(symbol=req.symbol, start=start, end=end)

        if len(result.equity_curve) > 500:
            step = len(result.equity_curve) // 500
            result.equity_curve = result.equity_curve[::step]

        return result.model_dump(mode="json", exclude={"trades"})
    except Exception as e:
        logger.exception("Backtest error: %s", e)
        return {"error": str(e)}


@app.get("/api/config")
async def get_config():
    return {
        "symbol": cfg.SYMBOL,
        "timeframe": cfg.TIMEFRAME,
        "initial_capital": cfg.INITIAL_CAPITAL,
        "max_risk_per_trade": cfg.MAX_RISK_PER_TRADE_PCT,
        "max_daily_loss": cfg.MAX_DAILY_LOSS_PCT,
        "max_trades_per_day": cfg.MAX_TRADES_PER_DAY,
        "position_size_limit": cfg.POSITION_SIZE_LIMIT,
        "sma_fast": cfg.SMA_FAST_PERIOD,
        "sma_slow": cfg.SMA_SLOW_PERIOD,
        "stop_loss": cfg.STOP_LOSS_PCT,
        "slippage_bps": cfg.SLIPPAGE_BPS,
        "commission": cfg.COMMISSION_PER_TRADE,
    }


@app.get("/api/rejections")
async def get_rejections():
    return {"rejections": trade_book.risk_rejections}


@app.get("/api/market/indices")
async def get_market_indices():
    return {"indices": []}


@app.get("/api/account/summary")
async def get_account_summary():
    return {
        "capital": engine._capital,
        "running_pnl": round(engine.running_pnl, 2),
        "daily_pnl": round(risk_manager.daily_pnl, 2),
        "open_positions": len(engine.open_positions),
    }


@app.websocket("/ws/market/indices")
async def ws_market_indices(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    logger.info("WebSocket client connected (total: %d)", len(ws_clients))
    try:
        await ws.send_text(json.dumps({
            "event": "state_change",
            "data": {"to_state": controller.state.value},
        }))
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"event": "pong"}))
    except WebSocketDisconnect:
        ws_clients.remove(ws)
        logger.info("WebSocket client disconnected (total: %d)", len(ws_clients))
