from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np
try:
    import joblib  # type: ignore
except Exception:  # pragma: no cover
    joblib = None
try:
    from sklearn.inspection import permutation_importance  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
except Exception:  # pragma: no cover
    LogisticRegression = None
    permutation_importance = None

class FeatureImportanceViolation(RuntimeError):
    pass

@dataclass
class SweepClassifier:
    feature_names: List[str]
    C: float = 1.0
    max_iter: int = 2000
    random_state: int = 42
    max_feature_importance: float = 0.3
    model: Optional[object] = field(default=None, repr=False)
    _fitted: bool = field(default=False, repr=False)
    _classes: Optional[np.ndarray] = field(default=None, repr=False)
    _coef: Optional[np.ndarray] = field(default=None, repr=False)

    def fit(self, X: np.ndarray, y: np.ndarray, *, run_importance_audit: bool = True) -> "SweepClassifier":
        X=np.asarray(X,dtype=float); y=np.asarray(y).astype(int)
        if LogisticRegression is not None:
            self.model = LogisticRegression(C=self.C,max_iter=self.max_iter,random_state=self.random_state,solver="lbfgs",multi_class="ovr")
            self.model.fit(X, y); self._classes = self.model.classes_; self._coef=None
        else:
            Xb=np.hstack([X,np.ones((X.shape[0],1))])
            self._coef=np.linalg.lstsq(Xb,y,rcond=None)[0]
            self.model=None
        self._fitted=True
        if run_importance_audit and np.unique(y).size >= 2 and X.shape[0] >= 10 and permutation_importance is not None and self.model is not None:
            self.assert_max_feature_importance(X, y, threshold=self.max_feature_importance)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self._fitted: raise RuntimeError("SweepClassifier.predict_proba called before fit()")
        X=np.atleast_2d(np.asarray(X,dtype=float))
        if self.model is not None:
            proba_all=self.model.predict_proba(X)
            idx=np.where(self._classes==1)[0] if self._classes is not None else np.array([])
            pos_idx=int(idx[0]) if idx.size else proba_all.shape[1]-1
            return np.clip(proba_all[:,pos_idx],0.0,1.0)
        Xb=np.hstack([X,np.ones((X.shape[0],1))])
        z=Xb@self._coef
        return 1.0/(1.0+np.exp(-z))

    def assert_max_feature_importance(self, X: np.ndarray, y: np.ndarray, *, threshold: float = 0.3, n_repeats: int = 10) -> dict:
        if self.model is None or permutation_importance is None:
            return {name: 0.0 for name in self.feature_names}
        result=permutation_importance(self.model,X,y,n_repeats=n_repeats,random_state=self.random_state,scoring="accuracy")
        imps={n: float(result.importances_mean[i]) for i,n in enumerate(self.feature_names)}
        m=max(imps,key=lambda k:imps[k])
        if imps[m] > threshold: raise FeatureImportanceViolation(f"Feature {m!r} permutation importance {imps[m]:.4f} exceeds threshold {threshold:.4f}")
        return imps
