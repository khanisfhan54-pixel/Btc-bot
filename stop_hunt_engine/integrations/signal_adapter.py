"""Signal adapter exposing SHPE probability only (no execution side-effects)."""
from __future__ import annotations

import logging
import os
import time as _time
import os as _os
from collections import defaultdict as _defaultdict
from typing import Optional, TypedDict

from ..model.engine import StopHuntProbabilityEngine
from .feature_pipeline import PipelineInput, build_feature_vector

_log = logging.getLogger("shpe.signal_adapter")

_WARN_COOLDOWN_SEC: float = 60.0
_warn_last_seen: dict = _defaultdict(float)


def _throttled_warn(key: str, msg: str) -> None:
    """Emit a warning at most once per _WARN_COOLDOWN_SEC for each unique key."""
    now = _time.monotonic()
    if now - _warn_last_seen[key] >= _WARN_COOLDOWN_SEC:
        _warn_last_seen[key] = now
        _log.error(msg)


class SHPEOutput(TypedDict):
    probability: float
    degraded: bool
    regime_used: str


_SHPE_MODEL_PATH = _os.environ.get("SHPE_MODEL_PATH", "shpe_model.pkl")
_SHPE_CALIBRATOR_PATH = _os.environ.get("SHPE_CALIBRATOR_PATH", "calibrator.pkl")


def _validate_loaded_shpe_engine(
    engine: StopHuntProbabilityEngine,
    *,
    model_path: str,
    require_calibrator: bool,
) -> None:
    """Validate SHPE artifacts before allowing production startup to proceed."""
    errors = []
    if not isinstance(engine, StopHuntProbabilityEngine):
        errors.append(f"loaded object is {type(engine).__name__}, not StopHuntProbabilityEngine")
    if getattr(engine, "classifier", None) is None:
        errors.append("classifier missing")
    if not str(getattr(engine, "model_version", "")).strip() or str(getattr(engine, "model_version", "")).strip().lower() == "unknown":
        errors.append("model_version missing")
    if not tuple(getattr(engine, "feature_names", ())):
        errors.append("feature_names missing")
    if require_calibrator and getattr(engine, "calibrator", None) is None:
        errors.append("calibrator missing")
    if errors:
        raise RuntimeError(f"Invalid SHPE artifact at {model_path}: " + "; ".join(errors))


def load_shpe_engine_at_boot(
    *,
    model_path: str = _SHPE_MODEL_PATH,
    calibrator_path: str = _SHPE_CALIBRATOR_PATH,
    require_trained: bool = False,
    require_calibrator: Optional[bool] = None,
) -> Optional[StopHuntProbabilityEngine]:
    """
    Boot-time loader with explicit artifact validation.

    ``require_trained`` is fail-closed: a missing, corrupt, unversioned, or
    uncalibrated artifact raises RuntimeError instead of starting with p=0.5.
    """
    import pickle

    calibrator_required = require_trained if require_calibrator is None else bool(require_calibrator)

    if _os.path.exists(model_path):
        try:
            engine = StopHuntProbabilityEngine.load(model_path)
            if engine.calibrator is None and _os.path.exists(calibrator_path):
                with open(calibrator_path, "rb") as fh:
                    engine = StopHuntProbabilityEngine(
                        classifier=engine.classifier,
                        calibrator=pickle.load(fh),
                        feature_names=engine.feature_names,
                        model_version=engine.model_version,
                        staleness_limit=engine.staleness_limit,
                    )
                _log.info("shpe_boot: loaded legacy calibrator from %s", calibrator_path)
            _validate_loaded_shpe_engine(
                engine,
                model_path=model_path,
                require_calibrator=calibrator_required,
            )
            _log.info(
                "shpe_boot: engine loaded from %s version=%s calibrated=%s",
                model_path,
                engine.model_version,
                engine.calibrator is not None,
            )
            return engine
        except Exception as exc:
            _log.error("shpe_boot: artifact validation failed for %s: %s", model_path, exc)
            if require_trained:
                raise RuntimeError(
                    f"SHPE model required but failed validation/load from {model_path}: {exc}"
                ) from exc
            return None

    if _os.path.exists(calibrator_path):
        msg = (
            f"shpe_boot: SHPE model required but full model missing at {model_path}; "
            f"standalone calibrator found at {calibrator_path} but cannot produce trained SHPE probabilities"
        )
        if require_trained:
            _log.error(msg)
            raise RuntimeError(msg)
        _log.warning("%s — starting in explicit degraded mode", msg)
    elif require_trained:
        raise RuntimeError(
            f"SHPE model required but not found at {model_path}. "
            "Run training and call StopHuntProbabilityEngine.save()."
        )
    else:
        _log.warning("shpe_boot: no model found at %s — starting in explicit degraded mode", model_path)
    return None


def get_shpe_probability(engine: Optional[StopHuntProbabilityEngine], input_data: PipelineInput, bar_index: int) -> SHPEOutput:
    try:
        if os.getenv("SHPE_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"} or engine is None:
            return {"probability": 0.5, "degraded": True, "regime_used": "<disabled>"}
        feature_vector = build_feature_vector(input_data, bar_index)
        prediction = engine.predict(feature_vector)
        return {
            "probability": float(max(0.0, min(1.0, prediction.p_sweep))),
            "degraded": bool(prediction.degraded),
            "regime_used": str(prediction.regime_used),
        }
    except Exception as exc:
        _throttled_warn(
            f"shpe_error:{type(exc).__name__}",
            f"shpe_signal_adapter_error bar_index={bar_index} exc={exc!r}",
        )
        return {"probability": 0.5, "degraded": True, "regime_used": "<error>"}
