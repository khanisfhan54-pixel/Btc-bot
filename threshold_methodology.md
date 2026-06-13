# Threshold Methodology

Source: `conviction_calibration_report.md` generated from the in-memory audit harness.

## Baseline

The current uncertainty-adjusted threshold is `max(0.52, 0.65 * (1 - 0.25 * uncertainty))`. Re-running `run_all()` in memory produced 3,051 TREND switch candidates and 2,547 BEAR switch candidates. Baseline RANGE precision was 0.111528.

## Candidate comparison

| option | threshold | TREND recall | BEAR recall | RANGE precision |
|---|---|---:|---:|---:|
| Current uncertainty-adjusted | `max(0.52, 0.65 * (1 - 0.25 * uncertainty))` | 0.000000 | 0.000000 | 0.111528 |
| Fixed P10 BEAR | `0.182039` | 0.921010 | 0.899882 | 0.204993 |
| Fixed P10 TREND | `0.188522` | 0.900033 | 0.868080 | 0.200225 |
| Fixed near P25 BEAR | `0.201525` | 0.854802 | 0.749902 | 0.187050 |
| Uncertainty-adjusted low | `max(0.18, 0.24 * (1 - 0.25 * uncertainty))` | 0.895116 | 0.861406 | 0.199202 |
| Percentile-based P25 by side | `0.211309 for TREND; 0.201525 for BEAR` | 0.749918 | 0.750294 | 0.178515 |

## Decision

Choose **A) fixed threshold** with `conviction >= 0.182039`.

This maximizes `TREND recall + BEAR recall` among the evaluated candidates while keeping RANGE precision above the measured baseline. The fixed threshold is the BEAR rejected-candidate P10 conviction from `conviction_calibration_report.md`, which is the most permissive evaluated formula that still improves RANGE precision in this synthetic audit harness.
