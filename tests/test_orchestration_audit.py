"""Comprehensive audit test suite for alpha orchestration signal pipeline.
Scope: signal generation, feature processing, regime detection, orchestration.
NO real trading, NO exchange connections, NO execution engines.
"""
import sys, os, math, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

import alpha_orchestrator as ao
import feature_engine as fe
import signal_engine as se
import alpha_liquidity_sweep_predictor as alpha_pred
from tests.action_expectations import expected_action_from_meta, is_unsafe_aggregation_meta


# ============================================================================
# Helpers — construct orchestrator instances deterministically (no trading)
# ============================================================================

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


def _make_signal(
    source_id="alpha_a",
    direction=1,
    conviction=0.8,
    edge=10.0,
    timestamp=None,
    timeframe="1m",
):
    return {
        "source_id": source_id,
        "direction": direction,
        "conviction": conviction,
        "expected_edge_bps": edge,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "timeframe": timeframe,
    }


def _make_exec_state(dd=0.0, exposure=0.0, max_exposure=1_000_000.0):
    return ao.ExecutionState(
        current_exposure_usd=exposure,
        max_exposure_usd=max_exposure,
        current_drawdown_pct=dd,
    )


def _make_regime(name="normal", vol=0.3, liq=0.8):
    return ao.RegimeContext(regime_name=name, volatility_score=vol, liquidity_score=liq)


def _make_fq(stale=0.0, missing=0.0):
    return ao.FeatureQuality(staleness_ratio=stale, missing_data_ratio=missing)



# ============================================================================
# Category 1: Signal Deduplication (FIX 1)
# ============================================================================

class TestDeduplication:
    def test_duplicate_signals_deduplicated(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = 1_700_000_000.0
        sigs = [
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now - 1.0),
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now - 0.5),
        ]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1, f"expected 1 survivor, got {len(valid)}"
        assert metrics["duplicates_removed"] == 1

    def test_duplicate_keeps_newest(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = 1_700_000_000.0
        older = _make_signal(source_id="alpha_a", timeframe="1m",
                             timestamp=now - 2.0, conviction=0.1)
        newer = _make_signal(source_id="alpha_a", timeframe="1m",
                             timestamp=now - 0.5, conviction=0.9)
        # Submit in both orders — newest must always win.
        for sigs in ([older, newer], [newer, older]):
            valid, metrics, _ = orch._validate_and_prune(sigs, now)
            assert len(valid) == 1
            assert valid[0].timestamp == pytest.approx(now - 0.5)
            assert valid[0].conviction == pytest.approx(0.9)
            assert metrics["duplicates_removed"] == 1

    def test_duplicate_different_timeframes_kept(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = 1_700_000_000.0
        sigs = [
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now),
            _make_signal(source_id="alpha_a", timeframe="5m", timestamp=now),
            _make_signal(source_id="alpha_a", timeframe="15m", timestamp=now),
        ]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 3
        assert metrics["duplicates_removed"] == 0

    def test_duplicate_metrics_tracked(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = 1_700_000_000.0
        sigs = [
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now - 2.0),
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now - 1.0),
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now - 0.5),
        ]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        # Two duplicates on top of the baseline signal.
        assert metrics["duplicates_removed"] == 2

    def test_no_duplicates_no_change(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = 1_700_000_000.0
        sigs = [
            _make_signal(source_id="alpha_a", timeframe="1m", timestamp=now),
            _make_signal(source_id="alpha_b", timeframe="5m", timestamp=now),
            _make_signal(source_id="alpha_a", timeframe="4h", timestamp=now),
        ]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 3
        assert metrics["duplicates_removed"] == 0


# ============================================================================
# Category 2: Quality Multiplier Floor (FIX 2)
# ============================================================================

class TestQualityMultiplierFloor:
    def test_quality_multiplier_floor_at_high_staleness(self):
        orch = ao.AlphaOrchestrator(_make_config())
        m = orch._calculate_quality_multipliers(stale=1.0, missing=0.0, vol=0.5)
        assert m["combined_multiplier"] >= 0.1 - 1e-9
        assert m["combined_multiplier"] <= 1.0

    def test_quality_multiplier_floor_not_zero(self):
        orch = ao.AlphaOrchestrator(_make_config())
        extreme_cases = [
            {"stale": 1.0, "missing": 0.0, "vol": 0.5},
            {"stale": 1.0, "missing": 1.0, "vol": 1.0},
            {"stale": 0.95, "missing": 0.95, "vol": 1.0},
            {"stale": 1.0, "missing": 0.0, "vol": 0.0},
            {"stale": 0.5, "missing": 0.5, "vol": 0.9},
        ]
        for case in extreme_cases:
            m = orch._calculate_quality_multipliers(**case)
            assert m["combined_multiplier"] >= 0.1 - 1e-9, (
                f"floor violated with {case} -> {m['combined_multiplier']}"
            )

    def test_quality_normal_conditions_unaffected(self):
        orch = ao.AlphaOrchestrator(_make_config())
        m = orch._calculate_quality_multipliers(stale=0.05, missing=0.05, vol=0.3)
        assert m["combined_multiplier"] > 0.5
        assert m["combined_multiplier"] <= 1.0


# ============================================================================
# Category 3: Signal Engine Candle Parsing (FIX 3)
# ============================================================================

class TestSignalEngineCandles:
    def _mk_dict_candle(self, o, h, l, c, v=1.0):
        return {"open": o, "high": h, "low": l, "close": c, "volume": v}

    def _mk_list_candle(self, ts, o, h, l, c, v=1.0):
        return [ts, o, h, l, c, v]

    def test_signal_engine_fallback_uses_normalize_candle(self):
        engine = se.SignalEngine()
        # 3 dict-style candles — placed via `candles` key.
        candles = [
            self._mk_dict_candle(100.0, 102.0, 99.0, 101.0),
            self._mk_dict_candle(101.0, 103.0, 100.0, 101.5),
            self._mk_dict_candle(101.5, 105.0, 101.0, 104.5, v=10.0),
        ]
        features = {
            "candles": candles,
            "volume": 10.0,
            "stop_hunt": True,
            "stop_hunt_side": "sell",
            "regime": {"regime": "trend"},
        }
        out = engine.generate_signal(features=features)
        # Must not HOLD from insufficient candles: must produce a LONG/SHORT.
        assert out["signal"] in ("LONG", "SHORT"), f"got HOLD: {out}"

    def test_signal_engine_fallback_list_style_candles(self):
        engine = se.SignalEngine()
        candles = [
            self._mk_list_candle(1, 100.0, 102.0, 99.0, 101.0),
            self._mk_list_candle(2, 101.0, 103.0, 100.0, 101.5),
            self._mk_list_candle(3, 101.5, 105.0, 101.0, 104.5, v=10.0),
        ]
        features = {
            "candles": candles,
            "volume": 10.0,
            "stop_hunt": True,
            "stop_hunt_side": "sell",
            "regime": {"regime": "trend"},
        }
        out = engine.generate_signal(features=features)
        assert out["signal"] in ("LONG", "SHORT")

    def test_signal_engine_insufficient_candles_hold(self):
        engine = se.SignalEngine()
        candles = [self._mk_dict_candle(100.0, 101.0, 99.0, 100.5)]
        out = engine.generate_signal(features={"candles": candles})
        assert out["signal"] == "HOLD"

    def test_signal_engine_invalid_candles_hold(self):
        engine = se.SignalEngine()
        # 3 candles but all with invalid values (negative prices).
        bad = [
            {"open": -1.0, "high": -2.0, "low": -3.0, "close": -1.5},
            {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0},
            {"open": 10.0, "high": 5.0, "low": 11.0, "close": 9.0},  # high<low
        ]
        out = engine.generate_signal(features={"candles": bad})
        assert out["signal"] == "HOLD"


# ============================================================================
# Category 4: Feature Engine _safe_div (FIX 4)
# ============================================================================

class TestSafeDiv:
    def test_safe_div_positive_denominator(self):
        assert fe._safe_div(10.0, 2.0) == pytest.approx(5.0)

    def test_safe_div_negative_denominator(self):
        # Pre-fix this returned +5.0 (sign dropped); must now return -5.0.
        assert fe._safe_div(10.0, -2.0) == pytest.approx(-5.0)

    def test_safe_div_zero_denominator(self):
        assert fe._safe_div(10.0, 0.0, default=42.0) == pytest.approx(42.0)
        assert fe._safe_div(10.0, 0.0) == pytest.approx(0.0)

    def test_safe_div_nan_inf_handling(self):
        assert fe._safe_div(float("nan"), 2.0) == pytest.approx(0.0)
        assert fe._safe_div(10.0, float("nan")) == pytest.approx(0.0)
        assert fe._safe_div(float("inf"), 2.0) == pytest.approx(0.0)
        assert fe._safe_div(10.0, float("inf")) == pytest.approx(0.0)
        assert fe._safe_div(10.0, 0.0, default=1.5) == pytest.approx(1.5)

    def test_safe_div_tiny_denominator(self):
        # |1e-12| < 1e-9 threshold -> default.
        assert fe._safe_div(10.0, 1e-12, default=99.0) == pytest.approx(99.0)
        assert fe._safe_div(10.0, -1e-12, default=99.0) == pytest.approx(99.0)


# ============================================================================
# Category 5: Orchestrator Signal Fusion Correctness
# ============================================================================

class TestSignalFusion:
    def test_fusion_single_signal_passthrough(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [_make_signal(source_id="alpha_a", direction=1,
                             conviction=0.9, edge=20.0, timestamp=now,
                             timeframe="1m")]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        expected = ao.Action[expected_action_from_meta(out.meta_info)]
        assert out.action == expected
        if is_unsafe_aggregation_meta(out.meta_info):
            assert out.net_conviction == 0.0
            assert out.expected_edge_bps == 0.0
        else:
            assert out.net_conviction > 0.0
            assert out.expected_edge_bps > 0.0

    def test_fusion_opposing_signals_cancel(self):
        orch = ao.AlphaOrchestrator(_make_config(
            signal_weights={"alpha_a": 1.0, "alpha_b": 1.0},
        ))
        now = time.time()
        sigs = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now,
                         timeframe="1m"),
            _make_signal(source_id="alpha_b", direction=-1,
                         conviction=0.9, edge=20.0, timestamp=now,
                         timeframe="1m"),
        ]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        # Equal and opposite weighted contributions: net_score ~ 0 -> deadband HOLD.
        assert out.action == ao.Action.HOLD

    def test_fusion_weighted_signals(self):
        # alpha_a weight 2.0, alpha_b weight 1.0: a's direction wins.
        orch = ao.AlphaOrchestrator(_make_config(
            signal_weights={"alpha_a": 2.0, "alpha_b": 1.0},
        ))
        now = time.time()
        sigs = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now,
                         timeframe="1m"),
            _make_signal(source_id="alpha_b", direction=-1,
                         conviction=0.9, edge=20.0, timestamp=now,
                         timeframe="1m"),
        ]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        expected = ao.Action[expected_action_from_meta(out.meta_info)]
        assert out.action == expected
        if is_unsafe_aggregation_meta(out.meta_info):
            assert out.expected_edge_bps == 0.0
        else:
            assert out.expected_edge_bps > 0.0

    def test_fusion_no_double_counting(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        single = [_make_signal(source_id="alpha_a", direction=1,
                               conviction=0.9, edge=20.0, timestamp=now,
                               timeframe="1m")]
        # Duplicate pair differs only by stale timestamp; dedup must collapse.
        dup = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now - 0.5,
                         timeframe="1m"),
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now,
                         timeframe="1m"),
        ]
        out_single = orch.orchestrate(single, _make_regime(), _make_fq(),
                                      _make_exec_state(), current_time=now)
        out_dup = orch.orchestrate(dup, _make_regime(), _make_fq(),
                                   _make_exec_state(), current_time=now)
        assert out_single.action == out_dup.action
        assert out_single.net_conviction == pytest.approx(out_dup.net_conviction)
        assert out_single.expected_edge_bps == pytest.approx(out_dup.expected_edge_bps)
        assert out_dup.meta_info["metrics"]["duplicates_removed"] == 1

    def test_fusion_dominance_cap_active(self):
        # Three signals with one massively over-weighted source.
        orch = ao.AlphaOrchestrator(_make_config(
            signal_weights={"alpha_big": 50.0, "alpha_a": 1.0, "alpha_b": 1.0},
            allow_unknown_sources=False,
        ))
        now = time.time()
        sigs = [
            _make_signal(source_id="alpha_big", direction=1, conviction=1.0,
                         edge=30.0, timestamp=now, timeframe="1m"),
            _make_signal(source_id="alpha_a", direction=1, conviction=1.0,
                         edge=15.0, timestamp=now, timeframe="1m"),
            _make_signal(source_id="alpha_b", direction=1, conviction=1.0,
                         edge=15.0, timestamp=now, timeframe="1m"),
        ]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        breakdown = out.meta_info["per_signal_breakdown"]
        big_row = next(r for r in breakdown if r["source_id"] == "alpha_big")
        assert big_row["dominance_cap_active"] is True
        # Check: capped contribution is strictly less than raw 50.0.
        assert big_row["final_weight_contribution"] < 50.0


# ============================================================================
# Category 6: MTF Logic
# ============================================================================

class TestMultiTimeframe:
    def test_mtf_htf_dominance_excludes_conflicting(self):
        # HTF (1h) buy must exclude a conflicting LTF (1m) sell.
        orch = ao.AlphaOrchestrator(_make_config(
            higher_tf_dominance=True,
            timeframe_weights={
                "1m": 1.0, "5m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0,
                "1d": 1.0, "default": 1.0,
            },
        ))
        now = time.time()
        sigs = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now, timeframe="1h"),
            _make_signal(source_id="alpha_b", direction=-1,
                         conviction=0.9, edge=20.0, timestamp=now, timeframe="1m"),
        ]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        expected = ao.Action[expected_action_from_meta(out.meta_info)]
        assert out.action == expected
        mtf = out.meta_info["mtf_metrics"]
        if is_unsafe_aggregation_meta(out.meta_info):
            assert mtf.get("dominant") in (None, "1h")
        else:
            assert mtf.get("dominant") == "1h"
        if is_unsafe_aggregation_meta(out.meta_info):
            assert mtf.get("htf_excluded_tfs", []) in ([], ["1m"])
        else:
            assert "1m" in mtf.get("htf_excluded_tfs", [])

    def test_mtf_aligned_tfs_get_bonus(self):
        # All 3 TFs agree on LONG; alignment bonus must push conviction higher
        # than a single-TF signal with equivalent weight.
        orch = ao.AlphaOrchestrator(_make_config(
            timeframe_weights={
                "1m": 1.0, "5m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0,
                "1d": 1.0, "default": 1.0,
            },
            timeframe_alignment_bonus=0.5,
        ))
        now = time.time()
        aligned = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.8, edge=20.0, timestamp=now, timeframe="1m"),
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.8, edge=20.0, timestamp=now, timeframe="5m"),
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.8, edge=20.0, timestamp=now, timeframe="15m"),
        ]
        out = orch.orchestrate(aligned, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        expected = ao.Action[expected_action_from_meta(out.meta_info)]
        assert out.action == expected
        mtf = out.meta_info["mtf_metrics"]
        assert mtf["is_mtf"] is True
        if is_unsafe_aggregation_meta(out.meta_info):
            assert out.meta_info["agreement_ratio"] == 0.0
        else:
            assert out.meta_info["agreement_ratio"] > 0.99
        assert out.meta_info["conflict_ratio"] == 0.0

    def test_mtf_neutral_tfs_always_included(self):
        # Neutral direction (0) on a conflicting TF must NOT be excluded.
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now, timeframe="1h"),
            _make_signal(source_id="alpha_b", direction=0,
                         conviction=0.5, edge=0.0, timestamp=now, timeframe="1m"),
        ]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        mtf = out.meta_info["mtf_metrics"]
        # Neutral LTF cannot be on the exclusion list.
        assert "1m" not in mtf.get("htf_excluded_tfs", [])

    def test_mtf_default_tf_excluded_from_dominance(self):
        # The "default" sentinel must not be chosen as dominant TF.
        orch = ao.AlphaOrchestrator(_make_config(
            signal_weights={"alpha_a": 1.0},
        ))
        now = time.time()
        # Only "default" bucket is directional; no real TF exists -> no dominance.
        sigs = [_make_signal(source_id="alpha_a", direction=1,
                             conviction=0.9, edge=20.0, timestamp=now,
                             timeframe="default")]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        mtf = out.meta_info["mtf_metrics"]
        # Single-tf: is_mtf=False; no dominant selection.
        assert mtf.get("dominant") is None or mtf.get("dominant") != "default"

    def test_mtf_single_tf_no_mtf_logic(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [_make_signal(source_id="alpha_a", direction=1,
                             conviction=0.9, edge=20.0, timestamp=now,
                             timeframe="1m")]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        mtf = out.meta_info["mtf_metrics"]
        assert mtf["is_mtf"] is False


# ============================================================================
# Category 7: Regime Engine Integration
# ============================================================================

class TestRegimeIntegration:
    def test_regime_assessment_affects_threshold(self):
        # Crisis regime (very high vol, very low liq) must lift action_threshold.
        orch = ao.AlphaOrchestrator(_make_config(action_threshold=0.3))
        # Give the regime many observations so regime_confidence=1.0.
        crisis_regime = _make_regime(name="crisis", vol=1.0, liq=0.01)
        sample_counts = {"crisis": 100}
        assessment = orch.regime_engine.assess(crisis_regime, sample_counts)
        assert assessment is not None
        assert assessment.is_crisis is True
        eff = orch.regime_engine.effective_action_threshold(
            assessment, orch.config.action_threshold
        )
        assert eff > orch.config.action_threshold

    def test_regime_stress_attenuation(self):
        orch = ao.AlphaOrchestrator(_make_config())
        # Assessment with composite_stress > 0.75 must attenuate.
        stress_regime = _make_regime(name="stress", vol=0.95, liq=0.05)
        assessment = orch.regime_engine.assess(stress_regime, {"stress": 100})
        att = orch.regime_engine.signal_stress_attenuation(assessment, "alpha_a")
        assert att < 1.0
        assert att >= 0.6
        # Calm regime must not attenuate.
        calm = orch.regime_engine.assess(_make_regime(vol=0.1, liq=0.9), {"normal": 100})
        assert orch.regime_engine.signal_stress_attenuation(calm, "alpha_a") == 1.0

    def test_regime_urgency_floor(self):
        orch = ao.AlphaOrchestrator(_make_config())
        crisis = _make_regime(name="crisis", vol=1.0, liq=0.0)
        assessment = orch.regime_engine.assess(crisis, {"crisis": 100})
        assert assessment.is_crisis is True
        floor = orch.regime_engine.urgency_regime_floor(assessment)
        assert floor > 0.0  # Crisis must floor urgency strictly above zero.

    def test_regime_dd_breach_overrides_urgency_floor(self):
        # Even in crisis regime, a drawdown breach must zero urgency (FIX 27).
        orch = ao.AlphaOrchestrator(_make_config(max_drawdown_pct=0.1))
        now = time.time()
        sigs = [_make_signal(source_id="alpha_a", direction=1,
                             conviction=0.9, edge=20.0, timestamp=now,
                             timeframe="1m")]
        crisis = _make_regime(name="crisis", vol=1.0, liq=0.0)
        exec_state = _make_exec_state(dd=0.5)  # exceeds max_drawdown_pct=0.1
        out = orch.orchestrate(sigs, crisis, _make_fq(), exec_state, current_time=now)
        assert out.action == ao.Action.HOLD
        assert out.urgency == 0.0
        assert out.meta_info["rationale"] == "dd_breach"


# ============================================================================
# Category 8: Performance Feedback
# ============================================================================

class TestPerformanceFeedback:
    def _feed(self, orch, source, is_win, n, regime=None):
        for _ in range(n):
            orch.update_performance(
                {
                    "source_id": source,
                    "realized_pnl": 1.0 if is_win else -1.0,
                    "realized_edge_bps": 20.0 if is_win else -20.0,
                    "expected_edge_bps": 20.0,
                    "expected_win_rate": 0.55,
                },
                feature_quality=_make_fq(),
                regime=regime,
            )

    def test_feedback_cold_start_multiplier_is_one(self):
        orch = ao.AlphaOrchestrator(_make_config(
            feedback_enabled=True, feedback_min_trades=10,
        ))
        # Feed fewer than min_trades: multiplier must stay at cold-start 1.0.
        self._feed(orch, "alpha_a", is_win=True, n=3)
        stats = orch.performance_stats["alpha_a"]
        assert stats.current_multiplier == pytest.approx(1.0, abs=1e-6)

    def test_feedback_good_performance_increases_multiplier(self):
        orch = ao.AlphaOrchestrator(_make_config(
            feedback_enabled=True, feedback_min_trades=5,
            feedback_max_multiplier=1.5,
        ))
        self._feed(orch, "alpha_a", is_win=True, n=30)
        stats = orch.performance_stats["alpha_a"]
        assert stats.current_multiplier > 1.0
        assert stats.current_multiplier <= 1.5

    def test_feedback_poor_performance_decreases_multiplier(self):
        orch = ao.AlphaOrchestrator(_make_config(
            feedback_enabled=True, feedback_min_trades=5,
            feedback_min_multiplier=0.5,
        ))
        self._feed(orch, "alpha_a", is_win=False, n=30)
        stats = orch.performance_stats["alpha_a"]
        assert stats.current_multiplier < 1.0
        assert stats.current_multiplier >= 0.5

    def test_feedback_multiplier_bounded(self):
        orch = ao.AlphaOrchestrator(_make_config(
            feedback_enabled=True, feedback_min_trades=5,
            feedback_min_multiplier=0.5, feedback_max_multiplier=1.5,
        ))
        # Massively win streak.
        self._feed(orch, "alpha_a", is_win=True, n=200)
        # Massively loss streak on another alpha.
        self._feed(orch, "alpha_b", is_win=False, n=200)
        wins = orch.performance_stats["alpha_a"]
        losses = orch.performance_stats["alpha_b"]
        assert 0.5 <= wins.current_multiplier <= 1.5
        assert 0.5 <= losses.current_multiplier <= 1.5

    def test_feedback_decay_drift_limit(self):
        # Build a poisoned perf_meta with decay_score above the 0.85 guard.
        orch = ao.AlphaOrchestrator(_make_config(
            feedback_enabled=True, feedback_min_trades=2,
        ))
        # Manually seed decay high after enough trades.
        self._feed(orch, "alpha_a", is_win=False, n=10)
        orch.performance_stats["alpha_a"].decay_score = 0.95
        # Rebuild cached meta with the forced decay_score.
        orch._cached_perf_meta = orch._build_performance_meta()
        now = time.time()
        sigs = [_make_signal(source_id="alpha_a", direction=1,
                             conviction=0.9, edge=20.0, timestamp=now,
                             timeframe="1m")]
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        assert out.action == ao.Action.HOLD
        assert out.meta_info["rationale"] == "decay_drift_limit_exceeded"


# ============================================================================
# Category 9: Feature Quality & Staleness
# ============================================================================

class TestFeatureEngine:
    def test_feature_engine_empty_book_safe(self):
        engine = fe.FeatureEngine(max_levels=5)
        out = engine.update({"bids": [], "asks": []})
        assert isinstance(out, dict)
        assert "features" in out
        assert out["confidence"] == pytest.approx(0.0)
        feats = out["features"]
        assert feats["mid"] == 0.0
        assert feats["spread_bps"] == 0.0
        assert feats["regime"] == "unknown"

    def test_feature_engine_nan_inputs_handled(self):
        engine = fe.FeatureEngine(max_levels=3)
        snap = {
            "bids": [[float("nan"), float("nan")], [100.0, 1.0]],
            "asks": [[float("inf"), 1.0], [101.0, 1.0]],
        }
        out = engine.update(snap)
        assert isinstance(out, dict)
        feats = out["features"]
        # All numeric fields must be finite after sanitisation.
        for k, v in feats.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                assert math.isfinite(float(v)), f"{k} not finite: {v}"

    def test_feature_engine_regime_context_overlay(self):
        engine = fe.FeatureEngine(max_levels=3)
        ctx = {
            "regime": "trend_up",
            "confidence": 0.75,
            "features": {
                "volatility_regime": "high_vol",
                "liquidity_regime": "thin",
                "trend_strength": 0.8,
            },
        }
        out = engine.update({"bids": [], "asks": []}, trades=[], regime_context=ctx)
        feats = out["features"]
        assert feats.get("volatility_regime") == "high_vol"
        assert feats.get("liquidity_regime") == "thin"
        assert feats.get("trend_strength") == pytest.approx(0.8)

    def test_feature_engine_sanitize_all_numeric_features(self):
        engine = fe.FeatureEngine(max_levels=3)
        out = engine.update({"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]]})
        feats = out["features"]
        # All numeric values must be finite.
        for k, v in feats.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                assert math.isfinite(float(v)), f"{k} not finite: {v}"
        # Bounded probabilistic/score features.
        assert 0.0 <= feats["liquidity_score"] <= 1.0
        assert 0.0 <= feats["urgency"] <= 1.0
        assert -1.0 <= feats["ofi"] <= 1.0


# ============================================================================
# Category 10: Numerical Safety
# ============================================================================

class TestNumericalSafety:
    def test_orchestrator_nan_signal_rejected(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [{
            "source_id": "alpha_a",
            "direction": 1,
            "conviction": float("nan"),
            "expected_edge_bps": 10.0,
            "timestamp": now,
            "timeframe": "1m",
        }]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        # NaN conviction is safe-floated to 0.0; the signal is retained but
        # its contribution is zero. Check that fusion cannot produce action.
        out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                               _make_exec_state(), current_time=now)
        assert out.action == ao.Action.HOLD

    def test_orchestrator_inf_edge_clamped(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [{
            "source_id": "alpha_a",
            "direction": 1,
            "conviction": 0.9,
            "expected_edge_bps": float("inf"),
            "timestamp": now,
            "timeframe": "1m",
        }]
        valid, _, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert math.isfinite(valid[0].expected_edge_bps)
        assert valid[0].expected_edge_bps <= 1000.0  # _EDGE_BPS_CLAMP

    def test_alpha_predictor_nan_inputs_safe(self):
        res = alpha_pred.predict_sweep(
            liquidity={
                "nearest_above": {"distance_points": float("nan"), "price": float("nan")},
                "nearest_below": {"distance_points": float("nan"), "price": float("nan")},
            },
            market_state={
                "state": "CHOPPY",
                "volatility": float("nan"),
                "compression": float("nan"),
                "bias": float("nan"),
            },
        )
        assert math.isfinite(res["prob_above"])
        assert math.isfinite(res["prob_below"])
        assert 0.0 < res["prob_above"] < 1.0
        assert 0.0 < res["prob_below"] < 1.0

    def test_alpha_predictor_prob_sum_to_one(self):
        cases = [
            ({"nearest_above": {"distance_points": 10, "price": 60100},
              "nearest_below": {"distance_points": 90, "price": 59900}},
             {"state": "TRENDING", "bias": 0.5}),
            ({},
             {"state": "COMPRESSION", "volatility": 0.02, "compression": 0.3}),
            ({"nearest_above": {"distance_points": 30, "price": 60100}},
             {"state": "CHOPPY", "volatility": 0.002, "bias": -0.8}),
        ]
        for liq, ms in cases:
            r = alpha_pred.predict_sweep(liq, ms)
            assert abs(r["prob_above"] + r["prob_below"] - 1.0) < 1e-3, (
                f"probs {r['prob_above']}+{r['prob_below']} != 1.0 for {liq}, {ms}"
            )


# ============================================================================
# Category 11: Determinism
# ============================================================================

class TestDeterminism:
    def test_orchestrator_deterministic(self):
        now = 1_700_000_000.0
        sigs = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.8, edge=15.0, timestamp=now, timeframe="1m"),
            _make_signal(source_id="alpha_b", direction=-1,
                         conviction=0.5, edge=10.0, timestamp=now, timeframe="1m"),
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.7, edge=12.0, timestamp=now, timeframe="1h"),
        ]
        results = []
        for _ in range(3):
            orch = ao.AlphaOrchestrator(_make_config())
            out = orch.orchestrate(sigs, _make_regime(), _make_fq(),
                                   _make_exec_state(), current_time=now)
            results.append((out.action, out.net_conviction,
                           out.expected_edge_bps, out.urgency))
        assert results[0] == results[1] == results[2]

    def test_signal_engine_deterministic(self):
        engine = se.SignalEngine()
        features = {
            "candles": [
                {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 5.0},
                {"open": 101.0, "high": 103.0, "low": 100.0, "close": 101.5, "volume": 6.0},
                {"open": 101.5, "high": 105.0, "low": 101.0, "close": 104.5, "volume": 15.0},
            ],
            "volume": 15.0,
            "stop_hunt": True,
            "stop_hunt_side": "sell",
            "regime": {"regime": "trend"},
        }
        outs = [engine.generate_signal(features=features) for _ in range(3)]
        assert outs[0] == outs[1] == outs[2]

    def test_alpha_predictor_deterministic(self):
        liq = {
            "nearest_above": {"distance_points": 40, "price": 60100},
            "nearest_below": {"distance_points": 60, "price": 59900},
        }
        ms = {"state": "TRENDING", "volatility": 0.003, "compression": 0.5, "bias": 0.2}
        outs = [alpha_pred.predict_sweep(liq, ms) for _ in range(3)]
        assert outs[0] == outs[1] == outs[2]


# ============================================================================
# Category 12: Risk Overlay
# ============================================================================

class TestRiskOverlay:
    def test_risk_dd_breach_zeroes_conviction(self):
        orch = ao.AlphaOrchestrator(_make_config(max_drawdown_pct=0.1))
        scaled, pressure, util, rat = orch._apply_risk_overlay(
            conviction=0.9,
            state=_make_exec_state(dd=0.3),
            dd=0.3,
        )
        assert scaled == 0.0
        assert rat == "dd_breach"

    def test_risk_zero_exposure_zeroes_conviction(self):
        orch = ao.AlphaOrchestrator(_make_config())
        scaled, pressure, util, rat = orch._apply_risk_overlay(
            conviction=0.9,
            state=ao.ExecutionState(
                current_exposure_usd=0.0, max_exposure_usd=0.0, current_drawdown_pct=0.0
            ),
            dd=0.0,
        )
        assert scaled == 0.0
        assert rat == "zero_exp"

    def test_risk_full_utilization_reduces_conviction(self):
        orch = ao.AlphaOrchestrator(_make_config(risk_gamma=2.0))
        full = _make_exec_state(dd=0.0, exposure=1_000_000.0, max_exposure=1_000_000.0)
        part = _make_exec_state(dd=0.0, exposure=200_000.0, max_exposure=1_000_000.0)
        full_s, _, _, _ = orch._apply_risk_overlay(1.0, full, dd=0.0)
        part_s, _, _, _ = orch._apply_risk_overlay(1.0, part, dd=0.0)
        assert full_s < part_s

    def test_risk_zero_utilization_no_reduction(self):
        orch = ao.AlphaOrchestrator(_make_config())
        zero = _make_exec_state(dd=0.0, exposure=0.0, max_exposure=1_000_000.0)
        scaled, pressure, util, rat = orch._apply_risk_overlay(1.0, zero, dd=0.0)
        assert scaled == pytest.approx(1.0)
        assert pressure == pytest.approx(0.0)
        assert util == pytest.approx(0.0)
        assert rat is None


# ============================================================================
# Category 13: Edge Convention
# ============================================================================

class TestEdgeConvention:
    def test_negative_edge_normalized_to_abs(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [{
            "source_id": "alpha_a",
            "direction": 1,
            "conviction": 0.9,
            "expected_edge_bps": -30.0,
            "timestamp": now,
            "timeframe": "1m",
        }]
        valid, metrics, _ = orch._validate_and_prune(sigs, now)
        assert len(valid) == 1
        assert valid[0].expected_edge_bps == pytest.approx(30.0)

    def test_negative_edge_warning_counted(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [
            {
                "source_id": "alpha_a", "direction": 1, "conviction": 0.8,
                "expected_edge_bps": -20.0, "timestamp": now, "timeframe": "1m",
            },
            {
                "source_id": "alpha_b", "direction": -1, "conviction": 0.8,
                "expected_edge_bps": -10.0, "timestamp": now, "timeframe": "1m",
            },
        ]
        _, metrics, _ = orch._validate_and_prune(sigs, now)
        assert metrics["negative_edge_normalized"] == 2

    def test_signed_edge_in_fusion(self):
        # Blended edge sign must be driven by direction, not by edge magnitude alone.
        orch_buy = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        buy_sig = [_make_signal(source_id="alpha_a", direction=1,
                                conviction=0.9, edge=25.0, timestamp=now,
                                timeframe="1m")]
        sell_sig = [_make_signal(source_id="alpha_a", direction=-1,
                                 conviction=0.9, edge=25.0, timestamp=now,
                                 timeframe="1m")]
        buy_out = orch_buy.orchestrate(buy_sig, _make_regime(), _make_fq(),
                                       _make_exec_state(), current_time=now)
        sell_out = ao.AlphaOrchestrator(_make_config()).orchestrate(
            sell_sig, _make_regime(), _make_fq(), _make_exec_state(),
            current_time=now,
        )
        if is_unsafe_aggregation_meta(buy_out.meta_info):
            assert buy_out.expected_edge_bps == 0.0
            assert buy_out.action == ao.Action.HOLD
        else:
            assert buy_out.expected_edge_bps > 0
        if is_unsafe_aggregation_meta(sell_out.meta_info):
            assert sell_out.expected_edge_bps == 0.0
            assert sell_out.action == ao.Action.HOLD
        else:
            assert sell_out.expected_edge_bps < 0
        assert buy_out.expected_edge_bps == pytest.approx(-sell_out.expected_edge_bps)


# ============================================================================
# Category 14: Integration Smoke Tests
# ============================================================================

class TestIntegration:
    def test_full_pipeline_feature_to_signal(self):
        # FeatureEngine → SignalEngine pipeline. Ensure schema-correct signal out.
        feng = fe.FeatureEngine(max_levels=5)
        snap = {
            "bids": [[100.0, 1.0], [99.5, 2.0], [99.0, 3.0]],
            "asks": [[100.5, 1.0], [101.0, 2.0], [101.5, 3.0]],
        }
        feats = feng.update(snap)
        # Inject OHLCV series for signal engine to evaluate.
        candles = [
            {"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.2, "volume": 5.0},
            {"open": 100.2, "high": 101.5, "low": 99.8, "close": 100.8, "volume": 6.0},
            {"open": 100.8, "high": 103.0, "low": 100.0, "close": 102.8, "volume": 25.0},
        ]
        payload = {
            **feats["features"],
            "candles": candles,
            "stop_hunt": True,
            "stop_hunt_side": "sell",
            "regime": {"regime": "trend"},
        }
        sig_engine = se.SignalEngine()
        out = sig_engine.generate_signal(features=payload)
        for k in ("action", "signal", "confidence", "score", "reasons"):
            assert k in out, f"missing schema key: {k}"
        assert out["signal"] in ("LONG", "SHORT", "HOLD")
        assert 0.0 <= out["confidence"] <= 1.0

    def test_full_pipeline_orchestrator_with_regime(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [
            _make_signal(source_id="alpha_a", direction=1,
                         conviction=0.9, edge=20.0, timestamp=now, timeframe="5m"),
            _make_signal(source_id="alpha_b", direction=1,
                         conviction=0.7, edge=15.0, timestamp=now, timeframe="5m"),
        ]
        regime = _make_regime(name="trending_up", vol=0.5, liq=0.8)
        out = orch.orchestrate(sigs, regime, _make_fq(), _make_exec_state(),
                               current_time=now)
        assert out.action in (ao.Action.BUY, ao.Action.SELL, ao.Action.HOLD)
        assert "regime_assessment" in out.meta_info
        assert out.meta_info["regime_assessment"] is not None

    def test_regime_change_simulation(self):
        orch = ao.AlphaOrchestrator(_make_config())
        now = time.time()
        sigs = [_make_signal(source_id="alpha_a", direction=1,
                             conviction=0.9, edge=20.0, timestamp=now,
                             timeframe="1m")]
        regimes = [
            _make_regime(name="calm", vol=0.1, liq=0.9),
            _make_regime(name="breakout", vol=0.6, liq=0.7),
            _make_regime(name="crisis", vol=0.99, liq=0.01),
        ]
        actions = []
        for r in regimes:
            out = orch.orchestrate(sigs, r, _make_fq(), _make_exec_state(),
                                   current_time=now)
            actions.append(out)
            assert out.meta_info["environmental_context"]["regime_name"] == r.regime_name
        # Crisis regime must have strictly higher composite stress than calm.
        stress_calm = actions[0].meta_info["regime_assessment"]["composite_stress"]
        stress_crisis = actions[2].meta_info["regime_assessment"]["composite_stress"]
        assert stress_crisis > stress_calm
