"""
calibrate_confirmation_ticks.py — Data-driven calibration of _REGIME_CONFIRMATION_TICKS.

Run on labeled training data to estimate BTC regime persistence and
compute the confirmation delay that minimises lag without instability.

Usage:
    from calibrate_confirmation_ticks import optimal_confirmation_ticks
    ticks = optimal_confirmation_ticks(true_labels)
"""
from __future__ import annotations

import numpy as np
from typing import Iterable


def compute_run_lengths(labels: np.ndarray) -> np.ndarray:
    """
    Compute the length of each contiguous regime run.
    Non-finite values (NaN from embargo gaps) are filtered out
    before run-length computation so they do not fragment runs.
    """
    arr = np.asarray(labels, dtype=float)
    # FIX-NAN: remove NaN/Inf labels produced by embargo windows
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return np.array([], dtype=int)
    runs = []
    count = 1
    for i in range(1, len(arr)):
        if arr[i] == arr[i - 1]:
            count += 1
        else:
            runs.append(count)
            count = 1
    runs.append(count)
    return np.array(runs, dtype=int)


def optimal_confirmation_ticks(
    true_labels: np.ndarray,
    candidate_ticks: Iterable[int] = range(1, 8),
    max_fraction_of_median: float = 0.50,
) -> int:
    """
    Estimate BTC regime persistence from labeled data and return a
    confirmation tick count that:
      - stays below `max_fraction_of_median` × median run length
      - avoids excessive lag without instability
    """
    runs = compute_run_lengths(np.asarray(true_labels))
    if len(runs) == 0:
        return 2

    median_run = float(np.median(runs))
    mean_run = float(np.mean(runs))
    print(f"[calibrate_confirmation_ticks] Run lengths — "
          f"median: {median_run:.1f}, mean: {mean_run:.1f} bars")

    max_allowed = max(1, int(median_run * max_fraction_of_median))
    recommended = max(1, int(median_run * 0.30))
    recommended = min(recommended, max_allowed)

    candidates = [t for t in candidate_ticks if 1 <= t <= max_allowed]
    if candidates:
        recommended = max(candidates[0], min(recommended, candidates[-1]))

    print(f"[calibrate_confirmation_ticks] "
          f"max_allowed={max_allowed}, recommended={recommended}")
    return int(recommended)
