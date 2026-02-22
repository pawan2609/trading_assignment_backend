
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from models import StateChangeLog, SystemState

logger = logging.getLogger(__name__)


class SystemController:


    def __init__(
        self,
        on_state_change: Optional[Callable[[StateChangeLog], None]] = None,
    ) -> None:
        self._state = SystemState.RUNNING
        self._lock = threading.Lock()
        self._state_history: list[StateChangeLog] = []
        self._on_state_change = on_state_change

    @property
    def state(self) -> SystemState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        return self.state == SystemState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self.state == SystemState.PAUSED

    @property
    def is_killed(self) -> bool:
        return self.state == SystemState.KILLED

    @property
    def state_history(self) -> list[StateChangeLog]:
        with self._lock:
            return list(self._state_history)

    def kill(self, reason: str = "Manual kill") -> bool:
        return self._transition(SystemState.KILLED, reason)

    def pause(self, reason: str = "Manual pause") -> bool:
        if self.state != SystemState.RUNNING:
            logger.warning("Cannot pause from state %s", self.state.value)
            return False
        return self._transition(SystemState.PAUSED, reason)

    def resume(self, reason: str = "Manual resume") -> bool:
        if self.state != SystemState.PAUSED:
            logger.warning("Cannot resume from state %s", self.state.value)
            return False
        return self._transition(SystemState.RUNNING, reason)
    def check_auto_kill(self, daily_loss_breached: bool) -> bool:
        if daily_loss_breached and not self.is_killed:
            return self.kill(reason="Max daily loss breached — auto kill")
        return False

    def handle_exception(self, exc: Exception) -> None:
        self.kill(reason=f"Unhandled exception: {exc}")
        logger.critical("System killed due to unhandled exception: %s", exc)
    def _transition(self, to_state: SystemState, reason: str) -> bool:
        with self._lock:
            from_state = self._state
            if from_state == to_state:
                return False
            self._state = to_state
            log_entry = StateChangeLog(
                from_state=from_state,
                to_state=to_state,
                timestamp=datetime.now(),
                reason=reason,
            )
            self._state_history.append(log_entry)
            logger.info(
                "STATE CHANGE: %s -> %s | Reason: %s",
                from_state.value,
                to_state.value,
                reason,
            )
            if self._on_state_change:
                self._on_state_change(log_entry)
        return True
