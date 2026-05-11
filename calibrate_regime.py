"""
Phase 3 — Regime Model Calibration
Produces: weights/advanced_regime_weights.npz

Rules:
- Strictly causal: no future data at any step
- Triple-barrier labels drive supervised components
- All randomness is seeded for reproducibility
- No modifications to any existing module
- Pure numpy only — no sklearn or scipy required
"""

import os
import numpy as np
from collections import deque

# ─── CONFIG ──────────────────────────────────────────────────

# N_STATES controls the regime model architecture:
#   3 = Legacy 3-state (Bull/Bear/Crisis) — default, backward-compatible
#   4 = Phase 3 4-state (Bull/Bear/Range/Crisis) — requires ARE(n_states=4)
#       and a 4-state weights file to be loaded into the engine.
#
# OPERATOR: to run Phase 3 calibration, set N_STATES = 4 here AND ensure
# AdvancedRegimeEngine is constructed with n_states=4.
# The .npz produced with N_STATES=4 is INCOMPATIBLE with n_states=3 engines.
N_STATES       = 3
N_FEATURES     = 3
N_BARS         = 8910
LOOKBACK       = 200
RV_WINDOW      = 5
RANDOM_SEED    = 42
BARRIER_WINDOW = 20
BARRIER_MULT   = 1.5
OUTPUT_DIR     = "weights"
OUTPUT_PATH    = os.path.join(OUTPUT_DIR, "advanced_regime_weights.npz")

# Guard: N_STATES must be 3 or 4. Any other value indicates a misconfiguration.
if N_STATES not in (3, 4):
    raise ValueError(
        f"calibrate_regime.py: N_STATES must be 3 (legacy) or 4 (Phase 3). "
        f"Got N_STATES={N_STATES}."
    )

np.random.seed(RANDOM_SEED)

print("=" * 60)
print("PHASE 3 — REGIME MODEL CALIBRATION")
print("=" * 60)


# ─── PURE NUMPY KMEANS (no sklearn required) ─────────────────

def _kmeans_numpy(X, n_clusters=3, random_state=42,
                  n_init=20, max_iter=500):
    """
    Pure numpy K-means. No external dependencies.
    Returns object with .cluster_centers_ and .labels_
    """
    rng = np.random.default_rng(random_state)
    best_inertia = np.inf
    best_centers = None
    best_labels  = None
    n = X.shape[0]

    for _ in range(n_init):
        idx = rng.choice(n, n_clusters, replace=False)
        centers = X[idx].copy()
        for _ in range(max_iter):
            dists  = np.linalg.norm(
                X[:, None, :] - centers[None, :, :], axis=2
            )
            labels = np.argmin(dists, axis=1)
            new_centers = centers.copy()
            for k in range(n_clusters):
                mask = labels == k
                if mask.any():
                    new_centers[k] = X[mask].mean(axis=0)
            if np.allclose(new_centers, centers, atol=1e-10):
                centers = new_centers
                break
            centers = new_centers
        inertia = float(((X - centers[labels]) ** 2).sum())
        if inertia < best_inertia:
            best_inertia = inertia
            best_centers = centers.copy()
            best_labels  = labels.copy()

    class _KMeansResult:
        pass
    result = _KMeansResult()
    result.cluster_centers_ = best_centers
    result.labels_           = best_labels
    return result


# ─── STEP 1: GENERATE / LOAD PRICE DATA ──────────────────────

print("\n[1/7] Generating synthetic training data...")

prices      = [50000.0]
returns     = []
bid_sizes   = []
ask_sizes   = []
trade_flows = []
buy_vols    = []
sell_vols   = []

for i in range(N_BARS):
    ret = np.random.randn() * 0.002 + 0.00005
    new_price = prices[-1] * (1 + ret)
    prices.append(new_price)
    returns.append(ret)
    bid_sizes.append(abs(np.random.randn() * 10 + 20))
    ask_sizes.append(abs(np.random.randn() * 10 + 20))
    trade_flows.append(np.random.randn() * 5)
    buy_vols.append(abs(np.random.randn() * 100 + 100))
    sell_vols.append(abs(np.random.randn() * 100 + 100))

prices  = np.array(prices)
returns = np.array(returns)
print(f"    Bars: {N_BARS} | Price range: "
      f"{prices.min():.0f} - {prices.max():.0f}")

# ─── STEP 2: BUILD FEATURE MATRIX ────────────────────────────

print("\n[2/7] Building feature matrix...")

_raw_vols = np.abs(np.random.randn(N_BARS) * 100 + 100)
_vol_mean = float(_raw_vols.mean())
_vol_std  = float(_raw_vols.std()) if float(_raw_vols.std()) > 1e-8 else 1.0
candle_volume_z = (_raw_vols - _vol_mean) / _vol_std  # z-scored, mean≈0, std≈1

X_raw         = []
valid_returns = []
valid_prices  = []

for i in range(N_BARS):
    log_ret = float(returns[i])
    ofi_z = float((bid_sizes[i] - ask_sizes[i]) / max(bid_sizes[i] + ask_sizes[i], 1e-8))
    vol_idx = float(candle_volume_z[i])

    X_raw.append([log_ret, ofi_z, vol_idx])
    valid_returns.append(returns[i])
    valid_prices.append(float(prices[i + 1]))

X_raw         = np.array(X_raw,         dtype=float)
valid_returns = np.array(valid_returns, dtype=float)

assert X_raw.shape == (N_BARS, N_FEATURES), \
    f"Feature matrix shape error: {X_raw.shape}"
assert not np.isnan(X_raw).any(),  "NaN in feature matrix"
assert np.isfinite(X_raw).all(),   "Non-finite in feature matrix"

print(f"    Feature matrix: {X_raw.shape} — OK")

# ─── STEP 3: COMPUTE NORMALIZATION MOMENTS ───────────────────

print("\n[3/7] Computing calibration-time normalization moments...")

calib_cutoff = int(0.8 * N_BARS)
X_calib      = X_raw[:calib_cutoff]

feature_mean = X_calib.mean(axis=0)
feature_std  = X_calib.std(axis=0)
feature_std  = np.where(feature_std > 1e-12, feature_std, 1.0)

print(f"    feature_mean: {feature_mean.round(4)}")
print(f"    feature_std:  {feature_std.round(4)}")

# ─── STEP 4: TRIPLE-BARRIER LABELS ───────────────────────────

print("\n[4/7] Computing triple-barrier regime labels...")


def triple_barrier_labels(
    returns: np.ndarray,
    window: int = 20,
    vol_mult: float = 1.5,
) -> np.ndarray:
    N      = len(returns)
    labels = np.zeros(N, dtype=int)
    rolling_vol = deque(maxlen=window)

    for i in range(N - window):
        if len(rolling_vol) >= 5:
            barrier = float(np.std(rolling_vol)) * vol_mult
        else:
            barrier = 0.003

        fwd = np.cumsum(returns[i + 1: i + 1 + window])
        hit_upper = np.any(fwd >=  barrier)
        hit_lower = np.any(fwd <= -barrier)

        if hit_upper and not hit_lower:
            labels[i] =  1
        elif hit_lower and not hit_upper:
            labels[i] = -1
        else:
            labels[i] =  0

        rolling_vol.append(returns[i])

    return labels



def add_toxic_label(
    labels: np.ndarray,
    realized_vol: np.ndarray,
    vol_threshold_quantile: float = 0.90,
) -> np.ndarray:
    """
    Overlay TOXIC / CRISIS label (2) on triple-barrier labels
    using a realized-volatility percentile threshold.

    INTERFACE NOTE: This version accepts np.ndarray inputs and is
    used exclusively by calibrate_regime.py. A semantically
    identical but pd.Series-based version exists in
    triple_barrier_labeler.py. Do NOT merge or alias these without
    verifying interface compatibility — their callers use different
    data types throughout.

    Logic:
        Any bar where rolling realized vol exceeds the
        vol_threshold_quantile of the vol distribution is
        relabeled as TOXIC (2), regardless of triple-barrier
        direction. This captures high-vol sideways and shock events
        that the barrier method does not explicitly label as crisis.

    Args:
        labels:                 Triple-barrier labels {-1, 0, 1}.
        realized_vol:           Per-bar rolling realized vol, same
                                length as labels. Must be non-negative.
        vol_threshold_quantile: Percentile threshold for TOXIC.
                                Default 0.90 (top 10% of vol bars).

    Returns:
        Modified labels array {-1, 0, 1, 2}. Original array
        is NOT modified (copy returned).

    Note:
        Only used in 4-state calibration (N_STATES == 4).
        The 3-state calibration path never calls this function.
    """
    if len(labels) != len(realized_vol):
        raise ValueError(
            f"labels length ({len(labels)}) must equal "
            f"realized_vol length ({len(realized_vol)})"
        )
    rv = np.asarray(realized_vol, dtype=float)
    if not np.all(np.isfinite(rv)):
        raise ValueError(
            "realized_vol contains non-finite values. "
            "Compute rolling vol only over valid return windows."
        )
    threshold = float(np.quantile(rv, float(vol_threshold_quantile)))
    result = labels.copy()
    result[rv > threshold] = 2
    return result

def estimate_emission_moments(
    returns: np.ndarray,
    labels: np.ndarray,  # {-1=BEAR, 0=RANGE, 1=TREND, 2=TOXIC}
) -> tuple:
    """
    Compute per-regime mean and std of returns from labeled training data.
    Used to calibrate NHHMM emission distributions for 4-state mode.
    State mapping: BULL=0, BEAR=1, RANGE=2, CRISIS=3
    """
    label_to_state = {1: 0, -1: 1, 0: 2, 2: 3}
    K = 4
    mu = np.zeros(K, dtype=float)
    sigma = np.ones(K, dtype=float) * 0.004

    for label, state_idx in label_to_state.items():
        mask = labels == label
        n = int(mask.sum())
        if n < 10:
            continue
        mu[state_idx] = float(returns[mask].mean())
        raw_std = float(returns[mask].std())
        sigma[state_idx] = max(raw_std, 1e-4)

    sigma[2] = min(sigma[2], sigma[0], sigma[1])
    sigma[3] = max(sigma[3], sigma[0], sigma[1])
    sigma = np.clip(sigma, 1e-4, None)

    return mu, sigma


y_labels = triple_barrier_labels(
    valid_returns,
    window=BARRIER_WINDOW,
    vol_mult=BARRIER_MULT,
)

y_3state = np.where(y_labels ==  1, 0,
           np.where(y_labels == -1, 1, 2))

counts = np.bincount(y_3state, minlength=3)
print(f"    Bull: {counts[0]} | Bear: {counts[1]} | Crisis: {counts[2]}")
assert counts.min() >= 10, \
    "Too few samples in one regime class — increase N_BARS"

# ─── STEP 5: FIT SJM CENTROIDS (pure numpy KMeans) ───────────

print("\n[5/7] Fitting SJM centroids via pure numpy KMeans...")

X_norm = (X_raw - feature_mean) / (feature_std + 1e-8)

kmeans = _kmeans_numpy(
    X_norm,
    n_clusters=N_STATES,
    random_state=RANDOM_SEED,
    n_init=20,
    max_iter=500,
)
sjm_centroids = kmeans.cluster_centers_

within_var = np.zeros(N_FEATURES)
for k in range(N_STATES):
    mask = kmeans.labels_ == k
    if mask.sum() > 1:
        within_var += X_norm[mask].var(axis=0)
within_var = within_var / N_STATES
within_var = np.where(within_var > 1e-12, within_var, 1.0)
sjm_feature_weights = 1.0 / (within_var + 1e-8)
sjm_feature_weights = sjm_feature_weights / (
    np.linalg.norm(sjm_feature_weights) + 1e-12
)

print(f"    Centroids shape: {sjm_centroids.shape}")
print(f"    Feature weights: {sjm_feature_weights.round(4)}")

# ─── STEP 6: FIT NHHMM PARAMETERS ────────────────────────────

print("\n[6/7] Fitting NHHMM parameters...")

if N_STATES == 4:
    # FIX-CRISIS-CALIB: compute rolling 5-bar realized volatility
    # to identify TOXIC bars. rv_5bar[i] = std of returns[i-5:i].
    # Bars with insufficient history receive 0.0 (will not exceed threshold).
    _rv_window = 5
    rv_5bar = np.zeros(len(valid_returns), dtype=float)
    for _i in range(_rv_window, len(valid_returns)):
        rv_5bar[_i] = float(np.std(valid_returns[_i - _rv_window:_i]))

    # Overlay TOXIC label (2) on top 10% of vol bars.
    y_labels_4state = add_toxic_label(
        y_labels,
        rv_5bar,
        vol_threshold_quantile=0.90,
    )

    # Report label distribution to catch degenerate calibration.
    _label_counts = {
        int(k): int(v)
        for k, v in zip(*np.unique(y_labels_4state, return_counts=True))
    }
    print(f"    4-state label distribution: {_label_counts}")
    assert 2 in _label_counts and _label_counts[2] >= 10, (
        "Too few TOXIC bars (label=2) after overlay. "
        "Lower vol_threshold_quantile or increase N_BARS."
    )

    nhhmm_mu, nhhmm_sigma = estimate_emission_moments(
        valid_returns, y_labels_4state
    )
else:
    nhhmm_mu = np.zeros(N_STATES, dtype=float)
    nhhmm_sigma = np.ones(N_STATES, dtype=float) * 0.005

    for k in range(N_STATES):
        mask = y_3state == k
        state_returns = valid_returns[mask]
        if len(state_returns) > 5:
            nhhmm_mu[k] = float(np.mean(state_returns))
            nhhmm_sigma[k] = float(max(np.std(state_returns), 1e-4))
        else:
            nhhmm_mu[k] = 0.0
            nhhmm_sigma[k] = 0.005

rng        = np.random.default_rng(RANDOM_SEED)
nhhmm_beta = rng.normal(
    0.0, 0.01, size=(N_STATES, N_STATES, N_FEATURES)
)
nhhmm_beta[:, 0, :] = 0.0  # identifiability pin

print(f"    nhhmm_mu:         {nhhmm_mu.round(6)}")
print(f"    nhhmm_sigma:      {nhhmm_sigma.round(6)}")
print(f"    nhhmm_beta shape: {nhhmm_beta.shape}")

assert np.all(nhhmm_sigma >= 1e-4), \
    "nhhmm_sigma below 1e-4 — emission too concentrated"

# ─── STEP 7: SAVE WEIGHTS ────────────────────────────────────

print("\n[7/7] Saving calibration artifacts...")

os.makedirs(OUTPUT_DIR, exist_ok=True)

np.savez(
    OUTPUT_PATH,
    nhhmm_beta          = nhhmm_beta.astype(np.float64),
    nhhmm_mu            = nhhmm_mu.astype(np.float64),
    nhhmm_sigma         = nhhmm_sigma.astype(np.float64),
    sjm_centroids       = sjm_centroids.astype(np.float64),
    sjm_feature_weights = sjm_feature_weights.astype(np.float64),
    feature_mean        = feature_mean.astype(np.float64),
    feature_std         = feature_std.astype(np.float64),
)

saved = np.load(OUTPUT_PATH)
required_keys = [
    "nhhmm_beta", "nhhmm_mu", "nhhmm_sigma",
    "sjm_centroids", "sjm_feature_weights",
    "feature_mean", "feature_std",
]
for key in required_keys:
    assert key in saved, f"Missing key: {key}"
    assert np.isfinite(saved[key]).all(), \
        f"Non-finite in saved key: {key}"

print(f"    Saved:  {OUTPUT_PATH}")
print(f"    Keys:   {sorted(saved.files)}")
print()
print("=" * 60)
print("  CALIBRATION COMPLETE")
print("  weights/advanced_regime_weights.npz — READY")
print("=" * 60)
