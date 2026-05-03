"""utils/log_rate_limit.py — FIX-5.2: shared rate-limited logger utility.

Replaces per-class _warn_rate_limited logic across the ARE codebase. Each
unique ``key`` is suppressed for ``cooldown_s`` seconds after its last
emission; suppressed counts are surfaced in the next allowed emission as
``[suppressed N×]``.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional


class RateLimitedLogger:
    """Wraps a standard logger to suppress repeated identical messages.

    Parameters
    ----------
    logger          : underlying logging.Logger
    default_cooldown: minimum seconds between identical key emissions (default 30s)
    """

    def __init__(
        self,
        logger: logging.Logger,
        default_cooldown: float = 30.0,
    ) -> None:
        self._logger = logger
        self._cooldown = float(default_cooldown)
        self._last: Dict[str, float] = {}
        self._counts: Dict[str, int] = {}
        self._lock = threading.Lock()

    def warning(
        self,
        key: str,
        message: str,
        *,
        cooldown_s: Optional[float] = None,
    ) -> None:
        self._emit(logging.WARNING, key, message, cooldown_s)

    def error(
        self,
        key: str,
        message: str,
        *,
        cooldown_s: Optional[float] = None,
    ) -> None:
        self._emit(logging.ERROR, key, message, cooldown_s)

    def info(
        self,
        key: str,
        message: str,
        *,
        cooldown_s: Optional[float] = None,
    ) -> None:
        self._emit(logging.INFO, key, message, cooldown_s)

    def debug(
        self,
        key: str,
        message: str,
        *,
        cooldown_s: Optional[float] = None,
    ) -> None:
        self._emit(logging.DEBUG, key, message, cooldown_s)

    def _emit(
        self,
        level: int,
        key: str,
        message: str,
        cooldown_s: Optional[float],
    ) -> None:
        cd = float(cooldown_s) if cooldown_s is not None else self._cooldown
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key, -cd - 1.0)
            if now - last >= cd:
                self._last[key] = now
                count = self._counts.get(key, 0)
                suffix = f" [suppressed {count}×]" if count else ""
                self._counts[key] = 0
                emit_msg = message + suffix
            else:
                self._counts[key] = self._counts.get(key, 0) + 1
                emit_msg = None
        if emit_msg is not None:
            try:
                self._logger.log(level, emit_msg)
            except Exception:
                pass
