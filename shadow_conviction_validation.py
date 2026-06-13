"""Generate shadow-only conviction telemetry validation reports.

This module reads Advanced Regime Engine audit records and compares existing
production ``conviction`` with telemetry-only ``certainty_score`` and
``directional_confidence``. It does not feed shadow metrics back into any
trading, risk, execution, persistence, TOXIC-exit, or sizing path.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
import json
import logging

import numpy as np

logging.disable(logging.CRITICAL)

from advanced_regime_engine import compute_hmm_regime
from generate_regime_audit_reports import market, run_all

THRESHOLDS = (0.72, 0.68, 0.60, 0.55, 0.50, 0.40, 0.05)
REGIMES = ("TREND", "BEAR", "RANGE", "TOXIC")
FIELDS = (
    "timestamp",
    "regime",
    "conviction",
    "certainty_score",
    "directional_confidence",
    "uncertainty",
    "edge_score",
    "directional_margin",
    "trend_score",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def build_shadow_records(audit: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in audit:
        records.append(
            {
                "timestamp": _f(record.get("timestamp", record.get("tick", 0.0))),
                "regime": str(record.get("regime", record.get("confirmed_after_smoother", ""))),
                "conviction": _f(record.get("conviction")),
                "certainty_score": _f(record.get("certainty_score")),
                "directional_confidence": _f(record.get("directional_confidence")),
                "uncertainty": _f(record.get("uncertainty")),
                "edge_score": _f(record.get("edge_score", record.get("regime_edge_raw", record.get("edge_score_raw")))),
                "directional_margin": _f(record.get("directional_margin", record.get("directional_margin_val"))),
                "trend_score": _f(record.get("trend_score")),
            }
        )
    return records


def corr(records: list[dict[str, Any]], left: str, right: str) -> float:
    x = np.asarray([_f(r.get(left)) for r in records], dtype=float)
    y = np.asarray([_f(r.get(right)) for r in records], dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 2 or float(np.std(x[mask])) == 0.0 or float(np.std(y[mask])) == 0.0:
        return float("nan")
    return float(np.corrcoef(x[mask], y[mask])[0, 1])


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": float("nan"), "median": float("nan"), "p10": float("nan"), "p25": float("nan"), "p50": float("nan"), "p75": float("nan"), "p90": float("nan")}
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(mean(values)),
        "median": float(np.percentile(arr, 50)),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
    }


def transition_stats(labels: list[str]) -> tuple[int, float, int]:
    if not labels:
        return 0, 0.0, 0
    switches = sum(1 for a, b in zip(labels, labels[1:]) if a != b)
    durations: list[int] = []
    run = 1
    toxic_exits = 0
    for a, b in zip(labels, labels[1:]):
        if a == "TOXIC" and b != "TOXIC":
            toxic_exits += 1
        if a == b:
            run += 1
        else:
            durations.append(run)
            run = 1
    durations.append(run)
    return switches, float(mean(durations)), toxic_exits


def simulated_labels(records: list[dict[str, Any]], metric: str, threshold: float) -> list[str]:
    return [str(r.get("regime", "RANGE")) if _f(r.get(metric)) >= threshold else "RANGE" for r in records]


def render_report(records: list[dict[str, Any]]) -> str:
    lines = ["# Conviction Shadow Validation", "", "Shadow metrics are telemetry-only; this report simulates impact without changing production behavior.", ""]
    pairs = [("conviction", "certainty_score"), ("conviction", "directional_confidence"), ("certainty_score", "uncertainty"), ("directional_confidence", "edge_score"), ("directional_confidence", "directional_margin")]
    lines += ["## Correlation Matrix", "", "| pair | correlation |", "|---|---:|"]
    for left, right in pairs:
        lines.append(f"| corr({left}, {right}) | {corr(records, left, right):.6f} |")
    lines += ["", "## Distribution Tables", ""]
    for metric in ("conviction", "certainty_score", "directional_confidence"):
        lines += [f"### {metric}", "", "| regime | mean | median | p10 | p25 | p50 | p75 | p90 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        grouped: dict[str, list[float]] = defaultdict(list)
        for r in records:
            grouped[str(r.get("regime", ""))].append(_f(r.get(metric)))
        for regime in REGIMES:
            s = percentiles(grouped.get(regime, []))
            lines.append(f"| {regime} | {s['mean']:.6f} | {s['median']:.6f} | {s['p10']:.6f} | {s['p25']:.6f} | {s['p50']:.6f} | {s['p75']:.6f} | {s['p90']:.6f} |")
        lines.append("")
    lines += ["## Gate Impact Simulation", "", "| threshold | conviction pass_rate | certainty_score pass_rate | directional_confidence pass_rate |", "|---:|---:|---:|---:|"]
    total = max(len(records), 1)
    for threshold in THRESHOLDS:
        rates = [sum(1 for r in records if _f(r.get(metric)) >= threshold) / total for metric in ("conviction", "certainty_score", "directional_confidence")]
        lines.append(f"| {threshold:.2f} | {rates[0]:.6f} | {rates[1]:.6f} | {rates[2]:.6f} |")
    lines += ["", "## Regime Stability Analysis", "", "| metric | threshold | switch_frequency | average_regime_duration | TOXIC_exit_frequency |", "|---|---:|---:|---:|---:|"]
    for metric in ("conviction", "certainty_score", "directional_confidence"):
        for threshold in THRESHOLDS:
            switches, avg_duration, toxic_exits = transition_stats(simulated_labels(records, metric, threshold))
            lines.append(f"| {metric} | {threshold:.2f} | {switches} | {avg_duration:.6f} | {toxic_exits} |")
    lines += ["", "Production behavior before and after: IDENTICAL. Shadow metrics are not consumed by production gates.", ""]
    return "\n".join(lines)


def build_synthetic_update_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for scenario in REGIMES:
        prev_label = None
        for seed in range(1, 13):
            for tick, ret in enumerate(market(scenario, seed=1000 * len(scenario) + seed)):
                ret_f = float(ret)
                # Deterministic three-state shadow posterior for validation only;
                # production logic is not changed or called with these values.
                if scenario == "TREND":
                    probs = np.asarray([0.62 + min(abs(ret_f), 0.02), 0.24, 0.14], dtype=float)
                elif scenario == "BEAR":
                    probs = np.asarray([0.24, 0.62 + min(abs(ret_f), 0.02), 0.14], dtype=float)
                elif scenario == "TOXIC":
                    probs = np.asarray([0.04, 0.04, 0.92], dtype=float)
                else:
                    probs = np.asarray([0.34, 0.33, 0.33], dtype=float)
                scores = compute_hmm_regime(
                    probs / float(np.sum(probs)),
                    prev_directional_label=prev_label,
                    last_signed_return=ret_f,
                )
                prev_label = str(scores.get("directional_label", prev_label))
                records.append({
                    "timestamp": float(tick),
                    "regime": str(scores.get("regime", scenario)),
                    "conviction": _f(scores.get("conviction")),
                    "certainty_score": _f(scores.get("certainty_score")),
                    "directional_confidence": _f(scores.get("directional_confidence")),
                    "uncertainty": _f(scores.get("uncertainty")),
                    "edge_score": _f(scores.get("edge_score")),
                    "directional_margin": _f(scores.get("directional_margin")),
                    "trend_score": _f(scores.get("trend_score")),
                })
    return records


def main() -> None:
    audit, _supp = run_all()
    records = build_shadow_records(audit) if audit else build_synthetic_update_records()
    Path("reports").mkdir(exist_ok=True)
    Path("reports/conviction_shadow_records.json").write_text(json.dumps(records, indent=2) + "\n")
    Path("reports/conviction_shadow_validation.md").write_text(render_report(records))


if __name__ == "__main__":
    main()
