# AdvancedRegimeEngine Weight Governance Audit

## Scope
This audit traces `calibrate_regime.py`, `ModelWeightManager`, `AdvancedRegimeEngine._load_model_weights()`, `_build_output()`, `signal_valid`, `calibration_status`, and downstream consumers of `signal_valid`.

## What currently defined calibrated before the fix
- `calibrate_regime.py` wrote arrays to `weights/advanced_regime_weights.npz` and wrote provenance separately to `weights/calibration_provenance.json`.
- For synthetic data, provenance explicitly used `data_source="synthetic"` and `production_valid=false`.
- For real aligned data, provenance used `data_source="real"` and `production_valid=true`.
- `ModelWeightManager.load_weights()` loaded only `.npz` arrays plus optional scalar JSON; it did not load or validate calibration provenance.
- `_load_model_weights()` treated successfully loaded arrays and shape checks as sufficient to set `_weights_loaded=True` and `_calibration_status="calibrated"`.

## What currently defined signal_valid before the fix
- Normal `_build_output()` accepts a `signal_valid` boolean and emits it into the top-level schema.
- The main normal update path passed `signal_valid=bool(self._weights_loaded)`.
- Therefore loaded arrays alone could make `signal_valid=True`, even if provenance said the artifact was synthetic and not production-valid.

## Where provenance was ignored
- `calibrate_regime.py` created provenance, but `ModelWeightManager` did not consume it.
- `_load_model_weights()` did not read `REGIME_PROVENANCE_PATH` or the default `calibration_provenance.json` next to the weights.
- `_build_output()` did not expose `weights_loaded`, `calibration_valid`, or `production_valid`, so downstream systems could not distinguish arrays from production-valid calibration.

## Downstream systems consuming signal_valid
- `backtest_engine.py` gates ARE output and blocks entries when `are_out.get("signal_valid") is False`.
- `main.py` live orchestration checks `reg_out.get("signal_valid", True)` and halts/degrades invalid regime context.
- `stop_hunt_engine.integrations.regime_adapter` maps regime payloads into stop-hunt features and defaults `signal_valid` from the payload.
- `stop_hunt_engine.features.regime_context`, training dataset builders, and SHPE model feature encoding consume `regime_signal_valid` as a model/context feature.

## Execution consequences
If synthetic arrays were accepted as calibrated, the engine could emit executable-looking regime payloads with `signal_valid=True`. Live orchestration, backtests, and downstream feature pipelines could then treat research artifacts as production-valid signal context.

## Implemented governance
- `weights_loaded`, `calibration_valid`, and `production_valid` are now separate state and output fields.
- `_load_model_weights()` validates arrays separately from provenance.
- Missing or corrupted provenance leaves arrays loaded but marks calibration invalid and production invalid.
- `data_source="synthetic"` always forces `production_valid=False`.
- `signal_valid` is permitted only when weights are loaded, calibration provenance is valid, and either `production_valid=True` or explicit `REGIME_RESEARCH_MODE` is enabled.
- Research mode is exposed as `research_mode=True` and `calibration_status="research"`.
- Fail-closed behavior zeroes position and emits a halt when calibration governance does not permit a production signal.
