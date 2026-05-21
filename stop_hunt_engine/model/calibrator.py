from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence, Tuple

import numpy as np

try:
    from sklearn.isotonic import IsotonicRegression  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
except Exception:  # pragma: no cover
    IsotonicRegression = None
    LogisticRegression = None


@dataclass
class ProbabilityCalibrator:
    method: Literal["platt", "isotonic"] = "platt"
    _model: Optional[object] = None
    _coef: Optional[Tuple[float, float]] = None

    def fit(self, raw: np.ndarray, y: np.ndarray) -> "ProbabilityCalibrator":
        raw_scores = np.asarray(raw, dtype=float).ravel()
        labels = np.asarray(y).astype(int).ravel()
        if self.method == "platt" and LogisticRegression is not None:
            model = LogisticRegression()
            model.fit(raw_scores.reshape(-1, 1), labels)
            self._model = model
            self._coef = None
            return self
        if self.method == "isotonic" and IsotonicRegression is not None:
            model = IsotonicRegression(out_of_bounds="clip")
            model.fit(raw_scores, labels)
            self._model = model
            self._coef = None
            return self
        epsilon = 1e-6
        bounded_scores = np.clip(raw_scores, epsilon, 1 - epsilon)
        log_odds = np.log(bounded_scores / (1 - bounded_scores))
        design_matrix = np.vstack([log_odds, np.ones_like(log_odds)]).T
        weights, bias = np.linalg.lstsq(design_matrix, labels, rcond=None)[0]
        self._coef = (float(weights), float(bias))
        self._model = None
        return self

    def transform(self, raw: np.ndarray) -> np.ndarray:
        raw_scores = np.asarray(raw, dtype=float).ravel()
        if self._model is not None:
            if self.method == "platt":
                calibrated = self._model.predict_proba(raw_scores.reshape(-1, 1))[:, 1]
            else:
                calibrated = self._model.transform(raw_scores)
            return np.clip(calibrated, 0, 1)
        if self._coef is not None:
            weights, bias = self._coef
            bounded_scores = np.clip(raw_scores, 1e-6, 1 - 1e-6)
            log_odds = np.log(bounded_scores / (1 - bounded_scores))
            calibrated = 1.0 / (1.0 + np.exp(-(weights * log_odds + bias)))
            return np.clip(calibrated, 0, 1)
        return np.clip(raw_scores, 0, 1)


def reliability_bins(
    probs: Sequence[float], y: Sequence[int], *, n_bins: int = 10
) -> List[Tuple[float, float, int]]:
    probabilities = np.asarray(probs)
    labels = np.asarray(y)
    edges = np.linspace(0, 1, n_bins + 1)
    output: List[Tuple[float, float, int]] = []
    for index in range(n_bins):
        in_bin = (probabilities >= edges[index]) & (
            probabilities < edges[index + 1] if index < n_bins - 1 else probabilities <= edges[index + 1]
        )
        if in_bin.sum() == 0:
            output.append((float("nan"), float("nan"), 0))
            continue
        output.append((float(probabilities[in_bin].mean()), float(labels[in_bin].mean()), int(in_bin.sum())))
    return output


def brier_score(probs: Sequence[float], y: Sequence[int]) -> float:
    probabilities = np.asarray(probs)
    labels = np.asarray(y)
    return float(np.mean((probabilities - labels) ** 2))


def expected_calibration_error(
    probs: Sequence[float], y: Sequence[int], *, n_bins: int = 10
) -> float:
    calibration_bins = reliability_bins(probs, y, n_bins=n_bins)
    sample_count = len(np.asarray(probs))
    return float(
        sum((count / max(sample_count, 1)) * abs(mean_conf - obs_freq) for mean_conf, obs_freq, count in calibration_bins if count > 0)
    )
