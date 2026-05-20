"""LOB imbalance metrics from most recent snapshots only."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..data.l2_snapshot import L2Snapshot


@dataclass(frozen=True)
class LOBImbalanceFeatures:
    ofi_zscore: float = 0.0
    queue_imbalance: float = 0.0
    depth_replenishment_ratio: float = 0.0
    stale: bool = True


def _depth_total(levels) -> float:
    return float(sum(max(0.0, l.size) for l in levels))


def compute_lob_imbalance(as_of_ts: float, l2_snapshots: Sequence[L2Snapshot], *, stale_seconds: int = 120) -> LOBImbalanceFeatures:
    snaps = [s for s in l2_snapshots if s.timestamp <= as_of_ts]
    if not snaps:
        return LOBImbalanceFeatures(stale=True)

    curr = snaps[-1]
    bid_depth = _depth_total(curr.bids)
    ask_depth = _depth_total(curr.asks)
    denom = max(bid_depth + ask_depth, 1e-9)
    queue_imb = float((bid_depth - ask_depth) / denom)

    top_bid = curr.bids[0].price if curr.bids else np.nan
    top_ask = curr.asks[0].price if curr.asks else np.nan
    spread_valid = bool(np.isfinite(top_bid) and np.isfinite(top_ask) and top_ask > top_bid)
    if not spread_valid:
        return LOBImbalanceFeatures(stale=True)

    ofi_series = []
    for s in snaps[-20:]:
        b = _depth_total(s.bids)
        a = _depth_total(s.asks)
        d = max(b + a, 1e-9)
        ofi_series.append((b - a) / d)
    arr = np.asarray(ofi_series, dtype=float)
    if arr.size >= 5 and float(np.std(arr)) > 0.0:
        ofi_z = float((arr[-1] - float(np.mean(arr))) / float(np.std(arr)))
    else:
        ofi_z = 0.0

    if len(snaps) >= 2:
        prev = snaps[-2]
        prev_total = max(_depth_total(prev.bids) + _depth_total(prev.asks), 1e-9)
        repl = float(denom / prev_total)
    else:
        repl = 1.0

    stale = (as_of_ts - curr.timestamp) > stale_seconds
    return LOBImbalanceFeatures(ofi_z, queue_imb, repl, stale)
