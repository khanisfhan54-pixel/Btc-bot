# Weight Governance Forensics

Generated: 2026-06-12 (UTC)

## Commands run

```bash
pytest -vv tests/test_weight_governance.py tests/test_calibration_provenance.py tests/test_are_gating_parity.py
python - <<'PY'
from advanced_regime_engine import AdvancedRegimeEngine
p={'return':0.0001,'features':[0.1,0.2,0.3],'price':100.01,'timestamp':1700000000.0}
e=AdvancedRegimeEngine(enable_background_workers=False)
out=e.update(p)
print(e._weight_path)
for k in ['weights_loaded','calibration_valid','production_valid','research_mode','calibration_status','signal_valid','regime_label','execution_mode','feed_status','engine_status']:
    print(k, out.get(k, getattr(e, '_'+k, None)))
e._shutdown_warning_worker()
PY
REGIME_RESEARCH_MODE=1 python - <<'PY'
from advanced_regime_engine import AdvancedRegimeEngine
p={'return':0.0001,'features':[0.1,0.2,0.3],'price':100.01,'timestamp':1700000000.0}
e=AdvancedRegimeEngine(enable_background_workers=False)
out=e.update(p)
for k in ['weights_loaded','calibration_valid','production_valid','research_mode','calibration_status','signal_valid','regime_label','execution_mode','feed_status']:
    print(k, out.get(k, getattr(e, '_'+k, None)))
e._shutdown_warning_worker()
PY
```

## Test result

`tests/test_weight_governance.py`, `tests/test_calibration_provenance.py`, and `tests/test_are_gating_parity.py` all passed: `8 passed in 3.93s`.

## Governance trace

1. `calibrate_regime.py` defaults `REGIME_DATA_SOURCE` to `synthetic`; any value outside `synthetic`/`real` is rejected.
2. Synthetic calibration writes `production_valid: false` with reason `trained_on_synthetic_data`; real calibration writes `production_valid: true` and records `aggTrades`, `bookDepth`, and bar count.
3. `ModelWeightManager.load_weights()` only loads the `.npz` arrays plus optional scalar JSON; it does not validate provenance itself.
4. `AdvancedRegimeEngine._load_model_weights()` validates required weight keys and shapes, loads NHHMM/SJM weights, then loads `calibration_provenance.json` from `REGIME_PROVENANCE_PATH` or the weight directory.
5. `_load_model_weights()` forcibly sets `production_valid = False` when provenance `data_source == "synthetic"`, even if a provenance file lies and says production-valid.
6. `_is_signal_permitted()` requires: `weights_loaded`, `calibration_valid`, and either `production_valid` or `research_mode`.
7. `update()` initially emits `signal_valid = _is_signal_permitted()`, then force-fails to `signal_valid=False`, `regime_label=UNCALIBRATED`, `execution_mode=halt`, zero size when `_is_signal_permitted()` is false.
8. If engine status is `DEGRADED`, `update()` also forces `signal_valid=False`.

## Direct answers

### 1. Can synthetic weights still produce `signal_valid=True`?

**Yes, but only when `REGIME_RESEARCH_MODE=1` is set.**

- Default active artifact run (no research mode): `production_valid=False`, `calibration_status=not_production_valid`, `signal_valid=False`, `regime_label=UNCALIBRATED`, `execution_mode=halt`.
- Research-mode run: `production_valid=False`, `research_mode=True`, `calibration_status=research`, `signal_valid=True`, `regime_label=RANGE`, `execution_mode=range_mean_revert`.

This is an explicit architecture decision in the current code/tests, not an accidental bypass: `test_research_mode_override_is_explicitly_labeled` asserts the same behavior.

### 2. Can synthetic weights still produce `calibration_status="calibrated"`?

**No under the current loader path.**

Synthetic provenance is forced to `production_valid=False`. Without research mode the status becomes `not_production_valid`; with research mode it becomes `research`. The passed governance tests assert both paths. A real/prod-valid provenance is the path that yields `calibration_status="calibrated"`.

### 3. Can `production_valid=false` still reach execution?

**Default production path: no, because `signal_valid=False` fails closed before entries. Research mode: yes, by explicit override.**

Evidence:

- ARE default active artifact emits `signal_valid=False` and `execution_mode=halt`.
- `main.py` sets `regime_fail_closed=True` when `reg_out.signal_valid` is false, and later blocks new entries / disables `allow_trade` for fail-closed, `HALTED`, `STALE_FALLBACK`, or `UNCALIBRATED` regimes.
- `backtest_engine.py` skips bars when `are_out.signal_valid is False`, and an explicit parity test confirms invalid ARE signals produce zero trades.
- However, research mode sets `signal_valid=True` despite `production_valid=False`, so any process launched with `REGIME_RESEARCH_MODE=1` can forward synthetic/non-production regime output into downstream execution unless a separate deployment guard forbids research mode in live runtime.

### 4. Which downstream modules consume `signal_valid`?

Direct consumers found by repository search:

| Module/test | Consumption |
|---|---|
| `main.py` | Reads `reg_out.get("signal_valid", True)`; false triggers fail-closed and blocks new entries. Also stores fallback context with `signal_valid=False`. |
| `backtest_engine.py` | Reads `are_out.get("signal_valid") is False`; skips the bar and increments `bars_skipped_signal_invalid`. |
| `stop_hunt_engine/integrations/regime_adapter.py` | Maps external regime payload to SHPE feature payload and preserves `signal_valid`, defaulting to true if missing. |
| `stop_hunt_engine/model/engine.py` | Includes `regime_signal_valid` as an SHPE model feature. |
| `stop_hunt_engine/validation/timestamp_alignment_audit.py` | Treats `regime_signal_valid` as an audited regime feature field. |
| `tests/test_are_gating_parity.py` | Verifies false `signal_valid` blocks backtest entries. |
| `tests/test_weight_governance.py` | Verifies synthetic/non-prod/missing/corrupt provenance fail closed and research mode explicitly allows signal. |
| `tests/test_calibration_pipeline_e2e.py` | Verifies real BTC calibration path loads as calibrated and signal-valid. |
| `validate_phase3.py` | Historical validation script expects `signal_valid=True` with loaded weights; likely stale relative to current provenance gate unless run with production-valid provenance/research mode. |

## Recommendation

- Treat the default governance path as **mostly effective**: synthetic weights do not produce production-calibrated status and do not emit signal-valid output without research override.
- Treat `REGIME_RESEARCH_MODE=1` as the remaining production risk. If live deployment can inherit this environment variable, synthetic `production_valid=false` artifacts can reach downstream execution by design.
- Add a deployment-level check later (not in this task) that rejects `REGIME_RESEARCH_MODE=1` when `LIVE_TRADING`/production execution is enabled.
