from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict

from .io import atomic_write_json, ensure_dir


def write_reports(dataset: Dict[str, Any], labels_payload: Dict[str, Any], model_manifest: Dict[str, Any], wf: Dict[str, Any], out_dir: str, *, report_version: str = "dev") -> Dict[str, str]:
    labels = [x["label"] for x in labels_payload.get("labels", []) if x.get("label") is not None]
    regimes = Counter(str(s.get("regime_label", "unknown")) for s in dataset.get("samples", []))
    metrics = wf.get("metrics", {})
    required = ["sharpe_ratio", "max_drawdown", "win_rate", "number_of_trades", "brier_score", "expected_calibration_error"]
    missing = [k for k in required if k not in metrics]
    if missing:
        raise RuntimeError(f"SHPE report missing required metrics: {missing}")
    payload = {
        "target_definition": dataset.get("target_definition"),
        "dataset_size": len(dataset.get("samples", [])),
        "date_range": [dataset["samples"][0]["timestamp_utc"], dataset["samples"][-1]["timestamp_utc"]] if dataset.get("samples") else [],
        "class_balance": dict(Counter(str(x) for x in labels)),
        "per_regime_counts": dict(regimes),
        "walk_forward_configuration": wf.get("walk_forward_config"),
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "number_of_trades": metrics["number_of_trades"],
        "calibration_metrics": {"brier_score": metrics["brier_score"], "expected_calibration_error": metrics["expected_calibration_error"]},
        "artifact_paths": {"model": model_manifest.get("model_path"), "model_manifest": model_manifest, "walk_forward": wf.get("path")},
        "fallback_comparison": "No degraded fallback benchmark edge claimed; validate with larger market data before live use.",
    }
    base = ensure_dir(os.path.join(out_dir, report_version))
    json_path = os.path.join(base, "report.json")
    md_path = os.path.join(base, "report.md")
    atomic_write_json(payload, json_path)
    md = ["# SHPE offline workflow report", "", f"Target: `{payload['target_definition']['version']}`", f"Dataset rows: {payload['dataset_size']}", f"Date range: {payload['date_range']}", f"Class balance: {payload['class_balance']}", f"Per-regime counts: {payload['per_regime_counts']}", "", "## Walk-forward metrics", f"- Sharpe ratio: {payload['sharpe_ratio']}", f"- Max drawdown: {payload['max_drawdown']}", f"- Win rate: {payload['win_rate']}", f"- Number of trades: {payload['number_of_trades']}", f"- Brier score: {payload['calibration_metrics']['brier_score']}", f"- Expected calibration error: {payload['calibration_metrics']['expected_calibration_error']}", "", payload["fallback_comparison"]]
    with open(md_path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    os.replace(md_path + ".tmp", md_path)
    return {"json": json_path, "markdown": md_path}
