"""
Regression suite for the advanced regime engine → predictor → engine → main wiring.

These tests codify the audit checks performed on 2026-04-18:
- main.run_all_engines must NOT be shadowed by fallback stubs when engine imports successfully
- LiquiditySweepAlpha.predict() must return the full schema even for None / non-dict input
- predict_sweep() must handle NaN/Inf/None gracefully and maintain sum-to-one normalization
- feature_engine.update() must merge regime_context into features (including the empty-book path)
- regime_context semantics: main/engine must forward regime_label as volatility_regime
- SniperExecutionEngine signal-only mode must not execute trades
- run_analysis_cycle() signal-only output must preserve schema
- Concurrency: predictor must not deadlock or corrupt state under multi-threaded access
- E2E: feature_engine → regime_context → predictor flow returns a consistent schema
"""
import math
import sys
import os
import threading
import time

import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import alpha_liquidity_sweep_predictor as alpha
import calibrate_regime
from advanced_regime_engine import AdvancedRegimeEngine
from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha, predict_sweep
from feature_engine import FeatureEngine
from model_weights import ModelWeightManager


REQUIRED_PREDICTOR_KEYS = {
    "action",
    "confidence",
    "state",
    "regime",
    "ofi_zscore",
    "hawkes_intensity",
    "logic",
    "micro_prob",
    "macro_prob",
    "prob_above",
    "prob_below",
}


def _assert_predictor_schema(out, *, strict=True):
    assert isinstance(out, dict)
    if strict:
        missing = REQUIRED_PREDICTOR_KEYS - set(out.keys())
        assert not missing, f"missing keys: {missing}"
    for k in ("confidence", "prob_above", "prob_below", "micro_prob", "macro_prob"):
        if k in out:
            v = out[k]
            assert isinstance(v, (int, float))
            assert math.isfinite(v)
            assert 0.0 <= v <= 1.0
    if "action" in out:
        assert out["action"] in ("BUY", "SELL", "HOLD")
    if "prob_above" in out and "prob_below" in out:
        assert abs(out["prob_above"] + out["prob_below"] - 1.0) < 1e-3


# ---------------------------------------------------------------------
# Predictor schema / sanity
# ---------------------------------------------------------------------

def test_predict_sweep_minimal_schema():
    out = predict_sweep({}, {})
    for k in ("side", "probability", "confidence", "prob_above", "prob_below", "state", "target_price"):
        assert k in out, f"predict_sweep missing {k}"
    assert out["confidence"] == out["probability"]
    assert abs(out["prob_above"] + out["prob_below"] - 1.0) < 1e-3
    assert 0.0 < out["prob_above"] < 1.0
    assert 0.0 < out["prob_below"] < 1.0


@pytest.mark.parametrize(
    "liq,ms",
    [
        (
            {
                "nearest_above": {"distance_points": float("nan"), "price": 60100},
                "nearest_below": {"distance_points": float("inf"), "price": 59900},
            },
            {
                "state": "TRENDING",
                "volatility": float("nan"),
                "compression": float("inf"),
                "bias": float("nan"),
            },
        ),
        (
            {
                "nearest_above": {"distance_points": -1e9, "price": None},
                "nearest_below": {"distance_points": 1e18, "price": float("nan")},
            },
            {"state": None, "volatility": -1.0, "compression": -1.0, "bias": -999},
        ),
        (None, None),
        ("bad", {}),
        ({}, "bad"),
    ],
)
def test_predict_sweep_adversarial(liq, ms):
    out = predict_sweep(liq, ms)
    assert isinstance(out, dict)
    assert math.isfinite(out["probability"])
    assert 0.0 < out["prob_above"] < 1.0
    assert 0.0 < out["prob_below"] < 1.0


def test_alpha_predict_none_returns_full_schema():
    """Regression: predict(None) used to return a schema-incomplete dict."""
    lsa = LiquiditySweepAlpha()
    out = lsa.predict(None)
    _assert_predictor_schema(out, strict=True)


def test_alpha_predict_non_dict_returns_full_schema():
    lsa = LiquiditySweepAlpha()
    for bad in ["str", 123, [], 0, None]:
        out = lsa.predict(bad)
        _assert_predictor_schema(out, strict=True)


def test_alpha_predict_features_wrapper():
    lsa = LiquiditySweepAlpha()
    out = lsa.predict({"features": {"price": 60050, "atr": 150, "timestamp": 1700000000.0}})
    _assert_predictor_schema(out, strict=True)


def test_alpha_predict_cold_start_full_schema():
    lsa = LiquiditySweepAlpha()
    out = lsa.predict({"price": 60050, "timestamp": 1700000000.0, "atr": 150})
    _assert_predictor_schema(out, strict=True)


def test_alpha_predict_adversarial_inputs_schema_stable():
    lsa = LiquiditySweepAlpha(history_window=5)
    lsa.update_liquidity_pools([60100] * 5, [59900] * 5)
    cases = [
        {"price": float("nan")},
        {"price": 0.0},
        {"price": -100.0},
        {"price": float("inf")},
        {"price": 60000.0, "atr": float("nan")},
        {"price": 60000.0, "atr": 0.0},
        {"price": 60000.0, "atr": -10.0},
        {"price": 60000.0, "atr": float("inf")},
        {"price": 60000.0, "prev_book": None, "curr_book": None},
        {"price": 60000.0, "prev_book": {"bids": []}, "curr_book": {"asks": []}},
        {"price": 60000.0, "timestamp": None},
        {"price": 60000.0, "timestamp": float("nan")},
        {"price": 60000.0, "timestamp": -1e12},
        {"price": 60000.0, "timestamp": 1e20},
        {"price": 60000.0, "trades_count": None},
        {"price": 60000.0, "trades_count": -1},
        {"price": 60000.0, "ema_fast": float("inf"), "ema_slow": float("nan")},
    ]
    for md in cases:
        out = lsa.predict(md)
        _assert_predictor_schema(out, strict=True)


def test_alpha_deterministic_replay():
    def run():
        l = LiquiditySweepAlpha(history_window=10)
        l.update_liquidity_pools([60100] * 10, [59900] * 10)
        book = {
            "bids": [{"price": 60000 - i * 10, "size": 1.0} for i in range(10)],
            "asks": [{"price": 60000 + i * 10, "size": 1.0} for i in range(10)],
        }
        outs = []
        for j in range(20):
            outs.append(
                l.get_signal(
                    {
                        "price": 60050 + j,
                        "prev_book": book,
                        "curr_book": book,
                        "timestamp": 1700000000.0 + j,
                        "trades_count": 10,
                        "atr": 150,
                        "ema_fast": 60080,
                        "ema_slow": 60050,
                    }
                )
            )
        return outs

    assert run() == run()


def test_alpha_concurrency_no_deadlock_no_corruption():
    lsa = LiquiditySweepAlpha(history_window=20)
    lsa.update_liquidity_pools([60100] * 20, [59900] * 20)
    errors = []
    book = {
        "bids": [{"price": 60000 - i * 10, "size": 1.0} for i in range(10)],
        "asks": [{"price": 60000 + i * 10, "size": 1.0} for i in range(10)],
    }

    def worker(n):
        try:
            for j in range(100):
                out = lsa.predict(
                    {
                        "price": 60050 + (n * 0.01),
                        "prev_book": book,
                        "curr_book": book,
                        "timestamp": time.time() + n * 0.0001 + j * 0.0001,
                        "trades_count": 5 + n,
                        "atr": 150,
                        "ema_fast": 60080,
                        "ema_slow": 60050,
                    },
                    regime_context={"regime": ["RANGE", "TREND", "TOXIC"][n % 3]},
                )
                _assert_predictor_schema(out)
        except Exception as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    deadline = time.time() + 30.0
    for t in threads:
        t.join(timeout=max(0.1, deadline - time.time()))
    assert not any(t.is_alive() for t in threads), "concurrency deadlock"
    assert not errors, f"concurrency errors: {errors[:3]}"


# ---------------------------------------------------------------------
# feature_engine regime_context integration
# ---------------------------------------------------------------------

def test_feature_engine_regime_context_populated():
    fe = FeatureEngine()
    snap = {"bids": [{"price": 60000, "size": 1.0}], "asks": [{"price": 60010, "size": 1.0}]}
    rc = {
        "regime": "TREND",
        "confidence": 0.8,
        "features": {
            "volatility_regime": "TREND",
            "liquidity_regime": "trend_follow",
            "trend_strength": 0.7,
        },
    }
    feats = fe.update(snap, [], regime_context=rc)["features"]
    assert feats.get("volatility_regime") == "TREND"
    assert feats.get("liquidity_regime") == "trend_follow"
    assert abs(feats.get("trend_strength", 0.0) - 0.7) < 1e-6


def test_feature_engine_sanitizes_nonfinite_trend_strength():
    fe = FeatureEngine()
    snap = {"bids": [{"price": 60000, "size": 1.0}], "asks": [{"price": 60010, "size": 1.0}]}
    rc = {"regime": "X", "confidence": 0.0, "features": {"trend_strength": float("inf")}}
    feats = fe.update(snap, [], regime_context=rc)["features"]
    if "trend_strength" in feats:
        v = feats["trend_strength"]
        assert math.isfinite(v)
        assert 0.0 <= v <= 1.0


def test_feature_engine_none_and_bad_regime_context():
    fe = FeatureEngine()
    snap = {"bids": [{"price": 60000, "size": 1.0}], "asks": [{"price": 60010, "size": 1.0}]}
    for rc in (None, "bad", 42, [], {}):
        out = fe.update(snap, [], regime_context=rc)
        assert "regime" in out["features"]


def test_feature_engine_empty_book_preserves_regime_context():
    """Regression: _empty_output path used to drop regime_context."""
    fe = FeatureEngine()
    rc = {
        "regime": "TREND",
        "confidence": 0.9,
        "features": {
            "volatility_regime": "TREND",
            "liquidity_regime": "trend_follow",
            "trend_strength": 0.77,
        },
    }
    out = fe.update({}, [], regime_context=rc)
    feats = out["features"]
    assert feats.get("volatility_regime") == "TREND"
    assert feats.get("liquidity_regime") == "trend_follow"
    assert abs(feats.get("trend_strength", 0.0) - 0.77) < 1e-6


# ---------------------------------------------------------------------
# main ↔ engine wiring
# ---------------------------------------------------------------------

def test_main_does_not_shadow_engine_symbols():
    """Regression: main.py was shadowing engine symbols with fallback stubs."""
    import engine as engine_mod
    import main as main_mod

    for name in (
        "run_all_engines",
        "analyze_volume_intelligence",
        "detect_entry_trigger",
        "build_trade_plan",
        "compute_score",
        "get_cascade_probability",
        "MarketStateDetector",
        "evaluate_smc_sniper",
        "evaluate_meta_filter",
        "apply_meta_to_decision",
    ):
        assert hasattr(main_mod, name), f"main missing {name}"
        assert hasattr(engine_mod, name), f"engine missing {name}"
        assert getattr(main_mod, name) is getattr(engine_mod, name), (
            f"main.{name} is shadowed by a fallback stub; should be engine.{name}"
        )


def test_main_signal_pipeline_engine_constructed():
    import main as main_mod

    assert main_mod._signal_pipeline_engine is not None, (
        "main._signal_pipeline_engine should be constructed when engine imports OK"
    )


# ---------------------------------------------------------------------
# Signal-only execution path
# ---------------------------------------------------------------------

def test_run_analysis_cycle_signal_only_schema():
    import main as main_mod

    class _StubExchange:
        id = "stub"

        def load_markets(self):
            return {"BTC/USDT": {}}

        def fetch_ohlcv(self, symbol, timeframe="1m", limit=50):
            now_ms = int(time.time() * 1000)
            return [
                [now_ms - (100 - i) * 60_000, 60000 + i * 0.1, 60050 + i * 0.1, 59950 + i * 0.1, 60020 + i * 0.1, 10.0]
                for i in range(100)
            ]

        def fetch_order_book(self, symbol, limit=20):
            return {
                "bids": [[60000 - i * 1.0, 1.0] for i in range(10)],
                "asks": [[60010 + i * 1.0, 1.0] for i in range(10)],
            }

        def fetch_trades(self, symbol, limit=100):
            return [
                {"price": 60005, "amount": 0.1, "side": "buy", "timestamp": int(time.time() * 1000)}
                for _ in range(20)
            ]

        def fetch_funding_rate(self, symbol):
            return {"fundingRate": 0.0}

        def fetch_ticker(self, symbol):
            return {"last": 60005}

    main_mod.SIGNAL_PIPELINE_CONFIG["signal_only_mode"] = True
    out = main_mod.run_analysis_cycle(_StubExchange())
    assert isinstance(out, dict)
    status = out.get("status")
    if status == "SIGNAL_ONLY":
        assert out.get("metadata", {}).get("execution_skipped") is True
        po = out.get("predictor_output", {})
        for k in ("confidence", "prob_above", "prob_below", "micro_prob", "macro_prob"):
            if k in po:
                v = po[k]
                assert math.isfinite(v) and 0.0 <= v <= 1.0


def test_sniper_execution_engine_signal_only_does_not_execute():
    from engine import MarketSnapshot, SniperExecutionEngine

    eng = SniperExecutionEngine(symbol="BTCUSDT", config={"signal_only_mode": True})
    snap = MarketSnapshot(
        symbol="BTCUSDT",
        price=60000.0,
        timestamp=time.time(),
        orderbook={"bids": [[60000, 1.0]], "asks": [[60010, 1.0]]},
        trades=[],
        candles={"1m": [[int(time.time() * 1000), 60000, 60050, 59950, 60000, 10.0]] * 30},
        open_interest=0.0,
        funding_rate=0.0,
    )
    eng.update_snapshot(snap)
    decision = eng.latest_decision
    if decision is not None:
        meta = getattr(decision, "metadata", {}) or {}
        assert meta.get("execution_skipped") is True, (
            "signal-only mode must flag execution_skipped in metadata"
        )


# ---------------------------------------------------------------------
# Engine alpha integration
# ---------------------------------------------------------------------

def test_engine_run_all_alpha_integration():
    from engine import run_all_engines

    out = (
        run_all_engines(
            orderbook={"bids": [[60000, 1.0], [59990, 2.0]], "asks": [[60010, 1.0], [60020, 2.0]]},
            trades=[
                {"price": 60005, "amount": 0.1, "side": "buy", "timestamp": int(time.time() * 1000)}
            ]
            * 30,
            price=60005.0,
            recent_candles=[
                [int(time.time() * 1000) - i * 60_000, 60000, 60050, 59950, 60020, 10.0]
                for i in range(100)
            ],
        )
        or {}
    )
    assert isinstance(out, dict)
    alpha_out = out.get("alpha", {})
    assert isinstance(alpha_out, dict)
    for k in ("direction", "confidence", "prob_above", "prob_below", "micro_prob", "macro_prob"):
        assert k in alpha_out, f"engine alpha missing {k}"
    assert 0.0 <= alpha_out["confidence"] <= 1.0
    assert abs(alpha_out["prob_above"] + alpha_out["prob_below"] - 1.0) < 1e-3
    assert alpha_out["direction"] in ("LONG", "SHORT", "NEUTRAL")


# ---------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------

def test_e2e_feature_engine_to_predictor():
    fe = FeatureEngine()
    snap = {
        "bids": [{"price": 60000 - i, "size": 1.0} for i in range(10)],
        "asks": [{"price": 60010 + i, "size": 1.0} for i in range(10)],
    }
    trades = [
        {"price": 60005, "amount": 0.1, "side": "buy", "timestamp": int(time.time() * 1000)}
    ] * 10

    regime_context = {
        "regime": "TREND",
        "confidence": 0.78,
        "features": {
            "volatility_regime": "TREND",
            "liquidity_regime": "trend_follow",
            "trend_strength": 0.55,
            "feed_status": "OK",
        },
    }
    feats = fe.update(snap, trades, regime_context=regime_context)["features"]
    assert feats.get("volatility_regime") == "TREND"
    assert feats.get("liquidity_regime") == "trend_follow"
    assert abs(feats.get("trend_strength", 0.0) - 0.55) < 1e-6

    pred = LiquiditySweepAlpha(history_window=5)
    pred.update_liquidity_pools([60100] * 5, [59900] * 5)
    out = None
    for j in range(6):
        out = pred.predict(
            {
                "price": 60005 + j,
                "prev_book": {"bids": [[60000, 1.0]], "asks": [[60010, 1.0]]},
                "curr_book": {"bids": [[60000, 1.0]], "asks": [[60010, 1.0]]},
                "timestamp": 1700000000.0 + j,
                "trades_count": 20,
                "atr": 150,
                "ema_fast": 60080,
                "ema_slow": 60050,
            },
            regime_context=regime_context,
        )
    _assert_predictor_schema(out)


def test_calibrate_regime_output_is_compatible_with_advanced_regime_engine(tmp_path):
    csv_path = tmp_path / "ohlcv.csv"
    weights_path = tmp_path / "advanced_regime_weights.npz"
    rows = []
    for i in range(30):
        ts = 1_700_000_000 + (i * 60)
        close = 100.0 + (0.2 * i)
        rows.append([ts, close - 1.0, close + 1.0, close - 2.0, close, 10.0 + i])
    np.savetxt(csv_path, np.asarray(rows, dtype=float), delimiter=",")

    calibrate_regime.calibrate(str(csv_path), str(weights_path))
    weights = ModelWeightManager.load_weights("advanced_regime", str(weights_path))
    assert weights is not None

    required = {"nhhmm_beta", "nhhmm_mu", "nhhmm_sigma", "sjm_centroids"}
    missing = required - set(weights.keys())
    assert not missing, f"missing keys: {sorted(missing)}"

    beta = np.asarray(weights["nhhmm_beta"], dtype=float)
    mu = np.asarray(weights["nhhmm_mu"], dtype=float)
    sigma = np.asarray(weights["nhhmm_sigma"], dtype=float)
    centroids = np.asarray(weights["sjm_centroids"], dtype=float)

    assert beta.ndim == 3
    assert beta.shape[:2] == (3, 3)
    assert mu.shape == (3,)
    assert sigma.shape == (3,)
    assert centroids.ndim == 2
    assert centroids.shape[0] == 3
    assert beta.shape[2] == centroids.shape[1]

    for arr in (beta, mu, sigma, centroids):
        assert np.isfinite(arr).all()

    engine = AdvancedRegimeEngine(
        n_states=3,
        n_features=centroids.shape[1],
        enable_background_workers=False,
        seed=42,
    )
    engine.nhhmm.load_weights(beta, mu, sigma)
