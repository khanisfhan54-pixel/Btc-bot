# Regression Matrix

| Test | Introduced By Patch? | Production Risk | Repair Required? | Expected Fix |
|------|----------------------|-----------------|------------------|--------------|
| test_bull_bias | no (pre-existing recall dependence exposed by override removal) | high | yes | Step 2: restore directional conviction/current-return scoring path without RANGE→TREND/BEAR override |
| test_accuracy_trend_recall | no | high | yes | Step 2 |
| test_mc_bull_trend_recognized | no | high | yes | Step 2 |
| test_strong_bull_returns_trend | no | high | yes | Step 2 |
| test_strong_bear_returns_bear | no | high | yes | Step 2 |
| test_active_sweep_hold_in_trending_up_high_sweep | yes (behavior removal in available patch diff; tests predate patch) | medium | yes | Step 4: restore explicit trend-aligned risk gate in get_signal() |
| test_active_sweep_hold_in_trending_down_low_sweep | yes (behavior removal in available patch diff; tests predate patch) | medium | yes | Step 4: restore explicit trend-aligned risk gate in get_signal() |
