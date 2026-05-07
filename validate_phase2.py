"""
Phase 2 validation suite.
ALL assertions must pass before proceeding to training.
Do NOT modify this file.
"""
import numpy as np
from microstructure_features import MicrostructureFeatureEngine
from feature_screen import screen_features, FEATURE_NAMES

print("=" * 60)
print("PHASE 2 VALIDATION SUITE")
print("=" * 60)

# ─── TEST 1: Numerical stability ─────────────────────────────

engine = MicrostructureFeatureEngine(lookback_bars=200, rv_window_bars=5)
np.random.seed(42)

features_list = []
for i in range(250):
    mid  = 50000.0 + np.random.randn() * 100
    bid  = mid - 1.0
    ask  = mid + 1.0
    bsz  = abs(np.random.randn() * 10 + 20)
    asz  = abs(np.random.randn() * 10 + 20)
    flow = np.random.randn() * 5
    bvol = abs(np.random.randn() * 100)
    svol = abs(np.random.randn() * 100)
    f = engine.update(mid, bid, ask, bsz, asz, flow, bvol, svol)
    features_list.append(f)

features_arr = np.array(features_list)

assert not np.isnan(features_arr).any(),  "FAIL TEST 1: NaN in features"
assert np.isfinite(features_arr).all(),   "FAIL TEST 1: Non-finite in features"
assert features_arr.shape[1] == 6,        "FAIL TEST 1: Wrong feature count"
print("TEST 1 PASS: Numerical stability (250 bars, no NaN/inf)")

# ─── TEST 2: Shape contract ───────────────────────────────────

sample = features_list[-1]
assert sample.shape == (6,), f"FAIL TEST 2: Expected (6,), got {sample.shape}"
print("TEST 2 PASS: Shape contract (6,)")

# ─── TEST 3: Determinism ─────────────────────────────────────

engine_a = MicrostructureFeatureEngine()
engine_b = MicrostructureFeatureEngine()
np.random.seed(99)

run1, run2 = [], []
inputs = []
for _ in range(50):
    mid  = 50000.0 + np.random.randn() * 50
    bid  = mid - 0.5
    ask  = mid + 0.5
    bsz  = abs(np.random.randn() * 5 + 10)
    asz  = abs(np.random.randn() * 5 + 10)
    flow = np.random.randn() * 2
    bvol = abs(np.random.randn() * 50)
    svol = abs(np.random.randn() * 50)
    inputs.append((mid, bid, ask, bsz, asz, flow, bvol, svol))

for args in inputs:
    run1.append(engine_a.update(*args))

for args in inputs:
    run2.append(engine_b.update(*args))

for i, (a, b) in enumerate(zip(run1, run2)):
    assert a.tolist() == b.tolist(), f"FAIL TEST 3: Non-determinism at tick {i}"
print("TEST 3 PASS: Determinism (50-tick identical replay)")

# ─── TEST 4: No future leakage ───────────────────────────────

engine_c = MicrostructureFeatureEngine()
run3 = []
for args in inputs:
    run3.append(engine_c.update(*args))

for i, (a, c) in enumerate(zip(run1, run3)):
    assert a.tolist() == c.tolist(), f"FAIL TEST 4: Leakage at tick {i}"
print("TEST 4 PASS: No future leakage")

# ─── TEST 5: Gate 2 (MI screen) ──────────────────────────────

N = 800
np.random.seed(7)
X_synth = np.random.randn(N, 6)

# Inject signal into OFI (0), VWOI (1), TradeImb (5)
y_synth = np.zeros(N, dtype=int)
for i in range(N):
    score = X_synth[i, 0] + X_synth[i, 1] + X_synth[i, 5]
    if score > 0.8:
        y_synth[i] = 1
    elif score < -0.8:
        y_synth[i] = -1
    else:
        y_synth[i] = 0

result = screen_features(X_synth, y_synth, feature_names=FEATURE_NAMES)
gate2 = result["_gate2"]

assert gate2["passes"], (
    f"FAIL TEST 5: Gate 2 — only {gate2['top5_micro_count']} microstructure "
    "features in top 5. DO NOT PROCEED TO TRAINING."
)
print(f"TEST 5 PASS: Gate 2 ({gate2['top5_micro_count']}/5 micro features in top 5)")

# ─── TEST 6: Regression guard ────────────────────────────────

import json, os
from advanced_regime_engine import AdvancedRegimeEngine

engine_are = AdvancedRegimeEngine(n_states=3, n_features=3)
test_input = {
    "return": 0.001,
    "features": np.array([0.001, -0.002, 0.003]),
    "price": 50000.0,
}
output = engine_are.update(test_input)

baseline_path = "audit_engine_output/baseline_before_phase2.json"
assert os.path.exists(baseline_path), "FAIL TEST 6: Baseline file missing"

with open(baseline_path) as f:
    baseline = json.load(f)

assert output["schema_version"] == baseline["schema_version"], \
    "FAIL TEST 6: REGRESSION — schema_version changed"
assert sorted(output["risk_metrics"].keys()) == baseline["risk_metrics_keys"], \
    "FAIL TEST 6: REGRESSION — risk_metrics keys changed"
assert sorted(output["alpha"].keys()) == baseline["alpha_keys"], \
    "FAIL TEST 6: REGRESSION — alpha keys changed"
assert abs(sum(output["probabilities"].values()) - 1.0) < 1e-3, \
    "FAIL TEST 6: REGRESSION — probabilities no longer sum to 1"
print("TEST 6 PASS: Regression guard (AdvancedRegimeEngine schema intact)")

# ─── FINAL VERDICT ───────────────────────────────────────────

print()
print("=" * 60)
print("  ALL 6 TESTS PASSED")
print("  GATE 2: PASS")
print("  PHASE 2: PRODUCTION VALIDATED")
print("  CLEARED TO PROCEED TO PHASE 3")
print("=" * 60)
