from __future__ import annotations

from typing import Dict, List

from ..features.feature_vector import StopHuntFeatureVector
from ..features.funding_pressure import FundingPressureFeatures
from ..features.liquidation_proximity import LiquidationProximityFeatures
from ..features.lob_imbalance import LOBImbalanceFeatures
from ..features.oi_dynamics import OIDynamicsFeatures
from ..features.pool_distance import PoolDistanceFeatures
from ..features.regime_context import RegimeContextFeatures
from ..features.volume_trap import VolumeTrapFeatures


def record_to_fv(sample: Dict[str, object]) -> StopHuntFeatureVector:
    f = sample["derived_features"]  # type: ignore[index]
    if not isinstance(f, dict):
        raise ValueError("sample missing derived_features")
    stale: List[str] = []
    return StopHuntFeatureVector(
        int(sample["row_index"]),
        float(sample["feature_available_ts_ms"]) / 1000.0,
        PoolDistanceFeatures(f["pool_dist_to_high_pct"], f["pool_dist_to_low_pct"], f["pool_high_pool_age_bars"], f["pool_low_pool_age_bars"], f["pool_round_number_proximity_bps"], False),
        FundingPressureFeatures(f["funding_rate_8h"], f["funding_z30d"], f["funding_oi_sign_divergence"], False),
        OIDynamicsFeatures(f["oi_delta_oi_velocity"], f["oi_pct_change_1h"], bool(f["oi_buildup_flag"]), f["oi_price_divergence_sign"], False),
        VolumeTrapFeatures(f["volume_wick_to_body_ratio"], f["volume_upper_wick_pct"], f["volume_lower_wick_pct"], f["volume_zscore"], f["volume_at_extreme_vs_close"], bool(f["volume_exhaustion_candle_flag"]), False),
        LOBImbalanceFeatures(f["lob_ofi_zscore"], f["lob_queue_imbalance"], f["lob_depth_replenishment_ratio"], False),
        LiquidationProximityFeatures(f["liq_nearest_long_cluster_dist_pct"], f["liq_nearest_short_cluster_dist_pct"], bool(f["liq_cascade_amplification_flag"]), True),
        RegimeContextFeatures(str(sample.get("regime_label", "unknown")), f["regime_confidence"], f["regime_conviction"], f["regime_edge_score"], bool(f["regime_signal_valid"]), f["regime_expected_volatility"], False),
        stale,
    )
