# Active Weight Artifact Audit

Generated: 2026-06-12 (UTC)

## Commands run

```bash
find . -maxdepth 4 -type f \( -path './weights/*' -o -name '*weights*.npz' -o -name '*scalars.json' -o -name 'calibration_provenance.json' \) -print -exec sh -c 'echo --- "$1"; stat -c "%y %s bytes" "$1"' sh {} \;
cat weights/calibration_provenance.json
python - <<'PY'
import numpy as np, os
p='weights/advanced_regime_weights.npz'
w=np.load(p, allow_pickle=False)
print('files', w.files)
for k in w.files:
    a=w[k]
    print(k, a.shape, a.dtype)
print('scalar exists', os.path.exists(p.replace('.npz','_scalars.json')))
PY
stat weights/advanced_regime_weights.npz weights/calibration_provenance.json
git log --format='%h %cI %s' -- weights/advanced_regime_weights.npz weights/calibration_provenance.json | head -20
git status --short --ignored weights
git ls-files weights
```

## Artifact path

- Active default weight path resolved by `AdvancedRegimeEngine`: `weights/advanced_regime_weights.npz`.
- Active provenance path: `weights/calibration_provenance.json`.
- No `weights/advanced_regime_weights_scalars.json` companion exists.

## Filesystem creation / modification date

`stat` reported both files with identical timestamps:

- Birth: `2026-06-12 20:14:18.369180238 +0000`
- Modify: `2026-06-12 20:14:18.369180238 +0000`
- Change: `2026-06-12 20:14:18.369180238 +0000`

## Git tracking status

- `weights/advanced_regime_weights.npz` is ignored (`!! weights/advanced_regime_weights.npz`).
- `weights/calibration_provenance.json` is untracked as part of `?? weights/`.
- `git ls-files weights` returned no tracked files.
- History shows older commits involving weight artifacts, including `374ca43 2026-05-09T12:43:55+05:30 Remove binary weight artifacts from PR` and `998b7c7 2026-05-07T08:45:52+05:30 Phase 3: fix calibrate_regime.py (pure numpy KMeans, no sklearn) + validated weights`, but the current active files are not tracked.

## Provenance

Current `weights/calibration_provenance.json` content:

```json
{
  "data_source": "synthetic",
  "production_valid": false,
  "reason": "trained_on_synthetic_data"
}
```

## Weight payload

Loaded `.npz` keys:

| Key | Shape | dtype |
|---|---:|---|
| `nhhmm_beta` | `(3, 3, 3)` | `float64` |
| `nhhmm_mu` | `(3,)` | `float64` |
| `nhhmm_sigma` | `(3,)` | `float64` |
| `sjm_centroids` | `(3, 3)` | `float64` |
| `sjm_feature_weights` | `(3,)` | `float64` |
| `feature_mean` | `(3,)` | `float64` |
| `feature_std` | `(3,)` | `float64` |

## Data source

The only active provenance file declares `data_source: synthetic`. The artifact's contents are compatible with the synthetic calibration path and there is no tracked real-BTC provenance beside it.

## Production-valid flag

`production_valid: false`.

## Classification

**A. Synthetic.**

Reason: active provenance explicitly says `data_source=synthetic`, `production_valid=false`, and `reason=trained_on_synthetic_data`; default engine initialization confirms `calibration_status=not_production_valid` and `signal_valid=False` when using this artifact without research mode.

## Recommendation

Do not treat the active artifact as a production BTC calibration artifact. It is suitable only for research/testing paths, and only with explicit `REGIME_RESEARCH_MODE=1` if signal emission is desired.
