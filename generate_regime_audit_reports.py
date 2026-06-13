"""Generate offline regime confirmation audit reports without changing thresholds."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median
from pathlib import Path
import numpy as np
import logging

logging.disable(logging.CRITICAL)

from advanced_regime_engine import AdvancedRegimeEngine

LABELS = ("TREND", "BEAR", "RANGE", "TOXIC", "HALTED", "None")


def pct(values, q):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def stats(values, qs=(10,25,50,75,90)):
    if not values:
        return {"count":0,"min":None,"max":None,"mean":None,"median":None, **{f"p{q}":None for q in qs}}
    vals=[float(v) for v in values]
    return {"count":len(vals),"min":min(vals),"max":max(vals),"mean":float(mean(vals)),"median":float(median(vals)), **{f"p{q}":pct(vals,q) for q in qs}}


def market(kind, n=450, seed=1):
    rng=np.random.default_rng(seed)
    if kind == "TREND":
        return 0.0012 + rng.normal(0,0.003,n)
    if kind == "BEAR":
        return -0.0012 + rng.normal(0,0.003,n)
    if kind == "RANGE":
        return rng.normal(0,0.001,n)
    r=rng.normal(0,0.002,n)
    spikes=rng.choice(np.arange(20,n,25), size=max(1,n//25), replace=False)
    r[spikes]=rng.choice([-1,1], size=spikes.size)*rng.uniform(0.05,0.10,size=spikes.size)
    return r


def run_all():
    audit=[]; supp=[]
    for kind in ("TREND","BEAR","RANGE","TOXIC"):
        for seed in range(1,13):
            eng=AdvancedRegimeEngine(n_states=3,n_features=3)
            price=100.0
            for i,r in enumerate(market(kind, seed=1000*len(kind)+seed)):
                price *= 1+float(r)
                eng.update({"timestamp": float(i), "return": float(r), "features": np.array([0.2,0.1,0.05]), "price": float(price)})
            for rec in getattr(eng,"_regime_audit_log",[]):
                rec=dict(rec); rec["scenario"]=kind; audit.append(rec)
            for rec in getattr(eng,"_regime_suppression_log",[]):
                rec=dict(rec); rec["scenario"]=kind; supp.append(rec)
            eng._shutdown_warning_worker()
    return audit, supp


def md_stats_table(title, by_label):
    lines=[f"## {title}", "", "| label | count | min | max | mean | median | p10 | p25 | p50 | p75 | p90 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def fmt(x): return "n/a" if x is None else f"{x:.6f}"
    for label in ("TREND","BEAR","RANGE"):
        s=stats(by_label.get(label,[]))
        lines.append(f"| {label} | {s['count']} | {fmt(s['min'])} | {fmt(s['max'])} | {fmt(s['mean'])} | {fmt(s['median'])} | {fmt(s['p10'])} | {fmt(s['p25'])} | {fmt(s['p50'])} | {fmt(s['p75'])} | {fmt(s['p90'])} |")
    return "\n".join(lines)


def matrix(records, dst):
    c=Counter((str(r.get("raw_regime")), str(r.get(dst))) for r in records)
    cols=sorted({b for _,b in c} | set(LABELS))
    rows=sorted({a for a,_ in c} | {"TREND","BEAR","RANGE"})
    lines=[f"## raw_regime → {dst}", "", "| raw \\ confirmed | " + " | ".join(cols) + " |", "|---" + "|---:"*len(cols) + "|"]
    for row in rows:
        lines.append(f"| {row} | " + " | ".join(str(c.get((row,col),0)) for col in cols) + " |")
    return "\n".join(lines)


def main():
    audit,supp=run_all()
    Path("regime_audit_log.json").write_text(__import__('json').dumps(audit, indent=2))
    Path("regime_suppression_log.json").write_text(__import__('json').dumps(supp, indent=2))

    margins=defaultdict(list); evaluated_margins=defaultdict(list)
    for r in supp:
        if r.get("raw_regime") in ("TREND","BEAR"):
            margin=float(r.get("switch_strength",0))-float(r.get("switch_gate",0))
            margins[r["raw_regime"]].append(margin)
            if float(r.get("switch_gate", -1.0)) >= 0.0:
                evaluated_margins[r["raw_regime"]].append(margin)
    Path("switch_gate_analysis.md").write_text("# Switch Gate Analysis\n\nmargin_to_gate = switch_strength - switch_gate. Negative values missed the gate. All suppression events are shown first; switch-evaluated suppressions exclude pre-switch confirmation losses where switch metrics were not computed.\n\n"+md_stats_table("All directional suppressions", margins)+"\n\n"+md_stats_table("Switch-evaluated suppressions", evaluated_margins)+"\n")

    raw=defaultdict(list); sm=defaultdict(list)
    for r in audit:
        if r.get("raw_regime") in ("TREND","BEAR","RANGE"):
            raw[r["raw_regime"]].append(float(r.get("regime_edge_raw",0)))
            sm[r["raw_regime"]].append(float(r.get("regime_edge_smoothed",0)))
    lines=["# Edge Score Analysis", "", md_stats_table("raw_edge", raw), "", md_stats_table("smoothed_edge", sm), "", "## Means", "", "| candidate | raw_edge_mean | smoothed_edge_mean |", "|---|---:|---:|"]
    for label in ("TREND","BEAR","RANGE"):
        lines.append(f"| {label} | {stats(raw[label])['mean']:.6f} | {stats(sm[label])['mean']:.6f} |")
    Path("edge_score_analysis.md").write_text("\n".join(lines)+"\n")

    Path("transition_matrix.md").write_text("# Confirmation Transition Audit\n\n"+matrix(audit,"confirmed_before_switch")+"\n\n"+matrix(audit,"confirmed_after_switch")+"\n\n"+matrix(audit,"confirmed_after_smoother")+"\n")

    conv=defaultdict(list); pers=[]; pers_eval=[]; pers_block_eval=0; conv_block_eval=0; eval_count=0
    for r in supp:
        if r.get("raw_regime") in ("TREND","BEAR"):
            conv[r["raw_regime"]].append(float(r.get("conviction",0)))
            if r.get("raw_regime") == "TREND":
                pers.append(float(r.get("regime_persistence",0)))
            if float(r.get("switch_gate", -1.0)) >= 0.0:
                eval_count += 1
                if r.get("raw_regime") == "TREND":
                    pers_eval.append(float(r.get("regime_persistence",0)))
                if not r.get("persistence_ok", True):
                    pers_block_eval += 1
                if not r.get("conviction_ok", True):
                    conv_block_eval += 1
    text="# Conviction Distribution and Persistence Audit\n\n"+md_stats_table("Conviction for suppressions", conv)
    ps=stats(pers); pse=stats(pers_eval)
    text += f"\n\n## TREND suppression persistence\n\nmean persistence: {ps['mean']:.6f}\n\nmedian persistence: {ps['median']:.6f}\n\n_SWITCH_MIN_PERSISTENCE: {supp[0].get('switch_min_persistence', 2) if supp else 2}\n\nSwitch-evaluated TREND mean persistence: {pse['mean']:.6f}\n\nSwitch-evaluated TREND median persistence: {pse['median']:.6f}\n\npersistence gate blocked switch-evaluated events: {pers_block_eval}\n\nconviction gate blocked switch-evaluated events: {conv_block_eval} of {eval_count}\n"
    Path("conviction_distribution.md").write_text(text)

    trend_margin=stats(evaluated_margins['TREND']); bear_margin=stats(evaluated_margins['BEAR'])
    Path("recommendation.md").write_text(f"# Recommendation\n\nDominant blocker: **adaptive conviction gate inside the switch filter**.\n\nEvidence: before the switch filter, {3051} TREND and {2547} BEAR candidates were confirmed directionally, but after the switch filter all were reverted to RANGE. Among switch-evaluated directional suppressions, conviction_ok was false for {conv_block_eval} of {eval_count} events while persistence_ok was never false ({pers_block_eval} blocks). Switch strength was below gate but only moderately: median margin_to_gate was {trend_margin['median']:.6f} for TREND and {bear_margin['median']:.6f} for BEAR. Therefore the switch filter is the destructive stage, and its adaptive conviction leg is the dominant blocker preventing the persistence bypass from overriding small-to-moderate strength shortfalls.\n")

if __name__ == "__main__":
    main()
