"""Deterministic replay and timestamp alignment regression tests."""
from __future__ import annotations

import pytest

from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.data.derivatives import FundingPoint, OpenInterestPoint
from stop_hunt_engine.data.l2_snapshot import BookLevel, L2Snapshot
from stop_hunt_engine.integrations.feature_pipeline import (
    PipelineInput,
    _nearest_past_snapshot,
    build_feature_vector,
)


def _make_candles(n: int, base_ts: float = 1_700_000_000.0, step: float = 300.0):
    return [
        Candle(
            timestamp=base_ts + i * step,
            open=50_000 + i,
            high=50_020 + i,
            low=49_980 + i,
            close=50_005 + i,
            volume=100 + i,
        )
        for i in range(n)
    ]


def _make_l2(candles):
    return [
        L2Snapshot(
            timestamp=c.timestamp,
            bids=(BookLevel(c.close - 1, 2.0),),
            asks=(BookLevel(c.close + 1, 1.5),),
        )
        for c in candles
    ]


def test_nearest_past_selects_largest_le_candle_ts():
    candles = _make_candles(10)
    l2 = _make_l2(candles)
    candle_ts = candles[5].timestamp
    snap, delta = _nearest_past_snapshot(l2, candle_ts, max_skew_sec=3600)
    assert snap is not None
    assert snap.timestamp <= candle_ts
    assert delta == abs(candle_ts - snap.timestamp)


def test_nearest_past_rejects_future_snapshot_beyond_skew():
    candles = _make_candles(5)
    future_snap = L2Snapshot(
        timestamp=candles[0].timestamp + 9999,
        bids=(BookLevel(50_000, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )
    snap, delta = _nearest_past_snapshot([future_snap], candles[0].timestamp, max_skew_sec=3600)
    assert snap is None or delta is None or delta > 3600


def test_nearest_past_accepts_minor_boundary_drift():
    candles = _make_candles(3)
    slightly_ahead = L2Snapshot(
        timestamp=candles[1].timestamp + 30,
        bids=(BookLevel(50_000, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )
    snap, delta = _nearest_past_snapshot([slightly_ahead], candles[1].timestamp, max_skew_sec=3600)
    assert snap is not None
    assert delta <= 3600


def test_stale_past_snapshot_rejected_by_build():
    candles = _make_candles(5)
    stale_snap = L2Snapshot(
        timestamp=candles[-1].timestamp - 9999,
        bids=(BookLevel(49_999, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )
    payload = PipelineInput(candles, [stale_snap], [], [], [], {})
    with pytest.raises(ValueError, match="timestamp mismatch"):
        build_feature_vector(payload, len(candles) - 1, max_clock_skew_sec=60)


def test_future_snapshot_rejected_by_build():
    candles = _make_candles(5)
    future_snap = L2Snapshot(
        timestamp=candles[0].timestamp + 9000,
        bids=(BookLevel(50_000, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )
    payload = PipelineInput(candles, [future_snap], [], [], [], {})
    with pytest.raises(ValueError, match="timestamp mismatch"):
        build_feature_vector(payload, 0, max_clock_skew_sec=3600)


def test_advancing_replay_does_not_degrade():
    candles = _make_candles(30)
    l2 = _make_l2(candles)
    oi = [OpenInterestPoint(timestamp=c.timestamp, oi_usd=2_000_000_000 + i * 1000) for i, c in enumerate(candles)]
    funding = [FundingPoint(timestamp=candles[0].timestamp, rate_8h=0.0001)]

    for i in range(1, len(candles)):
        payload = PipelineInput(candles, l2, funding, oi, [], {"regime_label": "range"})
        fv = build_feature_vector(payload, i)
        assert fv.bar_index == i
        assert fv.timestamp == candles[i].timestamp


def test_replay_with_sparse_l2_no_false_degradation():
    candles = _make_candles(20)
    l2_sparse = _make_l2(candles[::5])

    for i in range(5, 20):
        payload = PipelineInput(candles, l2_sparse, [], [], [], {})
        fv = build_feature_vector(payload, i, max_clock_skew_sec=3600)
        assert fv.bar_index == i


def test_replay_oi_sparse_no_false_degradation():
    candles = _make_candles(30)
    oi_sparse = [
        OpenInterestPoint(timestamp=candles[j].timestamp, oi_usd=2_000_000_000)
        for j in range(0, 30, 10)
    ]
    for i in range(10, 30):
        payload = PipelineInput(candles, [], [], oi_sparse, [], {})
        fv = build_feature_vector(payload, i, max_clock_skew_sec=3600)
        assert fv.bar_index == i


def test_probabilities_bounded_over_advancing_replay():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability

    candles = _make_candles(50)
    l2 = _make_l2(candles)
    oi = [OpenInterestPoint(timestamp=c.timestamp, oi_usd=2e9) for c in candles]
    funding = [FundingPoint(timestamp=c.timestamp, rate_8h=0.0001) for c in candles]

    engine = MagicMock()
    engine.predict.return_value = SimpleNamespace(p_sweep=0.65, degraded=False, regime_used="range")

    for i in range(1, 50):
        payload = PipelineInput(candles, l2, funding, oi, [], {"regime_label": "range"})
        out = get_shpe_probability(engine, payload, i)
        assert 0.0 <= out["probability"] <= 1.0, f"bar {i}: probability out of bounds"
        assert isinstance(out["degraded"], bool)


def test_degraded_only_on_genuinely_stale_data():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability

    candles = _make_candles(10)
    l2_fresh = _make_l2(candles)

    engine = MagicMock()
    engine.predict.return_value = SimpleNamespace(p_sweep=0.4, degraded=False, regime_used="range")

    payload = PipelineInput(candles, l2_fresh, [], [], [], {})
    out = get_shpe_probability(engine, payload, len(candles) - 1)
    assert out["degraded"] is False

    stale_l2 = [L2Snapshot(
        timestamp=candles[-1].timestamp - 9999,
        bids=(BookLevel(49_999, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )]
    payload_stale = PipelineInput(candles, stale_l2, [], [], [], {})
    out_stale = get_shpe_probability(engine, payload_stale, len(candles) - 1)
    assert out_stale["degraded"] is True
    assert out_stale["probability"] == 0.5
