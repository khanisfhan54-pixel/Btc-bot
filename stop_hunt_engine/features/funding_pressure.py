"""Funding pressure features with timestamp-safe rolling stats."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..data.derivatives import FundingPoint, OpenInterestPoint


@dataclass(frozen=True)
class FundingPressureFeatures:
    funding_rate_8h: float = 0.0
    funding_z30d: float = 0.0
    funding_oi_sign_divergence: float = 0.0
    stale: bool = True


def _clip01(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def compute_funding_pressure(
    as_of_ts: float,
    funding: Sequence[FundingPoint],
    open_interest: Sequence[OpenInterestPoint],
    *,
    z_window: int = 90,
    stale_seconds: int = 12 * 3600,
) -> FundingPressureFeatures:
    """Compute funding pressure from history up to ``as_of_ts`` only."""
    f_hist = [p for p in funding if p.timestamp <= as_of_ts]
    oi_hist = [p for p in open_interest if p.timestamp <= as_of_ts]
    if not f_hist:
        return FundingPressureFeatures(stale=True)

    latest_f = f_hist[-1]
    funding_rate = float(latest_f.rate_8h)

    recent = np.asarray([float(p.rate_8h) for p in f_hist[-z_window:]], dtype=float)
    if recent.size >= 5 and float(np.std(recent)) > 0.0:
        f_z = float((funding_rate - float(np.mean(recent))) / float(np.std(recent)))
    else:
        f_z = 0.0

    if len(oi_hist) >= 2:
        oi_delta = float(oi_hist[-1].oi_usd - oi_hist[-2].oi_usd)
        divergence = _clip01(float(np.sign(funding_rate) * np.sign(oi_delta)))
        oi_stale = (as_of_ts - oi_hist[-1].timestamp) > stale_seconds
    else:
        divergence = 0.0
        oi_stale = True

    funding_stale = (as_of_ts - latest_f.timestamp) > stale_seconds
    return FundingPressureFeatures(
        funding_rate_8h=funding_rate,
        funding_z30d=f_z,
        funding_oi_sign_divergence=divergence,
        stale=bool(funding_stale or oi_stale),
    )
