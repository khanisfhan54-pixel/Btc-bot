#!/usr/bin/env python3
"""
Convenience script: runs a minimal regime engine sequence and dumps audit log.
Usage: python tools/extract_audit_log.py [--use-existing-backtest]
"""
import json
import os
import sys

import numpy as np  # noqa: F401 - retained for prompt-compatible helper environment


def run_minimal_extraction():
    audit_path = "reports/regime_audit_log.json"
    supp_path = "reports/regime_suppression_log.json"
    if os.path.exists(audit_path) and os.path.exists(supp_path):
        with open(audit_path, encoding="utf-8") as f:
            audit = json.load(f)
        with open(supp_path, encoding="utf-8") as f:
            supp = json.load(f)
        print(f"Loaded {len(audit)} audit records, {len(supp)} suppression records")
        return audit, supp
    print("No existing audit log found.")
    print("Run: pytest tests/test_advanced_regime_engine.py tests/test_regime_accuracy.py -q")
    print("Then add to conftest.py:")
    print("  def pytest_sessionfinish(session, exitstatus):")
    print("    if hasattr(session, '_engine_ref'):")
    print("      import json")
    print("      json.dump(session._engine_ref._regime_audit_log, open('reports/regime_audit_log.json','w'))")
    print("      json.dump(session._engine_ref._regime_suppression_log, open('reports/regime_suppression_log.json','w'))")
    sys.exit(1)


if __name__ == "__main__":
    audit, supp = run_minimal_extraction()
