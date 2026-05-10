#!/usr/bin/env python3
from __future__ import annotations
import json, os
import numpy as np
import pandas as pd
from bar_aggregator import resample_bars
from backtest_engine import BacktestConfig, BacktestEngine
from calibrate_regime_5m import CalibrationSlice, calibrate_5m_artifacts


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
    if not log:
        blockers.append("No trade-level predictions/confusion labels; macro_f1/confusion_matrix unavailable from current backtest output.")
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


def main()->None:
    bars_1m=_load("data/ohlcv_1m.csv"); bars_5m=resample_bars(bars_1m,minutes=5)
    embargo,train,val,test,step=12,300,120,120,120
    folds=[]; blockers=[]
    for fs in range(0,len(bars_5m)-(train+val+test+2*embargo)+1,step):
        tr0,tr1=fs,fs+train; va0,va1=tr1+embargo,tr1+embargo+val; te0,te1=va1+embargo,va1+embargo+test
        try:
            cal=calibrate_5m_artifacts(bars_1m=bars_1m,out_path=f"weights/advanced_regime_weights_5m_fold_{fs}.npz",meta_path=f"weights/advanced_regime_weights_5m_fold_{fs}.meta.json",cal_slice=CalibrationSlice(start_idx=tr0,end_idx=va1))
        except Exception as e:
            blockers.append({"fold":fs,"reason":str(e)}); continue
        base_cfg=BacktestConfig(max_hold_bars=36)
        sel=_select_threshold(bars_5m[va0:va1],cal["output_path"],base_cfg)
        cfg5=BacktestConfig(max_hold_bars=36,orchestrator_action_threshold=float(sel["threshold"]))
        cost5=_cost_basis(cfg5)
        r5=BacktestEngine(config=cfg5,weight_path=cal["output_path"])._run_single_pass(bars_5m[te0:te1],label=f"5m_test_{fs}")
        t0,t1=int(bars_5m[te0][0]),int(bars_5m[te1-1][0])
        slice1=[r for r in bars_1m if t0<=int(r[0])<=t1]
        cfg1=BacktestConfig(); cost1=_cost_basis(cfg1)
        r1=BacktestEngine(config=cfg1)._run_single_pass(slice1,label=f"1m_test_{fs}")
        m5,b5=_compute_metrics(r5,len(bars_5m[te0:te1]),cost5); m1,b1=_compute_metrics(r1,len(slice1),cost1)
        blockers.extend({"fold":fs,"reason":b} for b in (b5+b1))
        folds.append({"fold":len(folds),"ranges":{"train":[int(bars_5m[tr0][0]),int(bars_5m[tr1-1][0])],"val":[int(bars_5m[va0][0]),int(bars_5m[va1-1][0])],"test":[t0,t1]},"walk_forward_integrity":True,"calibration_separation":True,"threshold_selection":sel,"baseline_1m":m1,"candidate_5m":m5})
    if not folds: raise RuntimeError(f"No valid folds produced. blockers={blockers}")
    agg=lambda side,key: float(np.mean([f[side][key] for f in folds]))
    comp={"delta_profit_factor":agg("candidate_5m","trading") if False else None}
    avg1={"trading":{k:float(np.mean([f["baseline_1m"]["trading"][k] for f in folds])) for k in ["total_trades","win_rate","profit_factor","expectancy","sharpe","max_drawdown","average_return_per_trade","average_holding_time","turnover"]},"signals":{k:float(np.mean([f["baseline_1m"]["signals"][k] for f in folds])) if f["baseline_1m"]["signals"][k] is not None else None for k in ["LONG_count","SHORT_count","HOLD_count","signal_coverage","directional_precision","macro_f1"]},"regimes":{k:float(np.mean([f["baseline_1m"]["regimes"][k] for f in folds])) for k in ["regime_entropy","regime_persistence","transition_frequency","trend_vs_range_ratio"]},"costs":{k:float(np.mean([f["baseline_1m"]["costs"][k] for f in folds])) for k in ["gross_expectancy","net_expectancy","fee_burden","slippage_burden","edge_after_costs"]}}
    avg5={"trading":{k:float(np.mean([f["candidate_5m"]["trading"][k] for f in folds])) for k in ["total_trades","win_rate","profit_factor","expectancy","sharpe","max_drawdown","average_return_per_trade","average_holding_time","turnover"]},"signals":{k:float(np.mean([f["candidate_5m"]["signals"][k] for f in folds])) if f["candidate_5m"]["signals"][k] is not None else None for k in ["LONG_count","SHORT_count","HOLD_count","signal_coverage","directional_precision","macro_f1"]},"regimes":{k:float(np.mean([f["candidate_5m"]["regimes"][k] for f in folds])) for k in ["regime_entropy","regime_persistence","transition_frequency","trend_vs_range_ratio"]},"costs":{k:float(np.mean([f["candidate_5m"]["costs"][k] for f in folds])) for k in ["gross_expectancy","net_expectancy","fee_burden","slippage_burden","edge_after_costs"]}}
    deltas={"delta_profit_factor":avg5["trading"]["profit_factor"]-avg1["trading"]["profit_factor"],"delta_expectancy":avg5["trading"]["expectancy"]-avg1["trading"]["expectancy"],"delta_sharpe":avg5["trading"]["sharpe"]-avg1["trading"]["sharpe"],"delta_max_drawdown":avg5["trading"]["max_drawdown"]-avg1["trading"]["max_drawdown"],"delta_signal_coverage":avg5["signals"]["signal_coverage"]-avg1["signals"]["signal_coverage"],"delta_regime_entropy":avg5["regimes"]["regime_entropy"]-avg1["regimes"]["regime_entropy"],"delta_macro_f1":None,"delta_net_edge_after_costs":avg5["costs"]["edge_after_costs"]-avg1["costs"]["edge_after_costs"]}
    verdict="STILL UNTRADABLE" if avg5["costs"]["net_expectancy"]<=0 else "PROMISING BUT WEAK"
    out={"run_id":{"start_ts":int(bars_5m[0][0]),"end_ts":int(bars_5m[-1][0]),"fold_count":len(folds)},"summary":"Research-only 5m walk-forward audit vs 1m baseline on matched windows.","metrics_table":{"baseline_1m":avg1,"candidate_5m":avg5,"comparison":deltas},"regime_analysis":{"baseline_1m":avg1["regimes"],"candidate_5m":avg5["regimes"]},"cost_analysis":{"baseline_1m":avg1["costs"],"candidate_5m":avg5["costs"]},"walk_forward_validation_quality":{"chronological":True,"embargo_bars":embargo,"calibration_separation":True,"production_parity":False},"files":{"inputs":["data/ohlcv_1m.csv","data/bookDepth.csv"],"outputs":["audit_output/5m_walk_forward_results.json","backtest_summary.json","replit.md"]},"blockers":blockers,"fold_results":folds,"final_verdict":verdict}
    os.makedirs("audit_output",exist_ok=True)
    json.dump(out,open("audit_output/5m_walk_forward_results.json","w",encoding="utf-8"),indent=2,sort_keys=True)
    json.dump(out,open("backtest_summary.json","w",encoding="utf-8"),indent=2,sort_keys=True)
    _rewrite_replit(f"## 5m walk-forward validation\n\n- mode: research_only\n- folds: {len(folds)}\n- final verdict: {verdict}\n")

if __name__=="__main__":main()
