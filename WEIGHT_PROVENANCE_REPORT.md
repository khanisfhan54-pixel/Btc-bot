# Weight Provenance Report

## Artifact audited

- Artifact: `weights/advanced_regime_weights.npz`
- Companion provenance: `weights/calibration_provenance.json`
- Arrays present: `nhhmm_beta`, `nhhmm_mu`, `nhhmm_sigma`, `sjm_centroids`, `sjm_feature_weights`, `feature_mean`, `feature_std`.

## A. Where generated

The artifact path is configured in `calibrate_regime.py` as `OUTPUT_PATH`, defaulting to `weights/advanced_regime_weights.npz`. The current engine loads from `REGIME_WEIGHT_PATH` or the same default path.

## B. Which script generated it

Primary generator: `calibrate_regime.py`.

Evidence:

- Header says it produces `weights/advanced_regime_weights.npz`.
- The pipeline saves exactly the observed array names via `np.savez(...)`.
- The standalone `calibrate(ohlcv_csv_path, output_path)` helper also saves the same key family for OHLCV-only real-data calibration.

## C. Whether provenance exists

Yes. `weights/calibration_provenance.json` exists and contains:

```json
{
  "data_source": "synthetic",
  "production_valid": false,
  "reason": "trained_on_synthetic_data"
}
```

## D. Whether calibration used real BTC or synthetic data

Current checked-in artifact provenance says synthetic data, not production-valid real BTC.

`calibrate_regime.py` defaults `DATA_SOURCE` to `synthetic`. A real path exists (`REGIME_DATA_SOURCE=real`) and loads aggTrades/bookDepth, but the persisted provenance explicitly marks the current artifact as synthetic.

## E. Whether artifact is currently loaded

Yes, by default. `AdvancedRegimeEngine.__init__` calls `_load_model_weights()` when `load_model_weights_on_init=True` (the default), and `_load_model_weights()` calls `ModelWeightManager.load_weights("advanced_regime", self._weight_path)`.

The loader validates shapes, injects arrays into `NHHMM_Engine` and `SparseJumpModel`, consumes normalization moments, then sets `_weights_loaded=True` and `_calibration_status="calibrated"`.

## F. Whether artifact affects output

Yes. It affects output through:

1. NHHMM transition/emission probabilities (`nhhmm_beta`, `nhhmm_mu`, `nhhmm_sigma`).
2. SparseJumpModel centroid distances and weighted features (`sjm_centroids`, `sjm_feature_weights`).
3. Feature normalization (`feature_mean`, `feature_std`).
4. The normal output contract, where `signal_valid=bool(self._weights_loaded)`.
5. Require-calibration and microstructure gates, which halt when weights are missing, but do not inspect provenance validity.

## Provenance verdict

| Question | Finding |
|---|---|
| Is provenance present? | Yes. |
| Does provenance prove real BTC calibration? | No. It proves synthetic calibration. |
| Is current artifact production-valid? | No, by its own provenance. |
| Does engine block synthetic provenance? | No. It treats successfully loaded arrays as `_calibration_status="calibrated"`. |
| Does artifact materially affect predictions? | Yes. |

## Risk

High. The code conflates “arrays loaded and shape-valid” with “calibrated enough for production.” Confidence, `signal_valid`, and regime labels can be emitted from a synthetic artifact while appearing operationally valid.
