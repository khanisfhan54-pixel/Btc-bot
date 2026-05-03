"""Tests for the 4 observability counters added in Task #9.

Covers FIX-3 (IGARCH runtime guardrail) and FIX-4 (schema/failsafe counters).
"""
from __future__ import annotations

import numpy as np
import pytest

import advanced_regime_engine as are_mod
from advanced_regime_engine import (
    AdvancedRegimeEngine,
    _build_output,
    _validate_output_schema,
    _OUTPUT_SCHEMA_VERSION,
)


pytestmark = pytest.mark.skipif(
    not are_mod._PROM_AVAILABLE,
    reason="prometheus_client not installed; counters unavailable",
)


def _counter_value(counter, label_values: tuple) -> float:
    metric = counter.labels(*label_values)
    return float(metric._value.get())


def test_fix3_igarch_runtime_warning():
    """After _garch_update with alpha+beta>=0.99, the IGARCH counter increments."""
    eng = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        allow_igarch=True,
        enable_background_workers=False,
    )
    # Force IGARCH-like persistence (alpha+beta = 1.0)
    eng.garch.alpha = np.array([0.5, 0.5], dtype=float)
    eng.garch.beta_garch = np.array([0.5, 0.5], dtype=float)

    eid = str(getattr(eng, "_metrics_engine_id", eng.engine_id))
    before = _counter_value(are_mod.REGIME_GARCH_PERSISTENCE_HIGH, (eid,))
    eng.garch._garch_update(np.array([1e-4, 1e-4], dtype=float), 0.001)
    after = _counter_value(are_mod.REGIME_GARCH_PERSISTENCE_HIGH, (eid,))

    assert after >= before + 1.0, (
        f"REGIME_GARCH_PERSISTENCE_HIGH did not increment: {before} -> {after}"
    )


def test_fix4_schema_violation_counter():
    """When _validate_output_schema returns False, the schema-violations counter increments."""
    eid = "test_engine_schema"
    bad = {"schema_version": "0.0.0-bad"}  # mismatch -> violation_type=schema_version
    before = _counter_value(
        are_mod.REGIME_SCHEMA_VIOLATIONS, (eid, "schema_version")
    )
    ok = _validate_output_schema(bad, engine_id=eid)
    after = _counter_value(
        are_mod.REGIME_SCHEMA_VIOLATIONS, (eid, "schema_version")
    )
    assert ok is False
    assert after >= before + 1.0, (
        f"REGIME_SCHEMA_VIOLATIONS did not increment: {before} -> {after}"
    )


def test_fix4_failsafe_counter():
    """When _build_output's hard guard rejects a payload, failsafe counter increments."""
    eid = "test_engine_failsafe"
    before = _counter_value(
        are_mod.REGIME_FAILSAFE_EMITTED, (eid, "schema_validation_failed")
    )
    # Force failsafe path by passing an invalid execution_mode/side combination.
    out = _build_output(
        regime_idx=0,
        regime_label="TREND",
        trend_strength=0.0,
        risk_level=0.5,
        confidence=0.5,
        conviction=0.5,
        edge_score=0.5,
        probabilities={"bull": 1 / 3, "bear": 1 / 3, "crisis": 1 / 3},
        macro_probs=[1 / 3, 1 / 3, 1 / 3],
        position_size=0.0,
        expected_vol=0.01,
        raw_size=0.0,
        is_toxic=False,
        garch_regime_probs=[0.5, 0.5],
        feed_status="OK",
        engine_status="OK",
        execution_mode="NOT_A_REAL_MODE",  # invalid -> schema violation
        execution_side="long",
        last_valid_vol=0.01,
        switch_stability_ema=1.0,
        engine_id=eid,
    )
    after = _counter_value(
        are_mod.REGIME_FAILSAFE_EMITTED, (eid, "schema_validation_failed")
    )
    assert out.get("engine_status") == "SCHEMA_FAILURE"
    assert out.get("execution_mode") == "fail_safe"
    assert after >= before + 1.0, (
        f"REGIME_FAILSAFE_EMITTED did not increment: {before} -> {after}"
    )
