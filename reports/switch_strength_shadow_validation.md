# Switch Strength Shadow Validation

## Final Verdict

A. Removing conviction breaks directional recall and persistence-bypass agreement; it improves false-switch suppression slightly by being more conservative.

B. Removing edge breaks the primary directional discriminator, producing the largest recall loss and missed-switch count; it improves churn only because it refuses most directional transitions.

C. Removing volatility has the smallest impact; it slightly lowers stress responsiveness and TOXIC exits but leaves most directional behavior intact.

D. Actual contribution ranking:
1. edge
2. conviction
3. volatility

E. Conviction is genuinely useful inside switch_strength as a secondary stabilizer, but it is not the dominant driver.

F. Production readiness score for removing conviction: 42/100.

G. Recommended next action: keep production unchanged and run the same shadow telemetry against real replay logs before considering a staged conviction-weight reduction.
