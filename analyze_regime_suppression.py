"""
Offline analysis of _regime_suppression_log collected during test runs.
Run after pytest with LOG_LEVEL=DEBUG or after injecting engine._regime_suppression_log.
"""
import json
from collections import Counter, defaultdict


def analyze(log: list) -> dict:
    total = len(log)
    raw_to_confirmed = Counter()
    gate_counts = Counter()
    for rec in log:
        key = f"{rec['raw_regime']} → {rec['confirmed_regime']}"
        raw_to_confirmed[key] += 1
        if rec.get("early_override_fired"):
            detail = rec.get("early_override_detail", {})
            if detail.get("edge_below_thresh"):
                gate_counts["early_override:edge_below_thresh"] += 1
            if detail.get("conviction_below_055"):
                gate_counts["early_override:conviction_below_055"] += 1
            if detail.get("margin_below_thresh"):
                gate_counts["early_override:margin_below_thresh"] += 1
            if not detail.get("return_ema_hint"):
                gate_counts["early_override:no_ema_hint"] += 1
        elif rec.get("switch_blocked"):
            if not rec.get("cooldown_ok"):
                gate_counts["switch:cooldown_blocked"] += 1
            if not rec.get("conviction_ok"):
                gate_counts["switch:conviction_lt_065"] += 1
            if not rec.get("persistence_ok"):
                gate_counts["switch:persistence_lt_min"] += 1
            if rec.get("switch_strength", 0) < rec.get("switch_gate", 1):
                gate_counts["switch:strength_lt_gate"] += 1
    return {
        "total_suppression_events": total,
        "transition_matrix": dict(raw_to_confirmed.most_common()),
        "gate_failure_counts": dict(gate_counts.most_common()),
        "primary_blocker": gate_counts.most_common(1)[0] if gate_counts else ("none", 0),
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "regime_suppression_log.json"
    with open(path) as f:
        log = json.load(f)
    result = analyze(log)
    print(json.dumps(result, indent=2))
