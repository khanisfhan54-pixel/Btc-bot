# Remaining Issues Verification (Phases 8-14)

| Issue | Root Cause | Fix Implemented | Evidence | Tests | Remaining Risks |
| --- | --- | --- | --- | --- | --- |
| SHPE degraded fallback when model missing/corrupt | Loader and main bootstrap allowed non-fatal `None` engine. | Artifact validation and fail-closed required startup mode. | `stop_hunt_engine/integrations/signal_adapter.py`, `main.py` | `pytest tests/test_shpe_model_load.py tests/test_shpe_probability_distribution.py tests/test_shpe_fail_closed.py` | Development mode can still explicitly run degraded. |
| Execution realism assumptions | Queue features were not consumed by backtest fill path. | Queue-aware fill helper, partial fills, remaining qty, timeout. | `backtest_engine.py`, `QUEUE_MODEL_AUDIT.md` | `pytest tests/test_backtest_accounting_phase8_14.py` | Exit queue and nonlinear market impact remain simplified. |
| Funding accounting missing | Settlement only included price move, fees, slippage. | Isolated funding cashflow layer updates equity while position is open. | `backtest_engine.py` | Positive/negative/flat funding tests | Funding schedule uses configured bar/interval fraction. |
| Magnet volatility hardcoded | Backtest used `volatility: 1.0`. | Backtest consumes `volatility`/`expected_volatility`/`atr_pct` contract. | `backtest_engine.py`, `MAGNET_PARITY_AUDIT.md` | Compile/full backtest validation | Candidate fallback still marks non-production parity. |
| PnL `0.25` scaling factor | Legacy undocumented balance multiplier ignored actual quantity. | Quantity-based PnL helper; factor removed. | `backtest_engine.py`, `PNL_ACCOUNTING_AUDIT.md` | PnL helper test | Requires downstream users to interpret returns as notional returns. |
| Production health ambiguity | Critical artifact failure lacked hard startup gate. | SHPE required mode logs critical, alerts Telegram, raises. | `main.py`, `SYSTEM_HEALTH_AUDIT.md` | Compile/startup import tests | Telegram network delivery external. |

## Commands/Evidence
- Baseline before edits: `pytest` failed during collection because `tests.action_expectations` could not be imported without `tests/__init__.py`.
- Focused phase tests: `pytest tests/test_shpe_model_load.py tests/test_shpe_probability_distribution.py tests/test_shpe_fail_closed.py tests/test_backtest_accounting_phase8_14.py stop_hunt_engine/tests/test_shpe_training_workflow.py`.
- Required full validation commands are recorded in the final response.

## Production Readiness Score
**86/100.** Critical artifact validation, accounting integrity, funding, and entry fill realism are materially improved without changing signal logic or thresholds. Remaining deductions are for simplified exit queue/market impact modeling and external alert delivery dependency.

## Profitability Confidence Score
**63/100.** Confidence improves because backtest fills, funding, and PnL are less optimistic and more mathematically grounded. Score remains moderate because profitability still depends on live liquidity, latency, exit queue behavior, and market-impact calibration not fully modeled here.
