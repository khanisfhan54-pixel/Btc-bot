"""Standalone verification for Issue 27 on PRICE_RETURN_MISMATCH path."""

from advanced_regime_engine import AdvancedRegimeEngine


def main() -> None:
    eng = AdvancedRegimeEngine(n_features=3, target_vol=0.02, seed=42)
    eng._last_price = 100.0
    eng._last_price_timestamp = 1.0
    eng.last_signed_position_size = 1.0

    before = int(eng._obs_counter)
    out = eng.update(
        {
            "timestamp": 2.0,
            "price": 110.0,
            "return": 0.0,  # guaranteed mismatch vs price-implied +10%
            "features": [0.0, 0.0, 0.0],
        }
    )
    after = int(eng._obs_counter)

    assert out["risk_metrics"]["feed_status"] == "PRICE_RETURN_MISMATCH", out
    assert (after - before) == 1, (before, after)
    print("PASS: _obs_counter incremented on PRICE_RETURN_MISMATCH early return.")


if __name__ == "__main__":
    main()
