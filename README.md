# Stop Hunt Probability Engine (SHPE)

Probability layer for the BTC trading bot that estimates the likelihood of
a stop-hunt sweep at each 5-minute bar. SHPE never places orders; it exposes
a single probability score consumed by the bot's risk gate.

## Integration

```python
from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput

output = get_shpe_probability(engine, pipeline_input, bar_index)
# output == {"probability": float, "degraded": bool, "regime_used": str}
```

## Feature dimensions

| Dimension | Module | Stale threshold |
|---|---|---|
| Pool distance | `pool_distance.py` | never (candle-derived) |
| Funding pressure | `funding_pressure.py` | 12 h |
| OI dynamics | `oi_dynamics.py` | 20 min |
| Volume trap | `volume_trap.py` | never (candle-derived) |
| LOB imbalance | `lob_imbalance.py` | 120 s |
| Liquidation proximity | `liquidation_proximity.py` | 1 h |
| Regime context | `regime_context.py` | 5 min |

## Safety rules

- If > 2 feature dimensions are stale: `degraded=True`, `p_sweep=0.5`.
- Unknown regime falls back to the global model.
- All probabilities are bounded to [0, 1].
- No crashes from missing data, NaNs, or missing snapshots.

## Validation

```bash
pytest stop_hunt_engine/tests/ -v
```

## What is production-safe (post PR #214)

- Dependency restore: all original bot deps present.
- Feature modules: real implementations, no stubs.
- Calibration: leakage-free holdout split (temporal, not random).
- Integration adapters: safe, no execution side-effects.
- Walk-forward CV: expanding-window and rolling-window variants.
- Permutation audit: implemented and tested.

## What still requires market validation

- Probability thresholds for risk gating (requires live paper-trading data).
- Per-regime sub-model sample sizes (requires labelled regime history).
- Calibration stability across exchange feed outages.
- Feature importance rankings under real market microstructure.

## Offline L1 BookTicker feature parquet workflow

The offline feature builder in `preprocess/build_btc_feature_parquets.py` converts Binance BTCUSDT BookTicker (L1/top-of-book only) plus AggTrades CSV files into native 1-minute and 5-minute parquet feature datasets. The `l1_order_flow_proxy` and compatibility aliases `ofi_zscore`/`ofi_norm` are explicitly L1 proxy features; they are not true multi-level L2 OFI and must not be routed through the L2-only CSV loader.

Example VPS preprocessing command:

```bash
python3 preprocess/build_btc_feature_parquets.py \
  --bookticker /home/ubuntu/btc_bot_data/raw/BTCUSDT_240329-bookTicker-2024-01.csv \
  --aggtrades /home/ubuntu/btc_bot_data/raw/BTCUSDT_240329-aggTrades-2024-01.csv \
  --outdir /home/ubuntu/btc_bot_data/processed \
  --symbol BTCUSDT
```

Example feature validation command:

```bash
python3 preprocess/build_btc_feature_parquets.py \
  --validate-parquet /home/ubuntu/btc_bot_data/processed/features_1m.parquet \
  --validate-parquet /home/ubuntu/btc_bot_data/processed/features_5m.parquet
```

Example feature-parquet backtest command, using the public BacktestEngine API and production-valid mode by default:

```bash
python3 run_backtest_from_features.py \
  --features-1m /home/ubuntu/btc_bot_data/processed/features_1m.parquet \
  --features-5m /home/ubuntu/btc_bot_data/processed/features_5m.parquet
```

Use `--legacy-mode` only for explicit diagnostic runs.

Example deterministic smoke test (creates tiny temporary raw CSVs, writes parquets, validates them, and calls the public backtest API; no live access):

```bash
python3 run_backtest_from_features.py --smoke-test
```

## Offline SHPE ML workflow

The offline SHPE training workflow is separate from inference and execution. It
uses the documented temporary target in `docs/SHPE_TARGET_DEFINITION.md`, builds
or loads a 5-minute BTCUSDT feature dataset, generates future-only labels,
trains/calibrates the existing SHPE model stack, saves versioned artifacts, runs
expanding-window walk-forward validation, and writes JSON/Markdown reports.

Deterministic local smoke command:

```bash
python -m stop_hunt_engine.training --smoke-test --run-version smoke
```

For real processed data, pass the 5-minute parquet produced by
`preprocess/build_btc_feature_parquets.py`:

```bash
python -m stop_hunt_engine.training \
  --features-5m /home/ubuntu/btc_bot_data/processed/features_5m.parquet \
  --run-version btcusdt_5m_YYYYMM
```

Artifacts are written under `artifacts/shpe/datasets/`, `artifacts/shpe/labels/`,
`artifacts/shpe/models/`, and `artifacts/shpe/reports/`.
