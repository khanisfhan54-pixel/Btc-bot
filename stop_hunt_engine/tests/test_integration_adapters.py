import numpy as np

from stop_hunt_engine.data.candle_store import Candle
from stop_hunt_engine.data.derivatives import FundingPoint, LiquidationCluster, OpenInterestPoint
from stop_hunt_engine.data.l2_snapshot import BookLevel, L2Snapshot
from stop_hunt_engine.integrations.feature_pipeline import PipelineInput
from stop_hunt_engine.integrations.signal_adapter import get_shpe_probability
from stop_hunt_engine.model.engine import StopHuntProbabilityEngine
from stop_hunt_engine.model.regime_conditional import RegimeConditionalClassifier


def _engine():
    clf=RegimeConditionalClassifier(feature_names=[str(i) for i in range(29)],min_samples_per_regime=5)
    X=np.random.RandomState(1).randn(20,29)
    y=np.array([0,1]*10)
    clf.fit(X,y,["range"]*20,run_importance_audit=False)
    return StopHuntProbabilityEngine(classifier=clf)


def test_live_compat_mocked_streams():
    candles=[Candle(timestamp=1_700_000_000+i*300,open=50000+i,high=50010+i,low=49990+i,close=50002+i,volume=100+i) for i in range(30)]
    l2=[L2Snapshot(timestamp=c.timestamp,bids=(BookLevel(c.close-1,1.0),),asks=(BookLevel(c.close+1,1.0),)) for c in candles]
    funding=[FundingPoint(timestamp=candles[0].timestamp,rate_8h=0.0)]
    oi=[OpenInterestPoint(timestamp=c.timestamp,oi_usd=2_000_000_000+i*5000) for i,c in enumerate(candles)]
    liq=[LiquidationCluster(price=candles[-1].close*0.99,size_usd=1e6,side="long",as_of=candles[-1].timestamp),LiquidationCluster(price=candles[-1].close*1.01,size_usd=1e6,side="short",as_of=candles[-1].timestamp)]
    payload=PipelineInput(candles,l2,funding,oi,liq,{"regime_label":"range"})
    out=get_shpe_probability(_engine(),payload,len(candles)-1)
    assert 0.0 <= out["probability"] <= 1.0
    assert isinstance(out["degraded"], bool)
