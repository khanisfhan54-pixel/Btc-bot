#!/usr/bin/env python3
"""
engine_fixed_verify.py
======================
Re-runs the audit harness against engine_fixed.py (loaded under the canonical
module name 'engine') and compares output to the baseline run produced by
audit_engine_dec2023.py.

Outputs:
  audit_engine_output/fixed_records.csv
  audit_engine_output/fixed_summary.json
  audit_engine_output/fixed_invariant_violations.json
  audit_engine_output/verify_diff.json   ← side-by-side comparison
  audit_engine_output/fixed_adversarial_results.json
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Dict


def _swap_engine() -> str:
    """Copy engine_fixed.py over engine.py (in a temp working dir-style swap)
    so import-by-name resolves to the patched module.  Returns backup path."""
    src = "engine.py"
    fixed = "engine_fixed.py"
    backup = ".engine_baseline.bak.py"
    if not os.path.exists(fixed):
        raise SystemExit("engine_fixed.py missing")
    shutil.copy(src, backup)
    shutil.copy(fixed, src)
    return backup


def _restore_engine(backup: str) -> None:
    if os.path.exists(backup):
        shutil.copy(backup, "engine.py")
        os.remove(backup)


def _diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k in ("bars_recorded", "errors", "elapsed_sec", "allow_trade_rate",
             "direction_distribution", "market_state_distribution",
             "confidence_stats", "alpha_confidence_stats",
             "invariant_violation_counts"):
        out[k] = {"baseline": a.get(k), "fixed": b.get(k)}
    return out


def main():
    backup = None
    try:
        backup = _swap_engine()

        # Force reimport of engine
        for m in list(sys.modules):
            if m.startswith("engine") or m == "audit_engine_dec2023" or m == "audit_engine_adversarial":
                sys.modules.pop(m, None)

        # Run adversarial first
        adv = importlib.import_module("audit_engine_adversarial")
        adv.results.clear()
        adv.main()
        # Move output
        shutil.copy("audit_engine_output/adversarial_results.json",
                    "audit_engine_output/fixed_adversarial_results.json")

        # Run harness
        for m in list(sys.modules):
            if m.startswith("engine") or m == "audit_engine_dec2023":
                sys.modules.pop(m, None)
        harness = importlib.import_module("audit_engine_dec2023")
        bars = int(os.environ.get("VERIFY_BARS", "1500"))
        fixed_summary = harness.run(num_bars=bars, start_offset=60, out_prefix="fixed")

    finally:
        if backup:
            _restore_engine(backup)

    # Compare
    base_path = "audit_engine_output/baseline_summary.json"
    if not os.path.exists(base_path):
        print("[VERIFY] baseline_summary.json missing — run audit_engine_dec2023.py first.")
        return
    baseline = json.load(open(base_path))
    diff = _diff(baseline, fixed_summary)
    fixed_adv = json.load(open("audit_engine_output/fixed_adversarial_results.json"))
    diff["adversarial"] = {
        "passed": fixed_adv.get("passed"),
        "failed": fixed_adv.get("failed"),
        "total": fixed_adv.get("total"),
    }
    out_path = "audit_engine_output/verify_diff.json"
    with open(out_path, "w") as f:
        json.dump(diff, f, indent=2)
    print(json.dumps(diff, indent=2))


if __name__ == "__main__":
    main()
