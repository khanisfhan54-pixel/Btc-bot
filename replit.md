# BTC Quant Trading System

## Audit harness (2026-05-02)
- `audit_runner.py` — assertion-based end-to-end production audit. Pulls live
  BTC/USDT public data from Binance USDT-M Futures, drives every public
  engine (AlphaOrchestrator, AdvancedRegimeEngine, LiquiditySweepAlpha,
  SignalEngine, BacktestEngine), runs concurrency stress + feedback loop,
  asserts schema and contract for every result, and writes
  `AUDIT_RUNNER_REPORT.json` plus a markdown summary.
- Last run: 59 checks → 56 PASS / 2 WARN / 0 FAIL / 0 SKIP. WARNs are
  (1) missing regime weights file → engine permanently DEGRADED, and
  (2) backtest produces 0 trades on 200 1m candles (signal coverage gap).
- Reproduce with: `python audit_runner.py`. Full report:
  `AUDIT_REPORT_2026-05-02.md`.

## Overview

A modular, production-grade Bitcoin (BTC/USDT) quantitative trading bot built in Python. This system runs in dry-run / signal-only mode by default and does not place real trades unless `LIVE_TRADING=true` is explicitly set.

## Architecture

- **main.py** — Entry point, bootstraps all modules, runs the main trading loop
- **engine.py** — Central orchestrator of all trading logic
- **execution.py** — Order execution logic
- **signal_engine.py** — Signal generation
- **feature_engine.py** — Market feature extraction
- **advanced_regime_engine.py** — Regime detection (requires calibration weights)
- **alpha_orchestrator.py** — Alpha signal orchestration
- **backtest_engine.py** — Strategy backtesting
- **learning_engine.py** — Adaptive learning system
- **telegram_bot.py** — Telegram notification integration

## Tech Stack

- **Language:** Python 3.12
- **Key packages:** ccxt, numpy, scipy, prometheus-client, websocket-client, python-dotenv
- **Exchange:** OKX for market data, Binance for execution (configurable via env vars)

## Running the Bot

The bot starts via the "Start application" workflow which runs `python main.py`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BINANCE_API_KEY` | "" | Binance API key for execution |
| `BINANCE_SECRET` | "" | Binance API secret |
| `TELEGRAM_BOT_TOKEN` | "" | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | "" | Telegram chat ID for alerts |
| `DRY_RUN` | "1" | If "1", no real orders placed |
| `LIVE_TRADING` | "false" | If "true", enables live order execution |
| `SIGNAL_ONLY_MODE` | "true" | If "true", only generates signals |
| `RISK_PERCENT_PER_TRADE` | "0.5" | Risk per trade as % of capital |

## Deployment

Configured as a VM deployment (`python main.py`) since it is a continuously-running process.

## Notes

- The `AdvancedRegimeEngine` requires trained weight files at `weights/advanced_regime_weights.npz` — it logs a warning and continues without it if not present.
- The system defaults to DRY_RUN and SIGNAL_ONLY_MODE for safety.

## Inter-module Action Vocabulary Contract (FIX D1)

`SignalEngine.generate()` publishes BOTH vocabularies on every payload:

- `out["signal"]` — **legacy** `LONG | SHORT | HOLD` for `execution.py` and
  `backtest_engine.py` (which have hard equality checks on those literals).
- `out["action"]` — **canonical** `BUY | SELL | HOLD` for any new consumer
  and the audit harness. Mapped via the `_DIR_TO_ACTION` table in
  `signal_engine.py → SignalEngine.generate()`.

New code MUST consume `action`. Never introduce *new* `LONG/SHORT` checks.

## Audit Remediation (2026-05-03) — phases A2, B1–B4, A1/A3 scaffolds, C1–C3, D1

| FIX | Scope | Effect |
|---|---|---|
| **A2** | `advanced_regime_engine.py::_build_output` | `engine_status` / `regime_label` / `signal_valid` are now mutually consistent. UNCALIBRATED → DEGRADED + `signal_valid=False`. |
| **B1** | `signal_engine.py::SignalEngine.generate` | LONG→BUY, SHORT→SELL canonical vocab at boundary; `out["action"]` alias added. |
| **B2** | `feature_engine.py::FeatureEngine.update` | `imbalance` setdefault cascade restored (failsafe gate stays armed). |
| **B3** | `engine.py::detect_smart_money_absorption` and `smart_money_absorption_engine` | Defensive unpack guards against `[price, size, count]` rows that previously raised "too many values to unpack". |
| **B4** | `signal_engine.py::_validate_alpha` | `Alpha validation adjusted: {}` log spam suppressed for empty payloads; downgraded to DEBUG. |
| **A1** | `calibrate_regime.py` (new) + import-time WARN in `advanced_regime_engine.py` | CLI scaffold for `weights/advanced_regime_weights.npz` calibration; startup WARN with the exact reproduction command. |
| **A3** | `signal_engine.py` | `DEBUG_VETO=1` env switch logs the gate that killed each candidate. |
| **C1** | `advanced_regime_engine.py` | Prometheus gauges `regime_engine_signal_valid`, `_multiplier`, `_win_rate`, `_gate_veto_count` (import-guarded). |
| **C2** | `audit_runner.py` | `--strict` flag exits non-zero on any FAIL or SKIP for CI gating. |
| **C3** | `alpha_orchestrator.py::AlphaPerformanceStats` | `last_n_multipliers` ring buffer (maxlen=128) for oscillation diagnostics. |
| **D1** | `replit.md` (this file) | Action-vocabulary contract documented. |

Reproduce the audit: `python audit_runner.py` (add `--strict` for CI).
Diagnose the 0-trade backtest: `DEBUG_VETO=1 python audit_runner.py`.
