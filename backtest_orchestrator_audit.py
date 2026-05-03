"""
AlphaOrchestrator production-readiness audit harness.
Runs against the same real Dec 2023 BTCUSDT data already in the repo.
Read-only: does NOT call execution.py, does NOT modify alpha_orchestrator.py.

Outputs:
  audit_orchestrator_output/orchestrator_audit.json   (all metrics)
  audit_orchestrator_output/orchestrator_records.csv  (per-bar trace)
  audit_orchestrator_output/orchestrator_trades.csv   (per-trade log)
  audit_orchestrator_output/adversarial_results.json  (TEST-1..TEST-20)
  audit_orchestrator_output/schema_audit.json         (key-presence audit)
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import statistics
import threading
import traceback
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple, Optional

logging.basicConfig(level=logging.WARNING)

OUT = "audit_orchestrator_output"
os.makedirs(OUT, exist_ok=True)

from alpha_orchestrator import (  # noqa: E402
    AlphaOrchestrator, AlphaSignal, OrchestratorConfig,
    RegimeContext, FeatureQuality, ExecutionState, Action,
    OrchestratedAction,
)

REQUIRED_META_KEYS = [
    "orchestration_ts", "metrics", "rejection_details", "fusion_stats",
    "alpha_performance", "rejection_telemetry", "environmental_context",
    "decision_telemetry", "source_policy_summary", "signal_metrics",
    "per_signal_breakdown", "timeframe_breakdown", "agreement_ratio",
    "conflict_ratio", "dominant_timeframe", "final_conviction",
    "risk_metrics", "quality_metrics",
]

# ---------- load data ----------
ohlcv: List[List[float]] = []
with open("data/ohlcv_1m.csv") as fh:
    for row in csv.DictReader(fh):
        ohlcv.append([
            int(row["timestamp_ms"]), float(row["open"]), float(row["high"]),
            float(row["low"]), float(row["close"]), float(row["volume"]),
        ])
print(f"[PHASE-3][STEP-3.1] loaded {len(ohlcv)} 1m bars  "
      f"price=[{min(b[4] for b in ohlcv):.2f},{max(b[4] for b in ohlcv):.2f}]  "
      f"ts=[{ohlcv[0][0]},{ohlcv[-1][0]}]")

# ---------- helpers (Phase 3.2) ----------
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def momentum_signal(prev_close: float, curr_close: float, atr: float) -> Tuple[int, float]:
    if atr <= 0 or prev_close <= 0:
        return 0, 0.0
    z = (curr_close - prev_close) / atr
    direction = 1 if z > 0.05 else (-1 if z < -0.05 else 0)
    conv = clamp(abs(z) / 2.0, 0.01, 0.99)
    return direction, conv

def ofi_proxy_signal(curr: List[float], prev: List[float]) -> Tuple[int, float]:
    body = curr[4] - curr[1]
    rng = max(curr[2] - curr[3], 1e-9)
    direction = 1 if body > 0 else (-1 if body < 0 else 0)
    conv = clamp(abs(body) / rng * (curr[5] / max(prev[5], 1.0)) * 0.4, 0.01, 0.99)
    return direction, conv

def build_alpha_signals(curr: List[float], prev: List[float], atr: float, ts_s: float) -> List[AlphaSignal]:
    md, mc = momentum_signal(prev[4], curr[4], atr)
    od, oc = ofi_proxy_signal(curr, prev)
    return [
        AlphaSignal(
            source_id="signal_engine", direction=md, conviction=mc,
            expected_edge_bps=abs(mc * 25.0), timestamp=ts_s, timeframe="1m",
        ),
        AlphaSignal(
            source_id="liquidity_sweep_alpha", direction=od, conviction=oc,
            expected_edge_bps=abs(oc * 25.0), timestamp=ts_s, timeframe="5m",
        ),
    ]

def build_regime_context(window: List[List[float]]) -> RegimeContext:
    if len(window) < 5:
        return RegimeContext("unknown", 0.3, 0.5)
    closes = [b[4] for b in window]
    mean_c = sum(closes) / len(closes)
    var = sum((c - mean_c) ** 2 for c in closes) / len(closes)
    sd = math.sqrt(var)
    vol = clamp(sd / max(mean_c, 1.0) * 200.0, 0.0, 1.0)
    vols = [b[5] for b in window]
    avg_vol = sum(vols) / len(vols)
    liq = clamp(avg_vol / max(max(vols), 1.0), 0.05, 1.0)
    if vol > 0.6:
        regime = "toxic"
    elif vol > 0.35:
        regime = "trend"
    else:
        regime = "range"
    return RegimeContext(regime, vol, liq)

def build_exec_state(balance: float, drawdown: float) -> ExecutionState:
    return ExecutionState(
        current_exposure_usd=0.0,
        max_exposure_usd=max(balance * 10.0, 1.0),
        current_drawdown_pct=clamp(drawdown, 0.0, 1.0),
    )

# ATR
def atr_series(bars: List[List[float]], period: int = 14) -> List[float]:
    tr: List[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b[2] - b[3])
        else:
            pc = bars[i - 1][4]
            tr.append(max(b[2] - b[3], abs(b[2] - pc), abs(b[3] - pc)))
    out: List[float] = []
    s = 0.0
    for i, t in enumerate(tr):
        if i < period:
            s += t
            out.append(s / (i + 1))
        else:
            out.append((out[-1] * (period - 1) + t) / period)
    return out

atr_arr = atr_series(ohlcv, 14)

# ---------- orchestrator config ----------
config = OrchestratorConfig(
    signal_weights={"signal_engine": 0.50, "liquidity_sweep_alpha": 0.50},
    timeframe_weights={"1m": 0.4, "5m": 0.6, "default": 1.0},
    timeframe_order=["1m", "5m", "default"],
    higher_tf_dominance=False,
    action_threshold=0.30,
    signal_ttl_seconds=60.0,
    feedback_enabled=True,
    feedback_min_trades=5,
    max_missing_data_ratio=0.5,
    allow_unknown_sources=False,
)
orch = AlphaOrchestrator(config)
print(f"[PHASE-3][STEP-3.2] OrchestratorConfig + AlphaOrchestrator constructed.")

# ---------- main loop (Phase 3.3) ----------
WARMUP = 30
HORIZON = 12
FEE_BPS = 8
SLIP_BPS = 3
START_EQUITY = 10_000.0

records: List[Dict[str, Any]] = []
trades: List[Dict[str, Any]] = []
action_ctr: Counter = Counter()
rationale_ctr: Counter = Counter()
regime_ctr: Counter = Counter()
metrics_acc: Counter = Counter()  # accepted/stale/invalid/future_timestamp/negative_edge_normalized/duplicates_removed
schema_violations: Dict[str, int] = defaultdict(int)
schema_sample_paths: Dict[str, List[str]] = defaultdict(list)

balance = START_EQUITY
peak_equity = START_EQUITY
max_dd_observed = 0.0
current_drawdown = 0.0
position: Optional[Dict[str, Any]] = None
update_perf_calls = 0
exception_count = 0

agreement_acc: List[float] = []
conflict_acc: List[float] = []
conviction_acc: List[float] = []
urgency_acc: List[float] = []
edge_acc: List[float] = []
dominant_tf_ctr: Counter = Counter()
conviction_buckets = [0, 0, 0, 0, 0]  # [0-.2, .2-.4, .4-.6, .6-.8, .8-1.0]

start = time.time()
for i in range(WARMUP, len(ohlcv)):
    bar = ohlcv[i]
    prev_bar = ohlcv[i - 1]
    ts_s = bar[0] / 1000.0
    # validate ts > 1e9
    if ts_s < 1e9:
        continue

    sigs = build_alpha_signals(bar, prev_bar, atr_arr[i], ts_s)
    rctx = build_regime_context(ohlcv[max(0, i - 20):i])
    exst = build_exec_state(balance, current_drawdown)
    fq = FeatureQuality(staleness_ratio=0.0, missing_data_ratio=0.0)

    try:
        result: OrchestratedAction = orch.orchestrate(
            signals=sigs, regime=rctx, feature_quality=fq,
            exec_state=exst, current_time=ts_s,
        )
    except Exception as exc:
        exception_count += 1
        if exception_count <= 3:
            traceback.print_exc()
        continue

    meta = result.meta_info or {}
    rationale = meta.get("rationale", "unknown")
    action_ctr[result.action.name] += 1
    rationale_ctr[rationale] += 1
    regime_ctr[rctx.regime_name] += 1
    conviction_acc.append(result.net_conviction)
    urgency_acc.append(result.urgency)
    edge_acc.append(result.expected_edge_bps)
    if meta.get("dominant_timeframe"):
        dominant_tf_ctr[meta["dominant_timeframe"]] += 1
    agreement_acc.append(meta.get("agreement_ratio", 0.0))
    conflict_acc.append(meta.get("conflict_ratio", 0.0))

    # conviction histogram
    nc = result.net_conviction
    if nc < 0.2:
        conviction_buckets[0] += 1
    elif nc < 0.4:
        conviction_buckets[1] += 1
    elif nc < 0.6:
        conviction_buckets[2] += 1
    elif nc < 0.8:
        conviction_buckets[3] += 1
    else:
        conviction_buckets[4] += 1

    # accumulate per-call metrics
    m = meta.get("metrics", {})
    for k in ("accepted", "stale", "invalid", "future_timestamp",
              "negative_edge_normalized", "duplicates_removed"):
        metrics_acc[k] += int(m.get(k, 0) or 0)

    # schema audit on EVERY bar (cheap)
    for k in REQUIRED_META_KEYS:
        if k not in meta:
            schema_violations[k] += 1
            if len(schema_sample_paths[k]) < 5:
                schema_sample_paths[k].append(rationale)

    records.append({
        "i": i, "ts_ms": bar[0], "close": bar[4],
        "action": result.action.name, "conviction": result.net_conviction,
        "urgency": result.urgency, "edge_bps": result.expected_edge_bps,
        "rationale": rationale, "regime": rctx.regime_name,
        "dominant_tf": meta.get("dominant_timeframe", ""),
        "agreement": meta.get("agreement_ratio", 0.0),
        "conflict": meta.get("conflict_ratio", 0.0),
    })

    # ---- single-position trade simulation ----
    if position is None and result.action in (Action.BUY, Action.SELL):
        direction = 1 if result.action == Action.BUY else -1
        entry_p = bar[4] * (1 + direction * SLIP_BPS / 1e4)
        position = {
            "entry_i": i, "entry_p": entry_p, "direction": direction,
            "entry_ts": ts_s, "entry_conviction": result.net_conviction,
            "entry_regime": rctx.regime_name,
        }
    elif position is not None:
        held = i - position["entry_i"]
        # exit on horizon, opposite signal, or last bar
        opp = (
            (position["direction"] == 1 and result.action == Action.SELL) or
            (position["direction"] == -1 and result.action == Action.BUY)
        )
        if held >= HORIZON or opp or i == len(ohlcv) - 1:
            exit_p = bar[4] * (1 - position["direction"] * SLIP_BPS / 1e4)
            gross = position["direction"] * (exit_p - position["entry_p"]) / position["entry_p"]
            net = gross - 2 * FEE_BPS / 1e4
            pnl_amount = balance * net
            balance += pnl_amount
            peak_equity = max(peak_equity, balance)
            current_drawdown = (peak_equity - balance) / peak_equity if peak_equity > 0 else 0.0
            max_dd_observed = max(max_dd_observed, current_drawdown)
            trades.append({
                "entry_i": position["entry_i"], "exit_i": i,
                "direction": position["direction"],
                "entry_p": position["entry_p"], "exit_p": exit_p,
                "gross": gross, "net": net, "pnl_amount": pnl_amount,
                "held_bars": held, "balance_after": balance,
                "entry_conviction": position["entry_conviction"],
                "regime": position["entry_regime"],
            })
            try:
                orch.update_performance(
                    {
                        "source_id": "signal_engine",
                        "realized_pnl": pnl_amount,
                        "realized_edge_bps": net * 1e4,
                        "expected_edge_bps": abs(result.expected_edge_bps),
                        "expected_win_rate": 0.52,
                    },
                    feature_quality=fq,
                    regime=rctx,
                    event_time=ts_s,
                )
                update_perf_calls += 1
            except Exception as exc:
                if update_perf_calls < 3:
                    print(f"[update_performance] {type(exc).__name__}: {exc}")
            position = None

elapsed = time.time() - start
print(f"[PHASE-3][STEP-3.3] loop done in {elapsed:.1f}s  bars_processed={len(records)}  "
      f"trades={len(trades)}  exceptions={exception_count}")

# ---------- Phase 3.4: metrics ----------
def safe_mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0

def safe_std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    return statistics.stdev(xs)

total_bars = len(records)
nets = [t["net"] for t in trades]
wins = [n for n in nets if n > 0]
losses = [n for n in nets if n <= 0]

# Sharpe / Sortino / Calmar
if len(nets) > 1:
    m_ret = sum(nets) / len(nets)
    sd_ret = statistics.stdev(nets)
    sharpe = (m_ret / sd_ret) * math.sqrt(252 * 24 * 60 / max(1, statistics.mean([t["held_bars"] for t in trades]))) if sd_ret > 0 else 0.0
    downside = [n for n in nets if n < 0]
    if len(downside) >= 2:
        ds_sd = statistics.stdev(downside)
        sortino = (m_ret / ds_sd) * math.sqrt(252 * 24 * 60 / max(1, statistics.mean([t["held_bars"] for t in trades]))) if ds_sd > 0 else 0.0
    else:
        sortino = 0.0
    calmar = ((balance / START_EQUITY) - 1.0) / max_dd_observed if max_dd_observed > 0 else 0.0
else:
    sharpe = sortino = calmar = 0.0

# consec wins/losses
max_consec_w = max_consec_l = cur_w = cur_l = 0
for n in nets:
    if n > 0:
        cur_w += 1; cur_l = 0
        max_consec_w = max(max_consec_w, cur_w)
    else:
        cur_l += 1; cur_w = 0
        max_consec_l = max(max_consec_l, cur_l)

gross_wins = sum(wins) if wins else 0.0
gross_losses = abs(sum(losses)) if losses else 0.0

signal_metrics = {
    "total_bars_processed": total_bars,
    "total_orchestrate_calls": total_bars,
    "exceptions_during_loop": exception_count,
    "BUY_count": action_ctr["BUY"],
    "SELL_count": action_ctr["SELL"],
    "HOLD_count": action_ctr["HOLD"],
    "BUY_pct": round(100.0 * action_ctr["BUY"] / max(1, total_bars), 4),
    "SELL_pct": round(100.0 * action_ctr["SELL"] / max(1, total_bars), 4),
    "HOLD_pct": round(100.0 * action_ctr["HOLD"] / max(1, total_bars), 4),
    "stale_rejections": metrics_acc["stale"],
    "invalid_rejections": metrics_acc["invalid"],
    "future_timestamp_rejections": metrics_acc["future_timestamp"],
    "duplicates_removed": metrics_acc["duplicates_removed"],
    "negative_edge_normalized": metrics_acc["negative_edge_normalized"],
    "avg_net_conviction": round(safe_mean(conviction_acc), 6),
    "std_net_conviction": round(safe_std(conviction_acc), 6),
    "conviction_distribution": {
        "0.0-0.2": conviction_buckets[0], "0.2-0.4": conviction_buckets[1],
        "0.4-0.6": conviction_buckets[2], "0.6-0.8": conviction_buckets[3],
        "0.8-1.0": conviction_buckets[4],
    },
    "avg_urgency": round(safe_mean(urgency_acc), 6),
    "std_urgency": round(safe_std(urgency_acc), 6),
    "avg_expected_edge_bps": round(safe_mean(edge_acc), 6),
    "std_expected_edge_bps": round(safe_std(edge_acc), 6),
    "action_threshold_hit_rate": round(
        100.0 * sum(1 for c in conviction_acc if c >= config.action_threshold) / max(1, len(conviction_acc)), 4
    ),
    "rationale_breakdown": dict(rationale_ctr),
}

trade_metrics: Dict[str, Any] = {
    "total_trades": len(trades),
}
if trades:
    trade_metrics.update({
        "win_rate": round(len(wins) / len(trades), 6),
        "avg_win_bps": round(safe_mean(wins) * 1e4 if wins else 0.0, 4),
        "avg_loss_bps": round(safe_mean(losses) * 1e4 if losses else 0.0, 4),
        "best_trade_bps": round(max(nets) * 1e4, 4),
        "worst_trade_bps": round(min(nets) * 1e4, 4),
        "max_consec_wins": max_consec_w,
        "max_consec_losses": max_consec_l,
        "expectancy_bps_per_trade": round(safe_mean(nets) * 1e4, 4),
        "sharpe_annualized_synthetic": round(sharpe, 4),
        "sortino_annualized_synthetic": round(sortino, 4),
        "calmar_synthetic": round(calmar, 4),
        "max_drawdown_pct": round(max_dd_observed * 100, 4),
        "profit_factor": round(gross_wins / gross_losses, 6) if gross_losses > 0 else float("inf"),
        "total_return_pct": round(100.0 * (balance / START_EQUITY - 1.0), 4),
        "final_equity": round(balance, 2),
        "round_trip_cost_drag_bps_total": round(2 * FEE_BPS * len(trades), 2),
    })

fusion_metrics = {
    "avg_agreement_ratio": round(safe_mean(agreement_acc), 6),
    "avg_conflict_ratio": round(safe_mean(conflict_acc), 6),
    "dominant_timeframe_distribution": dict(dominant_tf_ctr),
    "alignment_bonus_triggered_bars": sum(1 for r in records if r["agreement"] > 0),
    "conflict_penalty_triggered_bars": sum(1 for r in records if r["conflict"] > 0),
}

regime_breakdown: Dict[str, Dict[str, Any]] = {}
for r in records:
    rg = r["regime"]
    regime_breakdown.setdefault(rg, {"count": 0, "convs": [], "actions": Counter()})
    regime_breakdown[rg]["count"] += 1
    regime_breakdown[rg]["convs"].append(r["conviction"])
    regime_breakdown[rg]["actions"][r["action"]] += 1
regime_summary = {
    rg: {
        "count": v["count"],
        "avg_conviction": round(safe_mean(v["convs"]), 6),
        "actions": dict(v["actions"]),
    } for rg, v in regime_breakdown.items()
}

feedback_metrics = {
    "update_performance_calls": update_perf_calls,
    "tracked_sources": list(orch.performance_stats.keys()),
    "performance_multipliers": {
        sid: stats.current_multiplier
        for sid, stats in orch.performance_stats.items()
    },
    "trade_counts": {
        sid: stats.trade_count for sid, stats in orch.performance_stats.items()
    },
    "win_rates": {
        sid: stats.win_rate for sid, stats in orch.performance_stats.items()
    },
}

schema_audit = {
    "bars_audited": total_bars,
    "violations_per_required_key": {
        k: schema_violations[k] for k in REQUIRED_META_KEYS if schema_violations[k] > 0
    },
    "rationale_paths_with_violations": {
        k: schema_sample_paths[k] for k in REQUIRED_META_KEYS if schema_violations[k] > 0
    },
    "fully_clean_keys": [k for k in REQUIRED_META_KEYS if schema_violations[k] == 0],
}

# ---------- Phase 3.5: ADVERSARIAL TESTS ----------
adv: Dict[str, Dict[str, Any]] = {}
ts_now = ohlcv[-1][0] / 1000.0

def run_test(name: str, fn) -> None:
    try:
        adv[name] = {"PASS": fn()}
    except Exception as e:
        adv[name] = {"PASS": False, "exception": f"{type(e).__name__}: {e}"}

def t1():
    r = orch.orchestrate(signals=[], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    missing = [k for k in REQUIRED_META_KEYS if k not in r.meta_info]
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "missing_keys": missing,
            "result": r.action == Action.HOLD and len(missing) == 0}

def t2():
    sigs = [AlphaSignal("signal_engine", 0, 0.5, 1.0, ts_now, "1m"),
            AlphaSignal("liquidity_sweep_alpha", 0, 0.5, 1.0, ts_now, "5m")]
    r = orch.orchestrate(signals=sigs, regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "result": r.action == Action.HOLD}

def t3():
    r = orch.orchestrate(signals="LONG", regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    rd = r.meta_info.get("rejection_details", [])
    has_inv = any(d.get("reason") == "invalid_input_type" for d in rd)
    return {"action": r.action.name, "rejection_details": rd,
            "result": r.action == Action.HOLD and has_inv}

def t4():
    sigs = [AlphaSignal("unknown_alpha", 1, 0.6, 5.0, ts_now, "1m")]
    r = orch.orchestrate(signals=sigs, regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    rd = r.meta_info.get("rejection_details", [])
    has_unk = any(d.get("reason") == "unknown_source" for d in rd)
    return {"action": r.action.name, "rejection_details": rd,
            "result": r.action == Action.HOLD and has_unk}

def t5():
    s = AlphaSignal("signal_engine", 1, 0.0, 0.0, ts_now, "1m")
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    return {"action": r.action.name, "result": True}

def t6():
    s = AlphaSignal("signal_engine", 1, 1.0, 100.0, ts_now, "1m")
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    return {"action": r.action.name, "result": True}

def t7():
    r = orch.orchestrate(signals=[], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=None)
    missing = [k for k in REQUIRED_META_KEYS if k not in r.meta_info]
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "missing_keys": missing,
            "result": r.action == Action.HOLD and r.meta_info.get("rationale") == "invalid_current_time"}

def t8():
    r = orch.orchestrate(signals=[], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=float("nan"))
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "result": r.action == Action.HOLD and r.meta_info.get("rationale") == "invalid_current_time"}

def t9():
    s = AlphaSignal("signal_engine", 1, 0.8, 25.0, ts_now, "1m")
    es = ExecutionState(0.0, 1e6, 0.15)
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=es, current_time=ts_now)
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "urgency": r.urgency,
            "result": r.action == Action.HOLD and r.urgency == 0.0
                      and r.meta_info.get("rationale") == "dd_breach"}

def t10():
    constructed_ok = False
    try:
        AlphaSignal("signal_engine", 1, 0.5, 0.0, ts_now, "1m")
        constructed_ok = True
    except Exception:
        constructed_ok = False
    rejected_neg = False
    try:
        AlphaSignal("signal_engine", 1, 0.5, -5.0, ts_now, "1m")
    except ValueError:
        rejected_neg = True
    return {"zero_edge_ok": constructed_ok, "neg_rejected": rejected_neg,
            "result": constructed_ok and rejected_neg}

def t11():
    s = AlphaSignal("signal_engine", 1, 0.5, 5.0, ts_now + 10.0, "1m")
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    rd = r.meta_info.get("rejection_details", [])
    has_fut = any(d.get("reason") == "future_timestamp" for d in rd)
    return {"rejection_details": rd, "result": has_fut}

def t12():
    s = AlphaSignal("signal_engine", 1, 0.5, 5.0, ts_now - 500.0, "1m")
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    rd = r.meta_info.get("rejection_details", [])
    has_stale = any(d.get("reason") == "stale" for d in rd)
    return {"rejection_details": rd, "result": has_stale}

def t13():
    es = ExecutionState(0.0, 0.0, 0.05)
    s = AlphaSignal("signal_engine", 1, 0.8, 25.0, ts_now, "1m")
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=es, current_time=ts_now)
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "urgency": r.urgency,
            "result": r.action == Action.HOLD and r.urgency == 0.0
                      and r.meta_info.get("rationale") == "zero_exp"}

def t14():
    sigs = [
        AlphaSignal("signal_engine", 1, 0.5, 5.0, ts_now - 5.0, "1m"),
        AlphaSignal("signal_engine", -1, 0.7, 7.0, ts_now - 1.0, "1m"),
    ]
    r = orch.orchestrate(signals=sigs, regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    dups = r.meta_info.get("metrics", {}).get("duplicates_removed", 0)
    return {"duplicates_removed": dups, "result": dups == 1}

def t15():
    errs: List[str] = []
    def worker():
        for _ in range(50):
            s = AlphaSignal("signal_engine", 1, 0.5, 5.0, ts_now, "1m")
            try:
                r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                                     feature_quality=FeatureQuality(0.0, 0.0),
                                     exec_state=build_exec_state(START_EQUITY, 0.0),
                                     current_time=ts_now)
                if not isinstance(r, OrchestratedAction):
                    errs.append("not OrchestratedAction")
            except Exception as e:
                errs.append(f"{type(e).__name__}: {e}")
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    return {"errors": errs[:5], "n_errors": len(errs), "result": len(errs) == 0}

def t16():
    # malformed payload
    pre = orch.rejection_telemetry.get("malformed_payload", 0) if hasattr(orch, "rejection_telemetry") else 0
    try:
        orch.update_performance(trade_result=[1, 2, 3], event_time=ts_now)
        no_exc = True
    except Exception:
        no_exc = False
    post = orch.rejection_telemetry.get("malformed_payload", 0) if hasattr(orch, "rejection_telemetry") else 0
    return {"no_exception": no_exc, "telemetry_delta": post - pre,
            "result": no_exc}

def t17():
    # not realistically reproducible without reaching feedback drift state; just record SKIP
    return {"result": "SKIP (requires 20+ losses to produce drift)"}

def t18():
    s = AlphaSignal("signal_engine", 1, 0.05, 1.0, ts_now, "1m")
    r = orch.orchestrate(signals=[s], regime=RegimeContext("range", 0.3, 0.5),
                         feature_quality=FeatureQuality(0.0, 0.0),
                         exec_state=build_exec_state(START_EQUITY, 0.0),
                         current_time=ts_now)
    return {"action": r.action.name, "rationale": r.meta_info.get("rationale"),
            "result": r.action == Action.HOLD}

def t19():
    cfg2 = OrchestratorConfig(
        signal_weights={"signal_engine": 0.5, "liquidity_sweep_alpha": 0.5},
        timeframe_weights={"1m": 0.4, "5m": 0.6, "default": 1.0},
        timeframe_order=["1m", "5m", "default"],
        higher_tf_dominance=True,
        action_threshold=0.30, signal_ttl_seconds=60.0,
        feedback_enabled=False, max_missing_data_ratio=0.5,
        allow_unknown_sources=False,
    )
    o2 = AlphaOrchestrator(cfg2)
    sigs = [
        AlphaSignal("signal_engine", 1, 0.7, 25.0, ts_now, "1m"),
        AlphaSignal("liquidity_sweep_alpha", -1, 0.7, 25.0, ts_now, "5m"),
    ]
    r = o2.orchestrate(signals=sigs, regime=RegimeContext("range", 0.3, 0.5),
                       feature_quality=FeatureQuality(0.0, 0.0),
                       exec_state=build_exec_state(START_EQUITY, 0.0),
                       current_time=ts_now)
    dom = r.meta_info.get("dominant_timeframe")
    return {"action": r.action.name, "dominant_tf": dom,
            "tf_breakdown": r.meta_info.get("timeframe_breakdown"),
            "result": dom == "5m"}

def t20():
    # Sample 10 different HOLD-causing inputs and verify schema parity
    cases = [
        ("empty",        lambda: orch.orchestrate([], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), ts_now)),
        ("invalid_time", lambda: orch.orchestrate([], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), None)),
        ("nan_time",     lambda: orch.orchestrate([], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), float("nan"))),
        ("unknown",      lambda: orch.orchestrate([AlphaSignal("unknown_alpha",1,0.6,5.0,ts_now,"1m")], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), ts_now)),
        ("str_input",    lambda: orch.orchestrate("LONG", RegimeContext("r",0.3,0.5), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), ts_now)),
        ("dd_breach",    lambda: orch.orchestrate([AlphaSignal("signal_engine",1,0.8,25.0,ts_now,"1m")], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), ExecutionState(0.0,1e6,0.15), ts_now)),
        ("zero_exp",     lambda: orch.orchestrate([AlphaSignal("signal_engine",1,0.8,25.0,ts_now,"1m")], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), ExecutionState(0.0,0.0,0.05), ts_now)),
        ("low_liq",      lambda: orch.orchestrate([AlphaSignal("signal_engine",1,0.8,25.0,ts_now,"1m")], RegimeContext("r",0.3,0.05), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), ts_now)),
        ("poor_fq",      lambda: orch.orchestrate([AlphaSignal("signal_engine",1,0.8,25.0,ts_now,"1m")], RegimeContext("r",0.3,0.5), FeatureQuality(0.0,0.99), build_exec_state(START_EQUITY,0.0), ts_now)),
        ("weak",         lambda: orch.orchestrate([AlphaSignal("signal_engine",1,0.05,1.0,ts_now,"1m")], RegimeContext("r",0.3,0.5), FeatureQuality(0,0), build_exec_state(START_EQUITY,0.0), ts_now)),
    ]
    results = {}
    all_pass = True
    for nm, fn in cases:
        try:
            r = fn()
            missing = [k for k in REQUIRED_META_KEYS if k not in r.meta_info]
            results[nm] = {
                "action": r.action.name,
                "rationale": r.meta_info.get("rationale"),
                "missing_keys": missing,
            }
            if missing:
                all_pass = False
        except Exception as e:
            results[nm] = {"exception": f"{type(e).__name__}: {e}"}
            all_pass = False
    return {"per_case": results, "result": all_pass}

for nm, fn in [("TEST-1",t1),("TEST-2",t2),("TEST-3",t3),("TEST-4",t4),("TEST-5",t5),
               ("TEST-6",t6),("TEST-7",t7),("TEST-8",t8),("TEST-9",t9),("TEST-10",t10),
               ("TEST-11",t11),("TEST-12",t12),("TEST-13",t13),("TEST-14",t14),("TEST-15",t15),
               ("TEST-16",t16),("TEST-17",t17),("TEST-18",t18),("TEST-19",t19),("TEST-20",t20)]:
    run_test(nm, fn)
    print(f"[PHASE-3][STEP-3.5] {nm}: {adv[nm]}")

# ---------- write outputs ----------
all_metrics = {
    "audit_version": "v1",
    "data_source": "REAL: data/ohlcv_1m.csv (Dec 2023 BTCUSDT 1m)",
    "warmup_bars": WARMUP, "horizon_bars": HORIZON,
    "fee_bps_per_side": FEE_BPS, "slippage_bps_per_side": SLIP_BPS,
    "starting_equity": START_EQUITY,
    "elapsed_seconds": round(elapsed, 2),
    "signal_metrics": signal_metrics,
    "trade_metrics": trade_metrics,
    "fusion_metrics": fusion_metrics,
    "regime_summary": regime_summary,
    "feedback_metrics": feedback_metrics,
    "schema_audit": schema_audit,
    "rationale_distribution": dict(rationale_ctr),
}

with open(f"{OUT}/orchestrator_audit.json", "w") as fh:
    json.dump(all_metrics, fh, indent=2, default=str)

with open(f"{OUT}/adversarial_results.json", "w") as fh:
    json.dump(adv, fh, indent=2, default=str)

with open(f"{OUT}/orchestrator_records.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["i","ts_ms","close","action","conviction","urgency","edge_bps",
                "rationale","regime","dominant_tf","agreement","conflict"])
    for r in records:
        w.writerow([r["i"], r["ts_ms"], r["close"], r["action"], r["conviction"],
                    r["urgency"], r["edge_bps"], r["rationale"], r["regime"],
                    r["dominant_tf"], r["agreement"], r["conflict"]])

with open(f"{OUT}/orchestrator_trades.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["entry_i","exit_i","direction","entry_p","exit_p","gross","net",
                "pnl_amount","held_bars","balance_after","entry_conviction","regime"])
    for t in trades:
        w.writerow([t["entry_i"], t["exit_i"], t["direction"], t["entry_p"],
                    t["exit_p"], t["gross"], t["net"], t["pnl_amount"],
                    t["held_bars"], t["balance_after"], t["entry_conviction"],
                    t["regime"]])

print(f"\n[PHASE-3] complete.")
print(f"  bars={total_bars}  trades={len(trades)}  exceptions={exception_count}")
print(f"  actions={dict(action_ctr)}")
print(f"  rationales={dict(rationale_ctr)}")
print(f"  schema violations: {dict(schema_audit['violations_per_required_key'])}")
print(f"  trade win_rate={trade_metrics.get('win_rate','n/a')}  "
      f"PF={trade_metrics.get('profit_factor','n/a')}  "
      f"max_dd={trade_metrics.get('max_drawdown_pct','n/a')}%")
print(f"  final_equity={trade_metrics.get('final_equity','n/a')}")
print(f"  adv tests pass: {sum(1 for v in adv.values() if v.get('PASS') is True)}/20")
print(f"  outputs in {OUT}/")
