"""
triple_barrier_labeler.py — Triple-Barrier labeling for regime classification ground truth.

Labels:
     1 = TREND / Bull  (upper barrier touched first)
    -1 = BEAR          (lower barrier touched first)
     0 = RANGE         (time barrier reached first — no decisive move)
     2 = TOXIC         (overlaid on any bar where realized vol > 90th pct)

Reference: Lopez de Prado, "Advances in Financial Machine Learning", Ch. 3.

Usage:
    prices  = df["close"].values
    vol     = df["realized_vol"].values      # per-bar vol estimate
    labels  = triple_barrier_labels(prices, vol)
    labels4 = add_toxic_label(labels, pd.Series(vol))
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier_labels(
    prices: np.ndarray,
    volatility: np.ndarray,
    barrier_multiplier: float = 1.5,
    max_bars: int = 20,
    embargo_bars: int = 20,
) -> pd.Series:
    """
    Generate regime labels using the Triple-Barrier method.

    Args:
        prices:              Close prices, shape (N,)
        volatility:          Per-bar realized vol estimate, shape (N,)
        barrier_multiplier:  Horizontal barrier = multiplier * vol
        max_bars:            Vertical (time) barrier in bars
        embargo_bars:        Bars to null-out at end of each fold

    Returns:
        pd.Series of {-1, 0, 1}, NaN for embargoed/incomplete bars.
    """
    prices     = np.asarray(prices,     dtype=float)
    volatility = np.asarray(volatility, dtype=float)
    if prices.shape != volatility.shape:
        raise ValueError(
            f"prices and volatility must have the same shape, "
            f"got {prices.shape} vs {volatility.shape}"
        )
    N      = len(prices)
    labels = np.full(N, np.nan)

    for t in range(N - max_bars):
        upper = prices[t] * (1.0 + barrier_multiplier * volatility[t])
        lower = prices[t] * (1.0 - barrier_multiplier * volatility[t])
        label = 0   # default: time barrier
        for j in range(1, max_bars + 1):
            if t + j >= N:
                break
            if prices[t + j] >= upper:
                label = 1    # Bull / TREND
                break
            if prices[t + j] <= lower:
                label = -1   # Bear
                break
        labels[t] = label

    # Embargo: last `embargo_bars` of any training window must not be used as
    # training examples — they overlap with the test window look-forward.
    labels_series = pd.Series(labels)
    labels_series.iloc[-(embargo_bars):] = np.nan
    return labels_series


def add_toxic_label(
    labels: pd.Series,
    realized_vol: pd.Series,
    vol_threshold_quantile: float = 0.90,
) -> pd.Series:
    """
    Overlay TOXIC label (2) on top of Triple-Barrier labels.

    INTERFACE NOTE: This version accepts pd.Series inputs and is
    the public API in triple_barrier_labeler.py. A semantically
    identical but np.ndarray-based version exists in
    calibrate_regime.py. Do NOT merge or alias these without
    verifying interface compatibility — their callers use different
    data types throughout.

    Any bar where realized vol exceeds the 90th percentile becomes TOXIC.
    This yields the 4-class target: -1 (BEAR), 0 (RANGE), 1 (TREND), 2 (TOXIC).

    Args:
        labels:                   Output of triple_barrier_labels()
        realized_vol:             Per-bar realized vol, same index as labels
        vol_threshold_quantile:   Quantile above which a bar is TOXIC

    Returns:
        pd.Series with TOXIC bars set to 2.
    """
    vol_threshold = realized_vol.quantile(vol_threshold_quantile)
    result = labels.copy()
    result[realized_vol > vol_threshold] = 2
    return result


if __name__ == "__main__":
    # Smoke test
    rng = np.random.default_rng(0)
    n   = 500
    px  = np.cumprod(1.0 + rng.normal(0, 0.003, n))
    vol = np.abs(rng.normal(0.003, 0.001, n))

    lbl = triple_barrier_labels(px, vol, barrier_multiplier=1.5, max_bars=20)
    lbl4 = add_toxic_label(lbl, pd.Series(vol))

    counts = lbl4.value_counts(dropna=True).sort_index()
    print("Label distribution:", counts.to_dict())
    assert set(lbl4.dropna().unique()).issubset({-1.0, 0.0, 1.0, 2.0}), \
        "Unexpected label values"
    print("Smoke test passed.")
