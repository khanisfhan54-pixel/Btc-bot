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


def test_nearest_past_rejects_future_boundary_drift():
    candles = _make_candles(3)
    slightly_ahead = L2Snapshot(
        timestamp=candles[1].timestamp + 30,
        bids=(BookLevel(50_000, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )
    snap, delta = _nearest_past_snapshot([slightly_ahead], candles[1].timestamp, max_skew_sec=3600)
    assert snap is None
    assert delta is None


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


def test_clock_skew_warning_is_throttled(caplog: pytest.LogCaptureFixture):
    from stop_hunt_engine.integrations import feature_pipeline as _fp

    _fp._rate_limited_log._last.clear()
    _fp._rate_limited_log._counts.clear()
    candles = _make_candles(4)
    stale_snap = L2Snapshot(
        timestamp=candles[0].timestamp - 10_000,
        bids=(BookLevel(49_999, 1.0),),
        asks=(BookLevel(50_001, 1.0),),
    )
    payload = PipelineInput(candles, [stale_snap], [], [], [], {})

    with caplog.at_level("WARNING", logger="shpe.feature_pipeline"):
        for _ in range(3):
            with pytest.raises(ValueError, match="timestamp mismatch"):
                build_feature_vector(payload, len(candles) - 1, max_clock_skew_sec=60)
    hits = [r for r in caplog.records if "shpe_pipeline_clock_skew source=l2" in r.message]
    assert len(hits) == 1

from stop_hunt_engine.validation.timestamp_alignment_audit import run_timestamp_alignment_audit


def _audit_row(prediction_ts=1_712_000_000_000, **overrides):
    row = {
        "row_index": 0,
        "prediction_timestamp_ms": prediction_ts,
        "funding_rate_8h": 0.0001,
        "funding_timestamp_ms": prediction_ts,
    }
    row.update(overrides)
    return row


def test_timestamp_alignment_future_timestamp_detected():
    row = _audit_row(funding_timestamp_ms=1_712_000_005_000)

    report = run_timestamp_alignment_audit([row], fail_on_violation=False)

    assert report["status"] == "FAIL"
    assert report["violations"] == [
        {
            "feature": "funding",
            "row": 0,
            "prediction_ts": 1_712_000_000_000,
            "feature_ts": 1_712_000_005_000,
            "leak_ms": 5_000,
        }
    ]
    with pytest.raises(ValueError, match="Timestamp leakage detected"):
        run_timestamp_alignment_audit([row])


def test_timestamp_alignment_equal_timestamp_allowed():
    row = _audit_row(funding_timestamp_ms=1_712_000_000_000)

    report = run_timestamp_alignment_audit([row])

    assert report["status"] == "PASS"
    assert report["summary"]["violations"] == 0


def test_timestamp_alignment_older_timestamp_allowed():
    row = _audit_row(funding_timestamp_ms=1_711_999_995_000)

    report = run_timestamp_alignment_audit([row])

    assert report["status"] == "PASS"
    assert report["summary"]["violations"] == 0


def test_timestamp_alignment_mixed_feature_sources():
    prediction_ts = 1_712_000_000_000
    row = _audit_row(
        prediction_ts,
        funding_timestamp_ms=prediction_ts,
        delta_oi_velocity=1.0,
        oi_timestamp_ms=prediction_ts - 1,
        nearest_long_cluster_dist_pct=0.01,
        liquidation_timestamp_ms=prediction_ts - 2,
        ofi_zscore=0.4,
        last_book_event_ts_ms=prediction_ts - 3,
        regime="range",
        regime_timestamp_ms=prediction_ts + 10,
    )

    report = run_timestamp_alignment_audit([row], fail_on_violation=False)

    assert report["status"] == "FAIL"
    assert report["summary"]["violations"] == 1
    assert report["violations"][0]["feature"] == "regime"
    assert report["violations"][0]["leak_ms"] == 10


def test_timestamp_alignment_multiple_violations(tmp_path):
    prediction_ts = 1_712_000_000_000
    rows = [
        _audit_row(prediction_ts, row_index=3, funding_timestamp_ms=prediction_ts + 5),
        _audit_row(
            prediction_ts,
            row_index=4,
            funding_timestamp_ms=prediction_ts - 1,
            ofi_zscore=0.1,
            last_book_event_ts_ms=prediction_ts + 20,
            delta_oi_velocity=1.0,
            oi_timestamp_ms=prediction_ts + 10,
        ),
    ]

    report = run_timestamp_alignment_audit(rows, tmp_path, fail_on_violation=False)

    assert report["summary"]["total_rows"] == 2
    assert report["summary"]["rows_audited"] == 2
    assert report["summary"]["violations"] == 3
    assert report["summary"]["max_leak_ms"] == 20
    assert report["summary"]["average_leak_ms"] == pytest.approx((5 + 20 + 10) / 3)
    assert {v["row"] for v in report["violations"]} == {3, 4}
    assert (tmp_path / "timestamp_alignment_audit.json").exists()
    assert (tmp_path / "timestamp_alignment_summary.md").exists()
