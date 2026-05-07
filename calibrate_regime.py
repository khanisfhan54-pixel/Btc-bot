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
from microstructure_features import MicrostructureFeatureEngine

# ─── CONFIG ──────────────────────────────────────────────────

N_STATES       = 3
N_FEATURES     = 6
N_BARS         = 2000
LOOKBACK       = 200
RV_WINDOW      = 5
RANDOM_SEED    = 42
BARRIER_WINDOW = 20
BARRIER_MULT   = 1.5
OUTPUT_DIR     = "weights"
OUTPUT_PATH    = os.path.join(OUTPUT_DIR, "advanced_regime_weights.npz")

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

print("\n[2/7] Building microstructure feature matrix...")

feature_engine = MicrostructureFeatureEngine(
    lookback_bars=LOOKBACK,
    rv_window_bars=RV_WINDOW,
)

X_raw         = []
valid_returns = []
valid_prices  = []

for i in range(N_BARS):
    mid  = float(prices[i + 1])
    bid  = mid - 1.0
    ask  = mid + 1.0
    bsz  = float(bid_sizes[i])
    asz  = float(ask_sizes[i])
    flow = float(trade_flows[i])
    bvol = float(buy_vols[i])
    svol = float(sell_vols[i])

    feat = feature_engine.update(
        mid, bid, ask, bsz, asz, flow, bvol, svol
    )
    X_raw.append(feat)
    valid_returns.append(returns[i])
    valid_prices.append(mid)

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

nhhmm_mu    = np.zeros(N_STATES, dtype=float)
nhhmm_sigma = np.ones(N_STATES,  dtype=float) * 0.005

for k in range(N_STATES):
    mask          = y_3state == k
    state_returns = valid_returns[mask]
    if len(state_returns) > 5:
        nhhmm_mu[k]    = float(np.mean(state_returns))
        nhhmm_sigma[k] = float(max(np.std(state_returns), 1e-4))
    else:
        nhhmm_mu[k]    = 0.0
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
