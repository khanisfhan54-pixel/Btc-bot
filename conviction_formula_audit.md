# Conviction Formula Audit

## Scope and source

The active conviction assembly path is `compute_hmm_regime()` in `advanced_regime_engine.py`. Repository-wide searches for the requested terms located the production conviction expression only in that function for the AdvancedRegimeEngine runtime path; adjacent hits in execution/backtest/stop-hunt modules consume conviction or confidence but do not assemble the HMM regime conviction.

Command used:

```bash
rg -n "compute_hmm_regime|conviction|certainty|certainty_base|uncertainty|directional_bonus|directional_strength|directional_margin|edge_score|trend_score|range_score|confidence" .
```

## Formula inputs and normalization order

1. `alpha` is coerced to a finite 3-state vector and normalized by its sum.
2. `bull`, `bear`, and `crisis` are assigned from normalized `alpha`.
3. `directional_strength = clip(abs(bull - bear), 0, 1)`.
4. `directional_confidence = clip(max(bull, bear), 0, 1)`.
5. `trend_score`, `range_score`, `toxic_score`, `edge_score`, and `directional_margin` are computed from current-tick probabilities only.
6. Entropy is computed from normalized `alpha_safe`; `uncertainty = clip(entropy / log(3), 0, 1)`.
7. Conviction is assembled and clamped to `[0, 1]`.

## Pre-fix exact conviction expression

```python
certainty_base = float(np.clip(1.0 - uncertainty, 0.0, 1.0))
directional_bonus = 0.5 * float(directional_strength) * certainty_base
conviction = float(np.clip(certainty_base + directional_bonus, 0.0, 1.0))
```

Equivalent expression:

```text
conviction = clip((1 - uncertainty) * (1 + 0.5 * directional_strength), 0, 1)
```

## Clamp/clip bounds

- `directional_strength`: `[0, 1]`
- `directional_confidence`: `[0, 1]`
- `trend_score`: `[0, 1]`
- `toxic_score`: `[0, 1]`
- `range_score`: `[0, 1]`
- `edge_score`: `[0, 1]`
- `certainty_base`: `[0, 1]`
- `conviction`: `[0, 1]`

## Smoothing / EMA

No smoothing or EMA is performed inside the conviction assembly. Smoothing happens later on `regime_edge` and switch-stability state, outside the permitted formula path.
