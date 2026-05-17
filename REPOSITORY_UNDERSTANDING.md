# REPOSITORY_UNDERSTANDING.md
## Phase 4 Surgical Hardening — Confirmed Dependency Graph

### Confirmed Production Pipeline Flow

```
BacktestEngine._run_single_pass(ohlcv_data, book_features)
  └── _validate_l2_timestamp_alignment(l2_path, start_ms, end_ms)  ← [NEW: Issue C]
  └── _build_canonical_are_payload(bar, book_feat)
        ↓ real L1 spread/imbalance from BookSnapshot
        ↓ ofi_z = 0.0 (synthetic — no Dec-2023 L2 depth confirmed by Issue C)
  └── AdvancedRegimeEngine.update()
        → FeatureEngine.update()
        → NHHMM_Engine.forward_pass_step()
        → SparseJumpModel.online_predict()
        → MSGARCH_RiskEngine._garch_update()   ← IGARCH shock blending
        → [NEW Issue B] defense-in-depth norm before argmax
        → compute_hmm_regime(sjm_probs, ...)
  └── LiquiditySweepAlpha.predict_sweep()
  └── SignalEngine.generate_signal()
  └── AlphaOrchestrator.orchestrate([signal_engine, lsa])
        action_threshold=0.60  ← SACRED, unchanged
  └── [NEW Issue D] orchestration guard (force HOLD if orchestrator unavailable)
  └── ExecutionLogic.decide()  — only if action != HOLD
  └── [Result includes backtest_label: PRODUCTION-VALID | NON-PRODUCTION-VALID]
```

### Confirmed Dangerous Couplings Found

| Coupling | Severity | Status After Hardening |
|---------|---------|----------------------|
| BinanceFuturesStreamClient in engine.py — importable from backtest path | HIGH | FIXED (Issue A) — raises ImportError without BTCBOT_LIVE_MODE=1 |
| TelegramAlertSystem in engine.py — same file as backtest engine | HIGH | FIXED (Issue A) |
| SniperExecutionEngine in engine.py — instantiates BinanceFuturesStreamClient | HIGH | FIXED (Issue A) |
| HMM posterior prob_sum != 1.0 after IGARCH shock blending | HIGH | FIXED (Issue B) |
| bookDepth_L2.csv timestamps 3+ years off accepted silently | HIGH | FIXED (Issue C) — rejected with NON-PRODUCTION-VALID label |
| orch_action_str could theoretically be set from signal_engine bypass | MEDIUM | FIXED (Issue D) — explicit guard + HOLD enforcement |
| 70+ silent `except: pass` swallowing exceptions in core path | MEDIUM | FIXED (Issue E) — all replaced with logger.debug |
| l2_pipeline.py WebSocket reconnect has fixed 2s sleep, no stale-feed metric | MEDIUM | FIXED (Issue F) — exponential backoff + stale_feed_watchdog() |

### Module Dependency Map (Confirmed)

```
engine.py
  ├── AdvancedRegimeEngine (advanced_regime_engine.py)
  ├── AlphaOrchestrator (alpha_orchestrator.py)
  ├── SignalEngine (signal_engine.py)  [DO NOT MODIFY]
  ├── FeatureEngine (feature_engine.py)  [DO NOT MODIFY]
  ├── ExecutionLogic (execution.py)  [DO NOT MODIFY]
  ├── LiquiditySweepAlpha (alpha_liquidity_sweep_predictor.py)
  └── [LIVE ONLY] BinanceFuturesStreamClient, TelegramAlertSystem,
                  SniperExecutionEngine  ← guarded by BTCBOT_LIVE_MODE=1

backtest_engine.py
  ├── engine.run_all_engines()
  ├── AdvancedRegimeEngine
  ├── AlphaOrchestrator  ← orchestrator_action_threshold=0.60 SACRED
  ├── data_tools/l2_to_backtest.py (align_book_to_bars, load_book_csv)
  └── [NEW] _validate_l2_timestamp_alignment() static method

l2_pipeline.py  [LIVE streaming only — NOT in backtest path]
  └── WebSocket → Binance depth + trade streams
  └── [NEW] stale_feed_watchdog() + exponential backoff reconnect

data_tools/l2_pipeline.py  [Backtest replay only — DO NOT confuse with root l2_pipeline.py]
  └── build_aligned_book_features(bars, book_csv_path)
  └── data_tools/l2_to_backtest.py (BookSnapshot, align_book_to_bars)
```
