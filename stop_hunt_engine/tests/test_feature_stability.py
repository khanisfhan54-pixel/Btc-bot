import numpy as np

from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.data.derivatives import FundingPoint, LiquidationCluster, OpenInterestPoint
from stop_hunt_engine.data.l2_snapshot import BookLevel, L2Snapshot
from stop_hunt_engine.features.feature_vector import compute_feature_vector
from stop_hunt_engine.model.engine import feature_vector_to_array


def _candles(n=80):
    out=[]
    for i in range(n):
        p=50000+i*5
        out.append(Candle(timestamp=1_700_000_000+i*300, open=p, high=p+20, low=p-20, close=p+2, volume=100+i))
    return out


def test_feature_vector_no_nan_and_deterministic():
    candles=_candles()
    l2=[L2Snapshot(timestamp=c.timestamp,bids=(BookLevel(c.close-1,2.0),),asks=(BookLevel(c.close+1,1.5),)) for c in candles]
    funding=[FundingPoint(timestamp=candles[k].timestamp, rate_8h=0.0001*((k%5)-2)) for k in range(0,len(candles),16)]
    oi=[OpenInterestPoint(timestamp=c.timestamp, oi_usd=2_000_000_000 + k*100_000) for k,c in enumerate(candles)]
    liq=[LiquidationCluster(price=candles[-1].close*0.99,size_usd=1e6,side="long",as_of=candles[-1].timestamp), LiquidationCluster(price=candles[-1].close*1.01,size_usd=1.5e6,side="short",as_of=candles[-1].timestamp)]
    fv1=compute_feature_vector(len(candles)-1,candles,l2_snapshots=l2,funding=funding,open_interest=oi,liquidation_clusters=liq,regime_output={"regime_label":"range"})
    fv2=compute_feature_vector(len(candles)-1,candles,l2_snapshots=l2,funding=funding,open_interest=oi,liquidation_clusters=liq,regime_output={"regime_label":"range"})
    a1=feature_vector_to_array(fv1); a2=feature_vector_to_array(fv2)
    assert np.isfinite(a1).all()
    assert np.allclose(a1,a2)
