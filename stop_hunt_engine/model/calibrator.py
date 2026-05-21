from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import numpy as np

try:
    from sklearn.isotonic import IsotonicRegression  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
except Exception:  # pragma: no cover
    IsotonicRegression = None
    LogisticRegression = None

@dataclass
class ProbabilityCalibrator:
    method: Literal["platt","isotonic"] = "platt"
    _model: object | None = None
    _coef: tuple[float, float] | None = None

    def fit(self, raw: np.ndarray, y: np.ndarray) -> "ProbabilityCalibrator":
        x=np.asarray(raw,dtype=float).ravel(); y=np.asarray(y).astype(int).ravel()
        if self.method=="platt" and LogisticRegression is not None:
            m=LogisticRegression(); m.fit(x.reshape(-1,1),y); self._model=m; self._coef=None; return self
        if self.method=="isotonic" and IsotonicRegression is not None:
            m=IsotonicRegression(out_of_bounds="clip"); m.fit(x,y); self._model=m; self._coef=None; return self
        # Fallback for environments without sklearn (e.g., no wheel for interpreter).
        # Fit a 1D logistic by linearizing probabilities.
        eps=1e-6
        p=np.clip(x,eps,1-eps)
        z=np.log(p/(1-p))
        A=np.vstack([z, np.ones_like(z)]).T
        w,b=np.linalg.lstsq(A,y,rcond=None)[0]
        self._coef=(float(w),float(b)); self._model=None
        return self

    def transform(self, raw: np.ndarray) -> np.ndarray:
        x=np.asarray(raw,dtype=float).ravel()
        if self._model is not None:
            if self.method=="platt":
                out=self._model.predict_proba(x.reshape(-1,1))[:,1]
            else:
                out=self._model.transform(x)
            return np.clip(out,0,1)
        if self._coef is not None:
            w,b=self._coef
            p=np.clip(x,1e-6,1-1e-6)
            z=np.log(p/(1-p))
            out=1.0/(1.0+np.exp(-(w*z+b)))
            return np.clip(out,0,1)
        return np.clip(x,0,1)

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
