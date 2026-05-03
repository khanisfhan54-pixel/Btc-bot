#!/usr/bin/env python3
"""
audit_engine_dec2023.py
=======================
Senior-quant production audit harness for engine.run_all_engines().

CONTRACT (read-only):
  * Calls only engine.run_all_engines() — no execution helpers, no live API,
    no place_order_with_sl_tp / SniperExecutionEngine / detect_entry_trigger /
    build_trade_plan / compute_score / evaluate_smc_sniper.
  * Uses Dec-2023 BTCUSDT ohlcv_{1m,5m,15m}.csv + bookTicker_dec2023_30s.csv
    + aggTrades_dec2023.csv already present under data/.
  * Writes records.csv + summary.json + invariant_violations.json under
    audit_engine_output/.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import logging
import math
import os
import sys
import time
import traceback
from dataclasses import asdict
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=os.environ.get("AUDIT_LOG_LEVEL", "WARNING"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("audit.engine")

import engine  # noqa: E402

OUT_DIR = "audit_engine_output"
os.makedirs(OUT_DIR, exist_ok=True)

DATA_1M = "data/ohlcv_1m.csv"
DATA_5M = "data/ohlcv_5m.csv"
DATA_15M = "data/ohlcv_15m.csv"
DATA_BOOK = "data/bookTicker_dec2023_30s.csv"
DATA_TRADES = "data/aggTrades_dec2023.csv"


# ---------- data loading ----------

def _load_ohlcv(path: str) -> List[list]:
    rows: List[list] = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        for line in r:
            try:
                ts = int(line[0])
                o = float(line[1])
                h = float(line[2])
                l = float(line[3])
                c = float(line[4])
                v = float(line[5])
            except Exception:
                continue
            rows.append([ts, o, h, l, c, v])
    return rows


def _load_book(path: str) -> List[dict]:
    snaps: List[dict] = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        idx = {name: i for i, name in enumerate(header)}
        for line in r:
            try:
                bb = float(line[idx["best_bid_price"]])
                bq = float(line[idx["best_bid_qty"]])
                ba = float(line[idx["best_ask_price"]])
                aq = float(line[idx["best_ask_qty"]])
                ts = int(line[idx["transaction_time"]])
            except Exception:
                continue
            snaps.append({
                "ts": ts,
                "bids": [[bb, bq]],
                "asks": [[ba, aq]],
            })
    snaps.sort(key=lambda x: x["ts"])
    return snaps


def _load_trades_iter(path: str):
    """Streaming iterator over aggTrades.  Yields (ts_ms, dict)."""
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        idx = {name: i for i, name in enumerate(header)}
        for line in r:
            try:
                ts = int(line[idx["transact_time"]])
                p = float(line[idx["price"]])
                q = float(line[idx["quantity"]])
                m = line[idx["is_buyer_maker"]].lower() == "true"
            except Exception:
                continue
            yield ts, {
                "price": p,
                "amount": q,
                "side": "SELL" if m else "BUY",
                "ts": ts,
            }


def _bucket_trades_by_minute(trades_path: str, minute_keys: List[int]) -> Dict[int, List[dict]]:
    """Bucket aggTrades into per-minute lists, only for the requested minute_keys."""
    keyset = set(minute_keys)
    out: Dict[int, List[dict]] = {k: [] for k in keyset}
    log.info("Bucketing trades from %s ...", trades_path)
    n = 0
    for ts, t in _load_trades_iter(trades_path):
        bucket = (ts // 60000) * 60000
        if bucket in keyset:
            lst = out[bucket]
            if len(lst) < 200:  # cap per-minute list size
                lst.append(t)
        n += 1
        if n % 200000 == 0:
            log.info("  trades scanned: %d", n)
    return out


# ---------- invariant checks ----------

INVARIANT_DEFS = [
    "INV-1: result is dict",
    "INV-2: price > 0 and finite",
    "INV-3: confidence in [0,1]",
    "INV-4: direction in {LONG,SHORT,HOLD,NEUTRAL,WAIT}",
    "INV-5: alpha.confidence in [0,1]",
    "INV-6: alpha.direction in {LONG,SHORT,NEUTRAL}",
    "INV-7: alpha.prob_above + alpha.prob_below within 1e-3 of 1.0 (when both present)",
    "INV-8: allow_trade is bool",
    "INV-9: spread_pct >= 0",
    "INV-10: institutional_score finite",
    "INV-11: order_flow_pressure finite",
    "INV-12: order_imbalance in [-1,1]",
    "INV-13: cascade_probability in [0,1]",
    "INV-14: smc_signal.signal in {LONG,SHORT,NONE}",
    "INV-15: smc_signal.confidence in [0,10]",
    "INV-16: market_state.state is non-empty str",
    "INV-17: regime.confidence in [0,1]",
    "INV-18: when oi_missing or alpha NEUTRAL, allow_trade fail-closed = False",
    "INV-19: confluence_score in [0,10]",
    "INV-20: composite.direction matches top-level direction",
    # ---- Phase 3 additions (audit M-2/L-2 fallout) ----
    "INV-21: alpha.confidence == 0.5 implies fallback path used (reason==run_all_engines_error or default alpha)",
    "INV-22: every fallback bar has reason == 'run_all_engines_error'",
    "INV-23: confluence_score within OUTPUT_SCHEMA range [0,10] (mirrors INV-19, source-of-truth check)",
    "INV-24: cache_hit_rate (when reported) is finite float in [0,1]",
    "INV-25: per_engine fallback_reason_counts keys are well-formed 'CLASS.function' strings",
]


def _check_invariants(out: dict) -> List[str]:
    fails: List[str] = []
    if not isinstance(out, dict):
        return ["INV-1"]
    p = out.get("price")
    if not (isinstance(p, (int, float)) and math.isfinite(p) and p > 0):
        fails.append("INV-2")
    c = out.get("confidence", 0.0)
    if not (isinstance(c, (int, float)) and math.isfinite(c) and 0.0 <= c <= 1.0):
        fails.append("INV-3")
    if str(out.get("direction", "")) not in ("LONG", "SHORT", "HOLD", "NEUTRAL", "WAIT"):
        fails.append("INV-4")
    a = out.get("market_data", {}).get("alpha") or out.get("alpha") or {}
    ac = a.get("confidence", 0.0)
    if not (isinstance(ac, (int, float)) and math.isfinite(ac) and 0.0 <= ac <= 1.0):
        fails.append("INV-5")
    if str(a.get("direction", "")) not in ("LONG", "SHORT", "NEUTRAL"):
        fails.append("INV-6")
    pa = a.get("prob_above")
    pb = a.get("prob_below")
    if pa is not None and pb is not None:
        if not (math.isfinite(pa) and math.isfinite(pb) and abs((pa + pb) - 1.0) < 1e-3):
            fails.append("INV-7")
    if not isinstance(out.get("allow_trade"), bool):
        fails.append("INV-8")
    sp = out.get("spread_pct", 0.0)
    if not (isinstance(sp, (int, float)) and math.isfinite(sp) and sp >= 0.0):
        fails.append("INV-9")
    ins = out.get("institutional_score", 0.0)
    if not (isinstance(ins, (int, float)) and math.isfinite(ins)):
        fails.append("INV-10")
    ofp = out.get("order_flow_pressure", 0.0)
    if not (isinstance(ofp, (int, float)) and math.isfinite(ofp)):
        fails.append("INV-11")
    oi = out.get("order_imbalance", 0.0)
    if not (isinstance(oi, (int, float)) and math.isfinite(oi) and -1.0001 <= oi <= 1.0001):
        fails.append("INV-12")
    cp = out.get("cascade_probability", 0.0)
    if not (isinstance(cp, (int, float)) and math.isfinite(cp) and -0.0001 <= cp <= 1.0001):
        fails.append("INV-13")
    smc = out.get("smc_signal") or {}
    if str(smc.get("signal", "NONE")) not in ("LONG", "SHORT", "NONE"):
        fails.append("INV-14")
    smcc = smc.get("confidence", 0)
    try:
        if not (0.0 <= float(smcc) <= 10.0):
            fails.append("INV-15")
    except Exception:
        fails.append("INV-15")
    ms = out.get("market_state") or {}
    if not isinstance(ms.get("state"), str) or not ms.get("state"):
        fails.append("INV-16")
    reg = out.get("regime") or {}
    rc = reg.get("confidence", 0.0)
    if not (isinstance(rc, (int, float)) and math.isfinite(rc) and -0.0001 <= rc <= 1.0001):
        fails.append("INV-17")
    if (out.get("open_interest_missing") or str(a.get("direction", "")) == "NEUTRAL") and out.get("allow_trade") is True:
        fails.append("INV-18")
    cs = out.get("confluence_score", 0.0)
    if not (isinstance(cs, (int, float)) and math.isfinite(cs) and -0.0001 <= cs <= 10.0001):
        fails.append("INV-19")
    comp = out.get("composite") or {}
    if str(comp.get("direction", out.get("direction"))) != str(out.get("direction", "")):
        fails.append("INV-20")
    # ---- Phase 3 additions ----
    # INV-21: alpha.confidence == 0.5 implies the fallback / default-alpha
    # path was taken. We accept either a fallback reason OR alpha.direction
    # being NEUTRAL (which is what _default_alpha() returns).
    try:
        if isinstance(ac, (int, float)) and abs(float(ac) - 0.5) < 1e-9:
            if not (
                str(out.get("reason", "")) == "run_all_engines_error"
                or str(a.get("direction", "")) == "NEUTRAL"
            ):
                fails.append("INV-21")
    except Exception:
        fails.append("INV-21")
    # INV-22: every fallback bar must report reason="run_all_engines_error".
    # Detect a fallback by the marker note that the fallback dict carries.
    try:
        sa = out.get("strategy_adjustment") or {}
        notes = sa.get("notes") if isinstance(sa, dict) else None
        is_fallback_shape = isinstance(notes, list) and "fallback" in notes
        if is_fallback_shape and str(out.get("reason", "")) != "run_all_engines_error":
            fails.append("INV-22")
    except Exception:
        fails.append("INV-22")
    # INV-23: source-of-truth confluence range (mirrors INV-19, kept
    # separate so OUTPUT_SCHEMA refactors don't silently weaken INV-19).
    try:
        cs23 = out.get("confluence_score", 0.0)
        if not (isinstance(cs23, (int, float)) and math.isfinite(cs23) and 0.0 <= float(cs23) <= 10.0001):
            fails.append("INV-23")
    except Exception:
        fails.append("INV-23")
    # INV-24: cache_hit_rate (only when reported) must be a finite [0,1].
    try:
        chr_ = out.get("cache_hit_rate")
        if chr_ is not None:
            if not (isinstance(chr_, (int, float)) and math.isfinite(chr_) and 0.0 <= float(chr_) <= 1.0):
                fails.append("INV-24")
    except Exception:
        fails.append("INV-24")
    # INV-25: well-formed fallback_reason_counts keys (only when reported).
    try:
        frc = out.get("fallback_reason_counts")
        if isinstance(frc, dict):
            for k in frc.keys():
                if not (isinstance(k, str) and "." in k and len(k.split(".", 1)) == 2):
                    fails.append("INV-25")
                    break
    except Exception:
        fails.append("INV-25")
    return fails


# ---------- main loop ----------

def _book_lookup(book_snaps: List[dict], book_ts_index: List[int], target_ms: int) -> dict:
    if not book_snaps:
        return {"bids": [], "asks": []}
    i = bisect.bisect_right(book_ts_index, target_ms) - 1
    if i < 0:
        i = 0
    s = book_snaps[i]
    return {"bids": list(s["bids"]), "asks": list(s["asks"])}


def run(num_bars: int = 1500, start_offset: int = 60, out_prefix: str = "baseline") -> dict:
    t0 = time.time()
    log.warning("[PHASE-3][STEP-1][HARNESS] loading data ...")
    bars1 = _load_ohlcv(DATA_1M)
    bars5 = _load_ohlcv(DATA_5M)
    bars15 = _load_ohlcv(DATA_15M)
    book = _load_book(DATA_BOOK)
    book_ts = [s["ts"] for s in book]
    log.warning("[PHASE-3][STEP-1][HARNESS] 1m=%d 5m=%d 15m=%d book=%d",
                len(bars1), len(bars5), len(bars15), len(book))

    end = min(start_offset + num_bars, len(bars1))
    minute_keys = [bars1[i][0] for i in range(start_offset, end)]
    trades_by_min = _bucket_trades_by_minute(DATA_TRADES, minute_keys)
    log.warning("[PHASE-3][STEP-1][HARNESS] bucketed trades for %d minutes", len(trades_by_min))

    bars5_ts = [b[0] for b in bars5]
    bars15_ts = [b[0] for b in bars15]

    # reset alpha state for determinism
    engine.reset_alpha_state()

    rec_path = os.path.join(OUT_DIR, f"{out_prefix}_records.csv")
    viol_path = os.path.join(OUT_DIR, f"{out_prefix}_invariant_violations.json")
    summary_path = os.path.join(OUT_DIR, f"{out_prefix}_summary.json")

    field_names = [
        "bar_idx", "ts_ms", "price",
        "allow_trade", "direction", "confidence",
        "alpha_dir", "alpha_conf", "alpha_pa", "alpha_pb",
        "smc_sig", "smc_conf",
        "market_state", "substate", "regime_type", "regime_conf",
        "spread_pct", "imbalance", "ofp", "ofp_pressure",
        "institutional_score", "ai_score", "confluence_score", "ob_score",
        "cascade_probability", "fvg_exists",
        "open_interest_missing", "reason",
        "violations",
    ]
    invariants_count: Dict[str, int] = {k.split(":")[0]: 0 for k in INVARIANT_DEFS}
    direction_count: Dict[str, int] = {}
    state_count: Dict[str, int] = {}
    allow_count = 0
    fallback_count = 0
    err_count = 0
    err_samples: List[str] = []
    confidences: List[float] = []
    alpha_confs: List[float] = []

    open_oi = 0.0  # we have no live OI; pass 0 -> oi_missing fail-closed branch
    oi_history: List[float] = []
    fr = 0.0001

    with open(rec_path, "w", newline="") as fcsv:
        w = csv.DictWriter(fcsv, fieldnames=field_names)
        w.writeheader()
        for bi in range(start_offset, end):
            bar = bars1[bi]
            ts_ms = bar[0]
            price = bar[4]
            recent_1m = bars1[max(0, bi - 60):bi + 1]
            j5 = bisect.bisect_right(bars5_ts, ts_ms)
            j15 = bisect.bisect_right(bars15_ts, ts_ms)
            recent_5m = bars5[max(0, j5 - 60):j5]
            recent_15m = bars15[max(0, j15 - 60):j15]
            ob = _book_lookup(book, book_ts, ts_ms)
            ob_prev = _book_lookup(book, book_ts, ts_ms - 60_000)
            trades = trades_by_min.get(ts_ms, [])

            ohlcv_dict = {
                "1m": recent_1m,
                "5m": recent_5m,
                "15m": recent_15m,
            }
            try:
                out = engine.run_all_engines(
                    orderbook=ob,
                    trades=trades,
                    price=price,
                    symbol="BTC/USDT",
                    cascade_prob=0.0,
                    recent_candles=recent_1m,
                    open_interest=open_oi,
                    funding_rate=fr,
                    orderbook_snapshots=[ob_prev, ob],
                    liquidation_events=[],
                    performance={},
                    volume_intelligence=None,
                    ohlcv=ohlcv_dict,
                    oi_history=oi_history or None,
                    current_oi=None,
                    market_state_detector=None,
                    reset_state_on_entry=False,
                )
            except Exception as exc:
                err_count += 1
                if len(err_samples) < 5:
                    err_samples.append(f"bar={bi} {type(exc).__name__}: {exc}")
                continue

            fails = _check_invariants(out)
            for f in fails:
                invariants_count[f] = invariants_count.get(f, 0) + 1
            d = str(out.get("direction", ""))
            direction_count[d] = direction_count.get(d, 0) + 1
            ms = (out.get("market_state") or {}).get("state", "?")
            state_count[ms] = state_count.get(ms, 0) + 1
            if out.get("allow_trade"):
                allow_count += 1
            if str(out.get("reason", "")) == "run_all_engines_error":
                fallback_count += 1
            confidences.append(float(out.get("confidence", 0.0)))
            a = out.get("market_data", {}).get("alpha") or out.get("alpha") or {}
            ac = float(a.get("confidence", 0.0)) if isinstance(a.get("confidence"), (int, float)) else 0.0
            alpha_confs.append(ac)
            smc = out.get("smc_signal") or {}
            reg = out.get("regime") or {}
            ofp_raw = out.get("order_flow_details") or {}
            row = {
                "bar_idx": bi,
                "ts_ms": ts_ms,
                "price": price,
                "allow_trade": bool(out.get("allow_trade")),
                "direction": d,
                "confidence": round(float(out.get("confidence", 0.0)), 6),
                "alpha_dir": str(a.get("direction", "")),
                "alpha_conf": round(ac, 6),
                "alpha_pa": round(float(a.get("prob_above", 0.5)), 6),
                "alpha_pb": round(float(a.get("prob_below", 0.5)), 6),
                "smc_sig": str(smc.get("signal", "NONE")),
                "smc_conf": float(smc.get("confidence", 0)),
                "market_state": ms,
                "substate": (out.get("market_state") or {}).get("substate", ""),
                "regime_type": str(reg.get("type", "")),
                "regime_conf": float(reg.get("confidence", 0.0) or 0.0),
                "spread_pct": float(out.get("spread_pct", 0.0) or 0.0),
                "imbalance": float(out.get("imbalance", 0.0) or 0.0),
                "ofp": float(out.get("order_flow_pressure", 0.0) or 0.0),
                "ofp_pressure": float(ofp_raw.get("pressure_score", 0.0) or 0.0),
                "institutional_score": float(out.get("institutional_score", 0.0) or 0.0),
                "ai_score": float(out.get("ai_score", 0.0) or 0.0),
                "confluence_score": float(out.get("confluence_score", 0.0) or 0.0),
                "ob_score": float(out.get("order_block_score", 0.0) or 0.0),
                "cascade_probability": float(out.get("cascade_probability", 0.0) or 0.0),
                "fvg_exists": bool((out.get("fvg") or {}).get("exists", False)),
                "open_interest_missing": bool(out.get("open_interest_missing", False)),
                "reason": str(out.get("reason", "")),
                "violations": ";".join(fails),
            }
            w.writerow(row)

    elapsed = time.time() - t0
    # ---- AUDIT-FIX L-3 + Phase 2 #9: surface engine-side error counter and
    # fallback_reason_counts in the summary so reporting stays honest. We
    # never report errors==0 while fallback_count>0 because
    # _engine_error_count is read directly off the live engine attribute.
    try:
        _engine_error_count = int(getattr(engine.run_all_engines, "_error_count", 0))
    except Exception:
        _engine_error_count = 0
    try:
        _engine_reason_counts = dict(
            getattr(engine.run_all_engines, "_fallback_reason_counts", {}) or {}
        )
    except Exception:
        _engine_reason_counts = {}
    summary = {
        "out_prefix": out_prefix,
        "bars_attempted": end - start_offset,
        "bars_recorded": (end - start_offset) - err_count,
        "errors": err_count,
        "error_samples": err_samples,
        "elapsed_sec": round(elapsed, 2),
        "allow_trade_rate": (allow_count / max(end - start_offset, 1)),
        "fallback_rate": (fallback_count / max(end - start_offset, 1)),
        "fallback_count": fallback_count,
        "engine_error_count": _engine_error_count,
        "fallback_reason_counts": _engine_reason_counts,
        "direction_distribution": direction_count,
        "market_state_distribution": state_count,
        "confidence_stats": _stats(confidences),
        "alpha_confidence_stats": _stats(alpha_confs),
        "invariants": INVARIANT_DEFS,
        "invariant_violation_counts": invariants_count,
    }
    with open(viol_path, "w") as f:
        json.dump({"violations_per_invariant": invariants_count, "definitions": INVARIANT_DEFS}, f, indent=2)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.warning("[PHASE-3][STEP-9][HARNESS] elapsed=%.1fs records=%s violations=%s",
                elapsed, summary["bars_recorded"], sum(invariants_count.values()))
    print(json.dumps(summary, indent=2))
    return summary


def _stats(xs: List[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs2 = sorted(xs)
    n = len(xs2)
    return {
        "n": n,
        "min": xs2[0],
        "max": xs2[-1],
        "mean": sum(xs2) / n,
        "p50": xs2[n // 2],
        "p90": xs2[int(0.9 * (n - 1))],
        "p99": xs2[int(0.99 * (n - 1))],
        "zero_frac": sum(1 for x in xs2 if x == 0.0) / n,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=int(os.environ.get("AUDIT_BARS", "1500")))
    ap.add_argument("--start", type=int, default=60)
    ap.add_argument("--prefix", type=str, default="baseline")
    args = ap.parse_args()
    sys.exit(0 if run(args.bars, args.start, args.prefix) else 1)
