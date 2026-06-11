# Liquidity Magnet Parity Audit (Phase 12)

## Finding
`backtest_engine.py::_build_magnet_prediction` passed a hardcoded `market_state["volatility"] = 1.0` while live paths can provide actual volatility or expected volatility.

## Fix
Backtest magnet input now sources volatility from the same feature contract used elsewhere: `volatility`, then `expected_volatility`, then `atr_pct`, defaulting to `0.0` when absent. Candidate generation remains based on production-origin `liquidity_zones`; fallback macro pools continue to mark the run non-production-parity.

## Contract
| Field | Live/Backtest Schema |
| --- | --- |
| `candidates` | List of liquidity-zone dictionaries with `price`, `side`, `type`, `age_bars`, and strength metadata when available. |
| `market_state.regime` | Regime label string. |
| `market_state.volatility` | Feature-provided volatility/expected-volatility/ATR-percent value, never hardcoded. |
| `market_state.atr` | ATR with positive floor. |

## Validation
- Compile check for the changed call path.
- Full backtest commands in validation flow.
