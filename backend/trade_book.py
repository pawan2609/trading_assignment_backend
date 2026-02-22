

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from models import OrderIntent, RiskCheckResult, StateChangeLog, Trade

logger = logging.getLogger(__name__)


class TradeBook:

    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data: dict[str, list[dict[str, Any]]] = {
            "order_intents": [],
            "trades": [],
            "risk_rejections": [],
            "state_changes": [],
        }
        self._load()

    def log_order_intent(self, order: OrderIntent) -> None:
        entry = order.model_dump(mode="json")
        entry["logged_at"] = datetime.now().isoformat()
        self._data["order_intents"].append(entry)
        self._save()
        logger.debug("Logged order intent: %s", order.order_id)

    def log_trade(self, trade: Trade) -> None:
        entry = trade.model_dump(mode="json")
        entry["logged_at"] = datetime.now().isoformat()
        existing_idx = None
        for i, t in enumerate(self._data["trades"]):
            if t.get("trade_id") == trade.trade_id:
                existing_idx = i
                break
        if existing_idx is not None:
            self._data["trades"][existing_idx] = entry
        else:
            self._data["trades"].append(entry)
        self._save()
        logger.debug("Logged trade: %s", trade.trade_id)

    def log_risk_rejection(self, order: OrderIntent, result: RiskCheckResult) -> None:
        entry = {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": order.price,
            "rule": result.rule_name,
            "reason": result.reason,
            "timestamp": datetime.now().isoformat(),
        }
        self._data["risk_rejections"].append(entry)
        self._save()
        logger.info("Logged risk rejection: %s — %s", order.order_id, result.reason)

    def log_state_change(self, change: StateChangeLog) -> None:
        entry = change.model_dump(mode="json")
        entry["logged_at"] = datetime.now().isoformat()
        self._data["state_changes"].append(entry)
        self._save()

    @property
    def trades(self) -> list[dict]:
        return self._data["trades"]

    @property
    def order_intents(self) -> list[dict]:
        return self._data["order_intents"]

    @property
    def risk_rejections(self) -> list[dict]:
        return self._data["risk_rejections"]

    @property
    def state_changes(self) -> list[dict]:
        return self._data["state_changes"]

    def get_today_trades(self) -> list[dict]:
        today = datetime.now().date().isoformat()
        return [t for t in self._data["trades"] if t.get("timestamp", "").startswith(today)]

    def clear(self) -> None:
        """Clear all data (for testing)."""
        for key in self._data:
            self._data[key] = []
        self._save()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r") as f:
                    self._data = json.load(f)
                logger.info("Trade book loaded from %s (%d trades)", self._path, len(self._data.get("trades", [])))
            except (json.JSONDecodeError, IOError):
                logger.warning("Could not read trade book, starting fresh")

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except IOError as e:
            logger.error("Failed to save trade book: %s", e)


def setup_logging(log_dir: str, level: int = logging.INFO) -> None:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"trading_{datetime.now().strftime('%Y%m%d')}.log")

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    logger.info("Logging initialised — file: %s", log_file)
