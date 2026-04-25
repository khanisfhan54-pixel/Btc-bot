import numpy as np

from advanced_regime_engine import (
    AdvancedRegimeEngine,
    _build_output,
    _safe_int,
    compute_hmm_regime,
)


def test_new_a_timestamp_anchor_preserved_on_vol_shock_early_return():
    engine = AdvancedRegimeEngine(n_states=3, n_features=3, target_vol=0.001)
    engine.update({"return": 0.0001, "features": [0.1, 0.1, 0.1], "timestamp": 1000.0})
    engine.update({"return": 99.0, "features": [0.1, 0.1, 0.1], "timestamp": 1005.0})
    assert engine._last_timestamp == 1005.0, "VOL_SHOCK early return must update _last_timestamp"


def test_new_a_timestamp_anchor_preserved_on_single_tf_missing_features():
    engine = AdvancedRegimeEngine(n_states=3, n_features=3, target_vol=0.02)
    engine.update({"return": 0.001, "features": [0.1, 0.1, 0.1], "timestamp": 2000.0})
    engine.update({"return": 0.001, "timestamp": 2005.0})
    assert engine._last_timestamp == 2005.0, "Missing features early return must update _last_timestamp"
    engine.update({"return": 0.001, "features": [0.1, 0.1, 0.1], "timestamp": 2010.0})
    assert engine._last_valid_dt <= 10.0, "time_delta should be ~5.0 seconds, not ~2010.0"


def test_new_a_timestamp_anchor_preserved_on_mtf_base_features_missing():
    engine = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        target_vol=0.02,
        mtf_weights={"1m": 1.0},
    )
    engine.update(
        {
            "return": 0.001,
            "mtf": {
                "base": {"return": 0.001, "features": [0.1, 0.1, 0.1]},
                "1m": {"return": 0.001, "features": [0.1, 0.1, 0.1]},
            },
            "timestamp": 3000.0,
        }
    )
    engine.update(
        {
            "return": 0.001,
            "mtf": {"base": {"return": 0.001}},
            "timestamp": 3005.0,
        }
    )
    assert engine._last_timestamp == 3005.0


def test_issue_24_range_score_properly_scored_for_balanced_market():
    alpha_balanced = np.array([0.34, 0.33, 0.33])
    result = compute_hmm_regime(alpha_balanced)
    assert result["range_score"] >= 0.0

    alpha_perfect_range = np.array([0.333, 0.333, 0.334])
    result2 = compute_hmm_regime(alpha_perfect_range)
    assert result2["regime"] in ("RANGE", "TOXIC"), (
        "Perfectly balanced market should classify as RANGE or TOXIC, "
        "not TREND or BEAR"
    )


def test_issue_20_mtf_base_only_fallback_shows_degraded_feed_status():
    engine = AdvancedRegimeEngine(n_states=3, n_features=3, target_vol=0.02, mtf_weights={})
    out = engine.update(
        {
            "return": 0.001,
            "mtf": {"base": {"return": 0.001, "features": [0.1, 0.1, 0.1]}},
        }
    )
    primary = out["risk_metrics"]["feed_status"]["primary"]
    assert primary != "OK", f"Base-only MTF should not show OK, got {primary}"
    assert primary == "MTF_PARTIAL_SURVIVAL"


def test_new_b_switch_stability_ema_decays_during_stable_regime():
    engine = AdvancedRegimeEngine(n_states=3, n_features=3, target_vol=0.02)
    for i in range(600):
        engine.update(
            {
                "return": 0.0001 * (1 if i % 2 == 0 else -1),
                "features": [0.1, 0.0, 0.1],
                "timestamp": float(1000 + i),
            }
        )
    assert engine._switch_stability_ema < 0.95
    assert engine._switch_stability_ema > 0.0


def test_issue_17_no_duplicate_warnings_on_tick_order_violation():
    engine = AdvancedRegimeEngine(
        n_states=3,
        n_features=3,
        target_vol=0.02,
        allow_timestamp_free_pnl=True,
    )
    warned_keys = []
    original_warn = engine._warn_rate_limited

    def capture_warn(key, message, cooldown_s=30.0):
        warned_keys.append(key)
        original_warn(key, message, cooldown_s=cooldown_s)

    engine._warn_rate_limited = capture_warn
    engine.update({"return": 0.001, "features": [0.1, 0.1, 0.1], "price": 50000.0})
    warned_keys.clear()

    engine._last_price_tick_id = engine._tick_id + 10
    engine.update({"return": 0.001, "features": [0.1, 0.1, 0.1], "price": 50100.0})
    assert "pnl_tick_order_violation" in warned_keys
    assert "pnl_timestamp_policy_blocked" not in warned_keys


def test_issue_19_include_signal_valid_false_omits_signal_valid_key():
    out = _build_output(
        regime_idx=0,
        regime_label="TREND",
        trend_strength=0.5,
        risk_level=0.1,
        confidence=0.8,
        conviction=0.7,
        edge_score=0.6,
        probabilities={"bull": 0.6, "bear": 0.3, "crisis": 0.1},
        macro_probs=[0.6, 0.3, 0.1],
        position_size=0.1,
        expected_vol=0.01,
        raw_size=2.0,
        is_toxic=False,
        garch_regime_probs=[0.8, 0.2],
        feed_status="OK",
        signed_position_size=0.1,
        last_valid_vol=0.01,
        switch_stability_ema=0.8,
        include_signal_valid=False,
        signal_valid=True,
    )
    assert "signal_valid" not in out

    out2 = _build_output(
        regime_idx=0,
        regime_label="TREND",
        trend_strength=0.5,
        risk_level=0.1,
        confidence=0.8,
        conviction=0.7,
        edge_score=0.6,
        probabilities={"bull": 0.6, "bear": 0.3, "crisis": 0.1},
        macro_probs=[0.6, 0.3, 0.1],
        position_size=0.1,
        expected_vol=0.01,
        raw_size=2.0,
        is_toxic=False,
        garch_regime_probs=[0.8, 0.2],
        feed_status="OK",
        signed_position_size=0.1,
        last_valid_vol=0.01,
        switch_stability_ema=0.8,
        include_signal_valid=True,
        signal_valid=False,
    )
    assert out2["signal_valid"] is False


def test_issue_27_safe_int_rounds_correctly():
    assert _safe_int(2.9) == 3
    assert _safe_int(2.1) == 2
    assert _safe_int(2.9999999999) == 3
    assert _safe_int(-2.9) == -3
    assert _safe_int(3.5) == 4
    assert _safe_int("3.7") == 4
    assert _safe_int(None, default=5) == 5


def test_issue_28_obs_counter_and_obs_sample_rate_absent():
    with open("advanced_regime_engine.py", encoding="utf-8") as f:
        source = f.read()
    assert "_obs_counter" not in source
    assert "self._OBS_SAMPLE_RATE" not in source
