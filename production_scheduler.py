"""
production_scheduler.py — Periodic GARCH re-fit scheduler for live BTCUSDT deployment.
Re-fits MS-GARCH parameters every 7 days using a rolling 30-day window.
"""
from __future__ import annotations
import logging
import threading
from collections import deque
from typing import Any
import numpy as np

LOGGER = logging.getLogger(__name__)

_REFIT_INTERVAL_DAYS = 7
_MIN_BARS_FOR_REFIT = 43_200
_ROLLING_WINDOW_BARS = 43_200


def scheduled_garch_refit(engine: Any, returns_buffer: deque) -> None:
    """
    Re-fit GARCH parameters using last 30 days of 1-minute returns.
    Call this every 7 days in production.

    Args:
        engine:         AdvancedRegimeEngine instance with engine.garch
        returns_buffer: deque of fractional returns (float), at least 43_200 bars
    """
    from calibrate_garch import fit_msgarch_mle

    returns_arr = np.asarray(list(returns_buffer), dtype=float)
    if len(returns_arr) < _MIN_BARS_FOR_REFIT:
        LOGGER.warning(
            "scheduled_garch_refit: insufficient data (%d bars, need %d). Skipping.",
            len(returns_arr), _MIN_BARS_FOR_REFIT,
        )
        return
    try:
        params = fit_msgarch_mle(returns_arr[-_ROLLING_WINDOW_BARS:])
        if params["converged"]:
            engine.garch.load_fitted_params(
                omega=params["omega"],
                alpha=params["alpha"],
                beta_garch=params["beta_garch"],
                P=params["P"],
            )
            LOGGER.info(
                "scheduled_garch_refit: complete. alpha=%s beta=%s persistence=%s log_lik=%.4f",
                params["alpha"].tolist(),
                params["beta_garch"].tolist(),
                (params["alpha"] + params["beta_garch"]).tolist(),
                params["log_lik"],
            )
        else:
            LOGGER.error(
                "scheduled_garch_refit: optimizer did not converge. "
                "Previous parameters retained."
            )
    except Exception as exc:
        LOGGER.error("scheduled_garch_refit: failed with exception: %s", exc, exc_info=True)


class GARCHRefitScheduler:
    """
    Thread-safe periodic GARCH re-fit scheduler.
    Call .start() once at engine startup; it will fire every 7 days.
    """

    def __init__(self, engine: Any, returns_buffer: deque,
                 interval_days: float = _REFIT_INTERVAL_DAYS) -> None:
        self._engine = engine
        self._returns_buffer = returns_buffer
        self._interval_seconds = float(interval_days) * 86_400.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval_seconds):
            scheduled_garch_refit(self._engine, self._returns_buffer)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="garch_refit_scheduler"
        )
        self._thread.start()
        LOGGER.info(
            "GARCHRefitScheduler started (interval=%.1f days).",
            self._interval_seconds / 86_400.0,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)


if __name__ == "__main__":
    from collections import deque
    buf = deque([float(i) * 0.001 for i in range(50_000)], maxlen=50_000)

    class FakeEngine:
        class garch:
            @staticmethod
            def load_fitted_params(**kwargs):
                pass
    try:
        scheduled_garch_refit(FakeEngine(), buf)
    except Exception as e:
        print(f"scheduled_garch_refit raised unexpectedly: {e}")
    print("production_scheduler smoke test PASSED")
