from collections import Counter, defaultdict

import numpy as np
import pytest

from advanced_regime_engine import AdvancedRegimeEngine


def generate_bull_trend(n=500, seed=42):
    rng = np.random.default_rng(seed)
    return 0.0008 + rng.normal(0, 0.003, n)


def generate_bear_trend(n=500, seed=42):
    rng = np.random.default_rng(seed)
    return -0.0008 + rng.normal(0, 0.003, n)


def generate_range_bound(n=500, seed=42):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.0015, n)


def generate_vol_shock(n=500, seed=42):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.002, n)
    for i in range(50, n, 100):
        r[i] = rng.choice([-1, 1]) * rng.uniform(0.05, 0.10)
    return r


def generate_mixed_regime(n=600, seed=42):
    rng = np.random.default_rng(seed)
    bull = 0.001 + rng.normal(0, 0.003, 200)
    rang = rng.normal(0, 0.0015, 200)
    bear = -0.001 + rng.normal(0, 0.003, 200)
    return np.concatenate([bull, rang, bear])


def generate_jump_diffusion(n=500, seed=42):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.002, n)
    jumps = rng.poisson(0.02, n)
    r += jumps * rng.normal(0, 0.03, n)
    return r


def generate_low_liquidity(n=500, seed=42):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.008, n)


def run_engine_on_returns(returns, price_start=100.0):
    engine = AdvancedRegimeEngine(n_states=3, n_features=3)
    price = price_start
    outputs = []
    for i, r in enumerate(returns):
        price *= (1 + float(r))
        md = {
            "timestamp": float(i),
            "return": float(r),
            "features": np.array([0.2, 0.1, 0.05]),
            "price": float(price),
        }
        outputs.append(engine.update(md))
    engine._shutdown_warning_worker()
    return outputs


def _iter_numeric(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _iter_numeric(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _iter_numeric(v)
    elif isinstance(x, (int, float, np.floating, np.integer)) and not isinstance(x, bool):
        yield float(x)


SCENARIO_FACTORIES = {
    "bull": generate_bull_trend,
    "bear": generate_bear_trend,
    "range": generate_range_bound,
    "vol_shock": generate_vol_shock,
    "mixed": generate_mixed_regime,
    "jump": generate_jump_diffusion,
    "low_liquidity": generate_low_liquidity,
}


def _collect_mc_metrics(n_trials=20):
    required_keys = {"schema_version", "regime_label", "risk_metrics", "alpha"}
    metrics = {}
    global_labels = set()

    for scenario, factory in SCENARIO_FACTORIES.items():
        regime_counts = Counter()
        cb_triggers_per_trial = []
        total_ticks = 0
        toxic_ticks = 0
        halted_ticks = 0
        finite_ok = True
        schema_ok = True
        pos_ok = True
        signed_pos_ok = True
        vol_ok = True

        for trial_seed in range(n_trials):
            outputs = run_engine_on_returns(factory(seed=trial_seed))
            total_ticks += len(outputs)

            prev_halted = False
            cb_count = 0
            for out in outputs:
                labels = out["regime_label"]
                regime_counts[labels] += 1
                global_labels.add(labels)

                if labels == "TOXIC":
                    toxic_ticks += 1
                is_halted = labels == "HALTED"
                if is_halted:
                    halted_ticks += 1
                if is_halted and not prev_halted:
                    cb_count += 1
                prev_halted = is_halted

                nums = list(_iter_numeric(out))
                finite_ok = finite_ok and bool(nums) and bool(np.all(np.isfinite(nums)))

                schema_ok = schema_ok and required_keys.issubset(out.keys())

                ps = float(out.get("position_size", 0.0))
                sps = float(out.get("signed_position_size", 0.0))
                pos_ok = pos_ok and (0.0 <= ps <= 0.35)
                signed_pos_ok = signed_pos_ok and (-0.35 <= sps <= 0.35)

                vol = float(out["risk_metrics"]["expected_volatility"])
                vol_ok = vol_ok and (0.0 < vol <= 0.20)

            cb_triggers_per_trial.append(cb_count)

        metrics[scenario] = {
            "regime_counts": regime_counts,
            "toxic_rate": toxic_ticks / max(total_ticks, 1),
            "halted_rate": halted_ticks / max(total_ticks, 1),
            "cb_rate": float(np.mean(cb_triggers_per_trial)),
            "finite_ok": finite_ok,
            "schema_ok": schema_ok,
            "pos_ok": pos_ok,
            "signed_pos_ok": signed_pos_ok,
            "vol_ok": vol_ok,
            "total_ticks": total_ticks,
        }

    return metrics, global_labels


@pytest.fixture(scope="module")
def mc_results():
    return _collect_mc_metrics(n_trials=20)


def test_mc_all_outputs_finite(mc_results):
    metrics, _ = mc_results
    assert all(s["finite_ok"] for s in metrics.values())


def test_mc_position_bounds(mc_results):
    metrics, _ = mc_results
    assert all(s["pos_ok"] and s["signed_pos_ok"] for s in metrics.values())


def test_mc_schema_valid(mc_results):
    metrics, _ = mc_results
    assert all(s["schema_ok"] for s in metrics.values())


def test_mc_toxic_rate_bounded(mc_results):
    metrics, _ = mc_results
    for scenario in ("bull", "bear", "range"):
        assert metrics[scenario]["toxic_rate"] < 0.50


def test_mc_halted_rate_bounded(mc_results):
    metrics, _ = mc_results
    for scenario in ("bull", "bear", "range", "mixed"):
        assert metrics[scenario]["halted_rate"] < 0.20


def test_mc_regime_diversity(mc_results):
    _, global_labels = mc_results
    assert len(global_labels) >= 2


def test_mc_vol_bounds(mc_results):
    metrics, _ = mc_results
    assert all(s["vol_ok"] for s in metrics.values())


def test_mc_bull_trend_recognized(mc_results):
    metrics, _ = mc_results
    bull_counts = metrics["bull"]["regime_counts"]
    bull_total = sum(bull_counts.values())
    trend_ratio = bull_counts["TREND"] / max(bull_total, 1)
    assert trend_ratio > 0.30


def test_mc_bear_trend_recognized(mc_results):
    metrics, _ = mc_results
    bear_counts = metrics["bear"]["regime_counts"]
    bear_total = sum(bear_counts.values())
    bear_ratio = bear_counts["BEAR"] / max(bear_total, 1)
    assert bear_ratio > 0.20


def test_mc_summary_table(mc_results):
    metrics, _ = mc_results
    labels = ["TREND", "BEAR", "RANGE", "TOXIC", "HALTED", "UNKNOWN"]

    print("\nscenario,total,toxic_rate,halted_rate,cb_rate," + ",".join(labels))
    for scenario in sorted(metrics.keys()):
        row = metrics[scenario]
        counts = row["regime_counts"]
        label_counts = [str(counts.get(lbl, 0)) for lbl in labels]
        print(
            f"{scenario},{row['total_ticks']},{row['toxic_rate']:.4f},"
            f"{row['halted_rate']:.4f},{row['cb_rate']:.2f}," + ",".join(label_counts)
        )

    assert True
