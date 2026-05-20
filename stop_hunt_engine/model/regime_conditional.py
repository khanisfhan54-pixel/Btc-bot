# pasted
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np
from .sweep_classifier import SweepClassifier
log = logging.getLogger("shpe.regime_conditional")
@dataclass
class RegimeConditionalClassifier:
    feature_names: List[str]
    min_samples_per_regime: int = 30
    C: float = 1.0
    max_iter: int = 2000
    random_state: int = 42
    max_feature_importance: float = 0.3
    global_model: Optional[SweepClassifier] = field(default=None, repr=False)
    sub_models: Dict[str, SweepClassifier] = field(default_factory=dict, repr=False)
    _train_counts: Dict[str, int] = field(default_factory=dict, repr=False)
    _undertrained: set = field(default_factory=set, repr=False)
    last_routing_log: List[Dict[str, str]] = field(default_factory=list, repr=False)
    def fit(self, X: np.ndarray, y: np.ndarray, regime_labels: List[str], *, run_importance_audit: bool = True) -> "RegimeConditionalClassifier":
        X=np.asarray(X,dtype=float); y=np.asarray(y).astype(int); regimes=[str(r) for r in regime_labels]
        self.global_model = SweepClassifier(feature_names=list(self.feature_names), C=self.C, max_iter=self.max_iter, random_state=self.random_state, max_feature_importance=self.max_feature_importance)
        self.global_model.fit(X,y,run_importance_audit=run_importance_audit)
        self.sub_models={}; self._train_counts={}; self._undertrained=set()
        for r in sorted(set(regimes)):
            mask=np.array([rr==r for rr in regimes],dtype=bool); c=int(mask.sum()); self._train_counts[r]=c
            if c<self.min_samples_per_regime: self._undertrained.add(r); continue
            sub_y=y[mask]
            if np.unique(sub_y).size<2: continue
            sub=SweepClassifier(feature_names=list(self.feature_names),C=self.C,max_iter=self.max_iter,random_state=self.random_state,max_feature_importance=self.max_feature_importance)
            sub.fit(X[mask],sub_y,run_importance_audit=False); self.sub_models[r]=sub
        return self
    def predict_proba(self, x: np.ndarray, regime_label: Optional[str]) -> tuple[float, str]:
        if self.global_model is None: raise RuntimeError("called before fit")
        x=np.atleast_2d(np.asarray(x,dtype=float)); r=str(regime_label) if regime_label is not None else ""; sub=self.sub_models.get(r) if r else None
        if sub is not None: return float(sub.predict_proba(x)[0]), r
        return float(self.global_model.predict_proba(x)[0]), "<global>"
