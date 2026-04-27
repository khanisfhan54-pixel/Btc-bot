from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple


def _safe_float(v: float | int | str | None, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        if not math.isfinite(x):
            return default
        return x
    except Exception:
        return default


@dataclass(frozen=True)
class BasisStatus:
    ok: bool
    reason: str
    basis: float
    basis_pct: float
    ready: bool


class VenueBasisNormalizer:
    """Deterministic lifecycle for analysis/execution venue basis estimation.

    Lifecycle:
      1) `set_venues` establishes same-venue vs cross-venue policy.
      2) `seed` optionally initializes basis from known contemporaneous mids.
      3) `update` refines basis via EMA for cross-venue mode.
      4) `validate` returns explicit readiness/safety reason.
    """

    def __init__(
        self,
        horizon_seconds: float = 60.0,
        halt_threshold_pct: float = 0.5,
        min_samples: int = 1,
    ) -> None:
        self.horizon_seconds = max(1.0, float(horizon_seconds))
        self.halt_threshold_pct = max(0.0, float(halt_threshold_pct))
        self.min_samples = max(1, int(min_samples))
        self._ema_basis = 0.0
        self._ema_ready = False
        self._last_analysis_mid: Optional[float] = None
        self._last_execution_mid: Optional[float] = None
        self._history: Deque[Tuple[float, float]] = deque(maxlen=4096)
        self._same_venue = False
        self._analysis_venue = ""
        self._execution_venue = ""
        self._lock = threading.RLock()

    def snapshot(self) -> dict:
        with self._lock:
            basis_val = 0.0 if self._same_venue else float(self._ema_basis if self._ema_ready else 0.0)
            ref = _safe_float(self._last_analysis_mid, 0.0)
            basis_pct_val = 0.0 if ref <= 0.0 else (basis_val / ref) * 100.0
            ready_val = True if self._same_venue else bool(self._ema_ready and len(self._history) >= self.min_samples)
            return {
                "basis": basis_val,
                "basis_pct": basis_pct_val,
                "ready": ready_val,
                "same_venue": bool(self._same_venue),
            }

    def set_venues(self, analysis_venue: str, execution_venue: str) -> None:
        with self._lock:
            self._analysis_venue = str(analysis_venue or "").lower()
            self._execution_venue = str(execution_venue or "").lower()
            self._same_venue = (
                bool(self._analysis_venue)
                and bool(self._execution_venue)
                and self._analysis_venue == self._execution_venue
            )
            if self._same_venue:
                self._ema_basis = 0.0
                self._ema_ready = True
                self._history.clear()

    def seed(self, analysis_mid: float, execution_mid: float) -> None:
        with self._lock:
            a = _safe_float(analysis_mid)
            e = _safe_float(execution_mid)
            if a <= 0.0 or e <= 0.0:
                return
            self._last_analysis_mid = a
            self._last_execution_mid = e
            if self._same_venue:
                self._ema_basis = 0.0
                self._ema_ready = True
                return
            self._ema_basis = e - a
            self._ema_ready = True
            self._history.append((time.time(), self._ema_basis))

    def update(self, analysis_mid: float, execution_mid: float) -> None:
        with self._lock:
            a = _safe_float(analysis_mid)
            e = _safe_float(execution_mid)
            if a <= 0.0 or e <= 0.0:
                return
            self._last_analysis_mid = a
            self._last_execution_mid = e

            if self._same_venue:
                self._ema_basis = 0.0
                self._ema_ready = True
                return

            now = time.time()
            basis = e - a
            self._history.append((now, basis))
            cutoff = now - self.horizon_seconds
            while self._history and self._history[0][0] < cutoff:
                self._history.popleft()

            alpha = min(1.0, 2.0 / (len(self._history) + 1.0))
            if not self._ema_ready:
                self._ema_basis = basis
                self._ema_ready = True
            else:
                self._ema_basis = alpha * basis + (1.0 - alpha) * self._ema_basis

    @property
    def ready(self) -> bool:
        with self._lock:
            if self._same_venue:
                return True
            return bool(self._ema_ready and len(self._history) >= self.min_samples)

    @property
    def basis(self) -> float:
        with self._lock:
            if self._same_venue:
                return 0.0
            return float(self._ema_basis if self._ema_ready else 0.0)

    @property
    def basis_pct(self) -> float:
        with self._lock:
            ref = _safe_float(self._last_analysis_mid, 0.0)
            if ref <= 0.0:
                return 0.0
            basis_val = 0.0 if self._same_venue else float(self._ema_basis if self._ema_ready else 0.0)
            return (basis_val / ref) * 100.0

    def analysis_to_execution(self, analysis_price: float) -> float:
        with self._lock:
            status = self.validate()
            if not status.ready:
                raise RuntimeError("basis_unavailable: call seed()/update() or set same-venue mode before converting prices")
            return _safe_float(analysis_price, 0.0) + status.basis

    def execution_to_analysis(self, execution_price: float) -> float:
        with self._lock:
            status = self.validate()
            if not status.ready:
                raise RuntimeError("basis_unavailable: call seed()/update() or set same-venue mode before converting prices")
            return _safe_float(execution_price, 0.0) - status.basis

    # Backward-compatible aliases used in existing callers/tests.
    def okx_to_binance(self, price: float) -> float:
        with self._lock:
            status = self.validate()
            if not status.ready:
                raise RuntimeError("basis_unavailable: call seed()/update() or set same-venue mode before converting prices")
            return _safe_float(price, 0.0) + status.basis

    def binance_to_okx(self, price: float) -> float:
        with self._lock:
            status = self.validate()
            if not status.ready:
                raise RuntimeError("basis_unavailable: call seed()/update() or set same-venue mode before converting prices")
            return _safe_float(price, 0.0) - status.basis

    def validate(self, allow_unseeded_same_venue: bool = True) -> BasisStatus:
        with self._lock:
            snap = self.snapshot()
            if snap["same_venue"] and allow_unseeded_same_venue:
                return BasisStatus(True, "ok_same_venue", 0.0, 0.0, True)
            if not snap["ready"]:
                return BasisStatus(False, "basis_unavailable", snap["basis"], snap["basis_pct"], False)
            if abs(snap["basis_pct"]) > self.halt_threshold_pct:
                return BasisStatus(False, "basis_too_large", snap["basis"], snap["basis_pct"], True)
            return BasisStatus(True, "ok", snap["basis"], snap["basis_pct"], True)
