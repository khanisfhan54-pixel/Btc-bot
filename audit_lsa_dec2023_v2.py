"""
LSA audit v2 — RE-RUN using REAL per-minute aggTrades counts as trades_count.

This isolates "LSA on real data with real trade arrivals" from
"LSA on real data with synthetic trade-count proxy" (v1).

Reads v1 outputs by importing audit_lsa_dec2023 module-level state would
re-run everything; instead we reload data here and run a focused LSA loop.

Output: audit_lsa_output/lsa_records_v2.csv, audit_lsa_output/audit_v2.json
"""

from __future__ import annotations

import csv
import json
import math
import os
import time
import bisect
from collections import Counter
from typing import Any, Dict, List, Optional

OUT = "audit_lsa_output"
os.makedirs(OUT, exist_ok=True)

from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha  # noqa: E402

# -------- load OHLCV 1m --------
ohlcv: List[List[float]] = []
with open("data/ohlcv_1m.csv") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
        ohlcv.append([
            int(row["timestamp_ms"]), float(row["open"]), float(row["high"]),
            float(row["low"]), float(row["close"]), float(row["volume"]),
        ])
print(f"loaded {len(ohlcv)} 1m bars")

# -------- load L1 book --------
book_snaps: List[Dict[str, float]] = []
with open("data/features_book.csv") as fh:
    for row in csv.DictReader(fh):
        try:
            book_snaps.append({
                "ts_ms": int(row["timestamp_ms"]),
                "bid_p": float(row["best_bid_price"]),
                "bid_s": float(row["best_bid_qty"]),
                "ask_p": float(row["best_ask_price"]),
                "ask_s": float(row["best_ask_qty"]),
            })
        except (ValueError, KeyError):
            continue
book_ts = [b["ts_ms"] for b in book_snaps]
print(f"loaded {len(book_snaps)} book snapshots")

# -------- load aggTrades, bin per minute --------
trade_counts: Dict[int, int] = {}  # bar_start_ms -> count
print("binning aggTrades per minute...")
t0 = time.time()
with open("data/aggTrades_dec2023.csv") as fh:
    for row in csv.DictReader(fh):
        try:
            ts_ms = int(row["transact_time"])
        except (ValueError, KeyError):
            continue
        bar_start = (ts_ms // 60_000) * 60_000
        trade_counts[bar_start] = trade_counts.get(bar_start, 0) + 1
print(f"binned {sum(trade_counts.values())} trades into {len(trade_counts)} minute bins  ({time.time()-t0:.1f}s)")

# diagnostic stats on real trade counts
tc_vals = list(trade_counts.values())
tc_vals.sort()
def pct(p): return tc_vals[min(len(tc_vals)-1, int(len(tc_vals) * p))]
print(f"trades/min  min={tc_vals[0]}  p10={pct(0.1)}  p50={pct(0.5)}  p90={pct(0.9)}  p99={pct(0.99)}  max={tc_vals[-1]}")

# -------- EMA + ATR --------
def ema(vals, period):
    a = 2.0 / (period + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append((1 - a) * out[-1] + a * v)
    return out

closes = [b[4] for b in ohlcv]
ema_fast = ema(closes, 12)
ema_slow = ema(closes, 26)

def atr_series(bars, period=14):
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b[2] - b[3])
        else:
            pc = bars[i-1][4]
            tr.append(max(b[2] - b[3], abs(b[2] - pc), abs(b[3] - pc)))
    out, s = [], 0.0
    for i, t in enumerate(tr):
        if i < period:
            s += t
            out.append(s / (i + 1))
        else:
            out.append((out[-1] * (period - 1) + t) / period)
    return out

atr_arr = atr_series(ohlcv, 14)

# -------- align book to bar close --------
aligned_idx: List[Optional[int]] = []
for b in ohlcv:
    idx = bisect.bisect_right(book_ts, b[0] + 60_000) - 1
    aligned_idx.append(idx if idx >= 0 else None)

# -------- run LSA with REAL trades_count --------
warmup = 25
init_high = max(b[2] for b in ohlcv[:warmup])
init_low  = min(b[3] for b in ohlcv[:warmup])

lsa = LiquiditySweepAlpha(depth_levels=1, history_window=100,
                          initial_high=init_high, initial_low=init_low)

def book_dict(s):
    return {
        "bids": [{"price": s["bid_p"], "size": s["bid_s"]}],
        "asks": [{"price": s["ask_p"], "size": s["ask_s"]}],
    }

action_ctr: Counter = Counter()
state_ctr: Counter = Counter()
regime_ctr: Counter = Counter()
records: List[Dict[str, Any]] = []
prev_book = None
intensity_max = 0.0
intensity_baseline_samples = []

ROLLING = 60
t0 = time.time()
for i, bar in enumerate(ohlcv):
    snap_i = aligned_idx[i]
    if snap_i is None:
        continue
    cb = book_dict(book_snaps[snap_i])
    if prev_book is None:
        prev_book = cb

    if i > 0 and i % 5 == 0:
        lo = max(0, i - ROLLING)
        lsa.update_liquidity_pools(
            [b[2] for b in ohlcv[lo:i]],
            [b[3] for b in ohlcv[lo:i]],
        )

    real_tc = trade_counts.get(bar[0], 0)
    md = {
        "price": bar[4], "close_price": bar[4],
        "prev_book": prev_book, "curr_book": cb,
        "timestamp": bar[0] / 1000.0,
        "trades_count": real_tc,
        "atr": max(1e-8, atr_arr[i]),
        "ema_fast": ema_fast[i], "ema_slow": ema_slow[i],
        "pre_sweep_depth": book_snaps[snap_i]["bid_s"] + book_snaps[snap_i]["ask_s"],
        "curr_depth":      book_snaps[snap_i]["bid_s"] + book_snaps[snap_i]["ask_s"],
        "sweep_time_elapsed": 60.0,
    }
    try:
        out = lsa.get_signal(md, regime_context={"regime": "RANGING"})
    except Exception as exc:
        prev_book = cb
        continue

    if i >= warmup:
        action_ctr[out["action"]] += 1
        state_ctr[out["state"]] += 1
        regime_ctr[out["regime"]] += 1
        intensity_max = max(intensity_max, out["hawkes_intensity"])
        intensity_baseline_samples.append(out["hawkes_intensity"])
        records.append({
            "i": i, "ts_ms": bar[0], "close": bar[4],
            "action": out["action"], "confidence": out["confidence"],
            "state": out["state"], "regime": out["regime"],
            "ofi_z": out["ofi_zscore"], "hawkes": out["hawkes_intensity"],
            "prob_above": out["prob_above"], "prob_below": out["prob_below"],
        })
    prev_book = cb

print(f"\nv2 LOOP done in {time.time()-t0:.1f}s  records={len(records)}")
print(f"actions: {dict(action_ctr)}")
print(f"states:  {dict(state_ctr)}")
print(f"regimes: {dict(regime_ctr)}")
print(f"hawkes_max: {intensity_max:.4f}  hawkes_mean: {sum(intensity_baseline_samples)/len(intensity_baseline_samples):.4f}")

# -------- write records + PnL --------
with open(f"{OUT}/lsa_records_v2.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["bar_idx", "ts_ms", "close", "action", "confidence", "state",
                "regime", "ofi_z", "hawkes", "prob_above", "prob_below"])
    for r in records:
        w.writerow([r["i"], r["ts_ms"], r["close"], r["action"], r["confidence"],
                    r["state"], r["regime"], r["ofi_z"], r["hawkes"],
                    r["prob_above"], r["prob_below"]])

# -------- compute trades + metrics --------
HORIZON = 12
COST = (8 + 3) * 2 / 10_000.0
trades = []
for r in records:
    if r["action"] == "HOLD":
        continue
    direction = 1 if r["action"] == "BUY" else -1
    entry_i = r["i"]
    exit_i = min(entry_i + HORIZON, len(ohlcv) - 1)
    if exit_i <= entry_i:
        continue
    ep, xp = ohlcv[entry_i][4], ohlcv[exit_i][4]
    gross = direction * (xp - ep) / ep
    net = gross - COST
    trades.append({
        "entry_i": entry_i, "exit_i": exit_i,
        "exit_ts": ohlcv[exit_i][0], "direction": direction,
        "entry_p": ep, "exit_p": xp, "gross": gross, "net": net,
        "hold_bars": exit_i - entry_i, "confidence": r["confidence"],
        "state": r["state"], "ofi_z": r["ofi_z"], "hawkes": r["hawkes"],
    })

print(f"\nTRADES: {len(trades)}")

metrics: Dict[str, Any] = {
    "version": "v2_real_trades_count",
    "total_records_after_warmup": len(records),
    "action_distribution": dict(action_ctr),
    "state_distribution": dict(state_ctr),
    "regime_distribution": dict(regime_ctr),
    "hawkes_max": intensity_max,
    "hawkes_mean": sum(intensity_baseline_samples) / len(intensity_baseline_samples),
    "trades_count_stats": {
        "min": tc_vals[0], "p10": pct(0.1), "p50": pct(0.5),
        "p90": pct(0.9), "p99": pct(0.99), "max": tc_vals[-1],
    },
    "total_trades": len(trades),
}

if trades:
    nets = [t["net"] for t in trades]
    grosses = [t["gross"] for t in trades]
    wins = sum(1 for n in nets if n > 0)
    metrics["win_rate"] = round(wins / len(nets), 6)
    buy_t = [t for t in trades if t["direction"] == 1]
    sell_t = [t for t in trades if t["direction"] == -1]
    metrics["buy_count"] = len(buy_t); metrics["sell_count"] = len(sell_t)
    metrics["buy_hit_rate"] = round(sum(1 for t in buy_t if t["gross"] > COST) / max(1, len(buy_t)), 6)
    metrics["sell_hit_rate"] = round(sum(1 for t in sell_t if t["gross"] > COST) / max(1, len(sell_t)), 6)
    metrics["expectancy_per_trade_pct"] = round(100.0 * sum(nets) / len(nets), 6)

    sw = sum(n for n in nets if n > 0)
    sl = abs(sum(n for n in nets if n <= 0))
    metrics["profit_factor"] = round(sw / sl, 6) if sl > 0 else float("inf")
    metrics["best_trade_pct"]  = round(100.0 * max(nets), 6)
    metrics["worst_trade_pct"] = round(100.0 * min(nets), 6)
    metrics["avg_holding_bars"] = round(sum(t["hold_bars"] for t in trades) / len(trades), 4)

    # daily Sharpe
    daily: Dict[str, float] = {}
    for t in trades:
        day = time.strftime("%Y-%m-%d", time.gmtime(t["exit_ts"] / 1000))
        daily[day] = daily.get(day, 0.0) + t["net"]
    drs = list(daily.values())
    if len(drs) > 1:
        m = sum(drs) / len(drs)
        var = sum((x - m) ** 2 for x in drs) / (len(drs) - 1)
        sd = math.sqrt(var)
        metrics["sharpe_daily_annualized"] = round((m / sd) * math.sqrt(252) if sd > 0 else 0.0, 6)
    else:
        metrics["sharpe_daily_annualized"] = 0.0
    metrics["trading_days"] = len(drs)

    # max drawdown
    eq, peak, mdd = 1.0, 1.0, 0.0
    for n in nets:
        eq *= (1 + n)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    metrics["final_equity"] = round(eq, 6)
    metrics["max_drawdown"] = round(mdd, 6)

    # state breakdown
    by_s: Dict[str, list] = {}
    for t in trades:
        by_s.setdefault(t["state"], []).append(t)
    metrics["state_breakdown"] = {}
    for s, ts in by_s.items():
        ns = [t["net"] for t in ts]
        metrics["state_breakdown"][s] = {
            "signals": len(ts),
            "win_rate": round(sum(1 for n in ns if n > 0) / len(ts), 4),
            "avg_return_pct": round(100.0 * sum(ns) / len(ts), 6),
            "avg_confidence": round(sum(t["confidence"] for t in ts) / len(ts), 4),
            "avg_ofi_z": round(sum(t["ofi_z"] for t in ts) / len(ts), 4),
            "avg_hawkes": round(sum(t["hawkes"] for t in ts) / len(ts), 4),
        }

    # confidence calibration
    bks = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01)]
    metrics["confidence_calibration"] = {}
    for lo, hi in bks:
        sel = [t for t in trades if lo <= t["confidence"] < hi]
        if sel:
            metrics["confidence_calibration"][f"{lo}-{hi}"] = {
                "count": len(sel),
                "win_rate": round(sum(1 for t in sel if t["net"] > 0) / len(sel), 4),
                "avg_return_pct": round(100.0 * sum(t["net"] for t in sel) / len(sel), 6),
            }
        else:
            metrics["confidence_calibration"][f"{lo}-{hi}"] = {"count": 0, "win_rate": 0.0, "avg_return_pct": 0.0}

    # OFI z buckets
    obks = [(0, 1), (1, 2), (2, 4), (4, 999)]
    metrics["ofi_predictive"] = {}
    for lo, hi in obks:
        sel = [t for t in trades if lo <= abs(t["ofi_z"]) < hi]
        if sel:
            metrics["ofi_predictive"][f"{lo}-{hi}"] = {
                "count": len(sel),
                "win_rate": round(sum(1 for t in sel if t["net"] > 0) / len(sel), 4),
                "avg_return_pct": round(100.0 * sum(t["net"] for t in sel) / len(sel), 6),
            }
        else:
            metrics["ofi_predictive"][f"{lo}-{hi}"] = {"count": 0, "win_rate": 0.0, "avg_return_pct": 0.0}

    # Hawkes buckets
    hbks = [(0, 0.1), (0.1, 1.0), (1.0, 5.0), (5.0, 999)]
    metrics["hawkes_predictive"] = {}
    for lo, hi in hbks:
        sel = [t for t in trades if lo <= t["hawkes"] < hi]
        if sel:
            metrics["hawkes_predictive"][f"{lo}-{hi}"] = {
                "count": len(sel),
                "win_rate": round(sum(1 for t in sel if t["net"] > 0) / len(sel), 4),
                "avg_return_pct": round(100.0 * sum(t["net"] for t in sel) / len(sel), 6),
            }
        else:
            metrics["hawkes_predictive"][f"{lo}-{hi}"] = {"count": 0, "win_rate": 0.0, "avg_return_pct": 0.0}

with open(f"{OUT}/audit_v2.json", "w") as fh:
    json.dump(metrics, fh, indent=2, default=str)

print("\n=== METRICS SUMMARY ===")
for k, v in metrics.items():
    if not isinstance(v, dict):
        print(f"  {k}: {v}")
print(f"\nwrote {OUT}/audit_v2.json and {OUT}/lsa_records_v2.csv")
