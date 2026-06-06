import inspect
from pathlib import Path

from liquidity_magnet_predictor import LiquidityMagnetPredictor, predict_liquidity_magnet
import liquidity_magnet_predictor as lmp
import engine
import alpha_orchestrator as ao


def _candidate(price=50100.0, side="above"):
    return {"price": price, "side": side, "type": "equal_highs", "age_bars": 1.0, "base_strength": 1.0}


def _market(regime="normal"):
    return {"regime": regime, "volatility": 1.0, "trend_direction": "up", "atr": 100.0}


def test_helper_missing_persistent_instance_fails_closed_without_fresh_instantiation(monkeypatch):
    """Issue/root cause/proof: helper fallback silently created stateless memory; now missing persistent instance returns neutral without constructing a fresh predictor."""
    def fail_init(self, *args, **kwargs):
        raise AssertionError("fresh LiquidityMagnetPredictor fallback is forbidden")

    monkeypatch.setattr(lmp.LiquidityMagnetPredictor, "__init__", fail_init)
    out = predict_liquidity_magnet([_candidate()], 50000.0, 1.0, _market(), predictor_instance=None)
    assert out["zone_side"] == "none"
    assert out["confidence"] == 0.0
    assert "missing_persistent_predictor" in out["warnings"]


def test_engine_singleton_is_canonical_and_live_main_preserves_magnet_alpha_source():
    """Regression protection: live wiring continues to route through engine singleton and AlphaSignal(source_id='liquidity_magnet_alpha')."""
    assert engine.get_shared_magnet_predictor() is engine.get_shared_magnet_predictor()
    source = Path("main.py").read_text()
    assert "from engine import get_shared_magnet_predictor as _get_magnet" in source
    assert 'source_id="liquidity_magnet_alpha"' in source


def test_toxic_and_illiquid_regimes_hard_disable_magnet_participation():
    """Issue/root cause/proof: toxic/illiquid were only down-weighted; prediction now hard-disables to neutral fail-closed output."""
    predictor = LiquidityMagnetPredictor()
    for regime in ("TOXIC", "ILLIQUID"):
        out = predictor.predict([_candidate()], 50000.0, 1.0, _market(regime=regime))
        assert out["zone_side"] == "none"
        assert out["score"] == 0.0
        assert out["diagnostics"]["hard_disabled"] is True
        assert "hard_disabled_toxic_or_illiquid_regime" in out["warnings"]


def test_zone_memory_ttl_expires_before_scoring_and_before_updates():
    """Issue/root cause/proof: stale memory previously lived until capacity eviction; TTL pruning now removes it deterministically before score/read-write paths."""
    predictor = LiquidityMagnetPredictor(memory_ttl_bars=10.0, zone_price_bucket=1.0)
    predictor.update_memory(50100.0, "above", "equal_highs", "touch", 0.0)
    assert len(predictor.zone_memory) == 1

    out = predictor.predict([_candidate()], 50000.0, 11.0, _market())
    assert len(predictor.zone_memory) == 0
    assert out["diagnostics"]["expired_memory_entries"] == 1

    predictor.update_memory(50200.0, "above", "equal_highs", "touch", 0.0)
    predictor.update_memory(50300.0, "above", "equal_highs", "touch", 11.0)
    assert len(predictor.zone_memory) == 1
    assert next(iter(predictor.zone_memory)).endswith("50300.00")


def test_backtest_includes_magnet_alpha_and_marks_unproven_parity_non_production():
    """Issue/root cause/proof: backtest previously excluded magnet; source-level regression checks prevent silent exclusion or false parity labels."""
    source = Path("backtest_engine.py").read_text()
    assert '"liquidity_magnet_alpha": 0.5' in source
    assert 'source_id="liquidity_magnet_alpha"' in source
    assert '"magnet_inputs_unavailable_or_non_parity"' in source
    assert 'NON-PRODUCTION-VALID' in source
    assert 'return self.magnet_predictor.predict' in source


def test_backtest_uses_isolated_canonical_magnet_factory_without_hidden_fresh_fallback():
    """Issue/root cause/proof: replay uses an explicit backtest factory, not the live singleton or helper fallback."""
    source = Path("backtest_engine.py").read_text()
    assert "from engine import create_backtest_magnet_predictor" in source
    assert "self.magnet_predictor = create_backtest_magnet_predictor()" in source
    assert "from engine import get_shared_magnet_predictor" not in source
    assert "self.magnet_predictor = get_shared_magnet_predictor()" not in source
    assert "LiquidityMagnetPredictor()" not in source
    assert "from liquidity_magnet_predictor import LiquidityMagnetPredictor" not in source


def test_backtest_magnet_factory_isolates_two_runs_and_does_not_touch_live_singleton():
    """Issue/root cause/proof: consecutive backtest runs in one process must not inherit shared/live or prior replay zone memory."""
    shared = engine.get_shared_magnet_predictor()
    shared.zone_memory.clear()
    try:
        shared.update_memory(50100.0, "above", "equal_highs", "touch", 1.0)

        run1 = engine.create_backtest_magnet_predictor()
        run2 = engine.create_backtest_magnet_predictor()

        assert run1 is not run2
        assert run1 is not shared
        assert run2 is not shared
        assert len(run1.zone_memory) == 0
        assert len(run2.zone_memory) == 0

        run1.update_memory(50200.0, "above", "equal_highs", "touch", 2.0)
        assert len(run1.zone_memory) == 1
        assert len(run2.zone_memory) == 0
        assert len(shared.zone_memory) == 1
    finally:
        shared.zone_memory.clear()

def test_overlap_diagnostics_for_magnet_sweep_and_stop_hunt_are_available():
    """Diagnostics only: duplicate-direction magnet/sweep/stop-hunt overlap is reported without changing threshold or decision policy."""
    cfg = ao.OrchestratorConfig(
        signal_weights={"liquidity_magnet_alpha": 0.5, "liquidity_sweep_alpha": 0.5, "stop_hunt_engine": 0.5},
        feedback_enabled=False,
        min_aggregate_weight=0.0,
        correlation_min_conviction=0.0,
        correlation_min_group_size=2,
    )
    orch = ao.AlphaOrchestrator(cfg)
    signals = [
        ao.AlphaSignal("liquidity_magnet_alpha", 1, 0.8, 10.0, 1_700_000_000.0, timeframe="1m", correlation_group_id="directional"),
        ao.AlphaSignal("liquidity_sweep_alpha", 1, 0.8, 10.0, 1_700_000_000.0, timeframe="1m", correlation_group_id="directional"),
        ao.AlphaSignal("stop_hunt_engine", 1, 0.8, 10.0, 1_700_000_000.0, timeframe="1m", correlation_group_id="directional"),
    ]
    _, _, meta = orch._fuse_signals(signals, "normal", 0.0, {}, regime_assessment=None)
    diag = meta["correlation_summary"]["overlap_diagnostics"]
    assert diag["overlap_risk"] is True
    assert diag["policy_changed"] is False
    assert {"liquidity_magnet_alpha", "liquidity_sweep_alpha", "stop_hunt_engine"}.issubset(set(diag["sources_observed"]))


def test_orchestrator_threshold_unchanged_for_backtest_config():
    """Regression protection: magnet wiring did not alter orchestrator thresholds."""
    source = Path("backtest_engine.py").read_text()
    assert "action_threshold=float(self.cfg.orchestrator_action_threshold)" in source
    assert "orchestrator_action_threshold: float = 0.60" in source
