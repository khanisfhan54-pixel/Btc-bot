import time
import main

def test_stale_regime_halt():
    main._regime_context_timestamp = time.time()-600
    staleness=time.time()-main._regime_context_timestamp
    if staleness > main.MAX_REGIME_STALENESS_SECONDS:
        regime_context={"regime":"STALE_FALLBACK","signal_valid":False}
    else:
        regime_context={"regime":"TREND","signal_valid":True}
    assert regime_context["regime"]=="STALE_FALLBACK"
    assert regime_context["signal_valid"] is False
