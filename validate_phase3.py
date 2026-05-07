"""
Phase 3 validation suite.
ALL 7 tests must pass before proceeding to Phase 4.
Do NOT modify this file.
"""
import os
import json
import numpy as np
from advanced_regime_engine import AdvancedRegimeEngine

WEIGHTS_PATH = "weights/advanced_regime_weights.npz"
N_STATES     = 3
N_FEATURES   = 6   # must match calibrate_regime.py N_FEATURES

EXPECTED_SHAPES = {
    "nhhmm_beta":          (N_STATES, N_STATES, N_FEATURES),
    "nhhmm_mu":            (N_STATES,),
    "nhhmm_sigma":         (N_STATES,),
    "sjm_centroids":       (N_STATES, N_FEATURES),
    "sjm_feature_weights": (N_FEATURES,),
    "feature_mean":        (N_FEATURES,),
    "feature_std":         (N_FEATURES,),
}

print("=" * 60)
print("PHASE 3 VALIDATION SUITE")
print("=" * 60)

# ─── TEST 1: Weight file complete and finite ──────────────────

assert os.path.exists(WEIGHTS_PATH), \
    f"FAIL TEST 1: Weight file missing at {WEIGHTS_PATH}"

saved = np.load(WEIGHTS_PATH)
for key in EXPECTED_SHAPES:
    assert key in saved, f"FAIL TEST 1: Missing key '{key}' in weight file"
    assert np.isfinite(saved[key]).all(), \
        f"FAIL TEST 1: Non-finite values in key '{key}'"

print("TEST 1 PASS: Weight file complete and finite")

# ─── TEST 2: All weight shapes correct ───────────────────────

for key, expected_shape in EXPECTED_SHAPES.items():
    actual = tuple(saved[key].shape)
    assert actual == expected_shape, (
        f"FAIL TEST 2: '{key}' shape={actual}, expected={expected_shape}"
    )

print("TEST 2 PASS: All weight shapes correct")

# ─── TEST 3: Engine loaded weights successfully ───────────────

os.environ["REGIME_WEIGHT_PATH"] = WEIGHTS_PATH

engine = AdvancedRegimeEngine(n_states=N_STATES, n_features=N_FEATURES)

test_features = np.array([0.01, -0.02, 0.03, -0.01, 0.005, 0.002], dtype=float)
test_input = {
    "return":    0.001,
    "features":  test_features,
    "price":     50000.0,
}
out = engine.update(test_input)

assert out["engine_status"] != "UNCALIBRATED", \
    f"FAIL TEST 3: Engine still UNCALIBRATED after weight load (status={out['engine_status']})"
assert "regime_label" in out, "FAIL TEST 3: 'regime_label' missing from output"
assert out["regime_label"] != "UNCALIBRATED", \
    f"FAIL TEST 3: regime_label still UNCALIBRATED after weight load"

print("TEST 3 PASS: Engine loaded weights successfully")

# ─── TEST 4: signal_valid=True with loaded weights ────────────

# Warm up engine past circuit-breaker / confidence-collapse guards
rng_warm = np.random.default_rng(0)
for _ in range(30):
    engine.update({
        "return":   float(rng_warm.standard_normal()) * 0.001,
        "features": test_features,
        "price":    50000.0,
    })

out4 = engine.update(test_input)
assert out4["signal_valid"] is True, (
    f"FAIL TEST 4: signal_valid={out4['signal_valid']} "
    f"(engine_status={out4['engine_status']}, regime={out4['regime_label']})"
)

print("TEST 4 PASS: signal_valid=True with loaded weights")

# ─── TEST 5: Output schema regression check ───────────────────

baseline_path = "audit_engine_output/baseline_before_phase2.json"
assert os.path.exists(baseline_path), \
    "FAIL TEST 5: Baseline file audit_engine_output/baseline_before_phase2.json missing"

with open(baseline_path) as f:
    baseline = json.load(f)

assert out4["schema_version"] == baseline["schema_version"], \
    f"FAIL TEST 5: REGRESSION — schema_version changed: {out4['schema_version']} != {baseline['schema_version']}"
assert sorted(out4["risk_metrics"].keys()) == baseline["risk_metrics_keys"], \
    f"FAIL TEST 5: REGRESSION — risk_metrics keys changed"
assert sorted(out4["alpha"].keys()) == baseline["alpha_keys"], \
    f"FAIL TEST 5: REGRESSION — alpha keys changed"
assert abs(sum(out4["probabilities"].values()) - 1.0) < 1e-3, \
    "FAIL TEST 5: REGRESSION — probabilities no longer sum to 1"

print("TEST 5 PASS: Output schema regression check — intact")

# ─── TEST 6: Regime classification output ────────────────────

regime = out4["regime_label"]
side   = out4["execution_side"]
pos    = round(float(out4["position_size"]), 6)

assert isinstance(regime, str) and len(regime) > 0, \
    "FAIL TEST 6: regime_label empty or not a string"
assert side in ("long", "short", "flat", "range_mean_revert"), \
    f"FAIL TEST 6: execution_side='{side}' not in valid set"
assert 0.0 <= pos <= 0.35, \
    f"FAIL TEST 6: position_size={pos} out of [0, 0.35]"

print(f"TEST 6 PASS: regime={regime}  side={side}  pos={pos}")

# ─── TEST 7: End-to-end determinism ──────────────────────────

engine_a = AdvancedRegimeEngine(n_states=N_STATES, n_features=N_FEATURES)
engine_b = AdvancedRegimeEngine(n_states=N_STATES, n_features=N_FEATURES)

rng = np.random.default_rng(77)
for _ in range(40):
    inp = {
        "return":   float(rng.standard_normal() * 0.001),
        "features": rng.standard_normal(N_FEATURES).astype(float),
        "price":    50000.0 + float(rng.standard_normal() * 100),
    }
    out_a = engine_a.update(inp)
    out_b = engine_b.update(inp)

    for field in ("regime_label", "signal_valid", "engine_status",
                  "execution_side", "schema_version"):
        assert out_a[field] == out_b[field], (
            f"FAIL TEST 7: Non-determinism in '{field}': {out_a[field]} != {out_b[field]}"
        )
    for field in ("confidence", "conviction", "position_size",
                  "signed_position_size", "trend_strength", "risk_level"):
        assert abs(float(out_a[field]) - float(out_b[field])) < 1e-9, (
            f"FAIL TEST 7: Non-determinism in '{field}': "
            f"{out_a[field]} vs {out_b[field]}"
        )

print("TEST 7 PASS: End-to-end determinism")

# ─── FINAL VERDICT ───────────────────────────────────────────

print()
print("=" * 60)
print("  ALL 7 TESTS PASSED")
print("  PHASE 3: PRODUCTION VALIDATED")
print("  CLEARED TO PROCEED TO PHASE 4")
print("=" * 60)
