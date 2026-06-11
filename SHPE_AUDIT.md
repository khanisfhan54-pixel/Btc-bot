# SHPE Productionization Audit (Phase 8)

## Scope
- `stop_hunt_engine/integrations/signal_adapter.py` boot loader and inference fallback path.
- `main.py` SHPE singleton bootstrap and startup observability.

## Findings
| Area | Current Logic | Risk | Production Impact | Fix | Validation |
| --- | --- | --- | --- | --- | --- |
| Missing artifact | Missing `shpe_model.pkl` returned `None` unless caller required training. | Silent p=0.5 degraded inference can look operational. | Critical model absence can reach execution path without hard startup signal. | Loader remains explicit degraded only in non-required contexts; `require_trained=True` raises. | `tests/test_shpe_fail_closed.py` |
| Corrupt artifact | Load exception returned `None` in non-required contexts. | Corruption indistinguishable from intentionally disabled model. | Production may run without trained probabilities. | Artifact failures log as validation failures; required contexts raise `RuntimeError`. | `tests/test_shpe_fail_closed.py` |
| Metadata validation | Loaded payload accepted `model_version="unknown"`. | Cannot prove artifact lineage. | Operational rollback and incident analysis are compromised. | Loader validates engine type, classifier, feature names, non-unknown model version, and calibrator when required. | `tests/test_shpe_model_load.py` |
| Calibrator attachment | Legacy standalone calibrator could be attached, but missing calibrator was not fail-closed. | Probability scale may be raw/non-calibrated while appearing production-ready. | Risk gates use incorrectly scaled SHPE probabilities. | Required contexts require calibrator unless explicitly overridden. | `tests/test_shpe_model_load.py` |
| Startup behavior | `main.py` called `require_trained=False` unconditionally and swallowed failures. | Live startup could continue in degraded ambiguity. | Model-artifact failures do not stop live execution. | Startup requires trained SHPE when `LIVE_TRADING=true` or `SHPE_REQUIRE_TRAINED=true`, emits critical log/telegram alert, then raises. | `python -m py_compile main.py stop_hunt_engine/integrations/signal_adapter.py` |

## Result
SHPE is now fail-closed for production/required startup contexts while preserving explicit p=0.5 degraded behavior for non-required development contexts.
