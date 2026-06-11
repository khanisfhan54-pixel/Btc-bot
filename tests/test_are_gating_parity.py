import numpy as np

from backtest_engine import BacktestEngine, BacktestConfig


class InvalidSignalARE:
    def update(self, payload):
        return {
            "signal_valid": False,
            "execution_mode": "normal",
            "engine_status": "OK",
            "regime_label": "TREND",
            "confidence": 0.9,
            "risk_metrics": {"expected_volatility": 0.01},
        }


class MinimalFeatureEngine:
    def update(self, snapshot, trades):
        return {"features": {"ofi_zscore": 0.0, "vol_z": 1.0, "liquidity_score": 1.0}}


class MinimalSignalEngine:
    def generate(self, features):
        return {"signal": "LONG", "confidence": 1.0}


class MinimalMetaFilter:
    def evaluate(self, **kwargs):
        return {"allow_trade": True}


def _bars(n=60):
    out = []
    price = 50_000.0
    for i in range(n):
        ts = 1_700_000_000_000 + i * 60_000
        close = price + i
        out.append([ts, close - 5.0, close + 10.0, close - 10.0, close, 100.0 + i])
    return out


def test_are_signal_valid_false_blocks_backtest_entries():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    bt.are = InvalidSignalARE()
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

    result = bt._run_single_pass(_bars(60), label="are-gating-test")

    assert result["total_trades"] == 0
    assert result["bars_skipped_signal_invalid"] > 0
