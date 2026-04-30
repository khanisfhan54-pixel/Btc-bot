import math
import threading
import engine

def test_clamp_valid_and_invalid_inputs_deterministic():
    assert engine._clamp(5.0, 0.0, 10.0) == 5.0
    assert engine._clamp(-5.0, 0.0, 10.0) == 0.0
    assert engine._clamp(15.0, 0.0, 10.0) == 10.0
    assert engine._clamp("4.5", 0.0, 10.0) == 4.5
    invalid_cases = ["abc", None, float("nan"), float("inf"), float("-inf")]
    first = [engine._clamp(v, 0.0, 10.0) for v in invalid_cases]
    second = [engine._clamp(v, 0.0, 10.0) for v in invalid_cases]
    assert len(first) == len(second)
    for a, b in zip(first, second):
        assert a == b == 0.0

def test_spread_pct_price_anchor_and_invalid_price():
    ob = {'bids': [[100, 1]], 'asks': [[102, 1]]}
    s1 = engine._spread_pct(ob, 101.0)
    s2 = engine._spread_pct(ob, 202.0)
    assert s1 > s2
    for bad in [0, -1, None, float('nan'), float('inf'), float('-inf')]:
        out = engine._spread_pct(ob, bad)
        assert isinstance(out, float)
        assert out >= 0.0

def test_funding_trap_detector_schema_and_determinism():
    a = engine.funding_trap_detector(0.03, 0.5)
    b = engine.funding_trap_detector(0.03, 0.5)
    assert a == b
    assert set(['trap', 'severity', 'funding_rate']).issubset(a.keys())

def test_book_volumes_sorted_unsorted_and_malformed():
    sorted_ob = {'bids': [[100, 1], [99, 2], [98, 3]], 'asks': [[101, 1], [102, 2], [103, 3]]}
    unsorted_ob = {'bids': [[98, 3], [100, 1], [99, 2]], 'asks': [[103, 3], [101, 1], [102, 2]]}
    assert engine._book_volumes(sorted_ob, depth=1) == (1.0, 1.0)
    assert engine._book_volumes(unsorted_ob, depth=1) == (1.0, 1.0)
    assert engine._book_volumes(sorted_ob, depth=2) == (3.0, 3.0)
    assert engine._book_volumes(unsorted_ob, depth=2) == (3.0, 3.0)
    malformed = {'bids': [[100, 1], ['bad', 2], [99]], 'asks': [[101, 1], None, ['102', 'x']]}
    bid, ask = engine._book_volumes(malformed, depth=10)
    assert bid == 3.0 and ask == 1.0
    assert engine._book_volumes({'bids': [], 'asks': [[101, 1]]}, depth=10) == (0, 1.0)
    assert engine._book_volumes({'bids': [[100, 1]], 'asks': []}, depth=10) == (1.0, 0)
    assert engine._book_volumes({'bids': 'x', 'asks': None}, depth=10) == (0, 0)

def test_book_volume_downstream_determinism_unsorted_equivalent():
    sorted_ob = {'bids': [[100, 1], [99, 2], [98, 3]], 'asks': [[101, 1], [102, 2], [103, 3]]}
    unsorted_ob = {'bids': [[98, 3], [100, 1], [99, 2]], 'asks': [[103, 3], [101, 1], [102, 2]]}
    assert engine.order_imbalance_engine(sorted_ob) == engine.order_imbalance_engine(unsorted_ob)
    assert engine.order_flow_pressure_engine(sorted_ob, [{'price': 100, 'amount': 1, 'side': 'BUY'}], 100) == engine.order_flow_pressure_engine(unsorted_ob, [{'price': 100, 'amount': 1, 'side': 'BUY'}], 100)

def test_order_flow_pressure_price_anchor_and_invalid_price_safety():
    ob = {'bids': [[100, 5]], 'asks': [[101, 5]]}
    trades_missing_px = [{'amount': 1.0, 'side': 'BUY'}]
    out_100 = engine.order_flow_pressure_engine(ob, trades_missing_px, 100.0)
    out_200 = engine.order_flow_pressure_engine(ob, trades_missing_px, 200.0)
    assert out_100['aggressive_buy_usd'] == 100.0
    assert out_200['aggressive_buy_usd'] == 200.0
    for bad_px in [0, -1, None, float('nan'), float('inf'), float('-inf')]:
        out = engine.order_flow_pressure_engine(ob, trades_missing_px, bad_px)
        assert isinstance(out, dict)
        assert set(['pressure_score','buy_pressure','sell_pressure','aggressive_buy_usd','aggressive_sell_usd','bid_consumption','ask_consumption','trade_delta','book_delta']).issubset(out.keys())


def test_apply_meta_risk_scale_inputs_no_exception_and_schema():
    cases = [1, 0.5, '0.25', 'abc', None, float('nan'), float('inf')]
    for rs in cases:
        out = engine.apply_meta_to_decision({'execute': True, 'position_size': 1.0}, {'allow_trade': True, 'risk_scale': rs})
        assert isinstance(out, dict)
        assert set(['execute', 'position_size', 'risk_scale', 'meta_result']).issubset(out.keys())
        assert 0.0 <= out['risk_scale'] <= 1.0
        assert out['position_size'] >= 0.0


def test_apply_meta_allow_trade_false_preserved():
    out = engine.apply_meta_to_decision({'execute': True, 'position_size': 1.0}, {'allow_trade': False, 'risk_scale': 'abc', 'reason': 'x'})
    assert out['execute'] is False
    assert out['risk_scale'] == 0.0
    assert out['reason'] == 'x'


def test_get_meta_filter_thread_safety():
    results = []
    barrier = threading.Barrier(20)
    def call():
        barrier.wait()
        results.append(id(engine._get_meta_filter()))
    ts = [threading.Thread(target=call) for _ in range(20)]
    for t in ts: t.start()
    for t in ts: t.join()
    non_none = [r for r in results if r != id(None)]
    if non_none:
        assert len(set(non_none)) == 1


def test_compute_sma_invalid_inputs_fail_closed_neutral():
    for fast, slow in [(10, 10), (30, 10), (0, 30), (10, 0), (-2, 30)]:
        out = engine.compute_sma_signal(list(range(50)), fast, slow)
        assert out == {'signal': 'NEUTRAL', 'sma_fast': 0.0, 'sma_slow': 0.0, 'bias': 0.0}


def test_compute_sma_valid_and_short_input_preserved():
    valid = engine.compute_sma_signal(list(range(50)), 10, 30)
    assert valid['signal'] in ('BUY', 'SELL', 'NEUTRAL')
    short = engine.compute_sma_signal([1, 2, 3], 10, 30)
    assert short == {'signal': 'NEUTRAL', 'sma_fast': 0.0, 'sma_slow': 0.0, 'bias': 0.0}


def test_best_bid_ask_unsorted():
    assert engine._best_bid_ask({'bids': [[84000, 1], [84100, 1]], 'asks': [[84300, 1], [84200, 1]]}) == (84100.0, 84200.0)


def test_validate_alpha_micro_macro():
    r = engine._validate_alpha({'confidence': 0.7, 'prob_above': 0.6, 'prob_below': 0.4, 'direction': 'LONG', 'micro_prob': float('nan'), 'macro_prob': 1.5})
    assert math.isfinite(r['micro_prob']) and 0 <= r['macro_prob'] <= 1


def test_liq_convention():
    ev = [{'side': 'BUY', 'usd': 500000}, {'side': 'SELL', 'usd': 300000}]
    a = engine.liquidation_stream_processor(ev, 'BINANCE_PERP')
    b = engine.liquidation_stream_processor(ev, 'DIRECT')
    assert a['short_liquidations'] == 500000.0 and b['long_liquidations'] == 500000.0
