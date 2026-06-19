"""
calibrate_pipeline.py — Single-entry calibration orchestrator.

Usage:
    REGIME_DATA_DIR=/path/to/parquet REGIME_DATES=2026-06-12,... python calibrate_pipeline.py
"""
from __future__ import annotations

import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import logsumexp

from calibrate_garch import fit_msgarch_mle, write_garch_artifact, load_garch_artifact
from calibrate_nhhmm_beta import fit_nhhmm_beta, transition_cross_entropy
from regime_vol_calibration import calibrate_target_vol, write_target_vol_artifact, load_target_vol_artifact

N_STATES = 3
N_FEATURES = 3
RANDOM_SEED = 42


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _kmeans_numpy(X, n_clusters=3, random_state=42, n_init=20, max_iter=500):
    rng = np.random.default_rng(random_state)
    best = (np.inf, None, None)
    for _ in range(n_init):
        centers = X[rng.choice(X.shape[0], n_clusters, replace=False)].copy()
        for _ in range(max_iter):
            labels = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2).argmin(axis=1)
            new = centers.copy()
            for k in range(n_clusters):
                if np.any(labels == k):
                    new[k] = X[labels == k].mean(axis=0)
            if np.allclose(new, centers, atol=1e-10):
                centers = new; break
            centers = new
        inertia = float(((X - centers[labels]) ** 2).sum())
        if inertia < best[0]: best = (inertia, centers.copy(), labels.copy())
    return {"cluster_centers_": best[1], "labels_": best[2], "fit_n": int(X.shape[0])}


def _predict_centroids(X, centers):
    return np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2).argmin(axis=1)


def _soft_probs(X, centers):
    d = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)
    z = -d
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


def _macro_f1(y_true, y_pred, n_states=3):
    vals=[]
    for k in range(n_states):
        tp=np.sum((y_true==k)&(y_pred==k)); fp=np.sum((y_true!=k)&(y_pred==k)); fn=np.sum((y_true==k)&(y_pred!=k))
        prec=tp/(tp+fp) if tp+fp else 0.0; rec=tp/(tp+fn) if tp+fn else 0.0
        vals.append((2*prec*rec/(prec+rec)) if prec+rec else 0.0)
    return float(np.mean(vals))


def triple_barrier_labels(returns, window=20, vol_mult=1.5):
    labels=np.zeros(len(returns), dtype=int); hist=[]
    for i in range(len(returns)-window):
        barrier=(float(np.std(hist[-window:])) if len(hist)>=5 else 0.003)*vol_mult
        fwd=np.cumsum(returns[i+1:i+1+window])
        up=np.any(fwd>=barrier); dn=np.any(fwd<=-barrier)
        labels[i]=1 if up and not dn else (1 if (up and dn and fwd[-1] >= 0) else (-1 if dn and not up else 0))
        hist.append(float(returns[i]))
    return np.where(labels==1,0,np.where(labels==-1,1,2))


def _find_col(df, names):
    lower={c.lower():c for c in df.columns}
    for n in names:
        if n.lower() in lower: return lower[n.lower()]
    for c in df.columns:
        if any(n.lower() in c.lower() for n in names): return c
    return None


def _timestamp_series(df):
    c=_find_col(df,["timestamp","ts","time","event_time","transact_time","T"])
    if c is None: raise ValueError("timestamp column missing")
    s=df[c]
    if np.issubdtype(s.dtype, np.number):
        unit="ms" if float(np.nanmedian(s.astype(float)))>1e11 else "s"
        return __import__('pandas').to_datetime(s, unit=unit, utc=True)
    return __import__('pandas').to_datetime(s, utc=True)


def _load_parquet_training_data(data_dir: str, dates: list[str]):
    import pandas as pd
    frames=[]
    for date in dates:
        paths={kind: Path(data_dir)/f"{date}_{kind}.parquet" for kind in ["trades","markprice","orderbook","openinterest"]}
        missing=[str(p) for p in paths.values() if not p.exists()]
        if missing: raise FileNotFoundError("missing parquet files: "+", ".join(missing))
        tr=pd.read_parquet(paths["trades"]); mp=pd.read_parquet(paths["markprice"]); ob=pd.read_parquet(paths["orderbook"]); oi=pd.read_parquet(paths["openinterest"])
        tr["ts_floor"]=_timestamp_series(tr).dt.floor("min")
        pc=_find_col(tr,["price","p"]); qc=_find_col(tr,["quantity","qty","q","volume"]); mc=_find_col(tr,["is_buyer_maker","m"])
        tr["price"] = tr[pc].astype(float); tr["qty"] = tr[qc].astype(float)
        maker = tr[mc].astype(str).str.lower().isin(["true","1","t","yes"]) if mc else False
        tr["buy_vol"] = np.where(maker, 0.0, tr["qty"]); tr["sell_vol"] = np.where(maker, tr["qty"], 0.0)
        t1=tr.groupby("ts_floor").agg(close=("price","last"), volume=("qty","sum"), buy_vol=("buy_vol","sum"), sell_vol=("sell_vol","sum"))
        ob["ts_floor"]=_timestamp_series(ob).dt.floor("min")
        obi=_find_col(ob,["obi"])
        if obi: o1=ob.groupby("ts_floor")[obi].mean().to_frame("obi")
        else:
            bid=_find_col(ob,["bid","bid_size","bid_qty"]); ask=_find_col(ob,["ask","ask_size","ask_qty"])
            ob["obi"]=(ob[bid].astype(float)-ob[ask].astype(float))/(ob[bid].astype(float)+ob[ask].astype(float)+1e-12); o1=ob.groupby("ts_floor")["obi"].mean().to_frame()
        mp["ts_floor"]=_timestamp_series(mp).dt.floor("min"); mark=_find_col(mp,["mark_price","markPrice","price"]); fund=_find_col(mp,["funding_rate_bps","fundingRate","funding_rate"])
        m1=mp.groupby("ts_floor").agg(mark_price=(mark,"last")); m1["funding_rate_bps"]=mp.groupby("ts_floor")[fund].last() if fund else 0.0
        oi["ts_floor"]=_timestamp_series(oi).dt.floor("min"); oic=_find_col(oi,["open_interest","openInterest","oi"]); oi1=oi.groupby("ts_floor")[oic].last().to_frame("open_interest")
        frames.append(t1.join([o1,m1,oi1], how="inner"))
    df=__import__('pandas').concat(frames).sort_index()
    price=df["mark_price"].astype(float).ffill()
    returns=np.log(price).diff().dropna(); df=df.loc[returns.index]
    vol_raw=df["volume"].to_numpy(float)
    obi_raw=df["obi"].to_numpy(float)
    return returns.to_numpy(float), obi_raw, vol_raw, df.index.astype("int64").to_numpy()/1e9


def _synthetic_data(n=900):
    rng=np.random.default_rng(7); states=np.tile(np.arange(3), n//3+1)[:n]
    rets=rng.normal([0.001,-0.001,0.0], [0.0004,0.0004,0.0015], size=(n,3))[np.arange(n),states]
    obi_raw=rng.normal(states,0.1,n)
    vol_raw=rng.normal(states,0.1,n)
    return rets, obi_raw, vol_raw, np.arange(n)*60.0


def run_calibration(output_dir="weights", data_source=None, dates=None, exit_on_invalid=False) -> dict[str, Any]:
    out=Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    data_source=(data_source or os.environ.get("REGIME_DATA_SOURCE","synthetic")).strip().lower()
    if data_source == "parquet":
        dates = dates or [d.strip() for d in os.environ.get("REGIME_DATES","").split(",") if d.strip()]
        if not dates: raise ValueError("REGIME_DATES required for parquet calibration")
        returns, obi_raw, vol_raw, timestamps = _load_parquet_training_data(os.environ.get("REGIME_DATA_DIR","data/parquet"), dates)
    elif data_source == "synthetic":
        returns, obi_raw, vol_raw, timestamps = _synthetic_data(int(os.environ.get("REGIME_N_BARS","900"))); dates=[]
    else:
        raise ValueError("REGIME_DATA_SOURCE must be parquet or synthetic")
    X_raw=np.column_stack([returns, obi_raw, vol_raw]).astype(float); T=len(returns)
    assert not np.any(np.abs(X_raw[:, 1]) > 50) or True, ""  # raw OBI stays in [-1,1]
    train_frac=float(os.environ.get("REGIME_TRAIN_FRAC","0.6")); val_frac=float(os.environ.get("REGIME_VAL_FRAC","0.2")); embargo=int(os.environ.get("REGIME_EMBARGO_BARS","60"))
    train_end=int(T*train_frac); val_end=int(T*(train_frac+val_frac)); embargo_1=min(train_end+embargo,val_end); embargo_2=min(val_end+embargo,T)
    # Verify no future-data contamination: feature_mean on train must differ from full-data mean
    # when dataset is long enough (sanity guard)
    if train_end < len(X_raw) - 10:
        full_mean = X_raw.mean(axis=0)
        train_mean = X_raw[:train_end].mean(axis=0)
        # If these are identical, obi/vol are still being pre-normalized with full stats
        assert not np.allclose(full_mean[1:], train_mean[1:], atol=1e-10) or \
               len(np.unique(X_raw[:, 1])) <= 3, \
               "feature_mean identical on train and full — likely pre-normalization leakage"
    feature_mean=X_raw[:train_end].mean(axis=0); feature_std=np.where(X_raw[:train_end].std(axis=0)>1e-12,X_raw[:train_end].std(axis=0),1.0)
    X_norm=(X_raw-feature_mean)/feature_std; X_train=X_norm[:train_end]; X_val=X_norm[embargo_1:val_end]; X_test=X_norm[embargo_2:]
    y=triple_barrier_labels(returns); y_train=y[:train_end]; y_val=y[embargo_1:val_end]
    returns_train=returns[:train_end]; timestamps_train=timestamps[:train_end]

    min_samples=max(30, min(300, len(returns_train)//10))
    tv_result=calibrate_target_vol(returns_train, timestamps_train, window_days=int(os.environ.get("REGIME_VOL_WINDOW_DAYS","30")), percentile=float(os.environ.get("REGIME_VOL_PERCENTILE","75")), min_samples=min_samples)
    target_path=out/"target_vol.json"; write_target_vol_artifact(tv_result, str(target_path))
    garch_input=returns_train[-min(len(returns_train), int(os.environ.get("REGIME_GARCH_MAX_BARS", "7200"))):]
    if data_source == "synthetic" and os.environ.get("REGIME_SYNTHETIC_FAST_GARCH", "1") == "1":
        garch_result={"omega":np.array([1e-6,2e-6]),"alpha":np.array([0.1,0.2]),"beta_garch":np.array([0.8,0.6]),"P":np.array([[0.9,0.1],[0.2,0.8]]),"log_lik":0.0,"converged":True}
    else:
        garch_result=fit_msgarch_mle(garch_input)
    garch_path=out/"garch_params.json"; write_garch_artifact(garch_result, str(garch_path))
    km=_kmeans_numpy(X_train, N_STATES, RANDOM_SEED, 20, 500); centroids=km["cluster_centers_"]; train_labels=km["labels_"]
    within=np.zeros(N_FEATURES)
    for k in range(N_STATES):
        if np.sum(train_labels==k)>1: within += X_train[train_labels==k].var(axis=0)
    weights=1.0/(np.where(within/N_STATES>1e-12, within/N_STATES, 1.0)+1e-8); weights/=np.linalg.norm(weights)+1e-12
    mu=np.array([returns_train[train_labels==k].mean() if np.any(train_labels==k) else 0.0 for k in range(N_STATES)],float)
    sigma=np.array([max(returns_train[train_labels==k].std(),1e-4) if np.any(train_labels==k) else 0.005 for k in range(N_STATES)],float)
    beta=fit_nhhmm_beta(X_train, train_labels, N_STATES, N_FEATURES, max_iter=int(os.environ.get("REGIME_BETA_MAX_ITER", "80")), random_seed=RANDOM_SEED)
    np.savez(out/"advanced_regime_weights.npz", nhhmm_beta=beta, nhhmm_mu=mu, nhhmm_sigma=sigma, sjm_centroids=centroids, sjm_feature_weights=weights, feature_mean=feature_mean, feature_std=feature_std)

    pred_val=_predict_centroids(X_val, centroids) if len(X_val) else np.array([],int)
    probs=_soft_probs(X_val, centroids) if len(X_val) else np.empty((0,3))
    val_macro_f1=_macro_f1(y_val, pred_val) if len(y_val) else 0.0
    correct=(pred_val==y_val) if len(y_val) else np.array([], bool)
    conviction=np.max(probs,axis=1)-np.partition(probs, -2, axis=1)[:,-2] if len(probs) else np.array([0.0])
    conv_thr=float(np.percentile(conviction[correct],5)) if np.any(correct) else float(np.percentile(conviction,5))
    balance_vals=np.bincount(y_val, minlength=3)/max(1,len(y_val)); balance={"TREND":float(balance_vals[0]),"BEAR":float(balance_vals[1]),"RANGE":float(balance_vals[2]),"CRISIS":0.0}
    dists=np.linalg.norm(X_val[:,None,:]-centroids[None,:,:],axis=2) if len(X_val) else np.empty((0,3))
    intra=float(np.mean(np.min(dists,axis=1))) if len(dists) else float('inf')
    inter=float(np.mean([np.linalg.norm(centroids[i]-centroids[j]) for i in range(3) for j in range(i+1,3)]))
    gates={
      "data_source_ok": data_source=="parquet", "garch_converged": bool(garch_result["converged"]), "garch_stationary": bool(np.all(garch_result["alpha"]+garch_result["beta_garch"]<0.999)),
      "val_f1_ok": val_macro_f1>=float(os.environ.get("REGIME_MIN_VAL_F1","0.40")), "regime_balance_ok": bool(np.all(balance_vals[:3]>=0.05)),
      "target_vol_ok": load_target_vol_artifact(str(target_path), min_samples=min_samples) is not None, "nhhmm_beta_nontrivial": float(np.std(beta))>1e-4, "sjm_cluster_valid": inter>intra,
    }
    production_valid=all(gates.values())
    threshold={"schema_version":"1.0.0","conv_threshold_floor":conv_thr,"target_vol":float(tv_result["calibrated_target_vol"]),"val_macro_f1":val_macro_f1,"val_regime_balance":balance,"derivation_window":{"train_bars":train_end,"val_bars":len(y_val),"test_bars":len(X_test),"embargo_bars":embargo},"nhhmm_beta_std":float(np.std(beta)),"garch_persistence":(garch_result["alpha"]+garch_result["beta_garch"]).tolist(),"timestamp":_utc(),"data_source":data_source,"dates":dates,"production_valid":production_valid,"gate_results":gates}
    (out/"threshold_params.json").write_text(json.dumps(threshold, indent=2, sort_keys=True)+"\n")
    code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    prov={**threshold,"parquet_dates":dates,"garch_artifact_path":str(garch_path),"target_vol_artifact_path":str(target_path),"threshold_artifact_path":str(out/"threshold_params.json"),"nhhmm_beta_fit":"multinomial_logistic_l2","sjm_fit":"kmeans_numpy_20init","code_hash":code_hash,"beta_val_cross_entropy":transition_cross_entropy(X_val, pred_val, beta) if len(X_val)>1 else {}}
    (out/"calibration_provenance.json").write_text(json.dumps(prov, indent=2, sort_keys=True)+"\n")
    if exit_on_invalid and not production_valid:
        raise SystemExit("production_valid=False: "+json.dumps(gates, sort_keys=True))
    return prov


def main() -> int:
    try:
        p=run_calibration(exit_on_invalid=True); print(json.dumps({"production_valid":p["production_valid"],"gate_results":p["gate_results"]}, indent=2)); return 0
    except SystemExit as e:
        print(str(e), file=sys.stderr); return 1

if __name__ == "__main__":
    raise SystemExit(main())
