# Recommendation

Dominant blocker: **adaptive conviction gate inside the switch filter**.

Evidence: before the switch filter, 3051 TREND and 2547 BEAR candidates were confirmed directionally, but after the switch filter all were reverted to RANGE. Among switch-evaluated directional suppressions, conviction_ok was false for 5598 of 5598 events while persistence_ok was never false (0 blocks). Switch strength was below gate but only moderately: median margin_to_gate was -0.037228 for TREND and -0.047971 for BEAR. Therefore the switch filter is the destructive stage, and its adaptive conviction leg is the dominant blocker preventing the persistence bypass from overriding small-to-moderate strength shortfalls.
