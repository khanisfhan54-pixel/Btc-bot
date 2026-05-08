"""tests/test_smoke_l2.py — UPGRADE-5.9: real-L2 smoke gate.

Runs 200 bars of the Dec-2023 BTCUSDT replay through the full engine /
ARE pipeline using ``audit_engine_dec2023.run`` and asserts schema /
fallback budgets. Wall-clock target: < 30 s. Run with::

    pytest -m smoke_l2 --tb=short

The harness writes ``audit_engine_output/smoke_l2_summary.json``; this
test loads that artefact and validates the budgets.
"""
from __future__ import annotations

import json
import os

import pytest


@pytest.mark.smoke_l2
def test_real_l2_smoke_200_bars():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(repo_root)

    # Import inside the test to avoid paying the ARE/engine import cost
    # for unrelated test sessions.
    from audit_engine_dec2023 import DATA_1M, run  # type: ignore

    if not os.path.exists(os.path.join(repo_root, DATA_1M)):
        pytest.skip(f"l2 smoke data missing: {DATA_1M}")

    result = run(num_bars=200, start_offset=60, out_prefix="smoke_l2")
    assert result, "audit_engine_dec2023.run returned a falsy result"

    summary_path = os.path.join(
        repo_root, "audit_engine_output", "smoke_l2_summary.json"
    )
    assert os.path.exists(summary_path), f"summary missing: {summary_path}"
    with open(summary_path) as f:
        result = json.load(f)

    assert result["bars_recorded"] == 200, (
        f"bars_recorded={result['bars_recorded']}"
    )
    assert result["errors"] == 0, f"errors={result['errors']}"

    total_violations = sum(
        result.get("invariant_violation_counts", {}).values()
    )
    assert total_violations == 0, (
        f"schema violations={total_violations}"
    )

    fallback_count = int(result.get("fallback_count", 0))
    assert fallback_count <= 10, (
        f"fallback_count={fallback_count} — check engine.py NEW-MED-1 guard"
    )
