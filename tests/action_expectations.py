from __future__ import annotations

from typing import Any

EPS = 1e-12


def _correlation_summary_from_meta(meta: dict[str, Any], timeframe: str | None = None) -> dict[str, Any]:
    tf_breakdown = meta.get("tf_fusion_breakdown", {}) if isinstance(meta, dict) else {}
    selected_tf = timeframe if timeframe is not None else meta.get("dominant_timeframe") if isinstance(meta, dict) else None
    if selected_tf not in tf_breakdown and tf_breakdown:
        selected_tf = next(iter(tf_breakdown.keys()))
    tf_meta = tf_breakdown.get(selected_tf or "", {})
    return tf_meta.get("fusion_meta", {}).get("correlation_summary", {})


def is_unsafe_aggregation_meta(meta: dict[str, Any], timeframe: str | None = None) -> bool:
    summary = _correlation_summary_from_meta(meta, timeframe=timeframe)
    denom = summary.get("total_adjusted_weight")
    if denom is None:
        return True
    if bool(summary.get("low_aggregate_weight")):
        return True
    return abs(float(denom)) <= EPS


def expected_action_from_meta(meta: dict[str, Any], timeframe: str | None = None) -> str:
    summary = _correlation_summary_from_meta(meta, timeframe=timeframe)
    denom = summary.get("total_adjusted_weight")
    attenuated_edge = float(summary.get("attenuated_blended_edge_bps", 0.0) or 0.0)

    if (
        bool(summary.get("low_aggregate_weight"))
        or denom is None
        or abs(float(denom)) <= EPS
        or attenuated_edge == 0.0
    ):
        return "HOLD"
    return "BUY" if attenuated_edge > 0.0 else "SELL"
