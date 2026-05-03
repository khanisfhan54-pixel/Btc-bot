"""
Audit-driven regression tests for F-1 through F-8 fixes in alpha_orchestrator.

All tests must pass after the patches in fix/alpha-orchestrator-audit-f1-f8
are applied. Pre-existing test failures in other files are out of scope.
"""
import os
import sys
import time
import threading
import logging

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alpha_orchestrator as ao


# ── helpers ──────────────────────────────────────────────────────────

REQUIRED_HOLD_KEYS = [
    "rationale",
    "final_conviction",
    "risk_metrics",
    "quality_metrics",
    "correlation_metrics",
    "dominant_timeframe",
    "dominant_timeframe_basis",
]

REQUIRED_RISK_SUBKEYS = [
    "scaler", "utilization", "risk_pressure",
    "risk_penalty", "regime_adjusted_max_dd", "circuit_state",
]

REQUIRED_QUALITY_SUBKEYS = [
    "stale_ratio", "missing_ratio", "vol_amplifier",
    "stale_multiplier", "missing_multiplier",
    "regime_factor", "combined_multiplier",
    "conviction_pre_quality", "conviction_post_quality",
]


def _make_config(**overrides):
    base = dict(
        signal_weights={"alpha_a": 1.0, "alpha_b": 0.5},
        action_threshold=0.1,
        score_deadband=0.05,
        min_liquidity_threshold=0.0,
        max_missing_data_ratio=0.9,
        max_drawdown_pct=0.5,
        risk_gamma=2.0,
        signal_ttl_seconds=60.0,
        feedback_enabled=False,
        feedback_min_trades=5,
        timeframe_weights={
            "1m": 1.0, "5m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0, "1d": 1.0,
            "default": 1.0,
        },
    )
    base.update(overrides)
    return ao.OrchestratorConfig(**base)


def _make_engine(**cfg_overrides):
    return ao.AlphaOrchestrator(_make_config(**cfg_overrides))


def _exec_state(dd=0.0, exposure=0.0, max_exposure=1_000_000.0):
    return ao.ExecutionState(
        current_exposure_usd=exposure,
        max_exposure_usd=max_exposure,
        current_drawdown_pct=dd,
    )


def _regime(name="normal", vol=0.3, liq=0.8):
    return ao.RegimeContext(regime_name=name, volatility_score=vol, liquidity_score=liq)


def _fq(stale=0.0, missing=0.0):
    return ao.FeatureQuality(staleness_ratio=stale, missing_data_ratio=missing)


def _signal(source_id="alpha_a", direction=1, conviction=0.8, edge=10.0,
            timestamp=None, timeframe="1m"):
    return {
        "source_id": source_id,
        "direction": direction,
        "conviction": conviction,
        "expected_edge_bps": edge,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "timeframe": timeframe,
    }


def _meta(result):
    """Extract meta_info from an OrchestratedAction (or dict-like) result."""
    return getattr(result, "meta_info", result)


# ── F-1: schema stability on every HOLD path ─────────────────────────

def test_f1_hold_schema_has_all_required_keys_poor_feature_quality():
    """HOLD via poor_feature_quality must contain all schema-stable keys."""
    engine = _make_engine(max_missing_data_ratio=0.01)
    fq = _fq(stale=0.0, missing=0.99)
    result = engine.orchestrate(
        [_signal()], _regime(), fq, _exec_state(), current_time=time.time(),
    )
    meta = _meta(result)
    assert meta.get("rationale") == "poor_feature_quality"
    for key in REQUIRED_HOLD_KEYS:
        assert key in meta, f"Missing key '{key}' on HOLD schema"
    for sub in REQUIRED_RISK_SUBKEYS:
        assert sub in meta["risk_metrics"], f"Missing risk_metrics['{sub}']"
    for sub in REQUIRED_QUALITY_SUBKEYS:
        assert sub in meta["quality_metrics"], f"Missing quality_metrics['{sub}']"


def test_f1_hold_schema_has_all_required_keys_insufficient_liquidity():
    """HOLD via insufficient_liquidity must contain all required keys."""
    engine = _make_engine(min_liquidity_threshold=0.99)
    result = engine.orchestrate(
        [_signal()], _regime(liq=0.0), _fq(), _exec_state(),
        current_time=time.time(),
    )
    meta = _meta(result)
    assert meta.get("rationale") == "insufficient_liquidity"
    for key in REQUIRED_HOLD_KEYS:
        assert key in meta, f"Missing key '{key}' on HOLD schema"


def test_f1_missing_time_meta_has_all_required_keys():
    """HOLD via invalid_current_time must contain all required keys."""
    engine = _make_engine()
    result = engine.orchestrate(
        [_signal()], _regime(), _fq(), _exec_state(), current_time=None,
    )
    meta = _meta(result)
    assert meta.get("rationale") == "invalid_current_time"
    for key in REQUIRED_HOLD_KEYS:
        assert key in meta, f"Missing key '{key}' in _missing_time_meta"


# ── F-2: liquidity warning is rate-limited ───────────────────────────

def test_f2_liquidity_warning_is_rate_limited(caplog):
    """At most 1 WARNING per 60 s for repeated insufficient_liquidity HOLDs."""
    engine = _make_engine(min_liquidity_threshold=0.99)
    engine._last_liquidity_warn_ts = 0.0  # arm
    regime = _regime(liq=0.0)
    fq = _fq()
    es = _exec_state()
    sigs = [_signal()]

    with caplog.at_level(logging.WARNING, logger=ao.logger.name):
        for _ in range(10):
            engine.orchestrate(sigs, regime, fq, es, current_time=time.time())

    liquidity_warns = [
        r for r in caplog.records
        if "LIQUIDITY GATE" in r.getMessage()
    ]
    assert len(liquidity_warns) == 1, (
        f"Expected 1 rate-limited warning, got {len(liquidity_warns)}"
    )


# ── F-3: drawdown circuit-breaker hysteresis ─────────────────────────

def test_f3_circuit_breaker_opens_on_breach():
    """dd >= max_dd must open the circuit and return dd_breach HOLD."""
    engine = _make_engine(
        max_drawdown_pct=0.10,
        dd_recovery_ratio=0.60,
        dd_breach_min_seconds=0.0,
    )
    es = _exec_state(dd=0.15)  # over threshold
    result = engine.orchestrate(
        [_signal()], _regime(), _fq(), es, current_time=time.time(),
    )
    meta = _meta(result)
    assert engine._dd_circuit_state in ("OPEN", "HALF_OPEN")
    assert meta["risk_metrics"]["circuit_state"] in ("OPEN", "HALF_OPEN")


def test_f3_circuit_breaker_stays_open_above_recovery_threshold():
    """dd still above recovery threshold → stays OPEN, no trading."""
    engine = _make_engine(
        max_drawdown_pct=0.10,
        dd_recovery_ratio=0.60,   # recover below 0.06
        dd_breach_min_seconds=0.0,
    )
    engine._dd_circuit_state = "OPEN"
    engine._dd_circuit_open_ts = 0.0
    es = _exec_state(dd=0.08)    # recovered, but above 0.06
    result = engine.orchestrate(
        [_signal()], _regime(), _fq(), es, current_time=time.time(),
    )
    meta = _meta(result)
    assert engine._dd_circuit_state == "OPEN"
    assert meta["risk_metrics"]["circuit_state"] == "OPEN"


def test_f3_circuit_breaker_closes_after_full_recovery():
    """dd below recovery threshold → HALF_OPEN/CLOSED, trading resumes."""
    engine = _make_engine(
        max_drawdown_pct=0.10,
        dd_recovery_ratio=0.60,   # recover below 0.06
        dd_breach_min_seconds=0.0,
    )
    engine._dd_circuit_state = "OPEN"
    engine._dd_circuit_open_ts = 0.0
    es = _exec_state(dd=0.04)    # fully recovered
    engine.orchestrate(
        [_signal()], _regime(), _fq(), es, current_time=time.time(),
    )
    assert engine._dd_circuit_state in ("HALF_OPEN", "CLOSED")


# ── F-4: dominant_timeframe always populated ─────────────────────────

def test_f4_dominant_timeframe_populated_for_single_tf():
    """Single-tf with non-zero score → dominant_timeframe is set, not None."""
    engine = _make_engine()
    tf_results = {
        "1h": {"net_score": 0.7, "blended_edge": 5.0, "fusion_meta": {}},
    }
    _, _, mtf_meta = engine._combine_timeframes(tf_results)
    assert mtf_meta.get("dominant_timeframe") == "1h"
    assert mtf_meta.get("dominant_timeframe_basis") == "single_tf"


def test_f4_dominant_timeframe_none_when_all_zero():
    """All-zero scores → dominant_timeframe is None, basis 'none'."""
    engine = _make_engine()
    tf_results = {
        "1h": {"net_score": 0.0, "blended_edge": 0.0, "fusion_meta": {}},
        "4h": {"net_score": 0.0, "blended_edge": 0.0, "fusion_meta": {}},
        "1d": {"net_score": 0.0, "blended_edge": 0.0, "fusion_meta": {}},
    }
    _, _, mtf_meta = engine._combine_timeframes(tf_results)
    assert mtf_meta.get("dominant_timeframe") is None
    assert mtf_meta.get("dominant_timeframe_basis") == "none"


# ── F-5: correlation_metrics top-level key ───────────────────────────

def test_f5_correlation_metrics_present_in_meta_on_hold():
    """Even on a HOLD path, top-level correlation_metrics must be present."""
    engine = _make_engine(min_liquidity_threshold=0.99)
    result = engine.orchestrate(
        [_signal()], _regime(liq=0.0), _fq(), _exec_state(),
        current_time=time.time(),
    )
    meta = _meta(result)
    assert "correlation_metrics" in meta
    cm = meta["correlation_metrics"]
    assert "groups_active" in cm
    assert "max_group_size" in cm
    assert "any_gate_active" in cm


def test_f5_correlation_metrics_present_in_meta_on_signal():
    """On a SIGNAL path, top-level correlation_metrics must also be present."""
    engine = _make_engine()
    result = engine.orchestrate(
        [_signal(), _signal(source_id="alpha_b", direction=1)],
        _regime(), _fq(), _exec_state(),
        current_time=time.time(),
    )
    meta = _meta(result)
    assert "correlation_metrics" in meta
    cm = meta["correlation_metrics"]
    assert "groups_active" in cm
    assert "max_group_size" in cm
    assert "any_gate_active" in cm


# ── F-6: logger.exception in outer catches ───────────────────────────

def test_f6_orchestrate_outer_catch_uses_logger_exception(caplog):
    """An unexpected exception inside orchestrate must log a traceback."""
    engine = _make_engine()
    with patch.object(
        engine, "_orchestrate_impl",
        side_effect=RuntimeError("injected test error"),
    ):
        with caplog.at_level(logging.ERROR, logger=ao.logger.name):
            result = engine.orchestrate(
                [_signal()], _regime(), _fq(), _exec_state(),
                current_time=time.time(),
            )
    tb_logs = [r for r in caplog.records if r.exc_info is not None]
    assert len(tb_logs) >= 1, (
        "Expected at least one log record with exc_info (traceback) "
        "but got none — logger.warning was used instead of logger.exception"
    )
    # Engine returns a fully-instrumented HOLD on error (F-1 contract).
    meta = _meta(result)
    assert meta.get("rationale") == "internal_error"


# ── F-7: OrchestratorMetaInfo TypedDict exists ───────────────────────

def test_f7_typed_dict_exists():
    """OrchestratorMetaInfo must be importable from alpha_orchestrator."""
    from alpha_orchestrator import OrchestratorMetaInfo
    hints = getattr(OrchestratorMetaInfo, "__annotations__", {})
    for key in [
        "final_conviction", "risk_metrics", "quality_metrics",
        "correlation_metrics", "dominant_timeframe",
    ]:
        assert key in hints, f"OrchestratorMetaInfo missing annotation: '{key}'"


# ── F-8: RLock instead of Lock ───────────────────────────────────────

def test_f8_lock_is_rlock():
    """self._lock must be a RLock, not a plain Lock (re-entrant safe)."""
    engine = _make_engine()
    lock = engine._lock
    assert isinstance(lock, type(threading.RLock())), (
        f"Expected RLock, got {type(lock).__name__}"
    )
