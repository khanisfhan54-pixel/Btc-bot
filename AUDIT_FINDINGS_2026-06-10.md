# Trading Systems Audit Evidence — 2026-06-10

## WARNING SOURCE REPORT

Runtime instrumentation was enabled with `BTCBOT_MAGNET_AUDIT=1` and records timestamp, caller function, caller file, market_state, regime, volatility, trend_direction, atr, missing_fields, and stack trace at `LiquidityMagnetPredictor.predict()` entry.

First observed `market_state missing trend_direction` warning in the instrumented test pipeline:

- Command: `BTCBOT_MAGNET_AUDIT=1 python3 -m pytest tests/test_liquidity_magnet_predictor.py tests/test_magnet_critical_fixes.py -q -o log_cli=true -o log_cli_level=WARNING`
- Result: `34 passed in 0.62s`.
- Timestamp: `2026-06-10T15:40:40.038971+00:00`.
- Exact caller: `predict_liquidity_magnet`.
- Exact caller file/line: `/workspace/Btc-bot/liquidity_magnet_predictor.py:525`.
- Exact direct consumer: `LiquidityMagnetPredictor.predict()` at `/workspace/Btc-bot/liquidity_magnet_predictor.py:297`.
- Exact warning function: `LiquidityMagnetPredictor._score_regime()` at `/workspace/Btc-bot/liquidity_magnet_predictor.py:267`.
- Exact market_state contents: `{"atr": 100.0, "regime": "normal"}`.
- Exact missing fields: `trend_direction`, `volatility`.
- Exact stack: `tests/test_liquidity_magnet_predictor.py:132 test_no_lookahead_integration_smoke -> liquidity_magnet_predictor.py:525 predict_liquidity_magnet -> liquidity_magnet_predictor.py:311 LiquidityMagnetPredictor.predict -> liquidity_magnet_predictor.py:51 _emit_predict_audit_event`; scoring then enters `_score_regime()` and emits the warning.
- Why field was missing: the test intentionally supplies `market_state = {"atr": 100.0, "regime": "normal"}` without `trend_direction` or `volatility`.
- Canonical path status: non-canonical helper/test path. The helper requires an explicit persistent predictor instance; live canonical code calls `engine.get_shared_magnet_predictor().predict()` and backtest canonical code calls an isolated `self.magnet_predictor.predict()`.

## MISSING VOLATILITY VERDICT

Verdict: DISPROVEN for the discovered canonical live and canonical backtest `LiquidityMagnetPredictor.predict()` call sites; PROVEN only for the non-canonical helper/test path above.

Canonical builders observed in code:

| Builder function | Required fields | Actual fields | Consumer |
| --- | --- | --- | --- |
| `engine.run_all_engines` inline magnet `market_state` | `regime`, `volatility`, `trend_direction`, `atr` | all four fields present | `get_shared_magnet_predictor().predict()` |
| `backtest_engine.BacktestEngine._build_magnet_prediction` inline magnet `market_state` | `regime`, `volatility`, `trend_direction`, `atr` | all four fields present | `self.magnet_predictor.predict()` |
| `liquidity_magnet_predictor.predict_liquidity_magnet` pass-through helper | `regime`, `volatility`, `trend_direction`, `atr` | caller-controlled; first runtime hit supplied only `atr`, `regime` | `inst.predict()` |

Runtime command evidence:

- Live pipeline command: `BTCBOT_MAGNET_AUDIT=1 timeout 15s python3 main.py`; result: environment warning. The live cycle failed before magnet prediction due exchange/network access: `[EXCHANGE] Failed to load markets for binance: binance GET https://api.binance.com/api/v3/exchangeInfo` and proxy `403` errors.
- Main backtest command: `BTCBOT_MAGNET_AUDIT=1 timeout 60s python3 main.py backtest`; result: environment warning. It failed before backtest bars due Binance `exchangeInfo` fetch failure.
- Synthetic audit harness command: `BTCBOT_MAGNET_AUDIT=1 timeout 60s python3 run_backtest.py`; result: pass, but this harness uses `LiquiditySweepAlpha` and did not invoke `LiquidityMagnetPredictor.predict()`.
- Full test command: `BTCBOT_MAGNET_AUDIT=1 python3 -m pytest -q`; result: collection failed before execution because `tests.action_expectations` was not importable as a package module.

## LEAKAGE AUDIT TABLE

| Module | Function | Risk | Proof | Status |
| --- | --- | --- | --- | --- |
| `AdvancedRegimeEngine` | `update` | Future regime/volatility access | Input is the current `market_data` dict; timestamp is normalized from `market_data.get("timestamp")`. Repository scan found no `shift(-1)` or `iloc[i+1]` in this module. Runtime calibration failure prevents production signal validity without weights. | SAFE for searched future-index patterns; RISK for uncalibrated runtime |
| `SignalEngine` | `generate_signal` / `generate` | Future candle access | Uses normalized current payload candles and fallback `fallback_source[-3:]`; repository scan found no `shift(-1)` or `iloc[i+1]` in this module. | SAFE for searched future-index patterns |
| `LiquiditySweepAlpha` | `predict` | Directional fallback leakage | Calls `get_signal(data)` on supplied current data; fallback uses current data fields and `predict_sweep`. Repository scan found no `shift(-1)` or `iloc[i+1]` in this module. | SAFE for searched future-index patterns |
| `LiquidityMagnetPredictor` | `predict` | Future liquidity-zone/regime access | Scores only provided candidates/current price/current time/market_state; repository scan found no `shift(-1)` or `iloc[i+1]` in this module. | SAFE for searched future-index patterns |
| `BacktestEngine` | `_run_single_pass` | Future volatility normalization | Code explicitly states full-sample mean/std would leak future bars and computes `vol_window = data[max(0, i - rolling_vol_window + 1): i + 1]`. | SAFE for volume normalization; RISK because weights missing skipped ARE payloads |
| `FeatureEngine` | `update` / `_compute` | Future book/trade features | Computes from the supplied snapshot, top levels, and supplied recent trades; repository scan found no `shift(-1)` or `iloc[i+1]` in this module. | SAFE for searched future-index patterns |

## CALIBRATION REPORT

Runtime initialization evidence from `main.py backtest`, live startup, and targeted backtest tests:

- `weights_loaded`: `False`.
- `engine_status`: `DEGRADED`.
- `calibration_status`: `missing`.
- `fallback_mode`: active for SHPE and calibration-norm load; regime engine blocks/halt paths when weights are required.
- `signal_valid`: `False` when `AdvancedRegimeEngine` emits output with missing weights.
- `execution_mode`: `halt` in missing-weight paths.
- Production calibration verdict: `UNCALIBRATED`.

Observed runtime lines:

- `[WEIGHTS] Weight file not found for model 'advanced_regime': weights/advanced_regime_weights.npz`.
- `[REGIME] Missing trained weights at weights/advanced_regime_weights.npz; blocking regime engine until calibration artifacts are available.`
- `FIX-6: failed to load calibration norms: [Errno 2] No such file or directory: 'weights/advanced_regime_weights.npz'`.
- `SHPE boot: no model loaded — running in degraded mode (p=0.5). calibrator.pkl present=True.`

## LIVE VS BACKTEST PARITY AUDIT

| Row | LIVE | BACKTEST | IDENTICAL? | Impact | Severity | Expected PnL distortion |
| --- | --- | --- | --- | --- | --- | --- |
| Feature Source | Live `FeatureEngine.update()` over exchange snapshots/trades | Backtest `FeatureEngine.update()` over candle-derived snapshots/book features | NO | synthetic or replay book features alter microstructure signals | HIGH | HIGH |
| Regime Source | `AdvancedRegimeEngine.update()` in runtime path, but weights missing | Same engine, but canonical payload rejected when calibration norms missing | NO at runtime | both degraded/uncalibrated; backtest may skip bars | CRITICAL | HIGH |
| Volatility Source | `_estimate_volatility_from_ohlcv()` and live regime metrics | rolling backtest volume/vol windows through current bar only | NO | different estimator inputs | MEDIUM | MEDIUM |
| ATR Source | `_atr(primary_1m[-30:], 14)` or current-price fallback | candle high-low range passed to magnet | NO | distance/horizon scoring differs | MEDIUM | MEDIUM |
| Liquidity Zone Source | live `analyze_liquidity_intent(...).liquidity_zones` | replay features `liquidity_zones` else high/low pool fallback | NO | magnet parity explicitly marked non-production when missing | HIGH | HIGH |
| Order Book Source | live exchange orderbook | synthetic/replayed book snapshots | NO | OFI/imbalance divergence | HIGH | HIGH |
| Trade Source | live exchange trades | synthetic/replayed trades | NO | sweep/CVD/order-flow divergence | HIGH | HIGH |
| Execution Logic | live execution stack guarded by live-mode credentials | backtest `ExecutionLogic.decide()` and simulated fills | NO | fill/latency/queue assumptions | HIGH | HIGH |
| Risk Logic | live fail-closed exchange/risk gates | backtest capital/position simulation | NO | live blocks not fully replayed | HIGH | HIGH |
| Position Sizing | live allocator/position manager | backtest sizing simulation | NO | exposure differs | HIGH | HIGH |

## MICROSTRUCTURE AUDIT

| Signal | Runtime source status |
| --- | --- |
| CVD | Derived from supplied trades where available; replay synthetic if harness-generated. |
| OI | Live path passes open interest when available; backtest synthetic harness lacks real OI parity. |
| Funding | Live path passes funding when available; backtest synthetic harness lacks real funding parity. |
| Order Book Imbalance | Live exchange book in live path; synthetic/replay book in backtest paths. |
| OFI | `FeatureEngine` computes from supplied current book state; quality depends on live vs replay book. |
| Sweep Detection | `LiquiditySweepAlpha` on supplied market data; synthetic/replay if no real L2/trades. |
| Liquidity Pools | Live liquidity intent zones; backtest fallback high/low pools when production-origin zones unavailable. |
| Liquidation Data | No proven real liquidation feed in observed run; not runtime-proven. |

Production parity score from collected evidence: `35/100`.
Profitability confidence from collected evidence: `10/100`.
