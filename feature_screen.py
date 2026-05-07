import numpy as np
from sklearn.feature_selection import mutual_info_classif


FEATURE_NAMES = ["OFI", "VWOI", "RV_5m", "Kyle_lam", "Spread", "TradeImb"]
MI_THRESHOLD = 0.05


def screen_features(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list = None,
    mi_threshold: float = MI_THRESHOLD,
) -> dict:
    """
    Validate whether features contain regime-predictive information.

    Args:
        X: shape (N, n_features) — feature matrix
        y: shape (N,) — regime labels in {-1, 0, 1, 2}
        feature_names: list of feature name strings
        mi_threshold: minimum MI score to pass (default 0.05 nats)

    Returns:
        dict keyed by feature name with mi_score and passes flag.

    Gate 2 passes when at least 3 microstructure features rank in the top 5 by MI.
    """
    if feature_names is None:
        feature_names = [f"feature_{i}" for i in range(X.shape[1])]

    # Remap labels to non-negative ints required by sklearn
    y_mapped = (np.asarray(y) + 1).astype(int)  # {-1,0,1,2} → {0,1,2,3}

    mi_scores = mutual_info_classif(
        X, y_mapped, discrete_features=False, n_neighbors=5, random_state=42
    )

    result = {}
    for i, name in enumerate(feature_names):
        result[name] = {
            "mi_score": float(mi_scores[i]),
            "passes":   bool(mi_scores[i] >= mi_threshold),
        }

    ranked = sorted(result.items(), key=lambda x: -x[1]["mi_score"])

    print(f"\nFeature MI Scores (threshold={mi_threshold} nats):")
    for name, info in ranked:
        status = "PASS" if info["passes"] else "FAIL — remove"
        print(f"  {name:20s}: {info['mi_score']:.4f}  [{status}]")

    top5_names = [name for name, _ in ranked[:5]]
    microstructure_names = set(FEATURE_NAMES)
    top5_micro_count = sum(1 for n in top5_names if n in microstructure_names)

    gate2_passes = top5_micro_count >= 3

    print(f"\n[GATE 2] Microstructure features in top 5: {top5_micro_count}/5")
    print(f"[GATE 2] {'PASS ✅' if gate2_passes else 'FAIL ❌ — DO NOT PROCEED TO TRAINING'}")

    result["_gate2"] = {
        "top5_names": top5_names,
        "top5_micro_count": top5_micro_count,
        "passes": gate2_passes,
    }

    return result
