# Production Risk Review

## Calibration recommendation

Use the fixed conviction threshold represented by the new constants:

- `_CONV_THRESHOLD_FLOOR = 0.182039`
- `_CONV_THRESHOLD_BASE = 0.182039`
- `_CONV_THRESHOLD_UNCERTAINTY_WEIGHT = 0.0`

This preserves the existing adaptive-threshold code shape while implementing the methodology-selected fixed threshold.

## Lookahead bias

Risk: **medium**. The calibration uses the existing synthetic `run_all()` replay harness and fields emitted at update time. No future labels are injected into the engine decision path, but the threshold was selected after reviewing full-run audit distributions, so prospective/live validation is still required.

## Data/regime leakage

Risk: **medium**. The threshold is derived from engine-produced audit records on synthetic TREND/BEAR/RANGE/TOXIC scenarios. There is no direct production-market leakage, but scenario labels are known in the harness and are used in report metrics.

## Threshold overfitting and sensitivity

Sensitivity was evaluated by re-running the audit harness in memory around the selected threshold:

| threshold | TREND accepted | BEAR accepted | RANGE precision | total acceptances | warmup acceptances | false trend activations (`conviction < 0.10`) | avg run length |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.132039 (-0.05) | 551 | 511 | 0.106162 | 1062 | 163 | 0 | 3.876593 |
| 0.162039 (-0.02) | 484 | 456 | 0.104394 | 940 | 143 | 0 | 3.923710 |
| 0.182039 (chosen) | 384 | 375 | 0.101878 | 759 | 128 | 0 | 4.007639 |
| 0.202039 (+0.02) | 261 | 189 | 0.097851 | 450 | 83 | 0 | 3.976502 |
| 0.232039 (+0.05) | 118 | 36 | 0.094281 | 154 | 38 | 0 | 4.049402 |

Risk: **medium-high**. The selected value is sensitive: tightening by +0.02 materially reduces directional acceptances, while loosening by -0.02 increases acceptances and slightly improves the measured RANGE precision in this post-change replay. This indicates the threshold should be monitored and re-fit on real validation data.

## Synthetic-vs-real calibration caveat

Risk: **high**. No `calibration_provenance.json` file with a `production_valid` flag was found in the repository during this review. Treat the calibration as **not production-valid** until a provenance artifact explicitly marks the calibration as production validated.

## Warmup dependence

At the chosen threshold, 128 of 759 accepted candidates occurred during `_SHOCK_WARMUP_TICKS`, or approximately **16.86%**. Risk: **medium**, because a non-trivial share of acceptances occur during warmup.

## Regime oscillation / switch churn

Average run length using `confirmed_after_smoother`-style simulated labels was **4.007639** at the chosen threshold. Sensitivity ranged from **3.876593** to **4.049402** across ±0.05. Risk: **medium**, because average runs are short and should be monitored for churn in live replay.

## False trend activation

Accepted candidates with `conviction < 0.10`: **0** at the chosen threshold and throughout the tested ±0.02/±0.05 sensitivity band. Risk: **low** for ultra-low-conviction activations in this harness.

## Readiness assessment

Overall readiness score: **62 / 100**.

- Lookahead bias: 70/100.
- Data/regime leakage: 65/100.
- Threshold overfitting: 45/100.
- Synthetic-vs-real provenance: 30/100.
- Warmup dependence: 60/100.
- Switch churn: 55/100.
- False trend activation: 90/100.
