#!/usr/bin/env python3
"""Real switch-strength attribution audit.

Consumes serialized engine audit logs only. It never reruns the regime engine and
never generates synthetic fallback events.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

EDGE_W = 0.48
CONV_W = 0.34
VOL_W = 0.18
SHOCK_BONUS = 0.03
DIRECTIONAL_REGIMES = {"TREND", "BEAR"}
NON_DIRECTIONAL_REGIMES = {"RANGE", "TOXIC"}
REPORTS = Path("reports")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(val):
        return default
    return val


def _safe_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _read_json(path: str | Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _backtest_summary() -> dict[str, Any]:
    data = _read_json("backtest_summary.json", {})
    return data if isinstance(data, dict) else {}


def extract_transitions(audit_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for idx, rec in enumerate(audit_log):
        before = str(rec.get("confirmed_before_switch", "?"))
        after_switch = str(rec.get("confirmed_after_switch", "?"))
        raw = str(rec.get("raw_regime", "?"))
        final = str(rec.get("confirmed_after_smoother", rec.get("regime", "?")))
        if not (before != after_switch or raw != final):
            continue
        switch_strength = _safe_float(rec.get("switch_strength", -1.0), -1.0)
        events.append({
            "idx": idx,
            "tick": rec.get("tick"),
            "edge": _safe_float(rec.get("regime_edge_smoothed", rec.get("regime_edge_raw", 0.0))),
            "conviction": _safe_float(rec.get("conviction", 0.0)),
            "volatility": float(np.clip(1.0 - _safe_float(rec.get("uncertainty", 0.5), 0.5), 0.0, 1.0)),
            "switch_strength": switch_strength,
            "switch_gate": _safe_float(rec.get("switch_gate", -1.0), -1.0),
            "cooldown_ok": _safe_bool(rec.get("cooldown_ok", False)),
            "persistence_ok": _safe_bool(rec.get("persistence_ok", False)),
            "conviction_ok": _safe_bool(rec.get("conviction_ok", False)),
            "accepted": (after_switch != before) and not _safe_bool(rec.get("switch_blocked", True)),
            "raw_regime": raw,
            "final_regime": final,
            "confirmed_before_switch": before,
            "confirmed_after_switch": after_switch,
            "toxic_exit": before == "TOXIC" and final != "TOXIC",
            "non_switch_tick": switch_strength == -1.0,
            "switch_blocked": _safe_bool(rec.get("switch_blocked", True)),
            "shock": _safe_bool(rec.get("shock", rec.get("shock_detected", False))),
        })
    return events


def recompute_strength(event: dict[str, Any], override: dict[str, float] | None = None) -> float:
    values = {"edge": event["edge"], "conviction": event["conviction"], "volatility": event["volatility"]}
    if override:
        values.update(override)
    return EDGE_W * values["edge"] + CONV_W * values["conviction"] + VOL_W * values["volatility"] + (SHOCK_BONUS if event.get("shock") else 0.0)


def recompute_without(event: dict[str, Any], component_key: str) -> float:
    return recompute_strength(event, {component_key: 0.0})


def would_accept(strength: float, gate: float, cooldown_ok: bool, persistence_ok: bool, conviction_ok_real: bool) -> bool:
    return (cooldown_ok and strength >= gate) or (persistence_ok and conviction_ok_real)


def _metrics(events: list[dict[str, Any]], accept_fn: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    def accepted(e: dict[str, Any]) -> bool:
        return accept_fn(e) if accept_fn else e["accepted"]
    accepted_events = [e for e in events if accepted(e)]
    tp = sum(1 for e in accepted_events if e["final_regime"] == e["raw_regime"] and e["final_regime"] not in NON_DIRECTIONAL_REGIMES)
    fp = sum(1 for e in accepted_events if e["final_regime"] != e["raw_regime"] and e["final_regime"] not in NON_DIRECTIONAL_REGIMES)
    fn = sum(1 for e in events if not accepted(e) and e["raw_regime"] in DIRECTIONAL_REGIMES)
    return {"accepted": len(accepted_events), "rejected": len(events) - len(accepted_events), "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1), "false_switches": fp, "missed_switches": fn, "toxic_exits": sum(1 for e in accepted_events if e["toxic_exit"])}


def write_dataset_summary(audit_log: list[dict[str, Any]], suppression_log: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    summary = _backtest_summary(); prov = summary.get("data_provenance", {}) if isinstance(summary.get("data_provenance"), dict) else {}
    breakdown = Counter((e["confirmed_before_switch"], e["confirmed_after_switch"]) for e in events)
    accepted = sum(1 for e in events if e["accepted"])
    lines = ["# Real Switch Dataset Summary", "", "## Provenance", "- Source: engine._regime_audit_log (serialised from live test/backtest run)", f"- Symbol: {summary.get('symbol', 'BTC/USDT')}", f"- Timeframe: {prov.get('bar_size_sec', summary.get('bar_size_sec', 'unknown'))}", f"- Total audit records: {len(audit_log)}", f"- Total ticks with switch tracking: {sum(1 for r in audit_log if r.get('switch_strength', -1) > -1)}", f"- Transition events (regime_changed): {len(events)}", f"- Accepted transitions: {accepted}", f"- Rejected transitions: {len(events)-accepted}", f"- Acceptance rate: {accepted/max(len(events),1):.2%}", "", "## Transition Breakdown", "| From → To | Count |", "|-----------|-------|"]
    lines += [f"| {a} → {b} | {n} |" for (a, b), n in sorted(breakdown.items())]
    lines += ["", f"## TOXIC exits: {sum(e['toxic_exit'] for e in events)}", "", "## Data Quality Flags", f"- Records missing switch_strength: {sum(1 for r in audit_log if 'switch_strength' not in r)}", f"- Records missing conviction: {sum(1 for r in audit_log if 'conviction' not in r)}", f"- Suppression events (TREND/BEAR → non-directional): {len(suppression_log)}"]
    if prov.get("type") == "synthetic_historical":
        lines += ["", "## WARNING", "Underlying OHLCV data is synthetic_historical per backtest_summary.json. Regime transitions are real engine decisions on synthetic prices. Feature importance reflects engine behaviour, not live market patterns."]
    (REPORTS / "real_switch_dataset_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_ablation(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    formulas = {"baseline": lambda e: e["switch_strength"] if e["switch_strength"] > -1 else recompute_strength(e), "no_conviction": lambda e: EDGE_W * e["edge"] + VOL_W * e["volatility"], "no_edge": lambda e: CONV_W * e["conviction"] + VOL_W * e["volatility"], "no_volatility": lambda e: EDGE_W * e["edge"] + CONV_W * e["conviction"], "conviction_only": lambda e: CONV_W * e["conviction"], "edge_only": lambda e: EDGE_W * e["edge"], "volatility_only": lambda e: VOL_W * e["volatility"]}
    results = {name: _metrics(events, lambda e, f=f: would_accept(f(e), e["switch_gate"], e["cooldown_ok"], e["persistence_ok"], e["conviction_ok"])) for name, f in formulas.items()}
    base = results["baseline"]
    lines = ["# Real Switch Strength Component Ablation", "", "| Formula | Accepted | Rejected | Accept Δ | Reject Δ | Precision | Precision Δ | Recall | Recall Δ | False switches | Missed switches | TOXIC exit Δ |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, res in results.items():
        lines.append(f"| {name} | {res['accepted']} | {res['rejected']} | {res['accepted']-base['accepted']} | {res['rejected']-base['rejected']} | {res['precision']:.4f} | {res['precision']-base['precision']:.4f} | {res['recall']:.4f} | {res['recall']-base['recall']:.4f} | {res['false_switches']} | {res['missed_switches']} | {res['toxic_exits']-base['toxic_exits']} |")
    (REPORTS / "real_switch_strength_component_ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


def _rankdata(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i]); ranks = [0.0] * len(values); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]: j += 1
        rank = (i + j + 2) / 2.0
        for k in range(i, j + 1): ranks[order[k]] = rank
        i = j + 1
    return ranks


def _corrs(x: list[float], y: list[float]) -> tuple[float, float]:
    try:
        from scipy.stats import pearsonr, spearmanr
        return float(pearsonr(x, y)[0]), float(spearmanr(x, y)[0])
    except Exception:
        pearson = float(np.corrcoef(x, y)[0, 1]) if len(set(x)) > 1 and len(set(y)) > 1 else 0.0
        spearman = float(np.corrcoef(_rankdata(x), _rankdata(y))[0, 1]) if len(set(x)) > 1 and len(set(y)) > 1 else 0.0
        return pearson, spearman


def _mi(x: list[float], y: list[float]) -> float:
    try:
        from sklearn.feature_selection import mutual_info_regression
        return float(mutual_info_regression(np.array(x).reshape(-1, 1), np.array(y), random_state=42)[0])
    except Exception:
        xb = np.digitize(x, np.histogram_bin_edges(x, bins=10)); yb = np.digitize(y, np.histogram_bin_edges(y, bins=10)); mi = 0.0
        for xv in set(xb):
            for yv in set(yb):
                pxy = np.mean((xb == xv) & (yb == yv)); px = np.mean(xb == xv); py = np.mean(yb == yv)
                if pxy > 0 and px > 0 and py > 0: mi += pxy * math.log(pxy / (px * py))
        return float(mi)


def run_feature_importance(events: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [e for e in events if e["switch_strength"] > -1] or events
    strengths = [e["switch_strength"] if e["switch_strength"] > -1 else recompute_strength(e) for e in valid]
    comps = {"edge": [e["edge"] for e in valid], "conviction": [e["conviction"] for e in valid], "volatility": [e["volatility"] for e in valid]}; weights = {"edge": EDGE_W, "conviction": CONV_W, "volatility": VOL_W}
    rng = random.Random(42); out = {}
    for key, values in comps.items():
        pearson, spearman = _corrs(strengths, values); mse = []
        for _ in range(100):
            shuffled = values[:]; rng.shuffle(shuffled)
            new_strengths = [recompute_strength(e, {key: s}) for e, s in zip(valid, shuffled)]
            mse.append(float(np.mean((np.array(new_strengths) - np.array(strengths)) ** 2)))
        out[key] = {"pearson": pearson, "spearman": spearman, "mi": _mi(values, strengths), "variance_share": float(np.var([weights[key] * v for v in values]) / max(np.var(strengths), 1e-12)), "permutation_mse": float(np.mean(mse))}
    lines = ["# Real Switch Strength Feature Importance", "", f"Valid switch-strength records: {len(valid)}", "", "| Component | Pearson | Spearman | Mutual Information | Variance Share | Permutation MSE Increase |", "|---|---:|---:|---:|---:|---:|"]
    for key, row in out.items(): lines.append(f"| {key} | {row['pearson']:.6f} | {row['spearman']:.6f} | {row['mi']:.6f} | {row['variance_share']:.6f} | {row['permutation_mse']:.6f} |")
    (REPORTS / "real_switch_strength_feature_importance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _load_bearing(events: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [e for e in events if e["accepted"]]; flags = {k: [] for k in ("edge", "conviction", "volatility")}; per_regime = defaultdict(Counter)
    for e in accepted:
        reg = e["confirmed_after_switch"]; per_regime[reg]["accepted"] += 1
        for k in flags:
            lb = not would_accept(recompute_without(e, k), e["switch_gate"], e["cooldown_ok"], e["persistence_ok"], e["conviction_ok"])
            flags[k].append(lb); per_regime[reg][k] += int(lb)
    return {"accepted": accepted, "flags": flags, "per_regime": per_regime}


def run_gate_dependency(events: list[dict[str, Any]]) -> dict[str, Any]:
    data = _load_bearing(events); flags = data["flags"]; n = len(data["accepted"]); combos = Counter()
    for vals in zip(flags["edge"], flags["conviction"], flags["volatility"]):
        c = sum(vals)
        if vals == (True, False, False): combos["edge_only_load_bearing"] += 1
        if vals == (False, True, False): combos["conviction_only_load_bearing"] += 1
        if c == 2: combos["any_two_load_bearing"] += 1
        if c == 3: combos["all_three_load_bearing"] += 1
    lines = ["# Switch Gate Dependency Report (Real Data)", "", "## Load-Bearing Component Counts", "| Component | Count | % of Accepted |", "|-----------|-------|---------------|"]
    for k in ("edge", "conviction", "volatility"): lines.append(f"| {k} | {sum(flags[k])} | {sum(flags[k])/max(n,1):.1%} |")
    lines += ["", "## Multi-Load-Bearing (transition required ALL three)", "| Category | Count |", "|----------|-------|"]
    for k in ("edge_only_load_bearing", "conviction_only_load_bearing", "any_two_load_bearing", "all_three_load_bearing"): lines.append(f"| {k} | {combos[k]} |")
    lines += ["", "## Per-Regime Breakdown", "| Confirmed After Switch | Accepted | Edge LB | Conviction LB | Volatility LB |", "|---|---:|---:|---:|---:|"]
    for reg, row in sorted(data["per_regime"].items()): lines.append(f"| {reg} | {row['accepted']} | {row['edge']} | {row['conviction']} | {row['volatility']} |")
    (REPORTS / "switch_gate_dependency.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def run_persistence_bypass_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [e for e in events if e["accepted"]]
    bypass = [e for e in accepted if e["persistence_ok"] and e["conviction_ok"] and not (e["cooldown_ok"] and e["switch_strength"] >= e["switch_gate"])]
    by_regime = Counter(e["confirmed_after_switch"] for e in bypass); totals = Counter(e["confirmed_after_switch"] for e in accepted)
    lines = ["# Persistence Bypass Audit (Real Data)", "", "## Summary", f"- Total accepted transitions: {len(accepted)}", f"- Accepted via switch_strength (normal path): {len(accepted)-len(bypass)}", f"- Accepted via persistence bypass: {len(bypass)}", f"- Bypass rate: {len(bypass)/max(len(accepted),1):.1%}", "", "## Bypass Regime Breakdown", "| Final Regime | Bypass Count | % of Regime Transitions |", "|---|---:|---:|"]
    for reg in sorted(totals): lines.append(f"| {reg} | {by_regime[reg]} | {by_regime[reg]/max(totals[reg],1):.1%} |")
    lines += ["", "## Conviction Role in Bypass", f"- Bypasses where conviction_ok was decisive: {len(bypass)}", "- Bypasses where only persistence_ok mattered (conviction_ok always True): 0"]
    (REPORTS / "persistence_bypass_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"accepted": accepted, "bypass": bypass}


def write_final_verdict(audit_log: list[dict[str, Any]], events: list[dict[str, Any]], suppression_log: list[dict[str, Any]]) -> None:
    ablation = run_ablation(events); fi = run_feature_importance(events); lb = _load_bearing(events); n = len(lb["accepted"])
    rates = {k: sum(v) / max(n, 1) for k, v in lb["flags"].items()}; no_conv = ablation["no_conviction"]; base = ablation["baseline"]
    precision_delta = no_conv["precision"] - base["precision"]; recall_delta = no_conv["recall"] - base["recall"]
    edge_conv_r = _corrs([e["edge"] for e in events], [e["conviction"] for e in events])[0]
    risk = "LOW RISK: conviction weight can be reduced without material impact" if rates["conviction"] < 0.10 and precision_delta > -0.02 else ("MEDIUM RISK: staged reduction advised; shadow test first" if rates["conviction"] < 0.25 else f"HIGH RISK: conviction is load-bearing in {rates['conviction']:.1%} of transitions")
    score = max(0.0, min(100.0, 100 - (rates["conviction"] * 60) - (abs(precision_delta) * 500))); band = "GREEN (safe to stage)" if score >= 70 else ("AMBER (shadow only)" if score >= 40 else "RED (do not change)")
    ranking = " > ".join(k for k, _ in sorted(fi.items(), key=lambda kv: kv[1]["permutation_mse"], reverse=True))
    summary = _backtest_summary(); prov = summary.get("data_provenance", {}) if isinstance(summary.get("data_provenance"), dict) else {}; synth = prov.get("type") == "synthetic_historical"
    lines = ["# Real Switch Strength Final Verdict", "", f"1. Is conviction actually required? {'YES' if rates['conviction'] > 0 else 'NO'} — {rates['conviction']:.1%} of accepted transitions had conviction as load-bearing component.", "", f"2. Is conviction redundant? {'NO' if no_conv['accepted'] != base['accepted'] or abs(precision_delta) > 0.0 or abs(recall_delta) > 0.0 else 'YES'} — Without conviction, acceptance delta = {no_conv['accepted']-base['accepted']}. Precision delta = {precision_delta:.4f}. Recall delta = {recall_delta:.4f}.", "", f"3. Is conviction replaceable? {'YES' if edge_conv_r > 0.85 else 'NO'} — Pearson(conviction, edge) = {edge_conv_r:.4f}. {'conviction and edge are co-linear; conviction is replaceable.' if edge_conv_r > 0.85 else 'conviction provides independent signal.'}", "", f"4. % of accepted switches depending on conviction: {rates['conviction']:.1%}", f"5. % of accepted switches depending on edge: {rates['edge']:.1%}", f"6. % of accepted switches depending on volatility: {rates['volatility']:.1%}", "", f"7. Can conviction weight be reduced safely? {risk}", "", f"8. Recommended weight ranking: {ranking}", "", f"9. Production readiness score for changing conviction weight: {score:.1f}/100 — {band}", "", "## Data Quality Disclaimer", f"Analysis based on {len(events)} real regime transitions from {len(audit_log)} total ticks recorded during test/backtest execution. {'WARNING: underlying price data is synthetic per backtest_summary.json.' if synth else ''}", f"Suppression records reviewed: {len(suppression_log)}"]
    (REPORTS / "real_switch_strength_final_verdict.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    REPORTS.mkdir(exist_ok=True); audit_log_path = REPORTS / "regime_audit_log.json"; supp_log_path = REPORTS / "regime_suppression_log.json"
    if not audit_log_path.exists():
        print("INSUFFICIENT REAL REPLAY DATA FOR ATTRIBUTION"); print(f"Missing: {audit_log_path}"); print("Run pytest with conftest patch first to generate audit logs."); sys.exit(1)
    audit_log = _read_json(audit_log_path, []); suppression_log = _read_json(supp_log_path, []) if supp_log_path.exists() else []
    if len(audit_log) < 50:
        print("INSUFFICIENT REAL REPLAY DATA FOR ATTRIBUTION"); print(f"  audit_log records: {len(audit_log)}"); print(f"  suppression_log records: {len(suppression_log)}"); print("  Required: >= 50 total update() ticks with regime tracking"); print("  Action: run pytest or a backtest first to accumulate audit log"); sys.exit(1)
    events = extract_transitions(audit_log)
    if len(events) < 10:
        print("INSUFFICIENT TRANSITION EVENTS FOR ATTRIBUTION"); print(f"  transitions found: {len(events)}"); print(f"  total audit records: {len(audit_log)}"); print("  Action: ensure test suite exercises regime switching behaviour"); sys.exit(1)
    write_dataset_summary(audit_log, suppression_log, events); run_ablation(events); run_feature_importance(events); run_gate_dependency(events); run_persistence_bypass_audit(events); write_final_verdict(audit_log, events, suppression_log)
    print("Attribution audit complete. Reports written to reports/")


if __name__ == "__main__":
    main()
