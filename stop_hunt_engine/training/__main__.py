from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any, Dict, List

from .dataset_builder import build_dataset, load_feature_rows_many
from .io import read_json
from .label_generator import generate_labels
from .report import write_reports
from .research_audit import run_research_audit
from .target import DEFAULT_TARGET
from .trainer import train_and_save
from .walk_forward import run_walk_forward


def _smoke_rows(n: int = 80) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 1_704_067_200_000
    interval = 300_000
    for i in range(n):
        base = 43_000.0 + math.sin(i / 3.0) * 40.0 + i * 1.5
        open_ = base
        close = base + math.sin(i) * 6.0
        high = max(open_, close) + 10.0
        low = min(open_, close) - 10.0
        if i in {24, 38, 55, 70}:
            prev_high = max(r["high"] for r in rows[-20:]) if len(rows) >= 20 else high
            high = prev_high + 8.0
            close = prev_high - 2.0
        if i in {31, 47, 63}:
            prev_low = min(r["low"] for r in rows[-20:]) if len(rows) >= 20 else low
            low = prev_low - 8.0
            close = prev_low + 2.0
        ts = start + i * interval
        rows.append({
            "symbol": "BTCUSDT", "bar_interval": "5m", "timestamp_ms": ts, "bar_start_ts_ms": ts, "bar_end_ts_ms": ts + interval, "feature_available_ts_ms": ts + interval,
            "open": round(open_, 6), "high": round(max(high, open_, close), 6), "low": round(min(low, open_, close), 6), "close": round(close, 6), "volume": round(10.0 + (i % 7), 6),
            "ofi_zscore": math.sin(i / 5.0), "book_imbalance": max(-1.0, min(1.0, math.sin(i / 4.0))), "volatility": 0.01 + abs(math.sin(i / 8.0)) * 0.01,
            "regime": "range" if i % 2 == 0 else "trend", "regime_timestamp_ms": ts + interval, "last_trade_ts_ms": ts + interval - 1_000, "last_book_event_ts_ms": ts + interval - 1_000,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the full offline SHPE ML workflow")
    ap.add_argument("--features-5m", nargs="+", help="Existing 5m BTCUSDT feature parquet/json file(s) or glob(s) built by preprocess/build_btc_feature_parquets.py")
    ap.add_argument("--smoke-test", action="store_true", help="Use deterministic local feature rows; no live or network access")
    ap.add_argument("--artifact-root", default="artifacts/shpe")
    ap.add_argument("--run-version", default="dev")
    ap.add_argument("--min-train", type=int, default=12)
    ap.add_argument("--test-size", type=int, default=4)
    ap.add_argument("--research-audit", action="store_true", help="Generate research-only SHPE validation audit artifacts and final verdict")
    args = ap.parse_args()
    if not args.smoke_test and not args.features_5m:
        raise SystemExit("provide --features-5m or --smoke-test")
    rows = _smoke_rows() if args.smoke_test else load_feature_rows_many(args.features_5m)
    dataset_res = build_dataset(rows, os.path.join(args.artifact_root, "datasets"), dataset_version=args.run_version)
    dataset = dataset_res["payload"]
    labels_res = generate_labels(dataset, os.path.join(args.artifact_root, "labels"), labels_version=args.run_version)
    labels = labels_res["payload"]
    model_res = train_and_save(dataset, labels, os.path.join(args.artifact_root, "models"), model_version=f"shpe.v1.0.0-{args.run_version}")
    wf = run_walk_forward(dataset, labels, os.path.join(args.artifact_root, "reports", args.run_version), min_train=args.min_train, test_size=args.test_size)
    reports = write_reports(dataset, labels, model_res["manifest"], wf, os.path.join(args.artifact_root, "reports"), report_version=args.run_version)
    research = run_research_audit(dataset, labels, wf, args.artifact_root, min_train=args.min_train, test_size=args.test_size) if args.research_audit else None
    out = {"dataset": dataset_res["path"], "labels": labels_res["path"], "model": model_res["model_path"], "manifest": model_res["manifest_path"], "walk_forward": wf["path"], "reports": reports, "research_audit": research, "metrics": wf["metrics"], "target_definition_version": DEFAULT_TARGET.version}
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
