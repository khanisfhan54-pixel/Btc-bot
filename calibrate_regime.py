"""
Calibration CLI for advanced_regime_engine (FIX A1).

Usage:
    python calibrate_regime.py \
        --in  data/btc_90d.parquet \
        --out weights/advanced_regime_weights.npz

The weights file produced here is the ONLY input that transitions
engine_status from DEGRADED to OPERATIONAL. Until this CLI is implemented
end-to-end, the AdvancedRegimeEngine will continue to fail closed
(signal_valid=False) on every emit, and no live order can route.

This stub intentionally raises NotImplementedError so CI catches an
un-implemented calibration pipeline before deployment.
"""
from __future__ import annotations

import argparse
import pathlib
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate regime weights from historical OHLCV+orderbook data."
    )
    parser.add_argument("--in",  dest="data_path",    required=True, type=pathlib.Path,
                        help="Path to historical parquet (OHLCV + orderbook snapshot stream).")
    parser.add_argument("--out", dest="weights_path", required=True, type=pathlib.Path,
                        help="Output path for the calibrated weights .npz file.")
    args = parser.parse_args()

    # TODO (operator): load args.data_path (parquet), run NHHMM → SJM →
    # MS-GARCH training pipeline, save output to args.weights_path using
    # np.savez_compressed(...).
    raise NotImplementedError(
        f"Calibration pipeline not yet implemented. "
        f"Train on {args.data_path} and save to {args.weights_path}."
    )


if __name__ == "__main__":
    sys.exit(main() or 0)
