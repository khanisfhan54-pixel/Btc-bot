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
import tempfile
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
    "book_stale_ms", "has_book_data", "last_trade_ts_ms", "last_book_event_ts_ms", "data_quality_flags",
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
    ofi_norm: float
    liquidity_score: float
    fill_prob: float
    impact_cost_bps: float
    log_ret: float
    vol_z: float
    feature_available_ts_ms: int


def _read_parquet(path: str) -> List[Dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to read feature parquet files. Install requirements.txt on the VPS.") from exc
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    rows = pq.read_table(path).to_pylist()
    rows.sort(key=lambda r: int(r["timestamp_ms"]))
    validate_feature_rows(rows, path)
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
            (row.get("book_stale_ms") is not None, "book_stale_ms must not be null"),
            (int(row["book_stale_ms"]) >= -1, "book_stale_ms must be >= -1"),
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
        if row.get("last_book_event_ts_ms") is None:
            if int(row["book_stale_ms"]) != -1 or bool(row.get("has_book_data")):
                raise ValueError(f"{path}: missing book row must use book_stale_ms=-1 and has_book_data=False at row {i}")
        elif int(row["book_stale_ms"]) != be - int(row["last_book_event_ts_ms"]):
            raise ValueError(f"{path}: book_stale_ms inconsistent with last_book_event_ts_ms at row {i}")
        if "WARMUP" in str(row.get("data_quality_flags", "")):
            warmup += 1
    return {"rows": len(rows), "warmup_rows": warmup}


def _to_engine_inputs(rows: Sequence[Dict[str, Any]]) -> Tuple[List[list], List[Optional[FeatureBookSnapshot]]]:
    bars: List[list] = []
    snaps: List[Optional[FeatureBookSnapshot]] = []
    for row in rows:
        ts = int(row["timestamp_ms"])
        feature_ts = int(row["feature_available_ts_ms"])
        # BacktestEngine timestamps represent when the feature row is actionable.
        # The parquet keeps timestamp_ms == bar_start_ts_ms for bot compatibility,
        # but the adapter uses feature_available_ts_ms (bar end) to avoid trading
        # on a completed bar before it exists.
        bars.append([feature_ts, _as_float(row, "open"), _as_float(row, "high"), _as_float(row, "low"), _as_float(row, "close"), _as_float(row, "volume")])
        # Always pass the parquet L1 snapshot to avoid BacktestEngine synthetic snapshot fallback.
        # Stale-but-present snapshots retain liquidity_score=0 from preprocessing.
        snaps.append(FeatureBookSnapshot(
                timestamp=feature_ts,
                bid_price=_as_float(row, "bid_price"),
                ask_price=_as_float(row, "ask_price"),
                bid_qty=_as_float(row, "bid_qty"),
                ask_qty=_as_float(row, "ask_qty"),
                spread_bps=_as_float(row, "spread_bps"),
                imbalance=_as_float(row, "book_imbalance"),
                ofi_z=_as_float(row, "ofi_zscore"),
                ofi_norm=_as_float(row, "ofi_norm"),
                liquidity_score=_as_float(row, "liquidity_score"),
                fill_prob=_as_float(row, "fill_prob"),
                impact_cost_bps=_as_float(row, "impact_cost_bps"),
                log_ret=_as_float(row, "log_ret"),
                vol_z=_as_float(row, "vol_z"),
                feature_available_ts_ms=feature_ts,
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


def _write_smoke_raw_csvs(tmpdir: str, n_minutes: int) -> Tuple[str, str]:
    import csv

    book_path = os.path.join(tmpdir, "BTCUSDT-smoke-bookTicker.csv")
    trade_path = os.path.join(tmpdir, "BTCUSDT-smoke-aggTrades.csv")
    start_ms = 1_704_067_200_000
    with open(book_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["E", "b", "B", "a", "A"])
        w.writeheader()
        for i in range(n_minutes):
            ts = start_ms + i * 60_000 + 100
            mid = 43_000.0 + i * 2.0
            w.writerow({"E": ts, "b": f"{mid - 0.5:.2f}", "B": "2.0", "a": f"{mid + 0.5:.2f}", "A": "2.5"})
    with open(trade_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["a", "T", "p", "q", "m"])
        w.writeheader()
        for i in range(n_minutes):
            base = start_ms + i * 60_000
            price = 43_000.0 + i * 2.0
            w.writerow({"a": str(i * 2), "T": base + 1_000, "p": f"{price:.2f}", "q": "0.010", "m": "false"})
            w.writerow({"a": str(i * 2 + 1), "T": base + 30_000, "p": f"{price + 1.0:.2f}", "q": "0.005", "m": "true"})
    return book_path, trade_path


def run_smoke_test(initial_balance: float, legacy_mode: bool, n_minutes: int = 80) -> Dict[str, Any]:
    """Deterministic raw CSV -> parquet -> public BacktestEngine smoke test; no live access."""
    from preprocess.build_btc_feature_parquets import INTERVALS_MS, build_interval, read_books, read_trades, write_parquet

    with tempfile.TemporaryDirectory(prefix="btc_l1_feature_smoke_") as tmpdir:
        book_path, trade_path = _write_smoke_raw_csvs(tmpdir, n_minutes=n_minutes)
        outdir = os.path.join(tmpdir, "processed")
        trade_bars, trade_counts, trade_bounds = read_trades(trade_path, INTERVALS_MS)
        books, book_counts, book_bounds = read_books(book_path)
        source_min = min(x for x in [trade_bounds[0], book_bounds[0]] if x is not None)
        source_max = max(x for x in [trade_bounds[1], book_bounds[1]] if x is not None)
        paths: Dict[str, str] = {}
        for name, ms in INTERVALS_MS.items():
            rows, _stats = build_interval("BTCUSDT", name, ms, trade_bars[name], books, 120_000, 20, source_min, source_max)
            path = os.path.join(outdir, f"features_{name}.parquet")
            write_parquet(rows, path)
            paths[name] = path
        result_1m = run_one(paths["1m"], "smoke_1m", initial_balance, legacy_mode)
        result_5m = run_one(paths["5m"], "smoke_5m", initial_balance, legacy_mode)
        if not isinstance(result_1m, dict) or not isinstance(result_5m, dict):
            raise RuntimeError("BacktestEngine did not return result dictionaries during smoke test")
        summary = {
            "raw_trade_rows": trade_counts["raw"],
            "raw_book_rows": book_counts["raw"],
            "rows_1m": result_1m["input_rows"],
            "rows_5m": result_5m["input_rows"],
            "legacy_mode": bool(legacy_mode),
        }
        print(f"SMOKE TEST OK: raw CSV -> parquet -> BacktestEngine public API ({summary})")
        return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Run public BacktestEngine APIs from generated L1 feature parquets")
    ap.add_argument("--features-1m")
    ap.add_argument("--features-5m")
    ap.add_argument("--initial-balance", type=float, default=10_000.0)
    ap.add_argument("--legacy-mode", action="store_true", help="Explicit diagnostic mode; default uses production-valid BacktestEngine configuration")
    ap.add_argument("--output", default="backtest_from_features_summary.json")
    ap.add_argument("--smoke-test", action="store_true", help="Run a deterministic raw CSV -> parquet -> public BacktestEngine smoke test")
    ap.add_argument("--smoke-minutes", type=int, default=80, help="Number of one-minute sample bars for --smoke-test")
    args = ap.parse_args()
    if args.smoke_test:
        try:
            run_smoke_test(args.initial_balance, args.legacy_mode, n_minutes=args.smoke_minutes)
        except Exception as exc:
            raise SystemExit(f"SMOKE TEST FAILED: {exc}") from exc
        return
    if not (args.features_1m and args.features_5m):
        raise SystemExit("--features-1m and --features-5m are required unless --smoke-test is used")
    rows_1m = _read_parquet(args.features_1m)
    rows_5m = _read_parquet(args.features_5m)
    stats_1m = validate_feature_rows(rows_1m, args.features_1m)
    stats_5m = validate_feature_rows(rows_5m, args.features_5m)
    print(json.dumps({
        "input_rows": {"1m": len(rows_1m), "5m": len(rows_5m)},
        "validation": {"1m": stats_1m, "5m": stats_5m},
        "legacy_mode": bool(args.legacy_mode),
    }, indent=2))
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
    metrics = {k: {mk: v.get(mk) for mk in ("total_trades", "win_rate", "pnl", "max_drawdown", "sharpe") if mk in v} for k, v in summary["results"].items()}
    print(json.dumps({"written": args.output, "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
