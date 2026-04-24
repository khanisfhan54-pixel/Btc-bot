import math
import random
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alpha_orchestrator as ao


def _make_config(**overrides):
    base = dict(
        signal_weights={"alpha_a": 1.0, "alpha_b": 1.0, "alpha_c": 1.0, "alpha_d": 1.0},
        regime_alignment={},
        feedback_enabled=False,
        allow_unknown_sources=True,
        default_unknown_weight=1.0,
        min_aggregate_weight=0.0,
        correlation_min_conviction=0.0,
        correlation_min_group_size=2,
    )
    base.update(overrides)
    return ao.OrchestratorConfig(**base)


def _sig(source_id: str, direction: int, conviction: float, edge: float, timeframe: str = "1m", group: str = "g"):
    return ao.AlphaSignal(
        source_id=source_id,
        direction=direction,
        conviction=conviction,
        expected_edge_bps=edge,
        timestamp=1_700_000_000.0,
        timeframe=timeframe,
        correlation_group_id=group,
    )


def _run_fuse(orch: ao.AlphaOrchestrator, signals):
    return orch._fuse_signals(signals, "normal", 0.0, {}, regime_assessment=None)


def _assert_invariants(score, edge, meta):
    eps = 1e-8
    breakdown = meta["breakdown"]
    summary = meta["correlation_summary"]

    assert math.isfinite(score)
    assert math.isfinite(edge)

    raw_denom = 0.0
    denom = 0.0
    nd_final = 0.0
    for row in breakdown:
        raw = float(row["raw_weight_contribution"])
        pre = float(row["effective_weight_contribution"])
        final = float(row["final_weight_contribution"])
        direction = int(row["direction"])

        assert pre <= raw + eps
        assert final <= pre + eps
        assert final >= -eps

        if direction in (-1, 1):
            raw_denom += raw
            denom += max(0.0, final)
        else:
            nd_final += final

    assert abs(nd_final) <= eps
    assert abs(denom - sum(float(r["final_weight_contribution"]) for r in breakdown)) <= eps * max(1.0, denom)
    assert abs(raw_denom - summary["total_raw_weight"]) <= eps * max(1.0, raw_denom, summary["total_raw_weight"])
    assert summary["total_pre_cap_weight"] <= summary["total_raw_weight"] + eps
    assert summary["total_adjusted_weight"] <= summary["total_pre_cap_weight"] + eps


def test_fuse_determinism_100_runs_identical():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"a": 5.0, "b": 2.0, "c": 1.0, "d": 0.5}))
    signals = [
        _sig("a", 1, 0.97, 35.0, "1m", "mom"),
        _sig("b", 1, 0.83, 21.0, "1m", "mom"),
        _sig("c", -1, 0.62, 18.0, "1m", "mean_revert"),
        _sig("d", 1, 0.51, 9.0, "1m", "carry"),
    ]

    baseline = _run_fuse(orch, signals)
    for _ in range(100):
        current = _run_fuse(orch, signals)
        assert current == baseline


def test_dominance_cap_single_dominant_signal_is_capped_and_nonzero():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"big": 100.0, "s1": 1.0, "s2": 1.0}))
    signals = [_sig("big", 1, 1.0, 20.0), _sig("s1", 1, 1.0, 20.0), _sig("s2", 1, 1.0, 20.0)]

    _, _, meta = _run_fuse(orch, signals)
    rows = {r["source_id"]: r for r in meta["breakdown"]}
    total = sum(float(r["final_weight_contribution"]) for r in rows.values())

    assert total > 0.0
    assert rows["big"]["dominance_cap_active"] is True
    assert rows["big"]["final_weight_contribution"] <= 0.4 * total + 1e-8


def test_dominance_cap_multiple_dominant_signals_and_extreme_skew():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"a": 100.0, "b": 99.0, "c": 0.01, "d": 0.01}))
    signals = [_sig("a", 1, 1.0, 10.0), _sig("b", 1, 1.0, 10.0), _sig("c", 1, 1.0, 10.0), _sig("d", 1, 1.0, 10.0)]

    _, _, meta = _run_fuse(orch, signals)
    finals = [float(r["final_weight_contribution"]) for r in meta["breakdown"]]
    total = sum(finals)

    assert total > 0.0
    for final in finals:
        assert final <= 0.4 * total + 1e-8


def test_dominance_threshold_solver_has_nonzero_feasible_solution():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"a": 10.0, "b": 1.0, "c": 1.0}))
    signals = [_sig("a", 1, 1.0, 10.0), _sig("b", 1, 1.0, 10.0), _sig("c", 1, 1.0, 10.0)]
    _, _, meta = _run_fuse(orch, signals)
    rows = {r["source_id"]: r for r in meta["breakdown"]}
    assert rows["a"]["final_weight_contribution"] > 0.0
    assert rows["a"]["final_weight_contribution"] < rows["a"]["raw_weight_contribution"]


def test_correlation_penalty_identical_signals_applies():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"a": 1.0, "b": 1.0, "c": 1.0}))
    signals = [_sig("a", 1, 1.0, 10.0, group="shared"), _sig("b", 1, 1.0, 10.0, group="shared"), _sig("c", 1, 1.0, 10.0, group="shared")]

    _, _, meta = _run_fuse(orch, signals)
    for row in meta["breakdown"]:
        assert row["correlation_penalty"] < 1.0
        assert row["effective_weight_contribution"] < row["raw_weight_contribution"]


def test_correlation_groups_separate_penalties():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}))
    signals = [
        _sig("a", 1, 1.0, 10.0, group="g1"),
        _sig("b", 1, 1.0, 10.0, group="g1"),
        _sig("c", 1, 1.0, 10.0, group="g2"),
        _sig("d", 1, 1.0, 10.0, group="g2"),
    ]

    _, _, meta = _run_fuse(orch, signals)
    groups = meta["correlation_summary"]["groups"]
    assert len(groups) == 2
    for g in groups:
        assert g["size"] == 2
    summary = meta["correlation_summary"]
    assert 0.0 <= summary["group_normalized_correlation_impact"] <= 1.0
    assert 0.0 <= summary["group_normalized_dominance_cap_impact"] <= 1.0


def test_edge_cases_zero_denom_and_large_values():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"a": 100.0, "b": 100.0}, min_aggregate_weight=1.0))

    score0, edge0, meta0 = _run_fuse(orch, [_sig("a", 0, 1.0, 0.0), _sig("b", 0, 1.0, 0.0)])
    assert score0 == 0.0
    assert edge0 == 0.0
    _assert_invariants(score0, edge0, meta0)

    score1, edge1, meta1 = _run_fuse(orch, [_sig("a", 1, 1.0, 200.0), _sig("b", -1, 1.0, 200.0)])
    assert math.isfinite(score1)
    assert math.isfinite(edge1)
    _assert_invariants(score1, edge1, meta1)


def test_large_n_1000_signals_stable():
    weights = {f"s{i}": 1.0 + (i % 7) * 0.01 for i in range(1000)}
    orch = ao.AlphaOrchestrator(_make_config(signal_weights=weights, allow_unknown_sources=False))
    signals = [
        _sig(
            source_id=f"s{i}",
            direction=1 if i % 3 else -1,
            conviction=0.9,
            edge=20.0 + (i % 5),
            timeframe="1m",
            group=f"g{i % 20}",
        )
        for i in range(1000)
    ]
    score, edge, meta = _run_fuse(orch, signals)
    assert math.isfinite(score)
    assert math.isfinite(edge)
    _assert_invariants(score, edge, meta)


def test_fuzz_invariants_1500_cases():
    rng = random.Random(7)
    orch = ao.AlphaOrchestrator(_make_config())
    source_ids = [f"s{i}" for i in range(10)]

    for _ in range(1500):
        n = rng.randint(1, 12)
        signals = []
        for i in range(n):
            sid = source_ids[rng.randrange(len(source_ids))]
            direction = rng.choice([-1, 0, 1])
            conviction = rng.random()
            edge = rng.uniform(0.0, 250.0)
            tf = rng.choice(["1m", "5m", "15m", "1h", "default"])
            group = rng.choice(["g1", "g2", "g3", sid])
            signals.append(_sig(sid, direction, conviction, edge, tf, group))

        score, edge, meta = _run_fuse(orch, signals)
        _assert_invariants(score, edge, meta)


def test_monte_carlo_stress_10000_signal_sets():
    rng = random.Random(99)
    orch = ao.AlphaOrchestrator(_make_config())

    for _ in range(10_000):
        n = rng.randint(1, 8)
        signals = [
            _sig(
                source_id=f"mc_{rng.randint(0, 50)}",
                direction=rng.choice([-1, 0, 1]),
                conviction=rng.random(),
                edge=rng.uniform(0.0, 300.0),
                timeframe=rng.choice(["1m", "5m", "1h", "default"]),
                group=rng.choice(["g1", "g2", "g3", "g4"]),
            )
            for _ in range(n)
        ]
        score, edge, meta = _run_fuse(orch, signals)
        assert -1.0 <= score <= 1.0
        assert math.isfinite(score)
        assert math.isfinite(edge)
        _assert_invariants(score, edge, meta)


def test_regression_behavior_existing_dominance_meta_contract():
    orch = ao.AlphaOrchestrator(_make_config(signal_weights={"big": 50.0, "a": 1.0, "b": 1.0}, allow_unknown_sources=False))
    signals = [_sig("big", 1, 1.0, 30.0), _sig("a", 1, 1.0, 15.0), _sig("b", 1, 1.0, 15.0)]

    _, _, meta = _run_fuse(orch, signals)
    breakdown = meta["breakdown"]
    big_row = next(r for r in breakdown if r["source_id"] == "big")
    assert big_row["dominance_cap_active"] is True
    assert big_row["final_weight_contribution"] < big_row["raw_weight_contribution"]
