"""Canonical numeric utility functions for the BTC trading system.
All other files must import from here — local duplicates are forbidden.
"""
from __future__ import annotations
import math
from typing import Optional


def safe_float(
    value: object,
    default: float = 0.0,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Safely coerce value to float. Returns default on None, NaN, Inf, or error.
    Applies clamp if min_val or max_val are provided."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]. Raises ValueError if lo > hi."""
    if lo > hi:
        raise ValueError(f"clamp: lo ({lo}) must be <= hi ({hi})")
    return max(lo, min(hi, float(value)))


def validate_alpha(alpha_value: float, name: str = "alpha") -> float:
    """Assert alpha is in [0.0, 1.0]. Raises ValueError with descriptive message."""
    v = float(alpha_value)
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"{name} must be in [0.0, 1.0], got {v}")
    return v
