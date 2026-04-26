from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict

LOGGER = logging.getLogger(__name__)


class TradeLifecycleManager:
    def __init__(self) -> None:
        self.max_open_trades = int(os.environ.get("MAX_OPEN_TRADES", "1"))
        self.cooldown_seconds = float(os.environ.get("TRADE_COOLDOWN_SECONDS", "300"))
        self._lock = threading.Lock()
        self._last_trade_time: Dict[str, float] = {}
        self._open_trade_count = 0

    def can_open_new_trade(self, symbol: str | dict, regime_context: dict | None = None) -> bool:
        if isinstance(symbol, dict):
            regime_context = symbol
            symbol = str(regime_context.get("symbol", "BTC/USDT"))
        regime_context = regime_context or {}
        with self._lock:
            if self._open_trade_count >= self.max_open_trades:
                LOGGER.debug("[LIFECYCLE] reject: max open trades reached")
                return False
            last = self._last_trade_time.get(str(symbol), 0.0)
            if time.time() - last < self.cooldown_seconds:
                LOGGER.debug("[LIFECYCLE] reject: cooldown active")
                return False
        if regime_context.get("regime") in ("TOXIC", "HALTED", "STALE_FALLBACK", "UNCALIBRATED"):
            LOGGER.debug("[LIFECYCLE] reject: regime blocked")
            return False
        return True

    def on_trade_opened(self, symbol: str) -> None:
        with self._lock:
            self._open_trade_count += 1

    def on_trade_closed(self, symbol: str) -> None:
        with self._lock:
            self._open_trade_count = max(0, self._open_trade_count - 1)
            self._last_trade_time[symbol] = time.time()

    # compatibility hooks
    def update(self, price: float, features: dict) -> dict:
        _ = (price, features)
        return {"action": "HOLD", "block_new_entries": False, "risk_scale": 1.0, "reason": "ok"}

    def session_guard(self) -> dict:
        return {"action": "ALLOW", "block_new_entries": False}

    def get_correlation_id(self) -> str:
        return ""

    def on_entry(self, **kwargs) -> None:
        self.on_trade_opened(str(kwargs.get("symbol", "BTC/USDT")))

    def on_exit(self, **kwargs) -> None:
        self.on_trade_closed(str(kwargs.get("symbol", "BTC/USDT")))
