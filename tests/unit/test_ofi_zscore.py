import numpy as np
from alpha_liquidity_sweep_predictor import LiquiditySweepAlpha

def test_ofi_zscore_window():
    a=LiquiditySweepAlpha()
    z=0.0
    for i in range(200):
        pb={"bids":[{"price":100+i*0.01,"size":1}],"asks":[{"price":101+i*0.01,"size":1}]}
        cb={"bids":[{"price":100+i*0.01+0.001,"size":1.1}],"asks":[{"price":101+i*0.01-0.001,"size":0.9}]}
        z=a.calculate_ofi_zscore(pb,cb)
    assert -10<=z<=10
