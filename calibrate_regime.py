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

import json
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
N_BARS         = int(os.environ.get("REGIME_N_BARS", "8910"))
LOOKBACK       = 200
RV_WINDOW      = 5
RANDOM_SEED    = 42
BARRIER_WINDOW = 20
BARRIER_MULT   = 1.5
OUTPUT_DIR     = os.environ.get("REGIME_OUTPUT_DIR", "weights")
OUTPUT_PATH    = os.environ.get("REGIME_OUTPUT_PATH", os.path.join(OUTPUT_DIR, "advanced_regime_weights.npz"))
PROVENANCE_PATH = os.environ.get("REGIME_PROVENANCE_PATH", os.path.join(OUTPUT_DIR, "calibration_provenance.json"))
DATA_SOURCE    = os.environ.get("REGIME_DATA_SOURCE", "synthetic").strip().lower()
AGGTRADES_PATH = os.environ.get("REGIME_AGGTRADES_PATH", os.path.join("data", "aggTrades.csv"))
BOOKDEPTH_PATH = os.environ.get("REGIME_BOOKDEPTH_PATH", os.path.join("data", "bookDepth.csv"))

if DATA_SOURCE not in ("synthetic", "real"):
    raise ValueError(
        "calibrate_regime.py: REGIME_DATA_SOURCE must be 'synthetic' or 'real'. "
        f"Got {DATA_SOURCE!r}."
    )

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

def _parse_bool_is_buyer_maker(value):
    return str(value).strip().lower() in {"true", "1", "t", "yes"}


def _floor_minute_ms(ts_ms):
    return (int(ts_ms) // 60000) * 60000


def _load_real_btc_training_data(agg_path, depth_path, max_bars):
    """Load strictly real BTC calibration inputs from aggTrades + bookDepth.

    No synthetic fallback is permitted on this path.  aggTrades provide real
    timestamps, prices, trade direction and volume.  bookDepth provides real
    depth by timestamp/percentage bucket; negative percentages are treated as
    bid-side depth and positive percentages as ask-side depth.
    """
    import csv
    from datetime import datetime, timezone

    if not os.path.exists(agg_path):
        raise FileNotFoundError(f"aggTrades file missing: {agg_path}")
    if not os.path.exists(depth_path):
        raise FileNotFoundError(f"bookDepth file missing: {depth_path}")

    minute_rows = {}
    with open(agg_path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts_raw = row.get("transact_time") or row.get("T") or row.get("timestamp")
            px_raw = row.get("price") or row.get("p")
            qty_raw = row.get("quantity") or row.get("q") or row.get("qty")
            if ts_raw is None or px_raw is None or qty_raw is None:
                raise ValueError("aggTrades row missing timestamp/price/quantity")
            ts = int(float(ts_raw))
            price = float(px_raw)
            qty = float(qty_raw)
            if not (np.isfinite(price) and np.isfinite(qty) and price > 0.0 and qty >= 0.0):
                raise ValueError(f"invalid aggTrades row price={px_raw!r} qty={qty_raw!r}")
            minute = _floor_minute_ms(ts)
            rec = minute_rows.setdefault(minute, {"close": price, "buy": 0.0, "sell": 0.0, "count": 0})
            rec["close"] = price
            # Binance is_buyer_maker=True means buyer was passive, so the
            # aggressor trade was sell-side.
            is_buyer_maker = _parse_bool_is_buyer_maker(row.get("is_buyer_maker", row.get("m", "false")))
            if is_buyer_maker:
                rec["sell"] += qty
            else:
                rec["buy"] += qty
            rec["count"] += 1

    depth_rows = {}
    with open(depth_path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts_raw = row.get("timestamp") or row.get("ts") or row.get("T")
            pct_raw = row.get("percentage")
            depth_raw = row.get("depth")
            if ts_raw is None or pct_raw is None or depth_raw is None:
                raise ValueError("bookDepth row missing timestamp/percentage/depth")
            try:
                ts = int(float(ts_raw))
            except ValueError:
                dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp() * 1000)
            pct = float(pct_raw)
            depth = float(depth_raw)
            if not (np.isfinite(pct) and np.isfinite(depth) and depth >= 0.0):
                raise ValueError(f"invalid bookDepth row percentage={pct_raw!r} depth={depth_raw!r}")
            minute = _floor_minute_ms(ts)
            rec = depth_rows.setdefault(minute, {"bid": 0.0, "ask": 0.0})
            if pct < 0.0:
                rec["bid"] += depth
            elif pct > 0.0:
                rec["ask"] += depth

    common_minutes = sorted(set(minute_rows).intersection(depth_rows))
    if max_bars > 0:
        common_minutes = common_minutes[: int(max_bars)]
    if len(common_minutes) < max(LOOKBACK + BARRIER_WINDOW + 5, N_STATES * 10):
        raise ValueError(
            "real calibration data insufficient after aggTrades/bookDepth alignment: "
            f"{len(common_minutes)} aligned bars"
        )

    prices_local = []
    returns_local = []
    bid_sizes_local = []
    ask_sizes_local = []
    trade_flows_local = []
    buy_vols_local = []
    sell_vols_local = []
    raw_vols_local = []
    prev_price = None
    for minute in common_minutes:
        trade_rec = minute_rows[minute]
        depth_rec = depth_rows[minute]
        close = float(trade_rec["close"])
        if prev_price is None:
            prices_local.append(close)
            prev_price = close
            continue
        ret = np.log(close / prev_price)
        buy = float(trade_rec["buy"])
        sell = float(trade_rec["sell"])
        prices_local.append(close)
        returns_local.append(float(ret))
        bid_sizes_local.append(float(depth_rec["bid"]))
        ask_sizes_local.append(float(depth_rec["ask"]))
        trade_flows_local.append(float(buy - sell))
        buy_vols_local.append(buy)
        sell_vols_local.append(sell)
        raw_vols_local.append(float(buy + sell))
        prev_price = close

    return (
        np.asarray(prices_local, dtype=float),
        np.asarray(returns_local, dtype=float),
        list(bid_sizes_local),
        list(ask_sizes_local),
        list(trade_flows_local),
        list(buy_vols_local),
        list(sell_vols_local),
        np.asarray(raw_vols_local, dtype=float),
    )


print(f"\n[1/7] Generating {DATA_SOURCE} training data...")

if DATA_SOURCE == "real":
    (
        prices,
        returns,
        bid_sizes,
        ask_sizes,
        trade_flows,
        buy_vols,
        sell_vols,
        _raw_vols,
    ) = _load_real_btc_training_data(AGGTRADES_PATH, BOOKDEPTH_PATH, N_BARS)
    N_BARS = int(len(returns))
else:
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
    _raw_vols = np.abs(np.random.randn(N_BARS) * 100 + 100)

print(f"    Bars: {N_BARS} | Price range: "
      f"{prices.min():.0f} - {prices.max():.0f}")

# ─── STEP 2: BUILD FEATURE MATRIX ────────────────────────────

print("\n[2/7] Building feature matrix...")

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

with open(PROVENANCE_PATH, "w", encoding="utf-8") as fh:
    if DATA_SOURCE == "synthetic":
        provenance = {
            "data_source": "synthetic",
            "production_valid": False,
            "reason": "trained_on_synthetic_data",
        }
    else:
        provenance = {
            "data_source": "real",
            "production_valid": True,
            "aggTrades": AGGTRADES_PATH,
            "bookDepth": BOOKDEPTH_PATH,
            "bars": int(N_BARS),
            "reason": "real_aggTrades_bookDepth_aligned",
        }
    json.dump(provenance, fh, indent=2, sort_keys=True)
    fh.write("\n")

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
print(f"    Provenance: {PROVENANCE_PATH}")
print(f"    Keys:   {sorted(saved.files)}")
print()
print("=" * 60)
print("  CALIBRATION COMPLETE")
print("  weights/advanced_regime_weights.npz — READY")
print("=" * 60)


# ─── PUBLIC TESTABLE API ─────────────────────────────────────
def calibrate(ohlcv_csv_path: str, output_path: str) -> None:
    """Calibrate ARE-compatible weights from a real OHLCV CSV.

    This function is intentionally small and fail-closed for tests and batch
    callers.  It does not use synthetic fallback data; invalid input raises a
    ValueError and no strategy thresholds are tuned here.
    """
    data = np.loadtxt(ohlcv_csv_path, delimiter=",", ndmin=2)
    if data.shape[0] < 8:
        raise ValueError("calibrate requires at least 8 OHLCV rows")
    if data.shape[1] < 6:
        raise ValueError("calibrate requires OHLCV columns: timestamp,open,high,low,close,volume")
    closes = np.asarray(data[:, 4], dtype=float)
    volumes = np.asarray(data[:, 5], dtype=float)
    if not np.all(np.isfinite(closes)):
        raise ValueError("non-finite close prices")
    if not np.all(np.isfinite(volumes)):
        raise ValueError("non-finite volumes")
    if np.any(closes <= 0.0):
        raise ValueError("strictly positive close prices required")
    if np.any(volumes < 0.0):
        raise ValueError("non-negative volumes required")

    returns_local = np.diff(np.log(closes))
    vol = volumes[1:]
    vol_mean_local = float(np.mean(vol))
    vol_std_local = float(np.std(vol)) if float(np.std(vol)) > 1e-12 else 1.0
    vol_z = (vol - vol_mean_local) / vol_std_local
    # OHLCV-only calibration has no real book side; encode the available real
    # candle range as a neutral microstructure proxy for compatibility only.
    ranges = np.asarray((data[1:, 2] - data[1:, 3]) / closes[1:], dtype=float)
    X = np.column_stack([returns_local, ranges, vol_z]).astype(float)
    if not np.all(np.isfinite(X)):
        raise ValueError("non-finite calibration features")

    feature_mean_local = X.mean(axis=0)
    feature_std_local = X.std(axis=0)
    feature_std_local = np.where(feature_std_local > 1e-12, feature_std_local, 1.0)
    X_norm_local = (X - feature_mean_local) / feature_std_local

    kmeans_local = _kmeans_numpy(X_norm_local, n_clusters=3, random_state=RANDOM_SEED, n_init=5, max_iter=100)
    labels_local = np.asarray(kmeans_local.labels_, dtype=int)
    centroids_local = np.asarray(kmeans_local.cluster_centers_, dtype=float)

    mu_local = np.zeros(3, dtype=float)
    sigma_local = np.ones(3, dtype=float) * 0.005
    for k in range(3):
        state_returns = returns_local[labels_local == k]
        if len(state_returns) > 1:
            mu_local[k] = float(np.mean(state_returns))
            sigma_local[k] = float(max(np.std(state_returns), 1e-4))
    rng_local = np.random.default_rng(RANDOM_SEED)
    beta_local = rng_local.normal(0.0, 0.01, size=(3, 3, 3))
    beta_local[:, 0, :] = 0.0
    feature_weights_local = np.ones(3, dtype=float) / np.sqrt(3.0)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    np.savez(
        output_path,
        nhhmm_beta=beta_local.astype(np.float64),
        nhhmm_mu=mu_local.astype(np.float64),
        nhhmm_sigma=sigma_local.astype(np.float64),
        sjm_centroids=centroids_local.astype(np.float64),
        sjm_feature_weights=feature_weights_local.astype(np.float64),
        feature_mean=feature_mean_local.astype(np.float64),
        feature_std=feature_std_local.astype(np.float64),
    )
