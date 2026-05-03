#!/usr/bin/env python3
"""
audit_runner.py — End-to-end production audit of the BTC quant trading codebase.

Runs every public alpha pipeline against LIVE Binance / OKX market data and
captures every exception, schema violation and degraded-mode signal so we can
produce a senior-engineer audit report.

Usage:
    python audit_runner.py
"""
from __future__ import annotations

import os
import sys
import json
import time
import math
import argparse
import traceback
import threading
import random
from typing import Any, Dict, List, Tuple

# FIX C2: --strict flag for CI hard-fail on FAIL/SKIP.
_arg_parser = argparse.ArgumentParser(add_help=False)
_arg_parser.add_argument("--strict", action="store_true",
                         help="Exit non-zero on any FAIL or new SKIP (for CI)")
_args, _ = _arg_parser.parse_known_args()

# ---------------------------------------------------------------
# STEP 1: ENVIRONMENT SETUP
# ---------------------------------------------------------------
os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("SIGNAL_ONLY_MODE", "false")
os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("BACKTEST_STRICT_CALIBRATION", "0")
os.environ.setdefault("REGIME_WEIGHT_PATH", "weights/advanced_regime_weights.npz")

# Make sure the dummy weight directory exists (file may legitimately be absent).
os.makedirs("weights", exist_ok=True)

# Audit findings buffer
FINDINGS: List[Dict[str, Any]] = []


def record(category: str, name: str, status: str,
           detail: str = "", **extra: Any) -> None:
    """Push a structured result row into the audit log."""
    row = {"category": category, "name": name, "status": status, "detail": detail}
    row.update(extra)
    FINDINGS.append(row)
    tag = {"PASS": "[PASS]", "WARN": "[WARN]",
           "FAIL": "[FAIL]", "INFO": "[INFO]",
           "SKIP": "[SKIP]"}.get(status, "[?]")
    extras = " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    print(f"{tag} {category} :: {name} :: {detail} {extras}".rstrip())


def assert_check(category: str, name: str, condition: bool,
                 detail: str, fail_status: str = "FAIL", **extra: Any) -> bool:
    """Record a PASS if condition is True, else fail_status (FAIL/WARN)."""
    record(category, name, "PASS" if condition else fail_status, detail, **extra)
    return condition


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


# ---------------------------------------------------------------
# STEP 2: LIVE DATA FETCH
# ---------------------------------------------------------------
section("STEP 2: LIVE MARKET DATA FETCH (Binance / OKX public APIs)")

ohlcv_1m = ohlcv_5m = ohlcv_15m = ohlcv_1h = None
orderbook = None
trades = None
data_exchange_used = None

try:
    import ccxt  # type: ignore
except Exception as e:
    record("data", "ccxt_import", "FAIL", f"{type(e).__name__}: {e}")
    sys.exit(1)

import numpy as np  # noqa: E402

_VENUES = (
    ("binance", {"enableRateLimit": True, "options": {"defaultType": "future"}}),
    ("binance", {"enableRateLimit": True}),  # spot fallback
    ("okx",     {"enableRateLimit": True}),
)

for venue, opts in _VENUES:
    try:
        exch_cls = getattr(ccxt, venue)
        ex = exch_cls(opts)
        symbol = "BTC/USDT" if venue == "binance" else "BTC/USDT"
        t0 = time.time()
        o1  = ex.fetch_ohlcv(symbol, "1m",  limit=200)
        o5  = ex.fetch_ohlcv(symbol, "5m",  limit=200)
        o15 = ex.fetch_ohlcv(symbol, "15m", limit=200)
        o1h = ex.fetch_ohlcv(symbol, "1h",  limit=200)
        ob  = ex.fetch_order_book(symbol, limit=20)
        tr  = ex.fetch_trades(symbol, limit=200)
        latency_ms = (time.time() - t0) * 1000.0
        ohlcv_1m, ohlcv_5m, ohlcv_15m, ohlcv_1h = o1, o5, o15, o1h
        orderbook, trades = ob, tr
        data_exchange_used = f"{venue}({'futures' if 'options' in opts else 'spot'})"
        record("data", "fetch_all", "PASS",
               f"venue={data_exchange_used} candles={len(o1)} ob_depth={len(ob.get('bids', []))} trades={len(tr)}",
               latency_ms=round(latency_ms, 1))
        break
    except Exception as e:
        record("data", f"fetch_{venue}", "WARN",
               f"{type(e).__name__}: {str(e)[:160]}")
        continue

if ohlcv_1m is None:
    record("data", "fetch_all", "FAIL", "All venues failed; cannot continue")
    sys.exit(2)

# Quality checks on the fetched data
try:
    bid_vol = sum(float(b[1]) for b in orderbook["bids"][:10])
    ask_vol = sum(float(a[1]) for a in orderbook["asks"][:10])
    record("data", "orderbook_quality", "PASS",
           f"bid_vol_top10={bid_vol:.4f} ask_vol_top10={ask_vol:.4f} spread={(orderbook['asks'][0][0]-orderbook['bids'][0][0]):.2f}")
except Exception as e:
    record("data", "orderbook_quality", "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------
# STEP 3: ALPHA ORCHESTRATOR LIVE TEST
# ---------------------------------------------------------------
section("STEP 3: ALPHA ORCHESTRATOR — 5 SCENARIOS")

orch_results: Dict[str, Dict[str, Any]] = {}
try:
    from alpha_orchestrator import (
        AlphaOrchestrator, OrchestratorConfig, AlphaSignal,
        RegimeContext, FeatureQuality, ExecutionState, Action,
    )
except Exception as e:
    record("orchestrator", "import", "FAIL", f"{type(e).__name__}: {e}")
    AlphaOrchestrator = None  # type: ignore

if AlphaOrchestrator is None:
    record("orchestrator", "construct", "SKIP", "import failed; downstream tests skipped")
if AlphaOrchestrator is not None:
    try:
        config = OrchestratorConfig(
            signal_weights={"signal_engine": 1.0, "liquidity_sweep": 0.8},
            allow_unknown_sources=False,
            timeframe_weights={"1m": 1.0, "5m": 0.8, "15m": 0.6, "default": 0.5},
            timeframe_order=["1m", "5m", "15m", "default"],
            higher_tf_dominance=True,
            feedback_enabled=True,
            regime_feedback_enabled=True,
            signal_ttl_seconds=60.0,
            max_drawdown_pct=0.15,
            max_missing_data_ratio=0.3,
        )
        orchestrator = AlphaOrchestrator(config)
        record("orchestrator", "construct", "PASS", "OrchestratorConfig + AlphaOrchestrator built")
    except Exception as e:
        record("orchestrator", "construct", "FAIL",
               f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")
        orchestrator = None

    if orchestrator is not None:
        now = time.time()
        regime = RegimeContext("trending", 0.3, 0.7)
        quality = FeatureQuality(0.05, 0.02)
        exec_state = ExecutionState(1000.0, 50000.0, 0.05)

        def _safe_signals(builder):
            try:
                return builder()
            except Exception as e:
                return e

        signal_specs = {
            "normal_buy": lambda: [
                AlphaSignal("signal_engine", 1, 0.75, 12.0, now, "1m", "directional"),
                AlphaSignal("liquidity_sweep", 1, 0.65, 8.0, now, "5m", "directional"),
            ],
            "conflicting": lambda: [
                AlphaSignal("signal_engine",  1, 0.75, 12.0, now,        "1m",  "directional"),
                AlphaSignal("liquidity_sweep", -1, 0.70, 10.0, now,       "15m", "directional"),
            ],
            "stale": lambda: [
                AlphaSignal("signal_engine", 1, 0.75, 12.0, now - 999.0, "1m", "directional"),
            ],
            "empty": lambda: [],
            # Bad input intentionally bypasses construction
            "bad_input": lambda: "not_a_list",
        }

        # Expected action contracts per scenario.
        expected_action = {
            "normal_buy":  "BUY",
            "stale":       "HOLD",
            "empty":       "HOLD",
            "bad_input":   "HOLD",
            # "conflicting" intentionally has no fixed expected — direction depends on
            # higher_tf_dominance config + fusion math; we only assert it's BUY or SELL.
        }

        for name, builder in signal_specs.items():
            sigs = _safe_signals(builder)
            if isinstance(sigs, Exception):
                record("orchestrator", f"scenario_{name}", "FAIL",
                       f"AlphaSignal construction failed: {type(sigs).__name__}: {sigs}")
                orch_results[name] = {"error": str(sigs)}
                continue
            try:
                result = orchestrator.orchestrate(
                    sigs, regime, quality, exec_state, current_time=now,
                )
                action_name = getattr(result.action, "name", str(result.action))
                conv = float(result.net_conviction)
                edge = float(result.expected_edge_bps)
                urg = float(result.urgency)
                orch_results[name] = {
                    "action": action_name,
                    "net_conviction": conv,
                    "expected_edge_bps": edge,
                    "urgency": urg,
                }
                detail = (f"action={action_name} conv={conv:.4f} "
                          f"edge={edge:.2f}bps urg={urg:.3f}")
                # Assertion-based PASS: validate the contract, not just absence of crash.
                if name in expected_action:
                    ok = action_name == expected_action[name]
                    assert_check("orchestrator", f"scenario_{name}", ok,
                                 f"{detail} | expected={expected_action[name]}")
                elif name == "conflicting":
                    ok = action_name in ("BUY", "SELL") and abs(conv) > 0.0
                    assert_check("orchestrator", f"scenario_{name}", ok,
                                 f"{detail} | expected directional + non-zero conv")
                else:
                    record("orchestrator", f"scenario_{name}", "PASS", detail)
                # Field schema assertion: every result must expose these.
                assert_check("orchestrator", f"scenario_{name}_schema",
                             all(math.isfinite(v) for v in (conv, edge, urg)),
                             f"all of (conv,edge,urg) finite: {(conv, edge, urg)}")
            except Exception as e:
                orch_results[name] = {"error": f"{type(e).__name__}: {e}"}
                record("orchestrator", f"scenario_{name}", "FAIL",
                       f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------
# STEP 3b: ALPHA ORCHESTRATOR — DEEP-DIVE INVARIANTS
#   - lookahead bias
#   - numerical stability (NaN/Inf payloads)
#   - role separation (drawdown applied post-fusion only)
#   - hierarchy enforcement (higher_tf_dominance true vs false)
# ---------------------------------------------------------------
section("STEP 3b: ALPHA ORCHESTRATOR — DEEP-DIVE INVARIANTS")

if AlphaOrchestrator is not None and orchestrator is not None:
    now = time.time()
    regime_d = RegimeContext("trending", 0.3, 0.7)
    quality_d = FeatureQuality(0.05, 0.02)
    exec_state_d = ExecutionState(1000.0, 50000.0, 0.05)

    # ---- (a) Lookahead bias — future-dated signals must not be honoured ----
    try:
        future_sigs = [AlphaSignal(
            "signal_engine", 1, 0.9, 15.0, now + 600.0, "1m", "directional",
        )]
        r_future = orchestrator.orchestrate(
            future_sigs, regime_d, quality_d, exec_state_d, current_time=now,
        )
        action = r_future.action.name
        # Defensible contracts: either reject as HOLD, or accept but never
        # exceed the conviction of an equivalent past signal at the boundary.
        assert_check(
            "orchestrator_deep", "lookahead_future_signal_safe",
            action in ("HOLD",) or float(r_future.net_conviction) <= 0.9,
            f"future-dated signal action={action} conv={float(r_future.net_conviction):.4f} "
            f"(must be HOLD or have non-amplified conviction)",
            fail_status="WARN",
        )
    except Exception as e:
        record("orchestrator_deep", "lookahead_future_signal_safe", "FAIL",
               f"{type(e).__name__}: {e}")

    # ---- (b) Numerical stability — AlphaSignal validation rejects NaN/Inf ----
    nan_rejected = inf_rejected = False
    try:
        AlphaSignal("signal_engine", 1, float("nan"), 10.0, now, "1m", "directional")
    except Exception:
        nan_rejected = True
    try:
        AlphaSignal("signal_engine", 1, 0.5, float("inf"), now, "1m", "directional")
    except Exception:
        inf_rejected = True
    assert_check("orchestrator_deep", "numerical_nan_conviction_rejected",
                 nan_rejected, f"AlphaSignal(conviction=NaN) rejected={nan_rejected}")
    assert_check("orchestrator_deep", "numerical_inf_edge_rejected",
                 inf_rejected, f"AlphaSignal(edge=+Inf) rejected={inf_rejected}")

    # ---- (b2) Output finiteness under a long mixed batch ----
    try:
        mixed = [
            AlphaSignal("signal_engine",  1, 0.7,  10.0, now, "1m",  "directional"),
            AlphaSignal("liquidity_sweep", -1, 0.6,  8.0, now, "5m",  "directional"),
            AlphaSignal("signal_engine",  1, 0.4,  3.0, now, "15m", "directional"),
        ]
        r_mix = orchestrator.orchestrate(
            mixed, regime_d, quality_d, exec_state_d, current_time=now,
        )
        finite = all(math.isfinite(float(v)) for v in
                     (r_mix.net_conviction, r_mix.expected_edge_bps, r_mix.urgency))
        assert_check("orchestrator_deep", "output_finite_under_mixed_batch",
                     finite,
                     f"conv={r_mix.net_conviction:.4f} edge={r_mix.expected_edge_bps:.2f} urg={r_mix.urgency:.4f}")
    except Exception as e:
        record("orchestrator_deep", "output_finite_under_mixed_batch", "FAIL",
               f"{type(e).__name__}: {e}")

    # ---- (c) Role separation — drawdown gating happens post-fusion only ----
    try:
        cfg_low_dd = OrchestratorConfig(
            signal_weights={"signal_engine": 1.0},
            max_drawdown_pct=0.15,
        )
        orch_role = AlphaOrchestrator(cfg_low_dd)
        sigs_role = [AlphaSignal("signal_engine", 1, 0.9, 15.0, now, "1m", "directional")]
        # Healthy DD → BUY expected
        r_ok = orch_role.orchestrate(
            sigs_role, regime_d, quality_d,
            ExecutionState(1000.0, 50000.0, 0.02),
            current_time=now,
        )
        # DD over the configured cap → must HOLD (risk overlay killed it),
        # but per the FIX 22 contract _fuse_signals itself must not have
        # gated on DD; net_conviction at fusion-time should still be positive
        # in the meta_info if exposed, and the action should be HOLD.
        r_blocked = orch_role.orchestrate(
            sigs_role, regime_d, quality_d,
            ExecutionState(1000.0, 50000.0, 0.30),  # 30% DD ≫ 15% cap
            current_time=now,
        )
        assert_check("orchestrator_deep", "role_sep_healthy_dd_buys",
                     r_ok.action.name == "BUY",
                     f"action={r_ok.action.name} (expected BUY at 2% DD)")
        assert_check("orchestrator_deep", "role_sep_overcap_dd_holds",
                     r_blocked.action.name == "HOLD",
                     f"action={r_blocked.action.name} (expected HOLD at 30% DD over 15% cap)")
    except Exception as e:
        record("orchestrator_deep", "role_separation", "FAIL",
               f"{type(e).__name__}: {e}")

    # ---- (d) Hierarchy enforcement — higher_tf_dominance changes resolution ----
    try:
        cfg_dom = OrchestratorConfig(
            signal_weights={"signal_engine": 1.0, "liquidity_sweep": 1.0},
            timeframe_weights={"1m": 1.0, "5m": 0.8, "15m": 0.6, "default": 0.5},
            timeframe_order=["1m", "5m", "15m", "default"],
            higher_tf_dominance=True,
        )
        cfg_no_dom = OrchestratorConfig(
            signal_weights={"signal_engine": 1.0, "liquidity_sweep": 1.0},
            timeframe_weights={"1m": 1.0, "5m": 0.8, "15m": 0.6, "default": 0.5},
            timeframe_order=["1m", "5m", "15m", "default"],
            higher_tf_dominance=False,
        )
        # 1m says BUY at 0.9, 15m says SELL at 0.6.
        sigs_h = [
            AlphaSignal("signal_engine",   1, 0.9, 15.0, now, "1m",  "directional"),
            AlphaSignal("liquidity_sweep", -1, 0.6, 10.0, now, "15m", "directional"),
        ]
        r_dom = AlphaOrchestrator(cfg_dom).orchestrate(
            sigs_h, regime_d, quality_d, exec_state_d, current_time=now,
        )
        r_nodom = AlphaOrchestrator(cfg_no_dom).orchestrate(
            sigs_h, regime_d, quality_d, exec_state_d, current_time=now,
        )
        # The two configurations must produce a measurable difference —
        # either different action, or different conviction magnitude.
        differs = (r_dom.action.name != r_nodom.action.name or
                   abs(r_dom.net_conviction - r_nodom.net_conviction) > 1e-6)
        assert_check("orchestrator_deep", "hierarchy_dom_changes_outcome",
                     differs,
                     f"dom→{r_dom.action.name}({r_dom.net_conviction:+.4f}) vs "
                     f"no_dom→{r_nodom.action.name}({r_nodom.net_conviction:+.4f})")
        # Specifically: with higher_tf_dominance, the higher-TF SELL should
        # win the resolution. Without dominance, the conflicting signals
        # should approximately cancel (HOLD or much weaker conviction).
        biased = (r_dom.action.name == "SELL"
                  and r_nodom.action.name in ("HOLD",)
                  or (r_dom.action.name == r_nodom.action.name == "SELL"
                      and r_dom.net_conviction >= r_nodom.net_conviction))
        assert_check("orchestrator_deep", "hierarchy_dom_biases_to_higher_tf",
                     biased,
                     f"dom→{r_dom.action.name}({r_dom.net_conviction:+.4f}) "
                     f"vs no_dom→{r_nodom.action.name}({r_nodom.net_conviction:+.4f})")
    except Exception as e:
        record("orchestrator_deep", "hierarchy_enforcement", "FAIL",
               f"{type(e).__name__}: {e}")

    # ---- (e) Source allow-list — unknown source rejected when configured ----
    try:
        cfg_strict = OrchestratorConfig(
            signal_weights={"signal_engine": 1.0},
            allow_unknown_sources=False,
        )
        orch_strict = AlphaOrchestrator(cfg_strict)
        sigs_unknown = [AlphaSignal(
            "rogue_source_id", 1, 0.9, 15.0, now, "1m", "directional",
        )]
        r_unk = orch_strict.orchestrate(
            sigs_unknown, regime_d, quality_d, exec_state_d, current_time=now,
        )
        assert_check("orchestrator_deep", "unknown_source_rejected_strict",
                     r_unk.action.name == "HOLD",
                     f"unknown source action={r_unk.action.name} (expected HOLD)")
    except Exception as e:
        record("orchestrator_deep", "unknown_source_rejected_strict", "FAIL",
               f"{type(e).__name__}: {e}")

    # ---- (f) Quality gate — missing-data ratio over cap blocks action ----
    try:
        cfg_q = OrchestratorConfig(
            signal_weights={"signal_engine": 1.0},
            max_missing_data_ratio=0.10,
        )
        orch_q = AlphaOrchestrator(cfg_q)
        sigs_q = [AlphaSignal("signal_engine", 1, 0.9, 15.0, now, "1m", "directional")]
        r_q_bad = orch_q.orchestrate(
            sigs_q, regime_d, FeatureQuality(0.05, 0.50),  # 50% missing >> 10% cap
            exec_state_d, current_time=now,
        )
        assert_check("orchestrator_deep", "quality_gate_blocks_high_missing",
                     r_q_bad.action.name == "HOLD",
                     f"high-missing-data action={r_q_bad.action.name} (expected HOLD)")
    except Exception as e:
        record("orchestrator_deep", "quality_gate_blocks_high_missing", "FAIL",
               f"{type(e).__name__}: {e}")
else:
    record("orchestrator_deep", "all", "SKIP", "orchestrator unavailable")


# ---------------------------------------------------------------
# STEP 4: ADVANCED REGIME ENGINE
# ---------------------------------------------------------------
section("STEP 4: ADVANCED REGIME ENGINE")

engine = None
weights_path = os.environ["REGIME_WEIGHT_PATH"]
weights_present = os.path.exists(weights_path)
record("regime", "weights_file_present", "INFO" if weights_present else "WARN",
       f"path={weights_path} present={weights_present}")

try:
    from advanced_regime_engine import AdvancedRegimeEngine
    engine = AdvancedRegimeEngine(n_states=3, n_features=3, target_vol=0.02)
    record("regime", "construct", "PASS", "AdvancedRegimeEngine(3,3,0.02)")
except Exception as e:
    record("regime", "construct", "FAIL",
           f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")
    engine = None

regime_outputs: List[Dict[str, Any]] = []
if engine is None:
    record("regime", "ticks", "SKIP", "engine construction failed; ticks skipped")
if engine is not None:
    try:
        bid_vol = sum(float(b[1]) for b in orderbook["bids"][:10])
        ask_vol = sum(float(a[1]) for a in orderbook["asks"][:10])
        total_depth = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total_depth if total_depth > 0 else 0.0
        trade_vol = sum(float(t.get("amount", 0)) * float(t.get("price", 0)) for t in trades)
        current_price = float(ohlcv_1m[-1][4])
        prev_price = float(ohlcv_1m[-2][4])
        log_return = float(np.log(current_price / prev_price)) if prev_price > 0 else 0.0
        features = np.array([imbalance, bid_vol, trade_vol], dtype=float)
        market_data = {
            "return": log_return,
            "features": features,
            "price": current_price,
            "require_calibration": False,
            "require_microstructure": False,
        }
        # Required output keys per AdvancedRegimeEngine schema contract.
        REQUIRED_KEYS = (
            "schema_version", "regime_idx", "regime_label",
            "probabilities", "risk_metrics", "alpha", "conviction",
            "signal_valid", "engine_status",
        )
        for i in range(5):
            try:
                out = engine.update(market_data)
                regime_outputs.append(out)
                detail = (f"label={out.get('regime_label')} "
                          f"conf={out.get('confidence', 0):.4f} "
                          f"valid={out.get('signal_valid')} "
                          f"status={out.get('engine_status')}")
                # Assertion 1: every required key is present.
                missing = [k for k in REQUIRED_KEYS if k not in out]
                assert_check("regime", f"tick_{i}_schema", not missing,
                             f"{detail} | missing_keys={missing}")
                # Assertion 2: when weights are absent the engine MUST fail
                # closed (signal_valid=False) per the documented contract.
                if not weights_present:
                    assert_check("regime", f"tick_{i}_failclosed",
                                 out.get("signal_valid") is False,
                                 f"{detail} | uncalibrated → expected signal_valid=False")
                # Assertion 3: probabilities is a 3-key dict summing to ~1.
                probs = out.get("probabilities", {})
                psum = sum(float(probs.get(k, 0.0)) for k in ("bull", "bear", "crisis"))
                assert_check("regime", f"tick_{i}_probsum",
                             abs(psum - 1.0) < 1e-3,
                             f"sum(bull,bear,crisis)={psum:.6f}")
            except Exception as e:
                record("regime", f"tick_{i}", "FAIL", f"{type(e).__name__}: {e}")
    except Exception as e:
        record("regime", "feature_build", "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------
# STEP 5: LIQUIDITY SWEEP ALPHA
# ---------------------------------------------------------------
section("STEP 5: LIQUIDITY SWEEP ALPHA")

alpha_signals: List[Dict[str, Any]] = []
try:
    from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha
    alpha_pred = LiquiditySweepAlpha(depth_levels=10, history_window=100)
    record("alpha_sweep", "construct", "PASS", "LiquiditySweepAlpha(10,100)")
except Exception as e:
    record("alpha_sweep", "construct", "FAIL", f"{type(e).__name__}: {e}")
    alpha_pred = None

if alpha_pred is not None:
    try:
        recent_highs = [float(c[2]) for c in ohlcv_1m[-20:]]
        recent_lows  = [float(c[3]) for c in ohlcv_1m[-20:]]
        alpha_pred.update_liquidity_pools(recent_highs, recent_lows)
        record("alpha_sweep", "pool_seed", "PASS",
               f"high={alpha_pred.liquidity_pools.get('high')} low={alpha_pred.liquidity_pools.get('low')}")
    except Exception as e:
        record("alpha_sweep", "pool_seed", "FAIL", f"{type(e).__name__}: {e}")

    try:
        current_price = float(ohlcv_1m[-1][4])
        atr = float(np.mean([abs(ohlcv_1m[i][2] - ohlcv_1m[i][3]) for i in range(-14, 0)]))
        market_data_alpha = {
            "price": current_price,
            "close_price": current_price,
            "prev_book": orderbook,
            "curr_book": orderbook,
            "timestamp": time.time(),
            "trades_count": len(trades),
            "pre_sweep_depth": sum(float(b[1]) for b in orderbook["bids"][:5]),
            "curr_depth": sum(float(b[1]) for b in orderbook["bids"][:5]),
            "sweep_time_elapsed": 0.5,
            "atr": atr,
            "ema_fast": float(np.mean([c[4] for c in ohlcv_1m[-10:]])),
            "ema_slow": float(np.mean([c[4] for c in ohlcv_1m[-20:]])),
        }
        for i in range(3):
            try:
                signal = alpha_pred.get_signal(market_data_alpha)
                alpha_signals.append(signal)
                pa = signal.get("prob_above") or signal.get("prob_above_pool")
                pb = signal.get("prob_below") or signal.get("prob_below_pool")
                record("alpha_sweep", f"tick_{i}", "PASS",
                       f"action={signal.get('action')} conf={float(signal.get('confidence',0)):.4f} prob_above={pa} prob_below={pb}")
            except Exception as e:
                record("alpha_sweep", f"tick_{i}", "FAIL", f"{type(e).__name__}: {e}")
    except Exception as e:
        record("alpha_sweep", "build_market_data", "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------
# STEP 6: SIGNAL ENGINE
# ---------------------------------------------------------------
section("STEP 6: SIGNAL ENGINE")

try:
    from signal_engine import SignalEngine
    se = SignalEngine()
    record("signal", "construct", "PASS", "SignalEngine()")
except Exception as e:
    record("signal", "construct", "FAIL", f"{type(e).__name__}: {e}")
    se = None

if se is None:
    record("signal", "generate", "SKIP", "construction failed")
if se is not None:
    try:
        atr = float(np.mean([abs(ohlcv_1m[i][2] - ohlcv_1m[i][3]) for i in range(-14, 0)]))
        features_live = {
            "candles": ohlcv_1m[-20:],
            "volume": float(ohlcv_1m[-1][5]),
            "price": float(ohlcv_1m[-1][4]),
            "close": float(ohlcv_1m[-1][4]),
            "atr": atr,
            "regime": {"type": "trend"},
            "liquidity_score": 0.75,
            "spread_bps": 5.0,
            "latency_ms": 10.0,
            "staleness_ratio": 0.02,
            "missing_data_ratio": 0.01,
            "alpha": {"direction": "LONG", "confidence": 0.72, "prob_above": 0.65, "prob_below": 0.35},
            "confluence_score": 6.5,
            "institutional_score": 7.0,
            "stop_hunt_detected": False,
            "ofi_zscore": 1.2,
            "flow_imbalance": 0.3,
            "hawkes_intensity": 0.4,
        }
        sig_out = se.generate(features_live)
        # FIX B1 (revised): assert the CANONICAL action field against
        # BUY/SELL/HOLD. The legacy `signal` field stays LONG/SHORT/HOLD
        # for execution.py / backtest_engine.py back-compat; the new
        # `action` field is the cross-module contract.
        sig_val = sig_out.get("action", sig_out.get("signal"))
        conf_val = float(sig_out.get("confidence", 0))
        # Schema assertions.
        assert_check("signal", "generate_schema",
                     all(k in sig_out for k in ("signal", "action", "confidence", "reason")),
                     f"keys={sorted(sig_out.keys())[:8]}…")
        accepted_vocab = ("BUY", "SELL", "HOLD")
        assert_check("signal", "generate_signal_domain",
                     sig_val in accepted_vocab,
                     f"action={sig_val} (must be one of BUY/SELL/HOLD)")
        assert_check("signal", "generate_conf_bounds",
                     0.0 <= conf_val <= 1.0,
                     f"confidence={conf_val:.4f} ∈ [0, 1]")
        record("signal", "generate", "PASS",
               f"signal={sig_val} conf={conf_val:.4f} reason={sig_out.get('reason')}")
    except Exception as e:
        record("signal", "generate", "FAIL",
               f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")


# ---------------------------------------------------------------
# STEP 7: BACKTEST WITH LIVE-FETCHED CANDLES
# ---------------------------------------------------------------
section("STEP 7: BACKTEST ENGINE")

bt_summary: Dict[str, Any] = {}
try:
    from backtest_engine import BacktestEngine, BacktestConfig
    bt_config = BacktestConfig(
        fee_bps=8.0,
        slippage_bps=3.0,
        max_hold_bars=12,
        initial_balance=10_000.0,
        basis_mode="none",
    )
    bt = BacktestEngine(config=bt_config)
    record("backtest", "construct", "PASS",
           f"BacktestEngine ready, candles={len(ohlcv_1m)}")
except Exception as e:
    record("backtest", "construct", "FAIL", f"{type(e).__name__}: {e}")
    bt = None

if bt is None:
    record("backtest", "run", "SKIP", "construction failed")
if bt is not None:
    try:
        t0 = time.time()
        bt_result = bt.run_backtest(ohlcv_1m, initial_balance=10_000.0)
        elapsed = time.time() - t0
        bt_summary = bt_result
        # Schema assertions on the result dict.
        assert_check("backtest", "run_schema",
                     all(k in bt_result for k in
                         ("total_trades", "win_rate", "pnl", "max_drawdown", "sharpe")),
                     f"keys={sorted(bt_result.keys())[:8]}…")
        # Domain assertions.
        wr = float(bt_result.get("win_rate", 0))
        dd = float(bt_result.get("max_drawdown", 0))
        assert_check("backtest", "run_winrate_domain",
                     0.0 <= wr <= 1.0, f"win_rate={wr:.4f} ∈ [0, 1]")
        assert_check("backtest", "run_drawdown_nonneg",
                     dd >= 0.0, f"max_drawdown={dd:.4f} ≥ 0")
        record("backtest", "run", "PASS",
               f"trades={bt_result.get('total_trades')} pnl=${bt_result.get('pnl', 0):.2f} "
               f"win_rate={wr*100:.2f}% sharpe={bt_result.get('sharpe', 0):.4f} "
               f"max_dd={dd*100:.2f}% expectancy={bt_result.get('expectancy', 0):.4f} "
               f"elapsed={elapsed:.2f}s")
        if bt_result.get("total_trades", 0) == 0:
            record("backtest", "signal_coverage", "WARN",
                   "Backtest produced 0 trades — signal coverage gap")
    except Exception as e:
        record("backtest", "run", "FAIL",
               f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")


# ---------------------------------------------------------------
# STEP 8: SCHEMA VALIDATION
# ---------------------------------------------------------------
section("STEP 8: SCHEMA VALIDATION (advanced_regime_engine)")

schema_results: List[Tuple[str, bool, str]] = []
try:
    from advanced_regime_engine import _build_output, _validate_output_schema

    base = _build_output(
        regime_idx=0,
        regime_label="TREND",
        trend_strength=0.45,
        risk_level=0.2,
        confidence=0.75,
        conviction=0.68,
        edge_score=0.62,
        probabilities={"bull": 0.6, "bear": 0.25, "crisis": 0.15},
        macro_probs=[0.6, 0.25, 0.15],
        position_size=0.18,
        expected_vol=0.015,
        raw_size=1.2,
        is_toxic=False,
        garch_regime_probs=[0.7, 0.3],
        feed_status={"primary": "OK", "flags": []},
        engine_status="OK",
        signed_position_size=0.18,
        last_valid_vol=0.015,
        switch_stability_ema=0.85,
        execution_mode="trend_follow",
        execution_side="long",
        signal_valid=True,
    )
    valid_base = _validate_output_schema(base)
    schema_results.append(("baseline_valid_output", valid_base, ""))
    record("schema", "baseline_valid_output", "PASS" if valid_base else "FAIL",
           f"_validate_output_schema(base)={valid_base}")

    edge_cases = [
        # The user's spec notes: each case mutates the base output and we expect rejection.
        ("negative_position",
         {"position_size": -0.1, "signed_position_size": -0.1}),
        ("nan_confidence",
         {"confidence": float("nan")}),
        ("prob_sum_wrong",
         {"probabilities": {"bull": 0.9, "bear": 0.5, "crisis": 0.1}}),
        ("crisis_prob_out_of_bounds",
         {"probabilities": {"bull": -0.1, "bear": 0.5, "crisis": 0.6}}),
    ]
    for label, mutation in edge_cases:
        bad = dict(base)
        bad.update(mutation)
        try:
            r = _validate_output_schema(bad)
            schema_results.append((label, r, ""))
            expected_reject = True  # all edge cases SHOULD be rejected
            status = "PASS" if (r is False) == expected_reject else "FAIL"
            record("schema", f"edge_{label}", status,
                   f"_validate_output_schema={r} (expected False={expected_reject})")
        except Exception as e:
            schema_results.append((label, False, f"{type(e).__name__}: {e}"))
            record("schema", f"edge_{label}", "WARN",
                   f"raised {type(e).__name__}: {e}")
except Exception as e:
    record("schema", "import", "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------
# STEP 9: THREAD SAFETY STRESS
# ---------------------------------------------------------------
section("STEP 9: THREAD SAFETY / CONCURRENT STRESS")

if AlphaOrchestrator is not None and orchestrator is not None:
    try:
        orchestrator_shared = AlphaOrchestrator(config)
        regime_t = RegimeContext("trending", 0.3, 0.7)
        quality_t = FeatureQuality(0.05, 0.02)
        exec_state_t = ExecutionState(1000.0, 50000.0, 0.05)
        errors: List[Tuple[int, int, str]] = []
        results_t: List[Tuple[int, int, str]] = []
        lock = threading.Lock()

        def stress_worker(worker_id: int) -> None:
            for i in range(10):
                try:
                    now_w = time.time()
                    sigs = [AlphaSignal(
                        "signal_engine",
                        1 if i % 2 == 0 else -1,
                        0.7, 10.0, now_w, "1m", "dir",
                    )]
                    r = orchestrator_shared.orchestrate(
                        sigs, regime_t, quality_t, exec_state_t,
                        current_time=now_w,
                    )
                    with lock:
                        results_t.append((worker_id, i, r.action.name))
                except Exception as e:
                    with lock:
                        errors.append((worker_id, i, f"{type(e).__name__}: {e}"))

        threads = [threading.Thread(target=stress_worker, args=(t,))
                   for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Strict assertion: exactly 50 successes, 0 errors.
        assert_check("threading", "stress_50",
                     len(results_t) == 50 and len(errors) == 0,
                     f"successes={len(results_t)}/50 errors={len(errors)}")
        # Distribution sanity: each worker should have completed all 10 calls.
        per_worker = {w: 0 for w in range(5)}
        for w, _, _ in results_t:
            per_worker[w] = per_worker.get(w, 0) + 1
        assert_check("threading", "per_worker_distribution",
                     all(c == 10 for c in per_worker.values()),
                     f"per_worker={per_worker} (expected 10/each)")
        for err in errors[:5]:
            record("threading", f"err_w{err[0]}_t{err[1]}", "FAIL", err[2])
    except Exception as e:
        record("threading", "stress_setup", "FAIL", f"{type(e).__name__}: {e}")
else:
    record("threading", "stress_50", "SKIP", "orchestrator unavailable")


# ---------------------------------------------------------------
# STEP 10: PERFORMANCE FEEDBACK LOOP
# ---------------------------------------------------------------
section("STEP 10: PERFORMANCE FEEDBACK LOOP")

if AlphaOrchestrator is not None:
    try:
        orchestrator_fb = AlphaOrchestrator(OrchestratorConfig(
            signal_weights={"signal_engine": 1.0},
            feedback_enabled=True,
            feedback_min_trades=3,
        ))
        random.seed(42)
        fb_failures = 0
        for i in range(20):
            pnl = random.uniform(-50, 100)
            try:
                orchestrator_fb.update_performance({
                    "source_id": "signal_engine",
                    "realized_pnl": pnl,
                    "realized_edge_bps": pnl / 10.0,
                    "expected_edge_bps": 8.0,
                    "expected_win_rate": 0.55,
                    "event_time": time.time() + i,
                })
            except Exception as e:
                fb_failures += 1
                record("feedback", f"update_{i}", "FAIL", f"{type(e).__name__}: {e}")
        assert_check("feedback", "updates_total",
                     fb_failures == 0,
                     f"submitted=20 failures={fb_failures}")
        try:
            stats = orchestrator_fb.performance_stats
            assert_check("feedback", "stats_source_present",
                         "signal_engine" in stats,
                         f"sources={list(stats.keys())}")
            for src, s in stats.items():
                assert_check("feedback", f"stats_{src}_trade_count",
                             s.trade_count == 20,
                             f"trade_count={s.trade_count} (expected 20)")
                # Multiplier must remain inside the documented [0.5, 1.5] band.
                assert_check("feedback", f"stats_{src}_mult_bounds",
                             0.5 <= s.current_multiplier <= 1.5,
                             f"mult={s.current_multiplier:.4f} ∈ [0.5, 1.5]")
                # Win rate must be in [0, 1].
                assert_check("feedback", f"stats_{src}_winrate_bounds",
                             0.0 <= s.win_rate <= 1.0,
                             f"win_rate={s.win_rate:.4f} ∈ [0, 1]")
                record("feedback", f"stats_{src}", "INFO",
                       f"trades={s.trade_count} win_rate={s.win_rate:.3f} "
                       f"ema_wr={s.ema_win_rate:.3f} mult={s.current_multiplier:.3f}")
        except Exception as e:
            record("feedback", "stats_read", "FAIL", f"{type(e).__name__}: {e}")
    except Exception as e:
        record("feedback", "setup", "FAIL", f"{type(e).__name__}: {e}")
else:
    record("feedback", "setup", "SKIP", "orchestrator unavailable")


# ---------------------------------------------------------------
# STEP 11: AUDIT REPORT
# ---------------------------------------------------------------
section("STEP 11: AUDIT REPORT SUMMARY")


def by_status(s: str) -> List[Dict[str, Any]]:
    return [f for f in FINDINGS if f["status"] == s]


fails = by_status("FAIL")
warns = by_status("WARN")
passes = by_status("PASS")
infos = by_status("INFO")
skips = by_status("SKIP")

print(f"\nTotal checks: {len(FINDINGS)}  PASS={len(passes)}  WARN={len(warns)}  "
      f"FAIL={len(fails)}  SKIP={len(skips)}  INFO={len(infos)}\n")

if skips:
    print("--- SKIPPED (coverage gaps) ---")
    for f in skips:
        print(f"  • [{f['category']}] {f['name']}: {f['detail']}")

if fails:
    print("--- FAILURES ---")
    for f in fails:
        print(f"  • [{f['category']}] {f['name']}: {f['detail']}")

if warns:
    print("\n--- WARNINGS ---")
    for f in warns:
        print(f"  • [{f['category']}] {f['name']}: {f['detail']}")


report_path = "AUDIT_RUNNER_REPORT.json"
try:
    with open(report_path, "w") as f:
        json.dump({
            "data_exchange": data_exchange_used,
            "totals": {
                "checks": len(FINDINGS),
                "pass": len(passes),
                "warn": len(warns),
                "fail": len(fails),
                "skip": len(skips),
                "info": len(infos),
            },
            "findings": FINDINGS,
            "orchestrator_results": orch_results,
            "regime_outputs_count": len(regime_outputs),
            "alpha_signals_count": len(alpha_signals),
            "backtest_summary": {
                k: bt_summary.get(k) for k in
                ("total_trades", "win_rate", "pnl", "max_drawdown", "sharpe", "expectancy")
            } if bt_summary else None,
        }, f, indent=2, default=str)
    print(f"\nMachine-readable report written to {report_path}")
except Exception as e:
    print(f"\n[WARN] could not write {report_path}: {e}")

print("\nAudit run complete.")

# FIX C2: strict-mode exit code for CI gating.
if _args.strict and (len(fails) > 0 or len(skips) > 0):
    print(f"[STRICT] Exiting non-zero: fails={len(fails)} skips={len(skips)}")
    sys.exit(1)
