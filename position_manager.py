from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional, Any


class PositionManager:
    def __init__(self, path: str = "positions.json") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._positions: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                self._positions = raw
        except (FileNotFoundError, json.JSONDecodeError):
            self._positions = {}

    def _persist(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._positions, fh, indent=2)
        os.replace(tmp, self.path)

    def has_position(self, symbol: str | None = None) -> bool:
        with self._lock:
            if symbol is None:
                return bool(self._positions)
            return symbol in self._positions

    def on_entry(self, symbol: str, side: str, size: float, entry_price: float, order_id: str, **_: Any) -> None:
        with self._lock:
            if symbol in self._positions:
                raise RuntimeError(f"Position already open for {symbol} — cannot open duplicate")
            self._positions[symbol] = {
                "symbol": symbol,
                "side": str(side).upper(),
                "size": float(size),
                "entry_price": float(entry_price),
                "order_id": str(order_id),
                "opened_at": time.time(),
            }
            self._persist()

    def on_exit(self, symbol: str, exit_price: float, exit_time: str, **_: Any) -> dict:
        with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is None:
                return {"symbol": symbol, "realized_pnl": 0.0, "exit_price": float(exit_price)}
            entry = float(pos["entry_price"])
            size = float(pos["size"])
            side = pos["side"]
            pnl = ((float(exit_price) - entry) / max(entry, 1e-9)) * size
            if side == "SHORT":
                pnl = -pnl
            self._persist()
            return {"symbol": symbol, "realized_pnl": pnl, "exit_price": float(exit_price), "exit_time": exit_time}

    def get_position(self, symbol: str | None = None) -> Optional[dict]:
        with self._lock:
            if symbol is None:
                return next(iter(self._positions.values()), None)
            return self._positions.get(symbol)

    # compatibility
    def update(self, price: float, features: dict) -> dict:
        _ = (price, features)
        return {"action": "NO_POSITION"}
