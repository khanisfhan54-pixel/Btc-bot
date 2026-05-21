from stop_hunt_engine.data.derivatives import LiquidationCluster
from stop_hunt_engine.features.liquidation_proximity import compute_liquidation_proximity


def test_stale_when_clusters_too_old() -> None:
    as_of = 1_700_010_000.0
    old_ts = as_of - 7200.0
    clusters = [
        LiquidationCluster(price=49500.0, size_usd=1e6, side="long", as_of=old_ts),
        LiquidationCluster(price=50500.0, size_usd=1e6, side="short", as_of=old_ts),
    ]
    result = compute_liquidation_proximity(as_of, 50000.0, clusters, stale_seconds=3600)
    assert result.stale is True


def test_fresh_when_clusters_recent() -> None:
    as_of = 1_700_010_000.0
    clusters = [
        LiquidationCluster(price=49500.0, size_usd=1e6, side="long", as_of=as_of - 60),
        LiquidationCluster(price=50500.0, size_usd=1e6, side="short", as_of=as_of - 60),
    ]
    result = compute_liquidation_proximity(as_of, 50000.0, clusters, stale_seconds=3600)
    assert result.stale is False
