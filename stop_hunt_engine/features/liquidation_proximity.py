"""Liquidation cluster proximity and cascade likelihood features."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..data.derivatives import LiquidationCluster


@dataclass(frozen=True)
class LiquidationProximityFeatures:
    nearest_long_cluster_dist_pct: float = 0.0
    nearest_short_cluster_dist_pct: float = 0.0
    cascade_amplification_flag: bool = False
    stale: bool = True


def compute_liquidation_proximity(
    as_of_ts: float,
    price: float,
    liquidation_clusters: Sequence[LiquidationCluster],
    *,
    stale_seconds: int = 3600,
) -> LiquidationProximityFeatures:
    clusters = [c for c in liquidation_clusters if c.as_of <= as_of_ts]
    if not clusters or price <= 0:
        return LiquidationProximityFeatures(stale=True)

    longs = [c for c in clusters if str(c.side).lower() == "long"]
    shorts = [c for c in clusters if str(c.side).lower() == "short"]
    if not longs or not shorts:
        return LiquidationProximityFeatures(stale=True)

    long_dist = min(abs(price - c.price) / price for c in longs)
    short_dist = min(abs(price - c.price) / price for c in shorts)

    nearby = [c for c in clusters if abs(price - c.price) / price <= 0.01]
    total_nearby = sum(max(0.0, c.size_usd) for c in nearby)
    directional = max(
        sum(max(0.0, c.size_usd) for c in nearby if str(c.side).lower() == "long"),
        sum(max(0.0, c.size_usd) for c in nearby if str(c.side).lower() == "short"),
    )
    cascade = bool(total_nearby > 0 and directional / total_nearby >= 0.65)
    freshest_ts = max(c.as_of for c in clusters)
    stale = (as_of_ts - freshest_ts) > stale_seconds

    return LiquidationProximityFeatures(float(long_dist), float(short_dist), cascade, stale)
