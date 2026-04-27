from advanced_regime_engine import AdvancedRegimeEngine


def test_warmup_not_complete_if_only_time_met():
    eng = AdvancedRegimeEngine(enable_background_workers=False)
    eng._valid_return_count = 0
    eng._first_valid_return_ts = 0.0
    assert eng._warmup_progress(current_ts=1e9) < 1.0


def test_warmup_not_complete_if_only_ticks_met():
    eng = AdvancedRegimeEngine(enable_background_workers=False)
    eng._valid_return_count = eng._shock_warmup_ticks
    eng._first_valid_return_ts = None
    assert eng._warmup_progress(current_ts=0.0) < 1.0


def test_warmup_complete_when_both_met():
    eng = AdvancedRegimeEngine(enable_background_workers=False)
    eng._valid_return_count = eng._shock_warmup_ticks
    eng._first_valid_return_ts = 0.0
    assert eng._warmup_progress(current_ts=eng._shock_warmup_seconds + 1.0) >= 1.0
