# Stop Hunt Probability Engine (SHPE)

Probability layer for the BTC trading bot that estimates the likelihood of
a stop-hunt sweep at each 5-minute bar. SHPE never places orders; it exposes
a single probability score consumed by the bot's risk gate.

## Integration

```python
from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput

output = get_shpe_probability(engine, pipeline_input, bar_index)
# output == {"probability": float, "degraded": bool, "regime_used": str}
```

## Feature dimensions

| Dimension | Module | Stale threshold |
|---|---|---|
| Pool distance | `pool_distance.py` | never (candle-derived) |
| Funding pressure | `funding_pressure.py` | 12 h |
| OI dynamics | `oi_dynamics.py` | 20 min |
| Volume trap | `volume_trap.py` | never (candle-derived) |
| LOB imbalance | `lob_imbalance.py` | 120 s |
| Liquidation proximity | `liquidation_proximity.py` | 1 h |
| Regime context | `regime_context.py` | 5 min |

## Safety rules

- If > 2 feature dimensions are stale: `degraded=True`, `p_sweep=0.5`.
- Unknown regime falls back to the global model.
- All probabilities are bounded to [0, 1].
- No crashes from missing data, NaNs, or missing snapshots.

## Validation

```bash
pytest stop_hunt_engine/tests/ -v
```

## What is production-safe (post PR #214)

- Dependency restore: all original bot deps present.
- Feature modules: real implementations, no stubs.
- Calibration: leakage-free holdout split (temporal, not random).
- Integration adapters: safe, no execution side-effects.
- Walk-forward CV: expanding-window and rolling-window variants.
- Permutation audit: implemented and tested.

## What still requires market validation

- Probability thresholds for risk gating (requires live paper-trading data).
- Per-regime sub-model sample sizes (requires labelled regime history).
- Calibration stability across exchange feed outages.
- Feature importance rankings under real market microstructure.
