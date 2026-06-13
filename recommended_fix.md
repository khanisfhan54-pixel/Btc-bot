# Recommended Fixes (Do Not Implement)

| rank | fix | expected recall gain | expected precision loss | production risk | justification |
|---:|---|---|---|---|---|
| Fix #1 | Recalibrate the conviction formula so strong directional evidence can survive high entropy, for example via a bounded directional-evidence component or separate directional-confidence output. | High | Medium | Medium | Conviction is almost entirely anti-correlated with uncertainty, while thousands of high-edge/high-margin records remain below 0.25 conviction. |
| Fix #2 | Add first-class telemetry for `certainty_base`, `directional_bonus`, switch edge term, switch conviction term, switch volatility term, and shock add-on. | Medium | Low | Low | The audit had to reconstruct formula terms and infer volatility/shock as a residual; better telemetry allows safer validation before behavior changes. |
| Fix #3 | Only after telemetry validation, evaluate a limited switch fallback keyed to high `edge_score` and high `directional_margin` rather than global switch-threshold changes. | Medium | Medium | Medium-High | Switch strength itself was not conviction-dominated, but 611 high-edge switch evaluations still missed the gate, so any switch-side change should be narrowly scoped. |
