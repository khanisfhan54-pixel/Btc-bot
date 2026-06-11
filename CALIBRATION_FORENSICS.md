# Calibration Forensics

## Files audited

- `calibrate_regime.py`
- `weights/advanced_regime_weights.npz`
- `weights/calibration_provenance.json`
- `alpha_liquidity_sweep_predictor.py`
- `stop_hunt_engine/model/calibrator.py`
- `stop_hunt_engine/training/walk_forward.py`
- `stop_hunt_engine/training/research_audit.py`

## AdvancedRegimeEngine calibration pipeline

`calibrate_regime.py` is the generator for the ARE weight file. It:

1. Defaults `DATA_SOURCE` to `synthetic`.
2. Can load real aggTrades/bookDepth if `REGIME_DATA_SOURCE=real`.
3. Computes calibration-time feature normalization moments on the first 80% of bars.
4. Computes triple-barrier labels.
5. Fits K-means centroids and simple NHHMM emissions/transition parameters.
6. Saves `.npz` arrays and provenance JSON.

## Are probabilities calibrated?

For ARE: no reliable probability calibration is evident.

The SJM/NHHMM outputs are normalized probabilities/posteriors in a model-internal sense, but no isotonic/Platt calibration is applied to ARE regime probabilities. `confidence=max(bull,bear,crisis)` is therefore uncalibrated with respect to correctness.

## Is isotonic calibration actually used?

Not for ARE regime weights.

There are two other calibrator systems:

1. `alpha_liquidity_sweep_predictor.py::ProbabilityCalibrator` supports isotonic calibration for the liquidity sweep alpha predictor.
2. `stop_hunt_engine/model/calibrator.py::ProbabilityCalibrator` supports Platt or isotonic calibration for SHPE, and research diagnostics compare calibration methods.

These do not calibrate `weights/advanced_regime_weights.npz` or `compute_hmm_regime()` outputs.

## Metrics source: full sample, holdout, or out-of-fold?

For ARE weight generation:

- Feature normalization uses a calibration cutoff at 80% of bars.
- Labels are generated using forward windows.
- K-means/emissions are fit over generated features/labels in the script path.
- There is no explicit out-of-fold probability calibration for the final regime probabilities.

For SHPE:

- Training uses a calibration holdout fraction in `StopHuntProbabilityEngine.train` calls.
- Walk-forward utilities create train/test folds and compute Brier/ECE on predictions.
- Research audit can compare raw/platt/isotonic on calibration/test splits.

## Leakage assessment

### ARE pipeline

- The script claims strict causal rules, but labels are triple-barrier forward-looking labels by definition for supervised calibration. That is acceptable for offline labels if used only in training.
- Feature normalization uses the first 80% rather than all bars, which is better than full-sample normalization.
- No out-of-fold calibration is used for ARE probabilities, so no calibrated probability validity can be claimed.
- Current artifact is synthetic, so leakage is less relevant than provenance invalidity for production.

### SHPE pipeline

- The stop-hunt engine has purged walk-forward utilities and explicit leakage tests in separate directories.
- It is not the source of the ARE regime artifact.

## Key forensic conclusion

The current repository contains calibration infrastructure, but the AdvancedRegimeEngine artifact currently loaded by default is synthetic and not probability-calibrated. `_calibration_status="calibrated"` means "arrays loaded successfully," not "probabilities are calibrated on real BTC holdout/OoF data."
