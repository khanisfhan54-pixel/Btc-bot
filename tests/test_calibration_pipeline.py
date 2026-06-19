from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pytest

from calibrate_garch import write_garch_artifact, load_garch_artifact
from calibrate_nhhmm_beta import fit_nhhmm_beta, transition_cross_entropy
from advanced_regime_engine import MSGARCH_RiskEngine


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


def test_diff_scope_paths():
    import subprocess
    allowed={"calibrate_regime.py","calibrate_garch.py","calibrate_nhhmm_beta.py","calibrate_pipeline.py","advanced_regime_engine.py","weights/garch_params.json","weights/threshold_params.json","weights/target_vol.json","weights/calibration_provenance.json","weights/advanced_regime_weights.npz","tests/test_calibration_pipeline.py"}
    out=subprocess.check_output(["git","diff","--name-only"], text=True).splitlines()
    assert set(out).issubset(allowed)
