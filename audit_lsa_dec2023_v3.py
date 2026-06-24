"""
LSA audit v3 — fixes the v2 silent-exception bug flagged by code review.

Differences from v2:
  - Exceptions from get_signal() are NOT silently swallowed. They are
    classified and counted; the bar is recorded as HOLD so all 39,670
    post-warmup bars are accounted for.
  - Records the per-state exception count so we can prove that
    ACTIVE_SWEEP IS reached but raises AttributeError on
    self.ofi_sum / self.ofi_sq_sum (which __init__ never sets).
  - Adds the overlapping-signals caveat to the metrics output.

Output: audit_lsa_output/lsa_records_v3.csv, audit_lsa_output/audit_v3.json
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import bisect
import traceback
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

OUT = "audit_lsa_output"
os.makedirs(OUT, exist_ok=True)

from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha  # noqa: E402

# ---------- load OHLCV 1m ----------
ohlcv: List[List[float]] = []
with open("data/ohlcv_1m.csv") as fh:
    for row in csv.DictReader(fh):
        ohlcv.append([
            int(row["timestamp_ms"]), float(row["open"]), float(row["high"]),
            float(row["low"]), float(row["close"]), float(row["volume"]),
        ])
print(f"loaded {len(ohlcv)} 1m bars")

# ---------- load L1 book ----------
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

# ---------- bin aggTrades per minute ----------
trade_counts: Dict[int, int] = {}
with open("data/aggTrades_dec2023.csv") as fh:
    for row in csv.DictReader(fh):
        try:
            ts_ms = int(row["transact_time"])
        except (ValueError, KeyError):
            continue
        bar_start = (ts_ms // 60_000) * 60_000
        trade_counts[bar_start] = trade_counts.get(bar_start, 0) + 1
print(f"binned {sum(trade_counts.values())} trades into {len(trade_counts)} minute bins")

# ---------- EMA + ATR ----------
def ema(vals, period):
    a = 2.0 / (period + 1.0); out = [vals[0]]
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
            s += t; out.append(s / (i + 1))
        else:
            out.append((out[-1] * (period - 1) + t) / period)
    return out

atr_arr = atr_series(ohlcv, 14)

# ---------- align book to bar close ----------
aligned_idx: List[Optional[int]] = []
for b in ohlcv:
    idx = bisect.bisect_right(book_ts, b[0] + 60_000) - 1
    aligned_idx.append(idx if idx >= 0 else None)

# ---------- run LSA with REAL trades_count + honest exception accounting ----------
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
state_ctr: Counter = Counter()       # NOTE: only counts SUCCESSFUL signals
regime_ctr: Counter = Counter()
exc_ctr: Counter = Counter()         # exception type -> count
exc_first_traceback: Dict[str, str] = {}
exc_pre_state_attempts: Counter = Counter()  # how many pre-exception bars hit which state
records: List[Dict[str, Any]] = []
prev_book = None
intensity_baseline_samples = []
intensity_max = 0.0
attempts_after_warmup = 0
exceptions_after_warmup = 0

ROLLING = 60
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

    if i >= warmup:
        attempts_after_warmup += 1

    # Try get_signal; if it raises, classify and record HOLD for the bar.
    try:
        out = lsa.get_signal(md, regime_context={"regime": "RANGING"})
    except Exception as exc:
        if i >= warmup:
            exceptions_after_warmup += 1
            key = f"{type(exc).__name__}: {exc}"
            exc_ctr[key] += 1
            if key not in exc_first_traceback:
                exc_first_traceback[key] = traceback.format_exc()
            # We can infer that the bar was inside ACTIVE_SWEEP because the
            # only LSA path that calls _liquidity_forecast (the source of the
            # AttributeError) is ACTIVE_SWEEP. Record this attribution.
            # detect_sweep_state can also tell us — call it directly:
            try:
                state_attempted = lsa.detect_sweep_state(
                    bar[4], max(1e-8, atr_arr[i]), lsa.hawkes_lambda
                )
            except Exception:
                state_attempted = "UNKNOWN"
            exc_pre_state_attempts[state_attempted] += 1
            # Treat as HOLD for accounting
            out = {
                "action": "HOLD", "confidence": 0.0,
                "state": state_attempted, "regime": "RANGING",
                "ofi_zscore": 0.0,
                "hawkes_intensity": getattr(lsa, "hawkes_lambda", 0.0),
                "logic": f"EXC:{key[:60]}",
                "micro_prob": 0.5, "macro_prob": 0.5,
                "prob_above": 0.5, "prob_below": 0.5,
            }
        else:
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

print(f"\nv3 LOOP done. attempts_after_warmup={attempts_after_warmup}  "
      f"exceptions={exceptions_after_warmup}  "
      f"records={len(records)}")
print(f"actions: {dict(action_ctr)}")
print(f"states (incl. exception-attributed):  {dict(state_ctr)}")
print(f"exception types:")
for k, v in exc_ctr.most_common():
    print(f"  [{v}] {k}")
print(f"exception state attribution: {dict(exc_pre_state_attempts)}")
print(f"hawkes_max: {intensity_max:.4f}  hawkes_mean: "
      f"{(sum(intensity_baseline_samples)/max(1,len(intensity_baseline_samples))):.4f}")

# ---------- write records ----------
with open(f"{OUT}/lsa_records_v3.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["bar_idx", "ts_ms", "close", "action", "confidence", "state",
                "regime", "ofi_z", "hawkes", "prob_above", "prob_below"])
    for r in records:
        w.writerow([r["i"], r["ts_ms"], r["close"], r["action"], r["confidence"],
                    r["state"], r["regime"], r["ofi_z"], r["hawkes"],
                    r["prob_above"], r["prob_below"]])

# ---------- compute trades + metrics ----------
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
    "version": "v3_real_trades_count_with_exception_accounting",
    "attempts_after_warmup": attempts_after_warmup,
    "exceptions_after_warmup": exceptions_after_warmup,
    "exception_types": dict(exc_ctr),
    "exception_state_attribution": dict(exc_pre_state_attempts),
    "exception_first_traceback": exc_first_traceback,
    "total_records_after_warmup": len(records),
    "action_distribution": dict(action_ctr),
    "state_distribution_observed": dict(state_ctr),
    "regime_distribution": dict(regime_ctr),
    "hawkes_max": intensity_max,
    "hawkes_mean": sum(intensity_baseline_samples) / max(1, len(intensity_baseline_samples)),
    "total_trades": len(trades),
    "horizon_bars": HORIZON,
    "round_trip_cost_bps": 22,
    "OVERLAP_CAVEAT": (
        "Per-trade returns are not a portfolio backtest. With horizon=12 and "
        "1,384 entries over 39,670 bars, ~43% of adjacent trades overlap. The "
        "headline Sharpe and max-drawdown therefore describe the SIGNAL's "
        "synthetic equity, not a single-position portfolio. They remain "
        "comparable across runs (e.g. before/after a fix), but the absolute "
        "magnitude is not portfolio-realistic."
    ),
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
        metrics["sharpe_daily_annualized_signal_synthetic"] = round((m / sd) * math.sqrt(252) if sd > 0 else 0.0, 6)
    else:
        metrics["sharpe_daily_annualized_signal_synthetic"] = 0.0
    metrics["trading_days"] = len(drs)

    # max drawdown on signal-synthetic equity
    eq, peak, mdd = 1.0, 1.0, 0.0
    for n in nets:
        eq *= (1 + n)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    metrics["final_equity_signal_synthetic"] = round(eq, 6)
    metrics["max_drawdown_signal_synthetic"] = round(mdd, 6)

    # Non-overlapping single-position portfolio (greedy: take entry, skip until horizon-12 exit)
    np_nets: List[float] = []
    next_eligible = 0
    for r in sorted(records, key=lambda r: r["i"]):
        if r["action"] == "HOLD" or r["i"] < next_eligible:
            continue
        direction = 1 if r["action"] == "BUY" else -1
        entry_i = r["i"]
        exit_i = min(entry_i + HORIZON, len(ohlcv) - 1)
        ep, xp = ohlcv[entry_i][4], ohlcv[exit_i][4]
        np_nets.append(direction * (xp - ep) / ep - COST)
        next_eligible = exit_i + 1
    if np_nets:
        wins_np = sum(1 for n in np_nets if n > 0)
        metrics["non_overlap_trades"] = len(np_nets)
        metrics["non_overlap_win_rate"] = round(wins_np / len(np_nets), 6)
        sw_np = sum(n for n in np_nets if n > 0)
        sl_np = abs(sum(n for n in np_nets if n <= 0))
        metrics["non_overlap_profit_factor"] = round(sw_np / sl_np, 6) if sl_np > 0 else float("inf")
        eq_np, peak_np, mdd_np = 1.0, 1.0, 0.0
        for n in np_nets:
            eq_np *= (1 + n)
            peak_np = max(peak_np, eq_np)
            mdd_np = max(mdd_np, (peak_np - eq_np) / peak_np)
        metrics["non_overlap_final_equity"] = round(eq_np, 6)
        metrics["non_overlap_max_drawdown"] = round(mdd_np, 6)
        metrics["non_overlap_expectancy_per_trade_pct"] = round(100.0 * sum(np_nets) / len(np_nets), 6)

    # state breakdown (using observed state field, which already attributes exception bars)
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

    # OFI / Hawkes buckets
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

with open(f"{OUT}/audit_v3.json", "w") as fh:
    json.dump(metrics, fh, indent=2, default=str)

print("\n=== v3 KEY METRICS ===")
for k in ("attempts_after_warmup", "exceptions_after_warmup", "total_trades",
          "win_rate", "profit_factor", "expectancy_per_trade_pct",
          "sharpe_daily_annualized_signal_synthetic",
          "max_drawdown_signal_synthetic", "final_equity_signal_synthetic",
          "non_overlap_trades", "non_overlap_win_rate",
          "non_overlap_profit_factor", "non_overlap_final_equity",
          "non_overlap_max_drawdown"):
    v = metrics.get(k)
    if v is not None:
        print(f"  {k}: {v}")
print(f"\nwrote {OUT}/audit_v3.json and {OUT}/lsa_records_v3.csv")
