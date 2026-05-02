import inspect
from backtest_engine import BacktestEngine, BacktestConfig


def _candles(n=80):
    out=[];p=50000.0
    for i in range(n):
        o=p;c=p+1.0;h=max(o,c)+1;l=min(o,c)-1;v=10
        out.append([i,o,h,l,c,v]);p=c
    return out


def test_no_mixed_execution_branch_markers():
    source = inspect.getsource(BacktestEngine.run_backtest)
    assert 'production_parity_requires_regime_engine' in source
    assert 'production_valid' in source


def test_synthetic_mode_marked_non_production_valid():
    bt=BacktestEngine(config=BacktestConfig(), signal_only=True)
    out=bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    assert out['production_valid'] is False


def test_production_mode_fail_closed_without_microstructure():
    bt=BacktestEngine(config=BacktestConfig(), signal_only=True)
    out=bt.run_backtest(_candles(), signal_quality_required=True, allow_ohlcv_synthetic=False)
    assert out['signal_quality_valid'] is False
    assert out['signal_quality_reason'] == 'production_parity_requires_regime_engine'
    assert out['production_valid'] is False


def test_determinism():
    bt=BacktestEngine(config=BacktestConfig(), signal_only=True)
    r1=bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    r2=bt.run_backtest(_candles(), signal_quality_required=False, allow_ohlcv_synthetic=True)
    assert r1 == r2
