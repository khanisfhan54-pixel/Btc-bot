"""
calibrate_smoother.py — Validation-fold-only grid search for RegimeMarkovSmoother blend.

RULES:
- NEVER tune on test data
- NEVER leak future labels
- Optimize macro-F1 on validation fold only
- Walk-forward compatible

Usage:
    from calibrate_smoother import calibrate_smoother_blend
    best_blend = calibrate_smoother_blend(regime_posteriors_val, true_labels_val)
"""
from __future__ import annotations

import numpy as np
from typing import Optional
from advanced_regime_engine import RegimeMarkovSmoother


def calibrate_smoother_blend(
    regime_posteriors: np.ndarray,
    true_labels: np.ndarray,
    blend_candidates: Optional[np.ndarray] = None,
    n_states: int = 3,
) -> float:
    """
    Grid search over blend values on VALIDATION data only.
    Returns the blend that maximises macro-F1.

    CRITICAL: Pass ONLY validation fold data. Never pass test data.
    """
    try:
        from sklearn.metrics import f1_score as _f1_score
        def macro_f1(y_true, y_pred):
            return _f1_score(y_true, y_pred, average="macro", zero_division=0)
    except ImportError:
        def macro_f1(y_true, y_pred):
            classes = np.unique(np.concatenate([y_true, y_pred]))
            f1s = []
            for c in classes:
                tp = int(((y_pred == c) & (y_true == c)).sum())
                fp = int(((y_pred == c) & (y_true != c)).sum())
                fn = int(((y_pred != c) & (y_true == c)).sum())
                prec = tp / max(tp + fp, 1)
                rec = tp / max(tp + fn, 1)
                f1s.append(2 * prec * rec / max(prec + rec, 1e-12))
            return float(np.mean(f1s)) if f1s else 0.0

    if blend_candidates is None:
        blend_candidates = np.arange(0.10, 0.85, 0.05)

    label_map = {"TREND": 1, "BEAR": -1, "RANGE": 0, "TOXIC": 2}
    best_blend = 0.35
    best_f1 = 0.0

    for blend in blend_candidates:
        smoother = RegimeMarkovSmoother(blend=float(blend))
        preds = []

        for t in range(len(regime_posteriors)):
            row = regime_posteriors[t]
            if n_states == 4:
                scores = {
                    "bull": float(row[0]),
                    "bear": float(row[1]),
                    "range_score": float(row[2]),
                    "toxic_score": float(row[3]),
                    "trend_score": float(row[0]),
                    "bear_score": float(row[1]),
                }
            else:
                scores = {
                    "bull": float(row[0]),
                    "bear": float(row[1]),
                    "trend_score": float(row[0]),
                    "bear_score": float(row[1]),
                    "range_score": 0.0,
                    "toxic_score": float(row[2]),
                }
            regime, _ = smoother.update(scores, None)
            preds.append(regime)

        y_pred = np.array([label_map.get(p, 0) for p in preds])
        mask = np.isfinite(true_labels.astype(float))
        if mask.sum() < 10:
            continue
        f1 = macro_f1(true_labels[mask].astype(int), y_pred[mask])
        if f1 > best_f1:
            best_f1 = f1
            best_blend = float(blend)

    print(f"[calibrate_smoother] Best blend: {best_blend:.2f}  (val macro-F1={best_f1:.3f})")
    return best_blend
