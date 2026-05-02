from backtest_engine import BacktestEngine, BacktestConfig


def _candles(n=120):
    return [[i * 60_000, 50000.0, 50020.0, 49980.0, 50000.0 + (i % 3), 100000.0] for i in range(n)]


def _micro_rows(n=120):
    out = []
    for i in range(n):
        mid = 50000.0 + (i % 5)
        out.append({
            "timestamp": i * 60_000,
            "close": mid,
            "volume": 10.0 + i,
            "snapshot": {
                "timestamp": i * 60_000,
                "bids": [[mid - 0.5, 2.0]],
                "asks": [[mid + 0.5, 2.0]],
            },
            "trades": [{"price": mid, "amount": 0.1, "side": "buy"}],
        })
    return out


def test_synthetic_mode_explicitly_non_production_valid():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    out = bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    assert out["production_valid"] is False
    assert out["signal_quality_valid"] is False
    assert out["regime_state"] == "explicit_fallback"


def test_real_microstructure_does_not_use_synthetic_helpers(monkeypatch):
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)

    def _fail(*_a, **_k):
        raise AssertionError("synthetic helper invoked")

    monkeypatch.setattr("backtest_engine._simulate_snapshot_from_candle", _fail)
    monkeypatch.setattr("backtest_engine._simulate_trades_from_candle", _fail)

    out = bt.run_backtest(_micro_rows(), signal_quality_required=False)
    assert out["regime_state"] == "feature_derived"
    assert out["production_valid"] is False


def test_real_microstructure_can_be_production_valid():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)

    class _Regime:
        def update(self, _payload):
            return {"signal_valid": True, "regime_label": "range", "confidence": 0.8, "risk_level": 0.2}

    class _OrchAction:
        class _A:
            name = "BUY"
        action = _A()
        net_conviction = 0.7

    class _Orch:
        def orchestrate(self, **_kwargs):
            return _OrchAction()

    class _Alpha:
        def predict(self, _payload):
            return {"source_id": "alpha_model", "direction": "LONG", "confidence": 0.8, "expected_edge_bps": 4.0}

    class _Signal:
        def generate(self, _features):
            return {"signal": "LONG", "confidence": 0.9}

    bt.regime_engine = _Regime()
    bt.alpha_orchestrator = _Orch()
    bt.alpha_predictor = _Alpha()
    bt.signal_engine = _Signal()
    out = bt.run_backtest(_micro_rows(), signal_quality_required=True)
    assert out["signal_quality_valid"] is True
    assert out["production_valid"] is True


def test_determinism_real_microstructure_path():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    rows = _micro_rows()
    out1 = bt.run_backtest(rows, signal_quality_required=False)
    out2 = bt.run_backtest(rows, signal_quality_required=False)
    for k in ("signal_coverage", "long_signals", "short_signals", "hold_signals", "alpha_non_empty_count"):
        assert out1[k] == out2[k]


def test_validation_path_enforces_orchestrator_order_and_usage():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)
    order = []

    class _Regime:
        def update(self, _payload):
            order.append("regime")
            return {"signal_valid": True, "regime_label": "range", "confidence": 0.8, "risk_level": 0.2}

    class _OrchAction:
        class _A:
            name = "BUY"
        action = _A()
        net_conviction = 0.7

    class _Orch:
        def orchestrate(self, **_kwargs):
            order.append("orchestrator")
            return _OrchAction()

    class _Signal:
        def generate(self, _features):
            order.append("signal")
            return {"signal": "LONG", "confidence": 0.9}

    class _Alpha:
        def predict(self, _payload):
            return {"source_id": "alpha_model", "direction": "LONG", "confidence": 0.8, "expected_edge_bps": 4.0}

    bt.regime_engine = _Regime()
    bt.alpha_orchestrator = _Orch()
    bt.alpha_predictor = _Alpha()
    bt.signal_engine = _Signal()

    out = bt.run_backtest(_micro_rows(), signal_quality_required=True)
    assert out["production_valid"] is True
    first_triplet = order[:3]
    assert first_triplet == ["regime", "orchestrator", "signal"]


def test_validation_path_fails_closed_when_orchestrator_input_invalid():
    bt = BacktestEngine(config=BacktestConfig(), signal_only=True)

    class _Regime:
        def update(self, _payload):
            return {"signal_valid": True, "regime_label": "range", "confidence": 0.8, "risk_level": 0.2}

    class _Alpha:
        def predict(self, _payload):
            return {"direction": "LONG", "confidence": 0.8}

    bt.regime_engine = _Regime()
    bt.alpha_predictor = _Alpha()

    out = bt.run_backtest(_micro_rows(), signal_quality_required=True)
    assert out["production_valid"] is False
    assert out["signal_quality_valid"] is False
    assert out["signal_quality_reason"] == "production_parity_requires_alpha_orchestration"
