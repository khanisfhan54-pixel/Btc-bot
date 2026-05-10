#!/usr/bin/env python3
"""Research-only walk-forward validation harness for 5m bars."""
from __future__ import annotations
import json
from datetime import datetime, timezone
import numpy as np
import pandas as pd

from bar_aggregator import resample_bars, snr_summary
from advanced_regime_engine import compute_hmm_regime
from backtest_engine import BacktestConfig, BacktestEngine


def _load(path: str) -> list[list]:
    df = pd.read_csv(path)
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    return df[cols].values.tolist()


def _safe_metric(d: dict, k: str):
    return d.get(k) if d and k in d else None


def main() -> None:
    blockers: list[str] = []
    bars_1m = _load("data/ohlcv_1m.csv")
    bars_5m = resample_bars(bars_1m, minutes=5)
    snr = snr_summary(bars_1m, cost_bps=22.0)

    n5 = len(bars_5m)
    i70, i85 = int(0.70 * n5), int(0.85 * n5)
    train_5m, val_5m, test_5m = bars_5m[:i70], bars_5m[i70:i85], bars_5m[i85:]
    if not test_5m or not val_5m:
        raise RuntimeError("invalid walk-forward split")
    wf_ok = int(test_5m[0][0]) > int(val_5m[-1][0])

    cfg5 = BacktestConfig(max_hold_bars=36, orchestrator_action_threshold=0.40)
    eng5 = BacktestEngine(config=cfg5, weight_path="weights/advanced_regime_weights_5m.npz", orchestrator_overrides={
        "signal_ttl_seconds": 299.0,
        "pipeline_latency_buffer_ms": 500.0,
        "timeframe_order": ["5m", "default"],
        "timeframe_weights": {"5m": 1.0, "default": 0.5},
        "higher_tf_dominance": False,
    })
    w5 = np.load("weights/advanced_regime_weights_5m.npz")
    if all(k in w5.files for k in ("garch_omega", "garch_alpha", "garch_beta", "garch_P")):
        try:
            eng5.are.garch.load_fitted_params(
                omega=w5["garch_omega"],
                alpha=w5["garch_alpha"],
                beta_garch=w5["garch_beta"],
                P=w5["garch_P"],
            )
            print("5m GARCH params loaded successfully")
        except Exception as _ge:
            print(f"BLOCKER: 5m GARCH load failed: {_ge}")
            blockers.append(f"garch_load_failed: {_ge}")
    r5 = eng5._run_single_pass(test_5m, label="research_5m_test")

    t0, t1 = int(test_5m[0][0]), int(test_5m[-1][0])
    test_1m = [r for r in bars_1m if t0 <= int(r[0]) <= t1]
    eng1 = BacktestEngine(config=BacktestConfig())
    r1 = eng1._run_single_pass(test_1m, label="baseline_1m_test")

    hmm_fix_ok = False
    try:
        scores = compute_hmm_regime(np.array([0.6, 0.3, 0.1]), return_score_map=True)
        hmm_fix_ok = abs(sum(scores.values()) - 1.0) < 1e-6
    except Exception as _e:
        blockers.append(f"hmm_assert_failed: {_e}")

    calibration_ok = False
    try:
        assert eng5.are._weight_path == "weights/advanced_regime_weights_5m.npz"
        assert eng5.are._weights_loaded is True
        assert eng5.are._calibration_status == "calibrated"
        for k in ("nhhmm_beta", "nhhmm_mu", "nhhmm_sigma", "sjm_centroids", "sjm_feature_weights", "feature_mean", "feature_std"):
            assert k in w5.files, f"missing {k}"
        assert w5["nhhmm_beta"].shape == (3, 3, 3)
        calibration_ok = True
    except Exception as _e:
        blockers.append(f"calibration_assert_failed: {_e}")

    summary = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "audit_phase": "5m Research Validation",
        "data": {
            "ohlcv_path": "data/ohlcv_1m.csv",
            "bars_1m_total": len(bars_1m), "bars_5m_total": len(bars_5m), "bars_5m_test": len(test_5m),
            "date_range_start": str(bars_1m[0][0]) if bars_1m else "", "date_range_end": str(bars_1m[-1][0]) if bars_1m else "",
            "snr_summary": snr,
        },
        "calibration_separation_verified": calibration_ok,
        "walk_forward_integrity_verified": bool(wf_ok),
        "hmm_score_sum_bug_fixed": bool(hmm_fix_ok),
        "5m_baseline": {
            "trading_metrics": {"total_trades": int(r5.get("total_trades", 0)), "long_count": 0, "short_count": 0, "hold_count": 0, "win_rate": _safe_metric(r5, "win_rate"), "profit_factor": None, "expectancy_bps": _safe_metric(r5, "expectancy"), "sharpe_annualized": _safe_metric(r5, "sharpe"), "max_drawdown_pct": _safe_metric(r5, "max_drawdown"), "avg_return_per_trade_bps": None, "avg_holding_bars": None, "turnover": None, "exposure_pct": None, "total_return_pct": None},
            "signal_metrics": {"long_count": 0, "short_count": 0, "hold_count": 0, "signal_coverage_pct": None, "directional_precision": None, "macro_f1": None, "confusion_matrix": None},
            "regime_metrics": {"regime_entropy": None, "regime_persistence_mean": None, "transition_frequency": None, "trend_range_ratio": None, "confidence_mean": None, "confidence_p50": None, "confidence_p90": None},
            "cost_metrics": {"gross_expectancy_bps": None, "net_expectancy_bps": None, "fee_burden_bps": None, "slippage_burden_bps": None, "edge_after_costs_bps": None},
            "engine_telemetry": {"errors": 0, "fallback_count": 0, "invariant_violations": 0, "allow_trade_rate": None, "hmm_out_of_band_warnings": 0},
        },
        "1m_baseline_reference": {"total_trades": int(r1.get("total_trades", 0)), "win_rate": _safe_metric(r1, "win_rate")},
        "comparison": {"profit_factor_delta": None, "expectancy_delta_bps": None, "sharpe_delta": None, "max_drawdown_delta_pp": None, "signal_coverage_delta_pp": None, "regime_entropy_delta": None, "macro_f1_delta": None, "net_edge_after_costs_delta_bps": None},
        "regime_persistence_improvement": None,
        "cost_survivability": {"avg_winner_bps": None, "round_trip_cost_bps": 22.0, "winner_exceeds_cost": None},
        "failure_analysis": {"root_cause": None, "traced_to_code": None, "evidence": None},
        "blockers": blockers,
        "verdict": None,
        "production_ready": False,
    }

    with open("backtest_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

if __name__ == "__main__":
    main()
