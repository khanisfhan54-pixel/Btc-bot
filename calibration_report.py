"""Generate compact in-memory conviction calibration report."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np

from generate_regime_audit_reports import run_all

METRICS = ("conviction", "edge_score_raw", "uncertainty", "directional_margin_val")
GROUPS = ("accepted_TREND", "rejected_TREND", "accepted_BEAR", "rejected_BEAR")
PCTS = (10, 25, 50, 75, 90, 95)


def stat(values):
    vals = [float(v) for v in values]
    if not vals:
        return {"count": 0, "mean": None, "median": None, **{f"p{p}": None for p in PCTS}}
    arr = np.asarray(vals, dtype=float)
    return {
        "count": len(vals),
        "mean": float(mean(vals)),
        "median": float(median(vals)),
        **{f"p{p}": float(np.percentile(arr, p)) for p in PCTS},
    }


def fmt(value):
    return "n/a" if value is None else f"{value:.6f}"


def classify(record):
    raw = record.get("raw_regime")
    before = record.get("confirmed_before_switch")
    after = record.get("confirmed_after_switch")
    if raw == "TREND" and after == "TREND":
        return "accepted_TREND"
    if raw == "TREND" and before == "TREND" and after != "TREND":
        return "rejected_TREND"
    if raw == "BEAR" and after == "BEAR":
        return "accepted_BEAR"
    if raw == "BEAR" and before == "BEAR" and after != "BEAR":
        return "rejected_BEAR"
    return None


def main():
    audit, _supp = run_all()
    groups = {group: defaultdict(list) for group in GROUPS}
    for record in audit:
        group = classify(record)
        if group is None:
            continue
        for metric in METRICS:
            groups[group][metric].append(float(record.get(metric, -1.0)))

    lines = ["# Conviction Calibration Report", "", f"Audit records evaluated in memory: {len(audit)}", ""]
    for group in GROUPS:
        lines.extend([
            f"## {group}",
            "",
            "| metric | count | mean | median | p10 | p25 | p50 | p75 | p90 | p95 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for metric in METRICS:
            s = stat(groups[group][metric])
            lines.append(
                f"| {metric} | {s['count']} | {fmt(s['mean'])} | {fmt(s['median'])} | "
                f"{fmt(s['p10'])} | {fmt(s['p25'])} | {fmt(s['p50'])} | {fmt(s['p75'])} | "
                f"{fmt(s['p90'])} | {fmt(s['p95'])} |"
            )
        lines.append("")
    Path("conviction_calibration_report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
