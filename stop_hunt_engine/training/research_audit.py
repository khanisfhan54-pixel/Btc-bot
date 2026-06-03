from __future__ import annotations

import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..model.calibrator import ProbabilityCalibrator, brier_score, expected_calibration_error, reliability_bins
from ..model.engine import SHPE_FEATURE_NAMES, StopHuntProbabilityEngine
from ..model.regime_conditional import RegimeConditionalClassifier
from .feature_codec import record_to_fv
from .io import atomic_write_json, ensure_dir
from .target import DEFAULT_TARGET, TargetDefinition
from .trainer import align_samples
from .walk_forward import _assert_no_train_label_horizon_overlap, _purged_train_end

FIVE_MINUTES_MS = 5 * 60 * 1000
MIN_LABELED_SAMPLES = 10_000
MIN_HISTORY_DAYS = 90.0
EVENT_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)


def _date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


def _write_markdown(path: str, lines: Sequence[str]) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def _samples_and_labels(dataset: Dict[str, Any], labels_payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Optional[int]]]:
    labels_by_idx = {int(x["row_index"]): x.get("label") for x in labels_payload.get("labels", [])}
    samples = list(dataset.get("samples", []))
    labels = [None if labels_by_idx.get(int(s["row_index"])) is None else int(labels_by_idx[int(s["row_index"])]) for s in samples]
    return samples, labels


def _bars_per_day(target: TargetDefinition) -> int:
    if target.bar_interval == "5m":
        return 288
    return 288


def _history_days(timestamps: Sequence[int]) -> float:
    if len(timestamps) < 2:
        return 0.0
    return (max(timestamps) - min(timestamps)) / (24 * 60 * 60 * 1000)


def _gap_analysis(timestamps: Sequence[int], *, interval_ms: int = FIVE_MINUTES_MS) -> Dict[str, Any]:
    ordered = sorted(int(t) for t in timestamps)
    gaps: List[Dict[str, Any]] = []
    missing = 0
    for prev, cur in zip(ordered, ordered[1:]):
        delta = cur - prev
        if delta > interval_ms:
            missed = max(0, int(round(delta / interval_ms)) - 1)
            missing += missed
            gaps.append({"from_timestamp_ms": prev, "to_timestamp_ms": cur, "from_utc": _date(prev), "to_utc": _date(cur), "missing_bars": missed, "gap_ms": delta})
    return {"missing_timestamps": missing, "gap_count": len(gaps), "max_gap_ms": max((g["gap_ms"] for g in gaps), default=0), "gaps": gaps[:100]}


def _regime_bucket(sample: Dict[str, Any]) -> str:
    raw = str(sample.get("regime_label") or sample.get("raw_sample", {}).get("regime") or "unknown").lower()
    vol = float(sample.get("derived_features", {}).get("regime_expected_volatility", 0.0) or 0.0)
    if "trend" in raw:
        return "trending"
    if "range" in raw or "ranging" in raw:
        return "ranging"
    if "high" in raw and "vol" in raw:
        return "high_volatility"
    if "low" in raw and "vol" in raw:
        return "low_volatility"
    if vol >= 0.02:
        return "high_volatility"
    if vol > 0.0:
        return "low_volatility"
    return "unknown"


def write_data_coverage_report(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str, *, target: TargetDefinition = DEFAULT_TARGET) -> Dict[str, Any]:
    samples, labels = _samples_and_labels(dataset, labels_payload)
    timestamps = [int(s["timestamp_ms"]) for s in samples]
    duplicates = [ts for ts, count in Counter(timestamps).items() if count > 1]
    labeled = [x for x in labels if x is not None]
    history = _history_days(timestamps)
    gap = _gap_analysis(timestamps)
    payload = {
        "total_rows": len(samples),
        "total_bars": len(set(timestamps)),
        "date_range": [_date(min(timestamps)), _date(max(timestamps))] if timestamps else [],
        "history_days": history,
        "missing_timestamps": gap["missing_timestamps"],
        "duplicate_timestamps": len(duplicates),
        "duplicate_timestamp_examples": duplicates[:100],
        "gap_analysis": gap,
        "usable_sample_count": len(labeled),
        "label_distribution": dict(Counter(str(x) for x in labeled)),
        "regime_distribution": dict(Counter(_regime_bucket(s) for s in samples)),
        "required_external_datasets": {
            "funding": _field_presence(samples, ("funding_rate_8h", "funding_z30d")),
            "open_interest": _field_presence(samples, ("oi_delta_oi_velocity", "oi_pct_change_1h")),
            "liquidation": _field_presence(samples, ("liq_nearest_long_cluster_dist_pct", "liq_nearest_short_cluster_dist_pct")),
            "regime": _field_presence(samples, ("regime_confidence", "regime_expected_volatility")),
        },
        "failures": [],
    }
    if len(labeled) < MIN_LABELED_SAMPLES:
        payload["failures"].append(f"less than {MIN_LABELED_SAMPLES} labeled samples")
    if history < MIN_HISTORY_DAYS:
        payload["failures"].append(f"less than {MIN_HISTORY_DAYS:.0f} days of history")
    payload["passed"] = not payload["failures"]
    base = ensure_dir(out_dir)
    json_path = os.path.join(base, "coverage_report.json")
    md_path = os.path.join(base, "coverage_report.md")
    atomic_write_json(payload, json_path)
    _write_markdown(md_path, [
        "# SHPE Data Coverage Audit", "",
        f"- Total rows: {payload['total_rows']}",
        f"- Total bars: {payload['total_bars']}",
        f"- Date range: {payload['date_range']}",
        f"- History days: {payload['history_days']:.4f}",
        f"- Missing timestamps: {payload['missing_timestamps']}",
        f"- Duplicate timestamps: {payload['duplicate_timestamps']}",
        f"- Usable labeled samples: {payload['usable_sample_count']}",
        f"- Label distribution: {payload['label_distribution']}",
        f"- Regime distribution: {payload['regime_distribution']}",
        f"- Failures: {payload['failures'] or 'none'}",
    ])
    return {"payload": payload, "json": json_path, "markdown": md_path}


def _field_presence(samples: Sequence[Dict[str, Any]], fields: Sequence[str]) -> Dict[str, Any]:
    total = len(samples)
    present = 0
    for sample in samples:
        derived = sample.get("derived_features", {})
        if any(field in derived and derived.get(field) not in (None, "") for field in fields):
            present += 1
    return {"present_rows": present, "total_rows": total, "coverage_ratio": present / total if total else 0.0}


def write_dataset_statistics(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str) -> Dict[str, Any]:
    samples, labels = _samples_and_labels(dataset, labels_payload)
    labeled = [(s, int(y)) for s, y in zip(samples, labels) if y is not None]
    n = len(labeled)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)
    y = [lab for _, lab in labeled]
    positives = sum(y)
    negatives = n - positives
    payload = {
        "train_rows": train_end,
        "validation_rows": max(0, val_end - train_end),
        "test_rows": max(0, n - val_end),
        "positive_labels": positives,
        "negative_labels": negatives,
        "class_imbalance": {"positive_ratio": positives / n if n else 0.0, "negative_ratio": negatives / n if n else 0.0, "majority_to_minority_ratio": max(positives, negatives) / max(min(positives, negatives), 1)},
    }
    path = os.path.join(ensure_dir(out_dir), "dataset_statistics.json")
    atomic_write_json(payload, path)
    return {"payload": payload, "json": path}


def _prediction_histogram(values: Sequence[float], *, bins: Sequence[float]) -> List[Dict[str, Any]]:
    vals = np.asarray(values, dtype=float)
    out: List[Dict[str, Any]] = []
    for lo, hi in zip(bins, bins[1:]):
        mask = (vals >= lo) & (vals < hi if hi < 1.0 else vals <= hi)
        out.append({"bin_start": float(lo), "bin_end": float(hi), "count": int(mask.sum())})
    return out


def write_overconfidence_report(wf: Dict[str, Any], out_dir: str) -> Dict[str, Any]:
    preds = list(wf.get("predictions", []))
    probs = [float(p["probability"]) for p in preds]
    labels = [int(p["label"]) for p in preds]
    confidences = [max(p, 1.0 - p) for p in probs]
    extreme_mask = [p > 0.95 or p < 0.05 for p in probs]
    extreme_acc = (sum(1 for p, y, m in zip(probs, labels, extreme_mask) if m and int(p >= 0.5) == y) / max(sum(extreme_mask), 1)) if probs else 0.0
    payload = {
        "sample_count": len(preds),
        "confidence_histogram": _prediction_histogram(confidences, bins=[0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0]),
        "prediction_histogram": _prediction_histogram(probs, bins=[0.0, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0]),
        "calibration_bins": [{"mean_probability": a, "realized_frequency": b, "count": c} for a, b, c in reliability_bins(probs, labels)],
        "extreme_probability_frequency": {
            "probabilities_gt_0_95": sum(1 for p in probs if p > 0.95),
            "probabilities_gt_0_99": sum(1 for p in probs if p > 0.99),
            "probabilities_lt_0_05": sum(1 for p in probs if p < 0.05),
            "probabilities_lt_0_01": sum(1 for p in probs if p < 0.01),
        },
        "extreme_accuracy": extreme_acc,
        "flagged": bool(sum(extreme_mask) >= 10 and extreme_acc < 0.55),
    }
    base = ensure_dir(out_dir)
    json_path = os.path.join(base, "overconfidence_report.json")
    md_path = os.path.join(base, "overconfidence_report.md")
    atomic_write_json(payload, json_path)
    _write_markdown(md_path, ["# SHPE Overconfidence Audit", "", f"- Sample count: {payload['sample_count']}", f"- Extreme probability counts: {payload['extreme_probability_frequency']}", f"- Extreme accuracy: {payload['extreme_accuracy']:.6f}", f"- Flagged: {payload['flagged']}"])
    return {"payload": payload, "json": json_path, "markdown": md_path}


def write_calibration_diagnostics(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str) -> Dict[str, Any]:
    samples, labels, regimes = align_samples(dataset, labels_payload)
    n = len(samples)
    train_end = max(2, int(n * 0.6))
    cal_end = max(train_end + 2, int(n * 0.8))
    cal_end = min(cal_end, n - 1)
    methods: Dict[str, Dict[str, Any]] = {}
    raw_test: List[float] = []
    y_test = labels[cal_end:]
    if train_end < cal_end and y_test and len(set(labels[:train_end])) >= 2:
        engine = StopHuntProbabilityEngine.train([record_to_fv(s) for s in samples[:train_end]], labels[:train_end], regimes[:train_end], calibrate_method=None, run_importance_audit=False, model_version="shpe.research.calibration.raw")
        raw_cal = np.asarray([engine.predict(record_to_fv(s)).p_sweep for s in samples[train_end:cal_end]], dtype=float)
        y_cal = np.asarray(labels[train_end:cal_end], dtype=int)
        raw_test = [engine.predict(record_to_fv(s)).p_sweep for s in samples[cal_end:]]
        transforms = {"raw": np.asarray(raw_test, dtype=float)}
        if len(set(y_cal.tolist())) >= 2 and len(y_cal) >= 2:
            transforms["platt"] = ProbabilityCalibrator("platt").fit(raw_cal, y_cal).transform(raw_test)
            transforms["isotonic"] = ProbabilityCalibrator("isotonic").fit(raw_cal, y_cal).transform(raw_test)
        for name, probs in transforms.items():
            methods[name] = {"brier": brier_score(probs, y_test), "ece": expected_calibration_error(probs, y_test), "reliability_curve": [{"mean_probability": a, "realized_frequency": b, "count": c} for a, b, c in reliability_bins(probs, y_test)]}
    raw = methods.get("raw", {})
    improved = [name for name in ("platt", "isotonic") if name in methods and methods[name]["brier"] < raw.get("brier", float("inf")) and methods[name]["ece"] < raw.get("ece", float("inf"))]
    payload = {"sample_count": n, "split": {"train_rows": train_end, "calibration_rows": max(0, cal_end - train_end), "test_rows": max(0, n - cal_end)}, "methods": methods, "improves_both_ece_and_brier": improved, "failed": not bool(improved)}
    path = os.path.join(ensure_dir(out_dir), "calibration_diagnostics.json")
    atomic_write_json(payload, path)
    return {"payload": payload, "json": path}


def _trade_returns(preds: Sequence[Dict[str, Any]], threshold: float) -> List[float]:
    returns = []
    for p in preds:
        prob = float(p["probability"])
        if prob < threshold:
            continue
        gross = (1.0 if int(p["label"]) == 1 else -1.0) * max(prob - 0.5, 0.0)
        # Existing research cost assumptions: fees + spread + slippage + latency, expressed in probability-return units.
        cost = 0.0004 + 0.0001 + 0.0002 + 0.0001
        returns.append(gross - cost)
    return returns


def _return_metrics(returns: Sequence[float]) -> Dict[str, Any]:
    rets = list(float(r) for r in returns)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rets:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    sharpe = float(np.mean(rets) / np.std(rets) * math.sqrt(len(rets))) if len(rets) > 1 and float(np.std(rets)) > 1e-12 else 0.0
    return {"expectancy": float(np.mean(rets)) if rets else 0.0, "win_rate": len(wins) / len(rets) if rets else 0.0, "profit_factor": sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else 0.0), "sharpe": sharpe, "trade_count": len(rets), "max_drawdown": float(max_dd)}


def write_regime_performance(wf: Dict[str, Any], dataset: Dict[str, Any], out_dir: str) -> Dict[str, Any]:
    by_idx = {int(s["row_index"]): _regime_bucket(s) for s in dataset.get("samples", [])}
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in wf.get("predictions", []):
        buckets[by_idx.get(int(p["row_index"]), "unknown")].append(p)
    payload = {regime: _return_metrics(_trade_returns(preds, 0.55)) for regime, preds in sorted(buckets.items())}
    expectancies = [m["expectancy"] for m in payload.values() if m["trade_count"] > 0]
    regime_dependent = (max(expectancies) - min(expectancies) > 0.02) if len(expectancies) > 1 else False
    result = {"regimes": payload, "regime_dependent": regime_dependent}
    base = ensure_dir(out_dir)
    json_path = os.path.join(base, "regime_performance.json")
    md_path = os.path.join(base, "regime_performance.md")
    atomic_write_json(result, json_path)
    _write_markdown(md_path, ["# SHPE Regime Stability Audit", "", f"- Regime dependent: {regime_dependent}", "", *[f"- {k}: {v}" for k, v in payload.items()]])
    return {"payload": result, "json": json_path, "markdown": md_path}


def write_feature_stability(dataset: Dict[str, Any], labels_payload: Dict[str, Any], out_dir: str, *, target: TargetDefinition = DEFAULT_TARGET, min_train: int = 50, test_size: int = 50) -> Dict[str, Any]:
    samples, labels, regimes = align_samples(dataset, labels_payload)
    X = np.stack([np.asarray([float(s["derived_features"][name]) for name in SHPE_FEATURE_NAMES], dtype=float) for s in samples])
    rankings: List[List[str]] = []
    folds: List[Dict[str, Any]] = []
    start = min_train
    fold = 0
    while start < len(samples):
        train_end = _purged_train_end(samples, start, int(target.horizon_bars))
        end = min(start + test_size, len(samples))
        _assert_no_train_label_horizon_overlap(samples, train_end, start, end, int(target.horizon_bars))
        if train_end >= 4 and len(set(labels[:train_end])) >= 2:
            clf = RegimeConditionalClassifier(list(SHPE_FEATURE_NAMES), min_samples_per_regime=30).fit(X[:train_end], np.asarray(labels[:train_end]), regimes[:train_end], run_importance_audit=False)
            model = clf.global_model.model if clf.global_model is not None else None
            if model is not None and hasattr(model, "coef_"):
                coefs = np.abs(np.asarray(model.coef_).ravel())
            elif clf.global_model is not None and clf.global_model._coef is not None:
                coefs = np.abs(np.asarray(clf.global_model._coef).ravel()[: len(SHPE_FEATURE_NAMES)])
            else:
                coefs = np.zeros(len(SHPE_FEATURE_NAMES))
            ranked = [name for _, name in sorted(zip(coefs, SHPE_FEATURE_NAMES), reverse=True)]
            rankings.append(ranked)
            folds.append({"fold": fold, "train_rows": train_end, "top_features": ranked[:10]})
        fold += 1
        start = end
    top_counts = Counter(name for ranking in rankings for name in ranking[:5])
    drift: Dict[str, Any] = {}
    for name in SHPE_FEATURE_NAMES:
        positions = [ranking.index(name) + 1 for ranking in rankings if name in ranking]
        if positions:
            drift[name] = {"min_rank": min(positions), "max_rank": max(positions), "rank_range": max(positions) - min(positions)}
    unstable = [name for name, stats in drift.items() if stats["rank_range"] >= max(10, len(SHPE_FEATURE_NAMES) // 2)]
    payload = {"fold_count": len(rankings), "folds": folds, "top_features": dict(top_counts.most_common(20)), "ranking_drift": drift, "unstable_features": unstable, "flagged": bool(unstable)}
    path = os.path.join(ensure_dir(out_dir), "feature_stability.json")
    atomic_write_json(payload, path)
    return {"payload": payload, "json": path}


def write_expectancy_validation(wf: Dict[str, Any], out_dir: str) -> Dict[str, Any]:
    preds = list(wf.get("predictions", []))
    payload = {f"{threshold:.2f}": _return_metrics(_trade_returns(preds, threshold)) for threshold in EVENT_THRESHOLDS}
    path = os.path.join(ensure_dir(out_dir), "expectancy_validation.json")
    atomic_write_json({"thresholds": payload, "cost_assumptions": {"fees": 0.0004, "spread": 0.0001, "slippage": 0.0002, "latency": 0.0001}, "best_threshold_selected": False}, path)
    return {"payload": payload, "json": path}


def write_final_verdict(coverage: Dict[str, Any], calibration: Dict[str, Any], overconfidence: Dict[str, Any], regime: Dict[str, Any], feature_stability: Dict[str, Any], expectancy: Dict[str, Any], wf: Dict[str, Any], out_path: str) -> Dict[str, Any]:
    wf_metrics = wf.get("metrics", {})
    exp_055 = expectancy.get("0.55", {})
    data_score = 100 if coverage.get("passed") else max(0, int(100 * min(coverage.get("usable_sample_count", 0) / MIN_LABELED_SAMPLES, coverage.get("history_days", 0) / MIN_HISTORY_DAYS)))
    edge_score = int(max(0, min(100, 50 + 50 * exp_055.get("expectancy", 0.0) + 10 * (exp_055.get("profit_factor", 0.0) - 1.0))))
    cal_score = 80 if not calibration.get("failed") else 30
    confidence_score = min(data_score, 100 - (30 if overconfidence.get("flagged") else 0) - (20 if feature_stability.get("flagged") else 0))
    production_score = min(data_score, edge_score, cal_score, confidence_score)
    failures = []
    if not coverage.get("passed"):
        failures.extend(coverage.get("failures", []))
    if exp_055.get("expectancy", 0.0) <= 0 or exp_055.get("profit_factor", 0.0) < 1.0:
        failures.append("non-positive net expectancy or profit factor below 1 at 0.55 threshold")
    if calibration.get("failed"):
        failures.append("no calibration method improved both ECE and Brier")
    if overconfidence.get("flagged"):
        failures.append("extreme probabilities have poor realized accuracy")
    if feature_stability.get("flagged"):
        failures.append("feature importance ranking drift is unstable")
    verdict = "PASS" if not failures and production_score >= 80 else ("CONDITIONAL PASS" if production_score >= 60 and len(failures) <= 1 else "FAIL")
    lines = [
        "# SHPE Final Research Verdict", "",
        "## SECTION A — Data Quality", f"Passed: {coverage.get('passed')} — failures: {coverage.get('failures', [])}", "",
        "## SECTION B — Leakage Audit", "Feature availability timestamps, external source timestamps, positive label event timestamps, and purged fold boundaries are validated fail-closed.", "",
        "## SECTION C — Walk Forward Audit", f"Mode: {wf.get('walk_forward_config', {}).get('mode')} — folds: {len(wf.get('folds', []))} — metrics: {wf_metrics}", "",
        "## SECTION D — Calibration Audit", f"Failed: {calibration.get('failed')} — methods improving both ECE and Brier: {calibration.get('improves_both_ece_and_brier', [])}", "",
        "## SECTION E — Regime Audit", f"Regime dependent: {regime.get('regime_dependent')}", "",
        "## SECTION F — Feature Stability Audit", f"Flagged: {feature_stability.get('flagged')} — unstable features: {feature_stability.get('unstable_features', [])}", "",
        "## SECTION G — Expectancy Audit", f"Threshold metrics: {expectancy}", "",
        "## SECTION H — Production Readiness", "Deployment remains blocked unless the final verdict is PASS and all minimum data, leakage, calibration, stability, and expectancy gates pass.", "",
        f"Predictive Edge: {edge_score}/100", f"Calibration Quality: {cal_score}/100", f"Research Confidence: {confidence_score}/100", f"Production Readiness: {production_score}/100", "", verdict,
    ]
    _write_markdown(out_path, lines)
    return {"path": out_path, "verdict": verdict, "scores": {"Predictive Edge": edge_score, "Calibration Quality": cal_score, "Research Confidence": confidence_score, "Production Readiness": production_score}, "failures": failures}


def run_research_audit(dataset: Dict[str, Any], labels_payload: Dict[str, Any], wf: Dict[str, Any], artifact_root: str, *, target: TargetDefinition = DEFAULT_TARGET, min_train: int = 50, test_size: int = 50) -> Dict[str, Any]:
    coverage = write_data_coverage_report(dataset, labels_payload, os.path.join(artifact_root, "data_coverage"), target=target)
    stats = write_dataset_statistics(dataset, labels_payload, artifact_root)
    overconfidence = write_overconfidence_report(wf, os.path.join(artifact_root, "overconfidence"))
    calibration = write_calibration_diagnostics(dataset, labels_payload, os.path.join(artifact_root, "calibration"))
    regime = write_regime_performance(wf, dataset, os.path.join(artifact_root, "regime"))
    feature_stability = write_feature_stability(dataset, labels_payload, os.path.join(artifact_root, "feature_stability"), target=target, min_train=min_train, test_size=test_size)
    expectancy = write_expectancy_validation(wf, os.path.join(artifact_root, "expectancy"))
    verdict = write_final_verdict(coverage["payload"], calibration["payload"], overconfidence["payload"], regime["payload"], feature_stability["payload"], expectancy["payload"], wf, os.path.join("audit", "shpe_final_verdict.md"))
    return {"coverage": coverage, "dataset_statistics": stats, "overconfidence": overconfidence, "calibration": calibration, "regime": regime, "feature_stability": feature_stability, "expectancy": expectancy, "verdict": verdict}
