import json, os
from engine import (
    apply_meta_to_decision,_get_meta_filter,_validate_alpha,compute_sma_signal,_trade_side,_best_bid_ask,
    detect_liquidity_sweep,liquidation_stream_processor,_enforce_entry_fee_metadata,_clamp,run_all_engines
)

FIX='tests/fixtures/engine_baseline.json'
FIX2='tests/fixtures/run_all_engines_baseline.json'

def test_capture_baseline():
    os.makedirs('tests/fixtures',exist_ok=True)
    out={}
    out['apply_meta_to_decision_normal_allow']=apply_meta_to_decision({'execute':True,'position_size':1.0},{'allow_trade':True,'risk_scale':1.0})
    out['apply_meta_to_decision_block']=apply_meta_to_decision({'execute':True,'position_size':1.0},{'allow_trade':False,'risk_scale':0.0,'reason':'test_block'})
    out['apply_meta_to_decision_hi']=apply_meta_to_decision({'execute':True,'position_size':0.5},{'allow_trade':True,'risk_scale':2.0})
    out['apply_meta_to_decision_lo']=apply_meta_to_decision({'execute':True,'position_size':0.5},{'allow_trade':True,'risk_scale':-1.0})
    m1=_get_meta_filter(); m2=_get_meta_filter(); out['_get_meta_filter_same']= (id(m1)==id(m2))
    out['_validate_alpha']=[_validate_alpha(x) for x in [
        {'confidence':0.7,'prob_above':0.6,'prob_below':0.4,'direction':'LONG','micro_prob':0.8,'macro_prob':0.3},
        {'confidence':float('nan'),'prob_above':0.5,'prob_below':0.5,'direction':'NEUTRAL'},
        {'confidence':0.9,'prob_above':1.5,'prob_below':-0.5,'direction':'LONG'},
        {},'not_a_dict']]
    out['compute_sma_signal_valid']=compute_sma_signal(list(range(50)),10,30)
    out['_trade_side']=[_trade_side(x) for x in [{'side':'BUY'},{'S':'SELL'},{'takerSide':'buy'},{'side':None,'S':None,'takerSide':None},{}]]
    out['_best_bid_ask']=[_best_bid_ask(x) for x in [
        {'bids':[[84100,1.0],[84000,2.0]],'asks':[[84200,1.0],[84300,2.0]]},
        {'bids':[[84000,2.0],[84100,1.0]],'asks':[[84300,2.0],[84200,1.0]]},
        {'bids':[],'asks':[]},]]
    out['detect_liquidity_sweep']=detect_liquidity_sweep([{'price':84000,'amount':0.6,'side':'BUY'}],84000)
    out['liquidation_stream_processor']=liquidation_stream_processor([{'side':'BUY','usd':500000},{'side':'SELL','usd':300000}])
    out['_enforce_entry_fee_metadata']=[_enforce_entry_fee_metadata(*x) for x in [(10.0,'pct','trade_001'),(10.0,'quote','trade_002'),(10.0,'invalid_type','trade_003'),(10.0,None,'trade_004')]]
    out['_clamp']=[_clamp(*x) for x in [(5.0,0.0,10.0),('not_a_number',0.0,10.0),(float('nan'),0.0,10.0)]]
    with open(FIX,'w') as f: json.dump(out,f,indent=2,default=str)

    run_out = run_all_engines(orderbook={'bids':[[84000,1]],'asks':[[84010,1]]},trades=[{'price':84005,'amount':0.1,'side':'BUY'}],price=84005,recent_candles=[[0,1,2,0.5,1.5,10]]*50,oi_history=[100,102],open_interest=102)
    with open(FIX2,'w') as f: json.dump(run_out,f,indent=2,default=str)
    assert os.path.exists(FIX)
