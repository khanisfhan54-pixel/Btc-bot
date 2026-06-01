#!/usr/bin/env python3
"""Run BacktestEngine from generated L1 feature parquets.

VPS backtest example:
    python3 run_backtest_from_features.py \
      --features-1m /home/ubuntu/btc_bot_data/processed/features_1m.parquet \
      --features-5m /home/ubuntu/btc_bot_data/processed/features_5m.parquet

This adapter reads the parquets produced by ``preprocess/build_btc_feature_parquets.py``
and calls only public BacktestEngine APIs. It does not route BookTicker through
the L2 CSV loader and does not use live exchange access. ``ofi_zscore`` and
``ofi_norm`` are L1 proxy aliases, not true multi-level L2 OFI.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

REQUIRED_COLUMNS = [
    "symbol", "bar_interval", "timestamp_ms", "bar_start_ts_ms", "bar_end_ts_ms",
    "feature_available_ts_ms", "open", "high", "low", "close", "volume", "quote_volume",
    "buy_volume", "sell_volume", "buy_quote_volume", "sell_quote_volume",
    "trade_count", "buy_trade_count", "sell_trade_count",
    "log_ret", "log_ret_close", "ofi_zscore", "ofi_norm", "vol_z", "spread_bps",
    "liquidity_score", "fill_prob", "fill_probability", "impact_cost_bps",
    "bid_price", "ask_price", "bid_qty", "ask_qty", "book_imbalance",
    "last_trade_ts_ms", "last_book_event_ts_ms", "data_quality_flags",
]


@dataclass(frozen=True)
class FeatureBookSnapshot:
    """L1 BookTicker snapshot for BacktestEngine; ofi_z is an L1 proxy z-score."""

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
    validate_feature_rows(rows, path)
    rows.sort(key=lambda r: int(r["timestamp_ms"]))
    return rows


def _as_float(row: Dict[str, Any], key: str) -> float:
    value = row.get(key)
    return 0.0 if value is None else float(value)


def validate_feature_rows(rows: Sequence[Dict[str, Any]], path: str = "<memory>") -> Dict[str, int]:
    if not rows:
        raise ValueError(f"{path} contains no feature rows")
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise ValueError(f"{path} missing required feature columns: {missing}")
    prev_ts: Optional[int] = None
    warmup = 0
    for i, row in enumerate(rows):
        ts = int(row["timestamp_ms"])
        bs = int(row["bar_start_ts_ms"])
        be = int(row["bar_end_ts_ms"])
        if prev_ts is not None and ts <= prev_ts:
            raise ValueError(f"{path}: timestamps are not strictly increasing at row {i}")
        prev_ts = ts
        checks = [
            (ts == bs, "timestamp_ms must equal bar_start_ts_ms"),
            (int(row["feature_available_ts_ms"]) == be, "feature_available_ts_ms must equal bar_end_ts_ms"),
            (_as_float(row, "open") > 0 and _as_float(row, "close") > 0, "open/close must be positive"),
            (_as_float(row, "high") >= _as_float(row, "low"), "high < low"),
            (_as_float(row, "spread_bps") >= 0, "negative spread_bps"),
            (_as_float(row, "bid_price") > 0 and _as_float(row, "ask_price") > 0, "bid/ask must be positive; adapter refuses synthetic fallback"),
            (_as_float(row, "ask_price") >= _as_float(row, "bid_price"), "ask_price < bid_price"),
            (-1.0 <= _as_float(row, "book_imbalance") <= 1.0, "book_imbalance out of range"),
            (abs(_as_float(row, "volume") - (_as_float(row, "buy_volume") + _as_float(row, "sell_volume"))) < 1e-8, "volume mismatch"),
            (abs(_as_float(row, "quote_volume") - (_as_float(row, "buy_quote_volume") + _as_float(row, "sell_quote_volume"))) < 1e-5, "quote volume mismatch"),
            (int(row["trade_count"]) == int(row["buy_trade_count"]) + int(row["sell_trade_count"]), "trade count mismatch"),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(f"{path}: {msg} at row {i} ts={ts}")
        if row.get("last_trade_ts_ms") is not None and int(row["last_trade_ts_ms"]) >= be:
            raise ValueError(f"{path}: last_trade_ts_ms lookahead at row {i}")
        if row.get("last_book_event_ts_ms") is not None and int(row["last_book_event_ts_ms"]) >= be:
            raise ValueError(f"{path}: last_book_event_ts_ms lookahead at row {i}")
        if "WARMUP" in str(row.get("data_quality_flags", "")):
            warmup += 1
    return {"rows": len(rows), "warmup_rows": warmup}


def _to_engine_inputs(rows: Sequence[Dict[str, Any]]) -> Tuple[List[list], List[Optional[FeatureBookSnapshot]]]:
    bars: List[list] = []
    snaps: List[Optional[FeatureBookSnapshot]] = []
    for row in rows:
        ts = int(row["timestamp_ms"])
        bars.append([ts, _as_float(row, "open"), _as_float(row, "high"), _as_float(row, "low"), _as_float(row, "close"), _as_float(row, "volume")])
        # Always pass the parquet L1 snapshot to avoid BacktestEngine synthetic snapshot fallback.
        # Stale-but-present snapshots retain liquidity_score=0 from preprocessing.
        snaps.append(FeatureBookSnapshot(
                timestamp=ts,
                bid_price=_as_float(row, "bid_price"),
                ask_price=_as_float(row, "ask_price"),
                bid_qty=_as_float(row, "bid_qty"),
                ask_qty=_as_float(row, "ask_qty"),
                spread_bps=_as_float(row, "spread_bps"),
                imbalance=_as_float(row, "book_imbalance"),
                ofi_z=_as_float(row, "ofi_zscore"),
                liquidity_score=_as_float(row, "liquidity_score"),
                fill_prob=_as_float(row, "fill_prob"),
                impact_cost_bps=_as_float(row, "impact_cost_bps"),
                log_ret=_as_float(row, "log_ret"),
                vol_z=_as_float(row, "vol_z"),
        ))
    return bars, snaps


def run_one(path: str, label: str, initial_balance: float, legacy_mode: bool) -> Dict[str, Any]:
    rows = _read_parquet(path)
    bars, snaps = _to_engine_inputs(rows)
    from backtest_engine import BacktestConfig, BacktestEngine

    engine = BacktestEngine(BacktestConfig(initial_balance=initial_balance, legacy_mode=legacy_mode))
    result = engine.run_backtest(bars, initial_balance=initial_balance, book_features=snaps)
    result["source"] = path
    result["input_rows"] = len(rows)
    result["legacy_mode"] = bool(legacy_mode)
    result["l2_loader_used_by_adapter"] = False
    result["required_feature_columns_read"] = [
        "log_ret", "ofi_zscore", "vol_z", "spread_bps", "liquidity_score", "fill_prob", "impact_cost_bps"
    ]
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run public BacktestEngine APIs from generated L1 feature parquets")
    ap.add_argument("--features-1m", required=True)
    ap.add_argument("--features-5m", required=True)
    ap.add_argument("--initial-balance", type=float, default=10_000.0)
    ap.add_argument("--legacy-mode", action="store_true", help="Explicit diagnostic mode; default uses production-valid BacktestEngine configuration")
    ap.add_argument("--output", default="backtest_from_features_summary.json")
    args = ap.parse_args()
    rows_1m = _read_parquet(args.features_1m)
    rows_5m = _read_parquet(args.features_5m)
    stats_1m = validate_feature_rows(rows_1m, args.features_1m)
    stats_5m = validate_feature_rows(rows_5m, args.features_5m)
    summary = {
        "mode": "feature_parquet_l1_only",
        "l2_loader_used_by_adapter": False,
        "live_exchange_access": False,
        "legacy_mode": bool(args.legacy_mode),
        "ofi_semantics": "ofi_zscore is l1_order_flow_proxy_z, not true L2 OFI",
        "validation": {"1m": stats_1m, "5m": stats_5m},
        "results": {
            "1m": run_one(args.features_1m, "1m", args.initial_balance, args.legacy_mode),
            "5m": run_one(args.features_5m, "5m", args.initial_balance, args.legacy_mode),
        },
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps({"written": args.output, "validation": summary["validation"]}, indent=2))


if __name__ == "__main__":
    main()
