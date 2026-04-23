"""Comprehensive audit test suite for alpha orchestration signal pipeline.

Covers:
  - Signal fusion correctness (no bias, no double counting)
  - MTF logic (grouping, HTF dominance, conflict handling)
  - Regime engine integration (multipliers, drift, fallback, cold start)
  - Performance feedback (no runaway multipliers, decay handling)
  - Feature quality (missing/stale data handling)
  - Numerical safety (NaN, Inf, divide-by-zero)
  - Determinism (same input -> same output)
  - Feature engine edge cases
  - Signal engine edge cases
  - Alpha liquidity sweep predictor edge cases

NO real trading, NO exchange connections, NO execution engines.
"""
from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import alpha_orchestrator as ao
import feature_engine as fe
import signal_engine as se
import alpha_liquidity_sweep_predictor as alpha_pred


# ============================================================================
# Helpers
# ============================================================================

def _cfg(**overrides):
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


def _sig(source_id="alpha_a", direction=1, conviction=0.8, edge=10.0,
         timestamp=None, timeframe="1m"):
    return {
        "source_id": source_id,
        "direction": direction,
        "conviction": conviction,
        "expected_edge_bps": edge,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "timeframe": timeframe,
    }


def _exec(dd=0.0, exposure=0.0, max_exposure=1_000_000.0):
    return ao.ExecutionState(
        current_exposure_usd=exposure,
        max_exposure_usd=max_exposure,
        current_drawdown_pct=dd,
    )


def _regime(name="normal", vol=0.3, liq=0.8):
    return ao.RegimeContext(regime_name=name, volatility_score=vol, liquidity_score=liq)


def _fq(stale=0.0, missing=0.0):
    return ao.FeatureQuality(staleness_ratio=stale, missing_data_ratio=missing)


# ============================================================================
# 1. Signal Fusion Correctness
# ============================================================================

class TestSignalFusion:
    """Validates signal fusion produces correct weighted averages with no bias."""

    def test_single_buy_signal_produces_buy(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(), now,
        )
        assert result.action == ao.Action.BUY

    def test_single_sell_signal_produces_sell(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(direction=-1, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(), now,
        )
        assert result.action == ao.Action.SELL

    def test_hold_on_zero_direction(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(direction=0, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(), now,
        )
        assert result.action == ao.Action.HOLD

    def test_opposing_signals_cancel_out(self):
        """Equal opposing signals with same weights should cancel to HOLD."""
        config = _cfg(signal_weights={"alpha_a": 1.0, "alpha_b": 1.0})
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [
                _sig(source_id="alpha_a", direction=1, conviction=0.8, timestamp=now),
                _sig(source_id="alpha_b", direction=-1, conviction=0.8, timestamp=now),
            ],
            _regime(), _fq(), _exec(), now,
        )
        # Net score should be ~0, resulting in HOLD (deadband)
        assert result.action == ao.Action.HOLD

    def test_weighted_fusion_heavier_signal_dominates(self):
        """Signal with higher weight should dominate direction."""
        config = _cfg(signal_weights={"alpha_a": 5.0, "alpha_b": 0.1})
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [
                _sig(source_id="alpha_a", direction=1, conviction=0.9, timestamp=now),
                _sig(source_id="alpha_b", direction=-1, conviction=0.9, timestamp=now),
            ],
            _regime(), _fq(), _exec(), now,
        )
        assert result.action == ao.Action.BUY

    def test_no_double_counting_same_source_same_tf(self):
        """Duplicate signals from same source+timeframe should be deduped."""
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [
            _sig(source_id="alpha_a", timeframe="1m", timestamp=now - 1.0, conviction=0.3),
            _sig(source_id="alpha_a", timeframe="1m", timestamp=now - 0.5, conviction=0.9),
        ]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert valid[0].conviction == pytest.approx(0.9)

    def test_edge_bps_sign_convention(self):
        """expected_edge_bps must be absolute magnitude; negative is normalized."""
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [_sig(edge=-15.0, timestamp=now)]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert valid[0].expected_edge_bps == pytest.approx(15.0)
        assert metrics["negative_edge_normalized"] == 1


# ============================================================================
# 2. MTF Logic
# ============================================================================

class TestMTFLogic:
    """Validates multi-timeframe grouping, HTF dominance, and conflict handling."""

    def test_signals_grouped_by_timeframe(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [
            _sig(source_id="alpha_a", timeframe="1m", direction=1, timestamp=now),
            _sig(source_id="alpha_b", timeframe="5m", direction=-1, timestamp=now),
        ]
        result = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
        # Both TFs should appear in timeframe_breakdown
        assert "timeframe_breakdown" in result.meta_info
        assert "1m" in result.meta_info["timeframe_breakdown"]
        assert "5m" in result.meta_info["timeframe_breakdown"]

    def test_htf_dominance_overrides_ltf(self):
        """When higher_tf_dominance=True, conflicting lower TF is excluded."""
        config = _cfg(
            signal_weights={"alpha_a": 1.0, "alpha_b": 1.0},
            higher_tf_dominance=True,
        )
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        # 1h says BUY, 1m says SELL -> 1h should dominate
        sigs = [
            _sig(source_id="alpha_a", timeframe="1m", direction=-1, conviction=0.9, timestamp=now),
            _sig(source_id="alpha_b", timeframe="1h", direction=1, conviction=0.8, timestamp=now),
        ]
        result = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
        assert result.action == ao.Action.BUY

    def test_htf_dominance_disabled_allows_ltf_influence(self):
        """When higher_tf_dominance=False, all TFs contribute normally."""
        config = _cfg(
            signal_weights={"alpha_a": 1.0, "alpha_b": 1.0},
            higher_tf_dominance=False,
        )
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        # Equal conviction, opposing directions -> should cancel
        sigs = [
            _sig(source_id="alpha_a", timeframe="1m", direction=-1, conviction=0.8, timestamp=now),
            _sig(source_id="alpha_b", timeframe="1h", direction=1, conviction=0.8, timestamp=now),
        ]
        result = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
        # With equal weights and no dominance, net score is near 0
        assert result.action == ao.Action.HOLD

    def test_default_timeframe_excluded_from_dominance(self):
        """The 'default' sentinel should not be selected as dominant TF."""
        config = _cfg(signal_weights={"alpha_a": 1.0, "alpha_b": 1.0})
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        sigs = [
            _sig(source_id="alpha_a", timeframe="default", direction=1, conviction=0.9, timestamp=now),
            _sig(source_id="alpha_b", timeframe="1m", direction=-1, conviction=0.9, timestamp=now),
        ]
        result = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
        mtf = result.meta_info.get("mtf_metrics", {})
        assert mtf.get("dominant") != "default"

    def test_agreement_ratio_computed(self):
        """When multiple TFs agree, agreement_ratio > 0."""
        config = _cfg(signal_weights={"alpha_a": 1.0, "alpha_b": 1.0})
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        sigs = [
            _sig(source_id="alpha_a", timeframe="1m", direction=1, conviction=0.9, timestamp=now),
            _sig(source_id="alpha_b", timeframe="5m", direction=1, conviction=0.9, timestamp=now),
        ]
        result = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
        assert result.meta_info["agreement_ratio"] == 1.0
        assert result.meta_info["conflict_ratio"] == 0.0

    def test_conflict_ratio_computed(self):
        """When TFs disagree, conflict_ratio > 0."""
        config = _cfg(signal_weights={"alpha_a": 1.0, "alpha_b": 1.0})
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        sigs = [
            _sig(source_id="alpha_a", timeframe="1m", direction=1, conviction=0.9, timestamp=now),
            _sig(source_id="alpha_b", timeframe="5m", direction=-1, conviction=0.9, timestamp=now),
        ]
        result = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
        assert result.meta_info["conflict_ratio"] == 1.0


# ============================================================================
# 3. Regime Engine Integration
# ============================================================================

class TestRegimeIntegration:
    """Validates regime assessment, stress attenuation, and cold-start safety."""

    def test_regime_assessment_normal(self):
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(_regime("normal", 0.3, 0.8))
        assert assessment is not None
        assert assessment.is_crisis is False
        assert assessment.composite_stress < 0.5

    def test_regime_assessment_crisis(self):
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(
            _regime("crisis", vol=0.95, liq=0.05),
            regime_sample_counts={"crisis": 50},
        )
        assert assessment is not None
        assert assessment.is_crisis is True
        assert assessment.composite_stress > 0.85

    def test_regime_cold_start_confidence_zero(self):
        """With zero sample count, regime confidence should be 0."""
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(
            _regime("new_regime", vol=0.5, liq=0.5),
            regime_sample_counts={"new_regime": 0},
        )
        assert assessment is not None
        assert assessment.regime_confidence == 0.0

    def test_regime_cold_start_no_adjustment(self):
        """With 0 confidence, regime adjustments should be neutralized."""
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(
            _regime("crisis", vol=0.95, liq=0.05),
            regime_sample_counts={"crisis": 0},
        )
        # Even in crisis, confidence=0 means no adjustment
        eff_dd = orch.regime_engine.effective_max_drawdown(assessment, 0.15)
        assert eff_dd == pytest.approx(0.15)
        eff_thresh = orch.regime_engine.effective_action_threshold(assessment, 0.6)
        assert eff_thresh == pytest.approx(0.6)

    def test_regime_full_confidence_applies_adjustments(self):
        """With full confidence (30+ samples), adjustments should apply."""
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(
            _regime("crisis", vol=0.95, liq=0.05),
            regime_sample_counts={"crisis": 50},
        )
        assert assessment.regime_confidence == 1.0
        eff_dd = orch.regime_engine.effective_max_drawdown(assessment, 0.15)
        assert eff_dd < 0.15  # Tighter in crisis
        eff_thresh = orch.regime_engine.effective_action_threshold(assessment, 0.6)
        assert eff_thresh > 0.6  # Raised in crisis

    def test_regime_stress_attenuation_bounded(self):
        """Signal stress attenuation should be in [0.6, 1.0]."""
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(
            _regime("crisis", vol=0.99, liq=0.01),
            regime_sample_counts={"crisis": 50},
        )
        att = orch.regime_engine.signal_stress_attenuation(assessment, "alpha_a")
        assert 0.6 <= att <= 1.0

    def test_regime_none_returns_neutral(self):
        """None regime should produce no adjustments."""
        orch = ao.AlphaOrchestrator(_cfg())
        assessment = orch.regime_engine.assess(None)
        assert assessment is None
        assert orch.regime_engine.effective_max_drawdown(None, 0.15) == 0.15
        assert orch.regime_engine.effective_action_threshold(None, 0.6) == 0.6
        assert orch.regime_engine.signal_stress_attenuation(None, "x") == 1.0

    def test_urgency_floor_in_crisis_only_when_not_risk_stopped(self):
        """Crisis urgency floor must NOT override risk hard-stop."""
        config = _cfg(max_drawdown_pct=0.05)
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        # dd > max_dd triggers hard stop
        result = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime("crisis", vol=0.95, liq=0.05),
            _fq(),
            _exec(dd=0.10),  # 10% dd > 5% max_dd
            now,
        )
        assert result.action == ao.Action.HOLD
        assert result.urgency == 0.0  # FIX 27 ensured this


# ============================================================================
# 4. Performance Feedback
# ============================================================================

class TestPerformanceFeedback:
    """Validates feedback loop, multiplier bounds, and decay handling."""

    def _feedback_cfg(self, **extra):
        base = dict(
            signal_weights={"alpha_a": 1.0},
            feedback_enabled=True,
            feedback_min_trades=3,
            feedback_max_multiplier=1.5,
            feedback_min_multiplier=0.5,
            signal_ttl_seconds=60.0,
            action_threshold=0.1,
            score_deadband=0.05,
            max_drawdown_pct=0.5,
            timeframe_weights={"1m": 1.0, "default": 1.0},
        )
        base.update(extra)
        return ao.OrchestratorConfig(**base)

    def test_multiplier_never_exceeds_max(self):
        orch = ao.AlphaOrchestrator(self._feedback_cfg())
        # Feed many winning trades
        for _ in range(50):
            orch.update_performance({
                "source_id": "alpha_a",
                "realized_pnl": 100.0,
                "realized_edge_bps": 20.0,
                "expected_edge_bps": 10.0,
                "expected_win_rate": 0.6,
            })
        stats = orch.performance_stats.get("alpha_a")
        assert stats is not None
        assert stats.current_multiplier <= 1.5 + 1e-9

    def test_multiplier_never_below_min(self):
        orch = ao.AlphaOrchestrator(self._feedback_cfg())
        # Feed many losing trades
        for _ in range(50):
            orch.update_performance({
                "source_id": "alpha_a",
                "realized_pnl": -100.0,
                "realized_edge_bps": -20.0,
                "expected_edge_bps": 10.0,
                "expected_win_rate": 0.6,
            })
        stats = orch.performance_stats.get("alpha_a")
        assert stats is not None
        assert stats.current_multiplier >= 0.5 - 1e-9

    def test_cold_start_multiplier_is_one(self):
        """Before any trades, multiplier should be 1.0."""
        orch = ao.AlphaOrchestrator(self._feedback_cfg())
        # No trades yet
        assert "alpha_a" not in orch.performance_stats

    def test_decay_score_bounded(self):
        orch = ao.AlphaOrchestrator(self._feedback_cfg())
        for _ in range(30):
            orch.update_performance({
                "source_id": "alpha_a",
                "realized_pnl": -50.0,
                "realized_edge_bps": -10.0,
                "expected_edge_bps": 15.0,
                "expected_win_rate": 0.7,
            })
        stats = orch.performance_stats["alpha_a"]
        assert 0.0 <= stats.decay_score <= 1.0

    def test_feedback_disabled_ignores_updates(self):
        orch = ao.AlphaOrchestrator(_cfg(feedback_enabled=False))
        orch.update_performance({
            "source_id": "alpha_a",
            "realized_pnl": 100.0,
            "realized_edge_bps": 20.0,
        })
        assert len(orch.performance_stats) == 0

    def test_malformed_payload_rejected(self):
        orch = ao.AlphaOrchestrator(self._feedback_cfg())
        orch.update_performance("not_a_dict")
        assert orch._rejection_telemetry["malformed_payload"] == 1

    def test_nan_pnl_rejected(self):
        orch = ao.AlphaOrchestrator(self._feedback_cfg())
        orch.update_performance({
            "source_id": "alpha_a",
            "realized_pnl": float("nan"),
            "realized_edge_bps": 10.0,
        })
        assert orch._rejection_telemetry["malformed_outcome_values"] == 1


# ============================================================================
# 5. Regime Feedback
# ============================================================================

class TestRegimeFeedback:
    """Validates per-regime performance tracking and drift detection."""

    def _regime_cfg(self):
        return ao.OrchestratorConfig(
            signal_weights={"alpha_a": 1.0},
            feedback_enabled=True,
            regime_feedback_enabled=True,
            feedback_min_trades=3,
            regime_min_trades=3,
            feedback_max_multiplier=1.5,
            feedback_min_multiplier=0.5,
            regime_max_adjustment=1.3,
            regime_drift_threshold=0.85,
            signal_ttl_seconds=60.0,
            action_threshold=0.1,
            score_deadband=0.05,
            max_drawdown_pct=0.5,
            timeframe_weights={"1m": 1.0, "default": 1.0},
        )

    def test_regime_stats_tracked(self):
        orch = ao.AlphaOrchestrator(self._regime_cfg())
        regime = _regime("trending")
        for _ in range(5):
            orch.update_performance(
                {"source_id": "alpha_a", "realized_pnl": 10.0, "realized_edge_bps": 5.0},
                regime=regime,
            )
        stats = orch.performance_stats["alpha_a"]
        assert "trending" in stats.regimes
        assert stats.regimes["trending"].trade_count == 5

    def test_regime_multiplier_bounded(self):
        orch = ao.AlphaOrchestrator(self._regime_cfg())
        regime = _regime("trending")
        for _ in range(50):
            orch.update_performance(
                {"source_id": "alpha_a", "realized_pnl": 100.0, "realized_edge_bps": 20.0,
                 "expected_edge_bps": 10.0, "expected_win_rate": 0.6},
                regime=regime,
            )
        rs = orch.performance_stats["alpha_a"].regimes["trending"]
        assert rs.current_multiplier <= 1.3 + 1e-9

    def test_regime_fallback_on_cold_start(self):
        orch = ao.AlphaOrchestrator(self._regime_cfg())
        # Feed global trades without regime
        for _ in range(10):
            orch.update_performance(
                {"source_id": "alpha_a", "realized_pnl": 10.0, "realized_edge_bps": 5.0},
            )
        # Now check performance multiplier for unseen regime
        stats = orch.performance_stats["alpha_a"]
        mult, perf, is_fb, is_dr, dr_score, conf = orch._calculate_performance_multiplier(
            stats, "never_seen_regime"
        )
        assert is_fb is True  # Fallback used
        assert 0.5 <= mult <= 1.5


# ============================================================================
# 6. Feature Quality Handling
# ============================================================================

class TestFeatureQuality:
    """Validates handling of stale/missing data and quality degradation."""

    def test_high_missing_data_blocks_signal(self):
        config = _cfg(max_missing_data_ratio=0.3)
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(timestamp=now)],
            _regime(), _fq(missing=0.5), _exec(), now,
        )
        assert result.action == ao.Action.HOLD
        assert result.meta_info["rationale"] == "poor_feature_quality"

    def test_stale_data_degrades_conviction(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        # Normal quality
        r1 = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(stale=0.0), _exec(), now,
        )
        # High staleness
        r2 = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(stale=0.8), _exec(), now,
        )
        assert r2.net_conviction <= r1.net_conviction

    def test_quality_multiplier_breakdown_present(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(timestamp=now)],
            _regime(), _fq(stale=0.1, missing=0.05), _exec(), now,
        )
        qm = result.meta_info.get("quality_metrics")
        assert qm is not None
        assert "stale_multiplier" in qm
        assert "missing_multiplier" in qm
        assert "vol_amplifier" in qm
        assert "combined_multiplier" in qm


# ============================================================================
# 7. Numerical Safety
# ============================================================================

class TestNumericalSafety:
    """Validates no NaN/Inf leakage and safe defaults."""

    def test_nan_conviction_clamped(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [_sig(conviction=float("nan"), timestamp=now)]
        valid, _, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert valid[0].conviction == 0.0  # _safe_float default

    def test_inf_edge_clamped(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [_sig(edge=float("inf"), timestamp=now)]
        valid, _, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert math.isfinite(valid[0].expected_edge_bps)

    def test_nan_current_time_hold(self):
        orch = ao.AlphaOrchestrator(_cfg())
        result = orch.orchestrate(
            [_sig()], _regime(), _fq(), _exec(),
            current_time=float("nan"),
        )
        assert result.action == ao.Action.HOLD
        assert result.meta_info["rationale"] == "invalid_current_time"

    def test_zero_max_exposure_safe(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(timestamp=now)],
            _regime(), _fq(), _exec(max_exposure=0.0), now,
        )
        assert result.action == ao.Action.HOLD

    def test_quality_multiplier_floor(self):
        """Quality multiplier never goes below 0.1 even under extreme degradation."""
        orch = ao.AlphaOrchestrator(_cfg())
        m = orch._calculate_quality_multipliers(stale=1.0, missing=1.0, vol=1.0)
        assert m["combined_multiplier"] >= 0.1 - 1e-9

    def test_safe_float_handles_all_bad_inputs(self):
        assert ao._safe_float(None, 5.0) == 5.0
        assert ao._safe_float(float("nan"), 5.0) == 5.0
        assert ao._safe_float(float("inf"), 5.0) == 5.0
        assert ao._safe_float(float("-inf"), 5.0) == 5.0
        assert ao._safe_float("bad", 5.0) == 5.0
        assert ao._safe_float(42.0, 5.0) == 42.0


# ============================================================================
# 8. Determinism
# ============================================================================

class TestDeterminism:
    """Validates same input produces same output."""

    def test_deterministic_orchestration(self):
        now = 1_700_000_000.0
        sigs = [
            _sig(source_id="alpha_a", direction=1, conviction=0.8, edge=10.0,
                 timestamp=now, timeframe="1m"),
            _sig(source_id="alpha_b", direction=-1, conviction=0.6, edge=5.0,
                 timestamp=now, timeframe="5m"),
        ]
        results = []
        for _ in range(5):
            orch = ao.AlphaOrchestrator(_cfg())
            r = orch.orchestrate(sigs, _regime(), _fq(), _exec(), now)
            results.append((r.action, r.net_conviction, r.expected_edge_bps, r.urgency))

        for i in range(1, len(results)):
            assert results[i] == results[0], f"Run {i} differs from run 0"

    def test_deterministic_signal_validation(self):
        now = 1_700_000_000.0
        sigs = [
            _sig(timestamp=now), _sig(source_id="alpha_b", timestamp=now),
        ]
        orch = ao.AlphaOrchestrator(_cfg())
        r1 = orch._validate_and_prune(sigs, now)
        r2 = orch._validate_and_prune(sigs, now)
        assert len(r1[0]) == len(r2[0])
        for s1, s2 in zip(r1[0], r2[0]):
            assert s1 == s2


# ============================================================================
# 9. Signal Validation Edge Cases
# ============================================================================

class TestSignalValidation:
    """Tests edge cases in signal validation and pruning."""

    def test_stale_signal_rejected(self):
        orch = ao.AlphaOrchestrator(_cfg(signal_ttl_seconds=2.0))
        now = 1_700_000_000.0
        sigs = [_sig(timestamp=now - 10.0)]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 0
        assert metrics["stale"] == 1

    def test_future_timestamp_rejected(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [_sig(timestamp=now + 100.0)]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 0
        assert metrics["future_timestamp"] == 1

    def test_unknown_source_rejected_by_default(self):
        orch = ao.AlphaOrchestrator(_cfg(allow_unknown_sources=False))
        now = 1_700_000_000.0
        sigs = [_sig(source_id="unknown_source", timestamp=now)]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 0

    def test_unknown_source_accepted_when_allowed(self):
        config = _cfg(allow_unknown_sources=True, default_unknown_weight=0.5)
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        sigs = [_sig(source_id="new_alpha", timestamp=now)]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert "new_alpha" in metrics["unknown_sources_accepted"]

    def test_invalid_direction_rejected(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [{"source_id": "alpha_a", "direction": 5, "conviction": 0.8,
                  "expected_edge_bps": 10.0, "timestamp": now, "timeframe": "1m"}]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 0
        assert metrics["invalid"] >= 1

    def test_empty_signals_hold(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate([], _regime(), _fq(), _exec(), now)
        assert result.action == ao.Action.HOLD

    def test_string_signals_rejected(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate("not_a_list", _regime(), _fq(), _exec(), now)
        assert result.action == ao.Action.HOLD

    def test_missing_timeframe_defaults(self):
        """Signal without timeframe should use 'default' bucket."""
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        sigs = [{"source_id": "alpha_a", "direction": 1, "conviction": 0.8,
                  "expected_edge_bps": 10.0, "timestamp": now}]
        valid, _, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert valid[0].timeframe == "default"


# ============================================================================
# 10. Risk Overlay
# ============================================================================

class TestRiskOverlay:
    """Validates drawdown gating and exposure limits."""

    def test_drawdown_breach_forces_hold(self):
        config = _cfg(max_drawdown_pct=0.1)
        orch = ao.AlphaOrchestrator(config)
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(dd=0.15), now,
        )
        assert result.action == ao.Action.HOLD

    def test_full_utilization_reduces_conviction(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        # Nearly full utilization
        r = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(exposure=900_000.0, max_exposure=1_000_000.0), now,
        )
        # Low utilization
        r2 = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(exposure=100.0, max_exposure=1_000_000.0), now,
        )
        assert r.net_conviction <= r2.net_conviction


# ============================================================================
# 11. Feature Engine
# ============================================================================

class TestFeatureEngine:
    """Validates feature engine edge cases and output safety."""

    def _snapshot(self, mid=60000.0, spread=10.0, depth=5.0, levels=5):
        bids = [[mid - spread / 2 - i * 10, depth] for i in range(levels)]
        asks = [[mid + spread / 2 + i * 10, depth] for i in range(levels)]
        return {"bids": bids, "asks": asks, "timestamp": time.time() * 1000}

    def test_empty_orderbook_returns_safe_defaults(self):
        eng = fe.FeatureEngine()
        result = eng.update({"bids": [], "asks": []})
        assert result["confidence"] == 0.0
        assert result["features"]["regime"] == "unknown"

    def test_normal_snapshot_produces_features(self):
        eng = fe.FeatureEngine()
        result = eng.update(self._snapshot())
        f = result["features"]
        assert f["mid"] > 0
        assert f["spread"] >= 0
        assert 0.0 <= f["liquidity_score"] <= 1.0
        assert f["regime"] in ("toxic", "trend", "range", "accumulation", "distribution", "illiquid")

    def test_features_all_finite(self):
        eng = fe.FeatureEngine()
        result = eng.update(self._snapshot())
        for k, v in result["features"].items():
            if isinstance(v, (int, float)):
                assert math.isfinite(v), f"Feature {k} is not finite: {v}"

    def test_regime_detection_v3_scores(self):
        result = fe._regime_score(
            liquidity_score=0.8, spread_bps=5.0, vpin=0.3,
            latency_ms=100.0, ofi_acceleration=0.001,
            aggressor_imbalance=0.05, trade_burst=0.3,
            hidden_liquidity=False, resiliency=0.6,
            queue_churn=0.2, microprice_dev_bps=1.0, vamp_dev_bps=1.0,
        )
        assert result["regime"] in ("toxic", "illiquid", "trend", "range", "accumulation", "distribution")
        assert 0.0 <= result["regime_confidence"] <= 1.0

    def test_malformed_snapshot_safe(self):
        eng = fe.FeatureEngine()
        result = eng.update(None)
        assert result["confidence"] == 0.0

    def test_dict_level_format(self):
        eng = fe.FeatureEngine()
        snap = {
            "bids": [{"price": 59990, "size": 1.0}, {"price": 59980, "size": 2.0}],
            "asks": [{"price": 60010, "size": 1.0}, {"price": 60020, "size": 2.0}],
            "timestamp": time.time() * 1000,
        }
        result = eng.update(snap)
        assert result["features"]["mid"] > 0


# ============================================================================
# 12. Signal Engine
# ============================================================================

class TestSignalEngine:
    """Validates signal engine edge cases."""

    def test_insufficient_candles_returns_hold(self):
        eng = se.SignalEngine()
        result = eng.generate_signal(features={"candles": []})
        assert result["signal"] == "HOLD"
        assert result["confidence"] == 0.0

    def test_volatility_guard_triggers_hold(self):
        eng = se.SignalEngine()
        candles = [
            {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
            {"open": 101, "high": 103, "low": 100, "close": 102, "volume": 1},
            {"open": 102, "high": 105, "low": 101, "close": 104, "volume": 1},
        ]
        result = eng.generate_signal(features={
            "candles": candles,
            "atr": 10.0,
            "price": 100.0,  # atr/price = 0.1 > 0.05 threshold
        })
        assert result["signal"] == "HOLD"
        assert "volatility_circuit_breaker" in result.get("reasons", [])

    def test_signal_engine_backward_compat(self):
        eng = se.SignalEngine()
        result = eng.generate({"features": {"candles": []}})
        assert "signal" in result
        assert result["signal"] in ("LONG", "SHORT", "HOLD")

    def test_execution_quality_defaults_to_one(self):
        eq = se._compute_execution_quality({})
        assert eq == pytest.approx(1.0)


# ============================================================================
# 13. Alpha Liquidity Sweep Predictor
# ============================================================================

class TestAlphaLiquiditySweep:
    """Validates sweep predictor safety and output contracts."""

    def test_predict_sweep_empty_inputs(self):
        result = alpha_pred.predict_sweep({}, {})
        assert 0.0 < result["prob_above"] < 1.0
        assert 0.0 < result["prob_below"] < 1.0
        total = result["prob_above"] + result["prob_below"]
        assert abs(total - 1.0) < 0.01

    def test_predict_sweep_none_inputs(self):
        result = alpha_pred.predict_sweep(None, None)
        assert result["prob_above"] == pytest.approx(0.5, abs=0.1)

    def test_safe_output_normalizes_probs(self):
        result = alpha_pred._safe_output({
            "prob_above": 0.7,
            "prob_below": 0.8,
            "action": "BUY",
            "confidence": 0.6,
        })
        total = result["prob_above"] + result["prob_below"]
        assert abs(total - 1.0) < 0.001

    def test_safe_output_handles_nan(self):
        result = alpha_pred._safe_output({
            "prob_above": float("nan"),
            "prob_below": float("nan"),
            "confidence": float("nan"),
        })
        assert math.isfinite(result["prob_above"])
        assert math.isfinite(result["prob_below"])
        assert math.isfinite(result["confidence"])

    def test_lsa_invalid_price_returns_hold(self):
        lsa = alpha_pred.LiquiditySweepAlpha()
        result = lsa.get_signal({"price": 0.0})
        assert result["action"] == "HOLD"

    def test_lsa_predict_returns_valid_schema(self):
        lsa = alpha_pred.LiquiditySweepAlpha()
        result = lsa.predict({})
        for key in ("action", "confidence", "state", "regime", "prob_above", "prob_below"):
            assert key in result

    def test_standard_sigmoid_bounded(self):
        assert 0.0 <= alpha_pred._standard_sigmoid(-100.0) <= 1.0
        assert 0.0 <= alpha_pred._standard_sigmoid(0.0) <= 1.0
        assert 0.0 <= alpha_pred._standard_sigmoid(100.0) <= 1.0
        assert alpha_pred._standard_sigmoid(0.0) == pytest.approx(0.5)

    def test_safe_logit_bounded(self):
        """safe_logit should not produce Inf even at extremes."""
        val = alpha_pred._safe_logit(0.999999, 0.0)
        assert math.isfinite(val)
        val = alpha_pred._safe_logit(0.000001, 0.0)
        assert math.isfinite(val)


# ============================================================================
# 14. Config Validation
# ============================================================================

class TestConfigValidation:
    """Validates OrchestratorConfig rejects invalid configurations."""

    def test_empty_weights_rejected(self):
        with pytest.raises(ValueError, match="signal_weights is empty"):
            ao.OrchestratorConfig(signal_weights={}, allow_unknown_sources=False)

    def test_negative_feedback_weight_rejected(self):
        with pytest.raises(ValueError, match="feedback_win_rate_weight"):
            ao.OrchestratorConfig(
                signal_weights={"a": 1.0},
                feedback_win_rate_weight=-1.0,
            )

    def test_min_multiplier_greater_than_max_rejected(self):
        with pytest.raises(ValueError, match="feedback_min_multiplier"):
            ao.OrchestratorConfig(
                signal_weights={"a": 1.0},
                feedback_min_multiplier=2.0,
                feedback_max_multiplier=1.5,
            )

    def test_invalid_timeframe_order_rejected(self):
        """Unranked timeframe labels should be rejected when dominance is enabled."""
        with pytest.raises(ValueError, match="Unranked"):
            ao.OrchestratorConfig(
                signal_weights={"a": 1.0},
                higher_tf_dominance=True,
                timeframe_order=["custom_tf", "1m"],
            )

    def test_misordered_timeframes_rejected(self):
        """Timeframes must be in ascending order when dominance is enabled."""
        with pytest.raises(ValueError, match="ascending"):
            ao.OrchestratorConfig(
                signal_weights={"a": 1.0},
                higher_tf_dominance=True,
                timeframe_order=["1h", "1m"],  # Descending = invalid
            )

    def test_valid_config_succeeds(self):
        config = _cfg()
        assert config.action_threshold >= 0.0
        assert config.action_threshold <= 1.0


# ============================================================================
# 15. Advanced Regime Engine (within scope)
# ============================================================================

class TestAdvancedRegimeEngine:
    """Validates regime scoring and HMM outputs are bounded and consistent."""

    def test_compute_hmm_regime_valid_probs(self):
        from advanced_regime_engine import compute_hmm_regime
        import numpy as np
        result = compute_hmm_regime(np.array([0.6, 0.3, 0.1]))
        assert result["regime"] in ("TREND", "RANGE", "BEAR", "TOXIC")
        assert 0.0 <= result["confidence"] <= 1.0
        assert 0.0 <= result["edge_score"] <= 1.0

    def test_compute_hmm_regime_uniform(self):
        from advanced_regime_engine import compute_hmm_regime
        import numpy as np
        result = compute_hmm_regime(np.array([1 / 3, 1 / 3, 1 / 3]))
        assert result["regime"] in ("TREND", "RANGE", "BEAR", "TOXIC")

    def test_compute_hmm_regime_extreme_crisis(self):
        from advanced_regime_engine import compute_hmm_regime
        import numpy as np
        result = compute_hmm_regime(np.array([0.05, 0.05, 0.9]))
        assert result["toxic_score"] > result["trend_score"]

    def test_compute_hmm_regime_rejects_wrong_shape(self):
        from advanced_regime_engine import compute_hmm_regime
        import numpy as np
        with pytest.raises(ValueError, match="3-state"):
            compute_hmm_regime(np.array([0.5, 0.5]))

    def test_compute_hmm_regime_rejects_negative(self):
        from advanced_regime_engine import compute_hmm_regime
        import numpy as np
        with pytest.raises(ValueError, match="Negative"):
            compute_hmm_regime(np.array([-0.1, 0.6, 0.5]))

    def test_markov_smoother_deterministic(self):
        from advanced_regime_engine import RegimeMarkovSmoother
        smoother = RegimeMarkovSmoother()
        scores = {"bull": 0.6, "bear": 0.3, "trend_score": 0.5, "range_score": 0.3, "toxic_score": 0.1}
        r1, p1 = smoother.update(scores, None)
        smoother.reset()
        r2, p2 = smoother.update(scores, None)
        assert r1 == r2


# ============================================================================
# 16. Observability Schema Parity
# ============================================================================

class TestObservabilityParity:
    """All orchestrate paths must return consistent meta_info schema."""

    def _required_meta_keys(self):
        return [
            "rationale", "orchestration_ts", "metrics", "rejection_details",
            "environmental_context", "decision_telemetry",
            "source_policy_summary", "signal_metrics",
        ]

    def test_hold_path_has_required_keys(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate([], _regime(), _fq(), _exec(), now)
        for key in self._required_meta_keys():
            assert key in result.meta_info, f"Missing key: {key}"

    def test_buy_path_has_required_keys(self):
        orch = ao.AlphaOrchestrator(_cfg())
        now = 1_700_000_000.0
        result = orch.orchestrate(
            [_sig(direction=1, conviction=0.9, timestamp=now)],
            _regime(), _fq(), _exec(), now,
        )
        for key in self._required_meta_keys():
            assert key in result.meta_info, f"Missing key: {key}"

    def test_invalid_time_path_has_required_keys(self):
        orch = ao.AlphaOrchestrator(_cfg())
        result = orch.orchestrate(
            [_sig()], _regime(), _fq(), _exec(),
            current_time=float("nan"),
        )
        for key in self._required_meta_keys():
            assert key in result.meta_info, f"Missing key: {key}"
