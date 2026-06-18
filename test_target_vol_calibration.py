import json
import threading

import numpy as np

from advanced_regime_engine import AdvancedRegimeEngine, _validate_output_schema
from regime_vol_calibration import (
    calibrate_target_vol,
    load_target_vol_artifact,
    write_target_vol_artifact,
)


def test_calibrate_target_vol_known_std_and_monotonic_percentiles():
    rng = np.random.default_rng(42)
    sigma = 5e-4
    returns = rng.normal(0.0, sigma, 9000)
    timestamps = np.arange(returns.size) * 60.0
    result = calibrate_target_vol(
        returns, timestamps, window_days=4, percentile=75.0, min_samples=5000
    )
    assert abs(result["calibrated_target_vol"] - sigma) < 7.5e-5
    metrics = result["validation_metrics"]
    vals = [metrics[k] for k in ("p10", "p25", "p50", "p75", "p90", "p95", "p99")]
    assert vals == sorted(vals)


def test_calibrate_target_vol_walk_forward_ignores_future_values_for_prefix():
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0, 5e-4, 9000)
    timestamps = np.arange(returns.size) * 60.0
    t = 7000
    base = calibrate_target_vol(returns[:t], timestamps[:t], window_days=4, min_samples=5000)
    perturbed = returns.copy()
    perturbed[t:] = rng.normal(0.0, 0.05, returns.size - t)
    changed = calibrate_target_vol(perturbed[:t], timestamps[:t], window_days=4, min_samples=5000)
    assert changed["calibrated_target_vol"] == base["calibrated_target_vol"]
    assert changed["validation_metrics"] == base["validation_metrics"]


def test_load_target_vol_artifact_safe_failures(tmp_path):
    assert load_target_vol_artifact(str(tmp_path / "missing.json")) is None
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{bad", encoding="utf-8")
    assert load_target_vol_artifact(str(malformed)) is None

    valid = {
        "calibrated_target_vol": 5e-4,
        "calibration_window": {"start_ts": "0", "end_ts": "1", "window_days": 30},
        "sample_size": 5000,
        "percentile_used": 75.0,
        "validation_metrics": {},
        "timestamp": "2026-06-18T00:00:00Z",
    }
    for name, payload in {
        "missing_keys": {"calibrated_target_vol": 5e-4},
        "nonfinite": valid | {"calibrated_target_vol": float("inf")},
        "too_few": valid | {"sample_size": 4999},
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_target_vol_artifact(str(path)) is None


def test_engine_uses_valid_artifact_and_explicit_override(tmp_path):
    path = tmp_path / "target_vol.json"
    payload = {
        "calibrated_target_vol": 5e-4,
        "calibration_window": {"start_ts": "0", "end_ts": "1", "window_days": 30},
        "sample_size": 5000,
        "percentile_used": 75.0,
        "validation_metrics": {"p75": 5e-4},
        "timestamp": "2026-06-18T00:00:00Z",
    }
    write_target_vol_artifact(payload, str(path))
    eng = AdvancedRegimeEngine(target_vol_artifact_path=str(path), load_model_weights_on_init=False)
    try:
        assert np.isclose(eng.garch.target_vol, 5e-4)
        health = eng.get_health()
        assert health["target_vol_calibrated"] is True
        assert health["engine_status"] != "DEGRADED"
    finally:
        eng._shutdown_warning_worker()

    override = AdvancedRegimeEngine(target_vol=0.02, target_vol_artifact_path=str(path), load_model_weights_on_init=False)
    try:
        assert override.garch.target_vol == 0.02
        assert override.get_health()["target_vol_provenance"]["source"] == "explicit_override"
    finally:
        override._shutdown_warning_worker()


def test_recalibrate_target_vol_threaded_with_updates(tmp_path):
    eng = AdvancedRegimeEngine(target_vol=0.02, load_model_weights_on_init=False)
    outputs = []
    returns = np.random.default_rng(11).normal(0.0, 5e-4, 9000)
    timestamps = np.arange(returns.size) * 60.0

    def updater():
        for i in range(75):
            out = eng.update({
                "timestamp": float(i + 1),
                "return": float(returns[i]),
                "features": np.array([0.1, 0.2, 0.3], dtype=float),
                "price": 100.0 + i,
            })
            outputs.append(out)

    thread = threading.Thread(target=updater)
    thread.start()
    provenance = eng.recalibrate_target_vol(
        returns, timestamps, path=str(tmp_path / "target_vol.json"), window_days=4, min_samples=5000
    )
    thread.join()
    try:
        assert provenance["calibrated_target_vol"] > 0
        assert np.isclose(eng.garch.target_vol, provenance["calibrated_target_vol"])
        assert outputs
        assert all(_validate_output_schema(out) for out in outputs)
    finally:
        eng._shutdown_warning_worker()
