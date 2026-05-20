from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

@dataclass
class ProbabilityCalibrator:
    method: Literal["platt","isotonic"] = "platt"
    _model: object | None = None
    def fit(self, raw: np.ndarray, y: np.ndarray) -> "ProbabilityCalibrator":
        x=np.asarray(raw,dtype=float).reshape(-1,1); y=np.asarray(y).astype(int)
        if self.method=="platt":
            m=LogisticRegression(); m.fit(x,y); self._model=m
        else:
            m=IsotonicRegression(out_of_bounds="clip"); m.fit(x.ravel(),y); self._model=m
        return self
    def transform(self, raw: np.ndarray) -> np.ndarray:
        x=np.asarray(raw,dtype=float)
        if self._model is None: return np.clip(x,0,1)
        if self.method=="platt": out=self._model.predict_proba(x.reshape(-1,1))[:,1]
        else: out=self._model.transform(x)
        return np.clip(out,0,1)

def reliability_bins(probs,y,*,n_bins=10):
    p=np.asarray(probs); y=np.asarray(y); edges=np.linspace(0,1,n_bins+1); out=[]
    for i in range(n_bins):
        m=(p>=edges[i])&(p<edges[i+1] if i<n_bins-1 else p<=edges[i+1])
        if m.sum()==0: out.append((float('nan'),float('nan'),0)); continue
        out.append((float(p[m].mean()), float(y[m].mean()), int(m.sum())))
    return out

def brier_score(probs,y):
    p=np.asarray(probs); y=np.asarray(y); return float(np.mean((p-y)**2))

def expected_calibration_error(probs,y,*,n_bins=10):
    bins=reliability_bins(probs,y,n_bins=n_bins); n=len(np.asarray(probs));
    return float(sum((c/max(n,1))*abs(mc-of) for mc,of,c in bins if c>0))
