# CHANGELOG

## 2026-05-20 — SHPE hardening and compatibility pass

### What was fixed
- Restored core bot dependencies in `requirements.txt` and added only SHPE minimums (`scikit-learn`, `joblib`).
- Replaced SHPE feature stubs with bounded, timestamp-safe implementations for:
  - funding pressure,
  - OI dynamics,
  - LOB imbalance,
  - liquidation proximity,
  - volume trap,
  - pool distance.
- Added integration adapters under `stop_hunt_engine/integrations/`:
  - `regime_adapter.py` (regime payload mapping),
  - `feature_pipeline.py` (safe data orchestration + timestamp checks),
  - `signal_adapter.py` (probability API, no order placement).
- Added tests for feature bounds/stability, regime fallback behavior, monotonic calibration outputs, and adapter compatibility.

### Why
- Previous PR replaced critical dependency pins and introduced placeholder feature logic that could not produce meaningful, safe signals.
- This patch restores compatibility and provides deterministic, non-lookahead feature computations while preserving separation of concerns.

### Risks remaining
- Feature formulas are conservative baselines; predictive quality still requires out-of-sample market validation.
- Current adapters enforce strict timestamp checks and can reject mismatched feeds; upstream feed clock discipline is required.
- Classifier training/calibration quality depends on label quality and regime coverage.

### What is still stubbed
- `validation/permutation_audit.py` remains placeholder orchestration (core model-level permutation audit exists in `model/sweep_classifier.py`).

### Requires future market validation
- Full walk-forward + paper-trading validation with exchange-like latency/spread.
- Calibration drift monitoring over changing BTC volatility regimes.
- Stress tests under partial feed degradation (e.g., intermittent L2 gaps).

## Architecture diagram

```text
Data ingestion (candles/L2/funding/OI/liquidations)
        │
        ▼
integrations/feature_pipeline.build_feature_vector
        │        (timestamp alignment + regime mapping)
        ▼
features.compute_feature_vector
        │
        ▼
model.StopHuntProbabilityEngine.predict
  ├─ staleness guard (>2 stale dims => p=0.5 degraded)
  ├─ regime routing (RegimeConditionalClassifier)
  └─ optional calibration (ProbabilityCalibrator)
        │
        ▼
integrations/signal_adapter.get_shpe_probability
        │
        ▼
Caller risk/execution layers (unchanged)
```

## Data flow explanation
- SHPE reads only rows/snapshots with `timestamp <= as_of`.
- Feature engineering remains isolated in `stop_hunt_engine/features`.
- Inference remains isolated in `stop_hunt_engine/model`.
- Integration layer only maps and orchestrates inputs/outputs; it does not place trades.

## Integration map
- External regime payload -> `integrations/regime_adapter.map_regime_output`.
- Market streams -> `integrations/feature_pipeline.PipelineInput`.
- Inference call -> `integrations/signal_adapter.get_shpe_probability`.
- Execution/routing/risk modules remain untouched.
