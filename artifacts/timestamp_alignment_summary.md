# Timestamp Alignment Audit

## A. PASS / FAIL
PASS

## B. Violating features
None

## C. Leak magnitude
- Max leak (ms): 0
- Average leak (ms): 0.0

## D. Exact rows affected
None

## E. Recommended fixes
- Enforce an as-of join keyed by prediction timestamp for every external feature source.
- Reject or delay rows whose funding, open interest, liquidation, LOB, or regime timestamp is newer than the prediction timestamp.
- Preserve source event timestamps in training artifacts so leakage checks remain auditable.

## Summary metrics
- Total Rows: 0
- Rows Audited: 0
- Violations: 0
- Violation Rate: 0.0
- Max Leak (ms): 0
- Average Leak (ms): 0.0
