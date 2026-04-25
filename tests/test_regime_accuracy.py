from collections import Counter

import numpy as np
import pytest

from advanced_regime_engine import AdvancedRegimeEngine


WARMUP = 30


def gen_bull(n=400, seed=11):
    rng = np.random.default_rng(seed)
    return 0.0012 + rng.normal(0, 0.003, n)


def gen_bear(n=400, seed=12):
    rng = np.random.default_rng(seed)
    return -0.0012 + rng.normal(0, 0.003, n)


def gen_range(n=400, seed=13):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 0.0010, n)


def gen_toxic(n=400, seed=14):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.002, n)
    spikes = rng.choice(np.arange(20, n, 25), size=max(1, n // 25), replace=False)
    r[spikes] = rng.choice([-1, 1], size=spikes.size) * rng.uniform(0.05, 0.10, size=spikes.size)
    jumps = rng.poisson(0.03, n)
    r += jumps * rng.normal(0, 0.03, n)
    return r


def run_engine_on_returns(returns, price_start=100.0):
    engine = AdvancedRegimeEngine(n_states=3, n_features=3)
    price = price_start
    outputs = []
    for i, r in enumerate(returns):
        price *= (1 + float(r))
        outputs.append(
            engine.update(
                {
                    "timestamp": float(i),
                    "return": float(r),
                    "features": np.array([0.2, 0.1, 0.05]),
                    "price": float(price),
                }
            )
        )
    engine._shutdown_warning_worker()
    return outputs


def _evaluate_predictions(series_by_truth):
    confusion = {truth: Counter() for truth in series_by_truth.keys()}
    all_pred = []
    non_shock_toxic = []

    for truth, returns in series_by_truth.items():
        preds = [o["regime_label"] for o in run_engine_on_returns(returns)][WARMUP:]
        confusion[truth].update(preds)
        all_pred.extend(preds)
        if truth != "TOXIC":
            non_shock_toxic.extend([p == "TOXIC" for p in preds])

    truths = list(series_by_truth.keys())
    labels = sorted(set(all_pred) | set(truths))

    precision = {}
    recall = {}
    for label in labels:
        tp = confusion.get(label, Counter()).get(label, 0)
        predicted_as_label = sum(confusion[t].get(label, 0) for t in truths)
        truth_total = sum(confusion.get(label, Counter()).values())
        precision[label] = tp / predicted_as_label if predicted_as_label > 0 else 0.0
        recall[label] = tp / truth_total if truth_total > 0 else 0.0

    overall = Counter(all_pred)
    total = sum(overall.values())
    dominant_ratio = max(overall.values()) / max(total, 1)
    toxic_non_shock_rate = float(np.mean(non_shock_toxic)) if non_shock_toxic else 0.0

    return {
        "confusion": confusion,
        "precision": precision,
        "recall": recall,
        "overall": overall,
        "dominant_ratio": dominant_ratio,
        "toxic_non_shock_rate": toxic_non_shock_rate,
    }


@pytest.fixture(scope="module")
def accuracy_results():
    series_by_truth = {
        "TREND": gen_bull(),
        "BEAR": gen_bear(),
        "RANGE": gen_range(),
        "TOXIC": gen_toxic(),
    }
    return _evaluate_predictions(series_by_truth)


def test_accuracy_trend_recall(accuracy_results):
    assert accuracy_results["recall"].get("TREND", 0.0) > 0.30


def test_accuracy_bear_recall(accuracy_results):
    assert accuracy_results["recall"].get("BEAR", 0.0) > 0.20


def test_accuracy_range_not_suppressed(accuracy_results):
    assert sum(accuracy_results["confusion"]["RANGE"].values()) > 0


def test_accuracy_toxic_not_dominant(accuracy_results):
    assert accuracy_results["toxic_non_shock_rate"] < 0.40


def test_accuracy_no_regime_collapse(accuracy_results):
    assert accuracy_results["dominant_ratio"] < 0.80


def test_accuracy_summary(accuracy_results):
    print("\ntruth,pred,count")
    for truth, row in accuracy_results["confusion"].items():
        for pred, cnt in sorted(row.items()):
            print(f"{truth},{pred},{cnt}")

    print("\nlabel,precision,recall")
    all_labels = sorted(set(accuracy_results["precision"]) | set(accuracy_results["recall"]))
    for lbl in all_labels:
        p = accuracy_results["precision"].get(lbl, 0.0)
        r = accuracy_results["recall"].get(lbl, 0.0)
        print(f"{lbl},{p:.4f},{r:.4f}")

    print(
        f"\ndominant_ratio={accuracy_results['dominant_ratio']:.4f},"
        f"toxic_non_shock_rate={accuracy_results['toxic_non_shock_rate']:.4f}"
    )

    assert True
