# Queue Model Audit (Phase 11)

## Root Cause
`QueueFillModel` enriched feature dictionaries with `fill_probability`, side-specific probabilities, queue depth, expected slippage, and confidence, but the backtest entry path ignored those values and opened the requested size instantly.

## Fix
- Added `simulate_queue_fill()` as an isolated execution/accounting helper.
- Entry fills now consume side-specific fill probability, fill confidence, and top-of-book displayed quantity.
- Partially filled orders track remaining size in `pending_order` across bars until filled or timed out.
- Zero-fill orders remain pending and are cancelled after `queue_fill_timeout_bars`.

## Validation Matrix
| Case | Expected | Test |
| --- | --- | --- |
| 100% fill | Requested size fully filled. | `test_queue_fill_100_percent` |
| 50% fill | Half requested size filled; remainder is representable as pending. | `test_queue_fill_50_percent` |
| 0% fill | No realized fill. | `test_queue_fill_0_percent` |

## Remaining Risks
Exit queue priority and exchange-specific maker/taker queue rules remain simplified and should be calibrated against venue order acknowledgements before live capacity claims.
