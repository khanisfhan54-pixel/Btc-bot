"""Adapter to normalize external regime outputs for SHPE."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def map_regime_output(regime_payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not regime_payload:
        return {"regime_label": "", "confidence": 0.0, "conviction": 0.0, "edge_score": 0.0, "signal_valid": False, "expected_volatility": 0.0, "timestamp": 0.0}
    return {
        "regime_label": str(regime_payload.get("regime_label", regime_payload.get("label", ""))),
        "confidence": float(regime_payload.get("confidence", 0.0)),
        "conviction": float(regime_payload.get("conviction", 0.0)),
        "edge_score": float(regime_payload.get("edge_score", 0.0)),
        "signal_valid": bool(regime_payload.get("signal_valid", True)),
        "expected_volatility": float(regime_payload.get("expected_volatility", 0.0)),
        "timestamp": float(regime_payload.get("timestamp", 0.0)),
    }
