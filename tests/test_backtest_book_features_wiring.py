from backtest_engine import BacktestEngine, BacktestConfig
import backtest_engine as be


class _RecordingFeatureEngine:
    def __init__(self):
        self.rows = []

    def update(self, snapshot, trades):
        bid = float(snapshot["bids"][0][0])
        ask = float(snapshot["asks"][0][0])
        spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0
        ofi = float(snapshot["bids"][0][1]) - float(snapshot["asks"][0][1])
        feats = {"spread_bps": spread_bps, "ofi_zscore": ofi, "ofi_norm": ofi, "liquidity_score": 1.0}
        self.rows.append(feats)
        return {"features": feats}


class _HoldSignalEngine:
    def generate(self, features):
        return {"signal": "HOLD", "confidence": 0.0}


def _bars(n=60):
    out = []
    ts0 = 1700000000000
    for i in range(n):
        close = 100.0 + i * 0.1
        out.append([ts0 + i * 60000, close, close + 1.0, close - 1.0, close, 10.0])
    return out


def _aligned_book(bars):
    out = []
    for i, b in enumerate(bars):
        mid = b[4]
        spread = 0.01 + (i % 5) * 0.005
        out.append(
            {
                "timestamp": b[0],
                "bids": [[mid - spread / 2.0, 1.0 + i]],
                "asks": [[mid + spread / 2.0, 0.5 + i * 0.2]],
            }
        )
    return out


def test_real_book_features_are_used_and_not_synthetic():
    bars = _bars()
    book = _aligned_book(bars)
    engine = BacktestEngine(BacktestConfig(legacy_mode=True))
    rec = _RecordingFeatureEngine()
    engine.feature_engine = rec
    engine.signal_engine = _HoldSignalEngine()

    engine.run_backtest(bars, book_features=book)
    rows = rec.rows
    assert len(rows) == len(bars) - 25
    expected = [
        {
            "spread_bps": ((book[i]["asks"][0][0] - book[i]["bids"][0][0]) / ((book[i]["asks"][0][0] + book[i]["bids"][0][0]) / 2.0)) * 10000.0,
            "ofi_zscore": book[i]["bids"][0][1] - book[i]["asks"][0][1],
        }
        for i in range(25, len(bars))
    ]
    assert [round(r["spread_bps"], 8) for r in rows] == [round(e["spread_bps"], 8) for e in expected]
    assert [round(r["ofi_zscore"], 8) for r in rows] == [round(e["ofi_zscore"], 8) for e in expected]
    assert len({round(r["spread_bps"], 8) for r in rows}) > 1
    assert len({round(r["ofi_zscore"], 8) for r in rows}) > 1

    engine2 = BacktestEngine(BacktestConfig(legacy_mode=True))
    rec2 = _RecordingFeatureEngine()
    engine2.feature_engine = rec2
    engine2.signal_engine = _HoldSignalEngine()
    engine2.run_backtest(bars, book_features=None)
    assert [round(r["spread_bps"], 8) for r in rec2.rows] != [round(e["spread_bps"], 8) for e in expected]


def test_alignment_mismatch_fails_closed():
    bars = _bars()
    engine = BacktestEngine(BacktestConfig(legacy_mode=True))
    engine.feature_engine = _RecordingFeatureEngine()
    engine.signal_engine = _HoldSignalEngine()

    short_book = _aligned_book(bars[:-1])
    try:
        engine.run_backtest(bars, book_features=short_book)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_multi_resolution_real_book_wiring_and_determinism(monkeypatch):
    bars = _bars(500)
    book = _aligned_book(bars)

    def _resample(src, minutes=5, base_minutes=1):
        step = int(minutes / base_minutes)
        return [src[i] for i in range(step - 1, len(src), step)]

    monkeypatch.setattr(be, "resample_bars", _resample)

    engine = BacktestEngine(BacktestConfig(legacy_mode=True))
    rec = _RecordingFeatureEngine()
    engine.feature_engine = rec
    engine.signal_engine = _HoldSignalEngine()

    out1 = engine.run_backtest_multi_resolution(bars, book_features=book)
    rows1 = list(rec.rows)

    bars_5m = _resample(bars, minutes=5)
    bars_15m = _resample(bars, minutes=15)

    # Validate 5m/15m consume aligned real book by parity with direct single-resolution runs.
    engine_5m = BacktestEngine(BacktestConfig(legacy_mode=True))
    engine_5m.feature_engine = _RecordingFeatureEngine()
    engine_5m.signal_engine = _HoldSignalEngine()
    direct_5m = engine_5m.run_backtest(bars_5m, book_features=be.align_book_to_bars(bars_5m, book))

    engine_15m = BacktestEngine(BacktestConfig(legacy_mode=True))
    engine_15m.feature_engine = _RecordingFeatureEngine()
    engine_15m.signal_engine = _HoldSignalEngine()
    direct_15m = engine_15m.run_backtest(bars_15m, book_features=be.align_book_to_bars(bars_15m, book))

    assert out1["5m"]["total_trades"] == direct_5m["total_trades"]
    assert out1["15m"]["total_trades"] == direct_15m["total_trades"]
    engine2 = BacktestEngine(BacktestConfig(legacy_mode=True))
    rec2 = _RecordingFeatureEngine()
    engine2.feature_engine = rec2
    engine2.signal_engine = _HoldSignalEngine()
    out2 = engine2.run_backtest_multi_resolution(bars, book_features=book)
    assert out1 == out2

    engine3 = BacktestEngine(BacktestConfig(legacy_mode=True))
    engine3.feature_engine = _RecordingFeatureEngine()
    engine3.signal_engine = _HoldSignalEngine()
    fallback_out = engine3.run_backtest_multi_resolution(bars, book_features=None)
    assert set(fallback_out.keys()) == {"1m", "5m", "15m"}

    misordered = _aligned_book(bars)
    misordered[10], misordered[11] = misordered[11], misordered[10]
    try:
        engine.run_backtest(bars, book_features=misordered)
        assert False, "expected ValueError"
    except ValueError:
        pass
