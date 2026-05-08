"""tests/test_smoke.py — FIX-5.6: CI smoke harness.

Wraps the 25-test adversarial battery (audit_engine_adversarial.py) into a
single pytest entry tagged ``@pytest.mark.smoke`` so CI can gate every PR on
``pytest -k smoke`` in <2 s without paying for the full ARE test battery.

The adversarial script runs all 25 invariants on import (it is a CLI harness)
and writes ``audit_engine_output/adversarial_results.json``. This test
re-executes that battery in-process and asserts 25/25 PASS.
"""
from __future__ import annotations

import json
import os
import runpy

import pytest


@pytest.mark.smoke
def test_adversarial_battery_25_of_25_pass(tmp_path, monkeypatch):
    """Run the 25-invariant adversarial harness; assert all 25 PASS.

    Cwd is left at the repo root so the harness writes its own
    ``audit_engine_output/adversarial_results.json`` artefact, but we
    only assert on the in-memory return.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo_root, "audit_engine_adversarial.py")

    if not os.path.exists(script):
        pytest.skip(f"adversarial harness missing: {script}")

    # Execute the harness module-style; it writes its own JSON output.
    runpy.run_path(script, run_name="__main__")

    out_path = os.path.join(repo_root, "audit_engine_output", "adversarial_results.json")
    assert os.path.exists(out_path), f"adversarial output missing: {out_path}"
    with open(out_path) as f:
        data = json.load(f)

    assert data["total"] == 25, f"expected 25 tests, got {data['total']}"
    failed = [r for r in data["results"] if not r["passed"]]
    assert not failed, f"adversarial failures: {[r['id'] for r in failed]}"
    assert data["passed"] == 25, f"expected 25 passed, got {data['passed']}"
