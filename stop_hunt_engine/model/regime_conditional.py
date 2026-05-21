"""Regime-conditional classifier dispatcher with global fallback."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .sweep_classifier import SweepClassifier


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
    _undertrained: set[str] = field(default_factory=set, repr=False)
    last_routing_log: List[Dict[str, str]] = field(default_factory=list, repr=False)

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        regime_labels: List[str],
        *,
        run_importance_audit: bool = True,
    ) -> "RegimeConditionalClassifier":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).astype(int)
        regimes = [str(r) for r in regime_labels]
        if X.shape[0] != y.shape[0] or len(regimes) != X.shape[0]:
            raise ValueError("X, y, regime_labels must have matching length")

        self.global_model = SweepClassifier(
            feature_names=list(self.feature_names),
            C=self.C,
            max_iter=self.max_iter,
            random_state=self.random_state,
            max_feature_importance=self.max_feature_importance,
        ).fit(X, y, run_importance_audit=run_importance_audit)

        self.sub_models = {}
        self._train_counts = {}
        self._undertrained = set()
        for r in sorted(set(regimes)):
            mask = np.asarray([rr == r for rr in regimes], dtype=bool)
            count = int(mask.sum())
            self._train_counts[r] = count
            if count < self.min_samples_per_regime:
                self._undertrained.add(r)
                continue
            y_sub = y[mask]
            if np.unique(y_sub).size < 2:
                continue
            self.sub_models[r] = SweepClassifier(
                feature_names=list(self.feature_names),
                C=self.C,
                max_iter=self.max_iter,
                random_state=self.random_state,
                max_feature_importance=self.max_feature_importance,
            ).fit(X[mask], y_sub, run_importance_audit=False)
        return self

    def predict_proba(self, x: np.ndarray, regime_label: Optional[str]) -> tuple[float, str]:
        if self.global_model is None:
            raise RuntimeError("RegimeConditionalClassifier.predict_proba called before fit")
        xx = np.atleast_2d(np.asarray(x, dtype=float))
        r = str(regime_label) if regime_label is not None else ""
        sub = self.sub_models.get(r) if r else None
        if sub is not None:
            p = float(sub.predict_proba(xx)[0])
            self.last_routing_log.append({"regime": r, "used": r})
            return p, r

        p = float(self.global_model.predict_proba(xx)[0])
        reason = "no_regime" if not r else ("insufficient_samples" if r in self._undertrained else ("seen_but_unusable" if r in self._train_counts else "missing_regime"))
        self.last_routing_log.append({"regime": r or "<none>", "used": "<global>", "reason": reason})
        return p, "<global>"

    def train_counts(self) -> Dict[str, int]:
        return dict(self._train_counts)
