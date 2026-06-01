#!/usr/bin/env python3
"""Run the existing backtest engine from precomputed L1 feature parquets.

This adapter is intentionally small and safe: it reads generated parquet feature
rows, validates the required columns, passes native 1m/5m OHLCV bars and L1
BookTicker-derived compatibility fields to BacktestEngine, and never calls the
L2 CSV loader or live exchange APIs.  ``ofi_zscore`` is the L1 proxy z-score
created by preprocess/build_btc_feature_parquets.py, not true multi-level L2 OFI.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


REQUIRED_COLUMNS = [
    "timestamp_ms", "open", "high", "low", "close", "volume",
    "log_ret", "ofi_zscore", "vol_z", "spread_bps", "liquidity_score",
    "fill_prob", "impact_cost_bps", "bid_price", "ask_price", "bid_qty", "ask_qty",
]


@dataclass(frozen=True)
class FeatureBookSnapshot:
    timestamp: int
    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float
    spread_bps: float
    imbalance: float
    ofi_z: float
    liquidity_score: float
    fill_prob: float
    impact_cost_bps: float
    log_ret: float
    vol_z: float


def _read_parquet(path: str) -> List[Dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to read feature parquet files. Install requirements.txt on the VPS.") from exc
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    rows = pq.read_table(path).to_pylist()
    missing = [c for c in REQUIRED_COLUMNS if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"{path} missing required feature columns: {missing}")
    rows.sort(key=lambda r: int(r["timestamp_ms"]))
    return rows


def _to_engine_inputs(rows: List[Dict[str, Any]]) -> tuple[List[list], List[Optional[FeatureBookSnapshot]]]:
    bars: List[list] = []
    snaps: List[Optional[FeatureBookSnapshot]] = []
    prev_ts: Optional[int] = None
    for i, r in enumerate(rows):
        ts = int(r["timestamp_ms"])
        if prev_ts is not None and ts <= prev_ts:
            raise ValueError(f"timestamps are not strictly increasing at row {i}")
        prev_ts = ts
        bars.append([ts, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"])])
        snaps.append(FeatureBookSnapshot(
            timestamp=ts,
            bid_price=float(r["bid_price"]), ask_price=float(r["ask_price"]),
            bid_qty=float(r["bid_qty"]), ask_qty=float(r["ask_qty"]),
            spread_bps=float(r["spread_bps"]), imbalance=float(r.get("imbalance", 0.0)),
            ofi_z=float(r["ofi_zscore"]), liquidity_score=float(r["liquidity_score"]),
            fill_prob=float(r["fill_prob"]), impact_cost_bps=float(r["impact_cost_bps"]),
            log_ret=float(r["log_ret"]), vol_z=float(r["vol_z"]),
        ))
    return bars, snaps


def run_one(path: str, label: str, initial_balance: float) -> Dict[str, Any]:
    rows = _read_parquet(path)
    bars, snaps = _to_engine_inputs(rows)
    from backtest_engine import BacktestConfig, BacktestEngine
    engine = BacktestEngine(BacktestConfig(initial_balance=initial_balance, legacy_mode=True))
    result = engine._run_single_pass(bars, initial_balance=initial_balance, label=f"features_{label}", book_features=snaps)
    result["source"] = path
    result["input_rows"] = len(rows)
    result["required_feature_columns_read"] = [
        "log_ret", "ofi_zscore", "vol_z", "spread_bps", "liquidity_score", "fill_prob", "impact_cost_bps"
    ]
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run backtest from generated L1 feature parquets without L2 loader/live access")
    ap.add_argument("--features-1m", required=True)
    ap.add_argument("--features-5m", required=True)
    ap.add_argument("--initial-balance", type=float, default=10_000.0)
    ap.add_argument("--output", default="backtest_from_features_summary.json")
    args = ap.parse_args()
    summary = {
        "mode": "feature_parquet_l1_only",
        "l2_loader_used": False,
        "live_exchange_access": False,
        "ofi_semantics": "ofi_zscore is l1_order_flow_proxy_z, not true L2 OFI",
        "results": {
            "1m": run_one(args.features_1m, "1m", args.initial_balance),
            "5m": run_one(args.features_5m, "5m", args.initial_balance),
        },
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps({"written": args.output, "rows": {k: v["input_rows"] for k, v in summary["results"].items()}}, indent=2))


if __name__ == "__main__":
    main()
