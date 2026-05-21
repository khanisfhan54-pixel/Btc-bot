"""Regime adapter feature projection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RegimeContextFeatures:
    regime_label: str = ""
    confidence: float = 0.0
    conviction: float = 0.0
    edge_score: float = 0.0
    signal_valid: bool = False
    expected_volatility: float = 0.0
    stale: bool = False


def project_regime_context(
    regime_output: Optional[Mapping[str, Any]],
    *,
    stale_seconds: int = 300,
    as_of_ts: float = 0.0,
) -> RegimeContextFeatures:
    if not regime_output:
        return RegimeContextFeatures(regime_label="", stale=True)

    regime_ts = float(regime_output.get("timestamp", 0.0))
    stale = as_of_ts > 0 and regime_ts > 0 and (as_of_ts - regime_ts) > stale_seconds

    return RegimeContextFeatures(
        regime_label=str(regime_output.get("regime_label", "")),
        confidence=float(regime_output.get("confidence", 0.0)),
        conviction=float(regime_output.get("conviction", 0.0)),
        edge_score=float(regime_output.get("edge_score", 0.0)),
        signal_valid=bool(regime_output.get("signal_valid", False)),
        expected_volatility=float(regime_output.get("expected_volatility", 0.0)),
        stale=stale,
    )
