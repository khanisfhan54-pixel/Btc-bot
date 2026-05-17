# ISSUE_MAPPING.md
## Phase 4 Surgical Hardening — Exact Fix Locations

### Issue A — Live Execution Guard

| Target | File | Function | Lines | Change |
|--------|------|----------|-------|--------|
| engine.py guard comment | `engine.py` | module-level | before L5331 | Inserted comment block documenting BTCBOT_LIVE_MODE requirement |
| BinanceFuturesStreamClient guard | `engine.py` | `BinanceFuturesStreamClient.__init__` | L5339–5344 | `ImportError` raised unless `BTCBOT_LIVE_MODE=1` |
| TelegramAlertSystem guard | `engine.py` | `TelegramAlertSystem.__init__` | L5563–5567 | `ImportError` raised unless `BTCBOT_LIVE_MODE=1` |
| SniperExecutionEngine guard | `engine.py` | `SniperExecutionEngine.__init__` | L5607–5611 | `ImportError` raised unless `BTCBOT_LIVE_MODE=1` |
| BacktestEngine anti-live guard | `backtest_engine.py` | `BacktestEngine.__init__` | L264–269 | `RuntimeError` raised if `BTCBOT_LIVE_MODE=1` |

### Issue B — HMM Posterior Normalization Defense-in-Depth

| Target | File | Function | Lines | Change |
|--------|------|----------|-------|--------|
| Final sjm_probs normalization | `advanced_regime_engine.py` | `AdvancedRegimeEngine.update` (outer method) | L4845–4848 | Added `if np.all(np.isfinite(sjm_probs)): sjm_probs = _normalize_prob_vector(sjm_probs); sjm_state = int(np.argmax(sjm_probs))` before `self.current_regime_idx = sjm_state` |

### Issue C — L2 Timestamp Validation

| Target | File | Function | Lines | Change |
|--------|------|----------|-------|--------|
| Static validator method | `backtest_engine.py` | `BacktestEngine._validate_l2_timestamp_alignment` | L587–651 | NEW static method: opens CSV, reads first timestamp, rejects if outside Dec-2023 window |
| Validation call | `backtest_engine.py` | `BacktestEngine._run_single_pass` | L827–862 | Calls `_validate_l2_timestamp_alignment` for 3 candidate L2 files; computes `_backtest_label` |
| BACKTEST_LABEL in return dict | `backtest_engine.py` | `BacktestEngine._run_single_pass` | L1318–1319 | Added `backtest_label` and `non_production_conditions` keys to return dict |

### Issue D — Orchestration Guard

| Target | File | Function | Lines | Change |
|--------|------|----------|-------|--------|
| Post-orchestrator guard | `backtest_engine.py` | `BacktestEngine._run_single_pass` | L1084–1107 | If not legacy_mode: asserts `orch_action_str` is valid; forces HOLD if orchestrator unavailable or `len(alpha_signals) < 2` |

### Issue E — Silent Pass Replacement

| File | Fixes Applied | Notes |
|------|--------------|-------|
| `engine.py` | 25 | All core + live-class handler swallows replaced with `logger.debug("[SWALLOWED] ...")` |
| `advanced_regime_engine.py` | 19 | All IGARCH, HMM, regime classification swallows replaced with `LOGGER.debug(...)` |
| `main.py` | 14 | All module-level and function-level swallows replaced |
| `alpha_orchestrator.py` | 4 | All orchestration swallows replaced |
| `backtest_engine.py` | 3 | All backtest-path swallows replaced |
| **Total** | **65** | Utility functions (`_safe_float`, `_safe_int`, `_safe_array`, `_normalize_prob_vector`, etc.) intentionally preserved as-is |

### Issue F — L2 Pipeline Reconnect + Stale-Feed Watchdog

| Target | File | Function | Lines | Change |
|--------|------|----------|-------|--------|
| `_last_depth_msg_ts` tracker | `l2_pipeline.py` | `handle_depth` | L42–76 | Records timestamp of last depth message; emits `[STALE_FEED]` warning on reconnect if silent > 30s |
| `_last_trade_msg_ts` tracker | `l2_pipeline.py` | `handle_trades` | L83–115 | Records timestamp of last trade message; exponential backoff capped at 60s |
| Exponential backoff | `l2_pipeline.py` | `handle_depth`, `handle_trades` | reconnect loop | `backoff = min(backoff * 2.0, 60.0)` replacing fixed `asyncio.sleep(2)` |
| `stale_feed_watchdog` coroutine | `l2_pipeline.py` | `stale_feed_watchdog` | L118–132 | Polls every 10s; emits `[STALE_FEED]` if either stream silent > `_STALE_FEED_THRESHOLD_SECONDS=30.0` |
| Watchdog wired into main | `l2_pipeline.py` | `main` | L143 | Added `stale_feed_watchdog()` to `asyncio.gather(...)` |
