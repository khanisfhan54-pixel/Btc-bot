from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.data.derivatives import FundingPoint, OpenInterestPoint
from stop_hunt_engine.data.l2_snapshot import BookLevel, L2Snapshot
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput, build_feature_vector
from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability


def _candles(n: int = 5) -> list[Candle]:
    return [
        Candle(timestamp=1_700_000_000.0 + i * 300.0, open=50_000, high=50_020, low=49_980, close=50_000 + i, volume=100)
        for i in range(n)
    ]


def test_runtime_import_path_callable() -> None:
    from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability as fn

    assert callable(fn)


def test_shpe_output_consumed_into_feat_dict() -> None:
    payload = PipelineInput(candles_5m=_candles(), l2_snapshots=[], funding=[], open_interest=[], liquidation_clusters=[], regime_output={})
    engine = MagicMock()
    engine.predict.return_value = SimpleNamespace(p_sweep=0.8, degraded=False, regime_used="range")
    out = get_shpe_probability(engine, payload, bar_index=4)

    feat_dict = {}
    feat_dict["shpe_probability"] = float(out.get("probability", 0.5))
    assert feat_dict["shpe_probability"] == 0.8


def test_shpe_enabled_false_returns_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHPE_ENABLED", "false")
    result = get_shpe_probability(None, PipelineInput(_candles(), [], [], [], [], {}), 0)
    assert result == {"probability": 0.5, "degraded": True, "regime_used": "<disabled>"}


def test_engine_exception_degrades_no_crash() -> None:
    engine = MagicMock()
    engine.predict.side_effect = RuntimeError("boom")
    result = get_shpe_probability(engine, PipelineInput(_candles(), [], [], [], [], {}), 4)
    assert result["degraded"] is True and result["probability"] == 0.5


def test_run_cycle_injects_shpe_not_equal_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(main, "_validate_exchange_symbol_format", lambda *_a, **_k: True)
    monkeypatch.setattr(main, "SIGNAL_ONLY_MODE", False)
    monkeypatch.setitem(main.SIGNAL_PIPELINE_CONFIG, "signal_only_mode", False)
    monkeypatch.setattr(main, "_prune_reconciliation_blocks", lambda: None)
    monkeypatch.setattr(main, "_fetch_open_interest", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(main, "_fetch_funding_rate", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(main, "analyze_volume_intelligence", lambda **_k: {})
    monkeypatch.setattr(main, "_append_orderbook_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_get_orderbook_snapshot_history", lambda: [{}, {}, {}])
    monkeypatch.setattr(main, "run_all_engines", lambda **_k: {
        "stop_hunt_detected": False,
        "liquidation_data": {"events": []},
        "cascade_probability": 0.0,
        "market_state": {"allow_trade": False},
        "alpha": {},
        "smc_signal": {},
        "liquidity_map": {},
        "liquidation_heatmap": {"heat_score": 0, "color": "green"},
    })
    monkeypatch.setattr(main, "evaluate_meta_filter", lambda **_k: {
        "allow_trade": False, "risk_scale": 0.0, "reason": "test_block", "meta_state": {}
    })
    monkeypatch.setattr(main.trade_lifecycle, "update", lambda *_a, **_k: {
        "block_new_entries": True, "reason": "test"
    })
    monkeypatch.setattr(main.trade_lifecycle, "session_guard", lambda: {})
    monkeypatch.setattr(main.position_manager, "has_position", lambda: False)
    monkeypatch.setattr(main, "_fetch_market_snapshot", lambda *_a, **_k: {
        "snapshot_ts": 1_700_000_000.0,
        "candles_by_tf": {
            "1m": [[1_700_000_000_000 + i * 60_000, 1, 1, 1, 1 + i * 0.01, 1] for i in range(5)],
            "5m": [[1_700_000_000_000 + i * 300_000, 1, 1, 1, 1 + i * 0.01, 1] for i in range(5)],
            "1h": [],
            "15m": [],
        },
        "analysis_orderbook": {"bids": [[1.0, 1.0]], "asks": [[1.1, 1.0]]},
        "execution_orderbook": {"bids": [[1.0, 1.0]], "asks": [[1.1, 1.0]]},
        "trades": [],
    })
    monkeypatch.setattr(main.feature_engine, "update", lambda *_a, **_k: {"features": {"latency_ms": 1, "liquidity_score": 1, "spread_bps": 1, "imbalance": 0}})
    monkeypatch.setattr(main.fill_model, "enrich", lambda x: x)
    monkeypatch.setattr(main.tox_filter, "enrich", lambda x: x)
    monkeypatch.setattr(main.impact_tracker, "update", lambda *_a, **_k: {})
    monkeypatch.setattr(main.order_router, "route", lambda *_a, **_k: {"execute": False, "reason": "test", "order_type": "market"})
    monkeypatch.setattr(main.signal_engine, "generate", lambda _f: {"signal": "HOLD", "confidence": 0.0})
    monkeypatch.setattr(main.alpha_orchestrator, "orchestrate", lambda *_a, **_k: type("Sig", (), {"action": type("A", (), {"value": 0})(), "net_conviction": 0.0, "meta_info": {}})())
    monkeypatch.setattr(main.basis_normalizer, "set_venues", lambda *_a, **_k: None)
    monkeypatch.setattr(main.basis_normalizer, "seed", lambda **_k: None)
    monkeypatch.setattr(main.basis_normalizer, "update", lambda **_k: None)
    monkeypatch.setattr(main.basis_normalizer, "validate", lambda: type("S", (), {"ok": True, "reason": "", "basis": 0.0, "basis_pct": 0.0})())
    monkeypatch.setattr(main, "log_trade", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "send_telegram_message", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_execute_liquidity_trade", lambda **_k: {})

    captured = {}

    def _decide(*_a, **kwargs):
        captured.update(kwargs.get("features", {}))
        return {"execute": False, "position_size": 0.0}

    monkeypatch.setattr(main.execution_engine, "decide", _decide)
    exchange = SimpleNamespace(id="x", load_markets=lambda: {"BTC/USDT": {}})

    main.run_analysis_cycle(exchange, data_exchange=exchange, data_symbol="BTC/USDT")
    assert "shpe_probability" in captured
    assert "shpe_degraded" in captured
    assert "shpe_regime_used" in captured
    assert 0.0 <= float(captured["shpe_probability"]) <= 1.0


def test_stale_l2_timestamp_raises_value_error() -> None:
    candles = _candles()
    stale_snap = L2Snapshot(
        timestamp=candles[-1].timestamp - 9999,
        bids=(BookLevel(49999, 1.0),),
        asks=(BookLevel(50001, 1.0),),
    )
    payload = PipelineInput(candles, [stale_snap], [], [], [], {})
    with pytest.raises(ValueError, match="timestamp mismatch"):
        build_feature_vector(payload, len(candles) - 1, max_clock_skew_sec=60)


def test_300_bar_replay_probabilities_bounded() -> None:
    candles = _candles(300)
    payload = PipelineInput(
        candles_5m=candles,
        l2_snapshots=[],
        funding=[FundingPoint(timestamp=c.timestamp, rate_8h=0.0) for c in candles],
        open_interest=[OpenInterestPoint(timestamp=c.timestamp, oi_usd=1.0) for c in candles],
        liquidation_clusters=[],
        regime_output={},
    )
    engine = MagicMock()
    engine.predict.return_value = SimpleNamespace(p_sweep=0.7, degraded=False, regime_used="range")
    probs = [get_shpe_probability(engine, payload, i)["probability"] for i in range(1, 300)]
    assert all(0.0 <= p <= 1.0 for p in probs)
