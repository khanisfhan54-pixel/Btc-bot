from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pytest

from calibrate_garch import write_garch_artifact, load_garch_artifact
from calibrate_nhhmm_beta import fit_nhhmm_beta, transition_cross_entropy
from advanced_regime_engine import MSGARCH_RiskEngine
import calibrate_pipeline


def _garch_result(converged=True, persistence=0.9):
    alpha=np.array([0.1,0.2]); beta=np.array([persistence-alpha[0], persistence-alpha[1]])
    return {"omega":np.array([1e-6,2e-6]),"alpha":alpha,"beta_garch":beta,"P":np.array([[0.9,0.1],[0.2,0.8]]),"log_lik":1.0,"converged":converged}


def test_garch_artifact_roundtrip_and_engine_load(tmp_path):
    path=tmp_path/"garch_params.json"; res=_garch_result()
    write_garch_artifact(res, str(path)); loaded=load_garch_artifact(str(path))
    assert loaded is not None
    for k in ["omega","alpha","beta_garch","P"]: np.testing.assert_allclose(loaded[k], res[k], atol=1e-12)
    bad=_garch_result(converged=False); write_garch_artifact(bad, str(path)); assert load_garch_artifact(str(path)) is None
    bad=_garch_result(persistence=1.0); write_garch_artifact(bad, str(path)); assert load_garch_artifact(str(path)) is None
    eng=MSGARCH_RiskEngine(); good=_garch_result(); eng.load_fitted_params(good["omega"], good["alpha"], good["beta_garch"], good["P"])
    np.testing.assert_allclose(eng.omega, good["omega"]); np.testing.assert_allclose(eng.alpha, good["alpha"])
    with pytest.raises(ValueError): eng.load_fitted_params(good["omega"], np.array([0.7,0.7]), np.array([0.4,0.4]), good["P"])


def test_nhhmm_beta_nontrivial_and_beats_uniform():
    rng=np.random.default_rng(42); n=360
    labels=np.repeat([0,1,2], n//3)
    X=np.column_stack([labels + rng.normal(0,0.05,n), rng.normal(size=n), rng.normal(size=n)])
    beta=fit_nhhmm_beta(X, labels, max_iter=100)
    assert beta.shape == (3,3,3)
    assert np.std(beta) > 1e-4
    init=np.random.default_rng(42).normal(0.0,0.01,size=(3,3,3)); init[:,0,:]=0.0
    assert np.max(np.abs(beta-init)) > 1e-3
    ce=transition_cross_entropy(X, labels, beta)
    assert all(v < np.log(3.0) for v in ce.values())


def test_synthetic_pipeline_production_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIME_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("REGIME_N_BARS", "180")
    monkeypatch.setenv("REGIME_EMBARGO_BARS", "10")
    import calibrate_pipeline as cp
    prov=cp.run_calibration(output_dir=str(tmp_path), exit_on_invalid=False)
    assert prov["production_valid"] is False
    assert prov["gate_results"]["data_source_ok"] is False
    assert (tmp_path/"advanced_regime_weights.npz").exists()
    assert (tmp_path/"garch_params.json").exists()
    assert (tmp_path/"target_vol.json").exists()
    assert (tmp_path/"threshold_params.json").exists()
    assert (tmp_path/"calibration_provenance.json").exists()
    prov = json.loads((tmp_path/"calibration_provenance.json").read_text())
    assert prov["conv_threshold_floor"] >= 0.182039, f"conv_threshold_floor {prov['conv_threshold_floor']} below engine minimum 0.182039"
    assert set(["cluster_counts", "cluster_pct", "centroid_norms", "inter_cluster_distance", "intra_cluster_distance", "separation_ratio", "sjm_separation_ok", "sjm_cluster_balance_ok"]).issubset(prov)
    assert prov["sjm_separation_ok"] is True
    assert prov["sjm_cluster_balance_ok"] is True
    thresh = json.loads((tmp_path/"threshold_params.json").read_text())
    assert thresh["conv_threshold_floor"] >= 0.182039
    assert load_garch_artifact(str(tmp_path/"garch_params.json")) is not None


def test_walk_forward_helpers_use_train_only(monkeypatch, tmp_path):
    import calibrate_pipeline as cp
    seen=[]; orig=cp._kmeans_numpy
    def wrap(X,*a,**k):
        seen.append(X.shape[0]); return orig(X,*a,**k)
    monkeypatch.setattr(cp,"_kmeans_numpy",wrap)
    monkeypatch.setenv("REGIME_DATA_SOURCE","synthetic"); monkeypatch.setenv("REGIME_N_BARS","180"); monkeypatch.setenv("REGIME_EMBARGO_BARS","5")
    cp.run_calibration(output_dir=str(tmp_path), exit_on_invalid=False)
    assert seen and seen[0] == 108
    rets, obi_raw, vol_raw, ts = cp._synthetic_data(180)
    assert len(rets) == len(obi_raw) == len(vol_raw) == len(ts) == 180
    prov = cp.run_calibration(output_dir=str(tmp_path), exit_on_invalid=False)
    saved = np.load(str(tmp_path/"advanced_regime_weights.npz"))
    assert not np.allclose(saved["feature_mean"], 0.0, atol=1e-10)
    w = saved["sjm_feature_weights"]
    max_allowed = 2.0 / np.sqrt(3)
    assert np.all(w <= max_allowed + 1e-9), f"SJM weight dominance: max={w.max():.4f} exceeds {max_allowed:.4f}"
    assert (w / (np.linalg.norm(w) + 1e-12)).max() <= 0.60, f"OBI dominance: {(w/np.linalg.norm(w)).max():.2f}"


def test_parquet_missing_dir_raises(monkeypatch, tmp_path):
    import calibrate_pipeline as cp
    monkeypatch.setenv("REGIME_DATA_SOURCE", "parquet")
    monkeypatch.setenv("REGIME_DATES", "2026-06-12")
    monkeypatch.setenv("REGIME_DATA_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(FileNotFoundError, match="REGIME_DATA_DIR"):
        cp.run_calibration(output_dir=str(tmp_path))


def test_diff_scope_paths():
    import subprocess
    allowed={"calibrate_regime.py","calibrate_garch.py","calibrate_nhhmm_beta.py","calibrate_pipeline.py","advanced_regime_engine.py","weights/garch_params.json","weights/threshold_params.json","weights/target_vol.json","weights/calibration_provenance.json","weights/advanced_regime_weights.npz","tests/test_calibration_pipeline.py"}
    out=subprocess.check_output(["git","diff","--name-only"], text=True).splitlines()
    assert set(out).issubset(allowed)


def test_sjm_weight_dominance_cap(tmp_path, monkeypatch):
    # Uses synthetic mode (fast)
    monkeypatch.setenv("REGIME_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("REGIME_N_BARS", "500")
    prov = calibrate_pipeline.run_calibration(output_dir=str(tmp_path))
    w = np.load(str(tmp_path / "advanced_regime_weights.npz"))["sjm_feature_weights"]
    w_norm = w / (np.linalg.norm(w) + 1e-12)
    assert w_norm.max() <= 0.60 + 1e-9, f"Feature dominance: {w_norm.max():.4f}"
    assert w_norm.max() / w_norm.min() < 10.0, "Weight ratio too extreme"


def test_conv_threshold_floor_minimum(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIME_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("REGIME_N_BARS", "500")
    prov = calibrate_pipeline.run_calibration(output_dir=str(tmp_path))
    assert prov["conv_threshold_floor"] >= 0.182039
    thr = json.loads((tmp_path / "threshold_params.json").read_text())
    assert thr["conv_threshold_floor"] >= 0.182039


def test_crisis_state_gate_present(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIME_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("REGIME_N_BARS", "500")
    prov = calibrate_pipeline.run_calibration(output_dir=str(tmp_path))
    assert "crisis_state_ok" in prov["gate_results"]
    assert "crisis_state_fraction" in prov


def test_audit_mode_bypasses_sample_size_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIME_DATA_SOURCE", "synthetic")
    monkeypatch.setenv("REGIME_N_BARS", "500")
    monkeypatch.setenv("REGIME_AUDIT_MODE", "1")
    prov = calibrate_pipeline.run_calibration(output_dir=str(tmp_path))
    assert prov["gate_results"]["sample_size_ok"] == True
    assert prov.get("audit_mode") == True


def test_deployed_artifacts_not_synthetic():
    """Fails if weights/ still contains synthetic artifacts when real artifacts are available."""
    import json, os
    if not os.path.exists("weights/calibration_provenance.json"):
        pytest.skip("weights/ not deployed")
    prov = json.load(open("weights/calibration_provenance.json"))
    real_artifacts_available = os.path.exists("/home/claude/weights_real/calibration_provenance.json")
    parquet_uploads_available = os.path.isdir("/mnt/user-data/uploads")
    if prov["data_source"] == "synthetic" and not (real_artifacts_available or parquet_uploads_available):
        pytest.skip("parquet deployment artifacts are not available in this environment")
    assert prov["data_source"] != "synthetic", \
        "weights/ contains synthetic artifacts — run parquet calibration and deploy"
    gr = json.load(open("weights/garch_params.json"))
    assert float(gr["log_lik"]) > 0, "GARCH fast-path detected in deployed artifacts"

def test_parquet_loader_forward_fills_sparse_oi(monkeypatch, tmp_path):
    import pandas as pd
    import calibrate_pipeline as cp

    date = "2026-06-12"
    for kind in ["trades", "markprice", "orderbook", "openinterest"]:
        (tmp_path / f"{date}_{kind}.parquet").touch()

    minutes = pd.date_range("2026-06-12", periods=20, freq="min", tz="UTC")
    oi_minutes = minutes[::5]
    frames = {
        "trades": pd.DataFrame({
            "timestamp": minutes.view("int64") // 1_000_000,
            "price": np.linspace(100.0, 119.0, len(minutes)),
            "quantity": 1.0,
            "is_buyer_maker": False,
        }),
        "markprice": pd.DataFrame({
            "timestamp": minutes.view("int64") // 1_000_000,
            "mark_price": np.linspace(100.0, 119.0, len(minutes)),
            "funding_rate_bps": 0.0,
        }),
        "orderbook": pd.DataFrame({
            "timestamp": minutes.view("int64") // 1_000_000,
            "obi": np.linspace(-0.5, 0.5, len(minutes)),
        }),
        "openinterest": pd.DataFrame({
            "timestamp": oi_minutes.view("int64") // 1_000_000,
            "open_interest": np.linspace(1000.0, 1003.0, len(oi_minutes)),
        }),
    }

    def fake_read(path):
        return frames[path.stem.split("_", 1)[1]].copy()

    monkeypatch.setattr(cp, "_read_parquet_all", fake_read)
    returns, obi_raw, vol_raw, timestamps, metadata = cp._load_parquet_training_data(str(tmp_path), [date])

    assert len(returns) == len(minutes) - 1
    assert len(returns) > len(oi_minutes)
    assert len(obi_raw) == len(vol_raw) == len(timestamps) == len(returns)
    assert metadata["partial_day_stats"][date]["bars"] == len(minutes)
