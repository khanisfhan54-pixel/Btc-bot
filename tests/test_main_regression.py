import importlib
import sys
import threading
import types
from pathlib import Path

import pytest


def _stub_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


@pytest.fixture(scope="module")
def main_mod():
    class DummyExecutionLogic:
        def __init__(self, learning_engine=None):
            pass
    class DummyExecutionEngine:
        def place_order_with_sl_tp(self, *a, **k):
            return {}

    _stub_module("execution", ExecutionLogic=DummyExecutionLogic, ExecutionEngine=DummyExecutionEngine, calculate_position_size=lambda **k: 1.0, calculate_liquidity_sl_tp=lambda *a, **k: (1, 2))
    _stub_module("queue_fill_model", QueueFillModel=type("QueueFillModel", (), {}))
    _stub_module("toxicity_filter", ToxicityFilter=type("ToxicityFilter", (), {}))
    _stub_module("order_router", OrderRouter=type("OrderRouter", (), {}))
    _stub_module("impact_decay", ImpactDecay=type("ImpactDecay", (), {}))
    _stub_module("position_manager", PositionManager=type("PositionManager", (), {"has_position": lambda self: False}))
    _stub_module("trade_lifecycle_manager", TradeLifecycleManager=type("TradeLifecycleManager", (), {}))
    _stub_module("capital_allocator", CapitalAllocator=type("CapitalAllocator", (), {}))
    _stub_module("backtest_engine", BacktestEngine=type("BacktestEngine", (), {}), BacktestConfig=type("BacktestConfig", (), {}))
    _stub_module("alpha_orchestrator", AlphaOrchestrator=lambda cfg: object(), OrchestratorConfig=lambda **k: object(), AlphaSignal=object, RegimeContext=object, FeatureQuality=object, ExecutionState=object)
    _stub_module("venue_basis", VenueBasisNormalizer=lambda halt_threshold_pct: type("VB", (), {"validate": lambda self, x: {"ok": True}})())
    _stub_module("thread_safe_wrappers", ThreadSafeFeatureEngine=lambda e: type("W", (), {"_wrapped": e, "update": e.update})(), ThreadSafeAlphaPredictor=lambda x: x)
    _stub_module("trading_utils", safe_float=lambda v, default=0.0: float(v) if v not in (None, "") else default, clamp=lambda x,a,b:max(a,min(b,x)), validate_alpha=lambda x:x)

    return importlib.import_module("main")


class TestIssueA_FeatureEngineFallback:
    def test_fallback_accepts_regime_context_kwarg(self, main_mod):
        fe = main_mod.FeatureEngine()
        out = fe.update({}, [], regime_context={})
        assert isinstance(out, dict)

    def test_type_error_count_guard_still_present(self, main_mod):
        assert main_mod._feature_type_error_count == 0
        assert isinstance(main_mod._ANALYSIS_STATE_LOCK, type(threading.Lock()))


class TestIssueC_ConstantValidation:
    def test_defaults_pass_validation(self, main_mod):
        main_mod._validate_startup_constants()

    def test_risk_percent_zero_raises(self, main_mod, monkeypatch):
        monkeypatch.setattr(main_mod, "RISK_PERCENT_PER_TRADE", 0.0)
        with pytest.raises(ValueError):
            main_mod._validate_startup_constants()


class TestIssueD_ExecutorShutdown:
    def test_no_explicit_shutdown_in_main_block(self):
        text = Path("main.py").read_text()
        assert "_SHARED_FETCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)" not in text.split('if __name__ == "__main__":',1)[1]


class TestFeatureEngineWrapperContract:
    def test_main_uses_wrapped_feature_engine_reference(self):
        text = Path("main.py").read_text()
        assert "feature_engine._engine" not in text
        assert "feature_engine._wrapped" in text
