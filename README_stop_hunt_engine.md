# Stop Hunt Engine (SHPE)

## What is production-safe now
- Separation of concerns is explicit:
  - `data/` ingestion schemas only,
  - `features/` feature engineering only,
  - `model/` inference + calibration + routing only,
  - `integrations/` adapters only (no order placement).
- Inference safety controls:
  - stale-dimension degraded fallback (`>2` stale => `p_sweep=0.5`),
  - regime fallback to global model when regime model is missing/undertrained,
  - probability clipping in `[0,1]`.
- Calibration hygiene:
  - `StopHuntProbabilityEngine.train()` uses held-out calibration split (no in-sample calibration leakage).

## Current modules
- `data/`: candle/L2/derivatives/trade dataclasses.
- `features/`: funding, OI, LOB, liquidation, volume, pool, regime context, and composite feature vector.
- `model/`: sweep classifier, regime-conditional dispatcher, calibrator, engine.
- `integrations/`: feature pipeline, regime adapter, probability adapter.
- `validation/`: walk-forward split utility and audit placeholders.

## Future work / still limited
- `validation/permutation_audit.py` orchestration is still lightweight.
- Feature formulas are baseline-safe and deterministic but still require market-specific validation/tuning.
- Live monitoring hooks for SHPE diagnostics can be expanded in observability layer.

## What still needs market validation
- Paper-trading replay under realistic spread/slippage/latency regimes.
- Regime drift behavior and calibration stability across volatility regimes.
- Robustness under partial feed outages and stale L2/OI conditions.
