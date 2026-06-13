"""Compare current and proposed switch thresholds in-memory."""
from __future__ import annotations

from pathlib import Path

from advanced_regime_engine import AdvancedRegimeEngine
from generate_regime_audit_reports import run_all

PROPOSED_THRESHOLD = 0.182039


def current_threshold(record):
    uncertainty = float(record.get("uncertainty", 0.0))
    return max(0.52, 0.65 * (1.0 - 0.25 * uncertainty))


def summarize(audit):
    engine = AdvancedRegimeEngine(n_states=3, n_features=3)
    totals = {"TREND": 0, "BEAR": 0}
    before = {"TREND": 0, "BEAR": 0}
    after = {"TREND": 0, "BEAR": 0}
    directional_before = 0
    directional_after = 0
    before_after_labels = []
    proposed_after_labels = []

    for record in audit:
        raw = record.get("raw_regime")
        before_label = record.get("confirmed_after_switch")
        proposed_label = before_label
        if raw in ("TREND", "BEAR") and record.get("confirmed_before_switch") == raw:
            totals[raw] += 1
            metrics = engine._compute_shadow_switch_metrics(
                {"conviction": float(record.get("conviction", 0.0))},
                float(record.get("switch_strength", -1.0)),
                float(record.get("switch_gate", 999.0)),
                bool(record.get("persistence_ok", False)),
                bool(record.get("cooldown_ok", False)),
                current_threshold(record),
                PROPOSED_THRESHOLD,
            )
            if metrics["current_pass"]:
                before[raw] += 1
                directional_before += 1
                before_label = raw
            else:
                before_label = "RANGE"
            if metrics["proposed_pass"]:
                after[raw] += 1
                directional_after += 1
                proposed_label = raw
            else:
                proposed_label = "RANGE"
        before_after_labels.append((raw, before_label))
        proposed_after_labels.append((raw, proposed_label))

    def range_precision(labels):
        ranges = [(raw, label) for raw, label in labels if label == "RANGE"]
        return sum(1 for raw, _label in ranges if raw == "RANGE") / len(ranges)

    return {
        "totals": totals,
        "before": before,
        "after": after,
        "range_precision_before": range_precision(before_after_labels),
        "range_precision_after": range_precision(proposed_after_labels),
        "directional_before": directional_before,
        "directional_after": directional_after,
    }


def main():
    audit, _supp = run_all()
    s = summarize(audit)
    trend_before = s["before"]["TREND"] / s["totals"]["TREND"]
    trend_after = s["after"]["TREND"] / s["totals"]["TREND"]
    bear_before = s["before"]["BEAR"] / s["totals"]["BEAR"]
    bear_after = s["after"]["BEAR"] / s["totals"]["BEAR"]
    supported = (trend_after + bear_after) > (trend_before + bear_before) and s["range_precision_after"] >= s["range_precision_before"]
    lines = [
        "# Switch Threshold Comparison",
        "",
        f"Proposed fixed conviction threshold: `{PROPOSED_THRESHOLD:.6f}`",
        "",
        "| metric | before | after |",
        "|---|---:|---:|",
        f"| TREND recall | {trend_before:.6f} | {trend_after:.6f} |",
        f"| BEAR recall | {bear_before:.6f} | {bear_after:.6f} |",
        f"| RANGE precision | {s['range_precision_before']:.6f} | {s['range_precision_after']:.6f} |",
        f"| total directional acceptances | {s['directional_before']} | {s['directional_after']} |",
        "",
    ]
    if supported:
        lines.append("Result: data supports the minimal calibration change.")
    else:
        lines.append("Result: data does not support a production threshold change; no engine diff should be applied.")
    Path("switch_threshold_comparison.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
