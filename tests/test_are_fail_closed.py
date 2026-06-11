import numpy as np

from backtest_engine import BacktestConfig, BacktestEngine
from tests.test_are_gating_parity import MinimalFeatureEngine, MinimalMetaFilter, MinimalSignalEngine, _bars


class StaticARE:
    def __init__(self, payload):
        self.payload = dict(payload)

    def update(self, payload):
        return dict(self.payload)


def _run_with_are(are_payload):
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    bt.are = StaticARE(are_payload)
    bt.feature_engine = MinimalFeatureEngine()
    bt.signal_engine = MinimalSignalEngine()
    bt.meta_filter = MinimalMetaFilter()
    bt.fill_model = None
    bt.tox_filter = None
    bt.position_manager = None
    bt.trade_lifecycle = None
    bt.capital_allocator = None
    bt.lsa = None
    bt._calibration_feature_mean = np.zeros(3, dtype=float)
    bt._calibration_feature_std = np.ones(3, dtype=float)
    return bt._run_single_pass(_bars(60), label="are-fail-closed-test")


def _valid_base():
    return {
        "signal_valid": True,
        "execution_mode": "normal",
        "engine_status": "OK",
        "regime_label": "TREND",
        "confidence": 0.9,
        "risk_metrics": {"expected_volatility": 0.01, "feed_status": {"primary": "OK", "flags": []}},
    }


def test_no_trades_when_signal_valid_false():
    payload = _valid_base()
    payload["signal_valid"] = False
    result = _run_with_are(payload)
    assert result["total_trades"] == 0
    assert result["bars_skipped_signal_invalid"] > 0


def test_no_trades_when_execution_mode_halt():
    payload = _valid_base()
    payload["execution_mode"] = "halt"
    result = _run_with_are(payload)
    assert result["total_trades"] == 0
    assert result["bars_skipped_execution_halted"] > 0


def test_no_trades_when_engine_status_degraded():
    payload = _valid_base()
    payload["engine_status"] = "DEGRADED"
    result = _run_with_are(payload)
    assert result["total_trades"] == 0
    assert result["bars_skipped_execution_halted"] > 0


def test_no_trades_when_feed_status_invalid():
    payload = _valid_base()
    payload["risk_metrics"] = {"expected_volatility": 0.01, "feed_status": {"primary": "STALE", "flags": []}}
    result = _run_with_are(payload)
    assert result["total_trades"] == 0
    assert result["bars_skipped_execution_halted"] > 0
