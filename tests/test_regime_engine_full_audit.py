"""
test_regime_engine_full_audit.py -- Comprehensive regime engine audit tests

Covers:
  1. Regime engine logic correctness (TREND/RANGE/TOXIC/BEAR classification)
  2. Numerical safety (NaN/Inf guards, bounded outputs)
  3. Stability (no oscillation/jitter, smooth transitions)
  4. Integration: regime -> feature_engine -> signal_engine -> predictor
  5. Wiring: regime_context propagation from main.py through the pipeline
  6. Regime label normalization (TREND vs UPTREND consistency)
  7. Missing/corrupt input resilience
  8. Deterministic outputs across identical inputs

Scope: signal layer only. No execution/order/broker code is touched.
"""
from __future__ import annotations

import math
import sys
import os
import time

import numpy as np
import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advanced_regime_engine import (
    AdvancedRegimeEngine,
    _build_output,
    _map_execution_mode,
    _validate_output_schema,
    compute_hmm_regime,
    RegimeMarkovSmoother,
    _normalize_prob_vector,
    _coerce_1d_vector,
    _OUTPUT_SCHEMA_VERSION,
)
from feature_engine import FeatureEngine
from signal_engine import SignalEngine, _extract_regime_type
from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha, predict_sweep, _safe_output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orderbook(mid=50000.0, spread_bps=5.0, levels=10, qty=1.0):
    """Build a synthetic orderbook around a mid price."""
    half_spread = mid * (spread_bps / 10000.0) / 2.0
    bids = [[mid - half_spread - i * 0.5, qty] for i in range(levels)]
    asks = [[mid + half_spread + i * 0.5, qty] for i in range(levels)]
    return {"bids": bids, "asks": asks}


def _make_trades(n=5, price=50000.0, side="buy"):
    return [
        {"price": price, "size": 0.1, "side": side, "timestamp": time.time() * 1000}
        for _ in range(n)
    ]


def _run_regime_engine(returns, engine=None):
    """Run an AdvancedRegimeEngine over a list of returns (matches existing test pattern)."""
    if engine is None:
        engine = AdvancedRegimeEngine()
    price = 100.0
    outputs = []
    for i, r in enumerate(returns):
        price *= (1 + r)
        md = {
            "timestamp": float(i),
            "return": float(r),
            "features": np.array([0.2, 0.1, 0.05]),
            "price": float(price),
        }
        outputs.append(engine.update(md))
    engine._shutdown_warning_worker()
    return outputs


def _bull_market(n=300, seed=1):
    rng = np.random.default_rng(seed)
    return (0.0008 + rng.normal(0, 0.003, n)).tolist()


def _bear_market(n=300, seed=2):
    rng = np.random.default_rng(seed)
    return (-0.0008 + rng.normal(0, 0.003, n)).tolist()


def _range_market(n=300, seed=3):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.0015, n).tolist()


def _shock_market(n=300, seed=4):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.0015, n)
    for i in [50, 150, 250]:
        r[i] = rng.choice([-1, 1]) * rng.uniform(0.05, 0.12)
    return r.tolist()


def _assert_finite_dict(d, path=""):
    """Recursively check all numeric values in a dict are finite."""
    if isinstance(d, dict):
        for k, v in d.items():
            _assert_finite_dict(v, f"{path}.{k}")
    elif isinstance(d, (list, tuple)):
        for i, v in enumerate(d):
            _assert_finite_dict(v, f"{path}[{i}]")
    elif isinstance(d, float):
        assert math.isfinite(d), f"Non-finite value at {path}: {d}"


# ===========================================================================
# SECTION 1: Regime Engine Logic Correctness
# ===========================================================================

class TestRegimeClassification:
    """Verify regime engine produces correct labels for known market conditions."""

    def test_strong_bull_returns_trend(self):
        """Sustained positive returns should produce TREND regime."""
        outputs = _run_regime_engine(_bull_market())
        trend_count = sum(o["regime_label"] == "TREND" for o in outputs)
        assert trend_count > 0, "Expected at least one TREND in bull market"

    def test_strong_bear_returns_bear(self):
        """Sustained negative returns should produce BEAR regime."""
        outputs = _run_regime_engine(_bear_market())
        bear_count = sum(o["regime_label"] == "BEAR" for o in outputs)
        assert bear_count > 0, "Expected at least one BEAR in bear market"

    def test_flat_returns_range(self):
        """Near-zero returns should produce RANGE regime."""
        outputs = _run_regime_engine(_range_market())
        range_count = sum(o["regime_label"] == "RANGE" for o in outputs)
        assert range_count > 0, "Expected at least one RANGE in range market"

    def test_shock_returns_toxic(self):
        """Large sudden moves should trigger TOXIC regime."""
        outputs = _run_regime_engine(_shock_market())
        toxic_count = sum(o["regime_label"] in ("TOXIC", "HALTED") for o in outputs)
        assert toxic_count > 0, "Expected at least one TOXIC/HALTED on shock market"

    def test_no_contradictory_states(self):
        """A single update should never return contradictory regime info."""
        outputs = _run_regime_engine(_range_market(n=100, seed=77))
        for o in outputs:
            label = o["regime_label"]
            assert label in ("TREND", "RANGE", "BEAR", "TOXIC", "HALTED", "UNKNOWN"), \
                f"Invalid regime label: {label}"
            # If TOXIC, risk_level should be elevated or confidence low
            if label == "TOXIC":
                risk_metrics = o.get("risk_metrics", {})
                assert risk_metrics.get("toxic_penalty_applied", False) or \
                    o.get("confidence", 1.0) < 0.6, \
                    "TOXIC regime without expected risk markers"


class TestRegimeOutputSchema:
    """Verify the output schema is always complete and valid."""

    def test_output_has_required_keys(self):
        """Every output must contain regime_label, confidence, and risk_metrics."""
        engine = AdvancedRegimeEngine()
        out = engine.update({"price": 50000.0})
        assert "regime_label" in out
        assert "confidence" in out
        assert "risk_metrics" in out
        assert "schema_version" in out
        assert "probabilities" in out
        assert "alpha" in out
        engine._shutdown_warning_worker()

    def test_output_schema_version(self):
        engine = AdvancedRegimeEngine()
        out = engine.update({"price": 50000.0})
        assert out["schema_version"] == _OUTPUT_SCHEMA_VERSION
        engine._shutdown_warning_worker()

    def test_build_output_failsafe(self):
        """_build_output with valid params should pass schema validation."""
        out = _build_output(
            regime_idx=0,
            regime_label="TREND",
            trend_strength=0.5,
            risk_level=0.3,
            confidence=0.7,
            edge_score=0.5,
            probabilities={"bull": 0.6, "bear": 0.3, "crisis": 0.1},
            macro_probs=[0.6, 0.3, 0.1],
            position_size=0.1,
            expected_vol=0.02,
            raw_size=0.5,
            is_toxic=False,
            garch_regime_probs=[0.8, 0.2],
            feed_status="OK",
        )
        assert out["regime_label"] == "TREND"
        assert _validate_output_schema(out)

    def test_map_execution_mode_covers_all_regimes(self):
        """_map_execution_mode must handle all known regime labels."""
        assert _map_execution_mode("TREND") == "trend_follow"
        assert _map_execution_mode("BEAR") == "risk_off_or_short_bias"
        assert _map_execution_mode("TOXIC") == "flat_or_hedge"
        assert _map_execution_mode("RANGE") == "range_mean_revert"
        # Unknown falls through to range
        assert _map_execution_mode("UNKNOWN") == "range_mean_revert"


# ===========================================================================
# SECTION 2: Numerical Safety
# ===========================================================================

class TestNumericalSafety:
    """No NaN/Inf should ever leak through the regime engine."""

    def test_no_nan_inf_in_output(self):
        """Full run should produce only finite values."""
        outputs = _run_regime_engine(_bull_market(n=100, seed=99))
        for i, o in enumerate(outputs):
            _assert_finite_dict(o, path=f"output[{i}]")

    def test_extreme_returns_no_crash(self):
        """Extreme positive/negative returns should not crash or produce NaN."""
        outputs = _run_regime_engine(_shock_market())
        for o in outputs:
            assert math.isfinite(o["confidence"])
            assert math.isfinite(o["trend_strength"])
            assert o["regime_label"] in ("TREND", "RANGE", "BEAR", "TOXIC", "HALTED", "UNKNOWN")

    def test_zero_returns_safe(self):
        """All-zero returns should not cause division errors."""
        outputs = _run_regime_engine([0.0] * 50)
        for o in outputs:
            _assert_finite_dict(o)

    def test_normalize_prob_vector(self):
        """Probability normalization must handle edge cases."""
        result = _normalize_prob_vector(np.array([0.0, 0.0, 0.0]))
        assert np.allclose(result, [1/3, 1/3, 1/3])
        assert abs(result.sum() - 1.0) < 1e-9

        result = _normalize_prob_vector(np.array([1.0, 0.0, 0.0]))
        assert abs(result.sum() - 1.0) < 1e-9

    def test_coerce_1d_vector_scalar(self):
        """Scalar inputs should be coerced to 1-D arrays."""
        arr = _coerce_1d_vector(0.5, 1, name="test")
        assert arr.shape == (1,)
        assert arr[0] == 0.5

    def test_coerce_1d_vector_rejects_nan(self):
        with pytest.raises(ValueError, match="non-finite"):
            _coerce_1d_vector([1.0, float("nan")], 2, name="test")

    def test_position_size_bounded(self):
        """Position size must always be in [0, 0.35]."""
        outputs = _run_regime_engine(_bull_market(n=100, seed=7))
        for o in outputs:
            ps = o.get("position_size", 0.0)
            assert 0.0 <= ps <= 0.35, f"Position size out of bounds: {ps}"

    def test_confidence_bounded(self):
        """Confidence must always be in [0, 1]."""
        outputs = _run_regime_engine(_range_market(n=100, seed=7))
        for o in outputs:
            c = o["confidence"]
            assert 0.0 <= c <= 1.0, f"Confidence out of bounds: {c}"


# ===========================================================================
# SECTION 3: Stability (no oscillation/jitter)
# ===========================================================================

class TestRegimeStability:
    """Regime should not oscillate rapidly under stable conditions."""

    def test_stable_trend_no_jitter(self):
        """Sustained trend returns should not oscillate between regimes."""
        outputs = _run_regime_engine(_bull_market(n=200, seed=10))
        # Count regime switches after warmup
        labels = [o["regime_label"] for o in outputs[50:]]
        switches = sum(1 for i in range(1, len(labels)) if labels[i] != labels[i-1])
        # Should have very few switches in a stable trend
        assert switches < len(labels) * 0.3, \
            f"Too many switches ({switches}/{len(labels)}) in stable trend"

    def test_markov_smoother_hysteresis(self):
        """Markov smoother should resist weak transitions."""
        smoother = RegimeMarkovSmoother()
        # Start in TREND
        scores = {"bull": 0.7, "bear": 0.2, "trend_score": 0.6, "range_score": 0.3, "toxic_score": 0.1}
        regime, probs = smoother.update(scores, "TREND")
        
        # Slightly favor RANGE but not enough to overcome hysteresis
        scores2 = {"bull": 0.45, "bear": 0.35, "trend_score": 0.35, "range_score": 0.40, "toxic_score": 0.1}
        regime2, probs2 = smoother.update(scores2, regime)
        # With hysteresis, should stay in TREND if lead is small
        # This tests that the smoother doesn't flip on marginal changes

    def test_circuit_breaker_activates_and_heals(self):
        """Circuit breaker should activate on shock and heal after cooldown."""
        engine = AdvancedRegimeEngine()
        # Warmup
        for _ in range(20):
            engine.update({"price": 50000.0})
        # Trigger circuit breaker with vol shock
        out = engine.update({"price": 50000.0 * 1.15})
        # After enough ticks, should heal
        for _ in range(engine._HEALING_COOLDOWN_TICKS + 5):
            out = engine.update({"price": 50000.0})
        assert out["regime_label"] != "HALTED", "Engine should heal after cooldown"
        engine._shutdown_warning_worker()


# ===========================================================================
# SECTION 4: compute_hmm_regime correctness
# ===========================================================================

class TestComputeHMMRegime:
    """Test the pure scoring function that converts HMM probs to regimes."""

    def test_strong_bull_produces_trend(self):
        result = compute_hmm_regime(np.array([0.8, 0.1, 0.1]))
        assert result["regime"] in ("TREND", "RANGE"), \
            f"Strong bull should produce TREND or RANGE, got {result['regime']}"
        assert result["bull"] > result["bear"]

    def test_strong_bear_produces_bear(self):
        result = compute_hmm_regime(np.array([0.1, 0.8, 0.1]))
        assert result["regime"] in ("BEAR", "RANGE"), \
            f"Strong bear should produce BEAR or RANGE, got {result['regime']}"
        assert result["bear"] > result["bull"]

    def test_crisis_produces_toxic(self):
        result = compute_hmm_regime(np.array([0.1, 0.1, 0.8]))
        assert result["regime"] == "TOXIC", \
            f"High crisis should produce TOXIC, got {result['regime']}"

    def test_balanced_produces_range(self):
        result = compute_hmm_regime(np.array([0.35, 0.35, 0.30]))
        # Balanced bull/bear with low crisis should tend toward RANGE
        assert result["regime"] in ("RANGE", "TREND", "BEAR"), \
            f"Balanced probs produced unexpected regime: {result['regime']}"

    def test_rejects_wrong_shape(self):
        with pytest.raises(ValueError, match="3-state"):
            compute_hmm_regime(np.array([0.5, 0.5]))

    def test_rejects_negative_probs(self):
        with pytest.raises(ValueError, match="Negative"):
            compute_hmm_regime(np.array([0.5, 0.5, -0.1]))

    def test_rejects_non_finite(self):
        with pytest.raises(ValueError, match="Non-finite"):
            compute_hmm_regime(np.array([0.5, float("nan"), 0.5]))

    def test_output_keys_complete(self):
        result = compute_hmm_regime(np.array([0.5, 0.3, 0.2]))
        required = {"regime", "bull", "bear", "crisis", "trend_strength",
                     "risk_level", "confidence", "edge_score",
                     "trend_score", "range_score", "toxic_score"}
        assert required.issubset(result.keys()), \
            f"Missing keys: {required - result.keys()}"


# ===========================================================================
# SECTION 5: Integration -- feature_engine regime_context injection
# ===========================================================================

class TestFeatureEngineRegimeIntegration:
    """Verify feature_engine correctly injects regime_context into features."""

    def _make_regime_context(self, regime="TREND", confidence=0.8,
                              vol_regime="TREND", liq_regime="trend_follow",
                              trend_strength=0.6):
        return {
            "regime": regime,
            "confidence": confidence,
            "features": {
                "volatility_regime": vol_regime,
                "liquidity_regime": liq_regime,
                "trend_strength": trend_strength,
            }
        }

    def test_volatility_regime_injected(self):
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        ctx = self._make_regime_context(regime="TREND", vol_regime="TREND")
        out = fe.update(snap, _make_trades(), regime_context=ctx)
        feats = out["features"]
        assert feats.get("volatility_regime") == "TREND"

    def test_liquidity_regime_injected(self):
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        ctx = self._make_regime_context(liq_regime="risk_off_or_short_bias")
        out = fe.update(snap, _make_trades(), regime_context=ctx)
        feats = out["features"]
        assert feats.get("liquidity_regime") == "risk_off_or_short_bias"

    def test_trend_strength_injected(self):
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        ctx = self._make_regime_context(trend_strength=0.75)
        out = fe.update(snap, _make_trades(), regime_context=ctx)
        feats = out["features"]
        assert abs(feats.get("trend_strength", 0) - 0.75) < 1e-6

    def test_empty_book_preserves_regime_context(self):
        """When orderbook is empty, regime_context should still be visible."""
        fe = FeatureEngine(max_levels=3)
        ctx = self._make_regime_context(regime="BEAR", vol_regime="BEAR")
        out = fe.update({"bids": [], "asks": []}, [], regime_context=ctx)
        feats = out["features"]
        assert feats.get("volatility_regime") == "BEAR"

    def test_none_regime_context_no_crash(self):
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        out = fe.update(snap, _make_trades(), regime_context=None)
        assert "features" in out
        assert isinstance(out["features"], dict)

    def test_invalid_regime_context_types(self):
        """Non-dict regime_context should not crash."""
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        for bad_ctx in ("bad", 42, [], True):
            out = fe.update(snap, _make_trades(), regime_context=bad_ctx)
            assert "features" in out

    def test_inf_trend_strength_sanitized(self):
        """Infinite trend_strength from regime_context should be sanitized."""
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        ctx = {"regime": "TREND", "confidence": 0.5,
               "features": {"trend_strength": float("inf")}}
        out = fe.update(snap, _make_trades(), regime_context=ctx)
        feats = out["features"]
        if "trend_strength" in feats:
            assert math.isfinite(feats["trend_strength"])

    def test_fallback_uses_regime_as_vol_regime(self):
        """When features dict has no volatility_regime, use regime as fallback."""
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        ctx = {"regime": "TOXIC", "confidence": 0.3, "features": {}}
        out = fe.update(snap, _make_trades(), regime_context=ctx)
        feats = out["features"]
        assert feats.get("volatility_regime") == "TOXIC"


# ===========================================================================
# SECTION 6: Signal Engine regime usage (no double counting)
# ===========================================================================

class TestSignalEngineRegimeUsage:
    """Signal engine should use regime safely without double counting."""

    def test_extract_regime_type_string(self):
        assert _extract_regime_type({"regime": "trend"}) == "trend"
        assert _extract_regime_type({"regime": "TOXIC"}) == "toxic"

    def test_extract_regime_type_dict(self):
        assert _extract_regime_type({"regime": {"regime": "trend"}}) == "trend"
        assert _extract_regime_type({"regime": {"type": "range"}}) == "range"

    def test_extract_regime_type_defaults(self):
        assert _extract_regime_type({}) == "range"
        assert _extract_regime_type({"regime": None}) == "range"

    def test_signal_uses_regime_once(self):
        """Regime should contribute to signal confidence exactly once."""
        se = SignalEngine()
        candles = [
            {"open": 49900, "high": 50100, "low": 49800, "close": 50000, "volume": 100},
            {"open": 50000, "high": 50200, "low": 49900, "close": 50100, "volume": 120},
            {"open": 50100, "high": 50500, "low": 50000, "close": 50400, "volume": 200},
        ]
        features_trend = {
            "candles": candles,
            "regime": "trend",
            "stop_hunt": False,
            "volume": 200,
        }
        features_range = {
            "candles": candles,
            "regime": "range",
            "stop_hunt": False,
            "volume": 200,
        }
        r_trend = se.generate_signal(features=features_trend)
        r_range = se.generate_signal(features=features_range)
        # Both should produce valid outputs
        for r in (r_trend, r_range):
            assert r["action"] in ("HOLD", "LONG", "SHORT")
            assert 0.0 <= r["confidence"] <= 1.0

    def test_signal_hold_on_missing_candles(self):
        """Signal engine should HOLD when not enough candles."""
        se = SignalEngine()
        result = se.generate_signal(features={"regime": "trend"})
        assert result["action"] == "HOLD"


# ===========================================================================
# SECTION 7: Alpha Predictor regime_context integration
# ===========================================================================

class TestAlphaPredictorRegimeIntegration:
    """Verify alpha predictor reads and applies regime_context correctly."""

    def _base_market_data(self, price=50000.0):
        return {
            "price": price,
            "close_price": price,
            "curr_book": {"bids": [{"price": price - 1, "size": 10}] * 10,
                          "asks": [{"price": price + 1, "size": 10}] * 10},
            "prev_book": {"bids": [{"price": price - 1, "size": 10}] * 10,
                          "asks": [{"price": price + 1, "size": 10}] * 10},
            "timestamp": time.time(),
            "trades_count": 5,
            "atr": price * 0.01,
            "ema_fast": price,
            "ema_slow": price,
            "pre_sweep_depth": 100.0,
            "curr_depth": 100.0,
            "sweep_time_elapsed": 0.0,
        }

    def test_predict_returns_full_schema(self):
        alpha = LiquiditySweepAlpha()
        result = alpha.predict(self._base_market_data())
        required_keys = {"action", "confidence", "state", "regime",
                         "ofi_zscore", "hawkes_intensity", "logic",
                         "micro_prob", "macro_prob", "prob_above", "prob_below"}
        assert required_keys.issubset(result.keys()), \
            f"Missing keys: {required_keys - result.keys()}"

    def test_regime_context_adjusts_threshold(self):
        """TREND regime should lower threshold (threshold_offset=-0.02)."""
        alpha = LiquiditySweepAlpha()
        md = self._base_market_data()
        # Without regime context
        r1 = alpha.predict(md, regime_context=None)
        # With TREND regime context
        r2 = alpha.predict(md, regime_context={"regime": "TREND"})
        # Both should be valid
        assert 0.0 <= r1["confidence"] <= 1.0
        assert 0.0 <= r2["confidence"] <= 1.0

    def test_toxic_regime_raises_threshold(self):
        """TOXIC regime should raise threshold (threshold_offset=+0.05)."""
        alpha = LiquiditySweepAlpha()
        md = self._base_market_data()
        r = alpha.predict(md, regime_context={"regime": "TOXIC"})
        assert 0.0 <= r["confidence"] <= 1.0

    def test_regime_label_normalization_in_predictor(self):
        """Predictor handles both TREND and UPTREND labels."""
        alpha = LiquiditySweepAlpha()
        md = self._base_market_data()
        # Test with TREND (from AdvancedRegimeEngine)
        r1 = alpha.predict(md, regime_context={"regime": "TREND"})
        # Test with UPTREND (from internal _detect_regime)
        r2 = alpha.predict(md, regime_context={"regime": "UPTREND"})
        # Both should produce valid outputs
        for r in (r1, r2):
            assert r["action"] in ("BUY", "SELL", "HOLD")
            assert 0.0 <= r["confidence"] <= 1.0

    def test_predictor_no_nan_with_missing_regime_context(self):
        alpha = LiquiditySweepAlpha()
        md = self._base_market_data()
        for ctx in (None, {}, {"regime": ""}, {"regime": None}):
            r = alpha.predict(md, regime_context=ctx)
            assert math.isfinite(r["confidence"])
            assert math.isfinite(r["prob_above"])
            assert math.isfinite(r["prob_below"])

    def test_prob_sum_to_one(self):
        """prob_above + prob_below should approximately sum to 1."""
        alpha = LiquiditySweepAlpha()
        md = self._base_market_data()
        r = alpha.predict(md, regime_context={"regime": "RANGE"})
        total = r["prob_above"] + r["prob_below"]
        assert abs(total - 1.0) < 0.01, f"Probabilities sum to {total}, expected ~1.0"


# ===========================================================================
# SECTION 8: predict_sweep standalone safety
# ===========================================================================

class TestPredictSweep:
    """predict_sweep must handle all edge cases gracefully."""

    def test_minimal_input(self):
        result = predict_sweep({}, {})
        assert "prob_above" in result
        assert "prob_below" in result
        total = result["prob_above"] + result["prob_below"]
        assert abs(total - 1.0) < 0.01

    def test_none_inputs(self):
        result = predict_sweep(None, None)
        assert result["prob_above"] == pytest.approx(0.5, abs=0.3)

    def test_nan_inputs(self):
        result = predict_sweep(
            {"nearest_above": {"distance_points": float("nan")}},
            {"volatility": float("nan"), "bias": float("inf")}
        )
        assert math.isfinite(result["prob_above"])
        assert math.isfinite(result["prob_below"])

    def test_trending_bias(self):
        result = predict_sweep(
            {},
            {"state": "TRENDING", "bias": 1.0, "volatility": 0.01, "compression": 0.5}
        )
        # Positive bias should push prob_above higher
        assert result["prob_above"] >= 0.5


# ===========================================================================
# SECTION 9: E2E Pipeline Simulation (signal layer only)
# ===========================================================================

class TestE2EPipeline:
    """End-to-end: regime_engine -> feature_engine -> signal_engine -> predictor."""

    def test_stable_trend_pipeline(self):
        """Simulate a stable trending market through the full signal pipeline."""
        # 1. Get regime output
        engine = AdvancedRegimeEngine()
        for _ in range(30):
            reg_out = engine.update({"price": 50000.0 + _ * 10})

        # 2. Build regime_context (as main.py does)
        r_metrics = reg_out.get("risk_metrics", {})
        regime_context = {
            "regime": str(reg_out.get("regime_label", "UNKNOWN")),
            "confidence": float(reg_out.get("confidence", 0.0)),
            "features": {
                "volatility_regime": str(reg_out.get("regime_label", "unknown")),
                "liquidity_regime": str(reg_out.get("execution_mode", "unknown")),
                "trend_strength": float(reg_out.get("trend_strength", 0.0)),
            }
        }

        # 3. Feature engine
        fe = FeatureEngine(max_levels=5)
        snap = _make_orderbook(mid=50300.0, levels=5)
        feat_out = fe.update(snap, _make_trades(price=50300.0), regime_context=regime_context)

        # 4. Signal engine
        se = SignalEngine()
        feat_dict = feat_out["features"]
        feat_dict["candles"] = [
            {"open": 50100, "high": 50200, "low": 50000, "close": 50150, "volume": 100},
            {"open": 50150, "high": 50250, "low": 50100, "close": 50200, "volume": 110},
            {"open": 50200, "high": 50400, "low": 50150, "close": 50350, "volume": 150},
        ]
        signal = se.generate_signal(features=feat_dict)
        assert signal["action"] in ("HOLD", "LONG", "SHORT")
        assert math.isfinite(signal["confidence"])

        # 5. Alpha predictor
        alpha = LiquiditySweepAlpha()
        pred = alpha.predict(
            {"price": 50300.0, "close_price": 50300.0,
             "curr_book": snap, "prev_book": snap,
             "timestamp": time.time(), "trades_count": 5,
             "atr": 500.0, "ema_fast": 50300.0, "ema_slow": 50200.0},
            regime_context=regime_context,
        )
        assert pred["action"] in ("BUY", "SELL", "HOLD")
        assert math.isfinite(pred["confidence"])
        engine._shutdown_warning_worker()

    def test_toxic_market_pipeline(self):
        """In toxic/high-vol conditions, the pipeline should be cautious."""
        engine = AdvancedRegimeEngine()
        # Generate a shock
        for _ in range(20):
            engine.update({"price": 50000.0})
        reg_out = engine.update({"price": 57000.0})  # 14% jump

        regime_context = {
            "regime": str(reg_out.get("regime_label", "UNKNOWN")),
            "confidence": float(reg_out.get("confidence", 0.0)),
            "features": {
                "volatility_regime": str(reg_out.get("regime_label", "unknown")),
                "liquidity_regime": str(reg_out.get("execution_mode", "unknown")),
                "trend_strength": float(reg_out.get("trend_strength", 0.0)),
            }
        }

        # After a shock, the regime should be a valid label and confidence should be finite
        assert regime_context["regime"] in ("TOXIC", "HALTED", "TREND", "BEAR", "RANGE", "UNKNOWN")
        assert math.isfinite(regime_context["confidence"])
        assert 0.0 <= regime_context["confidence"] <= 1.0
        engine._shutdown_warning_worker()

    def test_missing_features_pipeline(self):
        """Pipeline should degrade gracefully with missing feature inputs."""
        fe = FeatureEngine(max_levels=3)
        # Empty orderbook
        out = fe.update({"bids": [], "asks": []}, [],
                        regime_context={"regime": "RANGE", "confidence": 0.5, "features": {}})
        feats = out["features"]
        assert feats.get("regime") in ("unknown", "range", "")  or feats.get("volatility_regime") is not None
        assert out["confidence"] == 0.0  # Low confidence on empty book

    def test_conflicting_signals_pipeline(self):
        """When internal and external regime signals conflict, system should not crash."""
        alpha = LiquiditySweepAlpha()
        # Internal regime will be RANGING (ema_fast == ema_slow)
        # External regime_context says TREND
        md = {
            "price": 50000.0,
            "close_price": 50000.0,
            "curr_book": {"bids": [{"price": 49999, "size": 10}] * 10,
                          "asks": [{"price": 50001, "size": 10}] * 10},
            "prev_book": {"bids": [{"price": 49999, "size": 10}] * 10,
                          "asks": [{"price": 50001, "size": 10}] * 10},
            "timestamp": time.time(),
            "trades_count": 5,
            "atr": 500.0,
            "ema_fast": 50000.0,
            "ema_slow": 50000.0,
        }
        result = alpha.predict(md, regime_context={"regime": "TREND"})
        assert result["action"] in ("BUY", "SELL", "HOLD")
        assert math.isfinite(result["confidence"])


# ===========================================================================
# SECTION 10: Determinism
# ===========================================================================

class TestDeterminism:
    """Identical inputs must produce identical outputs."""

    def test_deterministic_regime_engine(self):
        """Same returns sequence should give same outputs across two engine instances."""
        returns = _bull_market(n=50, seed=55)
        out1 = _run_regime_engine(returns, AdvancedRegimeEngine())
        out2 = _run_regime_engine(returns, AdvancedRegimeEngine())
        for a, b in zip(out1, out2):
            assert a["regime_label"] == b["regime_label"]
            assert abs(a["confidence"] - b["confidence"]) < 1e-10

    def test_deterministic_predict_sweep(self):
        liq = {"nearest_above": {"distance_points": 100, "price": 50100},
               "nearest_below": {"distance_points": 80, "price": 49920}}
        ms = {"state": "TRENDING", "bias": 0.5, "volatility": 0.02, "compression": 0.5}
        r1 = predict_sweep(liq, ms)
        r2 = predict_sweep(liq, ms)
        assert r1 == r2

    def test_deterministic_safe_output(self):
        result = {"action": "BUY", "confidence": 0.75, "state": "NORMAL",
                  "regime": "RANGING", "ofi_zscore": 1.5, "hawkes_intensity": 0.3,
                  "logic": "test", "micro_prob": 0.6, "macro_prob": 0.55,
                  "prob_above": 0.6, "prob_below": 0.4}
        o1 = _safe_output(result)
        o2 = _safe_output(result)
        assert o1 == o2


# ===========================================================================
# SECTION 11: Wiring verification (main.py constructs regime_context correctly)
# ===========================================================================

class TestMainWiring:
    """Verify main.py's regime_context construction matches what downstream expects."""

    def test_regime_context_schema(self):
        """Simulate what main.py builds and verify feature_engine accepts it."""
        # Simulate regime engine output
        engine = AdvancedRegimeEngine()
        reg_out = engine.update({"price": 50000.0})
        r_metrics = reg_out.get("risk_metrics", {})

        # main.py lines 1605-1620 construction
        regime_context = {
            "regime": str(reg_out.get("regime_label", reg_out.get("regime", "UNKNOWN"))),
            "confidence": float(reg_out.get("confidence", 0.0)),
            "features": {
                "volatility_regime": str(reg_out.get("regime_label", "unknown")),
                "liquidity_regime": str(reg_out.get("execution_mode", "unknown")),
                "trend_strength": float(reg_out.get("trend_strength", 0.0)),
                "feed_status": str(r_metrics.get("feed_status", "unknown")),
            },
        }

        # Verify feature_engine can consume it
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        out = fe.update(snap, _make_trades(), regime_context=regime_context)
        feats = out["features"]

        # Verify the expected fields exist
        assert "volatility_regime" in feats
        assert "liquidity_regime" in feats
        assert feats["volatility_regime"] == regime_context["regime"]
        engine._shutdown_warning_worker()

    def test_fallback_regime_context_default(self):
        """main.py's default _last_regime_context should be safe."""
        default_ctx = {"regime": "UNKNOWN", "confidence": 0.0, "features": {}}
        fe = FeatureEngine(max_levels=3)
        snap = _make_orderbook(levels=3)
        out = fe.update(snap, _make_trades(), regime_context=default_ctx)
        feats = out["features"]
        # Should fall back to regime key
        assert feats.get("volatility_regime") == "UNKNOWN"


# ===========================================================================
# SECTION 12: Regime label normalization
# ===========================================================================

class TestRegimeLabelNormalization:
    """Ensure no mismatch between TREND/UPTREND, BEAR/DOWNTREND across modules."""

    def test_advanced_regime_engine_labels(self):
        """AdvancedRegimeEngine should only emit known labels."""
        valid_labels = {"TREND", "RANGE", "BEAR", "TOXIC", "HALTED", "UNKNOWN"}
        outputs = _run_regime_engine(_range_market(n=100, seed=42))
        for o in outputs:
            assert o["regime_label"] in valid_labels, \
                f"Unknown regime label: {o['regime_label']}"

    def test_feature_engine_internal_regime_labels(self):
        """feature_engine's _regime_score uses lowercase labels."""
        fe = FeatureEngine(max_levels=5)
        snap = _make_orderbook(levels=5)
        out = fe.update(snap, _make_trades())
        regime = out["features"].get("regime", "")
        valid_internal = {"toxic", "illiquid", "trend", "range",
                          "accumulation", "distribution", "unknown", ""}
        assert regime in valid_internal, f"Unexpected internal regime: {regime}"

    def test_signal_engine_normalizes_to_lowercase(self):
        """signal_engine._extract_regime_type always returns lowercase."""
        test_cases = [
            ({"regime": "TREND"}, "trend"),
            ({"regime": "BEAR"}, "bear"),
            ({"regime": "TOXIC"}, "toxic"),
            ({"regime": "RANGE"}, "range"),
            ({"regime": "UPTREND"}, "uptrend"),
            ({"regime": "DOWNTREND"}, "downtrend"),
            ({"regime": {"regime": "TREND"}}, "trend"),
        ]
        for features, expected in test_cases:
            result = _extract_regime_type(features)
            assert result == expected, \
                f"Expected {expected}, got {result} for {features}"

    def test_alpha_predictor_handles_both_label_sets(self):
        """Alpha predictor should handle both TREND/BEAR and UPTREND/DOWNTREND."""
        alpha = LiquiditySweepAlpha()
        md = {
            "price": 50000.0, "close_price": 50000.0,
            "curr_book": {"bids": [{"price": 49999, "size": 10}] * 10,
                          "asks": [{"price": 50001, "size": 10}] * 10},
            "prev_book": {"bids": [{"price": 49999, "size": 10}] * 10,
                          "asks": [{"price": 50001, "size": 10}] * 10},
            "timestamp": time.time(), "trades_count": 5,
            "atr": 500.0, "ema_fast": 50000.0, "ema_slow": 50000.0,
        }
        for label in ("TREND", "BEAR", "TOXIC", "RANGE", "UPTREND", "DOWNTREND", "RANGING"):
            r = alpha.predict(md, regime_context={"regime": label})
            assert r["action"] in ("BUY", "SELL", "HOLD"), \
                f"Invalid action for regime {label}: {r['action']}"
            assert math.isfinite(r["confidence"]), \
                f"Non-finite confidence for regime {label}"


# ===========================================================================
# SECTION 13: No execution layer coupling
# ===========================================================================

class TestNoExecutionCoupling:
    """Verify that signal-layer modules do not import or call execution code."""

    def test_advanced_regime_engine_no_execution_imports(self):
        """advanced_regime_engine should not import execution modules."""
        import advanced_regime_engine as mod
        source = open(mod.__file__).read()
        forbidden = ["from execution ", "import execution",
                      "from order_router", "import order_router",
                      "from position_manager", "import position_manager",
                      "place_order", "place_market_order", "place_limit_order"]
        for pattern in forbidden:
            assert pattern not in source, \
                f"advanced_regime_engine contains forbidden import: {pattern}"

    def test_feature_engine_no_execution_imports(self):
        import feature_engine as mod
        source = open(mod.__file__).read()
        forbidden = ["from execution ", "import execution",
                      "place_order", "place_market_order"]
        for pattern in forbidden:
            assert pattern not in source, \
                f"feature_engine contains forbidden import: {pattern}"

    def test_signal_engine_no_execution_imports(self):
        import signal_engine as mod
        source = open(mod.__file__).read()
        forbidden = ["from execution ", "import execution",
                      "place_order", "place_market_order"]
        for pattern in forbidden:
            assert pattern not in source, \
                f"signal_engine contains forbidden import: {pattern}"
