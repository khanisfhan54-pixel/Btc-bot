"""shadow_gate.py — UPGRADE-5.10.

Automatic shadow-mode gate that prevents promotion to live trading while
the engine's model drawdown disagrees with the realised portfolio
drawdown. Lives in the orchestration layer (NOT in
``advanced_regime_engine``) so the engine never auto-mutes itself based
on portfolio data.

Wire ``ShadowGate.assert_safe_for_live(realized_dd)`` into the live
promotion code path. Call ``ShadowGate.check(realized_dd)`` periodically
during the shadow window so the lockout timer is updated even when
promotion is not yet attempted.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

_log = logging.getLogger(__name__)

_SHADOW_CHECK_INTERVAL_S: float = 300.0      # every 5 minutes
_SHADOW_DIVERGENCE_LOCKOUT_S: float = 86_400.0  # 24 h


class ShadowGate:
    """Prevents promotion to live mode when ``reconcile_drawdown`` reports
    divergence at any point in the past ``_SHADOW_DIVERGENCE_LOCKOUT_S``
    window.

    Parameters
    ----------
    engine:
        An ``AdvancedRegimeEngine`` instance exposing
        ``reconcile_drawdown(realized_dd, *, gap_threshold=...)``.
    gap_threshold:
        Maximum tolerated absolute gap (as a fraction, e.g. ``0.05`` for
        5pp) between model DD and realised DD before the gate latches.
    """

    def __init__(self, engine: Any, gap_threshold: float = 0.05) -> None:
        self._engine = engine
        self._gap_threshold = float(gap_threshold)
        self._last_divergence_at: float = 0.0
        self._last_check_at: float = 0.0
        self._last_reconciliation: Optional[dict] = None

    @property
    def last_reconciliation(self) -> Optional[dict]:
        """The most recent ``reconcile_drawdown`` payload (or ``None``)."""
        return self._last_reconciliation

    @property
    def lockout_remaining_s(self) -> float:
        """Seconds remaining in the divergence lockout window."""
        if self._last_divergence_at <= 0.0:
            return 0.0
        elapsed = time.time() - self._last_divergence_at
        return max(0.0, _SHADOW_DIVERGENCE_LOCKOUT_S - elapsed)

    def check(self, realized_dd: float) -> bool:
        """Run one reconciliation; return True if currently safe to remain
        or be promoted to live. Always updates the internal lockout timer
        when divergence is detected. Never raises.
        """
        try:
            rec = self._engine.reconcile_drawdown(
                realized_dd, gap_threshold=self._gap_threshold
            )
        except Exception:
            _log.exception("ShadowGate: reconcile_drawdown raised; failing closed")
            return False

        self._last_check_at = time.time()
        self._last_reconciliation = rec
        if bool(rec.get("divergence")):
            self._last_divergence_at = self._last_check_at
            _log.warning(
                "ShadowGate: drawdown divergence detected — "
                "gap=%.2f pp, model=%.2f%%, realized=%.2f%%",
                float(rec.get("gap_pp", 0.0)) * 100.0,
                float(rec.get("model_drawdown", 0.0)) * 100.0,
                float(rec.get("realized_drawdown", 0.0)) * 100.0,
            )
        return self.lockout_remaining_s == 0.0

    def assert_safe_for_live(self, realized_dd: float) -> None:
        """Raise ``RuntimeError`` if a divergence has fired within the past
        24 h. Use this in the live-promotion code path.
        """
        if not self.check(realized_dd):
            raise RuntimeError(
                "ShadowGate: drawdown divergence detected within last 24 h — "
                "live promotion blocked. Inspect reconcile_drawdown() output "
                f"({self._last_reconciliation!r})."
            )


__all__ = [
    "ShadowGate",
    "_SHADOW_CHECK_INTERVAL_S",
    "_SHADOW_DIVERGENCE_LOCKOUT_S",
]
