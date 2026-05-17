#!/usr/bin/env python3
from __future__ import annotations
import json, os
import math
import glob
import numpy as np
import pandas as pd
from typing import Optional
from bar_aggregator import resample_bars
from backtest_engine import BacktestConfig, BacktestEngine
from calibrate_regime_5m import CalibrationSlice, calibrate_5m_artifacts
from data_tools.l2_to_backtest import load_l2_csv


BASELINE_INVARIANTS = {
    "output_keys": ["action","confidence","state","regime","ofi_zscore",
                    "hawkes_intensity","logic","micro_prob","macro_prob",
                    "prob_above","prob_below"],
    "hold_on_missing_ofi": True,
    "hold_on_l1_only_book": True,
    "hold_on_no_pools": True,
    "hold_on_invalid_price": True,
    "hold_on_volatile_regime": True,
    "deterministic_across_runs": True,
    "no_nan_propagation": True,
    "no_inf_propagation": True,
    "prob_above_plus_prob_below_eq_1": True,
    "confidence_in_0_1": True,
    "ofi_count_sync": True,
    "orchestrator_compatible": True,
    "state_invalid_count_not_regression": True,
    "l2_partial_alpha_conf_mean": 0.29535,
    "l2_partial_alpha_dir_neutral_pct": 1.0,
    "ohlcv_synthetic_alpha_conf_mean": 0.32582,
    "ohlcv_synthetic_alpha_dir_neutral_pct": 1.0,
    "enable_sweep_directional_fallback_default": False,
    "fail_closed_on_uncertainty": True,
}


def _classify_ofi_capability(book_snapshots, required_levels=10):
    if not book_snapshots:
        return {"ofi_capable": False, "max_levels_available": 0, "classification": "NO_BOOK_DATA", "production_valid": False}
    # BookSnapshot path is L1-only by schema.
    effective_levels = 1
    if effective_levels < required_levels:
        return {
            "ofi_capable": False,
            "max_levels_available": effective_levels,
            "classification": "L1_ONLY_REPLAY" if effective_levels <= 1 else f"PARTIAL_L2_REPLAY_{effective_levels}_LEVELS",
            "production_valid": False,
            "warning": (
                f"NON-PRODUCTION MICROSTRUCTURE VALIDATION: Only {effective_levels} book level(s) available. "
                f"OFI requires {required_levels} levels. ofi_zscore will be ≈ 0.0. "
                "LSA directional output unreliable."
            ),
        }
    return {"ofi_capable": True, "max_levels_available": effective_levels, "classification": "REAL_L2_REPLAY", "production_valid": True}


def _diagnose_neutral_reason(lsa):
    if lsa is None or not hasattr(lsa, "get_state_metrics"):
        return "LSA not initialized"
    metrics = lsa.get_state_metrics() or {}
    reasons = []
    if metrics.get("last_ofi_levels_used", 10) < 5:
        reasons.append(f"ofi_levels_used={metrics.get('last_ofi_levels_used', 0)} (insufficient depth for meaningful OFI signal)")
    if metrics.get("neutral_predict_count", 0) > 0:
        reasons.append(f"neutral_predict_count={metrics['neutral_predict_count']} (cold-start or pool geometry insufficient)")
    if metrics.get("volatile_gate_count", 0) > 0:
        reasons.append(f"volatile_gate_count={metrics['volatile_gate_count']} (VOLATILE regime gate fired)")
    return " | ".join(reasons) if reasons else "unknown — check hawkes and pool state"


def _load(path:str)->list[list]:
    d=pd.read_csv(path)
    return d[["timestamp","open","high","low","close","volume"]].values.tolist()


def _cost_basis(cfg: BacktestConfig) -> dict:
    # Mirrors BacktestEngine close-trade model: net = gross - 2*fee - 1*slippage.
    return {"fee_bps":float(cfg.fee_bps),"slippage_bps":float(cfg.slippage_bps),"round_trip_cost_bps":float(2.0*cfg.fee_bps+cfg.slippage_bps),"formula":"net_bps=gross_bps-2*fee_bps-slippage_bps"}


def _compute_metrics(result:dict, bars:int, cost:dict)->tuple[dict,list[str]]:
    blockers=[]
    log=result.get("trade_log",[]) or []
    pnls=np.asarray([float(t.get("pnl",0.0)) for t in log],dtype=float)
    pnl_pct=np.asarray([float(t.get("pnl_pct",0.0))/100.0 for t in log],dtype=float)
    holds=np.asarray([int(t.get("exit_index",0))-int(t.get("entry_index",0)) for t in log],dtype=float)
    long_n=sum(1 for t in log if str(t.get("side","")).upper()=="LONG")
    short_n=sum(1 for t in log if str(t.get("side","")).upper()=="SHORT")
    hold_n=max(0,bars-long_n-short_n)
    sig_cov=float((long_n+short_n)/max(1,bars))
    conf=np.asarray([float((t.get("signal") or {}).get("confidence",0.0)) for t in log],dtype=float)
    regimes=[str((t.get("signal") or {}).get("regime","UNKNOWN")) for t in log]
    rc={k:regimes.count(k) for k in sorted(set(regimes))}
    probs=np.asarray([v/max(1,sum(rc.values())) for v in rc.values()],dtype=float)
    entropy=float(-np.sum(probs*np.log(probs+1e-12))) if probs.size else 0.0
    transitions=sum(1 for i in range(1,len(regimes)) if regimes[i]!=regimes[i-1])
    persistence=float(1.0-transitions/max(1,len(regimes)-1)) if regimes else 0.0
    trend=(rc.get("TREND",0)+rc.get("BULL",0)); range_n=rc.get("RANGE",0)
    wins=pnls[pnls>0]; losses=np.abs(pnls[pnls<=0])
    gross_exp=float(np.mean(pnl_pct))*1e4 if pnl_pct.size else 0.0
    net_exp=gross_exp-cost["round_trip_cost_bps"]
    m={
      "trading":{"total_trades":int(result.get("total_trades",0)),"win_rate":float(result.get("win_rate",0.0)),"profit_factor":float(wins.sum()/max(losses.sum(),1e-12)) if wins.size else 0.0,"expectancy":float(result.get("expectancy",0.0)),"sharpe":float(result.get("sharpe",0.0)),"max_drawdown":float(result.get("max_drawdown",0.0),),"average_return_per_trade":float(np.mean(pnls)) if pnls.size else 0.0,"average_holding_time":float(np.mean(holds)) if holds.size else 0.0,"turnover":float(len(log)/max(1,bars)),"exposure":None},
      "signals":{"LONG_count":long_n,"SHORT_count":short_n,"HOLD_count":hold_n,"signal_coverage":sig_cov,"directional_precision":float((pnls>0).mean()) if pnls.size else 0.0,"macro_f1":None,"confusion_matrix":None},
      "regimes":{"regime_entropy":entropy,"regime_persistence":persistence,"transition_frequency":float(transitions/max(1,len(regimes))),"trend_vs_range_ratio":float(trend/max(1,range_n)),"confidence_distribution":{"mean":float(conf.mean()) if conf.size else 0.0,"p50":float(np.quantile(conf,0.5)) if conf.size else 0.0,"p90":float(np.quantile(conf,0.9)) if conf.size else 0.0}},
      "costs":{"gross_expectancy":gross_exp,"net_expectancy":net_exp,"fee_burden":float(2.0*cost['fee_bps']),"slippage_burden":float(cost['slippage_bps']),"edge_after_costs":net_exp}
    }
    return m, blockers


def _select_threshold(val_bars:list[list], weight_path:str, cfg_template:BacktestConfig)->dict:
    cands=[0.50,0.55,0.60,0.65]
    cost=_cost_basis(cfg_template)
    scored=[]
    for th in cands:
        cfg=BacktestConfig(orchestrator_action_threshold=th,max_hold_bars=cfg_template.max_hold_bars,fee_bps=cfg_template.fee_bps,slippage_bps=cfg_template.slippage_bps)
        r=BacktestEngine(config=cfg,weight_path=weight_path)._run_single_pass(val_bars,label=f"5m_val_{th}")
        m,_=_compute_metrics(r,len(val_bars),cost)
        score=float(m["costs"]["edge_after_costs"])
        scored.append({"threshold":th,"validation_fold_score":score,"selection_metric":"edge_after_costs_bps"})
    scored=sorted(scored,key=lambda x:(x["validation_fold_score"],x["threshold"]),reverse=True)
    best=scored[0]
    best.update({"threshold_selection_mode":"research_only","selection_cost_basis":cost,"production_parity":False,"note":"Research-only threshold; do not copy to production without separate approval."})
    return best


def _rewrite_replit(report:str)->None:
    prev=""
    if os.path.exists("replit.md"):
        txt=open("replit.md","r",encoding="utf-8").read()
        prev=txt.split("## 5m walk-forward validation")[0].rstrip()+"\n\n"
    with open("replit.md","w",encoding="utf-8") as f:f.write(prev+report)


def main() -> None:
    json.dump(BASELINE_INVARIANTS, open("baseline_snapshot.json", "w"), indent=2)
    bars_1m = _load("data/ohlcv_1m.csv")
    bars_5m_raw = _load("data/ohlcv_5m.csv")
    bars_5m_resampled = resample_bars(bars_1m, minutes=5)
    bars_5m = bars_5m_raw if len(bars_5m_raw) >= len(bars_5m_resampled) else bars_5m_resampled
    print(f"[5m audit] 1m bars: {len(bars_1m)}  5m bars: {len(bars_5m)}")

    cal_result = None
    cal_blocker = None
    try:
        cal_result = calibrate_5m_artifacts(
            bars_1m=bars_1m,
            out_path="weights/advanced_regime_weights_5m.npz",
            meta_path="weights/advanced_regime_weights_5m.meta.json",
        )
        print(f"[5m audit] calibration OK: n_bars_used={cal_result['n_bars_used']}")
    except Exception as e:
        cal_blocker = str(e)
        print(f"[5m audit] BLOCKER — calibration failed: {cal_blocker}")
        print("  Continuing with default weights for comparison only.")

    pref_5m = "weights/advanced_regime_weights_5m.npz"
    if cal_result and os.path.exists(pref_5m):
        weight_path = pref_5m
    else:
        weight_path = "weights/advanced_regime_weights.npz"
        if not cal_result:
            print("[5m audit] calibration failed; explicit fallback to 1m default weights.")

    base_cfg = BacktestConfig(fee_bps=8.0, slippage_bps=3.0, max_hold_bars=12, orchestrator_action_threshold=0.60)
    cost = _cost_basis(base_cfg)
    try:
        l2_snaps = load_l2_csv("data/bookTicker_dec2023_30s.csv")
    except Exception:
        l2_snaps = []
    ofi_capability = _classify_ofi_capability(l2_snaps, required_levels=10)

    engine_A = BacktestEngine(config=base_cfg, weight_path=weight_path)
    result_A = engine_A._run_single_pass(bars_5m, label="5m_fixed_horizon")
    metrics_A, blockers_A = _compute_metrics(result_A, len(bars_5m), cost)

    regime_cfg = BacktestConfig(
        fee_bps=8.0, slippage_bps=3.0, max_hold_bars=12, orchestrator_action_threshold=0.60,
        regime_hold_horizon_bars={"CHOPPY": 4, "COMPRESSION": 4, "RANGING": 6, "RANGE": 6, "TREND": 20, "BULL": 20, "BEAR": 8, "TOXIC": 2},
    )
    engine_B = BacktestEngine(config=regime_cfg, weight_path=weight_path)
    result_B = engine_B._run_single_pass(bars_5m, label="5m_regime_horizon")
    cost_B = _cost_basis(regime_cfg)
    metrics_B, blockers_B = _compute_metrics(result_B, len(bars_5m), cost_B)

    cfg_1m = BacktestConfig(fee_bps=8.0, slippage_bps=3.0, max_hold_bars=12, orchestrator_action_threshold=0.60)
    cost_1m = _cost_basis(cfg_1m)
    engine_C = BacktestEngine(config=cfg_1m)
    result_C = engine_C._run_single_pass(bars_1m, label="1m_baseline")
    metrics_C, blockers_C = _compute_metrics(result_C, len(bars_1m), cost_1m)

    engine_D = BacktestEngine(config=base_cfg, weight_path=weight_path)
    result_D_multi = engine_D.run_backtest_multi_resolution(bars_1m[:3000])
    result_D = result_D_multi.get("5m", {})
    metrics_D, blockers_D = _compute_metrics(result_D, result_D.get("bars", 0), cost)

    all_blockers = []
    if cal_blocker:
        all_blockers.append({"pass": "calibration", "reason": cal_blocker})
    for b in (blockers_A + blockers_B + blockers_C + blockers_D):
        all_blockers.append(b)

    unavailable_metrics = [
        {
            "metric": "macro_f1",
            "reason": (
                "backtest engine does not expose per-bar predicted vs actual "
                "regime labels; per-trade signal dict contains 'regime' string "
                "but no ground-truth label"
            ),
            "impact": "classification accuracy unquantifiable from this harness",
        },
        {
            "metric": "confusion_matrix",
            "reason": "same as macro_f1",
            "impact": "none on trading metrics",
        },
    ]

    def _verdict(m: dict) -> str:
        net = m.get("costs", {}).get("net_expectancy", None)
        if net is None:
            return "INCONCLUSIVE"
        return "UNTRADABLE" if net <= 0 else "PROMISING"

    def _prior_field(doc: dict, field: str):
        try:
            return doc["passes"]["A_5m_fixed_horizon"]["metrics"]["trading"][field]
        except Exception:
            try:
                return doc["required_output_fields"][field]
            except Exception:
                return None
    def _prior_delta(current_val, field: str) -> Optional[float]:
        try:
            prior_val = _prior_field(prior, field)
            if prior_val is None:
                return None
            a, b = float(current_val), float(prior_val)
            return round(a - b, 6) if (math.isfinite(a) and math.isfinite(b)) else None
        except Exception:
            return None
    prior_docs = []
    for p in ["audit_output/post_run_comparison.json", "backtest_summary.json"] + glob.glob("calibration_report*.json") + glob.glob("audit_output/calibration_report*.json"):
        if os.path.exists(p):
            try:
                prior_docs.append(json.load(open(p)))
            except Exception:
                pass
    prior = prior_docs[-1] if prior_docs else {}
    output = {
        "run_timestamp": _utc_now(),
        "cost_basis": cost,
        "calibration": {"status": "ok" if cal_result else "failed", "n_bars_used": cal_result["n_bars_used"] if cal_result else None, "output_path": weight_path, "blocker": cal_blocker},
        "passes": {
            "A_5m_fixed_horizon": {"metrics": metrics_A, "verdict": _verdict(metrics_A)},
            "B_5m_regime_conditioned": {"metrics": metrics_B, "verdict": _verdict(metrics_B)},
            "C_1m_baseline": {"metrics": metrics_C, "verdict": _verdict(metrics_C)},
            "D_5m_multi_resolution": {"metrics": metrics_D, "raw": result_D_multi},
        },
        "comparison": {
            "fixed_vs_regime_conditioned": {
                "delta_profit_factor": _safe_delta(metrics_B, metrics_A, "trading", "profit_factor"),
                "delta_net_expectancy": _safe_delta(metrics_B, metrics_A, "costs", "net_expectancy"),
                "delta_max_drawdown": _safe_delta(metrics_B, metrics_A, "trading", "max_drawdown"),
                "delta_win_rate": _safe_delta(metrics_B, metrics_A, "trading", "win_rate"),
            },
            "5m_fixed_vs_1m_baseline": {
                "delta_profit_factor": _safe_delta(metrics_A, metrics_C, "trading", "profit_factor"),
                "delta_net_expectancy": _safe_delta(metrics_A, metrics_C, "costs", "net_expectancy"),
            },
        },
        "required_output_fields": {
            "LONG_count": metrics_A.get("signals", {}).get("LONG_count"),
            "SHORT_count": metrics_A.get("signals", {}).get("SHORT_count"),
            "HOLD_count": metrics_A.get("signals", {}).get("HOLD_count"),
            "signal_coverage": metrics_A.get("signals", {}).get("signal_coverage"),
            "win_rate": metrics_A.get("trading", {}).get("win_rate"),
            "profit_factor": metrics_A.get("trading", {}).get("profit_factor"),
            "expectancy": metrics_A.get("trading", {}).get("expectancy"),
            "sharpe": metrics_A.get("trading", {}).get("sharpe"),
            "max_drawdown": metrics_A.get("trading", {}).get("max_drawdown"),
            "average_return": metrics_A.get("trading", {}).get("average_return_per_trade"),
            "average_holding_time": metrics_A.get("trading", {}).get("average_holding_time"),
            "regime_distribution": metrics_A.get("regimes"),
            "regime_entropy": metrics_A.get("regimes", {}).get("regime_entropy"),
            "regime_persistence": metrics_A.get("regimes", {}).get("regime_persistence"),
            "cost_assumptions": cost,
            "forward_horizon_used": base_cfg.max_hold_bars,
        },
        "blockers": all_blockers,
        "unavailable_metrics": unavailable_metrics,
        "ofi_capability": ofi_capability,
        "lsa_validity": "NOT_VALID_OFI_INSUFFICIENT" if not ofi_capability.get("ofi_capable", False) else "VALID",
        "lsa_final_diagnostics": engine_A.lsa.get_state_metrics() if getattr(engine_A, "lsa", None) and hasattr(engine_A.lsa, "get_state_metrics") else {},
        "lsa_neutral_reason": _diagnose_neutral_reason(getattr(engine_A, "lsa", None)),
        "validation_integrity": {
            "walk_forward_implemented": False,
            "embargo_implemented": False,
            "train_test_split": "NONE",
            "calibration_window": "Dec-2023",
            "test_window": "Dec-2023",
            "in_sample_contamination_risk": "HIGH",
            "production_validity": "NOT_VALID — in-sample result only",
            "required_before_paper_trading": [
                "Fetch Dec-2023 20-level L2 depth",
                "Calibrate Hawkes parameters on Nov-2023",
                "Implement walk-forward: calibrate Nov-2023, test Dec-2023",
                "Add embargo to pool seeding",
            ],
        },
    }
    output["prior_run_comparison"] = {
        "total_trades": _prior_delta(metrics_A["trading"]["total_trades"], "total_trades"),
        "win_rate": _prior_delta(metrics_A["trading"]["win_rate"], "win_rate"),
        "profit_factor": _prior_delta(metrics_A["trading"]["profit_factor"], "profit_factor"),
        "max_drawdown": _prior_delta(metrics_A["trading"]["max_drawdown"], "max_drawdown"),
        "sharpe": _prior_delta(metrics_A["trading"]["sharpe"], "sharpe"),
        "net_expectancy": _prior_delta(metrics_A["costs"]["net_expectancy"], "net_expectancy"),
    }
    partial = (
        result_A.get("total_trades", 0) == 0
        or result_B.get("total_trades", 0) == 0
        or result_D.get("total_trades", 0) == 0
    )
    if cal_blocker:
        output["run_status"] = "BLOCKED"
    elif partial:
        output["run_status"] = "PARTIAL"
    elif result_A.get("total_trades",0) > 0:
        output["run_status"] = "OK"
    else:
        output["run_status"] = "PARTIAL"
    os.makedirs("audit_output", exist_ok=True)
    json.dump(output, open("audit_output/5m_walk_forward_results.json", "w"), indent=2)
    json.dump(output, open("backtest_summary.json", "w"), indent=2)
    _rewrite_replit(_build_markdown_report(output))
    print("[5m audit] Done. See backtest_summary.json and replit.md")


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _safe_delta(m_new: dict, m_base: dict, section: str, field: str) -> Optional[float]:
    try:
        a = float(m_new[section][field])
        b = float(m_base[section][field])
        if math.isfinite(a) and math.isfinite(b):
            return round(a - b, 6)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _build_markdown_report(output: dict) -> str:
    lines = ["## 5m architecture audit — four-pass comparison", ""]
    lines.append(f"Run: {output.get('run_timestamp', 'unknown')}")
    lines.append("")
    cal = output.get("calibration", {})
    lines.append(f"**Calibration:** {cal.get('status')}  n_bars_used={cal.get('n_bars_used')}  weight_path={cal.get('output_path')}")
    if cal.get("blocker"):
        lines.append(f"> BLOCKER: {cal['blocker']}")
    lines.append("")
    for pass_id, data in output.get("passes", {}).items():
        v = data.get("verdict", "?")
        m = data.get("metrics", {})
        tr = m.get("trading", {})
        lines.append(f"### {pass_id} — {v}")
        lines.append(f"- trades: {tr.get('total_trades')}  win_rate: {tr.get('win_rate')}  PF: {tr.get('profit_factor')}  maxDD: {tr.get('max_drawdown')}")
        lines.append("")
    comp = output.get("comparison", {}).get("fixed_vs_regime_conditioned", {})
    lines.append("### regime-conditioned vs fixed delta")
    for k, v in comp.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    blockers = output.get("blockers", [])
    if blockers:
        lines.append(f"**Blockers ({len(blockers)}):**")
        for b in blockers:
            lines.append(f"- {b}")
    unacc = output.get("unavailable_metrics", [])
    if unacc:
        lines.append(f"**Unavailable metrics ({len(unacc)}) — not blockers:**")
        for u in unacc:
            lines.append(f"- `{u['metric']}`: {u['reason']}")
        lines.append("")
    return "\n".join(lines) + "\n"

if __name__=="__main__":main()
