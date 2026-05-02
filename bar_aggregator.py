"""
bar_aggregator.py — Phase 4 Fix 4 (B005): 1-min → 5-min / 15-min OHLCV resampling

PROBLEM
-------
At 1-min resolution the SMA strategy hit only 16.7% of 11 bps round-trip cost.
Signal-to-noise is too low at that granularity for the features/costs involved.

SOLUTION
--------
Aggregate 1-min bars into 5-min and 15-min bars before feeding the BacktestEngine.
Lower bar count → fewer noise signals → higher hit-rate per signal.

PUBLIC API
----------
    from bar_aggregator import BarAggregator, resample_bars

    agg = BarAggregator(resolution_minutes=5)
    five_min_bars = agg.aggregate(one_min_bars)

    # Or one-shot convenience function:
    five_min  = resample_bars(one_min_bars, minutes=5)
    fifteen   = resample_bars(one_min_bars, minutes=15)

INPUT FORMAT (one_min_bars)
---------------------------
    List[List[Any]]  —  each row: [timestamp_ms, open, high, low, close, volume, ...]
    Rows must be sorted ascending by timestamp. Extra columns are preserved on the
    LAST bar of each aggregation window (useful for carrying metadata forward).

OUTPUT FORMAT
-------------
    Same list-of-lists schema: [timestamp_ms, open, high, low, close, volume, ...]
    timestamp_ms is the OPEN timestamp of the aggregated window.

USAGE IN BACKTEST
-----------------
    from bar_aggregator import resample_bars
    from backtest_engine import BacktestEngine, BacktestConfig

    raw_1m   = load_1m_ohlcv(...)
    bars_5m  = resample_bars(raw_1m, minutes=5)
    bars_15m = resample_bars(raw_1m, minutes=15)

    engine = BacktestEngine(BacktestConfig(fee_bps=8.0, slippage_bps=3.0))
    result_5m  = engine.run_backtest(bars_5m)
    result_15m = engine.run_backtest(bars_15m)
"""

from __future__ import annotations

import math
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------

class BarAggregator:
    """
    Aggregates 1-min (or any base-resolution) OHLCV bars into wider bars.

    Parameters
    ----------
    resolution_minutes : int
        Target bar width in minutes (e.g. 5 or 15).
    base_minutes : int
        Width of the input bars in minutes (default 1).
    allow_partial : bool
        If True, include an incomplete final window; if False (default), discard it.
    """

    SUPPORTED_RESOLUTIONS = (2, 3, 5, 10, 15, 20, 30, 60, 120, 240, 480, 1440)

    def __init__(
        self,
        resolution_minutes: int = 5,
        base_minutes: int = 1,
        allow_partial: bool = False,
    ) -> None:
        if resolution_minutes < base_minutes:
            raise ValueError(
                f"resolution_minutes ({resolution_minutes}) must be >= "
                f"base_minutes ({base_minutes})"
            )
        if resolution_minutes % base_minutes != 0:
            raise ValueError(
                f"resolution_minutes ({resolution_minutes}) must be an integer "
                f"multiple of base_minutes ({base_minutes})"
            )
        self.resolution_minutes = int(resolution_minutes)
        self.base_minutes       = int(base_minutes)
        self.bars_per_window    = self.resolution_minutes // self.base_minutes
        self.allow_partial      = bool(allow_partial)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def aggregate(self, bars: List[List[Any]]) -> List[List[Any]]:
        """
        Aggregate input bars into wider bars.

        Parameters
        ----------
        bars : List[List[Any]]
            Sorted 1-min OHLCV rows: [ts_ms, o, h, l, c, v, ...]

        Returns
        -------
        List[List[Any]]
            Aggregated bars in the same schema.
        """
        if not bars:
            return []

        valid = [r for r in bars if isinstance(r, (list, tuple)) and len(r) >= 6]
        if not valid:
            return []

        # Sort ascending by timestamp (defensive)
        valid = sorted(valid, key=lambda r: _safe_float(r[0], 0.0))

        result: List[List[Any]] = []
        window: List[List[Any]] = []
        window_open_ts: Optional[float] = None

        bar_ms = self.base_minutes * 60_000
        win_ms = self.resolution_minutes * 60_000

        for row in valid:
            ts = _safe_float(row[0], 0.0)

            if window_open_ts is None:
                # Align window start to a clean multiple of win_ms
                window_open_ts = math.floor(ts / win_ms) * win_ms

            # If this bar belongs to a new window, flush the current one
            if ts >= window_open_ts + win_ms:
                if window:
                    agg = self._merge(window, window_open_ts)
                    if agg is not None:
                        result.append(agg)
                # Advance window boundary (handle gaps of >1 window)
                window_open_ts = math.floor(ts / win_ms) * win_ms
                window = []

            window.append(row)

        # Flush trailing window: always flush if complete, only flush if partial when allow_partial=True
        if window:
            is_complete = len(window) >= self.bars_per_window
            if is_complete or self.allow_partial:
                agg = self._merge(window, window_open_ts)
                if agg is not None:
                    result.append(agg)

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _merge(
        self, window: List[List[Any]], open_ts: Optional[float]
    ) -> Optional[List[Any]]:
        """Merge a list of same-window bars into one aggregated bar."""
        if not window:
            return None

        ts_out = open_ts if open_ts is not None else _safe_float(window[0][0], 0.0)
        o      = _safe_float(window[0][1])
        h      = max(_safe_float(r[2]) for r in window)
        l      = min(_safe_float(r[3]) for r in window)
        c      = _safe_float(window[-1][4])
        v      = sum(_safe_float(r[5]) for r in window)

        if o <= 0 or c <= 0 or h <= 0 or l <= 0:
            return None

        # Preserve any extra columns from the LAST row in the window
        extra = list(window[-1][6:]) if len(window[-1]) > 6 else []

        return [ts_out, o, h, l, c, v] + extra

    def __repr__(self) -> str:
        return (
            f"BarAggregator(resolution={self.resolution_minutes}m, "
            f"base={self.base_minutes}m, "
            f"bars_per_window={self.bars_per_window}, "
            f"allow_partial={self.allow_partial})"
        )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def resample_bars(
    bars: List[List[Any]],
    minutes: int = 5,
    base_minutes: int = 1,
    allow_partial: bool = False,
) -> List[List[Any]]:
    """
    One-shot wrapper around BarAggregator.

    Parameters
    ----------
    bars          : List[List[Any]]   1-min OHLCV bars
    minutes       : int               target resolution in minutes (default 5)
    base_minutes  : int               input bar width in minutes (default 1)
    allow_partial : bool              include incomplete trailing window (default False)

    Returns
    -------
    List[List[Any]] aggregated bars
    """
    return BarAggregator(
        resolution_minutes=minutes,
        base_minutes=base_minutes,
        allow_partial=allow_partial,
    ).aggregate(bars)


# ---------------------------------------------------------------------------
# Signal-to-noise diagnostic (utility used by audit scripts)
# ---------------------------------------------------------------------------

def snr_summary(
    bars_1m: List[List[Any]],
    cost_bps: float = 11.0,
) -> dict:
    """
    Compute average bar range (bps) at 1m, 5m, 15m and compare against round-trip cost.

    Returns a dict with per-resolution metrics useful for choosing bar size.
    """
    def _avg_range_bps(bars: List[List[Any]]) -> float:
        ranges = []
        for r in bars:
            try:
                h, l, c = _safe_float(r[2]), _safe_float(r[3]), _safe_float(r[4])
                if c > 0 and h > l:
                    ranges.append((h - l) / c * 10_000.0)
            except Exception:
                continue
        return float(sum(ranges) / len(ranges)) if ranges else 0.0

    bars_5m  = resample_bars(bars_1m, minutes=5)
    bars_15m = resample_bars(bars_1m, minutes=15)

    r1  = _avg_range_bps(bars_1m)
    r5  = _avg_range_bps(bars_5m)
    r15 = _avg_range_bps(bars_15m)

    return {
        "cost_bps":        cost_bps,
        "1m":  {"n_bars": len(bars_1m),  "avg_range_bps": round(r1,  2), "snr": round(r1  / cost_bps, 2)},
        "5m":  {"n_bars": len(bars_5m),  "avg_range_bps": round(r5,  2), "snr": round(r5  / cost_bps, 2)},
        "15m": {"n_bars": len(bars_15m), "avg_range_bps": round(r15, 2), "snr": round(r15 / cost_bps, 2)},
        "recommendation": (
            "15m" if r15 / cost_bps >= 3.0 else
            "5m"  if r5  / cost_bps >= 2.0 else
            "1m"
        ),
    }


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    # Synthetic 1-min bars: 60 bars of BTC-like price
    import random
    random.seed(42)
    price = 69000.0
    bars_1m = []
    for i in range(60):
        o = price
        h = o * (1 + random.uniform(0, 0.002))
        l = o * (1 - random.uniform(0, 0.002))
        c = o * (1 + random.uniform(-0.001, 0.001))
        v = random.uniform(0.5, 5.0)
        bars_1m.append([i * 60_000, o, h, l, c, v])
        price = c

    bars_5m  = resample_bars(bars_1m, minutes=5)
    bars_15m = resample_bars(bars_1m, minutes=15)
    summary  = snr_summary(bars_1m, cost_bps=11.0)

    print(f"Input 1m bars : {len(bars_1m)}")
    print(f"Output 5m bars: {len(bars_5m)}  (expected ~12)")
    print(f"Output 15m bars:{len(bars_15m)} (expected ~4)")
    print("\nSNR summary:")
    print(json.dumps(summary, indent=2))

    assert len(bars_5m)  == 12, f"Expected 12, got {len(bars_5m)}"
    assert len(bars_15m) == 4,  f"Expected 4, got {len(bars_15m)}"
    print("\nAll assertions passed.")
