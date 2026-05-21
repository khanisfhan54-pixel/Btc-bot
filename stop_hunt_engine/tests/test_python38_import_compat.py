"""Proves all modules import cleanly (catches PEP 604 / PEP 585 annotation bugs)."""
import importlib


def test_all_shpe_modules_import() -> None:
    modules = [
        "stop_hunt_engine",
        "stop_hunt_engine.data.candle_store",
        "stop_hunt_engine.data.derivatives",
        "stop_hunt_engine.data.l2_snapshot",
        "stop_hunt_engine.data.trade_tape",
        "stop_hunt_engine.features.feature_vector",
        "stop_hunt_engine.features.funding_pressure",
        "stop_hunt_engine.features.liquidation_proximity",
        "stop_hunt_engine.features.lob_imbalance",
        "stop_hunt_engine.features.oi_dynamics",
        "stop_hunt_engine.features.pool_distance",
        "stop_hunt_engine.features.regime_context",
        "stop_hunt_engine.features.volume_trap",
        "stop_hunt_engine.integrations.feature_pipeline",
        "stop_hunt_engine.integrations.regime_adapter",
        "stop_hunt_engine.integrations.signal_adapter",
        "stop_hunt_engine.model.calibrator",
        "stop_hunt_engine.model.engine",
        "stop_hunt_engine.model.regime_conditional",
        "stop_hunt_engine.model.sweep_classifier",
        "stop_hunt_engine.validation.permutation_audit",
        "stop_hunt_engine.validation.walk_forward",
    ]
    for mod in modules:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            raise AssertionError(f"Failed to import {mod!r}: {exc}") from exc
