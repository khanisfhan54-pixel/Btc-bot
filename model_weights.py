"""ModelWeightManager: serialize and deserialize regime model weights."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
import numpy as np

logger = logging.getLogger(__name__)


class ModelWeightManager:

    @staticmethod
    def save_weights(model_name: str, weights_dict: Dict[str, Any], path: str) -> None:
        """Save weights to .npz (arrays) and a companion .json (scalars)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        arrays = {}
        scalars = {}
        for k, v in weights_dict.items():
            try:
                arr = np.asarray(v, dtype=float)
                arrays[k] = arr
            except Exception:
                scalars[k] = v
        np.savez(path, **arrays)
        scalar_path = path.replace(".npz", "_scalars.json")
        with open(scalar_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(scalars, f)
        os.replace(scalar_path + ".tmp", scalar_path)
        logger.info("[WEIGHTS] Saved %d arrays and %d scalars for model '%s' to %s",
                    len(arrays), len(scalars), model_name, path)

    @staticmethod
    def load_weights(model_name: str, path: str) -> Optional[Dict[str, Any]]:
        """Load weights from .npz. Returns None if file does not exist."""
        if not os.path.exists(path):
            logger.warning("[WEIGHTS] Weight file not found for model '%s': %s", model_name, path)
            return None
        try:
            npz = np.load(path, allow_pickle=False)
            weights = {k: npz[k] for k in npz.files}
            scalar_path = path.replace(".npz", "_scalars.json")
            if os.path.exists(scalar_path):
                with open(scalar_path, "r", encoding="utf-8") as f:
                    weights.update(json.load(f))
            logger.info("[WEIGHTS] Loaded %d weight arrays for model '%s' from %s",
                        len(weights), model_name, path)
            return weights
        except Exception as exc:
            logger.error("[WEIGHTS] Failed to load weights for model '%s' from %s: %s",
                         model_name, path, exc, exc_info=True)
            return None
