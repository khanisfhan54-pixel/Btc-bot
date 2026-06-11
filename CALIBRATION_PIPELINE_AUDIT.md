# Phase 2 — Calibration Pipeline E2E Audit

## Current path
`calibrate_regime.py` produces `advanced_regime_weights.npz`; `ModelWeightManager.load_weights()` loads arrays; `AdvancedRegimeEngine._load_model_weights()` consumes NHHMM, SJM, and feature normalization arrays; `AdvancedRegimeEngine.update()` emits `signal_valid` based on loaded weights.

## Root cause of gap
The real-data branch in `calibrate_regime.py` previously refused to run because no real loader was configured. That prevented proving: real BTC data → calibration artifact → `ModelWeightManager` → ARE update.

## Implemented path
Added a strict real BTC loader using:
- `aggTrades` for real timestamps, close prices, trade counts, buy volume, sell volume, and trade-flow sign.
- `bookDepth` for real depth by timestamp and percentage bucket; negative percentages map to bid depth, positive percentages to ask depth.
- Inner join by minute timestamp; no synthetic fallback on `REGIME_DATA_SOURCE=real`.

## Required E2E assertions
Covered by `tests/test_calibration_pipeline_e2e.py`:
- `weights_loaded == True`
- `calibration_status == "calibrated"`
- `engine_status != "DEGRADED"`
- `signal_valid == True`

## Risk
The real loader fails closed when aligned real rows are insufficient. It does not fabricate missing bars.

## Expected outcome
Real BTC calibration artifacts can be generated and consumed by ARE without degraded status.

## Validation procedure
Run `pytest -q tests/test_calibration_pipeline_e2e.py` and inspect generated temporary `.npz` plus provenance sidecar.
